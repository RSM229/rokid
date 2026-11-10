"""省统表「字段表」驱动的录入与追问口径（自动生成，勿手改）。

来源：监理通各记录关键字（草稿）(1).xlsx /「字段清单」+ 知识库补齐的序号 5、9。
重跑：cd cloud-backend && python scripts/gen_record_field_spec.py

kind 语义：
  auto    —— 表「由系统生成=是」，由系统带出，不进追问（项目信息/编号/日期/星期等）
  ask     —— 表「由系统生成=否」，用户没说就追问补充；签名/盖章类另见 signoff
  signoff —— 签名/签章/盖章，走签章服务，不追问、不入 args
"""
from __future__ import annotations

TABLE_SOURCE = '监理通各记录关键字（草稿）(1).xlsx'

ASK_ANYWAY = ['weather', 'wind']

QUESTION_OVERRIDES = {'section': '这次的工程部位（施工区域）是哪里？例如「3号楼二层临边防护」', 'problems': '现场发现了什么问题？无异常就回复「符合要求」', 'weather': '当天天气如何？可以直接选，或回复「跳过」', 'content': '具体检查/巡视内容是什么？', 'start_time': '开始时间是几点？例如「9:00」', 'end_time': '结束时间是几点？例如「11:30」', 'worker_count': '现场施工人数大约多少？', 'station_process': '旁站的关键工序是什么？例如「混凝土浇筑」', 'hazard_name': '危大工程名称/类型是什么？例如「落地式钢管脚手架工程-一般危大工程」', 'wind': '现场风力如何？可以直接选，或回复「跳过」', 'qa_onsite': '施工单位质检员、安全员、施工员是否到岗？', 'self_check': '施工单位是否已完成自查自检并达到质量标准？', 'acceptance_type': '这是哪类验收？隐蔽工程／检验批／进场验收／分项工程验收／分部工程验收／单位工程验收／竣工验收／预验收（「分项分部」为旧值别名）', 'acceptance_content': '验收要点（验收内容）是什么？'}

ENUM_OPTIONS = {'weather': ['晴', '多云', '阴', '小雨', '大雨', '大风'], 'qa_onsite': ['是', '否'], 'self_check': ['是', '否'], 'conclusion': ['合格', '不合格'], 'acceptance_result': ['合格', '不合格', '待整改'], 'hazard_category': ['深基坑', '高大模板', '起重吊装', '脚手架', '拆除', '暗挖', '其他'], 'pm_onsite': ['是', '否'], 'safety_onsite': ['是', '否'], 'test_result': ['合格', '不合格', '待检'], 'acceptance_result_cailiao': ['合格', '不合格']}

RECORD_TYPE_TO_FORM = {'巡视': 'gd_b_214_xunshi', '旁站': 'gd_b_212_pangzhan', '验收': 'acceptance_record', '见证': 'witness_record', '起重机械安拆检查': 'gdaq_30305_qizhong'}


FORMS: dict[str, dict] = {
    # GD-B-212  旁站记录
    'gd_b_212_pangzhan': {
        "form_number": 'GD-B-212',
        "name": '旁站记录',
        "signoffs": [{'role': '旁站监理人员', 'required': True}, {'role': '施工单位现场质检员', 'required': False}],
        "fields": [
            {'key': '1_01', 'canonical': 'project_name', 'label': '工程项目名称', 'raw_label': '工程项目名称', 'section': '基本信息', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '1_02', 'canonical': 'doc_number', 'label': '编号', 'raw_label': '编号', 'section': '基本信息', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '1_03', 'canonical': 'date', 'label': '日期', 'raw_label': '日期', 'section': '基本信息', 'table_type': '日期', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': 'weather', 'canonical': 'weather', 'label': '天气', 'raw_label': '天气', 'section': '基本信息', 'table_type': '枚举（晴/多云/阴/雨/大风等）', 'auto': True, 'required': False, 'kind': 'ask', 'arg': 'weather'},
            {'key': '1_05', 'canonical': 'section', 'label': '旁站的关键部位', 'raw_label': '旁站的关键部位', 'section': '基本信息', 'table_type': '文本（楼栋/楼层/轴线）', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'station_part'},
            {'key': '1_06', 'canonical': 'station_process', 'label': '旁站的关键工序', 'raw_label': '旁站的关键工序', 'section': '基本信息', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'station_process'},
            {'key': '1_07', 'canonical': 'start_time', 'label': '旁站开始时间（年 月 日 时 分）', 'raw_label': '旁站开始时间（年 月 日 时 分）', 'section': '基本信息', 'table_type': '日期时间', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'station_start_time'},
            {'key': '1_08', 'canonical': 'end_time', 'label': '旁站结束时间（年 月 日 时 分）', 'raw_label': '旁站结束时间（年 月 日 时 分）', 'section': '基本信息', 'table_type': '日期时间', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'station_end_time'},
            {'key': '1_09', 'canonical': 'construction_unit', 'label': '施工单位', 'raw_label': '施工单位', 'section': '基本信息', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '1_10', 'canonical': 'worker_count', 'label': '各专业作业人数情况', 'raw_label': '各专业作业人数情况', 'section': '施工情况', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'worker_count'},
            {'key': '1_11', 'canonical': 'equipment', 'label': '机械设备情况', 'raw_label': '机械设备情况', 'section': '施工情况', 'table_type': '数字', 'auto': False, 'required': False, 'kind': 'ask', 'arg': None},
            {'key': '1_12', 'canonical': 'material_info', 'label': '材料品牌、规格、数量情况', 'raw_label': '材料品牌、规格、数量情况', 'section': '施工情况', 'table_type': '', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '1_13', 'canonical': 'qa_onsite', 'label': '施工单位质检员、安全员、施工员等到岗情况', 'raw_label': '施工单位质检员、安全员、施工员等到岗情况', 'section': '监理情况', 'table_type': '枚举（是/否）', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '1_14', 'canonical': 'self_check', 'label': '施工单位是否完成自查自检并达到质量标准', 'raw_label': '施工单位是否完成自查自检并达到质量标准', 'section': '监理情况', 'table_type': '', 'auto': False, 'required': False, 'kind': 'ask', 'arg': None},
            {'key': '1_15', 'canonical': 'content', 'label': '检查内容及检查结果情况', 'raw_label': '检查内容及检查结果情况', 'section': '监理情况', 'table_type': '', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'work_content'},
            {'key': '1_16', 'canonical': 'problems', 'label': '旁站发现的问题及处理情况', 'raw_label': '旁站发现的问题及处理情况', 'section': '问题与处理', 'table_type': '文本', 'auto': False, 'required': False, 'kind': 'ask', 'arg': 'problems'},
            {'key': None, 'canonical': None, 'label': '旁站监理人员（签名）', 'raw_label': '旁站监理人员（签名）', 'section': '签章', 'table_type': '文本（签章控件）', 'kind': 'signoff', 'auto': False, 'required': True, 'arg': None},
            {'key': None, 'canonical': None, 'label': '施工单位现场质检员（签名确认）', 'raw_label': '施工单位现场质检员（签名确认）', 'section': '签章', 'table_type': '文本（签章控件）', 'kind': 'signoff', 'auto': False, 'required': False, 'arg': None},
        ],
    },
    # GD-B-213  平行检查记录
    'gd_b_213_pingxing': {
        "form_number": 'GD-B-213',
        "name": '平行检查记录',
        "signoffs": [{'role': '检查人员', 'required': True}, {'role': '复核人员', 'required': False}],
        "fields": [
            {'key': '2_01', 'canonical': 'project_name', 'label': '工程项目名称', 'raw_label': '工程项目名称', 'section': '签章', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '2_02', 'canonical': 'doc_number', 'label': '编号', 'raw_label': '编号', 'section': '签章', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '2_03', 'canonical': 'time', 'label': '检查时间（年 月 日）', 'raw_label': '检查时间（年 月 日）', 'section': '签章', 'table_type': '日期', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '2_04', 'canonical': 'section', 'label': '检查部位', 'raw_label': '检查部位', 'section': '签章', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'section'},
            {'key': '2_05', 'canonical': 'acceptance_content', 'label': '检查项目', 'raw_label': '检查项目', 'section': '签章', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '2_06', 'canonical': 'basis', 'label': '检查依据（图纸/规范编号）', 'raw_label': '检查依据（图纸/规范编号）', 'section': '签章', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': 'method', 'canonical': 'method', 'label': '检查方法', 'raw_label': '检查方法', 'section': '签章', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '2_08', 'canonical': 'instrument', 'label': '使用仪器设备名称及编号', 'raw_label': '使用仪器设备名称及编号', 'section': '签章', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '2_09', 'canonical': 'measure_data', 'label': '实测数据明细', 'raw_label': '实测数据明细', 'section': '签章', 'table_type': '文本（数值列表）', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '2_10', 'canonical': 'record', 'label': '检查记录', 'raw_label': '检查记录', 'section': '签章', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '2_11', 'canonical': 'conclusion', 'label': '检查结论', 'raw_label': '检查结论', 'section': '签章', 'table_type': '枚举（合格/不合格）', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '2_12', 'canonical': 'handling', 'label': '处理记录（不合格项整改要求）', 'raw_label': '处理记录（不合格项整改要求）', 'section': '签章', 'table_type': '文本', 'auto': False, 'required': False, 'kind': 'ask', 'arg': None},
            {'key': None, 'canonical': None, 'label': '检查人员（专业监理工程师签名）', 'raw_label': '检查人员（专业监理工程师签名）', 'section': '签章', 'table_type': '文本（签章控件）', 'kind': 'signoff', 'auto': False, 'required': True, 'arg': None},
            {'key': None, 'canonical': None, 'label': '复核人员（签名）', 'raw_label': '复核人员（签名）', 'section': '签章', 'table_type': '文本（签章控件）', 'kind': 'signoff', 'auto': False, 'required': False, 'arg': None},
        ],
    },
    # GD-B-214  巡视记录
    'gd_b_214_xunshi': {
        "form_number": 'GD-B-214',
        "name": '巡视记录',
        "signoffs": [{'role': '巡视监理人员', 'required': True}],
        "fields": [
            {'key': '3_01', 'canonical': 'project_name', 'label': '工程项目名称', 'raw_label': '工程项目名称', 'section': '基本信息', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '3_02', 'canonical': 'doc_number', 'label': '编号', 'raw_label': '编号', 'section': '基本信息', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '3_03', 'canonical': 'section', 'label': '巡视的工程部位', 'raw_label': '巡视的工程部位', 'section': '基本信息', 'table_type': '文本（精准到楼栋/楼层/区域）', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'section'},
            {'key': '3_04', 'canonical': 'construction_unit', 'label': '施工单位', 'raw_label': '施工单位', 'section': '基本信息', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '3_05', 'canonical': 'start_time', 'label': '巡视开始时间（年 月 日 时）', 'raw_label': '巡视开始时间（年 月 日 时）', 'section': '基本信息', 'table_type': '日期时间', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'inspection_start_time'},
            {'key': '3_06', 'canonical': 'end_time', 'label': '巡视结束时间（年 月 日 时）', 'raw_label': '巡视结束时间（年 月 日 时）', 'section': '基本信息', 'table_type': '日期时间', 'auto': False, 'required': False, 'kind': 'ask', 'arg': 'inspection_end_time'},
            {'key': '3_07', 'canonical': 'content', 'label': '巡视内容：\n（1.施工单位是否按工程设计文件、工程建设标准和批准的施工组织设计、（专项）施工方案施工\n2.使用的工程材料、构配件和设备是否合格\n3.施工现场管理人员，特别是施工质量管理人员是否到位\n4.特种作业人员是否持证上岗\n5.安全文明施工情况\n6.其他', 'raw_label': '巡视内容：（1.施工单位是否按工程设计文件、工程建设标准和批准的施工组织设计、（专项）施工方案施工 2.使用的工程材料、构配件和设备是否合格 3.施工现场管理人员，特别是施工质量管理人员是否到位 4.特种作业人员是否持证上岗 5.安全文明施工情况 6.其他）', 'section': '巡视内容', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '3_09', 'canonical': 'problems', 'label': '巡视的问题及处理情况', 'raw_label': '巡视的问题及处理情况', 'section': '问题与处理', 'table_type': '', 'auto': False, 'required': False, 'kind': 'ask', 'arg': 'problems'},
            {'key': None, 'canonical': None, 'label': '巡视监理人员（签名）', 'raw_label': '巡视监理人员（签名）', 'section': '签章', 'table_type': '文本（签章控件）', 'kind': 'signoff', 'auto': False, 'required': True, 'arg': None},
        ],
    },
    # GD-B-215  监理通知单
    'gd_b_215_tongzhi': {
        "form_number": 'GD-B-215',
        "name": '监理通知单',
        "signoffs": [{'role': '总监理工程师/专业监理工程师', 'required': True}],
        "fields": [
            {'key': '4_01', 'canonical': 'project_name', 'label': '工程项目名称', 'raw_label': '工程项目名称', 'section': '基本信息', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '4_02', 'canonical': 'doc_number', 'label': '编号', 'raw_label': '编号', 'section': '基本信息', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '4_03', 'canonical': 'date', 'label': '日期', 'raw_label': '日期', 'section': '基本信息', 'table_type': '', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '4_04', 'canonical': 'cc_unit', 'label': '抄送单位（默认建设单位）', 'raw_label': '抄送单位（默认建设单位）', 'section': '基本信息', 'table_type': '文本', 'auto': True, 'required': False, 'kind': 'auto', 'arg': None},
            {'key': 'to_unit', 'canonical': 'to_unit', 'label': '致（施工单位）', 'raw_label': '致（施工单位）', 'section': '基本信息', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '4_06', 'canonical': 'subject', 'label': '事由（关于XXXX问题）', 'raw_label': '事由（关于XXXX问题）', 'section': '内容', 'table_type': '文本（一句话概括问题性质）', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '4_07', 'canonical': 'section', 'label': '问题区域', 'raw_label': '问题区域', 'section': '内容', 'table_type': '文本（精准到楼栋/楼层/轴线）', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'section'},
            {'key': '4_08', 'canonical': 'problems', 'label': '存在问题描述', 'raw_label': '存在问题描述', 'section': '内容', 'table_type': '文本（发现时间、具体描述）', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'problems'},
            {'key': '4_09', 'canonical': 'violation', 'label': '违反的规范/图纸/合同条款', 'raw_label': '违反的规范/图纸/合同条款', 'section': '内容', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': 'requirement', 'canonical': 'requirement', 'label': '整改要求', 'raw_label': '整改要求', 'section': '内容', 'table_type': '文本（具体、可量化）', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '4_11', 'canonical': 'deadline', 'label': '整改完成时限', 'raw_label': '整改完成时限', 'section': '内容', 'table_type': '日期', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '4_12', 'canonical': 'attachment', 'label': '附件（问题照片编号/检测报告）', 'raw_label': '附件（问题照片编号/检测报告）', 'section': '内容', 'table_type': '文本', 'auto': False, 'required': False, 'kind': 'ask', 'arg': None},
            {'key': '4_13', 'canonical': 'supervision_org', 'label': '项目监理机构（项目章）', 'raw_label': '项目监理机构（项目章）', 'section': '签章', 'table_type': '文本（签章控件）', 'auto': True, 'required': True, 'kind': 'signoff', 'arg': None},
            {'key': None, 'canonical': None, 'label': '总监理工程师（代表）/专业监理工程师（签名）', 'raw_label': '总监理工程师（代表）/专业监理工程师（签名）', 'section': '签章', 'table_type': '文本（签章控件）', 'kind': 'signoff', 'auto': False, 'required': True, 'arg': None},
        ],
    },
    # YS  验收记录
    'acceptance_record': {
        "form_number": 'YS',
        "name": '验收记录',
        "signoffs": [{'role': '监理验收人', 'required': True}, {'role': '施工单位质检员', 'required': True}],
        "fields": [
            {'key': '6_01', 'canonical': 'project_name', 'label': '工程项目名称', 'raw_label': '工程项目名称', 'section': '基本信息', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '6_02', 'canonical': 'doc_number', 'label': '编号', 'raw_label': '编号', 'section': '基本信息', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': 'date', 'canonical': 'date', 'label': '验收日期', 'raw_label': '验收日期', 'section': '基本信息', 'table_type': '', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': 'acceptance_type', 'canonical': 'acceptance_type', 'label': '验收类型', 'raw_label': '验收类型', 'section': '内容', 'table_type': '枚举（隐蔽工程/检验批/进场验收/分项工程验收/分部工程验收/单位工程验收/竣工验收/预验收/分项分部）', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '6_05', 'canonical': 'section', 'label': '验收部位、工序、材料、设备进场', 'raw_label': '验收部位、工序、材料、设备进场', 'section': '内容', 'table_type': '文本（精准到楼栋/楼层/轴线）', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'section'},
            {'key': '6_06', 'canonical': 'acceptance_content', 'label': '验收内容（验收要点）', 'raw_label': '验收内容（验收要点）', 'section': '内容', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '6_07', 'canonical': 'self_check_conclusion', 'label': '施工单位自检结论', 'raw_label': '施工单位自检结论', 'section': '内容', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '6_08', 'canonical': 'supervision_conclusion', 'label': '监理验收结论', 'raw_label': '监理验收结论', 'section': '内容', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '6_09', 'canonical': 'photo', 'label': '举牌验收照片', 'raw_label': '举牌验收照片', 'section': '内容', 'table_type': '文本（广东统标要求）', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '6_10', 'canonical': 'acceptance_participants', 'label': '验收单位及人员', 'raw_label': '验收单位及人员', 'section': '签章', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': None, 'canonical': None, 'label': '监理验收人（签名）', 'raw_label': '监理验收人（签名）', 'section': '签章', 'table_type': '文本（签章控件）', 'kind': 'signoff', 'auto': False, 'required': True, 'arg': None},
            {'key': None, 'canonical': None, 'label': '施工单位质检员（签名）', 'raw_label': '施工单位质检员（签名）', 'section': '签章', 'table_type': '文本（签章控件）', 'kind': 'signoff', 'auto': False, 'required': True, 'arg': None},
        ],
    },
    # GD-B-220  监理日志
    'gd_b_220_rizhi': {
        "form_number": 'GD-B-220',
        "name": '监理日志',
        "signoffs": [{'role': '当日值班监理人员', 'required': True}, {'role': '专业监理工程师', 'required': False}, {'role': '总监理工程师/总监理工程师代表', 'required': False}],
        "fields": [
            {'key': '7_01', 'canonical': 'project_name', 'label': '工程项目名称', 'raw_label': '工程项目名称', 'section': '基本信息', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '7_02', 'canonical': 'doc_number', 'label': '编号', 'raw_label': '编号', 'section': '基本信息', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': 'date', 'canonical': 'date', 'label': '日期', 'raw_label': '日期', 'section': '基本信息', 'table_type': '日期', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '7_04', 'canonical': 'weekday', 'label': '星期', 'raw_label': '星期', 'section': '基本信息', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': 'weather', 'canonical': 'weather', 'label': '天气', 'raw_label': '天气', 'section': '基本信息', 'table_type': '枚举（晴/多云/阴/雨/雪/大风等）', 'auto': True, 'required': True, 'kind': 'ask', 'arg': 'weather'},
            {'key': 'temp_high', 'canonical': 'temperature_high', 'label': '最高气温（℃）', 'raw_label': '最高气温（℃）', 'section': '基本信息', 'table_type': '数字', 'auto': True, 'required': False, 'kind': 'auto', 'arg': None},
            {'key': 'temp_low', 'canonical': 'temperature_low', 'label': '最低气温（℃）', 'raw_label': '最低气温（℃）', 'section': '基本信息', 'table_type': '数字', 'auto': True, 'required': False, 'kind': 'auto', 'arg': None},
            {'key': '7_14', 'canonical': 'review_date', 'label': '审阅日期', 'raw_label': '审阅日期', 'section': '基本信息', 'table_type': '日期', 'auto': True, 'required': False, 'kind': 'auto', 'arg': None},
            {'key': '7_08', 'canonical': 'construction_progress', 'label': '当日各部位施工进展', 'raw_label': '当日各部位施工进展', 'section': '基本信息', 'table_type': '文本（由每日工作内容生成，可修改）', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '7_09', 'canonical': 'material_section', 'label': '当日材料/构配件/设备进场情况', 'raw_label': '当日材料/构配件/设备进场情况', 'section': '基本信息', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '7_10', 'canonical': 'supervision_section', 'label': '当日监理工作情况（旁站/巡视/平行检验/验收）', 'raw_label': '当日监理工作情况（旁站/巡视/平行检验/验收）', 'section': '基本信息', 'table_type': '文本（由每日工作内容生成，可修改）', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '7_11', 'canonical': 'problems_section', 'label': '当日存在问题及处理情况', 'raw_label': '当日存在问题及处理情况', 'section': '基本信息', 'table_type': '文本（由每日问题记录生成，可修改）', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '7_12', 'canonical': 'supervision_check', 'label': '上级/质监站检查情况', 'raw_label': '上级/质监站检查情况', 'section': '基本信息', 'table_type': '文本', 'auto': False, 'required': False, 'kind': 'ask', 'arg': None},
            {'key': '7_13', 'canonical': 'other_section', 'label': '其他有关事项（会议/发文/停水停电/变更洽商）', 'raw_label': '其他有关事项', 'section': '基本信息', 'table_type': '文本', 'auto': False, 'required': False, 'kind': 'ask', 'arg': None},
            {'key': 'duty_supervisor', 'canonical': 'duty_supervisor', 'label': '当日值班监理人员（签名）', 'raw_label': '当日值班监理人员（签名）', 'section': '基本信息', 'table_type': '文本（签章控件）', 'auto': False, 'required': True, 'kind': 'signoff', 'arg': None},
            {'key': 'professional_supervisor', 'canonical': 'professional_supervisor', 'label': '专业监理工程师（审阅签名）', 'raw_label': '专业监理工程师（审阅签名）', 'section': '基本信息', 'table_type': '文本（签章控件）', 'auto': False, 'required': False, 'kind': 'signoff', 'arg': None},
            {'key': 'chief_supervisor', 'canonical': 'chief_supervisor', 'label': '总监理工程师/代表（审阅签名）', 'raw_label': '总监理工程师/代表（审阅签名）', 'section': '基本信息', 'table_type': '文本（签章控件）', 'auto': False, 'required': False, 'kind': 'signoff', 'arg': None},
        ],
    },
    # GD-B-221  监理月报
    'gd_b_221_yuebao': {
        "form_number": 'GD-B-221',
        "name": '监理月报',
        "signoffs": [{'role': '总监理工程师', 'required': True}],
        "fields": [
            {'key': '8_01', 'canonical': 'project_name', 'label': '工程项目名称', 'raw_label': '工程项目名称', 'section': '基本信息', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '8_02', 'canonical': 'doc_number', 'label': '编号', 'raw_label': '编号', 'section': '基本信息', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '8_03', 'canonical': 'report_period', 'label': '报告期（年 月）', 'raw_label': '报告期（年 月）', 'section': '基本信息', 'table_type': '年月', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '8_04', 'canonical': 'project_overview', 'label': '工程概况及本月形象进度', 'raw_label': '工程概况及本月形象进度', 'section': '基本信息', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '8_05', 'canonical': 'progress_plan', 'label': '进度控制：本月计划vs实际完成', 'raw_label': '进度控制：本月计划vs实际完成', 'section': '基本信息', 'table_type': '文本（含完成百分比）', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '8_06', 'canonical': 'progress_deviation', 'label': '进度偏差分析及下月计划', 'raw_label': '进度偏差分析及下月计划', 'section': '基本信息', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '8_07', 'canonical': 'quality_material', 'label': '质量控制：材料进场及报验汇总', 'raw_label': '质量控制：材料进场及报验汇总', 'section': '基本信息', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '8_08', 'canonical': 'quality_acceptance', 'label': '质量控制：检验批/分项/分部验收情况', 'raw_label': '质量控制：检验批/分项/分部验收情况', 'section': '基本信息', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '8_09', 'canonical': 'quality_stats', 'label': '质量控制：旁站/巡视/平行检验次数统计', 'raw_label': '质量控制：旁站/巡视/平行检验次数统计', 'section': '基本信息', 'table_type': '数字（次数）', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '8_10', 'canonical': 'quality_issues', 'label': '质量控制：质量问题及整改闭环情况', 'raw_label': '质量控制：质量问题及整改闭环情况', 'section': '基本信息', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '8_11', 'canonical': 'cost_payment', 'label': '造价控制：本月工程款支付情况', 'raw_label': '造价控制：本月工程款支付情况', 'section': '基本信息', 'table_type': '文本', 'auto': False, 'required': False, 'kind': 'ask', 'arg': None},
            {'key': '8_12', 'canonical': 'cost_change', 'label': '造价控制：工程变更及费用增减', 'raw_label': '造价控制：工程变更及费用增减', 'section': '基本信息', 'table_type': '文本', 'auto': False, 'required': False, 'kind': 'ask', 'arg': None},
            {'key': '8_13', 'canonical': 'safety_stats', 'label': '安全管理：安全检查及隐患整改统计', 'raw_label': '安全管理：安全检查及隐患整改统计', 'section': '基本信息', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '8_14', 'canonical': 'safety_critical', 'label': '安全管理：危大工程管控情况', 'raw_label': '安全管理：危大工程管控情况', 'section': '基本信息', 'table_type': '文本', 'auto': False, 'required': False, 'kind': 'ask', 'arg': None},
            {'key': '8_15', 'canonical': 'contract_mgmt', 'label': '合同管理：变更/索赔/延期/争议处理', 'raw_label': '合同管理：变更/索赔/延期/争议处理', 'section': '基本信息', 'table_type': '文本', 'auto': False, 'required': False, 'kind': 'ask', 'arg': None},
            {'key': '8_16', 'canonical': 'work_stats', 'label': '监理工作统计汇总（通知单/联系单/会议次数）', 'raw_label': '监理工作统计汇总（通知单/联系单/会议次数）', 'section': '基本信息', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '8_17', 'canonical': 'next_focus', 'label': '下月监理工作重点', 'raw_label': '下月监理工作重点', 'section': '基本信息', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': 'chief_supervisor', 'canonical': 'chief_supervisor', 'label': '总监理工程师（签名）', 'raw_label': '总监理工程师（签名）', 'section': '基本信息', 'table_type': '文本（签章控件）', 'auto': False, 'required': True, 'kind': 'signoff', 'arg': None},
            {'key': '8_18', 'canonical': 'supervision_org', 'label': '监理单位（盖章）', 'raw_label': '监理单位（盖章）', 'section': '基本信息', 'table_type': '文本（签章控件）', 'auto': True, 'required': True, 'kind': 'signoff', 'arg': None},
            {'key': '8_19', 'canonical': 'submit_date', 'label': '报送日期', 'raw_label': '报送日期', 'section': '基本信息', 'table_type': '日期', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
        ],
    },
    # GDAQ30306  危大工程巡视记录
    'gdaq_30306_weida': {
        "form_number": 'GDAQ30306',
        "name": '危大工程巡视记录',
        "signoffs": [{'role': '施工单位专职安全员', 'required': True}, {'role': '专业分包单位专职安全员', 'required': False}, {'role': '监理检查人员', 'required': True}, {'role': '总监理工程师', 'required': False}],
        "fields": [
            {'key': '10_01', 'canonical': 'project_name', 'label': '工程项目名称', 'raw_label': '工程项目名称', 'section': '基本信息', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '10_02', 'canonical': 'doc_number', 'label': '编号', 'raw_label': '编号', 'section': '基本信息', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '10_03', 'canonical': 'date', 'label': '作业日期', 'raw_label': '作业日期', 'section': '基本信息', 'table_type': '日期', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '10_04', 'canonical': 'weather', 'label': '天气', 'raw_label': '天气', 'section': '基本信息', 'table_type': '', 'auto': True, 'required': True, 'kind': 'ask', 'arg': 'weather'},
            {'key': 'construct_unit', 'canonical': 'construction_unit', 'label': '施工单位', 'raw_label': '施工单位', 'section': '基本信息', 'table_type': '', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '10_08', 'canonical': 'subcontractor', 'label': '专业分包单位', 'raw_label': '专业分包单位', 'section': '基本信息', 'table_type': '系统无则无', 'auto': True, 'required': False, 'kind': 'auto', 'arg': None},
            {'key': '10_06', 'canonical': 'section', 'label': '危大工程部位', 'raw_label': '巡视的工程部位', 'section': '基本信息', 'table_type': '文本（精准到楼栋/楼层/区域）', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'section'},
            {'key': '10_09', 'canonical': 'hazard_name', 'label': '危大工程名称/类型/内容', 'raw_label': '危大工程名称/类型', 'section': '基本信息', 'table_type': '枚举（深基坑/高大模板/起重吊装/脚手架/拆除/暗挖/其他）', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'hazard_type'},
            {'key': '10_11', 'canonical': 'start_time', 'label': '巡视开始时间（年 月 日 时）', 'raw_label': '巡视开始时间（年 月 日 时）', 'section': '基本信息', 'table_type': '日期时间', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'inspection_start_time'},
            {'key': '10_12', 'canonical': 'end_time', 'label': '巡视结束时间（年 月 日 时）', 'raw_label': '巡视结束时间（年 月 日 时）', 'section': '基本信息', 'table_type': '日期时间', 'auto': False, 'required': False, 'kind': 'ask', 'arg': 'inspection_end_time'},
            {'key': '10_13', 'canonical': 'content', 'label': '巡视内容', 'raw_label': '巡视内容（按危大类型带出检查表）', 'section': '基本信息', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'work_content'},
            {'key': '10_14', 'canonical': 'pm_onsite', 'label': '施工单位项目负责人到位情况', 'raw_label': '施工单位项目负责人到位情况', 'section': '基本信息', 'table_type': '枚举（是/否）', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '10_15', 'canonical': 'safety_onsite', 'label': '专职安全生产管理人员到位情况', 'raw_label': '专职安全生产管理人员到位情况', 'section': '基本信息', 'table_type': '枚举（是/否）', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '10_16', 'canonical': 'special_cert', 'label': '特种作业人员持证情况', 'raw_label': '特种作业人员持证情况', 'section': '基本信息', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '10_17', 'canonical': 'monitoring', 'label': '监测数据记录（基坑位移/沉降等）', 'raw_label': '监测数据记录（基坑位移/沉降等）', 'section': '基本信息', 'table_type': '文本', 'auto': False, 'required': False, 'kind': 'ask', 'arg': None},
            {'key': '10_18', 'canonical': 'problems', 'label': '巡视发现的问题及处理情况', 'raw_label': '巡视发现的问题及处理情况', 'section': '基本信息', 'table_type': '文本', 'auto': False, 'required': False, 'kind': 'ask', 'arg': 'problems'},
            {'key': '10_19', 'canonical': 'rectify', 'label': '隐患整改及复查闭环情况', 'raw_label': '隐患整改及复查闭环情况', 'section': '基本信息', 'table_type': '文本', 'auto': False, 'required': False, 'kind': 'ask', 'arg': None},
            {'key': None, 'canonical': None, 'label': '巡视监理人员（签名）', 'raw_label': '巡视监理人员（签名）', 'section': '基本信息', 'table_type': '文本（签章控件）', 'kind': 'signoff', 'auto': False, 'required': True, 'arg': None},
            {'key': None, 'canonical': None, 'label': '总监签名', 'raw_label': '总监签名', 'section': '基本信息', 'table_type': '', 'kind': 'signoff', 'auto': False, 'required': False, 'arg': None},
            {'key': '10_05', 'canonical': 'wind', 'label': '风力', 'raw_label': '风力', 'section': '基本信息', 'table_type': '', 'auto': True, 'required': False, 'kind': 'ask', 'arg': 'wind'},
            {'key': '10_10', 'canonical': 'hazard_category', 'label': '危大工程类别', 'raw_label': '危大工程类别', 'section': '基本信息', 'table_type': '', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '10_20', 'canonical': 'review_date', 'label': '审阅时间', 'raw_label': '审阅时间', 'section': '签章', 'table_type': '', 'auto': False, 'required': False, 'kind': 'auto', 'arg': None},
        ],
    },
    # GDAQ30308  安全隐患整改通知
    'gdaq_30308_yinhuan': {
        "form_number": 'GDAQ30308',
        "name": '安全隐患整改通知',
        "signoffs": [{'role': '总监理工程师/总监代表', 'required': True}, {'role': '安全负责人签收', 'required': True}],
        "fields": [
            {'key': '5_01', 'canonical': 'project_name', 'label': '工程项目名称', 'raw_label': '工程项目名称', 'section': '基本信息', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '5_02', 'canonical': 'doc_number', 'label': '编号', 'raw_label': '编号', 'section': '基本信息', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '5_03', 'canonical': 'date', 'label': '日期', 'raw_label': '日期', 'section': '基本信息', 'table_type': '日期', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '5_04', 'canonical': 'to_unit', 'label': '致（施工单位）', 'raw_label': '致（施工单位）', 'section': '基本信息', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '5_05', 'canonical': 'cc_unit', 'label': '抄送单位（默认监督部门）', 'raw_label': '抄送单位（默认监督部门）', 'section': '基本信息', 'table_type': '文本', 'auto': True, 'required': False, 'kind': 'auto', 'arg': None},
            {'key': '5_06', 'canonical': 'subject', 'label': '事由（检查类型/背景）', 'raw_label': '事由（检查类型/背景）', 'section': '内容', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': 'part', 'canonical': 'section', 'label': '隐患部位', 'raw_label': '隐患部位', 'section': '内容', 'table_type': '文本（精准到楼栋/楼层/轴线）', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'section'},
            {'key': '5_08', 'canonical': 'problems', 'label': '隐患内容描述', 'raw_label': '隐患内容描述', 'section': '内容', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'problems'},
            {'key': 'requirement', 'canonical': 'requirement', 'label': '整改要求', 'raw_label': '整改要求', 'section': '内容', 'table_type': '文本（具体、可量化）', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '5_10', 'canonical': 'deadline', 'label': '整改完成时限', 'raw_label': '整改完成时限', 'section': '内容', 'table_type': '日期', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '5_13', 'canonical': 'attachment', 'label': '附件（隐患照片编号）', 'raw_label': '附件（隐患照片编号）', 'section': '内容', 'table_type': '文本', 'auto': False, 'required': False, 'kind': 'ask', 'arg': None},
            {'key': '5_14', 'canonical': 'supervision_org', 'label': '项目监理机构（项目章）', 'raw_label': '项目监理机构（项目章）', 'section': '签章', 'table_type': '文本（签章控件）', 'auto': True, 'required': True, 'kind': 'signoff', 'arg': None},
        ],
    },
    # GD-B-219  工程材料/构配件/设备管理台账
    'gd_b_219_cailiao': {
        "form_number": 'GD-B-219',
        "name": '工程材料/构配件/设备管理台账',
        "signoffs": [{'role': '取样见证人签名', 'required': False}, {'role': '验收人签名', 'required': True}],
        "fields": [
            {'key': '9_01', 'canonical': 'project_name', 'label': '工程项目名称', 'raw_label': '工程项目名称', 'section': '基本信息', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '9_02', 'canonical': 'serial_no', 'label': '序号', 'raw_label': '序号', 'section': '基本信息', 'table_type': '序号', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': '9_03', 'canonical': 'material_name', 'label': '名称', 'raw_label': '名称', 'section': '基本信息', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'witness_material_name'},
            {'key': 'spec', 'canonical': 'material_spec', 'label': '规格/型号', 'raw_label': '规格/型号', 'section': '基本信息', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'witness_material_spec'},
            {'key': 'quantity', 'canonical': 'quantity', 'label': '数量', 'raw_label': '数量', 'section': '基本信息', 'table_type': '数字', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'witness_quantity'},
            {'key': '9_06', 'canonical': 'manufacturer', 'label': '生产厂家或品牌', 'raw_label': '生产厂家或品牌', 'section': '基本信息', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': 'date', 'canonical': 'date', 'label': '进场日期', 'raw_label': '进场日期', 'section': '进场', 'table_type': '日期', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'record_date'},
            {'key': '9_08', 'canonical': 'use_part', 'label': '使用部位', 'raw_label': '使用部位', 'section': '进场', 'table_type': '文本', 'auto': False, 'required': False, 'kind': 'ask', 'arg': None},
            {'key': '9_09', 'canonical': 'sample_date', 'label': '取样日期', 'raw_label': '取样日期', 'section': '见证取样与检测', 'table_type': '日期', 'auto': False, 'required': False, 'kind': 'ask', 'arg': None},
            {'key': '9_10', 'canonical': 'test_result', 'label': '检测结果', 'raw_label': '检测结果', 'section': '见证取样与检测', 'table_type': '枚举', 'auto': False, 'required': True, 'kind': 'ask', 'arg': None},
            {'key': '9_11', 'canonical': 'acceptance_result', 'label': '进场验收结论', 'raw_label': '进场验收结论', 'section': '验收', 'table_type': '枚举', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'acceptance_result'},
            {'key': '9_12', 'canonical': 'acceptance_date', 'label': '验收日期', 'raw_label': '验收日期', 'section': '验收', 'table_type': '日期', 'auto': False, 'required': False, 'kind': 'ask', 'arg': None},
        ],
    },
    # JL-JZ  见证取样记录
    'witness_record': {
        "form_number": 'JL-JZ',
        "name": '见证取样记录',
        "signoffs": [{'role': '见证取样人签名', 'required': True}, {'role': '施工单位现场人员签名', 'required': False}],
        "fields": [
            {'key': 'project_name', 'canonical': 'project_name', 'label': '工程项目名称', 'raw_label': '工程项目名称', 'section': '基本信息', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': 'date', 'canonical': 'date', 'label': '见证日期', 'raw_label': '见证日期', 'section': '基本信息', 'table_type': '日期', 'auto': True, 'required': True, 'kind': 'auto', 'arg': 'record_date'},
            {'key': 'construction_unit', 'canonical': 'construction_unit', 'label': '施工单位', 'raw_label': '施工单位', 'section': '基本信息', 'table_type': '文本', 'auto': True, 'required': True, 'kind': 'auto', 'arg': None},
            {'key': 'material_name', 'canonical': 'material_name', 'label': '材料名称', 'raw_label': '材料名称', 'section': '取样信息', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'witness_material_name'},
            {'key': 'material_spec', 'canonical': 'material_spec', 'label': '规格/型号', 'raw_label': '规格/型号', 'section': '取样信息', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'witness_material_spec'},
            {'key': 'manufacturer', 'canonical': 'manufacturer', 'label': '生产厂家/品牌', 'raw_label': '生产厂家/品牌', 'section': '取样信息', 'table_type': '文本', 'auto': False, 'required': False, 'kind': 'ask', 'arg': None},
            {'key': 'batch_no', 'canonical': 'batch_no', 'label': '批号/炉号', 'raw_label': '批号/炉号', 'section': '取样信息', 'table_type': '文本', 'auto': False, 'required': False, 'kind': 'ask', 'arg': None},
            {'key': 'quantity', 'canonical': 'quantity', 'label': '取样数量', 'raw_label': '取样数量', 'section': '取样信息', 'table_type': '数字', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'witness_quantity'},
            {'key': 'unit', 'canonical': 'unit', 'label': '计量单位', 'raw_label': '计量单位', 'section': '取样信息', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'witness_unit'},
            {'key': 'section', 'canonical': 'section', 'label': '使用部位/取样部位', 'raw_label': '使用部位/取样部位', 'section': '取样信息', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'section'},
            {'key': 'sample_date', 'canonical': 'sample_date', 'label': '取样日期', 'raw_label': '取样日期', 'section': '取样与送检', 'table_type': '日期', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'record_date'},
            {'key': 'sampler', 'canonical': 'sampler', 'label': '取样人', 'raw_label': '取样人', 'section': '取样与送检', 'table_type': '文本', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'inspector'},
            {'key': 'cert_no', 'canonical': 'cert_no', 'label': '合格证/报告编号', 'raw_label': '合格证/报告编号', 'section': '取样与送检', 'table_type': '文本', 'auto': False, 'required': False, 'kind': 'ask', 'arg': 'witness_cert_no'},
            {'key': 'test_result', 'canonical': 'test_result', 'label': '检测结果', 'raw_label': '检测结果', 'section': '检测结果', 'table_type': '枚举', 'auto': False, 'required': False, 'kind': 'ask', 'arg': None},
            {'key': 'acceptance_result', 'canonical': 'acceptance_result', 'label': '见证结论', 'raw_label': '见证结论', 'section': '检测结果', 'table_type': '枚举', 'auto': False, 'required': True, 'kind': 'ask', 'arg': 'acceptance_result'},
            {'key': 'remark', 'canonical': 'remark', 'label': '备注', 'raw_label': '备注', 'section': '其他', 'table_type': '文本', 'auto': False, 'required': False, 'kind': 'ask', 'arg': None},
        ],
    },
}



# ---------------------------------------------------------------------------
# 取数辅助
# ---------------------------------------------------------------------------

def fields_of(form_code: str) -> list[dict]:
    return (FORMS.get(form_code) or {}).get("fields", [])


def ask_fields(form_code: str) -> list[dict]:
    """需要追问的字段，按表栏目顺序（用户没说就补）。签名/盖章类已排除。"""
    return [f for f in fields_of(form_code) if f.get("kind") == "ask"]


def auto_fields(form_code: str) -> list[dict]:
    """由系统带出的字段（表「由系统生成=是」）。"""
    return [f for f in fields_of(form_code) if f.get("kind") == "auto"]


def signoff_fields(form_code: str) -> list[dict]:
    return [f for f in fields_of(form_code) if f.get("kind") == "signoff"]


def required_ask_fields(form_code: str) -> list[dict]:
    return [f for f in ask_fields(form_code) if f.get("required")]


def question_for(field: dict) -> str:
    canon = field.get("canonical") or ""
    if canon in QUESTION_OVERRIDES:
        return QUESTION_OVERRIDES[canon]
    return "还差一项：" + str(field.get("label") or canon) + "（可直接填写，也可说「跳过」）。"


def options_for(field: dict) -> list[str]:
    return ENUM_OPTIONS.get(field.get("canonical") or "", [])


def form_for_record_type(
    record_type: str,
    is_weida: bool = False,
    project=None,
) -> str | None:
    """记录类型 -> 缺省表式 code（按工程类型路由）。

    ``project`` 为市政项目时返回对应市政表式（危大 → AQSZ-J-2.5 通用表；
    具体 2.5-x 由生成端按 hazard_type 细化），房建/缺省保持历史行为
    （GD-B / GDAQ30306）。不传 project 时行为与旧版完全一致。
    """
    try:
        from app.services.form_routing import (
            BUILDING_WEIDA_DEFAULT,
            MUNICIPAL_RECORD_DEFAULT,
            MUNICIPAL_WEIDA_FALLBACK,
            is_municipal,
        )
    except Exception:  # noqa: BLE001 - 路由异常不影响历史行为
        BUILDING_WEIDA_DEFAULT, MUNICIPAL_WEIDA_FALLBACK, is_municipal, MUNICIPAL_RECORD_DEFAULT = (
            "gdaq_30306_weida", "gdaq_30306_weida", lambda p: False, None,
        )
    if is_weida and record_type == "巡视":
        return MUNICIPAL_WEIDA_FALLBACK if is_municipal(project) else BUILDING_WEIDA_DEFAULT
    if is_municipal(project) and MUNICIPAL_RECORD_DEFAULT:
        return MUNICIPAL_RECORD_DEFAULT.get(record_type)
    return RECORD_TYPE_TO_FORM.get(record_type)


def field_by_key(form_code: str, key: str) -> dict | None:
    for f in fields_of(form_code):
        if f.get("key") == key:
            return f
    return None


def label_to_field(form_code: str) -> dict[str, dict]:
    """追问标签 → 字段（原话兜底回填时按标签反查）。"""
    out: dict[str, dict] = {}
    for f in fields_of(form_code):
        out[str(f.get("label"))] = f
        out[str(f.get("raw_label"))] = f
    return out

