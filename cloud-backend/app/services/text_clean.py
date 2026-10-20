"""AI 回答文本清洗：去掉 Markdown 标记，适配纯文本聊天气泡展示。

小程序/H5/Web 管理端的工作台聊天气泡均为纯文本渲染，模型输出的
Markdown（# 标题、** 加粗、--- 分隔线、反引号、多空行）会原样露出，
此处统一清洗为可读纯文本。
"""
import re

# 行首标题标记：### 一、xxx -> 一、xxx
_HEADING_RE = re.compile(r"^[ \t]*#{1,6}[ \t]+", re.MULTILINE)
# 加粗/斜体：**xxx** / *xxx* / __xxx__ / _xxx_
_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*|__([^_\n]+)__")
_ITALIC_RE = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])|(?<![\w_])_([^_\n]+)_(?![\w_])")
# 行内代码反引号：`xxx` -> xxx
_CODE_RE = re.compile(r"`([^`\n]+)`")
# 水平分隔线：--- / *** / ___（整行只有三个及以上相同符号）
_HR_RE = re.compile(r"^[ \t]*([-*_])[ \t]*\1[ \t]*\1[\1 \t]*$", re.MULTILINE)
# 无序列表标记：- / * / + 开头 -> 圆点
_BULLET_RE = re.compile(r"^[ \t]*[-*+][ \t]+", re.MULTILINE)
# 删除线：~~xxx~~ -> xxx
_STRIKE_RE = re.compile(r"~~([^~\n]+)~~")


def strip_markdown(text: str) -> str:
    """清洗 Markdown 标记并压缩连续空行（最多保留单个换行）。"""
    if not text:
        return text
    s = _HR_RE.sub("", text)
    s = _HEADING_RE.sub("", s)
    s = _BOLD_RE.sub(lambda m: m.group(1) or m.group(2) or "", s)
    s = _ITALIC_RE.sub(lambda m: m.group(1) or m.group(2) or "", s)
    s = _STRIKE_RE.sub(r"\1", s)
    s = _CODE_RE.sub(r"\1", s)
    s = _BULLET_RE.sub("• ", s)
    # 压缩空行：连续 2 个及以上换行压成单个换行；去首尾空白
    s = re.sub(r"\n[ \t]*\n[ \t]*(?:\n[ \t]*)+", "\n", s)
    return s.strip()
