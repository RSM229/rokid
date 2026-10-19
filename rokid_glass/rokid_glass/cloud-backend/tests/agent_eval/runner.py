"""Agent 意图路由跑分器 —— 用数字回答「prompt 改完到底变好还是变坏」。

为什么这么设计
--------------
本机 venv **缺 pydantic**，无法 `import app.routers.agent`。所以本脚本走
**AST 提取**而非导入：直接从生产源码里把 `_AGENT_SYSTEM` 模板和 TOOLS 注册表
抠出来，复刻 `catalog_for_prompt()`。好处不只是绕开依赖 ——

  🔴 **prompt 与生产同源**。你改了 agent.py 里的硬约束、或给 ToolSpec 加了
     examples，跑分器自动跟上，不存在「测的是另一套 prompt」这种假分数。

零第三方依赖：只用标准库（ast / json / urllib / argparse / concurrent）。

用法
----
    # 1) 先看会发什么（不调 API，不花钱）
    python tests/agent_eval/runner.py --dry-run

    # 2) 真跑全量（需要 key，见下）
    python tests/agent_eval/runner.py

    # 3) 只跑某一组 / 某几条，快速验证
    python tests/agent_eval/runner.py --group 巡视
    python tests/agent_eval/runner.py --id A01 --id J03

    # 4) 换模型对比（判断「是不是模型不行」）
    python tests/agent_eval/runner.py --model qwen3.7-max --tag max

    # 5) A/B 对比改版 prompt（判断「我的改动有没有用」）
    python tests/agent_eval/runner.py --prompt-file tests/agent_eval/prompt_v2.txt --tag v2

API key 来源（按优先级）：
    1. 环境变量 AGENT_EVAL_API_KEY
    2. 环境变量 DEFAULT_LLM_API_KEY
    3. --key 参数
    4. 从 docker-compose.yml 里解析 DEFAULT_LLM_API_KEY
🔴 key 只用于请求头，**不会写进报告**。

退出码：0 = 全过；1 = 有失败；2 = 环境/参数错误。可直接接 CI。
"""
from __future__ import annotations

import argparse
import ast
import concurrent.futures as cf
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Windows 控制台默认 GBK，中文报告会炸
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:
    pass

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent.parent          # cloud-backend/
REPO = BACKEND.parent                 # rokid_glass/
AGENT_PY = BACKEND / "app" / "routers" / "agent.py"
TOOLS_PY = BACKEND / "app" / "services" / "agent_tools.py"
COMPOSE = REPO / "docker-compose.yml"
EXPORTS = Path("D:/rokid/exports")

sys.path.insert(0, str(HERE))
from cases import CASES, groups, scorable, gap_cases  # noqa: E402

DEFAULT_BASE_URL = "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.7-plus"


# ---------------------------------------------------------------------------
# 1. 从生产源码 AST 提取 prompt（保证同源）
# ---------------------------------------------------------------------------

def extract_system_template(path: Path = AGENT_PY) -> str:
    """抠出 _AGENT_SYSTEM 的字面量模板（含 {catalog} 占位符）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "_AGENT_SYSTEM":
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        return node.value.value
    raise RuntimeError(f"在 {path} 里找不到 _AGENT_SYSTEM 字符串常量")


def extract_tools(path: Path = TOOLS_PY) -> list[dict]:
    """复刻 catalog_for_prompt()：从 TOOLS 元组里抠 name/desc/params/examples。

    走 AST 而非 import，所以 agent_tools.py 里那堆 sqlalchemy 依赖不影响提取。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tools_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == "TOOLS":
            tools_node = node.value
            break
    if tools_node is None:
        raise RuntimeError(f"在 {path} 里找不到 TOOLS 定义")

    out: list[dict] = []
    for call in tools_node.elts:
        if not isinstance(call, ast.Call):
            continue
        # ToolSpec 是位置参数：name, desc, params, permission, risk, handler, examples
        args = call.args
        if len(args) < 3:
            continue
        try:
            name = ast.literal_eval(args[0])
            desc = ast.literal_eval(args[1])
            params = ast.literal_eval(args[2])
        except (ValueError, SyntaxError):
            continue
        examples: tuple = ()
        if len(args) >= 7:
            try:
                examples = ast.literal_eval(args[6]) or ()
            except (ValueError, SyntaxError):
                examples = ()
        out.append({"name": name, "desc": desc, "params": params, "examples": examples})
    if not out:
        raise RuntimeError("TOOLS 提取结果为空，检查 agent_tools.py 结构是否变了")
    return out


def build_catalog(tools: list[dict]) -> str:
    """逐行对齐 agent_tools.catalog_for_prompt() 的输出格式。"""
    blocks = []
    for t in tools:
        params = "、".join(f"{k}={v}" for k, v in t["params"].items()) or "（无参数）"
        block = f"- {t['name']}：{t['desc']}\n  参数：{params}"
        if t["examples"]:
            block += "\n  示例说法：" + " / ".join(t["examples"])
        blocks.append(block)
    return "\n".join(blocks)


def build_system_prompt(prompt_file: str | None = None,
                        catalog_file: str | None = None) -> tuple[str, str]:
    """返回 (最终 system prompt, 来源说明)。

    catalog_file：用外部文件替换从 TOOLS 提取的 catalog。
    🔴 用途：验证「拆工具」的收益，**不用先改生产代码**。拆 create_work_record
    是高风险大改（牵扯 undo 审计 / _TOOL_LABELS / agent_drafts.tool / pending_tool），
    先用一份手写的拆分版 catalog 跑分，确认能提分再决定要不要真拆。
    """
    tools = extract_tools()
    if catalog_file:
        catalog = Path(catalog_file).read_text(encoding="utf-8").rstrip("\n")
    else:
        catalog = build_catalog(tools)
    if prompt_file:
        # rstrip 尾换行：让 --prompt-file 路径与 AST 提取路径逐字等价，
        # 否则文件末尾的换行会造成 1 字符「假差异」，浪费时间去查。
        tmpl = Path(prompt_file).read_text(encoding="utf-8").rstrip("\n")
        origin = f"自定义 {prompt_file}"
    else:
        tmpl = extract_system_template()
        origin = f"生产 {AGENT_PY.relative_to(REPO)}"
    if catalog_file:
        origin += f" + catalog={Path(catalog_file).name}"
    return tmpl.format(catalog=catalog), origin


# ---------------------------------------------------------------------------
# 2. 复刻生产端的解析与消息组装
# ---------------------------------------------------------------------------

def parse_json_object(raw: str) -> dict | None:
    """逐行对齐 agent_tools._parse_json_object（含 ```json 包裹与前后废话兼容）。"""
    if not raw:
        return None
    txt = raw.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-zA-Z]*\s*", "", txt)
        txt = re.sub(r"\s*```$", "", txt)
    try:
        data = json.loads(txt)
        return data if isinstance(data, dict) else None
    except (TypeError, ValueError):
        pass
    start, end = txt.find("{"), txt.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(txt[start:end + 1])
            return data if isinstance(data, dict) else None
        except (TypeError, ValueError):
            return None
    return None


def history_messages(history: list[dict], limit: int = 6) -> list[dict]:
    """逐行对齐 agent._history_messages。"""
    out: list[dict] = []
    for item in (history or [])[-limit:]:
        role = str(item.get("role") or "").strip()
        content = str(item.get("text") or item.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        out.append({"role": role, "content": content[:500]})
    return out


def build_messages(system: str, case: dict) -> list[dict]:
    msgs: list[dict] = [{"role": "system", "content": system}]
    msgs.extend(history_messages(case.get("history") or []))
    msgs.append({"role": "user", "content": case["say"]})
    return msgs


# ---------------------------------------------------------------------------
# 3. 调模型（标准库 urllib，temperature=0.2 对齐生产 agent_chat）
# ---------------------------------------------------------------------------

def call_llm(messages: list[dict], *, base_url: str, model: str, api_key: str,
             temperature: float = 0.2, max_tokens: int = 2048,
             timeout: float = 60.0, native_tools: list[dict] | None = None) -> dict:
    """返回 {"content": str, "tool_calls": list, "error": str|None, "latency": float}"""
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if native_tools is None:
        # 生产口径：JSON 模式 + 手搓 {tool, args, answer}
        payload["response_format"] = {"type": "json_object"}
    else:
        # 对照组：原生 function calling
        payload["tools"] = native_tools
        payload["tool_choice"] = "auto"

    url = base_url.rstrip("/") + "/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        return {"content": "", "tool_calls": [], "latency": time.time() - t0,
                "error": f"HTTP {exc.code}: {detail}"}
    except Exception as exc:  # noqa: BLE001
        return {"content": "", "tool_calls": [], "latency": time.time() - t0,
                "error": f"{type(exc).__name__}: {exc}"}

    choices = body.get("choices") or []
    if not choices:
        return {"content": "", "tool_calls": [], "latency": time.time() - t0,
                "error": f"无 choices: {json.dumps(body, ensure_ascii=False)[:200]}"}
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    return {
        "content": content if isinstance(content, str) else "",
        "tool_calls": msg.get("tool_calls") or [],
        "latency": time.time() - t0,
        "error": None,
        "usage": body.get("usage") or {},
    }


# ---------------------------------------------------------------------------
# 4. 比对断言
# ---------------------------------------------------------------------------

def _norm(v) -> str:
    return str(v if v is not None else "").strip()


def _match(actual, candidates: list) -> bool:
    """实际值与任一候选互为子串即算对（容忍「巡视」vs「巡视记录」这类措辞差）。"""
    a = _norm(actual)
    if not a:
        return False
    return any(a in _norm(c) or _norm(c) in a for c in candidates)


def judge(case: dict, parsed: dict | None, err: str | None) -> tuple[bool, list[str]]:
    """返回 (是否通过, 失败原因列表)。"""
    fails: list[str] = []
    exp = case["expect"]

    if err:
        return False, [f"请求失败：{err}"]
    if parsed is None:
        return False, ["JSON 解析失败（生产会在这里退化成纯问答 _plain_answer）"]
    actual_tool = parsed.get("tool")
    if actual_tool in ("", "none", "None", "null"):
        actual_tool = None

    # --- tool 断言 ---
    if "tool" in exp:
        want = exp["tool"]
        if want is None:
            if actual_tool is not None:
                fails.append(f"期望不调工具，实际调了 {actual_tool}")
        elif actual_tool != want:
            fails.append(f"工具错：期望 {want}，实际 {actual_tool or 'null'}")

    if "tool_forbidden" in exp and actual_tool in exp["tool_forbidden"]:
        fails.append(f"选中了禁止的工具 {actual_tool}")

    # --- tool_any_of：多个工具都可接受（用于本身有歧义的说法）---
    if "tool_any_of" in exp and actual_tool not in exp["tool_any_of"]:
        want = "/".join(str(x) for x in exp["tool_any_of"])
        fails.append(f"工具不在可接受集合内：期望 {want}，实际 {actual_tool or 'null'}")

    # --- expect_ask：期望不调工具、只在 answer 里追问 ---
    if exp.get("expect_ask"):
        if actual_tool is not None:
            fails.append(f"该追问却直接调了工具 {actual_tool}（硬约束要求先问类型）")
        else:
            answer = _norm(parsed.get("answer"))
            if not answer:
                fails.append("期望追问但 answer 为空")
            elif not re.search(r"[？?]|请|哪|什么|多少|几点|说|补充", answer):
                fails.append(f"answer 不像追问：{answer[:60]}")

    args = parsed.get("args")
    if not isinstance(args, dict):
        args = {}

    # --- args_required：每个 key 都必须命中 ---
    for key, cands in (exp.get("args_required") or {}).items():
        if key not in args or not _norm(args.get(key)):
            fails.append(f"缺参数 {key}（期望含 {'/'.join(map(str, cands))}）")
        elif not _match(args.get(key), cands):
            fails.append(f"参数 {key} 值不符：期望含 {'/'.join(map(str, cands))}，"
                         f"实际 {_norm(args.get(key))[:50]}")

    # --- args_any_of：任一组全部命中即通过 ---
    any_groups = exp.get("args_any_of")
    if any_groups and not any(
        all(k in args and _match(args.get(k), cs) for k, cs in grp.items())
        for grp in any_groups
    ):
        desc = " 或 ".join("/".join(g.keys()) for g in any_groups)
        fails.append(f"参数组合未命中任一种：{desc}")

    # --- args_forbidden：不该出现 ---
    for key in (exp.get("args_forbidden") or []):
        if _norm(args.get(key)):
            fails.append(f"出现了不该有的参数 {key}={_norm(args.get(key))[:40]}")

    # --- 逐 case 特判 ---
    if case["id"] == "A04":
        # 项目名不能串进 section
        sec = _norm(args.get("section"))
        if sec and "滨江花园" in sec:
            fails.append(f"项目名串进了 section：{sec[:40]}（硬约束第4条）")
    if case["id"] == "A05":
        # 中文时间：原样传 inspection_time，或已换算成 start/end，二者其一
        if not (_norm(args.get("inspection_time"))
                or _norm(args.get("inspection_start_time"))):
            fails.append("中文时间既没原样传 inspection_time，也没换算成 start_time")
    if case["id"] in ("A03", "J04"):
        # problems=符合要求 不能被当空值丢掉
        if not _norm(args.get("problems")):
            fails.append("problems 为空 —— 「符合要求」被当成没问题丢掉了")

    # --- 输出契约：answer 必须是纯文本 ---
    answer = _norm(parsed.get("answer"))
    if answer and re.search(r"(^|\n)\s*[#*]|```|\|.*\|", answer):
        fails.append("answer 含 Markdown 标记（硬约束要求纯文本）")

    return (not fails), fails


# ---------------------------------------------------------------------------
# 5. 原生 function calling 的 schema（对照组用）
# ---------------------------------------------------------------------------

def native_tool_schemas(tools: list[dict]) -> list[dict]:
    """把 TOOLS 转成 OpenAI tools schema —— 用于验证「换原生调用是否更好」。"""
    schemas = []
    for t in tools:
        props = {}
        required = []
        for pname, pdesc in t["params"].items():
            key = pname.lstrip("*")
            props[key] = {"type": "string", "description": pdesc}
            if pname.startswith("*"):
                required.append(key)
        schemas.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["desc"],
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        })
    return schemas


def normalize_native(result: dict) -> dict | None:
    """把原生 tool_calls 结果转成和手搓 JSON 一致的结构，方便同一套 judge 复用。"""
    calls = result.get("tool_calls") or []
    if not calls:
        content = result.get("content") or ""
        parsed = parse_json_object(content)
        if parsed:
            return parsed
        return {"tool": None, "args": {}, "answer": content}
    fn = calls[0].get("function") or {}
    try:
        args = json.loads(fn.get("arguments") or "{}")
    except (TypeError, ValueError):
        args = {}
    return {"tool": fn.get("name"), "args": args,
            "answer": result.get("content") or ""}


# ---------------------------------------------------------------------------
# 6. 跑分主流程
# ---------------------------------------------------------------------------

# 🔴 拆分版工具名 → (基准工具名, record_type)。
# 用途：验证「拆 create_work_record」的收益时，测试集期望仍是基准名
# create_work_record + args.record_type，归一后**无需改动 cases.py 就能公平 A/B**。
# 不归一的话拆分会因为工具名不匹配而全军覆没，得出「拆分没用」的错误结论。
_SPLIT_TOOL_MAP = {
    "create_xunshi_record": ("create_work_record", "巡视"),
    "create_pangzhan_record": ("create_work_record", "旁站"),
    "create_yanshou_record": ("create_work_record", "验收"),
    "create_jianzheng_record": ("create_work_record", "见证"),
    "create_weida_record": ("create_work_record", "危大工程巡视"),
    "create_pending_record": ("create_work_record", "待定"),
}


def normalize_split_tools(parsed: dict | None) -> dict | None:
    """把拆分版工具名折回基准形态，使 judge 可以用同一套期望断言。

    规则：tool 换成基准名；record_type 由工具名隐含确定（模型没传时补上，
    模型传了则尊重模型的值——它可能传得更准）。
    """
    if not isinstance(parsed, dict):
        return parsed
    tool = parsed.get("tool")
    hit = _SPLIT_TOOL_MAP.get(tool)
    if not hit:
        return parsed
    base, rtype = hit
    out = dict(parsed)
    out["tool"] = base
    args = out.get("args")
    args = dict(args) if isinstance(args, dict) else {}
    if not _norm(args.get("record_type")):
        args["record_type"] = rtype
    out["args"] = args
    return out


def resolve_key(cli_key: str | None) -> str | None:
    for env in ("AGENT_EVAL_API_KEY", "DEFAULT_LLM_API_KEY"):
        v = os.environ.get(env)
        if v:
            return v.strip()
    if cli_key:
        return cli_key.strip()
    if COMPOSE.exists():
        m = re.search(r'DEFAULT_LLM_API_KEY:\s*"([^"]+)"',
                      COMPOSE.read_text(encoding="utf-8"))
        if m:
            return m.group(1).strip()
    return None


# 基础设施故障（不是模型的错）—— 这类必须重试或单列，绝不能算进模型的失败率，
# 否则你会拿着「分数掉了」去改一个根本没问题的 prompt。
_INFRA_PAT = re.compile(
    r"TimeoutError|timed out|HTTP 429|HTTP 5\d\d|URLError|RemoteDisconnected|"
    r"ConnectionReset|BadGateway|ServiceUnavailable|GatewayTimeout|IncompleteRead",
    re.I)


def is_infra_error(err: str | None) -> bool:
    """区分「模型答得不好」和「网络/服务端炸了」。"""
    return bool(err and _INFRA_PAT.search(err))


def run(cases: list[dict], *, system: str, base_url: str, model: str, api_key: str,
        concurrency: int, native: bool, schemas: list[dict] | None,
        repeat: int, retries: int = 2, split_tools: bool = False) -> list[dict]:
    tools = [(c, i) for c in cases for i in range(repeat)]

    def one(item):
        case, idx = item
        msgs = build_messages(system, case)
        res: dict = {}
        # 基础设施故障自动重试；模型答错不重试（答错就是要如实记录的结果）
        for attempt in range(retries + 1):
            res = call_llm(msgs, base_url=base_url, model=model, api_key=api_key,
                           native_tools=schemas if native else None)
            if not is_infra_error(res.get("error")):
                break
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
        parsed = normalize_native(res) if native else parse_json_object(res.get("content") or "")
        if split_tools:
            parsed = normalize_split_tools(parsed)
        ok, fails = judge(case, parsed, res.get("error"))
        return {
            "case": case, "run": idx, "ok": ok, "fails": fails,
            "parsed": parsed, "error": res.get("error"),
            "infra": is_infra_error(res.get("error")),
            "latency": res.get("latency", 0.0), "usage": res.get("usage") or {},
        }

    with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(one, tools))
    return results


def render_report(results: list[dict], *, meta: dict) -> str:
    lines: list[str] = []
    ts = meta["timestamp"]
    lines.append(f"# Agent 意图路由跑分报告 · {ts}")
    lines.append("")
    lines.append(f"- **prompt 来源**：{meta['prompt_origin']}")
    lines.append(f"- **模型**：`{meta['model']}` @ {meta['base_url']}")
    lines.append(f"- **调用方式**：{'原生 function calling（对照组）' if meta['native'] else '生产口径：JSON 模式 + 手搓 {tool,args,answer}'}")
    lines.append(f"- **system prompt 长度**：{meta['prompt_chars']} 字符")
    lines.append(f"- **并发**：{meta['concurrency']}｜**每条重复**：{meta['repeat']} 次")
    if meta.get("split_tools"):
        lines.append("- 🔀 **拆分工具口径**：catalog 是拆分版（create_xunshi_record 等），"
                     "工具名已归一回 create_work_record + record_type 后再判分，"
                     "因此与基准版分数**可直接对比**")
    lines.append("")

    scorable_res = [r for r in results if not r["case"].get("gap")]
    gap_res = [r for r in results if r["case"].get("gap")]

    # --- 总分（重复多次时按「该 case 全部通过」算过，即严格模式）---
    by_id: dict[str, list[dict]] = {}
    for r in scorable_res:
        by_id.setdefault(r["case"]["id"], []).append(r)

    # 🔴 基础设施故障（超时/5xx/429）不算模型失败 —— 单独挑出来，
    #    否则网络抖动会让你误以为「prompt 改差了」。
    infra_ids = sorted({r["case"]["id"] for r in scorable_res if r.get("infra")})
    model_ids = [i for i in by_id if i not in set(infra_ids)]
    pass_ids = [i for i in model_ids if all(x["ok"] for x in by_id[i])]
    total_ids = len(model_ids)
    rate = (len(pass_ids) / total_ids * 100) if total_ids else 0.0

    lines.append("## 总分")
    lines.append("")
    lines.append(f"**{len(pass_ids)}/{total_ids} = {rate:.1f}%**"
                 f"（严格模式：每条重复 {meta['repeat']} 次需全过才算过）")
    if infra_ids:
        lines.append("")
        lines.append(f"- ⚠️ **{len(infra_ids)} 条因网络/服务端故障未计分**（已重试仍失败）："
                     f"{', '.join(infra_ids)}")
        lines.append(f"  这些不是模型的问题，重跑即可：`python {Path(__file__).name} "
                     f"{' '.join('--id ' + i for i in infra_ids)}`")
    lines.append("")

    # --- 稳定性（重复跑才有意义）---
    if meta["repeat"] > 1:
        flaky = [(i, sum(1 for x in rs if x["ok"]), len(rs))
                 for i, rs in by_id.items()
                 if i not in set(infra_ids) and 0 < sum(1 for x in rs if x["ok"]) < len(rs)]
        lines.append(f"### 不稳定用例（时对时错）：{len(flaky)} 条")
        lines.append("")
        if not flaky:
            lines.append("无 —— 说明这版 prompt 行为稳定。")
        for i, p, n in sorted(flaky):
            lines.append(f"- `{i}`：{p}/{n} 次通过 ← 靠单次测试发现不了，"
                         f"通常是 prompt 有歧义或该说法处于模型决策边界")
        lines.append("")

    # --- 按组 ---
    lines.append("## 按组通过率")
    lines.append("")
    lines.append("| 组 | 通过/总数 | 通过率 |")
    lines.append("|---|---|---|")
    for g in groups():
        rs = [r for r in scorable_res if r["case"]["group"] == g]
        if not rs:
            continue
        gid: dict[str, list[dict]] = {}
        for r in rs:
            gid.setdefault(r["case"]["id"], []).append(r)
        gid = {i: v for i, v in gid.items() if i not in set(infra_ids)}
        if not gid:
            continue
        p = sum(1 for v in gid.values() if all(x["ok"] for x in v))
        n = len(gid)
        flag = " 🔴" if p / n < 0.8 else ""
        lines.append(f"| {g} | {p}/{n} | {p / n * 100:.0f}%{flag} |")
    lines.append("")

    # --- 失败明细（剔除 infra 故障，那些在总分区块已单列）---
    failed = {}
    for r in scorable_res:
        if not r["ok"] and not r.get("infra"):
            failed.setdefault(r["case"]["id"], []).append(r)
    lines.append("## 失败明细")
    lines.append("")
    if not failed:
        lines.append("全过（infra 故障已在总分区块单列，不算模型失败）。")
    else:
        for cid in sorted(failed):
            rs = failed[cid]
            case = rs[0]["case"]
            lines.append(f"### `{cid}` · {case['group']}")
            lines.append("")
            lines.append(f"- **说法**：{case['say']}")
            if case.get("history"):
                for h in case["history"]:
                    lines.append(f"- **前文[{h['role']}]**：{h['text']}")
            lines.append(f"- **测的是**：{case['note']}")
            # 同一 case 多次重复若失败原因相同，合并成一条 + 次数，避免刷屏
            seen: dict[str, int] = {}
            for r in rs:
                got = json.dumps(r["parsed"], ensure_ascii=False)
                if len(got) > 400:
                    got = got[:400] + "…"
                key = got + "|" + "；".join(r["fails"])
                seen[key] = seen.get(key, 0) + 1
            for key, cnt in seen.items():
                got, _, failstr = key.partition("|")
                times = f"（{cnt} 次重复均如此）" if cnt > 1 else ""
                lines.append(f"- **实际输出**{times}：`{got}`")
                for f in filter(None, failstr.split("；")):
                    lines.append(f"  - ❌ {f}")
            lines.append("")

    # --- gap 产品缺口 ---
    if gap_res:
        lines.append("## 已知产品缺口（gap，不计入总分）")
        lines.append("")
        lines.append("> 🔴 **读法**：跑分器只测「模型选工具 + 抽参数」这一层。下面这些用例")
        lines.append("> **即使显示通过，功能在端到端仍然不可用** —— 失败点在服务端")
        lines.append("> （聚合分支缺失 / validate_values 卡住 / 参数无处可传），")
        lines.append("> 本跑分器测不到执行层。别把「通过」误读成「已修好」。")
        lines.append("")
        # 按 case 去重（repeat>1 时同一条会出现多次）
        uniq: dict[str, dict] = {}
        for r in gap_res:
            uniq.setdefault(r["case"]["id"], r)
        for cid in sorted(uniq):
            r = uniq[cid]
            case = r["case"]
            mark = ("路由层通过（但服务端缺口仍在，功能不可用）"
                    if r["ok"] else "路由层也失败")
            lines.append(f"- `{case['id']}` {case['say']}")
            lines.append(f"  - {mark}")
            lines.append(f"  - {case['note']}")
        lines.append("")

    # --- 耗时与 token ---
    lat = [r["latency"] for r in results if r["latency"]]
    if lat:
        infra_n = sum(1 for r in results if r.get("infra"))
        err_n = sum(1 for r in results if r["error"])
        lines.append("## 性能")
        lines.append("")
        lines.append(f"- 调用次数：{len(results)}｜出错请求：{err_n}"
                     f"（其中基础设施故障 {infra_n}，已自动重试）")
        lines.append(f"- 延迟：均 {sum(lat) / len(lat):.2f}s / 最大 {max(lat):.2f}s")
        pt = sum((r["usage"].get("prompt_tokens") or 0) for r in results)
        ct = sum((r["usage"].get("completion_tokens") or 0) for r in results)
        if pt:
            lines.append(f"- token：prompt 合计 {pt}（均 {pt // len(results)}）/ completion {ct}")
            lines.append(f"- 💡 均 prompt token 就是每次调用的**固定成本**，"
                         f"拆工具/精简 catalog 能直接把它压下来")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Agent 意图路由跑分器")
    ap.add_argument("--id", action="append", default=[], help="只跑指定 id（可多次）")
    ap.add_argument("--group", default=None, help="只跑某一组，如 巡视")
    ap.add_argument("--model", default=os.environ.get("AGENT_EVAL_MODEL", DEFAULT_MODEL))
    ap.add_argument("--base-url", default=os.environ.get("AGENT_EVAL_BASE_URL", DEFAULT_BASE_URL))
    ap.add_argument("--key", default=None, help="API key（优先用环境变量）")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--repeat", type=int, default=1, help="每条重复几次，>1 可测稳定性")
    ap.add_argument("--retries", type=int, default=2,
                    help="基础设施故障（超时/5xx/429）自动重试次数，默认 2")
    ap.add_argument("--prompt-file", default=None, help="用自定义 system prompt 模板做 A/B")
    ap.add_argument("--catalog-file", default=None,
                    help="用外部 catalog 替换从 TOOLS 提取的版本（验证「拆工具」收益，"
                         "不用先改生产代码）。配合 --split-tools 使用。")
    ap.add_argument("--split-tools", action="store_true",
                    help="启用拆分工具名归一：把 create_xunshi_record 等映射回 "
                         "create_work_record + record_type，使测试集无需改动即可公平对比")
    ap.add_argument("--native", action="store_true",
                    help="对照组：改用原生 function calling（验证该不该做这个改造）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要发的 prompt，不调 API")
    ap.add_argument("--tag", default=None, help="报告文件名后缀，便于对比多个版本")
    ap.add_argument("--out", default=None, help="报告输出路径（默认 exports/）")
    args = ap.parse_args()

    # 选 case
    sel = CASES
    if args.id:
        sel = tuple(c for c in CASES if c["id"] in set(args.id))
    elif args.group:
        sel = tuple(c for c in CASES if c["group"] == args.group)
    if not sel:
        print("没有匹配的用例", file=sys.stderr)
        return 2

    try:
        system, origin = build_system_prompt(args.prompt_file, args.catalog_file)
    except Exception as exc:  # noqa: BLE001
        print(f"提取生产 prompt 失败：{exc}", file=sys.stderr)
        return 2

    tools = extract_tools()
    print(f"prompt 来源：{origin}")
    print(f"提取到 {len(tools)} 个工具，system prompt {len(system)} 字符")
    print(f"本次跑 {len(sel)} 条用例 ×{args.repeat} = {len(sel) * args.repeat} 次调用")

    if args.dry_run:
        print("\n" + "=" * 70)
        print("SYSTEM PROMPT（将要原样发给模型）")
        print("=" * 70)
        print(system)
        print("=" * 70)
        sample = sel[0]
        msgs = build_messages(system, sample)
        print(f"\n样例用例 {sample['id']} 的消息结构（共 {len(msgs)} 条）：")
        for m in msgs[1:]:
            print(f"  [{m['role']}] {m['content'][:120]}")
        print(f"\n工具清单：{', '.join(t['name'] for t in tools)}")
        return 0

    key = resolve_key(args.key)
    if not key:
        print("找不到 API key。设置环境变量 AGENT_EVAL_API_KEY，或用 --key 传入。",
              file=sys.stderr)
        return 2

    schemas = native_tool_schemas(tools) if args.native else None
    t0 = time.time()
    results = run(list(sel), system=system, base_url=args.base_url, model=args.model,
                  api_key=key, concurrency=args.concurrency, native=args.native,
                  schemas=schemas, repeat=args.repeat, retries=args.retries,
                  split_tools=args.split_tools)
    elapsed = time.time() - t0

    meta = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "prompt_origin": origin,
        "model": args.model,
        "base_url": args.base_url,
        "native": args.native,
        "prompt_chars": len(system),
        "concurrency": args.concurrency,
        "repeat": args.repeat,
        "split_tools": args.split_tools,
    }
    report = render_report(results, meta=meta)

    # 落盘
    if args.out:
        outp = Path(args.out)
    else:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        suffix = f"_{args.tag}" if args.tag else ""
        EXPORTS.mkdir(parents=True, exist_ok=True)
        outp = EXPORTS / f"agent跑分{suffix}_{stamp}.md"
    outp.write_text(report, encoding="utf-8")

    # 终端打摘要（口径与报告一致：剔除 infra 故障，取首个真失败，gap 去重）
    sc = [r for r in results if not r["case"].get("gap")]
    infra_ids = {r["case"]["id"] for r in sc if r.get("infra")}
    gid: dict[str, list] = {}
    for r in sc:
        gid.setdefault(r["case"]["id"], []).append(r)
    model_ids = {i: v for i, v in gid.items() if i not in infra_ids}
    p = sum(1 for v in model_ids.values() if all(x["ok"] for x in v))
    print(f"\n{'=' * 70}")
    print(f"总分：{p}/{len(model_ids)} = "
          f"{p / len(model_ids) * 100 if model_ids else 0:.1f}%   耗时 {elapsed:.1f}s")
    if infra_ids:
        print(f"⚠️  {len(infra_ids)} 条因网络故障未计分（不算模型失败）："
              f"{', '.join(sorted(infra_ids))}")
    print(f"报告：{outp}")
    print("=" * 70)
    for cid, rs in sorted(model_ids.items()):
        bad = [x for x in rs if not x["ok"]]
        if bad:
            print(f"  ❌ {cid} [{bad[0]['case']['group']}] {bad[0]['fails'][0]}")
    for cid in sorted({r["case"]["id"] for r in results if r["case"].get("gap")}):
        r = next(x for x in results if x["case"]["id"] == cid)
        print(f"  ⚠️  {cid} (gap) 路由层{'通过' if r['ok'] else '失败'}"
              f" —— 服务端缺口另计")

    return 0 if p == len(gid) else 1


if __name__ == "__main__":
    sys.exit(main())
