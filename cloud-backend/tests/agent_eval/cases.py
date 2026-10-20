"""Agent 意图路由回归测试集（数据，不含逻辑）。

每条 case = 一句真实说法 + 期望的 {tool, args}。跑分器把「实际输出 vs 期望」
逐条比对，得出准确率。改 prompt / 拆工具 / 换模型后重跑，用数字判断有没有变好。

语料来源（都是真实用法，不是编的）：
  - exports/一句话建记录_语料_20261016.md   —— 巡视 / 危大巡视 / 监理日志 / 安全监理日志
  - exports/监理通真机测试步骤与对应文本_v2_20261014.md —— 2.1/2.3/2.4/2.6 等章节的原话
  - app/services/agent_tools.py TOOLS 注册表里的 examples（模型本来就该答对的基本盘）

字段说明
--------
id            唯一编号，报告里定位用
group         分组（报告按组分块，便于看哪一类最弱）
say           发给模型的用户原话
history       可选，前置对话轮次 [{"role": "user|assistant", "text": "..."}]
expect        期望结果，支持四种断言（可组合）：
    tool             期望选中的工具名；None = 期望不调工具（纯问答）
    args_required    {参数名: [可接受的值, ...]}，实际值与任一候选**互为子串**即算对
    args_forbidden   [参数名, ...]，这些参数**不该**出现（防编造 / 防串槽）
    tool_forbidden   [工具名, ...]，选到其中任一即算错（防"退化成纯问答"等偏航）
    expect_ask       True = 期望模型不调工具、只在 answer 里追问（关键参数缺失时）
    answer_forbidden [措辞, ...]，answer 里出现即算错（防编造"经图片识别"、防能力误报）
gap           True = 已知产品缺口，单列统计、**不计入总分**（见 note）。
                含义是「这条在**路由层**可能通过，但**端到端功能不可用**」——
                失败点在服务端（聚合分支缺失 / validate_values 卡住 / 参数无处可传），
                而跑分器只测「模型选工具 + 抽参数」，测不到执行层。所以它通过与否
                都不代表功能可用，必须单列并写清缺口在哪。
note          这条在测什么 / 为什么这么期望

新增 case 的规矩：从真实测试反馈里取原话，不要自己造"理想句"。
理想句测出来的高分是假的。
"""
from __future__ import annotations

CASES: tuple[dict, ...] = (
    # =========================================================================
    # A. 巡视 GD-B-214 —— 主力场景，覆盖最全的一句话建单
    # =========================================================================
    {
        "id": "A01", "group": "巡视",
        "say": "记一条巡视：10月9日8:30到17:30，巡视了3号楼二层临边洞口防护和高处作业，"
               "现场12人作业，未发现问题，符合要求。",
        "expect": {
            "tool": "create_work_record",
            "args_required": {
                "record_type": ["巡视"],
                "section": ["3号楼二层"],
                "inspection_start_time": ["8:30", "08:30"],
                "inspection_end_time": ["17:30"],
                "problems": ["未发现问题", "符合要求"],
            },
        },
        "note": "语料①完整版。一次给全 → 应该直接建单，不该追问。",
    },
    {
        "id": "A02", "group": "巡视",
        "say": "记一条巡视：3号楼二层临边洞口防护，上午9点开始巡视。",
        "expect": {
            "tool": "create_work_record",
            "args_required": {
                "record_type": ["巡视"],
                "section": ["3号楼二层"],
                "inspection_start_time": ["9:00", "09:00", "9"],
            },
        },
        "note": "语料①精简版。类型和部位都给了 → 该建单，尾巴字段由服务端追问。",
    },
    {
        "id": "A03", "group": "巡视",
        "say": "3号楼二层临边防护，9:00 到 11:00，符合要求，记一条巡视",
        "expect": {
            "tool": "create_work_record",
            "args_required": {
                "record_type": ["巡视"],
                "section": ["3号楼二层"],
                "problems": ["符合要求"],
            },
        },
        "note": "🔴 ToolSpec 自带 example，属于基本盘。重点测 problems=符合要求 "
                "不能被当成空值丢掉（MEMORY.md 记的服务端修复点）。",
    },
    {
        "id": "A04", "group": "巡视",
        "say": "巡视滨江花园一期，3号楼二层临边防护，符合要求",
        "expect": {
            "tool": "create_work_record",
            "args_required": {
                "record_type": ["巡视"],
                "section": ["3号楼二层"],
            },
            "args_forbidden": [],
        },
        "note": "🔴 陷阱：项目名「滨江花园一期」不能填进 section（_AGENT_SYSTEM 硬约束第4条）。"
                "runner 额外断言 section 值里不含「滨江花园」。",
    },
    {
        "id": "A05", "group": "巡视",
        "say": "记一条巡视：3号楼二层，九点到五点半",
        "expect": {
            "tool": "create_work_record",
            "args_required": {
                "record_type": ["巡视"],
                "section": ["3号楼二层"],
            },
        },
        "note": "中文时间。硬约束允许原样传 inspection_time 由服务端换算，"
                "也接受 start/end 已换算 —— 两者有其一即算对（runner 特判）。",
    },

    # =========================================================================
    # B. 危大巡视 GDAQ30306 —— 字段最多，最容易抽漏
    # =========================================================================
    {
        "id": "B01", "group": "危大巡视",
        "say": "记一条危大工程巡视：今天晴、无风，8:30到15:32，西楼装配式楼梯吊装部位，"
               "危大工程是「装配式建筑混凝土预制构件安装工程-一般危大工程」、类别选其他；"
               "巡视内容按预制构件吊装检查表逐项检查；施工单位项目负责人到岗、专职安全员到岗、"
               "特种作业人员持证且与方案相符；未发现问题，符合要求。",
        "expect": {
            "tool": "create_work_record",
            "args_required": {
                "record_type": ["危大", "危大工程巡视", "危大巡视"],
                "section": ["西楼装配式楼梯吊装", "装配式楼梯吊装"],
                "hazard_type": ["装配式建筑混凝土预制构件安装工程"],
                "weather": ["晴"],
                "wind": ["无风"],
                "problems": ["未发现问题", "符合要求"],
            },
        },
        "note": "语料②装配式版。长句多字段，考察参数抽取完整性。",
    },
    {
        "id": "B02", "group": "危大巡视",
        "say": "记一条危大工程巡视：今天晴、无风，8:31到17:30，西楼落地式钢管脚手架，"
               "危大工程「落地式钢管脚手架工程-一般危大工程」、类别脚手架；"
               "巡视内容：立杆间距、水平杆步距、剪刀撑、连墙件、脚手板与安全网；"
               "施工单位项目负责人到岗、专职安全员到岗、架子工持证；未发现问题，符合要求。",
        "expect": {
            "tool": "create_work_record",
            "args_required": {
                "record_type": ["危大", "危大工程巡视", "危大巡视"],
                "hazard_type": ["落地式钢管脚手架工程"],
                "weather": ["晴"],
            },
        },
        "note": "语料②脚手架版。",
    },
    {
        "id": "B03", "group": "危大巡视",
        "say": "记一条危大巡视：西楼落地式钢管脚手架，一般危大工程，符合要求。",
        "expect": {
            "tool": "create_work_record",
            "args_required": {
                "record_type": ["危大", "危大巡视", "危大工程巡视"],
                "hazard_type": ["落地式钢管脚手架"],
                "problems": ["符合要求"],
            },
        },
        "note": "语料②精简版。",
    },

    # =========================================================================
    # C. 旁站 / 验收 / 见证 —— 其余三类工作记录
    # =========================================================================
    {
        "id": "C01", "group": "旁站",
        "say": "记一条旁站：屋面防水卷材铺贴，9:00 到 12:00，10人",
        "expect": {
            "tool": "create_work_record",
            "args_required": {
                "record_type": ["旁站"],
                "station_part": ["屋面", "防水卷材"],
                "station_start_time": ["9:00", "09:00"],
                "station_end_time": ["12:00"],
            },
        },
        "note": "🔴 ToolSpec 自带 example，基本盘。",
    },
    {
        "id": "C02", "group": "旁站",
        "say": "旁站记录：2号楼三层混凝土浇筑，关键工序是混凝土浇筑，8点到12点，"
               "混凝土工8人钢筋工4人，检查模板支撑和坍落度都符合要求，未发现问题。",
        "expect": {
            "tool": "create_work_record",
            "args_required": {
                "record_type": ["旁站"],
                "station_part": ["2号楼三层"],
                "station_process": ["混凝土浇筑"],
                # 用户原话同时说了「都符合要求」和「未发现问题」，两者都是合理结论，
                # 只认「符合要求」会造成假阴性（拆分版实测踩到过）。
                "station_conclusion": ["符合要求", "未发现问题"],
            },
        },
        "note": "真机 4.2 旁站表的口语化版本。核心是测 station_part / station_process **不串槽**"
                "（部位填 station_part、工序填 station_process）。"
                " 🔴 实测 v2 prompt 下这条 2/3 通过、不稳定：模型把「2号楼三层」放进 section，"
                "却把「混凝土浇筑」同时塞进 station_part 和 station_process，部位丢了。"
                "根因是 create_work_record 里 section/station_part/station_process 三个参数"
                "语义重叠、边界不清。注：station_conclusion 曾只认「符合要求」，但用户原话"
                "同时说了「符合要求」和「未发现问题」，模型填后者是合理的 → 已放宽为两者皆可"
                "（修正假阴性，非放松核心断言；station_part/process 的串槽检测仍在）。",
    },
    {
        "id": "C03", "group": "验收",
        "say": "记一条验收：2号楼三层墙柱钢筋隐蔽工程验收，验收钢筋规格间距保护层和连接方式，"
               "自检合格，符合设计及规范要求，验收合格。",
        "expect": {
            "tool": "create_work_record",
            "args_required": {
                "record_type": ["验收"],
                "section": ["2号楼三层"],
                "acceptance_result": ["合格"],
            },
        },
        "note": "真机 4.7 验收表。",
    },
    {
        "id": "C04", "group": "见证",
        "say": "记一条见证取样：钢筋 HRB400 Φ25，取样数量5吨，合格证编号报审-2026-1014。",
        "expect": {
            "tool": "create_work_record",
            "args_required": {
                "record_type": ["见证"],
                "witness_material_name": ["钢筋"],
                "witness_material_spec": ["HRB400", "Φ25"],
                "witness_quantity": ["5"],
            },
        },
        "note": "真机 4.10 材料台账口径。测 witness_* 系列参数不串到 section。",
    },

    # =========================================================================
    # D. 文书生成 / 省统表
    # =========================================================================
    {
        "id": "D01", "group": "文书生成",
        "say": "生成今天的监理日志，天气晴。",
        "expect": {
            "tool": "generate_document",
            "args_required": {"period": ["day", "日"]},
            "tool_forbidden": ["create_work_record"],
        },
        "note": "语料③。🔴 高危混淆：不能理解成「新建一条记录」。",
    },
    {
        "id": "D02", "group": "文书生成",
        "say": "生成今天的监理日志，天气晴，上级质监站上午来检查未发现问题，其他事项无。",
        "expect": {
            "tool": "generate_document",
            "args_required": {"period": ["day", "日"]},
        },
        "gap": True,
        "note": "🔴 产品缺口（不是模型的错）：用户额外说了「天气晴」「上级质监站检查未发现问题」"
                "「其他事项无」这三栏，但 generate_document 的参数只有 "
                "record_id/form_code/period/date —— **没有 extra_fields**，这些信息无处可传。"
                "硬约束又明写「args 只能包含该工具声明的参数」，所以模型丢掉它们是"
                "正确遵守指令。跑分断言 extra_fields 必然失败，单列不计分。"
                "要修的是后端：给 generate_document 加 extra_fields 参数，"
                "让天气/上级检查/其他事项能进聚合文书（语料③明确要求这三栏可补）。",
    },
    {
        "id": "D03", "group": "文书生成",
        "say": "生成本月监理月报",
        "expect": {
            "tool": "generate_document",
            "args_required": {"period": ["month", "月"]},
        },
        "note": "ToolSpec example 基本盘。",
    },
    {
        "id": "D04", "group": "文书生成",
        "say": "把这条记录生成监理日志",
        "history": [{"role": "assistant", "text": "已创建巡视记录（ID 128）：3号楼二层临边防护。"}],
        "expect": {
            "tool": "generate_document",
            "args_required": {"record_id": ["128"]},
        },
        "note": "ToolSpec example + 指代消解。record_id 要从 history 里捞出来。",
    },
    {
        "id": "D05", "group": "省统表文书",
        "say": "建一份监理工程师通知单：要求整改脚手架搭设",
        "expect": {
            "tool": "create_provform_doc",
            "args_required": {"form_code": ["通知单", "gd_b_215"]},
        },
        "note": "ToolSpec example 基本盘。",
    },
    {
        "id": "D06", "group": "省统表文书",
        "say": "生成今天的安全监理日志。",
        "expect": {
            "tool": "generate_document",
            "args_required": {"period": ["day", "日"]},
        },
        "gap": True,
        "note": "🔴 已知缺口1（语料文档记录在案）：安全监理日志没有可用生成入口 —— "
                "generate_document 的聚合分支只认 day/week/month→日志/周报/月报，"
                "create_provform_doc 又被 validate_values 卡住（11_02~11_09 标 system=True "
                "却按必填拒绝，实测8条报错）。这条单列，不计入总分。",
    },

    # =========================================================================
    # E. 隐患
    # =========================================================================
    {
        "id": "E01", "group": "隐患",
        "say": "楼面积水还有碎石，有滑倒风险",
        "expect": {
            "tool": "create_hazard",
            "args_required": {"description": ["积水", "碎石", "滑倒"]},
            "tool_forbidden": ["create_work_record"],
        },
        "note": "ToolSpec example 基本盘。测不被误判成工作记录。",
    },
    {
        "id": "E02", "group": "隐患",
        "say": "5层东侧临边防护栏杆缺失，存在高处坠落风险，要求立即补齐并挂警示标志",
        "expect": {
            "tool": "create_hazard",
            "args_required": {
                "description": ["临边防护", "栏杆缺失"],
                "section": ["5层东侧", "5层"],
            },
        },
        "note": "真机 4.6 安全隐患整改通知的隐患描述。",
    },

    # =========================================================================
    # F. 查询类（read 工具，不该编造写操作）
    # =========================================================================
    {
        "id": "F01", "group": "查询",
        "say": "我今天记了几条",
        "expect": {
            "tool": "query_work_records",
            "tool_forbidden": ["create_work_record"],
        },
        "note": "ToolSpec example。🔴 「记了几条」含「记」字，易被误判成建单。",
    },
    {
        "id": "F02", "group": "查询",
        "say": "3号楼有哪些记录",
        "expect": {
            "tool": "query_work_records",
            "args_required": {"section": ["3号楼"]},
        },
        "note": "ToolSpec example。",
    },
    {
        "id": "F03", "group": "查询",
        "say": "未整改的隐患有哪些",
        "expect": {
            "tool": "query_hazards",
            "args_required": {"status": ["未整改", "整改中"]},
        },
        "note": "ToolSpec example。",
    },
    {
        "id": "F04", "group": "查询",
        "say": "有没有监理月报",
        "expect": {
            "tool": "query_documents",
            "args_required": {"doc_type": ["监理月报", "月报"]},
        },
        "note": "ToolSpec example。🔴 不能误判成 generate_document（「有没有」≠「生成」）。",
    },
    {
        "id": "F05", "group": "查询",
        "say": "模板支撑验收规范怎么要求",
        "expect": {
            "tool": "query_regulation",
            "args_required": {"keyword": ["模板支撑"]},
        },
        "note": "ToolSpec example。",
    },
    {
        "id": "F06", "group": "查询",
        "say": "今天天气怎么样",
        "expect": {"tool": "query_weather"},
        "note": "ToolSpec example。",
    },
    {
        "id": "F07", "group": "查询",
        "say": "查看台账",
        "expect": {"tool": "navigate"},
        "note": "真机 2.6 语音命令。台账不是 11 个工具之一，应走 navigate 打开页面。",
    },

    # =========================================================================
    # G. 导航 / 撤销
    # =========================================================================
    {
        "id": "G01", "group": "导航",
        "say": "打开工作记录",
        "expect": {
            "tool": "navigate",
            "args_required": {"page": ["工作记录"]},
        },
        "note": "ToolSpec example。",
    },
    {
        "id": "G02", "group": "导航",
        "say": "监理文书",
        "expect": {"tool": "navigate"},
        "note": "真机 2.6：光说页面名也该跳转，不该当成知识问答。",
    },
    {
        "id": "G03", "group": "撤销",
        "say": "撤销",
        "expect": {"tool": "undo_last_action"},
        "note": "ToolSpec example。",
    },
    {
        "id": "G04", "group": "撤销",
        "say": "刚才那条删掉，说错了",
        "expect": {"tool": "undo_last_action"},
        "note": "ToolSpec example。",
    },

    # =========================================================================
    # H. 纯问答（tool 必须是 null，不能乱调工具）
    # =========================================================================
    {
        "id": "H01", "group": "纯问答",
        "say": "混凝土浇筑旁站监理的要点有哪些？",
        "expect": {
            "tool": None,
            "tool_forbidden": ["create_work_record", "generate_document"],
        },
        "note": "真机 2.1。🔴 含「旁站」「监理」等业务词，但是问知识不是办事。"
                "误调工具就是典型的「答非所问」。",
    },
    {
        "id": "H02", "group": "纯问答",
        "say": "你好",
        "expect": {"tool": None},
        "note": "打招呼，_AGENT_SYSTEM 明确要求 tool=null。",
    },
    {
        "id": "H03", "group": "纯问答",
        "say": "钢筋锚固长度的基本要求是什么？",
        "expect": {"tool_any_of": ["query_regulation", None]},
        "note": "真机 7.2 资料库问答。这条**本身有歧义**：query_regulation 走 RAG 检索"
                "（答案有出处、可控），null 则凭模型训练知识直答 —— 两条路都能给用户正确答案，"
                "所以用 tool_any_of 接受两者。要强制走检索是**产品决策**（规范类答案必须可溯源），"
                "若定了这个口径就把期望改成 tool=query_regulation。",
    },

    # =========================================================================
    # I. 追问行为（该问就问，不该硬猜）
    # =========================================================================
    {
        "id": "I01", "group": "追问",
        "say": "3号楼二层钢筋绑扎，天气晴，现场情况正常",
        "expect": {
            "expect_ask": True,
            "tool_forbidden": ["create_work_record"],
        },
        "note": "🔴 真机 2.3 语音原话。没说巡视/旁站/验收/见证 → 硬约束要求"
                "「不要猜、先追问类型」。模型硬猜 record_type 就是错。",
    },
    {
        "id": "I02", "group": "追问",
        "say": "2号楼三层模板安装，天气多云，施工人数12人，现场正常",
        "expect": {
            "expect_ask": True,
            "tool_forbidden": ["create_work_record"],
        },
        "note": "真机 2.4 建议按钮原话。同样缺类型 → 期望追问。",
    },
    {
        "id": "I03", "group": "追问",
        "say": "记一条记录：5号楼屋面防水做完了",
        "expect": {
            "expect_ask": True,
            "tool_forbidden": ["create_work_record"],
        },
        "note": "「记一条记录」明示要建单但没给类型 → 期望追问，且一次只问一个问题。",
    },

    # =========================================================================
    # J. 多轮合并（最难的场景，_AGENT_SYSTEM 硬约束第6、7条）
    # =========================================================================
    {
        "id": "J01", "group": "多轮合并",
        "say": "巡视",
        "history": [
            {"role": "user", "text": "3号楼二层钢筋绑扎，天气晴，现场情况正常"},
            {"role": "assistant", "text": "请问这条记录的类型是巡视、旁站、验收还是见证？"},
        ],
        "expect": {
            "tool": "create_work_record",
            "args_required": {
                "record_type": ["巡视"],
                "section": ["3号楼二层"],
                "work_content": ["钢筋绑扎"],
                "weather": ["晴"],
            },
        },
        "note": "🔴 硬约束第7条：必须把历史里已收集的信息与本次补充**合并**成完整 args。"
                "只传 record_type 会导致反复追问 —— 这是用户抱怨「答非所问」的高发点。",
    },
    {
        "id": "J02", "group": "多轮合并",
        "say": "九点到十一点",
        "history": [
            {"role": "user", "text": "记一条巡视：3号楼二层临边防护"},
            {"role": "assistant", "text": "好的，巡视开始和结束时间是几点？"},
        ],
        "expect": {
            "tool": "create_work_record",
            "args_required": {
                "record_type": ["巡视"],
                "section": ["3号楼二层"],
            },
        },
        "note": "补时间。历史里的类型和部位必须带回来，不能只传时间。",
    },
    {
        "id": "J03", "group": "多轮合并",
        "say": "跳过，直接创建",
        "history": [
            {"role": "user", "text": "记一条巡视：3号楼二层临边防护，9点到11点"},
            {"role": "assistant", "text": "巡视发现的问题及处理情况是什么？"},
        ],
        "expect": {
            "tool": "create_work_record",
            "args_required": {
                "record_type": ["巡视"],
                "section": ["3号楼二层"],
            },
        },
        "note": "🔴 硬约束第6条：用户说「跳过/直接创建」时**必须仍调用工具**，"
                "不能只回一句文字。模型回「好的已跳过」= 严重失败。",
    },
    {
        "id": "J04", "group": "多轮合并",
        "say": "符合要求",
        "history": [
            {"role": "user", "text": "记一条巡视：3号楼二层临边防护，9点到11点"},
            {"role": "assistant", "text": "巡视发现的问题及处理情况是什么？"},
        ],
        "expect": {
            "tool": "create_work_record",
            "args_required": {
                "record_type": ["巡视"],
                "section": ["3号楼二层"],
                "problems": ["符合要求"],
            },
        },
        "note": "🔴 problems=符合要求 必须原样传（参数说明里写死了）。"
                "MEMORY.md 记的 _NO_ISSUE_HINTS 服务端兜底就是为这个场景。",
    },
    {
        "id": "J05", "group": "多轮合并",
        "say": "规范里对模板支撑的验收是怎么要求的？",
        "history": [
            {"role": "user", "text": "记一条巡视：3号楼二层临边防护"},
            {"role": "assistant", "text": "巡视开始时间是几点？"},
        ],
        "expect": {
            "tool": "query_regulation",
            "tool_forbidden": ["create_work_record"],
        },
        "note": "🔴 _SUPPLEMENT_SKIP_HINTS 存在的理由：追问过程中用户岔开问规范，"
                "不能被当成「补充字段」塞进草稿。",
    },
    # =========================================================================
    # K. 图片与模糊请求 —— 2026-10-19 真机截图反馈
    #    「看不到图却声称经图片识别」「该办事却回一段建议」两类编造，必须被断言挡住
    # =========================================================================
    {
        "id": "K01", "group": "图片与模糊请求",
        "say": "记录今天工作情况",
        "expect": {
            "tool": None,
            "expect_ask": True,
            "answer_forbidden": ["图片识别", "经图片", "照片显示", "看图片"],
        },
        "note": "类型不明 → 只问一句类型（巡视/旁站/验收/见证）。真机原状：回了"
                "「今日工作情况结论与建议」，还写「经图片识别未发现明显安全隐患」——"
                "这一轮根本没有任何图片素材，属凭空编造。",
    },
    {
        "id": "K02", "group": "图片与模糊请求",
        "say": "先识别水印",
        "expect": {
            "tool": None,
            "expect_ask": True,
            "answer_forbidden": ["无法识别图片", "接收不到图片", "不能识别图片", "无法直接接收"],
        },
        "note": "实际能力：照片随消息发就会先读水印（/media/watermark/recognize 回填拍摄时间/地点）。"
                "应答「把照片发到输入框，我先读水印里的拍摄时间和地点」，不能说做不到。",
    },
    {
        "id": "K03", "group": "图片与模糊请求",
        "say": "记录今天工作情况",
        "history": [
            {"role": "user", "text": "巡视3号楼二层临边防护，上午9点到11点，符合要求，有现场照片"},
        ],
        "expect": {
            "tool": "create_work_record",
            "args_required": {"record_type": ["巡视"], "section": ["3号楼二层"]},
        },
        "note": "同一句话，历史里已有类型和部位 → 必须直接建单。不能因为这句话本身没写类型就退回追问。",
    },
    {
        "id": "K04", "group": "图片与模糊请求",
        "history": [
            {"role": "user", "text": "记录今天的工作"},
            {"role": "assistant", "text": "今天要记的是巡视、旁站、验收还是见证？如果要的是今天的监理日志，"
                                          "直接说『生成今天的监理日志』。"},
        ],
        "say": "巡视",
        "expect": {
            "tool": "create_work_record",
            "args_required": {"record_type": ["巡视"]},
            "args_forbidden": ["section", "location", "work_content",
                               "problems", "treatment", "worker_count",
                               "weather", "inspection_time"],
        },
        "note": "用户只回了类型。真实事故：模型把安全检查分项清单"
                "（临边洞口防护、高处作业、卸料平台、起重吊装、移动操作平台、模板支撑体系）"
                "当部位和内容填进 args，回执糊成一行、记录里全是编造字段。"
                "除 record_type 外必须留空，交给服务端逐栏追问。",
    },
    {
        "id": "K05", "group": "图片与模糊请求",
        "say": "记录今天工作",
        "material": (
            "【图片 1】\n【水印】拍摄时间：2026-10-19 15:42；地点：广州市黄埔区XX路；"
            "部位：3号楼二层东侧；天气：晴\n未识别到明显安全隐患。"
        ),
        "expect": {
            "tool": "create_work_record",
            "args_required": {"record_type": ["巡视"], "section": ["3号楼二层"]},
            "answer_forbidden": ["要记的是巡视、旁站", "还是见证"],
        },
        "note": "🔴 2026-10-19 真机反馈：带水印照片 + 「记录今天工作」，系统什么都没填就反问类型，"
                "用户以为没看图。带素材时必须直接按巡视建单，水印里的部位/时间由服务端填入，"
                "缺的栏目才逐栏问。",
    },
)


# ---------------------------------------------------------------------------
# 统计辅助
# ---------------------------------------------------------------------------

def groups() -> list[str]:
    """按出现顺序返回分组名。"""
    out: list[str] = []
    for c in CASES:
        if c["group"] not in out:
            out.append(c["group"])
    return out


def by_group(name: str) -> list[dict]:
    return [c for c in CASES if c["group"] == name]


def scorable() -> list[dict]:
    """计入总分的用例（排除 gap 已知产品缺口）。"""
    return [c for c in CASES if not c.get("gap")]


def gap_cases() -> list[dict]:
    """单列的产品缺口用例：路由层可能通过，但端到端功能不可用。"""
    return [c for c in CASES if c.get("gap")]


if __name__ == "__main__":
    print(f"共 {len(CASES)} 条用例，其中 gap {len(CASES) - len(scorable())} 条，"
          f"计分 {len(scorable())} 条")
    for g in groups():
        cs = by_group(g)
        print(f"  {g:10} {len(cs):2d} 条  ({', '.join(c['id'] for c in cs)})")
