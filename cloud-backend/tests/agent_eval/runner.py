"""Agent 意图路由跑分器 —— 用数字回答「prompt 改完到底变好还是变坏」。

为什么这么设计
--------------
`_AGENT_SYSTEM` 模板和 TOOLS 注册表走 **AST 提取**（不 import `app.routers.agent`，
那是 FastAPI router，依赖链重）。好处不只是绕开依赖 ——

  🔴 **prompt 与生产同源**。你改了 agent.py 里的硬约束、或给 ToolSpec 加了
     examples，跑分器自动跟上，不存在「测的是另一套 prompt」这种假分数。

🔴 但**解析与评分层必须直接 import 生产函数**（agent_request_plan /
agent_fact_model / agent_context / agent_tools），不能手抄。
2026-10-27 教训：10-16 契约改版后模型输出从 `{tool,args,answer}` 变成
`{intent,actions[].id,record_type,facts}`，而 judge 仍读 `parsed["tool"]`
→ 恒 null → 45 条里 37 条被误杀，报出 17.8% 的假分数（模型其实全答对了）。
手抄一份映射只会再漂移一次，所以这里直接调生产的 `normalize_candidate` +
`enrich_plan_facts` + `facts_to_args`，与 agent.py:838-919 同一条链路。

若生产模块 import 失败（venv 缺依赖），降级为 AST 提取的 ACTIONS 词典做
id→tool 映射（不复刻 clarifications 逻辑），并在报告里标注降级。

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
PLAN_PY = BACKEND / "app" / "services" / "agent_request_plan.py"
COMPOSE = REPO / "docker-compose.yml"
EXPORTS = Path("D:/rokid/exports")

sys.path.insert(0, str(HERE))
from cases import CASES, groups, scorable, gap_cases  # noqa: E402

# 🔴 生产解析链：评分必须与 agent.py 走同一条路（见文件头「2026-10-27 教训」）。
# BACKEND 入 sys.path，使 `import app.*` 在任何 cwd 下都能成立。
sys.path.insert(0, str(BACKEND))
try:
    from app.services import agent_fact_model as _fact_model
    from app.services import agent_request_plan as _plan
    from app.services import agent_tools as _tools_mod
    from app.services.agent_context import extract_explicit_message_facts as _explicit_facts
    PROD_CHAIN_OK = True
    PROD_CHAIN_ERR = ""
except Exception as exc:  # noqa: BLE001
    PROD_CHAIN_OK = False
    PROD_CHAIN_ERR = f"{type(exc).__name__}: {exc}"

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
    """取工具目录（name/desc/params/examples）。

    🔴 生产模块可 import 时**直接用生产 TOOLS 对象**，不走 AST。
    原因（2026-10-27 实测踩坑）：AST 路径用 ``ast.literal_eval`` 取 desc，
    而 f-string 在 AST 里是 ``JoinedStr`` 节点 → literal_eval 抛 ValueError →
    被 ``except: continue`` **静默跳过整个工具**。当时 create_provform_doc 的
    desc 改成 f-string（为让表式清单跟随 PROVFORM_CHOICES 动态生成、不再硬编码），
    提取数就从 14 变 13，而 build_catalog 走的是生产 catalog_for_prompt()，
    于是「prompt 里有 14 个工具、extract_tools 只报 13 个」这种不一致毫无提示。
    与 build_catalog 同源原则一致：能 import 生产就用生产。
    """
    if PROD_CHAIN_OK:
        return [
            {"name": t.name, "desc": t.desc, "params": dict(t.params),
             "examples": tuple(t.examples or ())}
            for t in _tools_mod.TOOLS
        ]

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
    skipped: list[str] = []
    for call in tools_node.elts:
        if not isinstance(call, ast.Call):
            continue
        # ToolSpec 是位置参数：name, desc, params, permission, risk, handler, examples
        args = call.args
        if len(args) < 3:
            continue
        # 🔴 降级路径下不再静默跳过：literal_eval 失败说明 desc/params 用了
        # f-string 等非常量写法，必须显式报出来，否则评测集会悄悄少一个工具。
        try:
            name = ast.literal_eval(args[0])
            desc = ast.literal_eval(args[1])
            params = ast.literal_eval(args[2])
        except (ValueError, SyntaxError) as exc:
            raw_name = "<unknown>"
            try:
                raw_name = ast.literal_eval(args[0])
            except Exception:  # noqa: BLE001
                pass
            skipped.append(f"{raw_name}（{type(exc).__name__}，desc/params 可能是 f-string）")
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
    if skipped:
        raise RuntimeError(
            "AST 降级提取漏掉工具（生产模块 import 失败 + desc/params 不是常量）："
            + "；".join(skipped)
            + f"；已提取 {len(out)} 个。要么修好生产 import，要么把这些字段改回常量。"
        )
    return out


def _module_level_dict(tree: ast.AST, name: str) -> dict:
    """抠出模块级 `name: ... = {...}` 或 `name = {...}` 的字面量 dict。"""
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        elif isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if name in targets:
            try:
                return ast.literal_eval(node.value)
            except (ValueError, SyntaxError):
                break
    raise RuntimeError(f"在 agent_request_plan.py 里找不到 {name} 字典字面量")


# 降级用：生产模块 import 不了时，用 AST 抠的 ACTIONS 词典做 id→tool 映射。
_AST_ACTIONS: dict = {}


def extract_plan_catalog(path: Path = PLAN_PY) -> tuple[str, str]:
    """取 agent.py:770-771 的 format 参数（actions / record_types）。

    🔴 优先直接调生产 `action_catalog()` + `RECORD_TYPES`（同源，不会漂移）；
    生产 import 不了才降级为 AST 抠字面量，并把结果缓存进 `_AST_ACTIONS`
    供 resolve_via_ast_fallback 用。"""
    if PROD_CHAIN_OK:
        _AST_ACTIONS.update(_module_level_dict(ast.parse(path.read_text(encoding="utf-8")), "ACTIONS"))
        return (json.dumps(_plan.action_catalog(), ensure_ascii=False),
                json.dumps(_plan.RECORD_TYPES, ensure_ascii=False))
    tree = ast.parse(path.read_text(encoding="utf-8"))
    actions = _module_level_dict(tree, "ACTIONS")
    record_types = _module_level_dict(tree, "RECORD_TYPES")
    _AST_ACTIONS.update(actions)
    catalog = [{"id": action_id, **spec} for action_id, spec in actions.items()]
    return (json.dumps(catalog, ensure_ascii=False),
            json.dumps(record_types, ensure_ascii=False))


def build_catalog(tools: list[dict]) -> str:
    """工具参数说明段。生产 `catalog_for_prompt()` 可用时直接用（同源），
    否则退回 AST 提取结果逐行拼装。"""
    if PROD_CHAIN_OK:
        return _tools_mod.catalog_for_prompt()
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
    # 🔴 2026-10-27 修：_AGENT_SYSTEM 自 2026-10-16 改版起有三个占位符
    # （actions / record_types / catalog），此前只喂 catalog → KeyError: 'actions'。
    # str.format 忽略多余关键字，因此只含 {catalog} 的旧版自定义 prompt 仍可用。
    actions, record_types = extract_plan_catalog()
    return tmpl.format(catalog=catalog, actions=actions, record_types=record_types), origin


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
    # 对齐生产 agent_chat：随消息附带的素材（图片识别结果/水印/文件文本）拼在用户话后面
    say = case["say"]
    material = str(case.get("material") or "").strip()
    if material:
        say = (f"{say}\n\n以下是随消息发送的素材（系统已识别，不是你要识别的图片本体）：\n"
               f"{material}")
    msgs.append({"role": "user", "content": say})
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

    # --- answer_forbidden：回答里不该出现的措辞（防编造 / 防能力误报）---
    # 例：没有图片素材却写「经图片识别」；明明能读水印却说「无法接收图片」。
    for bad in exp.get("answer_forbidden") or []:
        if bad and bad in _norm(parsed.get("answer")):
            fails.append(f"answer 出现禁用措辞「{bad}」")

    # --- answer_required：回答里必须出现的措辞（每项给候选，命中任一即可）---
    # 用途：测「行为正确但工具为空」的场景里必须带上的措辞。
    # 🔴 2026-11-16 起水印识别内容按用户要求只留在内部思考、不再复述，
    # 原「必须写出水印具体值」的期望已改为 answer_forbidden（见 K05）。
    for item in exp.get("answer_required") or []:
        cands = item if isinstance(item, (list, tuple, set)) else [item]
        cands = [str(c) for c in cands if str(c).strip()]
        if cands and not any(c in _norm(parsed.get("answer")) for c in cands):
            fails.append(f"answer 缺少必答内容「{'/'.join(cands)}」：{_norm(parsed.get('answer'))[:60]}")

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


# 🔴 新契约 → judge 期望的旧形态。生产 agent.py:838-919 的同一链路：
#   normalize_candidate → enrich_plan_facts → facts_to_args + args 合并 → selected_tool
def resolve_via_production(parsed: dict | None, message: str,
                           material: str = "") -> dict | None:
    """用生产函数把模型 JSON 解析成 {tool, args, answer}。

    与 agent_chat 保持一致的三件事：
      1. record_type 只放在 args 里时也认（agent.py:846-847）；
      2. selected_tool 由 actions 经目录解析，受 clarifications 影响
         （agent_request_plan.py:373-376）—— 所以「该追问却建单」能被测出来；
      3. args = facts_to_args(facts) 打底，模型显式 args 覆盖（agent.py:916-917），
         并补 record_type（agent.py:918-919）。
    """
    if not isinstance(parsed, dict):
        return parsed
    has_material = bool(material)
    candidate = dict(parsed)
    semantic_args = candidate.get("args") if isinstance(candidate.get("args"), dict) else {}
    if not candidate.get("record_type") and semantic_args.get("record_type"):
        candidate["record_type"] = semantic_args.get("record_type")
    # 🔴 与生产 agent.py 调同一个函数（素材明示类型兜底），不手抄逻辑。
    candidate = _plan.apply_material_record_type(candidate, material)
    plan = _plan.normalize_candidate(
        candidate,
        message=message,
        attachment_count=1 if has_material else 0,
        evidence_count=1 if has_material else 0,
        evidence=[],
        system_facts=[],
    )
    plan = _plan.enrich_plan_facts(
        plan, _explicit_facts(message, message_ref=message),
        evidence=[], system_facts=[],
    )
    tool = str(plan.get("selected_tool") or "").strip()
    if tool not in _tools_mod.TOOL_BY_NAME:
        tool = ""
    # 🔴 与生产 agent.py:1477-1506 同步：多动作且无澄清（plan_only）时，
    # 生产路由取「第一个可执行动作」接管执行，其余动作待续办。评测若不同步，
    # 模型输出多动作会被误判成「工具错」（2026-11-12 多步编排升级）。
    # 🔴 过滤规则与生产一致：navigate/undo/chat 不进编排（导航/撤销/闲聊不是
    # 待办业务动作），否则「打开工作台再记一条巡视」会被评测成先执行 navigate。
    _ORCH_SKIP = {"navigate", "undo", "chat"}
    if not tool and plan.get("execution_mode") == "plan_only":
        if not plan.get("clarifications"):
            for _item in (plan.get("actions") or []):
                if not isinstance(_item, dict):
                    continue
                _aid = str(_item.get("id") or "").strip()
                if _aid in _ORCH_SKIP:
                    continue
                _spec = _plan.ACTIONS.get(_aid)
                _mtool = str((_spec or {}).get("tool") or "").strip() if _spec else ""
                if _mtool and _mtool in _tools_mod.TOOL_BY_NAME:
                    tool = _mtool
                    break
    args = {**_fact_model.facts_to_args(plan.get("facts")), **semantic_args}
    if plan.get("record_type") and not str(args.get("record_type") or "").strip():
        args["record_type"] = plan["record_type"]
    return {
        "tool": tool or None,
        "args": args,
        "answer": _norm(parsed.get("answer")) or "",
        # 透传给报告用，judge 不读
        "intent": plan.get("intent"),
        "action_ids": [str(i.get("id") or "") for i in (plan.get("actions") or [])],
        "clarifications": plan.get("clarifications") or [],
        "execution_mode": plan.get("execution_mode"),
    }


def resolve_via_ast_fallback(parsed: dict | None) -> dict | None:
    """降级路径：生产模块 import 不了时，用 AST 抠的 ACTIONS 做 id→tool 映射。

    只覆盖「单动作直译」，不复刻 clarifications/多动作判定（那是生产逻辑，
    抄一遍就会再漂移）。报告里会标注降级，此时分数只能当粗筛。
    """
    if not isinstance(parsed, dict):
        return parsed
    actions = parsed.get("actions")
    action_ids: list[str] = []
    if isinstance(actions, list):
        for item in actions[:8]:
            aid = item.strip() if isinstance(item, str) else (
                _norm(item.get("id") or item.get("action")) if isinstance(item, dict) else "")
            if aid:
                action_ids.append(aid)
    tool = None
    if len(action_ids) == 1:
        spec = _AST_ACTIONS.get(action_ids[0]) or {}
        tool = spec.get("tool") or None
    args = dict(parsed.get("args")) if isinstance(parsed.get("args"), dict) else {}
    for fact in (parsed.get("facts") or []):
        if isinstance(fact, dict):
            field, value = _norm(fact.get("field")), fact.get("value")
            if field and _norm(value) and field not in args:
                args[field] = value
    if parsed.get("record_type") and not args.get("record_type"):
        args["record_type"] = parsed["record_type"]
    return {"tool": tool, "args": args,
            "answer": _norm(parsed.get("answer")) or "",
            "intent": parsed.get("intent"), "action_ids": action_ids,
            "clarifications": [], "execution_mode": "fallback"}


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
        # 🔴 新契约解析：生产链可用则调生产函数（normalize_candidate→
        # enrich_plan_facts→facts_to_args），否则降级 AST id→tool 映射。
        # native 对照组已产出 {tool,args}，无需再解析。
        if not native and parsed is not None:
            say = case.get("say") or ""
            material = str(case.get("material") or "").strip()
            parsed = (resolve_via_production(parsed, say, material)
                      if PROD_CHAIN_OK else resolve_via_ast_fallback(parsed))
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
