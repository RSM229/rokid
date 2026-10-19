import httpx
key = "sk-sp-H.DXLMDD.sYc6.MEQCIBqHw9M0SGRx8YfdKmUrblymdCiaB32yQb8ilRU2Dd0jAiAtVA42P-XA26-8cjwHi96_diqAqHtbK9snjMZjOZ8F9Q"
url = "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions"
r = httpx.post(url, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
               json={"model": "qwen3.7-plus", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
               timeout=30)
print(r.status_code, r.text[:500])
