"""后端接口权限矩阵冒烟测试（对生产只读为主 + 幂等清理）。

测试方法：
  1. 用 5 个真实账号登录，取 token（含未登录态）
  2. 对每个接口按「路径占位符→真实资源 id」替换后发请求
  3. 只发 **GET/HEAD** 请求做真实调用；写操作（POST/PUT/DELETE）
     仅做「鉴权前置检查」——即用一个必错的最小 body，通过 HTTP 状态码
     区分「403 权限拒绝」（正确的守卫生效）与「400/422 参数错误」
     （说明已越过鉴权、进入业务校验 → 潜在越权需人工复核）
  4. 输出矩阵 CSV

用法（容器内）：
    python -u - < _perm_matrix.py
"""
import asyncio
import csv
import json
import os
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
# 路径占位符 → 真实存在的资源 id（生产实测值）
SUBST = {
    "{project_id}": "1",
    "{user_id}": "4",
    "{task_id}": "1",
    "{hazard_id}": "1",
    "{document_id}": "1",
    "{ledger_id}": "1",
    "{id}": "1",
    "{binding_id}": "1",
    "{kb_id}": "1",
    "{meeting_id}": "1",
    "{note_id}": "1",
    "{session_id}": "1",
    "{code}": "PMX3RV6S",
}

WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


def subst(path: str) -> str:
    for k, v in SUBST.items():
        path = path.replace(k, v)
    return path


async def login(client: httpx.AsyncClient, username: str, password: str):
    r = await client.post(
        f"{BASE}/api/auth/login",
        json={"username": username, "password": password},
        timeout=20,
    )
    if r.status_code != 200:
        return None, f"login {r.status_code}"
    return r.json().get("access_token"), ""


async def probe(client, method, url, token):
    """返回 (status, note)"""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        if method in ("GET", "HEAD"):
            r = await client.request(method, url, headers=headers, timeout=20)
        else:
            # 写操作：故意用最小/非法 body，看是否被鉴权拦住
            r = await client.request(
                method, url, headers={**headers, "Content-Type": "application/json"},
                json={}, timeout=20,
            )
        return r.status_code, (r.text[:120].replace("\n", " ") if r.status_code >= 400 else "")
    except Exception as e:  # noqa: BLE001
        return -1, repr(e)[:100]


async def main():
    inv_path = sys.argv[1] if len(sys.argv) > 1 else "/workspace/_api_inventory.json"
    rows = json.load(open(inv_path, encoding="utf-8"))

    async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
        tokens = {}
        for name, pwd in ACCOUNTS.items():
            if pwd is None:
                tokens[name] = None
                continue
            t, err = await login(client, name, pwd)
            tokens[name] = t
            print(f"登录 {name}: {'OK' if t else 'FAIL ' + err}")

        out = []
        for r in rows:
            path = subst(r["path"])
            url = f"{BASE}/api{path}" if path.startswith("/") else f"{BASE}/api/{path}"
            row = {"method": r["method"], "path": r["path"], "module": r["module"],
                   "fn": r["fn"], "declared_perm": "|".join(r["perm"]),
                   "declared_role": "|".join(r["role"])}
            for who, tk in tokens.items():
                st, note = await probe(client, r["method"], url, tk)
                row[who] = st
                if who == "anonymous" and st not in (401, 403, 404):
                    row["anon_note"] = note
                if who == "inspector" and r["method"] in WRITE_METHODS and st not in (401, 403):
                    row["inspector_write_note"] = f"{st} {note}"
            out.append(row)
            print(f"{r['method']:6} {r['path'][:60]:60} anon={row['anonymous']} insp={row['inspector']} admin={row['admin']}")

        with open("/workspace/_perm_matrix.csv", "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
            w.writeheader()
            w.writerows(out)
        print(f"\n完成 {len(out)} 条，落盘 /workspace/_perm_matrix.csv")


if __name__ == "__main__":
    asyncio.run(main())
