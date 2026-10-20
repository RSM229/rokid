"""「忘记密码」自助重置 + 密保问题 + 强制改密 —— 生产端到端验收。

对**生产环境**发起真实 HTTP 调用，覆盖：
  ① 抗枚举（账号不存在 / 未设密保 → 同一响应）
  ② 密保问题设置 / 查看
  ③ 密保答案校验（错误拒绝、正确发凭证）
  ④ 自助重置（凭证一次性、改密后旧凭证失效）
  ⑤ 管理员兜底：重置为一次性临时口令 + 首次登录强制改密
  ⑥ 密保答案爆破限流（429）

**自恢复**：全程使用 inspector 账号，结束时把密码恢复为初始值、清空密保问题，
恢复生产原状（脚本中途失败也会在 finally 里尝试恢复）。

用法：
    python docs/测试产物/_password_reset_acceptance.py
凭据从 .workbuddy/账号密码清单-2026-10-12.txt 读取。
"""
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

BASE = "https://www.baize.gdces.cn:4432/api"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PWD_FILE = os.path.join(ROOT, ".workbuddy", "账号密码清单-2026-10-12.txt")

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name} {detail}".strip())
        print(f"  [FAIL] {name} {detail}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def call(method: str, path: str, body=None, token: str | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "ignore")
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, {"raw": raw[:200]}
    except Exception as exc:  # 网络层
        return 0, {"error": str(exc)}


def load_passwords() -> dict:
    text = open(PWD_FILE, encoding="utf-8").read()
    result = {}
    for line in text.splitlines():
        m = re.match(r"^(admin|chief|deputy|supervisor|inspector)\s+\S+\s+(\S+)\s*$", line.strip())
        if m:
            result[m.group(1)] = m.group(2)
    return result


def main() -> None:
    pwds = load_passwords()
    if "admin" not in pwds or "inspector" not in pwds:
        print("无法从密码清单解析出 admin / inspector 口令")
        sys.exit(2)
    inspector_pwd0 = pwds["inspector"]

    # 准备两个临时口令（脚本内自造，不落盘）
    pwd_self_reset = "Zt7#kQm2Wp9$Xr4N"
    pwd_after_admin = "Hy3@Ld8Vn6^Kw2Bt"
    questions = [
        {"question": "我的小学名称", "answer": "实验小学"},
        {"question": "我最喜欢的城市", "answer": "广州"},
    ]

    inspector_token = None
    inspector_id = None
    restored = False

    try:
        # ---------- 0. 基础 ----------
        section("0. 基础探活")
        status, _ = call("GET", "/auth/me")
        check("未登录 /auth/me 返回 401", status == 401, f"status={status}")

        status, res = call("POST", "/auth/login",
                           {"username": "admin", "password": pwds["admin"]})
        admin_token = res.get("access_token")
        check("管理员登录成功", status == 200 and bool(admin_token), f"status={status}")

        status, res = call("POST", "/auth/login",
                           {"username": "inspector", "password": inspector_pwd0})
        inspector_token = res.get("access_token")
        inspector_id = res.get("user_id")
        check("inspector 初始口令登录成功", status == 200 and bool(inspector_token),
              f"status={status}")
        check("登录响应携带 must_change_password=false",
              res.get("must_change_password") is False, str(res.get("must_change_password")))

        # ---------- 1. 抗枚举 ----------
        section("1. 抗枚举（账号不存在 / 未设密保）")
        status, no_user = call("POST", "/auth/forgot/questions",
                               {"username": "__no_such_user__"})
        check("不存在账号：HTTP 200 且 available=false",
              status == 200 and no_user.get("available") is False, f"status={status}")
        status, before_set = call("POST", "/auth/forgot/questions", {"username": "inspector"})
        check("未设密保时：与不存在账号同一响应",
              status == 200 and before_set.get("available") is False,
              f"status={status} body={before_set}")
        check("两种情况的 message 完全一致",
              no_user.get("message") == before_set.get("message"),
              f"{no_user.get('message')} vs {before_set.get('message')}")

        # ---------- 2. 密保问题设置 / 查看 ----------
        section("2. 密保问题设置与查看")
        status, _ = call("GET", "/auth/me/security-questions")
        check("未登录查看密保返回 401", status == 401, f"status={status}")

        status, res = call("PUT", "/auth/me/security-questions",
                           {"current_password": "wrong-pwd", "questions": questions},
                           token=inspector_token)
        check("原密码错误 → 400", status == 400, f"status={status}")

        status, res = call("PUT", "/auth/me/security-questions",
                           {"current_password": inspector_pwd0, "questions": questions},
                           token=inspector_token)
        check("设置密保问题成功", status == 200 and res.get("count") == 2,
              f"status={status} body={res}")

        status, res = call("GET", "/auth/me/security-questions", token=inspector_token)
        check("查看密保 is_set=true", status == 200 and res.get("is_set") is True,
              f"status={status}")
        check("只回题目不回答案",
              res.get("questions") == [q["question"] for q in questions],
              str(res.get("questions")))

        status, res = call("PUT", "/auth/me/security-questions",
                           {"current_password": inspector_pwd0,
                            "questions": [questions[0], questions[0]]},
                           token=inspector_token)
        check("重复密保问题 → 400", status == 400, f"status={status}")

        # ---------- 3. 忘记密码：取题 + 校验 ----------
        section("3. 忘记密码 —— 取题与校验")
        status, res = call("POST", "/auth/forgot/questions", {"username": "inspector"})
        check("取回题目 available=true", status == 200 and res.get("available") is True,
              f"status={status}")
        check("题目与设置一致",
              res.get("questions") == [q["question"] for q in questions],
              str(res.get("questions")))

        status, res = call("POST", "/auth/forgot/verify",
                           {"username": "inspector", "answers": ["错的", "错的"]})
        check("密保答案错误 → 400", status == 400, f"status={status} body={res}")

        status, res = call("POST", "/auth/forgot/verify",
                           {"username": "inspector", "answers": ["实验小学", "广州"]})
        token1 = res.get("reset_token")
        check("密保答案正确 → 发放重置凭证",
              status == 200 and bool(token1), f"status={status} body={res}")
        if token1:
            parts = token1.split(".")
            payload = json.loads(
                __import__("base64").urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
            )
            check("凭证 purpose=pwd_reset", payload.get("purpose") == "pwd_reset",
                  str(payload.get("purpose")))

        # 大小写 / 空格不敏感
        status, res = call("POST", "/auth/forgot/verify",
                           {"username": "inspector", "answers": ["  实验小学 ", "广州"]})
        check("答案大小写/空格不敏感", status == 200 and bool(res.get("reset_token")),
              f"status={status}")

        # ---------- 4. 自助重置 ----------
        section("4. 自助重置密码")
        status, res = call("POST", "/auth/forgot/reset",
                           {"reset_token": token1, "new_password": pwd_self_reset})
        check("凭凭证重置成功", status == 200, f"status={status} body={res}")

        status, res = call("POST", "/auth/login",
                           {"username": "inspector", "password": pwd_self_reset})
        check("新口令可登录", status == 200, f"status={status}")
        status, _ = call("POST", "/auth/login",
                         {"username": "inspector", "password": inspector_pwd0})
        check("旧口令失效 → 401", status == 401, f"status={status}")

        status, res = call("POST", "/auth/forgot/reset",
                           {"reset_token": token1, "new_password": "Another#Pwd123"})
        check("同一凭证不能二次使用（一次性）", status == 400,
              f"status={status} body={res}")

        # ---------- 5. 管理员兜底 ----------
        section("5. 管理员兜底：一次性临时口令 + 强制改密")
        status, res = call("POST", f"/auth/users/{inspector_id}/reset-password",
                           token=admin_token)
        temp_pwd = res.get("temp_password")
        check("管理员重置成功并返回临时口令",
              status == 200 and bool(temp_pwd), f"status={status} body={res}")
        check("临时口令不含易混字符",
              not (set(temp_pwd or "") & set("IlOo01")), str(temp_pwd))

        status, res = call("POST", "/auth/login",
                           {"username": "inspector", "password": temp_pwd})
        check("临时口令可登录", status == 200, f"status={status}")
        check("登录响应 must_change_password=true",
              res.get("must_change_password") is True,
              str(res.get("must_change_password")))

        status, res = call("PUT", "/auth/me/password",
                           {"current_password": temp_pwd, "new_password": pwd_after_admin},
                           token=inspector_token)
        check("用临时口令改密成功", status == 200, f"status={status} body={res}")

        status, res = call("POST", "/auth/login",
                           {"username": "inspector", "password": pwd_after_admin})
        check("改密后新口令可登录", status == 200, f"status={status}")
        check("must_change_password 已清零",
              res.get("must_change_password") is False,
              str(res.get("must_change_password")))

        # 非超管 / 未登录不能调管理员重置
        status, _ = call("POST", f"/auth/users/{inspector_id}/reset-password")
        check("未登录调用管理员重置 → 401", status == 401, f"status={status}")

        # ---------- 6. 限流 ----------
        section("6. 密保答案爆破限流")
        codes = []
        for _ in range(7):
            code, _b = call("POST", "/auth/forgot/verify",
                            {"username": "__no_such_user__", "answers": ["x"]})
            codes.append(code)
        check("连续失败后出现 429 限流", 429 in codes, str(codes))
        check("限流不影响真实账号（inspector 仍可取题）",
              call("POST", "/auth/forgot/questions", {"username": "inspector"})[0] == 200)

        # ---------- 7. 恢复原状 ----------
        section("7. 恢复生产原状")
        status, res = call("POST", "/auth/login",
                           {"username": "inspector", "password": pwd_after_admin})
        tk = res.get("access_token")
        status, _ = call("PUT", "/auth/me/password",
                         {"current_password": pwd_after_admin, "new_password": inspector_pwd0},
                         token=tk or inspector_token)
        check("密码已恢复为初始值", status == 200, f"status={status}")
        status, _ = call("PUT", "/auth/me/security-questions",
                         {"current_password": inspector_pwd0, "questions": []},
                         token=tk or inspector_token)
        check("密保问题已清空", status == 200, f"status={status}")
        status, _ = call("POST", "/auth/login",
                         {"username": "inspector", "password": inspector_pwd0})
        check("恢复后初始口令可登录", status == 200, f"status={status}")
        restored = True

    finally:
        if not restored and inspector_token:
            print("\n[警告] 脚本中断，尝试恢复 inspector 口令与密保…")
            for cand in (pwd_self_reset, pwd_after_admin):
                st, rs = call("POST", "/auth/login",
                              {"username": "inspector", "password": cand})
                if st == 200:
                    call("PUT", "/auth/me/password",
                         {"current_password": cand, "new_password": inspector_pwd0},
                         token=rs.get("access_token"))
                    print("  已恢复口令")
                    break

    print(f"\n===== 结果：{PASS} 通过 / {FAIL} 失败 =====")
    if FAILURES:
        print("失败项：")
        for item in FAILURES:
            print("  -", item)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
