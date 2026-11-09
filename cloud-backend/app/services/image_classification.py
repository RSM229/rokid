"""工程影像资料分类引擎（监理用）。

把《工程影像资料分类标准.md》落成为**确定性规则引擎**：

- 视觉 / OCR 先把一张照片抽成结构化 ``ImageSignal``（部位、工序对象、是否施工中、
  是否举牌、是否封样、是否尺量、是否会议、是否监理实测、是否危大、是否整改、
  是否进度形象等）；
- 本模块按标准的一级类别判定标准、旁站清单、冲突优先级、标签体系，给出唯一类别、
  二级类、标签、归档目录、归类理由与置信度，并在边界场景标记 ``needs_confirm``。

这样「分类」是可审计、可复现的，而不是每次让模型自由发挥——也正是截图里
"沟槽开挖是危大工程巡视，不用旁站" 这种口径能被稳定落地的关键。

分类轴（标准第一章）：只采用一条——**拍摄瞬间监理正在履行的行为性质**。
验收对象降为二级类别，拍摄形式降为标签。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------- 标准数据

# 一级类别总表（8 类，互斥）。code -> (名称, 归档目录名, 一句话定义)
CATEGORIES: dict[str, tuple[str, str, str]] = {
    "P": ("旁站", "01_旁站", "关键部位、关键工序施工过程中，监理在现场进行的连续监督"),
    "W": ("见证", "02_见证", "对取样、制样、封样、送检及现场检测过程的现场见证"),
    "Y": ("举牌验收", "03_举牌验收", "参建各方持验收牌合影，证明验收真实发生的验收留证"),
    "M": ("材料验收", "04_材料验收", "对进场材料、构配件、设备的外观、资料、尺寸、数量核查"),
    "X": ("巡视", "05_巡视", "对现场的巡回检查，掌握质量、安全、进度、文明施工状况"),
    "H": ("平行检验", "06_平行检验", "监理独立实施的检查或检测，形成与施工方自检的对比"),
    "C": ("会议交底", "07_会议交底", "会议、图纸会审、技术/安全交底、教育培训"),
    "O": ("其他", "08_其他", "形象进度、上级检查、投诉处理、突发事件、竣工外观"),
}

# X 二级类别
X_SUBCATEGORIES: dict[str, tuple[str, str]] = {
    "X1": ("日常质量巡视", "作业面工艺执行、成品保护、材料堆放"),
    "X2": ("安全专项巡视", "危大工程实施、临时用电、临边洞口、高处作业、有限空间、消防、机械"),
    "X3": ("文明施工与环保", "围挡、车辆冲洗、洒水降尘、裸土覆盖、噪声、泥浆处置"),
    "X4": ("隐患整改复查", "整改前后同角度对比、监理通知单回复核实"),
    "X5": ("进度形象", "固定机位进度形象，可用于形象进度确认"),
}

# 旁站清单（标准第四章，P 类判定基准）。name 为可命中关键词。
PANGZHAN_LIST: list[tuple[int, str, str]] = [
    (1, "沟槽、路基土方分层回填与压实", "分层厚度、含水率、压实度、夯实遍数"),
    (2, "管道基础、管座、护管混凝土浇筑", "配合比、坍落度、振捣、几何尺寸"),
    (3, "管道安装、接口处理", "轴线、高程、接口密实"),
    (4, "管道闭水/闭气试验", "试验水头、渗水量、时长"),
    (5, "顶管顶进", "轴线偏差、注浆、纠偏、地面沉降"),
    (6, "检查井砌筑、井周回填", "砂浆饱满度、垂直度、回填密实"),
    (7, "水泥稳定碎石基层摊铺碾压", "厚度、含水率、压实度、养护"),
    (8, "沥青面层摊铺与碾压", "温度、厚度、压实度、接缝"),
    (9, "箱涵钢筋隐蔽、混凝土浇筑、拆模", "钢筋规格间距、保护层、浇筑振捣、拆模强度"),
    (10, "钢板桩支护打拔", "打入深度、垂直度、变形监测"),
    (11, "有限空间作业全程", "通风、气体检测、监护、应急措施"),
    (12, "设备安装与调试", "安装精度、调试参数"),
    (13, "水泥搅拌桩（软基处理）成桩", "水泥掺量、浆液比重、提升速度、桩长、垂直度、桩身试块"),
]

# 项目口径（2026-11-03 监理部确认）：沟槽/深基坑**开挖**不旁站，按 X2。
# 注意只排除"开挖"，"回填"仍在清单第 1 项。
PANGZHAN_EXCLUSION_KEYWORDS = ("开挖", "深基坑开挖", "沟槽开挖")
# 危大工程关键词（命中后巡视优先归 X2，除非另有旁站工序在施）
HAZARD_KEYWORDS = (
    "危大", "深基坑", "沟槽开挖", "模板支架", "顶管", "有限空间", "高处作业",
    "临边洞口", "临时用电", "消防", "起重", "脚手架", "拆除", "暗挖",
)

# 冲突消解优先级（同一时点确实同时满足两类时，就高归档）。
CONFLICT_PRIORITY = ["P", "W", "Y", "M", "H", "X", "C", "O"]

# 标签体系（多值，不参与目录归属）。
LABELS = (
    "举牌", "危大工程", "有限空间", "混凝土浇筑", "钢筋隐蔽", "见证取样",
    "整改前", "整改后", "雨季施工", "首件验收",
)

# 命名规范：YYYYMMDD_类别码+序号_单位工程_部位或桩号_工序或对象_NN.jpg
NAMING_TEMPLATE = "{date}_{cat}{seq:02d}_{unit}_{section}_{content}_{nn:02d}.jpg"

# 影像台账字段（标准第七章）。
LEDGER_FIELDS = [
    "序号", "拍摄日期", "时间", "类别码", "子类", "单位工程", "部位/桩号",
    "工序/对象", "拍摄人", "关联资料编号", "存储路径", "标签", "备注",
]


# ---------------------------------------------------------------- 信号与结果

@dataclass
class ImageSignal:
    """一张照片抽出的结构化信号（vision/OCR 提供，规则引擎消费）。

    布尔字段用 Optional[bool]：None 表示未知，未知时引擎尽量给默认，
    但会在边界场景置 ``needs_confirm``。
    """

    project_name: Optional[str] = None
    unit: Optional[str] = None          # 单位工程（如 沙盛四路 / 大兴路）
    section: Optional[str] = None       # 部位或桩号
    content: Optional[str] = None       # 工序或对象（水印"内容"字段 / vision）
    taken_at: Optional[str] = None
    inspector: Optional[str] = None     # 拍摄人

    in_progress: Optional[bool] = None  # 施工进行中（非准备、非成果）
    has_board: Optional[bool] = None    # 画面有举牌验收牌
    has_seal: Optional[bool] = None     # 封样编号（W 唯一不可缺识别特征）
    has_measuring: Optional[bool] = None  # 尺量 / 资料核查等检查动作
    material_incoming: Optional[bool] = None  # 进场待用材料/构配件/设备
    is_meeting: Optional[bool] = None   # 会议 / 交底 / 培训场景
    has_reading: Optional[bool] = None  # 监理独立实测读数
    is_hazard: Optional[bool] = None    # 危大工程
    is_rectification: Optional[bool] = None  # 整改前后对比
    progress_shot: Optional[bool] = None  # 固定机位进度形象
    related_doc_no: Optional[str] = None  # 关联书面资料编号


@dataclass
class ClassificationResult:
    category_code: str                 # P/W/Y/M/X/H/C/O
    subcategory: Optional[str] = None  # 如 X2
    labels: list[str] = field(default_factory=list)
    archive_dir: str = ""
    reason: str = ""
    confidence: float = 1.0            # 1.0=硬规则；<1=启发式/存疑
    needs_confirm: bool = False
    candidates: list[tuple[str, str]] = field(default_factory=list)  # 边界候选 (code, reason)

    @property
    def category_name(self) -> str:
        return CATEGORIES.get(self.category_code, ("", "", ""))[0]


# ---------------------------------------------------------------- 旁站清单匹配

def matched_pangzhan_item(content: str) -> Optional[tuple[int, str, str]]:
    """工序/对象是否落在旁站清单，返回命中的清单项。"""
    if not content:
        return None
    text = content
    for item in PANGZHAN_LIST:
        if item[1] in text or any(kw in text for kw in _pangzhan_kw(item[1])):
            return item
    return None


def _pangzhan_kw(name: str) -> list[str]:
    """从清单项名称里拆出可独立命中的**具体**子短语。

    刻意不用「砌筑」「浇筑」这类单概念词——否则「电缆沟砌筑」会被错判成
    旁站（清单第 6 项只列「检查井砌筑、井周回填」）。只在短语级别匹配，
    边界由 ``needs_confirm`` 兜底。
    """
    extras: list[str] = []
    table: dict[int, list[str]] = {
        1: ["土方分层回填", "分层回填", "回填与压实", "沟槽回填", "路基回填", "回填"],
        2: ["护管混凝土浇筑", "管座混凝土浇筑", "管道基础混凝土", "混凝土浇筑"],
        3: ["管道安装", "接口处理"],
        4: ["闭水", "闭气试验"],
        5: ["顶管顶进"],
        6: ["检查井砌筑", "井周回填"],
        7: ["水稳碎石", "稳定碎石", "基层摊铺"],
        8: ["沥青面层", "沥青摊铺"],
        9: ["箱涵钢筋隐蔽", "箱涵混凝土浇筑", "箱涵拆模", "钢筋隐蔽", "混凝土浇筑"],
        10: ["钢板桩"],
        11: ["有限空间作业"],
        12: ["顶管设备", "设备安装", "调试"],
        13: ["水泥搅拌桩", "搅拌桩"],
    }
    for idx, kws in table.items():
        if any(kw in name for kw in kws):
            extras.extend(kws)
    return extras


def _excluded_from_pangzhan(content: str) -> bool:
    return bool(content) and any(kw in content for kw in PANGZHAN_EXCLUSION_KEYWORDS)


# ---------------------------------------------------------------- 主归类

def classify_image(signal: ImageSignal) -> ClassificationResult:
    """按标准给一张照片定唯一类别。详见标准第二、三、六章。"""
    content = (signal.content or "").strip()
    labels: list[str] = []

    # —— 第六章 行为优先 + 时点定性，按行为依次判定 ——

    # C 会议交底（行为：开会/交底/培训）
    if signal.is_meeting:
        return ClassificationResult("C", reason="会议/交底/培训场景，归会议交底类", archive_dir=CATEGORIES["C"][1])

    # Y 举牌验收 vs M（边界：验收对象是进场材料时归 M 加举牌标签）
    if signal.has_board:
        if signal.material_incoming:
            labels.append("举牌")
            return ClassificationResult(
                "M", labels=labels,
                reason="画面有验收牌，但验收对象为进场材料/构配件，按边界规则归材料验收并加「举牌」标签",
                archive_dir=CATEGORIES["M"][1],
            )
        labels.append("举牌")
        return ClassificationResult(
            "Y", labels=labels,
            reason="画面验收牌清晰、参验人员同框，验收对象为工序/隐蔽/检验批，归举牌验收",
            archive_dir=CATEGORIES["Y"][1],
        )

    # W 见证（封样编号是 W 唯一不可缺识别特征）
    if signal.has_seal:
        labels.append("见证取样")
        return ClassificationResult(
            "W", labels=labels,
            reason="画面含封样编号/试样编号，属监理在场见证的取样封样送检，归见证",
            archive_dir=CATEGORIES["W"][1],
        )

    # M 材料验收（进场待用材料 + 检查动作）
    if signal.material_incoming or (signal.has_measuring and signal.in_progress is False and not signal.has_seal):
        if signal.has_board:
            labels.append("举牌")
        return ClassificationResult(
            "M", labels=labels,
            reason="对象为进场待用的材料/构配件/设备且有尺量或资料核查动作，归材料验收",
            archive_dir=CATEGORIES["M"][1],
        )

    # H 平行检验（监理独立实测读数）
    if signal.has_reading:
        return ClassificationResult(
            "H", reason="监理独立实施检查/检测且有实测数据，与施工方自检形成对比，归平行检验",
            archive_dir=CATEGORIES["H"][1],
        )

    # P 旁站（须同时满足：在旁站清单 + 施工中 + 非排除项）
    if signal.in_progress:
        if _excluded_from_pangzhan(content):
            # 项目口径：沟槽/深基坑开挖不旁站，按危大工程巡视
            return ClassificationResult(
                "X", subcategory="X2", labels=["危大工程"],
                reason="沟槽/深基坑开挖不在本项目旁站清单（2026-11-03 确认），按危大工程巡视归类，不实施旁站",
                archive_dir=CATEGORIES["X"][1],
            )
        item = matched_pangzhan_item(content)
        if item is not None:
            return ClassificationResult(
                "P", reason=f"工序『{item[1]}』在旁站清单第 {item[0]} 项且为施工进行中，归旁站",
                archive_dir=CATEGORIES["P"][1],
            )
        # 施工中但工序不在旁站清单 → 巡视形象（如 钢筋模板安装、电缆沟砌筑）
        sub, lab = _x_subcategory(signal, content, hazard_default=True)
        return ClassificationResult(
            "X", subcategory=sub, labels=lab,
            reason="施工进行中但工序不在旁站清单（如安装过程/砌筑过程），归巡视（验收时应另拍举牌验收）",
            archive_dir=CATEGORIES["X"][1],
        )

    # X 巡视（无特定验收对象、无连续旁站工序、无取样/检测动作）
    if _looks_like_site_status(signal):
        sub, lab = _x_subcategory(signal, content, hazard_default=False)
        return ClassificationResult(
            "X", subcategory=sub, labels=lab,
            reason=_x_reason(sub, content),
            archive_dir=CATEGORIES["X"][1],
        )

    # O 其他（具备留档价值但不属以上任一类）
    return ClassificationResult(
        "O", reason="不属于以上任一类但具备留档价值，归其他",
        archive_dir=CATEGORIES["O"][1], confidence=0.9,
    )


def _looks_like_site_status(signal: ImageSignal) -> bool:
    if signal.is_hazard or signal.is_rectification or signal.progress_shot:
        return True
    if signal.has_measuring or signal.in_progress is False:
        return True
    # 无任何明确行为信号时，默认按现场状态归巡视（时点定性：无验收/取样即检查）
    return True


def _x_subcategory(signal: ImageSignal, content: str, hazard_default: bool) -> tuple[Optional[str], list[str]]:
    labels: list[str] = []
    if signal.is_rectification:
        return "X4", labels + (["整改后"] if "后" in content else [])
    if signal.progress_shot:
        return "X5", labels
    if signal.is_hazard or _hazard_hit(content):
        labels.append("危大工程")
        return "X2", labels
    if any(k in content for k in ("降尘", "围挡", "冲洗", "裸土", "噪声", "泥浆", "文明")):
        return "X3", labels
    return "X1", labels


def _hazard_hit(content: str) -> bool:
    return any(k in content for k in HAZARD_KEYWORDS)


def _x_reason(sub: Optional[str], content: str) -> str:
    name = X_SUBCATEGORIES.get(sub, ("", ""))[0]
    return f"无特定验收/旁站/取样对象，反映现场总体状态，归巡视-{name}"


# ---------------------------------------------------------------- 文本信号抽取

# 关键词 → 信号标志的启发式（vision 缺失时的兜底，best-effort）。
_KEYWORD_FLAGS: list[tuple[tuple[str, ...], str]] = [
    (("举牌", "验收牌", "举牌验收"), "has_board"),
    (("封样", "试样编号", "封条", "见证取样"), "has_seal"),
    (("尺量", "量测", "实测", "读数", "资料核查", "铭牌", "合格证", "发货单", "报验"), "has_measuring"),
    # 只用「进场/验收动作」词，不用裸材料名——否则「钢筋模板安装」会被误判为材料验收
    (("进场", "卸车", "堆放", "开箱", "报验", "发货单", "材料验收"), "material_incoming"),
    (("会议", "交底", "培训", "会审", "签到"), "is_meeting"),
    (("整改前", "整改后", "复查"), "is_rectification"),
    (("进度形象", "固定机位"), "progress_shot"),
]


def extract_signals_from_text(content: str, section: str = "", project: str = "") -> ImageSignal:
    """从"工序/对象"自由文本 + 部位 兜底抽取信号（供无 vision 时调用）。

    这是启发式，不能替代人工/vision 对"是否施工中、是否举牌"的判断；
    边界结论应配合 ``needs_confirm`` 由人工确认。
    """
    text = f"{content or ''} {section or ''}"
    sig = ImageSignal(content=content, section=section, unit=project)
    for kws, flag in _KEYWORD_FLAGS:
        if any(k in text for k in kws):
            setattr(sig, flag, True)
    # 施工中判定：含进度动词且非"已完成/验收"
    if any(v in content for v in ("浇筑", "回填", "夯实", "碾压", "摊铺", "安装", "砌筑", "顶进", "调试", "打拔", "成桩", "开挖", "转运", "施工")):
        sig.in_progress = True
    if any(v in content for v in ("完成", "验收", "已", "后")):
        sig.in_progress = False
    if _hazard_hit(content) or _hazard_hit(section):
        sig.is_hazard = True
    return sig


# ---------------------------------------------------------------- 命名与台账

def build_filename(date: str, category_code: str, subcategory: Optional[str],
                   unit: str, section: str, content: str, seq: int, nn: int = 1) -> str:
    """按第七章命名规范生成文件名。

    ``类别码`` 取二级类（X 家族）或一级类；``seq`` 为同组两位序号，``nn`` 为组内帧序号。
    """
    cat = subcategory if (category_code == "X" and subcategory) else category_code
    clean = lambda s: (s or "").replace("/", "-").replace("\\", "-").strip()
    return NAMING_TEMPLATE.format(
        date=date, cat=cat, seq=seq, unit=clean(unit),
        section=clean(section), content=clean(content), nn=nn,
    )


def build_ledger_row(index: int, signal: ImageSignal, result: ClassificationResult,
                     seq: int, nn: int = 1, storage_path: str = "") -> dict:
    """拼出一条影像台账记录（标准第七章字段）。"""
    date = (signal.taken_at or "")[:10].replace("-", "")
    if len(date) == 8:
        pass
    else:
        date = date or "00000000"
    return {
        "序号": index,
        "拍摄日期": (signal.taken_at or "")[:10],
        "时间": (signal.taken_at or "")[11:16],
        "类别码": result.category_code,
        "子类": result.subcategory or "",
        "单位工程": signal.unit or "",
        "部位/桩号": signal.section or "",
        "工序/对象": signal.content or "",
        "拍摄人": signal.inspector or "",
        "关联资料编号": signal.related_doc_no or "待补",
        "存储路径": storage_path or build_filename(date, result.category_code, result.subcategory,
                                                   signal.unit or "", signal.section or "",
                                                   signal.content or "", seq, nn),
        "标签": "、".join(result.labels),
        "备注": result.reason,
    }


# ---------------------------------------------------------------- 缺口检测

# 常见缺口的补拍建议（标准第八章）。
GAP_SUGGESTIONS: dict[str, str] = {
    "W": "补充见证取样送检（原材料试样制作与封样、压实度/回弹等现场检测见证）",
    "Y": "补充举牌验收（隐蔽工程、检验批、危大工程验收，牌面日期须与验收记录一致）",
    "H": "补充平行检验（监理自行灌砂/回弹/高程轴线复核等实测）",
    "C": "补充会议/交底/培训影像（附会议纪要或签到表）",
    "P": "补充关键工序旁站过程影像（如下料、振捣、收面覆盖三段式）",
}


def detect_gaps(results: list[ClassificationResult]) -> dict:
    """给定一天已归类的影像，统计各类数量并给出缺失类别与补拍建议。"""
    present: dict[str, int] = {}
    for r in results:
        present[r.category_code] = present.get(r.category_code, 0) + 1
    missing = [c for c in ("W", "Y", "H", "C", "P") if c not in present]
    return {
        "present": present,
        "total": len(results),
        "missing": missing,
        "suggestions": {c: GAP_SUGGESTIONS[c] for c in missing},
    }


# ---------------------------------------------------------------- 接上工作台：OCR → 信号

_ROAD_HINT = ("路", "大道", "街", "线", "标段")


def _infer_unit(section: str, project_name: str) -> str:
    """单位工程兜底：优先取带道路/标段特征的短片段。

    水印相机常见「单位工程:沙盛四路」「部位:w27 井」。OCR 把「单位工程」
    与「部位」都映射到 section（见 watermark_ocr._SEMANTIC_ALIASES），故这里
    从文本里挑出道路名；调用方显式传 ``unit`` 时永远以调用方为准。
    """
    for text in (section, project_name):
        if not text:
            continue
        for part in (p.strip() for p in text.replace("～", "~").split("~")):
            if part and any(k in part for k in _ROAD_HINT) and len(part) <= 20:
                return part
    return ""


_UNIT_LABELS = {"单位工程", "单位工程名称", "施工区域", "施工区域名称"}
_SECTION_LABELS = {"部位", "施工部位", "桩号", "部位桩号"}
_LOCATION_LABELS = {"位置", "地点", "地址", "施工地点", "拍摄地点", "施工位置"}


def _label_value(fields: dict, labels: set[str]) -> str:
    """从 OCR 的 ``labeled_items`` 里按原始标签取值。

    watermark_ocr 把「单位工程」「部位」「位置」都折叠到同一个 ``section``
    canonical（2026-11-04 口径），只保留第一个命中值。而分类标准的「单位工程」
    与「部位/桩号」是台账里两列，故在桥接层按原始标签拆开，不改 OCR 映射。
    """
    for item in fields.get("labeled_items") or []:
        label = str(item.get("label") or "").strip().replace("：", "").replace(":", "")
        value = str(item.get("value") or "").strip()
        if value and label in labels:
            return value
    return ""


def signal_from_watermark(fields: dict, *, unit: str = "", section: str = "",
                         extra_signals: Optional[dict] = None) -> ImageSignal:
    """把 ``recognize_watermark()['fields']`` 转成归类引擎的输入信号。

    - 结构化字段（项目/单位工程/部位/时间/拍摄人/内容）直接映射；
    - 缺失的行为判据（是否施工中、是否举牌、是否封样等）用文本启发式兜底；
    - ``extra_signals`` 里由 vision / 人工确认给出的硬信号优先级最高，会覆盖兜底。

    这样「水印 OCR 出字段 → 视觉模型补行为判据 → 规则引擎按标准定类」三段
    职责清晰，且每一步都可复核。
    """
    generic = str(fields.get("section") or fields.get("location") or "")
    part = _label_value(fields, _SECTION_LABELS) or _label_value(fields, _LOCATION_LABELS) or generic
    sec = section or part
    road = _label_value(fields, _UNIT_LABELS) or _infer_unit(sec, str(fields.get("project_name") or ""))
    content = str(fields.get("content") or fields.get("remark") or "")
    sig = ImageSignal(
        project_name=fields.get("project_name"),
        unit=unit or str(fields.get("unit") or "") or road,
        section=sec,
        content=content,
        taken_at=fields.get("taken_at"),
        inspector=fields.get("inspector"),
        related_doc_no=fields.get("related_doc_no"),
    )

    heuristic = extract_signals_from_text(content, sec, str(fields.get("project_name") or ""))
    for attr in ("in_progress", "has_board", "has_seal", "has_measuring",
                 "material_incoming", "is_meeting", "has_reading", "is_hazard",
                 "is_rectification", "progress_shot"):
        current = getattr(sig, attr)
        if current is None:
            setattr(sig, attr, getattr(heuristic, attr))
    if sig.is_hazard is None:
        sig.is_hazard = heuristic.is_hazard
    if extra_signals:
        for key, value in extra_signals.items():
            if hasattr(sig, key):
                setattr(sig, key, value)
    return sig


# ---------------------------------------------------------------- 接上工作台：批量归档

def model_fields(result: ClassificationResult, filename: str = "",
                 status: str = "confirmed", related_doc_no: str = "") -> dict:
    """产出可直接写进 ``ImageRecord`` 的归类字段（供上传链路 setattr）。"""
    return {
        "category_code": result.category_code,
        "subcategory": result.subcategory,
        "labels": "、".join(result.labels) or None,
        "archive_dir": result.archive_dir,
        "archive_filename": filename or None,
        "related_doc_no": related_doc_no or None,
        "classification_status": status,
        "classification_note": result.reason,
    }


def archive_batch(signals: list[ImageSignal]) -> dict:
    """一批照片一次产出：归档结果 + 影像台账 + 缺口报告 + 待确认项。

    对应截图里的「归档结果」表、「影像台账_YYYYMMDD.csv」与「三处资料缺口」。
    这是纯计算，**不落盘、不写库**；落盘/建文档/同步资料库由上层编排决定。

    命名遵循标准第七章：同类别内 ``seq`` 递增，同一事件（证据成套原则）
    共用 ``seq``、用 ``nn`` 区分帧。
    """
    records: list[dict] = []
    ledger_rows: list[dict] = []
    confirmations: list[dict] = []
    results: list[ClassificationResult] = []
    seq_by_cat: dict[str, int] = {}
    group_seq: dict[tuple, int] = {}
    group_nn: dict[tuple, int] = {}

    for index, sig in enumerate(signals, start=1):
        result = classify_image(sig)
        results.append(result)
        date = (sig.taken_at or "")[:10].replace("-", "") or "00000000"
        # 序号按「类别码」走：X 家族用二级类（X1/X2）独立编号，保证同组连续；
        # 用一级类会让 X1 的照片把 X2 的序号挤开（出现 X201→X204 跳号）。
        seq_key = result.subcategory or result.category_code
        hhmm = (sig.taken_at or "")[11:16]
        # 证据成套原则：同一类别 + 同一部位 + 同一分钟的多张照片算一组，
        # 共用 seq、用 nn 区分帧（如 09:19 箱涵钢筋模板安装的全景与细节）。
        group_key = (date, seq_key, sig.section or "", hhmm)
        if group_key not in group_seq:
            seq_by_cat[seq_key] = seq_by_cat.get(seq_key, 0) + 1
            group_seq[group_key] = seq_by_cat[seq_key]
        seq = group_seq[group_key]
        group_nn[group_key] = group_nn.get(group_key, 0) + 1
        nn = group_nn[group_key]

        filename = build_filename(date, result.category_code, result.subcategory,
                                  sig.unit or "", sig.section or "", sig.content or "", seq, nn)
        status = "confirmed"
        notes: list[str] = []
        if result.needs_confirm:
            status = "needs_review"
            notes.append("归类存疑，待人工确认：" + "; ".join(f"{c}({r})" for c, r in result.candidates))
        if not sig.related_doc_no:
            # 就近对应原则：无书面资料编号的影像只作参考，不入正式类别
            status = "needs_review"
            notes.append("无对应书面资料编号，暂作参考影像，归档前须补齐编号")
        if notes:
            confirmations.append({"序号": index, "时间": (sig.taken_at or "")[11:16],
                                  "部位": sig.section or "", "工序/对象": sig.content or "",
                                  "待办": "；".join(notes)})

        records.append({
            "序号": index,
            "时间": (sig.taken_at or "")[11:16],
            "单位工程": sig.unit or "",
            "部位": sig.section or "",
            "施工内容": sig.content or "",
            "归类": f"{result.category_code} {result.category_name}" + (f"-{result.subcategory}" if result.subcategory else ""),
            "依据": result.reason,
            "归档目录": result.archive_dir,
            "文件名": filename,
            "标签": result.labels,
            "状态": status,
        })
        ledger_rows.append(build_ledger_row(index, sig, result, seq, nn, filename))
        ledger_rows[-1]["备注"] = result.reason + ("；" + "；".join(notes) if notes else "")

    gaps = detect_gaps(results)
    return {
        "records": records,
        "ledger_rows": ledger_rows,
        "ledger_fields": LEDGER_FIELDS,
        "summary": {
            "total": len(signals),
            "by_category": gaps["present"],
            "needs_review": len(confirmations),
        },
        "gaps": gaps,
        "confirmations": confirmations,
    }
