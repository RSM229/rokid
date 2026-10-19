# Agent 意图路由跑分器

给「聊天即操作」的 LLM 意图路由打分。**改 prompt / 拆工具 / 换模型之后跑一遍，
用数字判断有没有变好**，不再凭感觉说"答非所问"。

## 为什么需要它

线上模型是 `qwen3.7-plus`，`/api/agent/chat` 把 11 个工具的目录塞进 system prompt，
让模型手搓一个 `{tool, args, answer}` 的 JSON。**它经常返回 `tool: null` 然后反问用户**
—— 这就是「答非所问」的技术真相：不是模型听不懂，是它选择了不调工具。

### 首轮跑分结果（2026-10-16，均为 `--repeat 3` 严格模式）

| prompt | 分数 | 不稳定用例 | 状态 |
|---|---|---|---|
| 旧生产 prompt | **75.0%**（30/40） | 4 条 | 已被替换 |
| v2（收窄追问约束） | **97.5%**（39/40） | 1 条 | ✅ 11:21 已合入 `agent.py` |
| 合并后复验 | **97.5%**（39/40） | 1 条（J02） | ✅ 与 A/B 一致 |

根因是 prompt 里三条约束打架，模型把「某个字段没给」也当成了「关键参数缺失」，
于是触发追问而不调工具。修法见 `prompt_v2.txt`（已合入生产，文件保留作 A/B 基准）。

**铁证**：`C01「记一条旁站：屋面防水卷材铺贴，9:00 到 12:00，10人」`是 `ToolSpec`
自带的 example、一字不差，旧 prompt 下模型仍返回 `tool: null` 去追问「旁站结论是什么」。
旧 prompt 的 11 条失败里 **9 条都是 tool=null**。

### 合并后仍存的 1 条不稳定

`J02`（用户答「九点到十一点」补时间）：2/3 通过。失败时模型返回 null 去追问
「巡视发现的问题及处理情况是什么」——「合并后立即调工具、严禁继续追问」这条约束
在边界情况下没压住。这是当前唯一残留，属于「拆工具」能进一步改善的范围。

## 一分钟上手

```bash
cd D:\rokid\rokid_glass\rokid_glass\cloud-backend

# 0) 先自测跑分器本身（不调 API、不花钱、不需要 key）
python tests/agent_eval/test_judge.py

# 1) 看会发什么给模型（不调 API）
python tests/agent_eval/runner.py --dry-run

# 2) 跑生产 prompt 全量（v2 已合入 agent.py，这就是当前线上口径）
python tests/agent_eval/runner.py --concurrency 6

# 3) 🔴 要下结论就得重复跑 —— 单次跑分有波动（实测同版本 73.2% vs 80.0%）
python tests/agent_eval/runner.py --concurrency 6 --repeat 3

# 4) 改 prompt 做 A/B（--prompt-file 与 AST 路径逐字等价）
python tests/agent_eval/runner.py --prompt-file tests/agent_eval/prompt_v2.txt --repeat 3

# 5) 只跑某一组 / 某几条，快速验证一个改动
python tests/agent_eval/runner.py --group 巡视
python tests/agent_eval/runner.py --id J02 --repeat 5
```

报告落在 `D:/rokid/exports/agent跑分_*.md`。退出码 0=全过 / 1=有失败 / 2=环境问题，可直接接 CI。

### API key 从哪来

按优先级：环境变量 `AGENT_EVAL_API_KEY` → `DEFAULT_LLM_API_KEY` → `--key` →
从 `docker-compose.yml` 里解析 `DEFAULT_LLM_API_KEY`（默认走这条，无需配置）。
🔴 key 只进请求头，**不会写进报告**。

## 三个文件

| 文件 | 作用 |
|---|---|
| `cases.py` | **测试集**：42 条真实说法 + 期望结果。改这个 = 改考卷 |
| `runner.py` | **跑分器**：提 prompt、调模型、比对、出报告。零第三方依赖 |
| `test_judge.py` | **跑分器自测**：42 条断言，防跑分器自己算错 |
| `prompt_v2.txt` | 改进版 system prompt（**11:21 已合入生产 `agent.py`**，文件保留作 A/B 基准） |

🔴 `--prompt-file` 读文件时会 `rstrip("\n")`，与 AST 提取路径**逐字等价**
（实测两者 md5 均为 `7c66660e`）。此前不 rstrip 会造成 1 字符「假差异」，
让人误以为 catalog 变了、白查一轮。

## 设计上的三个关键决定

### 1. prompt 走 AST 提取，不 import

本机 venv **缺 pydantic**，`import app.routers.agent` 直接失败。所以用 `ast` 从生产源码里
抠 `_AGENT_SYSTEM` 模板和 `TOOLS` 注册表，复刻 `catalog_for_prompt()`。

好处不只是绕开依赖 —— 🔴 **prompt 与生产同源**。你改了 `agent.py` 里的硬约束、
或给 `ToolSpec` 加了 examples，跑分器自动跟上，不存在「测的是另一套 prompt」这种假分数。
`test_judge.py` 里有一条自测会校验 catalog 的输出格式没走样。

### 2. judge 的假阳性/假阴性防线

跑分器自己算错，比没有跑分器更危险。所以 `test_judge.py` 用**合成的模型输出**打 judge，
两类风险都测：

- **假阳性**（明明错了却判通过）→ 你会以为 prompt 改好了，其实没有
- **假阴性**（明明对了却判失败）→ 你会去改根本没问题的地方

真实踩过的两个坑，都已变成自测：

1. 我口头说 `create_work_record` 有 27 个参数，自测一跑是 **30 个** —— 连口算都不可信
2. `D02` 曾断言 `generate_document` 要传 `extra_fields`，但**这个工具根本没这个参数**。
   模型不传才是对的，却被判失败。现在有一条元测试
   `test_expected_args_all_declared` 专门拦这类错：期望的参数必须在工具的 params 里真实声明过。

### 3. 基础设施故障 ≠ 模型失败

超时、5xx、429 这类是网络问题，**不算模型的错**。跑分器会自动重试（`--retries`，默认 2 次），
仍失败就单列出来、不进总分分母。

踩过这个坑：一次 repeat=3 的跑分里 H03 超时了，被记成「模型失败」，
总分从 100% 掉到 95% —— 差点让我去改一个根本没问题的 prompt。
报告里现在会明确写「⚠️ N 条因网络故障未计分」。

## 读报告

### 总分是严格模式

`--repeat N` 时，一条用例**N 次全过才算过**。因为线上只调一次，
时对时错的用例在用户眼里就是"有时好用有时不好用"，比稳定失败更难查。
报告里「不稳定用例」区块会单独列出这些。

### gap 用例：路由通过 ≠ 功能可用

跑分器**只测「模型选工具 + 抽参数」这一层，测不到服务端执行层**。

标了 `gap: True` 的用例（如 `D06` 安全监理日志）即使显示「路由层通过」，
端到端功能**仍然不可用** —— 失败点在服务端（聚合分支缺失 / `validate_values` 卡住 /
参数无处可传）。这类单列、不计入总分，note 里写清缺口在哪。
🔴 别把「通过」误读成「已修好」。

### 均 prompt token = 每次调用的固定成本

报告末尾的 token 统计里，`prompt 均 XXXX` 就是每问一句话都要付的固定开销
（当前约 2500）。拆工具、精简 catalog 能直接把它压下来 —— 省钱也降延迟。

## 加用例的规矩

🔴 **从真实测试反馈里取原话，不要自己造"理想句"。** 理想句测出来的高分是假的。

当前语料来源：
- `D:/rokid/exports/一句话建记录_语料_20261016.md` —— 巡视/危大巡视/监理日志
- `D:/rokid/exports/监理通真机测试步骤与对应文本_v2_20261014.md` —— 2.1/2.3/2.4/2.6 等章节原话
- `agent_tools.py` TOOLS 注册表里的 `examples` —— 模型本该答对的基本盘

一条 case 长这样：

```python
{
    "id": "A01", "group": "巡视",              # id 唯一；group 决定报告分组
    "say": "记一条巡视：3号楼二层临边洞口防护，符合要求。",
    "history": [{"role": "assistant", "text": "什么类型？"}],   # 可选，测多轮
    "expect": {
        "tool": "create_work_record",           # 期望工具；None = 期望不调工具
        "tool_any_of": ["query_regulation", None],  # 或用这个：多个都可接受
        "tool_forbidden": ["create_hazard"],    # 选到即算错
        "args_required": {"record_type": ["巡视"], "section": ["3号楼二层"]},
        #                  ↑ 参数名      ↑ 可接受值列表，互为子串即算对
        "args_forbidden": ["station_part"],     # 不该出现的参数
        "expect_ask": True,                     # 期望不调工具、只在 answer 里追问
    },
    "gap": True,                                # 可选：已知产品缺口，不计分
    "note": "这条在测什么、为什么这么期望",        # 必填，报告里会显示
},
```

**值匹配是「互为子串」**：期望 `巡视` 时，模型答 `巡视记录` 算对。
这是刻意的宽松 —— 否则全是假阴性。要严格相等就自己写特判（见 `judge()` 末尾的
`if case["id"] == "A04"` 那段，逐 case 特判的写法）。

改完 `cases.py` **务必跑一次自测**，元测试会拦住写错的期望：

```bash
python tests/agent_eval/test_judge.py
```

## 已知边界（这个跑分器测不了什么）

- 🔴 **测不了服务端执行**。只覆盖「模型输出的 JSON 对不对」，不验证落库、渲染 docx、权限校验。
  `gap` 用例就是这类边界的标记。
- **测不了追问的措辞质量**。只判断"有没有追问"和"是不是一次只问一个"，
  不判断问得好不好、顺序对不对。
- **测不了语音转写错误**。`say` 是文字，真实场景用户是语音输入，
  ASR 错字会额外增加路由难度 —— 这部分要靠真机测试脚本补。
- **单次分数有波动**。temperature=0.2 下同一版 prompt 两次跑分能差 3 条。
  🔴 要下结论必须 `--repeat 3` 以上，别拿单次分数说事。
