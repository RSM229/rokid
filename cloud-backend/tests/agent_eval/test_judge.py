"""judge() 自测 —— 跑分器自己必须先是对的，否则分数是假的。

用**合成的模型输出**打 judge，不调 API、不花钱、不需要 key。
覆盖两类风险：
  1. **假阳性**：明明错了却判通过 → 你会以为 prompt 改好了，其实没有
  2. **假阴性**：明明对了却判失败 → 你会去改根本没问题的地方

跑法：
    python -m pytest tests/agent_eval/test_judge.py -q
    python tests/agent_eval/test_judge.py      # 直接跑，打印明细
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cases import CASES  # noqa: E402
from runner import judge, parse_json_object, history_messages, build_catalog, extract_tools  # noqa: E402

CASE_BY_ID = {c["id"]: c for c in CASES}


# ---------------------------------------------------------------------------
# 一、parse_json_object 必须与生产 _parse_json_object 行为一致
# ---------------------------------------------------------------------------

def test_parse_plain():
    assert parse_json_object('{"tool": null, "args": {}, "answer": "你好"}') == {
        "tool": None, "args": {}, "answer": "你好"}


def test_parse_fenced():
    """模型爱加 ```json 包裹 —— 生产兼容，跑分器也必须兼容，否则大量假阴性。"""
    raw = '```json\n{"tool": "query_weather", "args": {}, "answer": "今天晴"}\n```'
    assert parse_json_object(raw)["tool"] == "query_weather"


def test_parse_with_prose():
    """模型在 JSON 前后加废话 —— 生产用 find('{')/rfind('}') 兜底。"""
    raw = '好的，结果如下：{"tool": "navigate", "args": {"page": "工作记录"}, "answer": "已为你打开"} 希望有帮助'
    assert parse_json_object(raw)["tool"] == "navigate"


def test_parse_garbage_returns_none():
    """解析不出 → None。生产此时会退化成 _plain_answer，跑分器要如实记失败。"""
    assert parse_json_object("我不知道该调什么工具") is None
    assert parse_json_object("") is None


# ---------------------------------------------------------------------------
# 二、history_messages 必须与生产 _history_messages 一致
# ---------------------------------------------------------------------------

def test_history_filters_and_truncates():
    h = [
        {"role": "user", "text": "记一条巡视"},
        {"role": "assistant", "text": "什么类型？"},
        {"role": "system", "text": "这条该被丢掉"},
        {"role": "user", "text": ""},
        {"role": "user", "content": "巡视"},
    ]
    out = history_messages(h)
    assert len(out) == 3
    assert out[0] == {"role": "user", "content": "记一条巡视"}
    assert out[-1] == {"role": "user", "content": "巡视"}


def test_history_limit_6():
    out = history_messages([{"role": "user", "text": f"m{i}"} for i in range(20)])
    assert len(out) == 6
    assert out[0]["content"] == "m14"   # 只留最近 6 轮


# ---------------------------------------------------------------------------
# 三、judge —— 假阳性防线：这些**必须判失败**
# ---------------------------------------------------------------------------

def _j(cid, parsed, err=None):
    ok, fails = judge(CASE_BY_ID[cid], parsed, err)
    return ok, fails


def test_wrong_tool_detected():
    """选错工具必须抓到 —— 这是「答非所问」最直接的表现。"""
    ok, fails = _j("A01", {"tool": "create_hazard", "args": {}, "answer": "好的"})
    assert not ok and any("工具错" in f for f in fails)


def test_degraded_to_plain_answer_detected():
    """🔴 最隐蔽的失败：该调工具却 tool=null（生产会退化成纯聊天）。"""
    ok, fails = _j("A01", {"tool": None, "args": {}, "answer": "巡视记录需要注意临边防护哦"})
    assert not ok and any("工具错" in f for f in fails)


def test_missing_required_arg_detected():
    ok, fails = _j("A01", {"tool": "create_work_record",
                           "args": {"record_type": "巡视"}, "answer": "好的"})
    assert not ok
    assert any("缺参数 section" in f for f in fails)
    assert any("缺参数 problems" in f for f in fails)


def test_wrong_arg_value_detected():
    ok, fails = _j("A01", {"tool": "create_work_record",
                           "args": {"record_type": "旁站", "section": "3号楼二层",
                                   "inspection_start_time": "8:30",
                                   "inspection_end_time": "17:30",
                                   "problems": "符合要求"}, "answer": ""})
    assert not ok and any("record_type" in f for f in fails)


def test_forbidden_tool_detected():
    """F01「我今天记了几条」含"记"字，若误判成建单必须抓到。"""
    ok, fails = _j("F01", {"tool": "create_work_record", "args": {}, "answer": ""})
    assert not ok
    assert any("禁止的工具" in f for f in fails)


def test_h01_knowledge_question_must_not_call_tool():
    """🔴 H01「旁站监理要点有哪些」是问知识。调了工具 = 典型答非所问。"""
    ok, fails = _j("H01", {"tool": "create_work_record",
                           "args": {"record_type": "旁站"}, "answer": ""})
    assert not ok and any("期望不调工具" in f for f in fails)


def test_a04_project_name_leak_detected():
    """🔴 A04 特判：项目名串进 section。这是 MEMORY 里记的硬约束第4条。"""
    ok, fails = _j("A04", {"tool": "create_work_record",
                           "args": {"record_type": "巡视", "section": "滨江花园一期3号楼二层临边防护",
                                   "problems": "符合要求"}, "answer": ""})
    assert not ok and any("项目名串进了 section" in f for f in fails)


def test_a03_problems_dropped_detected():
    """🔴 A03/J04 特判：problems=符合要求 被当空值丢掉（服务端 _NO_ISSUE_HINTS 的由来）。"""
    ok, fails = _j("A03", {"tool": "create_work_record",
                           "args": {"record_type": "巡视", "section": "3号楼二层临边防护"},
                           "answer": ""})
    assert not ok and any("被当成没问题丢掉" in f for f in fails)


def test_j03_skip_must_still_call_tool():
    """🔴 J03「跳过，直接创建」必须仍调工具，只回文字 = 严重失败。"""
    ok, fails = _j("J03", {"tool": None, "args": {}, "answer": "好的，已为你跳过补充"})
    assert not ok
    assert any("工具错" in f for f in fails)


def test_j01_incomplete_merge_detected():
    """🔴 J01 多轮合并：只传本次补充的 record_type，丢了历史的部位/内容 = 反复追问的根因。"""
    ok, fails = _j("J01", {"tool": "create_work_record",
                           "args": {"record_type": "巡视"}, "answer": ""})
    assert not ok
    assert any("缺参数 section" in f for f in fails)
    assert any("缺参数 work_content" in f for f in fails)


def test_j05_supplement_skip_detected():
    """🔴 J05：追问途中岔开问规范，不能被当成补字段塞进草稿。"""
    ok, fails = _j("J05", {"tool": "create_work_record",
                           "args": {"record_type": "巡视", "section": "3号楼二层",
                                   "work_content": "模板支撑验收规范"}, "answer": ""})
    assert not ok and any("禁止的工具" in f for f in fails)


def test_expect_ask_but_called_tool_detected():
    """I01/I02/I03 缺类型 → 该追问。硬猜 record_type 必须判失败。"""
    ok, fails = _j("I01", {"tool": "create_work_record",
                           "args": {"record_type": "巡视", "section": "3号楼二层"},
                           "answer": "已记录"})
    assert not ok and any("该追问却直接调了工具" in f for f in fails)


def test_markdown_in_answer_detected():
    """硬约束：answer 纯文本。真机测试第 10.7 条也在查这个。"""
    ok, fails = _j("H02", {"tool": None, "args": {}, "answer": "你好！\n**要点**：\n- 第一项"})
    assert not ok and any("Markdown" in f for f in fails)


def test_request_error_detected():
    ok, fails = _j("A01", None, err="HTTP 401: invalid api key")
    assert not ok and any("请求失败" in f for f in fails)


def test_unparseable_detected():
    """解析不出 JSON → 明确记为「生产会退化成纯问答」，不能静默算过。"""
    ok, fails = _j("A01", None, None)
    assert not ok and any("退化成纯问答" in f for f in fails)


def test_h03_tool_any_of_accepts_both():
    """H03 本身有歧义：走 RAG 或凭知识直答都算对。"""
    for tool in ("query_regulation", None):
        ok, fails = _j("H03", {"tool": tool, "args": {}, "answer": "锚固长度要求……"})
        assert ok, (tool, fails)


def test_tool_any_of_rejects_wrong_one():
    """但选到第三个工具仍要判错，否则 tool_any_of 会变成万能通过。"""
    ok, fails = _j("H03", {"tool": "create_work_record", "args": {}, "answer": ""})
    assert not ok and any("不在可接受集合内" in f for f in fails)


# ---------------------------------------------------------------------------
# 四、judge —— 假阴性防线：这些**必须判通过**
# ---------------------------------------------------------------------------

def test_a01_perfect_pass():
    ok, fails = _j("A01", {
        "tool": "create_work_record",
        "args": {"record_type": "巡视", "section": "3号楼二层临边洞口防护和高处作业",
                 "inspection_start_time": "8:30", "inspection_end_time": "17:30",
                 "worker_count": "12", "problems": "未发现问题，符合要求",
                 "record_date": "2026-10-09"},
        "answer": "已创建巡视记录"})
    assert ok, fails


def test_value_substring_tolerance():
    """「巡视记录」vs「巡视」、「08:30」vs「8:30」应算对，否则全是假阴性。"""
    ok, fails = _j("A01", {
        "tool": "create_work_record",
        "args": {"record_type": "巡视记录", "section": "3号楼二层",
                 "inspection_start_time": "08:30", "inspection_end_time": "17:30",
                 "problems": "符合要求"},
        "answer": ""})
    assert ok, fails


def test_h01_null_tool_pass():
    ok, fails = _j("H01", {"tool": None, "args": {},
                           "answer": "混凝土浇筑旁站要点：一是核对配合比，二是检查模板支撑，三是控制浇筑顺序"})
    assert ok, fails


def test_h01_null_string_variants_pass():
    """模型可能输出字符串 "null" 而不是 JSON null，都要认。"""
    for v in ("null", "none", "None", ""):
        ok, fails = _j("H02", {"tool": v, "args": {}, "answer": "你好，我是白泽"})
        assert ok, (v, fails)


def test_i01_ask_pass():
    ok, fails = _j("I01", {"tool": None, "args": {},
                           "answer": "请问这条记录的类型是巡视、旁站、验收还是见证？"})
    assert ok, fails


def test_a05_chinese_time_both_ways_pass():
    """中文时间：原样传 或 已换算，两种都该算对（硬约束允许）。"""
    for args in ({"record_type": "巡视", "section": "3号楼二层",
                  "inspection_time": "九点到五点半"},
                 {"record_type": "巡视", "section": "3号楼二层",
                  "inspection_start_time": "09:00", "inspection_end_time": "17:30"}):
        ok, fails = _j("A05", {"tool": "create_work_record", "args": args, "answer": ""})
        assert ok, (args, fails)


def test_j01_full_merge_pass():
    ok, fails = _j("J01", {
        "tool": "create_work_record",
        "args": {"record_type": "巡视", "section": "3号楼二层",
                 "work_content": "钢筋绑扎", "weather": "晴",
                 "problems": "现场情况正常"},
        "answer": "已创建巡视记录"})
    assert ok, fails


def test_b01_long_sentence_pass():
    """B01 超长句：所有必填都抽到就该过，不苛求选填。"""
    ok, fails = _j("B01", {
        "tool": "create_work_record",
        "args": {"record_type": "危大工程巡视", "section": "西楼装配式楼梯吊装部位",
                 "hazard_type": "装配式建筑混凝土预制构件安装工程-一般危大工程",
                 "weather": "晴", "wind": "无风",
                 "inspection_start_time": "8:30", "inspection_end_time": "15:32",
                 "problems": "未发现问题，符合要求"},
        "answer": ""})
    assert ok, fails


def test_d01_period_day_and_chinese_pass():
    for p in ("day", "日"):
        ok, fails = _j("D01", {"tool": "generate_document",
                               "args": {"period": p}, "answer": "好的"})
        assert ok, (p, fails)


def test_d02_with_extra_fields_pass():
    ok, fails = _j("D02", {"tool": "generate_document",
                           "args": {"period": "day",
                                    "extra_fields": {"7_10": "上级质监站上午来检查未发现问题",
                                                     "7_11": "无"}},
                           "answer": "好的"})
    assert ok, fails


def test_missing_args_key_treated_as_empty():
    """模型完全不返回 args 键时不该崩，应记为缺参数。"""
    ok, fails = _j("F02", {"tool": "query_work_records", "answer": ""})
    assert not ok and any("缺参数 section" in f for f in fails)


def test_args_not_dict_tolerated():
    """模型把 args 写成字符串 —— judge 不该抛异常。"""
    ok, fails = _j("F06", {"tool": "query_weather", "args": "今天", "answer": ""})
    assert ok, fails


# ---------------------------------------------------------------------------
# 五、catalog 复刻必须与生产同构
# ---------------------------------------------------------------------------

def test_catalog_shape_matches_production():
    """🔴 关键防线：catalog 格式若与 catalog_for_prompt() 不一致，跑分测的就是另一套 prompt。"""
    catalog = build_catalog(extract_tools())
    assert "- create_work_record：" in catalog
    assert "  参数：" in catalog
    assert "  示例说法：" in catalog          # examples 必须被输出
    assert "撤销 / 刚才那条删掉" in catalog    # 多 example 用 " / " 连接
    assert "（无参数）" in catalog             # undo_last_action 无参数


def test_all_11_tools_extracted():
    tools = extract_tools()
    assert len(tools) == 11, f"应提取到 11 个工具，实际 {len(tools)}"
    names = {t["name"] for t in tools}
    assert names == {"create_work_record", "create_hazard", "create_provform_doc",
                     "generate_document", "undo_last_action", "query_work_records",
                     "query_hazards", "query_documents", "query_regulation",
                     "query_weather", "navigate"}


def test_create_work_record_has_30_params():
    """记录当前参数规模 —— 这个数字是「该不该拆工具」讨论的量化依据。

    2026-10-16 实测 = 30 个（不是先前口头估的 27）。一个工具挂 30 个参数、
    还要模型在其中按 record_type 挑对 5~8 个，是意图路由出错的主要结构性原因。
    """
    t = next(x for x in extract_tools() if x["name"] == "create_work_record")
    assert len(t["params"]) == 30, f"create_work_record 参数数变了：{len(t['params'])}"
    # 只有 1 个必填（work_content），其余 29 个全靠模型自己判断该不该传
    required = [k for k in t["params"] if k.startswith("*")]
    assert required == ["*work_content"], f"必填参数变了：{required}"


def test_every_case_id_unique_and_referenced_correctly():
    """测试集自检：id 唯一、group 非空、expect 至少有断言。"""
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)), "case id 重复"
    for c in CASES:
        assert c["group"], f"{c['id']} 缺 group"
        assert c["note"], f"{c['id']} 缺 note"
        exp = c["expect"]
        assert any(k in exp for k in
                   ("tool", "tool_any_of", "tool_forbidden", "args_required",
                    "args_any_of", "expect_ask")), \
            f"{c['id']} 的 expect 没有任何断言，等于白跑"


def test_expected_tools_all_exist():
    """🔴 元防线：期望的工具名必须在 TOOLS 里真实存在，否则是测试集写错。"""
    real = {t["name"] for t in extract_tools()}
    for c in CASES:
        exp = c["expect"]
        for key in ("tool",):
            v = exp.get(key)
            if v is not None:
                assert v in real, f"{c['id']} 期望了不存在的工具 {v}"
        for v in exp.get("tool_any_of") or []:
            if v is not None:
                assert v in real, f"{c['id']} tool_any_of 含不存在的工具 {v}"
        for v in exp.get("tool_forbidden") or []:
            assert v in real, f"{c['id']} tool_forbidden 含不存在的工具 {v}"


def test_expected_args_all_declared():
    """🔴 元防线：期望的参数必须在该工具的 params 里声明过。

    这条是为了防止再犯 D02 那种错 —— 当时断言 generate_document 要传
    extra_fields，但它根本没这个参数，模型不传才是对的，结果被误判为失败。
    有这条自测，写错当场就暴露。
    """
    tools = {t["name"]: {k.lstrip("*") for k in t["params"]} for t in extract_tools()}
    for c in CASES:
        candidates = [c["expect"].get("tool")] + list(c["expect"].get("tool_any_of") or [])
        for want in candidates:
            if not want or want not in tools:
                continue
            declared = tools[want]
            for key in (c["expect"].get("args_required") or {}):
                assert key in declared, \
                    f"{c['id']}：{want} 没有声明参数 {key}（现有：{sorted(declared)}）"
            for key in c["expect"].get("args_forbidden") or []:
                assert key in declared, f"{c['id']}：{want} 没有声明参数 {key}"
            for grp in c["expect"].get("args_any_of") or []:
                for key in grp:
                    assert key in declared, f"{c['id']}：{want} 没有声明参数 {key}"


def test_gap_cases_have_reason():
    """gap 不计入总分，所以必须写清缺口在哪，否则会被用来掩盖真问题。

    🔴 注意：这条断言里必须用 "gap" 而不是旧的 "xfail" —— 键名改过之后，
    若这里没跟着改，循环体永远不执行，测试会**静默变成永真**（比没测更危险）。
    """
    from cases import gap_cases
    gaps = gap_cases()
    assert gaps, "gap_cases() 返回空 —— 要么用例丢了，要么键名写错了"
    for c in gaps:
        assert c.get("gap") is True, f"{c['id']} 未被 gap_cases() 正确识别"
        assert "缺口" in c["note"] or "歧义" in c["note"], \
            f"{c['id']} 标了 gap 但 note 没说明缺口在哪"


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 六、拆分工具归一（--split-tools）—— 决定 A/B 对比是否公平
# ---------------------------------------------------------------------------

def test_normalize_split_maps_tool_and_fills_record_type():
    """拆分工具名折回基准名，并补 record_type。"""
    from runner import normalize_split_tools
    got = normalize_split_tools({"tool": "create_xunshi_record",
                                 "args": {"section": "3号楼二层"}, "answer": ""})
    assert got["tool"] == "create_work_record"
    assert got["args"]["record_type"] == "巡视"
    assert got["args"]["section"] == "3号楼二层"   # 原参数不丢


def test_normalize_split_respects_model_record_type():
    """模型自己传了 record_type 就尊重它，不覆盖。"""
    from runner import normalize_split_tools
    got = normalize_split_tools({"tool": "create_weida_record",
                                 "args": {"record_type": "危大工程巡视"}, "answer": ""})
    assert got["args"]["record_type"] == "危大工程巡视"


def test_normalize_split_all_six_tools():
    """六个拆分工具都要能正确映射。"""
    from runner import normalize_split_tools, _SPLIT_TOOL_MAP
    assert len(_SPLIT_TOOL_MAP) == 6
    for split, (base, rtype) in _SPLIT_TOOL_MAP.items():
        got = normalize_split_tools({"tool": split, "args": {}, "answer": ""})
        assert got["tool"] == base, split
        assert got["args"]["record_type"] == rtype, split


def test_normalize_split_leaves_other_tools_untouched():
    """非拆分工具（create_hazard / navigate 等）原样返回，不能被误改。"""
    from runner import normalize_split_tools
    for t in ("create_hazard", "navigate", "query_weather", "generate_document"):
        got = normalize_split_tools({"tool": t, "args": {"x": 1}, "answer": "a"})
        assert got["tool"] == t
        assert got["args"] == {"x": 1}


def test_normalize_split_handles_none_and_null():
    """tool=null（追问场景）不能被归一搞坏。"""
    from runner import normalize_split_tools
    assert normalize_split_tools(None) is None
    got = normalize_split_tools({"tool": None, "args": {}, "answer": "请问是巡视还是旁站？"})
    assert got["tool"] is None and got["args"] == {}


def test_normalize_split_tolerates_non_dict_args():
    """args 不是 dict（模型写错）时不能崩。"""
    from runner import normalize_split_tools
    got = normalize_split_tools({"tool": "create_pangzhan_record", "args": "旁站", "answer": ""})
    assert got["tool"] == "create_work_record"
    assert isinstance(got["args"], dict)


def test_normalize_split_does_not_mutate_input():
    """归一必须返回新对象，不能原地改（否则 repeat 多次会互相污染）。"""
    from runner import normalize_split_tools
    src = {"tool": "create_xunshi_record", "args": {"section": "3号楼"}, "answer": ""}
    got = normalize_split_tools(src)
    assert src["tool"] == "create_xunshi_record", "原始对象被改了！"
    assert "record_type" not in src["args"], "原始 args 被改了！"
    assert got is not src


def test_split_catalog_file_shape():
    """🔴 拆分版 catalog 文件必须含 6 个拆分工具、且不含基准 create_work_record
    （否则 prompt 与 catalog 口径不一致，实验就废了）。"""
    cat = Path(__file__).resolve().parent.joinpath("catalog_split.txt").read_text(encoding="utf-8")
    for split in ("create_xunshi_record", "create_pangzhan_record", "create_yanshou_record",
                  "create_jianzheng_record", "create_weida_record", "create_pending_record"):
        assert f"- {split}：" in cat, f"拆分 catalog 缺 {split}"
    assert "- create_work_record：" not in cat, "拆分 catalog 不该再含基准工具"
    # 非记录类工具必须原样保留
    for keep in ("create_hazard", "generate_document", "navigate", "query_regulation"):
        assert f"- {keep}：" in cat, f"拆分 catalog 丢了 {keep}"


def test_split_prompt_has_no_base_tool_reference():
    """🔴 拆分版 prompt 不能还引用 create_work_record（catalog 里已无此工具）。"""
    p = Path(__file__).resolve().parent.joinpath("prompt_v3_split.txt").read_text(encoding="utf-8")
    assert "create_work_record" not in p, "prompt_v3_split 仍引用基准工具名，会误导模型"
    assert "create_pangzhan_record" in p


def test_answer_forbidden_blocks_hallucinated_image_claim():
    """answer_forbidden：没图却说「经图片识别」必须判失败，不能白捡分。"""
    case = {"id": "X01", "group": "t", "say": "记录今天工作情况",
            "expect": {"tool": None, "expect_ask": True,
                       "answer_forbidden": ["图片识别", "照片显示"]},
            "note": "t"}
    # 编造看图 → 失败
    ok, fails = judge(case, {"tool": None, "args": {},
                             "answer": "经图片识别未发现明显安全隐患，请补充类型？"}, None)
    assert not ok and any("禁用措辞" in f for f in fails), fails
    # 正常追问 → 通过
    ok2, fails2 = judge(case, {"tool": None, "args": {},
                              "answer": "今天要记的是巡视、旁站、验收还是见证？"}, None)
    assert ok2, fails2


def test_answer_forbidden_blocks_false_capability_denial():
    """能力误报也要挡：能读水印却说「无法接收图片」，用户会以为功能坏了。"""
    case = {"id": "X02", "group": "t", "say": "先识别水印",
            "expect": {"tool": None, "expect_ask": True,
                       "answer_forbidden": ["无法接收图片", "无法识别图片", "无法直接接收"]},
            "note": "t"}
    ok, fails = judge(case, {"tool": None, "args": {},
                             "answer": "抱歉，我无法直接接收和识别图片水印。请直接描述？"}, None)
    assert not ok and any("禁用措辞" in f for f in fails), fails


def _run_all() -> int:
    """不依赖 pytest 也能跑：直接 python tests/agent_eval/test_judge.py"""
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    passed, failed = [], []
    for name, fn in fns:
        try:
            fn()
            passed.append(name)
        except AssertionError as exc:
            failed.append((name, f"断言失败: {exc}"))
        except Exception as exc:  # noqa: BLE001
            failed.append((name, f"{type(exc).__name__}: {exc}"))

    print(f"\njudge 自测：{len(passed)} 通过 / {len(failed)} 失败 / 共 {len(fns)}")
    for name, why in failed:
        print(f"  ❌ {name}\n     {why}")
    if not failed:
        print("  ✅ 假阳性与假阴性防线均有效，跑分器可信")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
