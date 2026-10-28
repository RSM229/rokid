"""危大工程专项施工方案「严重缺陷清单」数据访问层。

只读 app/data/severe_defect_checklist.json，零数据库依赖 —— 80 条红线是
稳定的静态判定标准，直接内存过滤即可，不必走 RAG（RAG 检索用在「联想/兜底」，
精确判定必须逐条对照原文，内存查表最可靠）。

作者: cloud-backend
创建日期: 2026-10-28
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "severe_defect_checklist.json"
_lock = threading.Lock()
_cache: dict[str, Any] | None = None

# 工程类型关键词 → 清单分类 key。用于从方案文本里推断适用的专业条款子集。
# 命中任一关键词即纳入对应专业类；通用条款（general）永远适用。
CATEGORY_HINTS: list[tuple[str, list[str]]] = [
    ("foundation_pit", ["基坑", "土方开挖", "支护", "锚杆", "锚索", "内支撑", "围护", "地连墙", "深基坑"]),
    ("formwork", ["模板", "支撑体系", "高支模", "爬模", "滑模", "满堂支架", "脚手架支撑"]),
    ("lifting", ["起重", "吊装", "塔吊", "汽车吊", "履带吊", "架桥机", "抬吊", "吊耳", "施工升降机"]),
    ("scaffolding", ["脚手架", "悬挑架", "附着式升降", "爬架", "吊篮", "卸料平台", "连墙件"]),
    ("demolition", ["拆除", "爆破", "定向倾倒", "拆违", "拆旧"]),
    ("underground", ["暗挖", "盾构", "顶管", "矿山法", "隧道", "马头门", "管棚", "超前支护"]),
    ("curtain_wall", ["幕墙", "玻璃板块", "轨道吊篮", "环轨"]),
    ("manual_bored_pile", ["人工挖孔", "挖孔桩", "护壁", "扩底", "跳挖"]),
    ("steel_structure", ["钢结构", "钢梁", "钢桁架", "索结构", "索膜", "滑移", "顶升", "转体", "大跨度钢"]),
]


def load_checklist() -> dict[str, Any]:
    """加载清单（带进程内缓存）。"""
    global _cache
    if _cache is not None:
        return _cache
    with _lock:
        if _cache is None:
            _cache = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    return _cache


def meta() -> dict[str, Any]:
    return load_checklist().get("meta", {})


def all_items() -> list[dict[str, Any]]:
    return list(load_checklist().get("items") or [])


def categories() -> list[dict[str, Any]]:
    return list(meta().get("categories") or [])


def item_by_id(item_id: str) -> dict[str, Any] | None:
    for item in all_items():
        if item.get("id") == item_id:
            return item
    return None


def items_by_category(category_key: str) -> list[dict[str, Any]]:
    return [i for i in all_items() if i.get("category_key") == category_key]


def infer_category_keys(text: str) -> list[str]:
    """从方案文本里推断专业分类 key（可命中多个，不含 general）。"""
    hit: list[str] = []
    for key, keywords in CATEGORY_HINTS:
        if any(kw in (text or "") for kw in keywords):
            hit.append(key)
    return hit


def applicable_items(text: str = "", category_keys: list[str] | None = None) -> list[dict[str, Any]]:
    """返回适用条款：通用 17 条 + 命中的专业类条款（去重、按序号排）。

    Args:
        text: 方案文本（用于自动推断专业类）；与 category_keys 二选一。
        category_keys: 显式指定的专业类 key 列表。
    """
    if not category_keys:
        category_keys = infer_category_keys(text or "")
    wanted = {"general", *category_keys}
    selected = [i for i in all_items() if i.get("category_key") in wanted]
    selected.sort(key=lambda i: i.get("global_no") or 0)
    return selected


def search_items(keyword: str, limit: int = 10) -> list[dict[str, Any]]:
    """按关键词匹配条款（原文 + 解读 + 分类名三者任一命中即算）。"""
    kw = (keyword or "").strip()
    if not kw:
        return []
    results: list[tuple[int, dict[str, Any]]] = []
    for item in all_items():
        haystack = "\n".join([
            str(item.get("category") or ""),
            str(item.get("clause") or ""),
            str(item.get("interpretation") or ""),
        ])
        score = 0
        if kw in haystack:
            score = 3
        # 关键词里的分片命中也算（粗略），给一个较低分兜底
        elif any(part and part in haystack for part in kw.split()):
            score = 1
        if score:
            results.append((score, item))
    # score 降序；同分按 global_no 升序（条文顺序稳定）
    results.sort(key=lambda pair: (-pair[0], pair[1].get("global_no") or 0))
    return [item for _score, item in results[:limit]]


def checklist_summary() -> dict[str, Any]:
    """给前端/提示词用的清单概要（不含全部原文，控制体积）。"""
    return {
        "title": meta().get("short_title"),
        "doc_no": meta().get("doc_no"),
        "total": len(all_items()),
        "consequences": meta().get("consequences"),
        "categories": [
            {"key": c["key"], "name": c["name"], "count": c["count"]}
            for c in categories()
        ],
    }