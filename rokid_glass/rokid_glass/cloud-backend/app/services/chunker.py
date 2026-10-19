"""中文文档分块器

按句边界切分，块内不截断句子；相邻块保留尾部重叠，避免答案正好落在切口上。

**块大小为何是 400 字而不是 512**：bge-small-zh 的输入上限是 512 token，中文
近似 1 字 ≈ 1 token，而每块实际送去编码的文本是「来源名 + 正文」（来源名前缀是我
们刻意加的上下文，见 ``rag_index``），必须给它留出预算，否则尾部会被静默截断。
"""
from __future__ import annotations

import re

DEFAULT_CHUNK_SIZE = 400
DEFAULT_OVERLAP = 64

_SENT_SPLIT = re.compile(r"(?<=[。！？；!?;])\s*|\n+")


def normalize(text: str) -> str:
    """归一化空白：全角空格、连续空格、过多空行。"""
    if not text:
        return ""
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def split_sentences(text: str) -> list[str]:
    """按中英文句末标点与换行切句，丢弃空片段。"""
    return [part.strip() for part in _SENT_SPLIT.split(text) if part and part.strip()]


def chunk_text(
    text: str,
    size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[str]:
    """把长文本切成若干块。

    - 原文短于 ``size`` 时原样返回单块；
    - 超长单句（表格行、无标点长串）先硬切，再参与组装；
    - 每块以「上一块尾部 overlap 字符」开头，保留跨块上下文。
    """
    text = normalize(text)
    if not text:
        return []
    if len(text) <= size:
        return [text]

    pieces: list[str] = []
    for sentence in split_sentences(text):
        if len(sentence) <= size:
            pieces.append(sentence)
        else:
            pieces.extend(sentence[i:i + size] for i in range(0, len(sentence), size))

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 1 > size:
            chunks.append(current)
            tail = current[-overlap:] if overlap > 0 else ""
            current = f"{tail}\n{piece}" if tail else piece
        else:
            current = f"{current}\n{piece}" if current else piece
    if current.strip():
        chunks.append(current)

    return [chunk.strip() for chunk in chunks if chunk.strip()]


def compose_embed_text(source_name: str, content: str) -> str:
    """拼装送去编码的文本。

    省统表这类模板的正文大量是「工程名称 / 施工单位 / 检查部位」之类的通用表头，
    脱离文档名几乎无法区分，因此把来源名作为上下文前缀一起编码。
    """
    name = (source_name or "").strip()
    return f"{name}\n{content}" if name else content
