# -*- coding: utf-8 -*-
"""从「监理通各记录关键字（草稿）.xlsx / 字段清单」生成 record_field_spec.py。

用法（在 cloud-backend 目录）：
    python scripts/gen_record_field_spec.py

数据来源
--------
1. 主表：``D:/rokid/监理通各记录关键字（草稿）(1).xlsx`` 的「字段清单」sheet
   （8 类：旁站/平行检查/巡视/监理通知单/验收/监理日志/监理月报/危大巡视）
2. 主表跳号缺的第 5、9 类，按知识库原始表格内联补齐（见 SUPPLEMENT_ROWS）：
   - 序号 5 安全隐患整改通知 GDAQ30308
     来源 知识库/第三节安全监理用表/安全监理用表/安全隐患整改通知.docx
   - 序号 9 工程材料/构配件/设备管理台账 GD-B-219
     来源 知识库/省统表word版/20材料台账.docx

字段的 key/canonical 不来自 xlsx，而是沿用 ``provform_schema.SCHEMAS`` 与
``provform_schema.CONTENT_MAP``（那是前端渲染与落库的真实骨架）；xlsx 只决定
两件事：**由系统生成（auto）** 与 **是否必填（required）**。xlsx 留空的单元格
视为「不覆盖代码既有定义」。

生成物：``app/services/record_field_spec.py``（勿手改，改表后重跑本脚本）
"""
from __future__ import annotations

import json
import os
import re
import sys

XLSX = r'D:/rokid/监理通各记录关键字（草稿）(1).xlsx'
HERE = os.path.dirname(os.path.abspath(__file__))
API_ROOT = os.path.dirname(HERE)
OUT = os.path.join(API_ROOT, 'app', 'services', 'record_field_spec.py')

# ---------------------------------------------------------------------------
# 表名 → form_code
# ---------------------------------------------------------------------------
FORM_MAP = {
    '旁站记录': 'gd_b_212_pangzhan',
    '平行检查记录': 'gd_b_213_pingxing',
    '巡视记录': 'gd_b_214_xunshi',
    '监理通知单': 'gd_b_215_tongzhi',
    '验收记录': 'acceptance_record',
    '监理日志': 'gd_b_220_rizhi',
    '监理月报': 'gd_b_221_yuebao',
    '危大工程巡视记录': 'gdaq_30306_weida',
}

# 主表跳号缺的两类，按知识库原始表格内联补齐（section|字段名|由系统生成|是否必填|类型）
SUPPLEMENT_ROWS: dict[str, list[tuple]] = {
    'gdaq_30308_yinhuan': [
        ('基本信息', '工程项目名称', '是', '是', '文本'),
        ('基本信息', '编号', '是', '是', '文本'),
        ('基本信息', '日期', '是', '是', '日期'),
        ('基本信息', '致（施工单位）', '是', '是', '文本'),
        ('基本信息', '抄送单位（默认监督部门）', '是', '否', '文本'),
        ('内容', '事由（检查类型/背景）', '否', '是', '文本'),
        ('内容', '隐患部位', '否', '是', '文本（精准到楼栋/楼层/轴线）'),
        ('内容', '隐患内容描述', '否', '是', '文本'),
        ('内容', '整改要求', '否', '是', '文本（具体、可量化）'),
        ('内容', '整改完成时限', '否', '是', '日期'),
        ('内容', '复查/回复要求', '否', '是', '文本'),
        ('内容', '处罚依据', '否', '否', '文本'),
        ('内容', '附件（隐患照片编号）', '否', '否', '文本'),
        ('签章', '项目监理机构（项目章）', '是', '是', '文本（签章控件）'),
        ('签收', '签收日期', '否', '是', '日期'),
    ],
    'gd_b_219_cailiao': [
        ('基本信息', '工程项目名称', '是', '是', '文本'),
        ('基本信息', '序号', '是', '是', '序号'),
        ('基本信息', '名称', '否', '是', '文本'),
        ('基本信息', '规格/型号', '否', '是', '文本'),
        ('基本信息', '数量', '否', '是', '数字'),
        ('基本信息', '生产厂家或品牌', '否', '是', '文本'),
        ('进场', '进场日期', '否', '是', '日期'),
        ('进场', '使用部位', '否', '否', '文本'),
        ('见证取样与检测', '取样日期', '否', '否', '日期'),
        ('见证取样与检测', '检测结果', '否', '是', '枚举'),
        ('验收', '进场验收结论', '否', '是', '枚举'),
        ('验收', '验收日期', '否', '否', '日期'),
        ('三对应溯源', '报审表编号', '否', '否', '文本'),
    ],
}

# ---------------------------------------------------------------------------
# 人工校准（生成器匹配不到 / 匹配错时在这里钉死）
# ---------------------------------------------------------------------------
# 表字段名 → schema key
LABEL_ALIAS: dict[str, dict[str, str]] = {
    'gdaq_30306_weida': {
        '危大工程部位': '10_06',
        '危大工程名称/类型/内容': '10_09',
        '巡视内容': '10_13',
        '施工单位项目负责人到位情况': '10_14',
        '专职安全生产管理人员到位情况': '10_15',
        '特种作业人员持证情况': '10_16',
        '监测数据记录（基坑位移/沉降等）': '10_17',
        '巡视发现的问题及处理情况': '10_18',
        '隐患整改及复查闭环情况': '10_19',
    },
    'acceptance_record': {'验收部位、工序、材料、设备进场': '6_05'},
    'gd_b_215_tongzhi': {'问题区域': '4_07', '事由（关于XXXX问题）': '4_06'},
}

# 表里重复列 / 已由别的列承载 → 丢弃（避免同一 schema key 被两条表行覆盖）
TABLE_SKIP: dict[str, set[str]] = {
    'gdaq_30306_weida': {'巡视日期'},   # 与「作业日期」同栏，只保留先出现的作业日期
}

# 表上标「由系统生成=是」，但因外部依赖未就绪、实际仍需追问的字段
# （说明里写明「天气（可对接气象接口）」，当前未接 → 天气、风力都走追问，给枚举）
ASK_ANYWAY: set[str] = {'weather', 'wind'}

SIGNOFF_RE = re.compile(r'签名|签章|盖章|项目章')

# ---------------------------------------------------------------------------
# canonical key → create_work_record 参数名（表里没有对应参数的一律 None → 落 extra_fields）
# ---------------------------------------------------------------------------
ARG_BY_CANONICAL: dict[str, str] = {
    'date': 'record_date',
    'weather': 'weather',
    'section': 'section',
    'content': 'work_content',
    'problems': 'problems',
    'treatment': 'treatment',
    'worker_count': 'worker_count',
    'station_process': 'station_process',
    'station_content': 'station_content',
    'acceptance_result': 'acceptance_result',
    'acceptance_participants': 'acceptance_participants',
    'acceptance_basis': 'acceptance_basis',
    'material_name': 'witness_material_name',
    'material_spec': 'witness_material_spec',
    'quantity': 'witness_quantity',
    'hazard_name': 'hazard_type',
    'wind': 'wind',
    'subcontractor': 'subcontract_unit',
}

# 起止时间在各类记录里对应不同的工具参数
TIME_ARG_BY_FORM: dict[str, tuple[str, str]] = {
    'gd_b_212_pangzhan': ('station_start_time', 'station_end_time'),
    'gd_b_214_xunshi': ('inspection_start_time', 'inspection_end_time'),
    'gdaq_30306_weida': ('inspection_start_time', 'inspection_end_time'),
}

# 同一 canonical 在不同表里回落到不同工具参数（旁站的部位走 station_part → station_location 列）
ARG_OVERRIDE_BY_FORM: dict[str, dict[str, str]] = {
    'gd_b_212_pangzhan': {'section': 'station_part'},
}

# Agent record_type → form_code（create_work_record 用）
RECORD_TYPE_TO_FORM = {
    '巡视': 'gd_b_214_xunshi',
    '旁站': 'gd_b_212_pangzhan',
    '验收': 'acceptance_record',
    '见证': 'gd_b_219_cailiao',
}

# 追问话术（覆盖通用模板；其余走「请补充{label}：」兜底）
QUESTION_OVERRIDES = {
    'section': '这次的工程部位（施工区域）是哪里？例如「3号楼二层临边防护」',
    'problems': '现场发现了什么问题？无异常就回复「符合要求」',
    'weather': '当天天气如何？可以直接选，或回复「跳过」',
    'content': '具体检查/巡视内容是什么？',
    'start_time': '开始时间是几点？例如「9:00」',
    'end_time': '结束时间是几点？例如「11:30」',
    'worker_count': '现场施工人数大约多少？',
    'station_process': '旁站的关键工序是什么？例如「混凝土浇筑」',
    'hazard_name': '危大工程名称/类型是什么？例如「落地式钢管脚手架工程-一般危大工程」',
    'wind': '现场风力如何？可以直接选，或回复「跳过」',
    'qa_onsite': '施工单位质检员、安全员、施工员是否到岗？',
    'self_check': '施工单位是否已完成自查自检并达到质量标准？',
    'acceptance_type': '这是哪类验收？隐蔽工程／检验批／进场验收／分项分部',
    'acceptance_content': '验收要点（验收内容）是什么？',
}

# 枚举选项（追问时前端渲染成 chips；仅表里明示枚举的字段）
ENUM_OPTIONS = {
    'weather': ['晴', '多云', '阴', '小雨', '大雨', '大风'],
    'qa_onsite': ['是', '否'],
    'self_check': ['是', '否'],
    'conclusion': ['合格', '不合格'],
    'acceptance_result': ['合格', '不合格', '待整改'],
    'hazard_category': ['深基坑', '高大模板', '起重吊装', '脚手架', '拆除', '暗挖', '其他'],
    'pm_onsite': ['是', '否'],
    'safety_onsite': ['是', '否'],
    'test_result': ['合格', '不合格', '待检'],
    'acceptance_result_cailiao': ['合格', '不合格'],
}

# 表未列、但可由系统/项目上下文带出的 canonical（仅用于「表里根本没这一行」的情形；
# 表里明确写了「否」的一律按表走追问）
NEVER_ASK_CANONICAL: set[str] = {
    'project_name', 'doc_number', 'construction_unit', 'supervision_org',
    'supervision_unit', 'weekday', 'serial_no', 'report_period', 'review_date',
}


def _norm(text: str) -> str:
    return re.sub(r'[\s　]+', '', str(text or '')).strip()


def load_schemas() -> dict:
    src = open(os.path.join(API_ROOT, 'app', 'services', 'provform_schema.py'),
               encoding='utf-8').read()
    src = src.replace('from app.services.form_registry import FORM_BY_CODE, FORMS',
                      'FORM_BY_CODE={};FORMS=[]')
    ns: dict = {}
    exec(compile(src, 'provform_schema', 'exec'), ns)  # noqa: S102 - 本地只读导出
    return ns['SCHEMAS'], ns['CONTENT_MAP']


def load_table_rows() -> dict[str, list[dict]]:
    import openpyxl
    wb = openpyxl.load_workbook(XLSX, data_only=True)
    ws = wb['字段清单']
    out: dict[str, list[dict]] = {}
    name = section = None
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, max_col=8, values_only=True):
        _no, c_name, c_sec, c_field, c_auto, c_req, c_type = (list(row) + [None] * 7)[:7]
        if c_name:
            name = _norm(c_name)
        if c_sec:
            section = str(c_sec).strip()
        if not c_field:
            continue
        out.setdefault(name, []).append({
            'section': section, 'label': str(c_field).strip(),
            'auto': str(c_auto).strip() if c_auto else '',
            'required': str(c_req).strip() if c_req else '',
            'type': str(c_type).strip() if c_type else '',
        })
    return out


def match_schema_key(label: str, schema_fields: list[dict], used: set[str],
                     alias: dict[str, str]) -> str | None:
    if label in alias:
        return alias[label]
    nl = _norm(label)
    for f in schema_fields:                      # 完全一致
        if f['key'] in used:
            continue
        if _norm(f['label']) == nl:
            return f['key']
    for f in schema_fields:                      # 互为子串
        if f['key'] in used:
            continue
        fl = _norm(f['label'])
        if nl and fl and (nl in fl or fl in nl):
            return f['key']
    return None


def build() -> dict:
    schemas, content_map = load_schemas()
    table = load_table_rows()
    forms: dict[str, dict] = {}
    report: list[str] = []

    jobs: list[tuple[str, list[dict]]] = []
    for tname, code in FORM_MAP.items():
        rows = table.get(tname)
        if rows is None:                       # 表里名称带后缀（如「监理通知单（隐患识别/安隐单）」）
            for k, v in table.items():
                if k.startswith(tname):
                    rows = v
                    break
        if rows is None:
            report.append(f'!! 主表未找到「{tname}」')
            continue
        jobs.append((code, rows))
    for code, rows in SUPPLEMENT_ROWS.items():
        jobs.append((code, [{'section': s, 'label': l, 'auto': a, 'required': r, 'type': t}
                            for s, l, a, r, t in rows]))

    for code, rows in jobs:
        schema = schemas[code]
        schema_fields = []
        for sec in schema['sections']:
            for f in sec['fields']:
                schema_fields.append({'key': f['key'], 'label': f['label'],
                                      'type': f['type'], 'required': f['required'],
                                      'system': bool(f.get('system')),
                                      'section': sec['title']})
        cmap = content_map.get(code, {})
        alias = LABEL_ALIAS.get(code, {})
        skips = TABLE_SKIP.get(code, set())
        used: set[str] = set()
        fields: list[dict] = []
        matched_by_table: dict[str, dict] = {}

        for row in rows:
            label = row['label']
            if label in skips:
                report.append(f'   [{code}] 跳过表中重复行「{label}」')
                continue
            key = match_schema_key(label, schema_fields, used, alias)
            if key is None:
                if SIGNOFF_RE.search(label):
                    # 表里真实存在、但 schema 用 signoffs[] 表达的签名/盖章位 → 记为 signoff，不追问
                    fields.append({
                        'key': None, 'canonical': None, 'label': label,
                        'raw_label': label, 'section': row['section'],
                        'table_type': row['type'], 'kind': 'signoff',
                        'auto': row['auto'] == '是', 'required': row['required'] == '是',
                        'arg': None,
                    })
                    continue
                report.append(f'   [{code}] 表行「{label}」未匹配到 schema 字段')
                continue
            used.add(key)
            sf = next(f for f in schema_fields if f['key'] == key)
            canonical = cmap.get(key, key)
            fields.append({
                'key': key,
                'canonical': canonical,
                'label': label,
                'raw_label': sf['label'],
                'section': row['section'] or sf['section'],
                'table_type': row['type'],
                'auto': (row['auto'] == '是') if row['auto'] else bool(sf.get('system')),
                'required': (row['required'] == '是') if row['required'] else bool(sf['required']),
            })
            matched_by_table[label] = fields[-1]

        for sf in schema_fields:
            if sf['key'] not in used:
                canonical = cmap.get(sf['key'], sf['key'])
                fields.append({
                    'key': sf['key'], 'canonical': canonical, 'label': sf['label'],
                    'raw_label': sf['label'], 'section': sf['section'],
                    'table_type': '', 'auto': bool(sf.get('system')),
                    'required': bool(sf['required']),
                })
                report.append(f'   [{code}] 表未列字段「{sf["label"]}」→ 沿用代码定义'
                              f'(auto={bool(sf.get("system"))}, required={bool(sf["required"])})')

        # 定 kind / arg
        for f in fields:
            if f.get('kind') == 'signoff':       # 上面已定（schema 无对应字段的签名位）
                f['arg'] = None
                continue
            if SIGNOFF_RE.search(f['label']) or SIGNOFF_RE.search(f['raw_label']):
                f['kind'] = 'signoff'
            elif f['canonical'] in NEVER_ASK_CANONICAL:
                f['kind'] = 'auto'
            elif f['auto'] and f['canonical'] not in ASK_ANYWAY:
                f['kind'] = 'auto'
            elif f['auto'] and f['canonical'] in ASK_ANYWAY:
                f['kind'] = 'ask'          # 表标「是」但依赖未就绪（天气）
            else:
                f['kind'] = 'ask'
            if f['kind'] != 'ask':
                f['arg'] = None
            elif f['canonical'] in ('start_time', 'end_time'):
                f['arg'] = None          # 下面按表指派（旁站/巡视/危大参数名不同）
            else:
                f['arg'] = ARG_BY_CANONICAL.get(f['canonical'])

        # 按表覆盖 canonical → 工具参数
        for canon, arg in (ARG_OVERRIDE_BY_FORM.get(code) or {}).items():
            for f in fields:
                if f['kind'] == 'ask' and f['canonical'] == canon:
                    f['arg'] = arg

        # 时间字段按表单独指派（旁站/巡视/危大参数名不同）
        pair = TIME_ARG_BY_FORM.get(code)
        if pair:
            for f in fields:
                if f['canonical'] == 'start_time':
                    f['arg'] = pair[0]
                elif f['canonical'] == 'end_time':
                    f['arg'] = pair[1]

        forms[code] = {
            'form_code': code,
            'form_number': schema['form_number'],
            'name': schema['name'],
            'sections': [s['title'] for s in schema['sections']],
            'signoffs': schema.get('signoffs', []),
            'fields': fields,
        }

    return {'forms': forms, 'report': report}


def emit(forms: dict, report: list[str]) -> str:
    lines: list[str] = []
    lines.append('"""省统表「字段表」驱动的录入与追问口径（自动生成，勿手改）。')
    lines.append('')
    lines.append(f'来源：{os.path.basename(XLSX)} /「字段清单」+ 知识库补齐的序号 5、9。')
    lines.append('重跑：cd cloud-backend && python scripts/gen_record_field_spec.py')
    lines.append('')
    lines.append('kind 语义：')
    lines.append('  auto    —— 表「由系统生成=是」，由系统带出，不进追问（项目信息/编号/日期/星期等）')
    lines.append('  ask     —— 表「由系统生成=否」，用户没说就追问补充；签名/盖章类另见 signoff')
    lines.append('  signoff —— 签名/签章/盖章，走签章服务，不追问、不入 args')
    lines.append('"""')
    lines.append('from __future__ import annotations')
    lines.append('')
    lines.append('TABLE_SOURCE = %r' % os.path.basename(XLSX))
    lines.append('')
    lines.append('ASK_ANYWAY = %r' % sorted(ASK_ANYWAY))
    lines.append('')
    for name, obj in (('QUESTION_OVERRIDES', QUESTION_OVERRIDES),
                      ('ENUM_OPTIONS', ENUM_OPTIONS),
                      ('RECORD_TYPE_TO_FORM', RECORD_TYPE_TO_FORM)):
        lines.append('%s = %s' % (name, _pp(obj)))
        lines.append('')
    lines.append('')
    lines.append('FORMS: dict[str, dict] = {')
    for code, form in forms.items():
        lines.append(f'    # {form["form_number"]}  {form["name"]}')
        lines.append(f'    {code!r}: {{')
        lines.append(f'        "form_number": {form["form_number"]!r},')
        lines.append(f'        "name": {form["name"]!r},')
        lines.append(f'        "signoffs": {form["signoffs"]!r},')
        lines.append('        "fields": [')
        for f in form['fields']:
            lines.append('            ' + _pp(f) + ',')
        lines.append('        ],')
        lines.append('    },')
    lines.append('}')
    lines.append('')
    lines.append(HELPERS)
    return '\n'.join(lines) + '\n'


def _pp(obj) -> str:
    """Python 字面量（不是 JSON）——true/false/null 在 .py 里非法，必须用 repr。"""
    return repr(obj)


HELPERS = '''

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
    return "请补充" + str(field.get("label") or canon) + "："


def options_for(field: dict) -> list[str]:
    return ENUM_OPTIONS.get(field.get("canonical") or "", [])


def form_for_record_type(record_type: str, is_weida: bool = False) -> str | None:
    if is_weida and record_type == "巡视":
        return "gdaq_30306_weida"
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
'''


def main() -> int:
    built = build()
    with open(OUT, 'w', encoding='utf-8') as fh:
        fh.write(emit(built['forms'], built['report']))
    total = sum(len(v['fields']) for v in built['forms'].values())
    print(f'written {OUT}')
    print(f'forms={len(built["forms"])} fields={total}')
    for code, form in built['forms'].items():
        ask = len(ask_fields_of(form))
        auto = sum(1 for f in form['fields'] if f['kind'] == 'auto')
        sign = sum(1 for f in form['fields'] if f['kind'] == 'signoff')
        print(f'  {code:24s} 字段{len(form["fields"]):3d}  追问{ask:3d}  系统{auto:3d}  签章{sign:2d}')
    print('--- 校准提示 ---')
    for line in built['report']:
        print(line)
    return 0


def ask_fields_of(form: dict) -> list[dict]:
    return [f for f in form['fields'] if f['kind'] == 'ask']


if __name__ == '__main__':
    sys.exit(main())
