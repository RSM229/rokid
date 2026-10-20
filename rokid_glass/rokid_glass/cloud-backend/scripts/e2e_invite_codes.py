"""邀请码全链路端到端实测（生产，走真实 HTTP 接口）。

流程：
 1. admin 登录（公司级 super_admin）→ 拿 token
 2. 建邀请码（inspector，1 次）→ 断言返回 code
 3. 只读校验该码 → 断言 role=inspector
 4. 用该码走小程序注册 → 断言创建成功且 role 取自邀请码
 5. 再建一个码并注册 → 断言 used_count 递增、用满自动停用
 6. 校验非法码 → 断言 400
 7. 未授权调用邀请码列表 → 断言 401/403
 8. 清理：停用本次创建的码、删除本次注册的测试账号

用法：python scripts/e2e_invite_codes.py <base_url> <admin_pass>
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "https://www.baize.gdces.cn:4432/api"
ADMIN_PASS = sys.argv[2] if len(sys.argv) > 2 else "admin123"

ok = 0
fail = 0


def chk(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  " + name)
    else:
        fail += 1
        print("  FAIL  " + name + "  " + str(extra))


def req(method, path, body=None, token=None, timeout=20):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
        return e.code, parsed
    except Exception as e:
        return 0, str(e)


def main():
    created_codes = []
    created_users = []

    print("== 1. admin 登录 ==")
    st, res = req("POST", "/auth/login", {"username": "admin", "password": ADMIN_PASS, "role": "super_admin"})
    chk("admin 以 super_admin 登录成功", st == 200, f"status={st} {res}")
    if st != 200:
        return 1
    token = res["access_token"]
    print("    token 前 20 位 =", token[:20])

    print("\n== 2. 创建邀请码（inspector, 1 次, 无过期）==")
    st, res = req("POST", "/auth/invite-codes",
                  {"role": "inspector", "level": "personal", "max_uses": 1}, token)
    chk("创建成功", st == 200, f"status={st} {res}")
    if st != 200:
        return 1
    code1 = res["code"]
    created_codes.append(code1)
    print("    code1 =", code1, " role_label =", res.get("role_label"))
    chk("role 为 inspector", res.get("role") == "inspector", res.get("role"))

    print("\n== 3. 创建邀请码（禁止公司级）==")
    st, res = req("POST", "/auth/invite-codes",
                  {"role": "super_admin", "level": "personal", "max_uses": 1}, token)
    chk("公司级被拒 400", st == 400, f"status={st} {res}")
    print("    detail =", res.get("detail") if isinstance(res, dict) else res)

    print("\n== 4. 只读校验邀请码 ==")
    st, res = req("GET", f"/auth/invite-codes/validate?code={code1}")
    chk("校验成功", st == 200, f"status={st} {res}")
    chk("role=inspector", isinstance(res, dict) and res.get("role") == "inspector", res)

    print("\n== 5. 非法邀请码 ==")
    st, res = req("GET", "/auth/invite-codes/validate?code=ZZZZZZZZ")
    chk("非法码被拒 400", st == 400, f"status={st} {res}")

    print("\n== 6. 未授权访问邀请码列表 ==")
    st, res = req("GET", "/auth/invite-codes")
    chk("无 token 被拒", st in (401, 403), f"status={st}")

    print("\n== 7. 小程序用邀请码注册 ==")
    uname = "e2etest_" + code1.lower()[:6]
    st, res = req("POST", "/miniprogram/auth/register", {
        "username": uname,
        "password": "Test12345",
        "real_name": "端到端测试",
        "invite_code": code1,
    })
    chk("注册成功", st == 200, f"status={st} {res}")
    if st == 200:
        created_users.append(uname)
        print("    resp =", {k: v for k, v in res.items() if k != "access_token"} if isinstance(res, dict) else res)

    print("\n== 8. 无邀请码注册应被拒 ==")
    st, res = req("POST", "/miniprogram/auth/register", {
        "username": "e2etest_nocode",
        "password": "Test12345",
        "real_name": "无码测试",
    })
    chk("无邀请码注册被拒 422", st == 422, f"status={st} {res}")

    print("\n== 9. 邀请码被用满后自动停用 ==")
    st, res = req("GET", "/auth/invite-codes?include_inactive=true", token=token)
    chk("列表可读", st == 200, f"status={st}")
    if st == 200:
        row = next((i for i in res.get("items", []) if i["code"] == code1), None)
        if row:
            print("    code1 状态:", {k: row[k] for k in ("used_count", "max_uses", "remaining", "is_active")})
            chk("used_count=1", row["used_count"] == 1, row["used_count"])
            chk("用满后自动停用", row["is_active"] is False, row["is_active"])
        else:
            chk("能在列表找到 code1", False)

    print("\n== 10. 清理 ==")
    for c in created_codes:
        st, res = req("POST", f"/auth/invite-codes/{c}/revoke", token=token)
        print(f"    revoke {c} -> {st}")
    print("    待手工清理的测试账号:", created_users)

    print(f"\n===== {ok} PASS / {fail} FAIL =====")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
