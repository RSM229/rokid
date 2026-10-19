"""后端接口只读权限矩阵测试（零副作用，仅发 GET）。

测试目标（只读范围内能查出的问题）：
  1. 未登录（anonymous）能访问哪些接口 → 应全部 401/403，例外须复核
  2. 低权限角色（inspector）能读到哪些高权限数据 → 越权读
  3. 各角色在同一接口的状态码差异 → 权限边界是否符合设计

只发 GET，不改任何数据。

⚠️ 判据说明：`declared_perm`/`declared_role` 取自 OpenAPI 里**该路由模块**声明的权限，
同一个 router 内 GET 常用 `get_current_user`（仅登录）而 POST/PUT/DELETE 才用
`require_permission`。因此「inspector 拿到 200 且 declared_perm 非空」**不等于越权**，
需回源码看**该 GET 端点本身**的依赖。本脚本的 `INSP_MAYBE_OVERREACH` 只是**候选清单**。
"""
import asyncio
import csv
import json
import sys

import httpx

BASE = "https://www.baize.gdces.cn:4432"
ACCOUNTS = {
    "anonymous": None,
    "inspector": "admin123",
    "supervisor": "admin123",
    "deputy": "admin123",
    "chief": "admin123",
    "admin": "admin123",
}
SUBST = {
    "{project_id}": "1", "{user_id}": "4", "{task_id}": "1",
    "{hazard_id}": "1", "{document_id}": "1", "{ledger_id}": "1",
    "{id}": "1", "{binding_id}": "1", "{kb_id}": "1", "{meeting_id}": "1",
    "{note_id}": "1", "{session_id}": "1", "{code}": "PMX3RV6S",
}


def subst(p: str) -> str:
    for k, v in SUBST.items():
        p = p.replace(k, v)
    return p


async def login(client, username, password):
    r = await client.post(f"{BASE}/api/auth/login",
                          json={"username": username, "password": password}, timeout=20)
    if r.status_code != 200:
        return None, f"{r.status_code}"
    return r.json().get("access_token"), ""


async def get(client, url, token):
    h = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        r = await client.get(url, headers=h, timeout=20)
        return r.status_code, (r.text[:100].replace("\n", " ") if r.status_code >= 400 else "")
    except Exception as e:  # noqa: BLE001
        return -1, repr(e)[:80]


async def main():
    inv = sys.argv[1] if len(sys.argv) > 1 else "/workspace/_api_inventory.json"
    rows = [r for r in json.load(open(inv, encoding="utf-8")) if r["method"] in ("GET", "HEAD")]
    print(f"只读接口数: {len(rows)}")

    async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
        tokens = {}
        for n, p in ACCOUNTS.items():
            tokens[n] = None if p is None else (await login(client, n, p))[0]
        print("登录:", {k: ("OK" if v else "-") for k, v in tokens.items()})

        out = []
        for r in rows:
            path = subst(r.get("full_path") or r["path"])
            url = f"{BASE}{path}"
            row = {"method": r["method"], "path": r["path"], "full_path": r.get("full_path", ""),
                   "module": r["module"],
                   "fn": r["fn"], "declared_perm": "|".join(r["perm"]),
                   "declared_role": "|".join(r["role"]),
                   "ANON_OPEN": "", "INSP_MAYBE_OVERREACH": ""}
            for who, tk in tokens.items():
                st, note = await get(client, url, tk)
                row[who] = st
                if who == "anonymous" and st not in (401, 403, 404):
                    row["ANON_OPEN"] = f"{st} {note}".strip()
                if who == "inspector" and st == 200 and (r["perm"] or r["role"]):
                    row["INSP_MAYBE_OVERREACH"] = "|".join(r["perm"]) or "|".join(r["role"])
            out.append(row)
            print(f"{r['path'][:58]:58} anon={row['anonymous']:>4} insp={row['inspector']:>4} chief={row['chief']:>4} admin={row['admin']:>4}")

        fields = ["method", "full_path", "path", "module", "fn", "declared_perm", "declared_role",
                  "anonymous", "inspector", "supervisor", "deputy", "chief", "admin",
                  "ANON_OPEN", "INSP_MAYBE_OVERREACH"]
        with open("/workspace/_perm_matrix.csv", "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(out)

        # 汇总
        open_anon = [r for r in out if r.get("ANON_OPEN")]
        overreach = [r for r in out if r.get("INSP_MAYBE_OVERREACH")]
        print(f"\n=== 汇总 ===")
        print(f"接口总数(只读): {len(out)}")
        print(f"⚠ 未登录可访问(非401/403/404): {len(open_anon)}")
        for r in open_anon:
            print(f"   {r['method']} {r['path']} → {r['ANON_OPEN']}")
        print(f"⚠ 低权限拿到200但接口声明了权限: {len(overreach)}")
        for r in overreach:
            print(f"   {r['method']} {r['path']} 声明={r['INSP_MAYBE_OVERREACH']}")
        print("落盘 /workspace/_perm_matrix.csv")


if __name__ == "__main__":
    asyncio.run(main())
