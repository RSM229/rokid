"""Step 3 静态验证：权限目录 / 矩阵 / 越权保护 / 即时生效链路。

**纯静态**（AST + 正则，不 import 任何项目模块，因此本机无 sqlalchemy 也能跑）。

为什么这层验证很重要：Step 3 把权限从「代码常量」变成「可配置数据」，
配置一旦可以漂移，最容易出的事就是**键对不上**——目录里有界面勾不上的幽灵
权限、代码里用的键不在目录内（勾不上、也永远没人有）、前端门控的键后端
不认（页面永远打不开）。这些都不会报错，只会静默失效，所以必须用脚本兜住。

用法::

    python scripts/verify_step3_logic.py
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # cloud-backend/
REPO = ROOT.parent                                      # 仓库根（web-admin 在隔壁）
BACKEND = ROOT / "app"
FRONTEND = REPO / "web-admin" / "src"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))


def _module_assign(tree: ast.Module, name: str) -> ast.expr | None:
    """取模块级赋值的右侧节点。

    🔴 必须同时处理 ``Assign``（``X = ...``）与 ``AnnAssign``
    （``X: tuple[PermissionInfo, ...] = ...``）—— 本项目所有常量都带类型
    注解，只判 ``ast.Assign`` 会**静默解析出空结果**（本脚本第一版就栽在这，
    还连带让「并集 ⊆ 目录」变成空集包含空集的假阳性）。
    """
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id == name:
                    return node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                return node.value
    return None


# --------------------------------------------------------------------------
# 1) 解析权限目录
# --------------------------------------------------------------------------
def parse_catalog() -> list[dict]:
    src = (BACKEND / "models" / "permission_catalog.py").read_text(encoding="utf-8")
    value = _module_assign(ast.parse(src), "PERMISSION_CATALOG")
    entries: list[dict] = []
    if not isinstance(value, (ast.Tuple, ast.List)):
        return entries
    for call in value.elts:  # tuple of PermissionInfo(...)
        if not isinstance(call, ast.Call):
            continue
        args = [a.value for a in call.args if isinstance(a, ast.Constant)]
        kwargs = {
            kw.arg: (kw.value.value if isinstance(kw.value, ast.Constant) else None)
            for kw in call.keywords
        }
        if len(args) >= 3:
            entries.append(
                {"key": args[0], "label": args[1], "group": args[2],
                 "org_level": bool(kwargs.get("org_level", False))}
            )
    return entries


# --------------------------------------------------------------------------
# 2) 解析 ROLE_PERMISSIONS（seed 与兜底）
# --------------------------------------------------------------------------
def parse_role_permissions() -> dict[str, list[str]]:
    src = (BACKEND / "models" / "user.py").read_text(encoding="utf-8")
    value = _module_assign(ast.parse(src), "ROLE_PERMISSIONS")
    out: dict[str, list[str]] = {}
    if not isinstance(value, ast.Dict):
        return out
    for key_node, value_node in zip(value.keys, value.values):
        role = None
        if isinstance(key_node, ast.Attribute) and key_node.attr == "value":
            role = key_node.value.attr.lower()          # UserRole.X.value
        elif isinstance(key_node, ast.Name):
            role = {"ORG_ROLE_SUPER_ADMIN": "super_admin",
                    "ORG_ROLE_ADMIN": "admin"}.get(key_node.id)
        if role and isinstance(value_node, ast.List):
            out[role] = [
                e.value for e in value_node.elts if isinstance(e, ast.Constant)
            ]
    return out


def parse_enum_values() -> list[str]:
    src = (BACKEND / "models" / "user.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "UserRole":
            values = []
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant):
                    values.append(stmt.value.value)
            return values
    return []


# --------------------------------------------------------------------------
# 3) 扫描后端实际使用的权限键
# --------------------------------------------------------------------------
def scan_backend_keys() -> set[str]:
    keys: set[str] = set()
    pattern = re.compile(r'(?:require_permission|has_permission)\(\s*"([a-z_]+)"')
    for path in BACKEND.rglob("*.py"):
        keys |= set(pattern.findall(path.read_text(encoding="utf-8")))
    return keys


# --------------------------------------------------------------------------
# 4) 扫描前端使用的权限键（路由 meta + hasPermission 调用）
# --------------------------------------------------------------------------
def scan_frontend_keys() -> set[str]:
    keys: set[str] = set()
    if not FRONTEND.exists():
        return keys
    meta = re.compile(r"permission:\s*'([a-z_]+)'")
    call = re.compile(r"hasPermission\(\s*'([a-z_]+)'\s*\)")
    for path in list(FRONTEND.rglob("*.ts")) + list(FRONTEND.rglob("*.vue")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        keys |= set(meta.findall(text))
        keys |= set(call.findall(text))
    return keys


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def main() -> int:
    catalog = parse_catalog()
    catalog_keys = [item["key"] for item in catalog]
    catalog_set = set(catalog_keys)
    org_level_keys = {item["key"] for item in catalog if item["org_level"]}
    matrix = parse_role_permissions()
    enum_values = parse_enum_values()

    # ---- 【1】权限目录自身 ----
    check("目录非空", len(catalog) >= 20, f"实际 {len(catalog)} 条")
    check("目录键唯一", len(catalog_keys) == len(catalog_set),
          f"重复: {sorted({k for k in catalog_keys if catalog_keys.count(k) > 1})}")
    check("每条都有分组与中文名",
          all(item["group"] and item["label"] for item in catalog))

    # ---- 【2】目录是超管真源（不与矩阵并集耦合）----
    user_src = read(BACKEND / "models" / "user.py")
    check("ALL_PERMISSIONS 取自权限目录（不再取矩阵并集）",
          "list(ALL_PERMISSION_KEYS)" in user_src,
          "若回到矩阵并集：某权限从所有角色取消勾选后超管也失去它，系统将无法恢复")

    # ---- 【3】矩阵并集必须都在目录内（否则播种会丢键）----
    matrix_union = {p for perms in matrix.values() for p in perms}
    missing = sorted(matrix_union - catalog_set)
    check("矩阵并集 ⊆ 权限目录", not missing, f"目录缺: {missing}")

    # ---- 【4】后端代码使用的权限键必须在目录内 ----
    backend_keys = scan_backend_keys()
    missing_be = sorted(backend_keys - catalog_set)
    check("后端 require_permission 的键都在目录内", not missing_be,
          f"目录缺: {missing_be}（缺了这些键谁都配不出来）")

    # ---- 【5】前端门控键必须在目录内（抓「孤儿键」）----
    frontend_keys = scan_frontend_keys()
    missing_fe = sorted(frontend_keys - catalog_set)
    check("前端权控键都在目录内（无孤儿键）", not missing_fe,
          f"目录缺: {missing_fe}（缺了会让页面除超管外谁都打不开）")

    # ---- 【6】矩阵覆盖全部角色 key ----
    expected_roles = set(enum_values) | {"super_admin", "admin"}
    missing_roles = sorted(expected_roles - set(matrix))
    check("默认矩阵覆盖全部角色", not missing_roles, f"缺角色: {missing_roles}")

    # ---- 【7】默认矩阵不违反层级约束 ----
    project_roles = set(enum_values)
    violations = sorted(
        f"{role}:{perm}"
        for role in project_roles
        for perm in matrix.get(role, [])
        if perm in org_level_keys
    )
    check("项目级岗位未持有公司级权限", not violations, f"越层: {violations}")

    # ---- 【8】signature_manage 孤儿键已收编 ----
    check("signature_manage 已在目录内", "signature_manage" in catalog_set)
    check("signature_manage 已进入默认矩阵（不再只有超管可见）",
          any("signature_manage" in perms for perms in matrix.values()))

    # ---- 【9】服务层：缓存 + 兜底 ----
    svc = read(BACKEND / "services" / "permissions.py")
    check("权限服务有进程内矩阵缓存", "_matrix_cache" in svc and "def get_matrix" in svc)
    check("有从 DB 加载并覆盖缓存的入口", "async def load_matrix" in svc)
    check("加载失败回退代码常量", "回退代码常量" in svc and "except Exception" in svc)
    check("矩阵为空判定看 roles 表（尊重「故意清空」的配置）",
          "select(Role.key)" in svc)
    check("权限版本读取/递增已实现",
          "async def current_perm_version" in svc and "async def bump_perm_version" in svc)
    check("版本可供中间件同步读取", "def get_cached_perm_version" in svc)

    # ---- 【10】配置接口：越权保护 / 审计 / 层级校验 ----
    router = read(BACKEND / "routers" / "role_permissions.py")
    check("配置接口需 role_grant 权限", 'require_permission("role_grant")' in router)
    check("不能授予自己没有的权限（约束 2）",
          "不能授予自己没有的权限" in router and "permission_set()" in router)
    check("项目级角色不得持有公司级权限（约束 3）",
          "ORG_ONLY_PERMISSION_KEYS" in router and "is_org_level" in router)
    check("拒绝目录外的权限键（约束 4）", "validate_permission_keys" in router)
    check("禁止编辑 super_admin（约束 5）", "ORG_ROLE_SUPER_ADMIN" in router)
    check("变更写 security_audit", "SecurityAudit" in router and "role_permission_update" in router)
    check("变更后 bump 版本并刷新缓存",
          "bump_perm_version" in router and "load_matrix" in router)
    check("提供恢复默认入口", "reset_role_permissions_to_default" in router)

    # ---- 【11】播种与装配 ----
    seed = read(BACKEND / "services" / "role_seed.py")
    check("播种幂等（roles 非空则跳过）", "if existing:" in seed and "seeded" in seed)
    check("播种丢弃目录外的键并告警", "dropped" in seed and "logger.warning" in seed)

    db_src = read(BACKEND / "db.py")
    check("db.init_db 登记了新模型", "from app.models import role_permission" in db_src)
    check("db.init_db 播种并预热矩阵",
          "seed_roles_and_permissions" in db_src and "load_matrix" in db_src)

    main_src = read(BACKEND / "main.py")
    check("权限配置路由已挂载", "role_permissions.router" in main_src)
    check("响应头带权限版本（前端即时刷新依据）",
          "X-Perm-Version" in main_src and "get_cached_perm_version" in main_src)

    # ---- 【12】/auth/roles 与 /auth/me 已接矩阵/版本 ----
    auth_src = read(BACKEND / "routers" / "auth.py")
    check("/auth/roles 从矩阵取权限（登录页与配置同口径）",
          "get_matrix()" in auth_src)
    check("/auth/me 下发 perm_version", "perm_version" in auth_src)

    # ---- 【13】前端联动 ----
    api_src = read(FRONTEND / "api" / "index.ts")
    check("前端拦截器读取版本头", "x-perm-version" in api_src.lower())
    check("前端用回调避免 api↔store 循环依赖", "setPermVersionHandler" in api_src)
    check("前端有角色权限页", (FRONTEND / "views" / "RolePermissions.vue").exists())
    check("前端注册了刷新回调", "setPermVersionHandler" in read(FRONTEND / "main.ts"))
    router_src = read(FRONTEND / "router" / "index.ts")
    check("角色权限路由已登记且带 role_grant 门控",
          "role-permissions" in router_src and "permission: 'role_grant'" in router_src)
    layout_src = read(FRONTEND / "layouts" / "MainLayout.vue")
    check("菜单入口已添加", "role-permissions" in layout_src)

    # ---- 【14】迁移脚本 ----
    mig = ROOT / "scripts" / "migrate_step3_roles.py"
    check("迁移脚本存在且支持 dry-run", mig.exists() and "--dry-run" in read(mig))

    # ---------------- 输出 ----------------
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print("=" * 72)
    print("Step 3 静态验证（权限目录 / 矩阵 / 越权保护 / 即时生效）")
    print("=" * 72)
    for name, ok, detail in results:
        mark = "✅" if ok else "❌"
        line = f"{mark} {name}"
        if not ok and detail:
            line += f"\n      └─ {detail}"
        print(line)
    print("-" * 72)
    print(f"通过 {passed}/{total}")
    print(f"权限目录 {len(catalog)} 条 | 后端用到 {len(backend_keys)} 键 | "
          f"前端用到 {len(frontend_keys)} 键 | 矩阵角色 {len(matrix)} 个")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
