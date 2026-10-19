"""本地文本嵌入服务 — BGE-small-zh-v1.5（ONNX Runtime / CPU）

三条设计约束：
1. **进程级单例**：模型只加载一次。容器以 ``uvicorn --reload`` 运行，每个 worker
   各持一份实例；若写成「请求内加载」，每次请求都要重读 95 MB 模型。
2. **可降级**：依赖缺失或模型文件不存在时 ``available()`` 返回 False，调用方自动
   回退关键词检索。检索链路绝不能因为缺模型而整体崩溃。
3. **池化方式**：bge 系列官方用法是 CLS（``last_hidden_state[:, 0]``）+ L2 归一化。
   归一化后余弦相似度等价于点积，检索时只需一次矩阵乘法。

模型目录可用环境变量 ``RAG_MODEL_DIR`` 覆盖，默认 ``<后端根>/models/bge-small-zh-v1.5``。
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Sequence

import numpy as np

logger = logging.getLogger(__name__)

EMBED_DIM = 512
MAX_TOKENS = 512
_BATCH = 32

_DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[2] / "models" / "bge-small-zh-v1.5"
_DEFAULT_MODEL_NAME = "BAAI/bge-small-zh-v1.5"

_lock = threading.Lock()
_tokenizer = None
_session = None
_input_names: set[str] = set()
_load_failed = False


def model_dir() -> Path:
    """嵌入模型目录。"""
    configured = os.getenv("RAG_MODEL_DIR", "").strip()
    return Path(configured) if configured else _DEFAULT_MODEL_DIR


def _load() -> None:
    """懒加载模型（幂等，线程安全）。失败后本进程内不再重试，避免每次请求都抛异常。"""
    global _tokenizer, _session, _input_names, _load_failed

    if _session is not None or _load_failed:
        return
    with _lock:
        if _session is not None or _load_failed:
            return
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            directory = model_dir()
            onnx_path = directory / "model.onnx"
            tokenizer_path = directory / "tokenizer.json"
            if not onnx_path.exists() or not tokenizer_path.exists():
                raise FileNotFoundError(f"嵌入模型文件缺失：{directory}")

            tokenizer = Tokenizer.from_file(str(tokenizer_path))
            tokenizer.enable_truncation(max_length=MAX_TOKENS)
            tokenizer.enable_padding()

            session = ort.InferenceSession(
                str(onnx_path), providers=["CPUExecutionProvider"]
            )
            _tokenizer = tokenizer
            _session = session
            _input_names = {item.name for item in session.get_inputs()}
            logger.info("嵌入模型已加载：%s（dim=%s）", directory, EMBED_DIM)
        except Exception as exc:  # noqa: BLE001 — 缺模型不能阻断服务
            _load_failed = True
            logger.warning("嵌入模型不可用，检索将回退关键词模式：%s", exc)


def reset() -> None:
    """清空加载状态（测试或模型目录变更后使用）。"""
    global _tokenizer, _session, _input_names, _load_failed
    with _lock:
        _tokenizer = _session = None
        _input_names = set()
        _load_failed = False


def available() -> bool:
    """嵌入能力是否就绪。"""
    _load()
    return _session is not None


def model_label() -> str:
    """用于结果标注的模型名。"""
    return os.getenv("RAG_MODEL_LABEL", _DEFAULT_MODEL_NAME)


def warmup() -> bool:
    """启动预热：把 tokenizer 与 ONNX 会话都建起来，避免首个请求承担加载开销。"""
    if not available():
        return False
    try:
        embed_texts(["预热"])
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("嵌入预热失败：%s", exc)
        return False


def embed_texts(texts: Sequence[str], batch_size: int = _BATCH) -> np.ndarray | None:
    """批量编码为 L2 归一化的 float32 向量，形状 (n, EMBED_DIM)。

    模型不可用时返回 None（调用方据此降级），而不是抛异常。
    """
    _load()
    if _session is None:
        return None
    if not texts:
        return np.zeros((0, EMBED_DIM), dtype=np.float32)

    vectors: list[np.ndarray] = []
    for start in range(0, len(texts), batch_size):
        batch = [text if text and text.strip() else "空" for text in texts[start:start + batch_size]]
        encodings = _tokenizer.encode_batch(batch)
        input_ids = np.array([enc.ids for enc in encodings], dtype=np.int64)
        attention_mask = np.array([enc.attention_mask for enc in encodings], dtype=np.int64)

        feed = {"input_ids": input_ids, "attention_mask": attention_mask}
        if "token_type_ids" in _input_names:
            feed["token_type_ids"] = np.zeros_like(input_ids)

        hidden = _session.run(None, feed)[0]  # (batch, seq, 512)
        vectors.append(hidden[:, 0, :].astype(np.float32))  # CLS 池化

    matrix = np.vstack(vectors)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    np.divide(matrix, np.maximum(norms, 1e-9), out=matrix)
    return matrix


def embed_query(text: str) -> np.ndarray | None:
    """单条查询向量，形状 (EMBED_DIM,)。"""
    matrix = embed_texts([text])
    if matrix is None or matrix.shape[0] == 0:
        return None
    return matrix[0]


def to_blob(vector: np.ndarray) -> bytes:
    """向量 → BLOB。"""
    return np.asarray(vector, dtype=np.float32).tobytes()


def from_blob(blob: bytes | None, dim: int = EMBED_DIM) -> np.ndarray | None:
    """BLOB → 向量。长度不符则返回 None（视为无效向量）。"""
    if not blob:
        return None
    expected = dim * 4
    if len(blob) != expected:
        return None
    return np.frombuffer(blob, dtype=np.float32, count=dim).copy()


def blobs_to_matrix(blobs: Sequence[bytes | None], dim: int = EMBED_DIM) -> tuple[np.ndarray, list[int]]:
    """把若干 BLOB 组装成矩阵，返回 (matrix, 有效行的原索引列表)。

    无效（空 / 长度不符）的向量会被剔除，避免污染矩阵乘法。
    """
    valid: list[np.ndarray] = []
    indices: list[int] = []
    for index, blob in enumerate(blobs):
        vector = from_blob(blob, dim)
        if vector is None:
            continue
        valid.append(vector)
        indices.append(index)
    if not valid:
        return np.zeros((0, dim), dtype=np.float32), []
    return np.vstack(valid).astype(np.float32), indices
