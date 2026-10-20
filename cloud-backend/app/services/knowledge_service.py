"""知识库检索服务 — 基于省统表和安全监理用表，含内容提取与智能检索"""
import html
import os
import re
import zipfile
from pathlib import Path
from typing import Optional
from app.config import settings


# 知识库文档映射（基于省统表目录结构）
KNOWLEDGE_DOCS = {
    "法定代表人任命书": "1法定代表人任命书.docx",
    "质量终身责任书": "2质量终身责任书.docx",
    "总监任命书": "3总监任命书.docx",
    "总监变更申请书": "4总监变更申请书.docx",
    "总代授权书": "5总代授权书.docx",
    "项目印章使用授权书": "6项目印章使用授权书.docx",
    "驻场人员通知书": "7驻场人员通知书.docx",
    "驻场人员调整通知书": "8驻场人员调整通知书.docx",
    "监理规划": "9监理规划.docx",
    "监理细则": "10监理细则.docx",
    "监理审查表": "11监理审查表.docx",
    "工程开工令": "12工程开工令.docx",
    "暂停令": "13暂停令.docx",
    "旁站记录": "14旁站记录.docx",
    "平行检查记录": "15平行检查记录.docx",
    "巡视记录": "16巡视记录.docx",
    "监理通知单、回复单": "17监理通知单、回复单.docx",
    "会议纪要": "18会议纪要.docx",
    "工程变更台账": "19工程变更台账.docx",
    "材料台账": "20材料台账.docx",
    "监理日志": "21监理日志.docx",
    "监理月报": "22监理月报.docx",
    "质量事故监理报告": "23质量事故监理报告.docx",
    "监理工作联系单": "24监理工作联系单.docx",
    "监理档案移交目录": "25监理档案移交目录.docx",
}

# 安全监理用表
SAFETY_DOCS = {
    "监理单位管理人员签名笔迹备查表": "1.监理单位管理人员签名笔迹备查表.xlsx",
    "安全监理危险源控制表": "2.安全监理危险源控制表.xlsx",
    "安全监理日志": "3.安全监理日志.xlsx",
    "施工安全监理周报": "4.施工安全监理周报.xlsx",
    "建筑施工起重机械安装拆卸监理检查记录表": "5.建筑施工起重机械安装拆卸监理检查记录表.xlsx",
    "危险性较大的分部分项工程监理巡视检查记录表": "6.危险性较大的分部分项工程监理巡视检查记录表.xlsx",
    "安全监理工作联系单": "7.安全监理工作联系单.xlsx",
    "安全隐患整改通知": "8.安全隐患整改通知（监理对施工单位项目部）.xlsx",
    "安全隐患整改通知回复": "9.安全隐患整改通知回复（施工单位项目部报监理）.xlsx",
    "暂时停止施工通知": "10.暂时停止施工通知（监理对施工单位项目部）.xlsx",
    "复工申请": "11.复工申请（施工单位项目部报监理）.xlsx",
    "安全监理重大情况报告": "12.安全监理重大情况报告（监理报主管部门）.xlsx",
}

# 规范分类
REGULATION_CATEGORIES = [
    "省统表",
    "安全监理用表",
    "质量验收规范",
    "安全管理规范",
    "施工技术规范",
    "材料规范",
    "消防/给排水/人防规范",
    "地方专属规范",
]

ALLOWED_KNOWLEDGE_EXTS = {".docx", ".xlsx", ".pdf", ".doc", ".xls"}


def iter_knowledge_documents() -> list[dict]:
    """枚举内置与后续导入的知识库文件，避免导入成功后列表/搜索不可见。"""
    root = get_knowledge_base_path()
    categories = {
        "省统表": root / "省统表word版",
        "安全监理用表": root / "第三节安全监理用表" / "安全监理用表",
    }
    display_names = {
        **{filename: name for name, filename in KNOWLEDGE_DOCS.items()},
        **{filename: name for name, filename in SAFETY_DOCS.items()},
    }
    documents = []
    for category, directory in categories.items():
        if not directory.exists():
            continue
        for file_path in sorted(directory.iterdir()):
            if not file_path.is_file() or file_path.suffix.lower() not in ALLOWED_KNOWLEDGE_EXTS:
                continue
            documents.append({
                "name": display_names.get(file_path.name, file_path.stem),
                "filename": file_path.name,
                "category": category,
                "path": str(file_path),
                "exists": True,
            })
    return documents

# 验收指引知识库（内置模板，后续可接入大模型扩展）
ACCEPTANCE_GUIDES = {
    "钢筋": {
        "steps": ["材料进场验收", "钢筋加工检查", "钢筋连接检查", "钢筋安装检查", "隐蔽工程验收"],
        "key_points": ["钢筋型号规格", "钢筋间距", "保护层厚度", "搭接长度", "锚固长度"],
        "common_violations": ["钢筋型号不符", "间距偏差超标", "保护层不足", "搭接长度不够"],
        "regulation_basis": ["GB 50204 混凝土结构工程施工质量验收规范"],
    },
    "混凝土": {
        "steps": ["浇筑前准备", "混凝土进场检验", "浇筑施工", "振捣", "养护"],
        "key_points": ["坍落度", "浇筑顺序", "振捣密实", "养护时间", "施工缝处理"],
        "common_violations": ["坍落度不合格", "振捣不密实", "养护不到位", "随意加水"],
        "regulation_basis": ["GB 50204 混凝土结构工程施工质量验收规范"],
    },
    "模板": {
        "steps": ["模板设计", "模板安装", "支撑系统检查", "预检", "拆除"],
        "key_points": ["模板刚度", "支撑稳定性", "标高尺寸", "拼缝严密", "脱模剂涂刷"],
        "common_violations": ["支撑不牢", "拼缝漏浆", "标高偏差", "拆除过早"],
        "regulation_basis": ["GB 50204 混凝土结构工程施工质量验收规范"],
    },
    "脚手架": {
        "steps": ["方案审查", "材料验收", "基础检查", "搭设施工", "验收使用", "拆除"],
        "key_points": ["立杆间距", "连墙件设置", "剪刀撑", "扫地杆", "安全网", "荷载控制"],
        "common_violations": ["连墙件不足", "剪刀撑缺失", "扫地杆缺失", "超载使用"],
        "regulation_basis": ["JGJ 130 建筑施工扣件式钢管脚手架安全技术规范"],
    },
    "深基坑": {
        "steps": ["方案审查", "支护施工", "开挖", "监测", "验收"],
        "key_points": ["支护方案", "开挖顺序", "变形监测", "地下水位", "周边环境"],
        "common_violations": ["未按方案开挖", "监测不到位", "超挖", "支护不及时"],
        "regulation_basis": ["GB 50497 建筑基坑工程监测技术规范"],
    },
    "防水": {
        "steps": ["基层处理", "防水材料验收", "防水层施工", "细部处理", "闭水试验", "保护层"],
        "key_points": ["基层平整度", "搭接宽度", "细部加强", "闭水时间", "保护层厚度"],
        "common_violations": ["基层不平", "搭接不足", "细部遗漏", "闭水时间不够"],
        "regulation_basis": ["GB 50208 地下防水工程质量验收规范"],
    },
    "电气": {
        "steps": ["材料进场验收", "配管配线", "设备安装", "接线", "绝缘测试", "通电试运行"],
        "key_points": ["管路敷设", "接线规范", "接地连接", "绝缘电阻", "标识齐全"],
        "common_violations": ["管路不规范", "接线错误", "接地缺失", "绝缘不达标"],
        "regulation_basis": ["GB 50303 建筑电气工程施工质量验收规范"],
    },
    "安全帽": {
        "steps": ["进场检查", "佩戴检查", "日常巡查"],
        "key_points": ["安全帽有效期", "佩戴规范", "颜色区分", "配件完好"],
        "common_violations": ["未佩戴安全帽", "佩戴不规范", "使用过期安全帽"],
        "regulation_basis": ["GB 2811 安全帽", "JGJ 59 建筑施工安全检查标准"],
    },
    "临边洞口": {
        "steps": ["识别临边洞口", "设置防护", "验收", "日常巡查", "维护"],
        "key_points": ["防护栏杆", "盖板", "安全网", "警示标识", "夜间警示"],
        "common_violations": ["防护缺失", "防护不牢", "盖板不符", "无警示标识"],
        "regulation_basis": ["JGJ 80 建筑施工高处作业安全技术规范"],
    },
    "临时用电": {
        "steps": ["方案编制", "线路敷设", "配电箱安装", "接地接零", "漏电保护", "验收使用"],
        "key_points": ["三级配电", "二级保护", "TN-S系统", "一机一闸", "漏电保护器"],
        "common_violations": ["三级配电不到位", "漏电保护缺失", "私拉乱接", "一闸多机"],
        "regulation_basis": ["JGJ 46 施工现场临时用电安全技术规范"],
    },
}


def get_knowledge_base_path() -> Path:
    """获取知识库根目录"""
    return settings.knowledge_base_path


def search_by_keyword(keyword: str, category: Optional[str] = None) -> list[dict]:
    """基于关键词检索知识库文档"""
    normalized = keyword.strip().lower()
    return [
        doc for doc in iter_knowledge_documents()
        if (not category or doc["category"] == category)
        and (
            not normalized
            or normalized in doc["name"].lower()
            or normalized in doc["filename"].lower()
        )
    ]


def search_by_content(keyword: str, category: Optional[str] = None) -> list[dict]:
    """基于内容的检索：优先走 RAG 混合检索，不可用时回退关键词子串匹配。

    返回结构保持与旧实现一致（含 name/filename/category/path/exists/snippet），
    另附 score / matched_by 便于前端与评测观察检索质量。
    """
    keyword = (keyword or "").strip()
    if not keyword:
        return []
    semantic = _search_by_content_rag(keyword, category)
    if semantic is not None:
        return semantic
    return _search_by_content_keyword(keyword, category)


def _search_by_content_rag(
    keyword: str, category: Optional[str] = None, bridge: bool = False
) -> Optional[list[dict]]:
    """RAG 语义检索。索引不可用时返回 None，由调用方回退。

    ``bridge`` 打开知识图谱语义桥（默认关闭，见 rag_index.BRIDGE_SOURCE_TYPES
    上方的实测结论）。
    """
    try:
        from app.services import rag_index
    except Exception:
        return None

    try:
        hits = rag_index.search(
            keyword,
            top_k=10,
            source_types=[rag_index.SOURCE_TYPE_KNOWLEDGE],
            bridge=bridge,
        )
    except Exception:
        return None

    # 索引尚未构建时返回 None，让调用方回退关键词检索（而不是误报"没有相关内容"）
    if not hits and not rag_index.has_index():
        return None

    known = {doc["filename"]: doc for doc in iter_knowledge_documents()}
    root = get_knowledge_base_path()

    grouped: dict[str, dict] = {}
    for hit in hits:
        filename = Path(hit["source_id"]).name
        doc = known.get(filename)
        if doc is None:
            relative = Path(hit["source_id"])
            parts = relative.parts
            if "省统表word版" in parts:
                fallback_category = "省统表"
            elif any("安全监理用表" in part for part in parts):
                fallback_category = "安全监理用表"
            else:
                fallback_category = "其他资料"
            doc = {
                "name": hit["source_name"],
                "filename": filename,
                "category": fallback_category,
                "path": str(root / relative),
                "exists": True,
            }
        if category and doc["category"] != category:
            continue
        current = grouped.get(filename)
        if current is None:
            grouped[filename] = {
                **doc,
                "snippet": hit["snippet"],
                "score": hit["score"],
                "matched_by": hit["matched_by"],
            }
    return list(grouped.values())


def _search_by_content_keyword(keyword: str, category: Optional[str] = None) -> list[dict]:
    """关键词子串匹配（RAG 不可用时的兜底实现）。

    提取文档文本后做 ``keyword in text`` 判断，无法处理同义表达与口语化提问。
    """
    results = []
    for doc in iter_knowledge_documents():
        if category and doc["category"] != category:
            continue
        file_path = Path(doc["path"])
        try:
            if file_path.suffix.lower() == ".docx":
                text = _extract_docx_text(file_path)
            elif file_path.suffix.lower() == ".xlsx":
                text = _extract_xlsx_text(file_path)
            elif file_path.suffix.lower() == ".pdf":
                text = _extract_pdf_text(file_path)
            else:
                # 旧版 doc/xls 仍可下载；内容检索需要外部解析器，先按文件名匹配。
                text = ""
            if keyword in text:
                snippet = _extract_snippet(text, keyword)
                results.append({
                    **doc,
                    "snippet": snippet,
                })
        except Exception:
            continue
    return results


_CHECKBOX = "\u25a1"  # □ 标准勾选框


def sanitize_display_text(text: str) -> str:
    """清洗展示文本：私用区字符（无字形的勾选占位符号）归一为标准勾选框 □，
    丢弃无意义的控制字符，避免小程序端出现无法渲染的方框。"""
    if not text:
        return text
    out = []
    for ch in text:
        cp = ord(ch)
        if ch in "\n\t\r":
            out.append(ch)
        elif (0xE000 <= cp <= 0xF8FF
              or 0xF0000 <= cp <= 0xFFFFD
              or 0x100000 <= cp <= 0x10FFFD):
            out.append(_CHECKBOX)
        elif cp < 32 or 0x7F <= cp <= 0x9F:
            continue
        else:
            out.append(ch)
    return "".join(out)


def _extract_docx_text(file_path: Path) -> str:
    """提取 docx 文本内容"""
    try:
        from docx import Document
        doc = Document(str(file_path))
        texts = []
        for para in doc.paragraphs:
            if para.text.strip():
                texts.append(para.text.strip())
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    if cell.text.strip():
                        texts.append(cell.text.strip())
        return sanitize_display_text("\n".join(texts))
    except ImportError:
        return ""
    except Exception:
        return ""


def _extract_xlsx_text(file_path: Path) -> str:
    """提取 xlsx 文本内容（两级防线）。

    1. openpyxl 常规读取（先 read_only，失败再普通模式）；
    2. 仍失败则按 ZIP 直读 —— xlsx 本质是压缩包，表格文字在
       ``xl/sharedStrings.xml`` 与工作表的 ``<t>`` 内联串里。

    为什么需要第 2 道：WPS 等工具生成的表格会让 openpyxl 在 read_only 模式下抛
    ``TypeError: expected string or bytes-like object, got 'NoneType'``，实测安全监理
    用表的 12 个 xlsx **全部** 抽取为空 —— 而它们恰好装着表名、表号、字段名这些
    最有检索价值的文字。
    """
    via_openpyxl = _extract_xlsx_via_openpyxl(file_path)
    if via_openpyxl:
        return via_openpyxl
    return _extract_xlsx_via_zip(file_path)


def _extract_xlsx_via_openpyxl(file_path: Path) -> str:
    try:
        from openpyxl import load_workbook
    except ImportError:
        return ""
    for read_only in (True, False):
        try:
            wb = load_workbook(str(file_path), read_only=read_only, data_only=True)
            texts = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    for cell in row:
                        if cell is not None and str(cell).strip():
                            texts.append(str(cell).strip())
            wb.close()
            if texts:
                return sanitize_display_text("\n".join(texts))
        except Exception:
            continue
    return ""


def _extract_xlsx_via_zip(file_path: Path) -> str:
    """不依赖 openpyxl 的兜底抽取：直接解 ZIP 取文本节点。"""
    try:
        with zipfile.ZipFile(str(file_path)) as archive:
            names = archive.namelist()
            targets = [n for n in names if n == "xl/sharedStrings.xml"]
            targets += [
                n for n in names
                if n.startswith("xl/worksheets/") and n.endswith(".xml")
            ]
            parts: list[str] = []
            for target in targets:
                try:
                    xml = archive.read(target).decode("utf-8", "replace")
                except Exception:
                    continue
                for raw in re.findall(r"<t[^>]*>(.*?)</t>", xml, re.S):
                    value = html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()
                    if value:
                        parts.append(value)
        return sanitize_display_text("\n".join(parts))
    except Exception:
        return ""


def _extract_pdf_text(file_path: Path) -> str:
    """提取 PDF 文本内容（使用 pypdf）"""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(file_path))
        texts = []
        for page in reader.pages:
            text = page.extract_text()
            if text and text.strip():
                texts.append(text.strip())
        return sanitize_display_text("\n".join(texts))
    except ImportError:
        return ""
    except Exception:
        return ""


def _extract_snippet(text: str, keyword: str, context: int = 50) -> str:
    """提取关键词上下文片段"""
    idx = text.find(keyword)
    if idx == -1:
        return ""
    start = max(0, idx - context)
    end = min(len(text), idx + len(keyword) + context)
    snippet = text[start:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."
    return snippet


def get_acceptance_guide(keyword: str) -> dict:
    """验收流程AI智能指引

    当前基于内置模板匹配，后续接入大模型生成。
    """
    matched_docs = search_by_keyword(keyword)

    # 匹配关键词
    for key, guide in ACCEPTANCE_GUIDES.items():
        if key in keyword:
            return {
                "keyword": keyword,
                "steps": guide["steps"],
                "key_points": guide["key_points"],
                "common_violations": guide["common_violations"],
                "regulation_basis": guide["regulation_basis"],
                "matched_docs": [d["name"] for d in matched_docs],
            }

    # 内容检索补充
    content_matches = search_by_content(keyword)

    # 默认指引
    return {
        "keyword": keyword,
        "steps": ["1. 施工前准备检查", "2. 材料进场验收", "3. 施工过程检查", "4. 质量验收", "5. 资料归档"],
        "key_points": ["施工方案审批", "材料合格证", "施工记录", "验收标准", "隐蔽工程记录"],
        "common_violations": ["未按方案施工", "材料不合格", "记录缺失", "验收不规范"],
        "regulation_basis": ["请查询对应专业施工质量验收规范"],
        "matched_docs": [d["name"] for d in matched_docs],
        "content_matches": [{"name": m["name"], "snippet": m.get("snippet", "")} for m in content_matches[:5]],
    }


def get_doc_template(doc_type: str) -> dict | None:
    """获取文书模板信息（用于自动生成文书时参考）"""
    type_map = {
        "旁站记录": "旁站记录",
        "监理日志": "监理日志",
        "监理工程师通知单（质量类）": "监理通知单、回复单",
        "安全隐患整改通知单": "安全隐患整改通知",
        "巡视记录": "巡视记录",
        "见证取样记录": "材料台账",
        "监理周报": "施工安全监理周报",
        "监理月报": "监理月报",
    }

    template_name = type_map.get(doc_type)
    if not template_name:
        return None

    # 查找模板文件
    if template_name in KNOWLEDGE_DOCS:
        filename = KNOWLEDGE_DOCS[template_name]
        path = get_knowledge_base_path() / "省统表word版" / filename
    elif template_name in SAFETY_DOCS:
        filename = SAFETY_DOCS[template_name]
        path = get_knowledge_base_path() / "第三节安全监理用表" / "安全监理用表" / filename
    else:
        return None

    return {
        "name": template_name,
        "filename": filename,
        "path": str(path),
        "exists": path.exists(),
    }
