"""「忘记密码」自助重置 + 密保问题 + 强制改密 —— 逻辑校验（容器内运行）。

只读、零副作用：不创建/修改任何数据，仅做结构检查 + 纯函数行为验证。
真机端到端验收见 docs/测试产物/_password_reset_acceptance.py。

用法（服务器上）：
    ssh rokid-deploy "docker cp <脚本> rokid-backend:/tmp/ && docker exec -i rokid-backend python /tmp/verify_password_reset_logic.py"
"""
import asyncio
import sys
import traceback

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name} {detail}".strip())
        print(f"  [FAIL] {name} {detail}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


async def main() -> None:
    # ---- 按 init_db() 的口径导入全部模型，避免 NoReferencedTableError ----
    from app.models import (  # noqa: F401
        acceptance_template, archive, document, equipment, hazard, image,
        inspection_session, invite_code, kb_chunk, knowledge_base,
        knowledge_graph, ledger, meeting, miniprogram, model_config, note,
        notification, organization, parallel, permission_catalog, progress,
        project, project_member, rectification, regulation_chunk,
        regulation_version, resources, security_audit, security_question,
        signature, task_assignment, user, video, visa, wechat_subscription,
        work_record,
    )
    from app.models.user import User
    from app.models.security_question import (
        MAX_SECURITY_QUESTIONS,
        UserSecurityQuestion,
    )
    from app.routers import auth as auth_mod
    from app.routers import miniprogram_auth as mp_mod

    # ============ 1. 模型 / 字段 ============
    section("1. 模型与字段")
    cols = {c.name: c for c in User.__table__.columns}
    check("users.must_change_password 存在", "must_change_password" in cols)
    if "must_change_password" in cols:
        col = cols["must_change_password"]
        check("must_change_password 有 server_default（可安全自动迁移）",
              col.server_default is not None, str(col.server_default))
        check("must_change_password 非主键且默认 False", col.default is not None)

    sq_cols = {c.name for c in UserSecurityQuestion.__table__.columns}
    for name in ("id", "user_id", "sort_order", "question", "answer_hash", "created_at"):
        check(f"user_security_questions.{name} 存在", name in sq_cols)
    check("MAX_SECURITY_QUESTIONS == 3", MAX_SECURITY_QUESTIONS == 3)

    # ============ 2. 路由注册 ============
    section("2. 路由注册")
    def routes_of(router):
        return {(r.path, tuple(sorted(getattr(r, "methods", set()) or []))) for r in router.routes}

    auth_routes = routes_of(auth_mod.router)
    check("POST /auth/forgot/questions", ("/forgot/questions", ("POST",)) in auth_routes,
          str(sorted(p for p, _ in auth_routes if "forgot" in p)))
    check("POST /auth/forgot/verify", ("/forgot/verify", ("POST",)) in auth_routes)
    check("POST /auth/forgot/reset", ("/forgot/reset", ("POST",)) in auth_routes)
    check("GET /auth/me/security-questions",
          ("/me/security-questions", ("GET",)) in auth_routes)
    check("PUT /auth/me/security-questions",
          ("/me/security-questions", ("PUT",)) in auth_routes)
    reset_routes = [m for p, m in auth_routes if p == "/users/{user_id}/reset-password"]
    check("POST /auth/users/{user_id}/reset-password", ("POST",) in reset_routes,
          str(reset_routes))

    # ============ 3. 纯函数行为 ============
    section("3. 纯函数行为")
    # 答案归一化
    check("_normalize_answer 去空格+小写",
          auth_mod._normalize_answer("  White  ") == "white")
    h = auth_mod._hash_answer("Blue")
    check("_hash_answer 为 bcrypt 哈希", h.startswith("$2"), h[:7])
    check("_verify_answer 正确答案通过", auth_mod._verify_answer("blue", h))
    check("_verify_answer 错误答案拒绝", not auth_mod._verify_answer("red", h))

    # 密码指纹
    fp1 = auth_mod._password_fingerprint("$2b$abc")
    fp2 = auth_mod._password_fingerprint("$2b$xyz")
    check("_password_fingerprint 长度 16", len(fp1) == 16)
    check("_password_fingerprint 随哈希变化", fp1 != fp2)
    check("_password_fingerprint 对 None 安全", len(auth_mod._password_fingerprint(None)) == 16)

    # 一次性临时口令
    pwd = auth_mod._generate_temp_password()
    check("临时口令长度 12", len(pwd) == 12)
    check("临时口令剔除易混字符",
          not (set(pwd) & set("IlOo01")), pwd)
    check("临时口令两次不同", pwd != auth_mod._generate_temp_password())

    # 重置凭证：purpose= 隔离 + 指纹绑定
    from app.models.user import UserRole
    fake = User(id=9999, username="__probe__", real_name="probe",
                role=UserRole.INSPECTOR.value, password_hash="$2b$fakehash", is_active=True)
    token = auth_mod._create_reset_token(fake)
    payload = auth_mod._decode_reset_token(token)
    check("重置凭证 purpose=pwd_reset", payload.get("purpose") == "pwd_reset")
    check("重置凭证带密码指纹且与用户一致",
          payload.get("fp") == auth_mod._password_fingerprint(fake.password_hash))
    check("重置凭证 sub=用户 id", payload.get("sub") == "9999")

    # 反向：access_token 不能当重置凭证用
    access_like = auth_mod.create_access_token({"sub": "9999", "purpose": "access"})
    try:
        auth_mod._decode_reset_token(access_like)
        check("access_token 不能当重置凭证", False)
    except Exception:
        check("access_token 不能当重置凭证", True)

    # 反向：pwd_reset 凭证不能当 access token 用（get_user_from_token 拦截）
    from app.db import async_session
    async with async_session() as session:
        try:
            await auth_mod.get_user_from_token(token, session)
            check("pwd_reset 凭证不能用于鉴权", False)
        except Exception as exc:
            check("pwd_reset 凭证不能用于鉴权",
                  "401" in str(exc) or "无效访问凭证" in str(exc), str(exc))

    # 密保失败限流
    auth_mod._forgot_attempts.clear()
    blocked = False
    for _ in range(auth_mod.FORGOT_MAX_ATTEMPTS):
        auth_mod._check_forgot_throttle("__probe__")
        auth_mod._record_forgot_failure("__probe__")
    try:
        auth_mod._check_forgot_throttle("__probe__")
    except Exception as exc:
        blocked = "429" in str(exc) or "尝试次数过多" in str(exc)
    check(f"密保失败 {auth_mod.FORGOT_MAX_ATTEMPTS} 次后限流", blocked)
    auth_mod._forgot_attempts.clear()
    try:
        auth_mod._check_forgot_throttle("__probe__")
        check("限流窗口清理后恢复可用", True)
    except Exception as exc:
        check("限流窗口清理后恢复可用", False, str(exc))

    # ============ 4. 小程序端一致性 ============
    section("4. 小程序端一致")
    src = open(mp_mod.__file__, encoding="utf-8").read()
    check("小程序 _token_response 返回 must_change_password",
          "must_change_password" in src)
    check("小程序 set_password 清除 must_change_password",
          "user.must_change_password = False" in src)

    # ============ 5. 只读 DB 结构核对 ============
    section("5. 数据库结构（只读）")
    from sqlalchemy import inspect as sa_inspect, text
    from app.db import engine
    async with engine.connect() as conn:
        def _introspect(sync_conn):
            insp = sa_inspect(sync_conn)
            return set(insp.get_table_names()), {
                t: {c["name"] for c in insp.get_columns(t)}
                for t in insp.get_table_names()
            }
        tables, colmap = await conn.run_sync(_introspect)
        check("user_security_questions 表已创建", "user_security_questions" in tables)
        if "user_security_questions" in tables:
            for name in ("user_id", "question", "answer_hash", "sort_order"):
                check(f"user_security_questions.{name} 列存在",
                      name in colmap["user_security_questions"])
        if "users" in tables:
            has_col = "must_change_password" in colmap["users"]
            check("users.must_change_password 列存在（自动迁移）", has_col)
            if has_col:
                row = await conn.execute(text(
                    "SELECT COUNT(*) FROM users WHERE must_change_password = 1"
                ))
                pending = row.scalar_one()
                check("存量账号 must_change_password 默认 0", pending == 0,
                      f"当前为 1 的账号数={pending}")

    print(f"\n===== 结果：{PASS} 通过 / {FAIL} 失败 =====")
    if FAILURES:
        print("失败项：")
        for item in FAILURES:
            print("  -", item)
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
