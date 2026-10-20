"""Step 5 生产接口验收（用**临时项目**跑全流程，避免污染真实项目）。

覆盖清单 §8 十二条。收尾由外部脚本按打印出的 PID 清理。

用法：python _step5_acceptance.py
"""
from __future__ import annotations

import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = "https://www.baize.gdces.cn:4432"
TMP = Path("D:/rokid/.workbuddy/tmp")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

ADMIN = (TMP / "t5_admin.txt").read_text(encoding="utf-8").strip()
CHIEF = (TMP / "t5_chief.txt").read_text(encoding="utf-8").strip()

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""))


def call(method: str, path: str, token: str | None = None, body: dict | None = None):
    req = urllib.request.Request(BASE + path, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, context=CTX, timeout=40) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {}), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            payload = json.loads(raw) if raw else {}
        except Exception:
            payload = {"raw": raw[:200]}
        return exc.code, payload, dict(exc.headers)


def ids_of(payload) -> list[int]:
    return [p["id"] for p in (payload or [])]


def main() -> int:
    pid = None
    try:
        # ---- 准备：取 chief 的 user_id ----
        status, users, _ = call("GET", "/api/auth/users", ADMIN)
        items = users.get("items", []) if isinstance(users, dict) else []
        chief_id = next((u["id"] for u in items if u["username"] == "chief"), None)
        admin_id = next((u["id"] for u in items if u["username"] == "admin"), None)
        check("准备：定位 chief 账号", chief_id is not None, f"chief_id={chief_id}")

        # ---- 测1：权限矩阵（目录 +project_complete，超管行全量 27）----
        status, mx, _ = call("GET", "/api/permissions/matrix", ADMIN)
        check("测1 超管可读矩阵 (200)", status == 200, f"实际 {status}")
        perms = mx.get("permissions", [])
        keys = [p["key"] for p in perms]
        check("测1 目录含 project_complete", "project_complete" in keys)
        check("测1 目录 27 条", len(keys) == 27, f"实际 {len(keys)}")
        pc = next((p for p in perms if p["key"] == "project_complete"), {})
        check("测1 project_complete 标 org_level", pc.get("org_level") is True)
        check("测1 project_complete 归入「系统与人员」组",
              pc.get("group") == "系统与人员", f"实际 {pc.get('group')}")
        sa = next((r for r in mx.get("roles", []) if r["key"] == "super_admin"), {})
        check("测1 超管行=目录全量 27", len(sa.get("permissions", [])) == 27,
              f"实际 {len(sa.get('permissions', []))}")
        check("测1 超管行含 project_complete", "project_complete" in sa.get("permissions", []))
        proj_roles = [r for r in mx.get("roles", []) if r["key"] != "super_admin"]
        holders = [r["key"] for r in proj_roles if "project_complete" in r.get("permissions", [])]
        check("测1 其余角色默认均不持 project_complete", not holders, f"异常持有 {holders}")

        # ---- 准备：建临时项目（超管）----
        code = f"SP5TEST{int(time.time())}"
        status, proj, _ = call("POST", "/api/project/create", ADMIN,
                               {"name": "Step5验收临时项目", "code": code,
                                "short_name": "S5", "location": "测试", "description": "验收后删除"})
        check("准备：超管创建临时项目 (200)", status == 200, f"实际 {status} {proj}")
        if status != 200:
            print(f"PID={pid}")
            return 1
        pid = proj["id"]
        print(f"  >> 临时项目 PID={pid} code={code}")
        check("准备：新项目 status=active", proj.get("status") == "active")
        check("准备：新项目 completed_at 为空", not proj.get("completed_at"))

        # ---- 准备：把 chief 以非排他岗加入（admin 建项目时已占总监位）----
        status, r, _ = call("POST", f"/api/project/{pid}/members", ADMIN,
                            {"user_id": chief_id, "role": "inspector"})
        check("准备：加入成员 chief (200)", status == 200, f"实际 {status} {r}")
        check("准备：加入返回 detail=已加入项目", r.get("detail") == "已加入项目")

        # ---- 测6：members 默认只列在岗 ----
        status, m, _ = call("GET", f"/api/project/{pid}/members", ADMIN)
        present_before = len(m.get("items", []))
        check("测6 在岗成员 2 名（admin+chief）", present_before == 2, f"实际 {present_before}")
        check("测6 每条 is_present=True",
              all(i.get("is_present") for i in m.get("items", [])))
        check("测6 默认不含离场者", all(i.get("left_at") is None for i in m.get("items", [])))

        # ---- 测8：移出=软删除 ----
        status, r, _ = call("DELETE", f"/api/project/{pid}/members/{chief_id}", ADMIN)
        check("测8 移出成员 (200)", status == 200, f"实际 {status} {r}")
        check("测8 返回 left_at 非空", bool(r.get("left_at")))
        status, m2, _ = call("GET", f"/api/project/{pid}/members", ADMIN)
        check("测8 移出后在岗 -1", len(m2.get("items", [])) == present_before - 1,
              f"实际 {len(m2.get('items', []))}")
        status, m2l, _ = call("GET", f"/api/project/{pid}/members?include_left=true", ADMIN)
        left_items = [i for i in m2l.get("items", []) if not i.get("is_present")]
        check("测8 include_left 含离场者（行仍在）", len(m2l.get("items", [])) == present_before,
              f"实际 {len(m2l.get('items', []))}")
        check("测8 离场者 left_at 非空且 is_present=False",
              bool(left_items) and bool(left_items[0].get("left_at")))
        status, _r, _ = call("DELETE", f"/api/project/{pid}/members/{chief_id}", ADMIN)
        check("测8 重复 DELETE -> 404", status == 404, f"实际 {status}")

        # ---- 测9：重新加入=复职（清 left_at）----
        status, r, _ = call("POST", f"/api/project/{pid}/members", ADMIN,
                            {"user_id": chief_id, "role": "inspector"})
        check("测9 复职 (200)", status == 200, f"实际 {status} {r}")
        check("测9 detail=已更新项目岗位", r.get("detail") == "已更新项目岗位")
        status, m3, _ = call("GET", f"/api/project/{pid}/members", ADMIN)
        check("测9 复职后在岗恢复", len(m3.get("items", [])) == present_before,
              f"实际 {len(m3.get('items', []))}")
        chief_row = next((i for i in m3.get("items", []) if i["user_id"] == chief_id), {})
        check("测9 复职者 left_at 已清空", chief_row.get("left_at") is None)

        # ---- 测2：结项（批量置离场）----
        status, r, _ = call("POST", f"/api/project/{pid}/complete", ADMIN)
        check("测2 结项 (200)", status == 200, f"实际 {status} {r}")
        check("测2 status=completed", r.get("status") == "completed")
        check("测2 removed_members=结项前在岗数",
              r.get("removed_members") == present_before,
              f"实际 {r.get('removed_members')} vs {present_before}")
        check("测2 completed_at 非空", bool(r.get("completed_at")))
        check("测2 changed=True", r.get("changed") is True)
        status, r2, _ = call("POST", f"/api/project/{pid}/complete", ADMIN)
        check("测2 重复结项幂等 (changed=False)", r2.get("changed") is False)

        # ---- 测3：结项后成员列表过滤 ----
        status, lst, _ = call("GET", "/api/project/list", CHIEF)
        check("测3 结项项目不在 chief 默认列表", pid not in ids_of(lst))
        status, lst_c, _ = call("GET", "/api/project/list?include_closed=true", CHIEF)
        check("测3 chief 放开 include_closed 仍不可见（已离场=收口）",
              pid not in ids_of(lst_c))

        # ---- 测4：include_closed 对超管可见 ----
        status, lst_a, _ = call("GET", "/api/project/list", ADMIN)
        check("测4 超管默认列表不含已结项项目", pid not in ids_of(lst_a))
        status, lst_ac, _ = call("GET", "/api/project/list?include_closed=true", ADMIN)
        closed = [p for p in lst_ac if p["id"] == pid]
        check("测4 超管 include_closed 可见该项目", bool(closed))
        check("测4 该项目 status=completed",
              bool(closed) and closed[0].get("status") == "completed")

        # ---- 测5：切入已结项项目被拒 ----
        status, r, _ = call("POST", f"/api/project/{pid}/switch", CHIEF)
        check("测5 非公司级切入已结项项目 -> 403", status == 403, f"实际 {status}")

        # ---- 测7：结项后 include_left 追溯 ----
        status, m4, _ = call("GET", f"/api/project/{pid}/members?include_left=true", ADMIN)
        check("测7 include_left 含全部成员", len(m4.get("items", [])) == present_before,
              f"实际 {len(m4.get('items', []))}")
        check("测7 全部已离场（is_present=False）",
              all(not i.get("is_present") for i in m4.get("items", [])))
        check("测7 每条带 left_at 与 joined_at",
              all(i.get("left_at") and i.get("joined_at") for i in m4.get("items", [])))
        status, m4p, _ = call("GET", f"/api/project/{pid}/members", ADMIN)
        check("测7 默认在岗列表为空", len(m4p.get("items", [])) == 0,
              f"实际 {len(m4p.get('items', []))}")

        # ---- 测10：恢复为进行中 ----
        status, r, _ = call("POST", f"/api/project/{pid}/reopen", ADMIN)
        check("测10 恢复 (200)", status == 200, f"实际 {status} {r}")
        check("测10 status=active", r.get("status") == "active")
        status, lst_ac2, _ = call("GET", "/api/project/list?include_closed=true", ADMIN)
        row = next((p for p in lst_ac2 if p["id"] == pid), {})
        check("测10 completed_at 已清空", row.get("completed_at") is None)
        status, m5, _ = call("GET", f"/api/project/{pid}/members", ADMIN)
        check("测10 成员未自动恢复（需重新任命）", len(m5.get("items", [])) == 0,
              f"实际 {len(m5.get('items', []))}")

        # ---- 测11：总监无 project_complete ----
        status, _r, _ = call("POST", f"/api/project/{pid}/complete", CHIEF)
        check("测11 总监结项 -> 403", status == 403, f"实际 {status}")
        status, _r, _ = call("POST", f"/api/project/{pid}/reopen", CHIEF)
        check("测11 总监恢复 -> 403", status == 403, f"实际 {status}")

    finally:
        print(f"PID={pid}")
        passed = sum(1 for _, ok, _ in results if ok)
        print(f"\n===== 验收结果：{passed}/{len(results)} =====")
        for name, ok, detail in results:
            if not ok:
                print(f"  FAILED: {name}  {detail}")
    return 0 if all(ok for _, ok, _ in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
