"""Step 5 静态逻辑验证：转场留痕（软删除）与项目生命周期。

纯静态（AST + 文本），不连数据库、不依赖 sqlalchemy —— 与
``verify_step1_logic.py`` / ``verify_step3_logic.py`` 同风格，
可在无依赖的机器上跑（本项目本机即无 sqlalchemy）。

覆盖（设计说明 §7.7④⑤ / §8.4 Step 5）：

  1. 数据层：``Project.completed_at`` 落地、``status`` 取值含 completed
  2. 权限键：``project_complete`` 入目录（org_level）+ 超管种子 + 公司级集合
  3. 软删除：``remove_member`` 不再物理删除，改写 ``left_at``
  4. 列表过滤：``list_projects`` / ``switch_project`` 补齐有效成员与状态判据
  5. 生命周期：结项批量置离场 + 清上下文；恢复只改状态
  6. 转场追溯：``list_members`` 支持 ``include_left`` 并出参含在场区间
  7. 接口自洽：``read_matrix`` 超管行返回目录全量
  8. 前端：项目页生命周期 UI + 成员页在场区间
  9. 迁移脚本：``completed_at`` 前滚列
 10. 全局回归：除 remove_member 外无针对 ProjectMember 的物理删除
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND.parent
WEB = PROJECT_ROOT / "web-admin" / "src"

_passed = 0
_failed: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global _passed
    if ok:
        _passed += 1
        print(f"  ✅ {label}")
    else:
        _failed.append(label)
        print(f"  ❌ {label}" + (f" —— {detail}" if detail else ""))


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _module_assign(tree: ast.Module, name: str):
    """取模块级赋值节点，兼容 ``Assign`` 与 ``AnnAssign``（带注解）。

    🔴 本项目踩过的坑：``X: dict = {...}`` 是 ``AnnAssign``，
    只判 ``ast.Assign`` 会静默解析出 0 条并造成假阳性。
    """
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                return node.value
    return None


def _func_source(src: str, tree: ast.Module, name: str) -> str:
    """某函数/异步函数的源码段（找不到返回空串）。"""
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            seg = ast.get_source_segment(src, node)
            if seg:
                return seg
    return ""

def main() -> int:
    project_router = BACKEND / "app/routers/project.py"
    user_model = BACKEND / "app/models/user.py"
    catalog = BACKEND / "app/models/permission_catalog.py"
    role_router = BACKEND / "app/routers/role_permissions.py"
    migration = BACKEND / "scripts/migrate_step5_lifecycle.py"

    pr_src = read(project_router)
    pr_tree = ast.parse(pr_src)
    um_src = read(user_model)
    um_tree = ast.parse(um_src)

    # ------------------------------------------------------------------
    print("\n【1】数据层：项目生命周期字段")
    # ------------------------------------------------------------------
    project_cls = next(
        (n for n in ast.walk(um_tree)
         if isinstance(n, ast.ClassDef) and n.name == "Project"),
        None,
    )
    project_fields = set()
    for stmt in ast.walk(project_cls) if project_cls else []:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            project_fields.add(stmt.target.id)
    check("Project.completed_at 已定义", "completed_at" in project_fields)
    check("Project.status 已定义", "status" in project_fields)
    check(
        "status 取值说明含 completed",
        "completed" in um_src and "archived" in um_src,
    )

    # ------------------------------------------------------------------
    print("\n【2】权限键：project_complete")
    # ------------------------------------------------------------------
    cat_tree = ast.parse(read(catalog))
    cat_entries: dict[str, dict] = {}
    cat_node = None
    for node in ast.walk(cat_tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "PERMISSION_CATALOG":
                    cat_node = node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "PERMISSION_CATALOG":
                cat_node = node.value
    if cat_node is not None and isinstance(cat_node, (ast.Tuple, ast.List)):
        for elt in cat_node.elts:
            if isinstance(elt, ast.Call) and elt.args:
                first = elt.args[0]
                if isinstance(first, ast.Constant):
                    kwargs = {
                        kw.arg: (kw.value.value if isinstance(kw.value, ast.Constant) else None)
                        for kw in elt.keywords
                    }
                    cat_entries[first.value] = kwargs
    check("目录含 project_complete", "project_complete" in cat_entries)
    check(
        "project_complete 标记为 org_level（仅公司级可持有）",
        bool(cat_entries.get("project_complete", {}).get("org_level")),
    )
    check("目录仍含原有 project_edit（未误删）", "project_edit" in cat_entries)

    # ROLE_PERMISSIONS[super_admin] 与 ORG_LEVEL_PERMISSIONS
    rp_node = _module_assign(um_tree, "ROLE_PERMISSIONS")
    super_perms: set[str] = set()
    if isinstance(rp_node, ast.Dict):
        for key, value in zip(rp_node.keys, rp_node.values):
            is_super = (
                isinstance(key, ast.Attribute)
                and key.attr == "value"
                and isinstance(key.value, ast.Name)
                and key.value.id == "ORG_ROLE_SUPER_ADMIN"
            ) or (
                # 公司级角色在字典里是**裸 Name**（ORG_ROLE_SUPER_ADMIN），
                # 项目级岗位才是 ``UserRole.X.value`` 形态 —— 两种都要认。
                isinstance(key, ast.Name) and key.id == "ORG_ROLE_SUPER_ADMIN"
            )
            if is_super and isinstance(value, ast.List):
                super_perms = {
                    e.value for e in value.elts if isinstance(e, ast.Constant)
                }
    check("种子中 super_admin 含 project_complete", "project_complete" in super_perms)

    ol_node = _module_assign(um_tree, "ORG_LEVEL_PERMISSIONS")
    org_level_keys: set[str] = set()
    if isinstance(ol_node, ast.Call) and ol_node.args:
        arg = ol_node.args[0]
        if isinstance(arg, (ast.Set, ast.List, ast.Tuple)):
            org_level_keys = {
                e.value for e in arg.elts if isinstance(e, ast.Constant)
            }
    check("ORG_LEVEL_PERMISSIONS 含 project_complete", "project_complete" in org_level_keys)

    # ------------------------------------------------------------------
    print("\n【3】软删除：remove_member 改写 left_at")
    # ------------------------------------------------------------------
    rm = _func_source(pr_src, pr_tree, "remove_member")
    check("定位到 remove_member", bool(rm))
    check("remove_member 不再物理删除成员", "db.delete(member)" not in rm and "db.delete(" not in rm)
    check("remove_member 写入 left_at", bool(re.search(r"member\.left_at\s*=\s*datetime\.now\(\)", rm)))
    check(
        "remove_member 只对在岗成员操作（left_at IS NULL）",
        "left_at.is_(None)" in rm,
    )
    check("remove_member 仍清空上下文", "project_id = None" in rm)

    # ------------------------------------------------------------------
    print("\n【4】列表过滤：有效成员 + 项目状态")
    # ------------------------------------------------------------------
    lp = _func_source(pr_src, pr_tree, "list_projects")
    check("list_projects 过滤在岗成员（left_at IS NULL）", "left_at.is_(None)" in lp)
    check("list_projects 支持 include_closed", "include_closed" in lp)
    check("list_projects 按状态过滤", "PROJECT_STATUS_ACTIVE" in lp)

    sp = _func_source(pr_src, pr_tree, "switch_project")
    check("switch_project 成员校验补 left_at", "left_at.is_(None)" in sp)
    check("switch_project 拒绝切入已结项项目", "PROJECT_STATUS_ACTIVE" in sp)

    # ------------------------------------------------------------------
    print("\n【5】生命周期：结项 / 恢复")
    # ------------------------------------------------------------------
    cp = _func_source(pr_src, pr_tree, "complete_project")
    check("定位到 complete_project", bool(cp))
    check("结项挂 project_complete 权限", 'require_permission("project_complete")' in cp)
    check("结项批量置离场", bool(re.search(r"member\.left_at\s*=\s*now", cp)))
    check("结项置 status=completed", "PROJECT_STATUS_COMPLETED" in cp)
    check("结项写 completed_at", "project.completed_at" in cp)
    check("结项清空停留用户上下文", "project_id = None" in cp)

    rp = _func_source(pr_src, pr_tree, "reopen_project")
    check("定位到 reopen_project", bool(rp))
    check("恢复挂 project_complete 权限", 'require_permission("project_complete")' in rp)
    check("恢复置 status=active", "PROJECT_STATUS_ACTIVE" in rp)
    check("恢复清空 completed_at", "completed_at = None" in rp)
    check(
        "恢复不自动复职成员（留痕不被抹掉）",
        "left_at = None" not in rp and "db.add(ProjectMember" not in rp,
    )

    # ------------------------------------------------------------------
    print("\n【6】转场追溯：list_members 支持历史")
    # ------------------------------------------------------------------
    lm = _func_source(pr_src, pr_tree, "list_members")
    check("list_members 支持 include_left", "include_left" in lm)
    check("出参含 left_at", '"left_at"' in lm)
    check("出参含 is_present", '"is_present"' in lm)
    check("默认只列在岗（条件式过滤）", "left_at.is_(None)" in lm)

    # ------------------------------------------------------------------
    print("\n【7】接口自洽：read_matrix 超管全量")
    # ------------------------------------------------------------------
    rr_src = read(role_router)
    check("read_matrix 超管行取目录全量", "ALL_PERMISSION_KEYS" in rr_src)
    check(
        "超管行按 role.key 判定",
        "role.key == ORG_ROLE_SUPER_ADMIN" in rr_src,
    )

    # ------------------------------------------------------------------
    print("\n【8】前端：生命周期 UI 与在场区间")
    # ------------------------------------------------------------------
    pm_src = read(WEB / "views/ProjectManage.vue")
    check("ProjectManage 有 canComplete", "canComplete" in pm_src and "project_complete" in pm_src)
    check("ProjectManage 有 includeClosed", "includeClosed" in pm_src)
    check("ProjectManage 有状态标签映射", "PROJECT_STATUS_LABELS" in pm_src)
    check("ProjectManage 有 completeProject", "completeProject" in pm_src)
    check("ProjectManage 有 reopenProject", "reopenProject" in pm_src)

    umv_src = read(WEB / "views/UserManage.vue")
    check("UserManage 有 memberIncludeLeft", "memberIncludeLeft" in umv_src)
    check("UserManage 有在场区间函数", "presenceRange" in umv_src)
    check("UserManage 有重新加入", "restoreMember" in umv_src)

    api_src = read(WEB / "api/index.ts")
    check("api 有 projectApi.complete", "complete: (projectId: number)" in api_src)
    check("api 有 projectApi.reopen", "reopen: (projectId: number)" in api_src)
    check("api members 支持 includeLeft", "includeLeft" in api_src)
    check("api list 支持 includeClosed", "includeClosed" in api_src)

    # ------------------------------------------------------------------
    print("\n【9】迁移脚本")
    # ------------------------------------------------------------------
    mig_src = read(migration)
    check("迁移脚本含 completed_at", "completed_at" in mig_src)
    check("迁移脚本支持 dry-run", "--dry-run" in mig_src)
    check("迁移脚本幂等（列存在性检查）", "_col_exists" in mig_src)

    # ------------------------------------------------------------------
    print("\n【10】全局回归：无遗漏的物理删除")
    # ------------------------------------------------------------------
    offenders: list[str] = []
    for py in (BACKEND / "app").rglob("*.py"):
        text = read(py)
        if "ProjectMember" not in text:
            continue
        for match in re.finditer(r"db\.delete\((\w+)\)", text):
            var = match.group(1)
            # 只关心删除 ProjectMember 实例的写法
            if re.search(rf"{var}\s*=\s*\(?.*ProjectMember", text) or \
               re.search(rf"select\(ProjectMember\).*?{var}\s*=", text, re.S):
                offenders.append(f"{py.name}:{match.group(0)}")
    check(
        "app/ 下无针对 ProjectMember 的物理删除",
        not offenders,
        ", ".join(offenders),
    )

    # ------------------------------------------------------------------
    total = _passed + len(_failed)
    print(f"\n{'=' * 56}")
    print(f"Step 5 静态验证：{_passed}/{total} 通过")
    if _failed:
        print("失败项：")
        for item in _failed:
            print(f"  - {item}")
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
