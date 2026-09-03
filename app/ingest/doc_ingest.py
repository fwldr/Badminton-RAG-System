"""通用文档入库：txt/md/csv/pdf/图片 → 分块 → embedding → 存入 Chroma collection。

- 每文档一个 collection（便于删除/重索引）：管理端上传 = `doc_{id}`；
  CLI 批量入库 = `pdf_{file_hash前8位}` / `img_{...}` / `doc_{...}`（按文件 hash 幂等）；
- 重索引 = 删 collection 再入（版本 +1，由调用方维护）；
- 分块策略：txt/md 按段落 + 行（chunk_size 默认 500，超长段滑动窗口重叠）；
  csv 按「表头:值」逐行序列化（不假设固定表头）；pdf 按页抽取文本 + `find_tables()`
  表格转「表头:值」，每块带 page_no；图片走 OCR 文本（Step 2 接入引擎）。
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
from pathlib import Path

from app.ingest.embedder import Embedder
from app.ingest.store import VectorStore

logger = logging.getLogger(__name__)

MAX_CHUNK = 200  # 旧分块上限（_split_text 兼容包装）

# 支持的图片扩展名（OCR/多模态入库；admin.py 的 ALLOWED_EXTS 在 Step 4 扩展）
IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "bmp"}


def _chunk_text(text: str, chunk_size: int = 500, chunk_overlap: int = 0) -> list[str]:
    """按段落优先、行兜底分块；超长段用滑动窗口重叠切分。"""
    step = max(chunk_size - chunk_overlap, 1) if chunk_overlap < chunk_size else chunk_size
    blocks: list[str] = []
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if len(para) <= chunk_size:
            blocks.append(para)
            continue
        # 超长段落：按行累加；行内超长用窗口重叠硬切
        lines = [ln.strip() for ln in para.split("\n") if ln.strip()]
        buf = ""
        for line in lines:
            if len(line) > chunk_size:
                if buf:
                    blocks.append(buf)
                    buf = ""
                pos = 0
                while pos < len(line):
                    piece = line[pos : pos + chunk_size]
                    if piece.strip():
                        blocks.append(piece)
                    pos += step
            elif len(buf) + len(line) + 1 <= chunk_size:
                buf = f"{buf}\n{line}".strip() if buf else line
            else:
                if buf:
                    blocks.append(buf)
                buf = line
        if buf:
            blocks.append(buf)
    return blocks


def _split_text(text: str) -> list[str]:
    """旧行为包装：200 字符无重叠分块（兼容既有调用/测试）。"""
    return _chunk_text(text, MAX_CHUNK, 0)


def _parse_csv(file_bytes: bytes) -> list[str]:
    """CSV 按「表头:值」逐行序列化（通用，不依赖固定表头）。"""
    text = file_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []
    rows: list[str] = []
    for row in reader:
        parts = [f"{k}:{v}" for k, v in row.items() if v is not None and str(v).strip()]
        if parts:
            rows.append("，".join(parts))
    return rows


def _parse_pdf(
    file_bytes: bytes, chunk_size: int = 500, chunk_overlap: int = 50
) -> list[tuple[str, int]]:
    """逐页抽取 PDF 文本 + 表格 → (文本块, 页码) 列表（页码从 1 开始）。

    仅支持有文字层的电子版 PDF（Q2=A）：整页无文字层（扫描页）跳过并计数；
    全部页面均无文字层时返回空列表（调用方判 failed）。
    """
    import fitz  # pymupdf（懒加载：不装 pymupdf 也不影响其余链路）

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    blocks: list[tuple[str, int]] = []
    for page_no in range(len(doc)):
        page = doc[page_no]
        parts: list[str] = []

        # 1) 正文文本（无文字层的扫描页 get_text 为空）
        text = page.get_text("text").strip()
        if text:
            parts.append(text)

        # 2) 表格 → 「表头:值」序列化（对齐 _parse_csv 模式）
        try:
            tables = page.find_tables().tables
            for table in tables:
                rows = table.extract()
                if not rows:
                    continue
                header = [str(c or "").strip() for c in rows[0]]
                for row in rows[1:]:
                    cells = [str(c or "").strip() for c in row]
                    kv = [f"{h}:{v}" for h, v in zip(header, cells) if v]
                    if kv:
                        parts.append("，".join(kv))
        except Exception:
            logger.debug("第 %d 页表格抽取失败，忽略", page_no + 1, exc_info=True)

        if parts:
            for chunk in _chunk_text("\n".join(parts), chunk_size, chunk_overlap):
                if chunk.strip():
                    blocks.append((chunk, page_no + 1))
    doc.close()
    return blocks


def _parse_image(
    file_bytes: bytes,
    filename: str,
    ocr=None,
    vision_embed=None,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    ocr_min_chars: int = 20,
) -> list[tuple[str, dict]]:
    """图片 → (文本块, metadata) 列表。

    索引①（OCR 文本链路）：OCR 文本 ≥ ocr_min_chars → 文本块（入 doc_{id} 文本检索）；
    无文字图片（OCR 文本过短或为空）→ 交视觉向量索引（索引②，Step 2/5 接入 vision_embed）。
    本期 Step 1 未接入引擎时返回空列表（调用方判 failed 并给出提示）。
    """
    if ocr is None:
        return []
    text = (ocr.ocr(file_bytes) or "").strip()
    if len(text) < ocr_min_chars:
        return []  # 无文字：交视觉索引（Step 2 接入）
    return [
        (chunk, {"ocr_text": text})
        for chunk in _chunk_text(text, chunk_size, chunk_overlap)
    ]


def _save_image_copy(
    image_dir: Path | None, file_bytes: bytes, ext: str, file_hash: str
) -> str | None:
    """把图片副本落盘到公开目录（data/uploads/docs），返回保存文件名（纯 ASCII）。

    image_dir 为空（未启用）时返回 None：图片只入向量库，不对外展示。
    """
    if image_dir is None:
        return None
    image_dir.mkdir(parents=True, exist_ok=True)
    name = f"img_{file_hash}.{ext}"  # 与 collection 名一致，纯 ASCII 利于 URL
    (image_dir / name).write_bytes(file_bytes)
    return name


def ingest_document(
    file_bytes: bytes,
    filename: str,
    doc_id: int | None,
    store: VectorStore,
    embedder: Embedder,
    ocr=None,
    vision_embed=None,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    ocr_min_chars: int = 20,
    collection: str | None = None,
    source_path: str = "",
    image_dir: Path | None = None,
    image_url_prefix: str = "/uploads/docs",
) -> tuple[str, int, str | None]:
    """解析并入库文档，返回 (status, chunk_count, error_msg)。

    status ∈ {ready, failed}；失败时 error_msg 记录原因（由调用方把记录置 failed）。
    collection 缺省为 `doc_{doc_id}`（管理端）；显式传入时用该名（CLI 按文件 hash 命名）。
    按**真实扩展名**分派（不信任上传 Content-Type），PDF 二进制不会被 utf-8 解码。
    """
    try:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        file_hash = hashlib.sha256(file_bytes).hexdigest()[:12]
        base_meta: dict = {
            "文件名": filename,
            "来源文件": f"上传文档 {filename}",
            "file_hash": file_hash,
        }
        if doc_id is not None:
            base_meta["doc_id"] = str(doc_id)
        # 图片文档：把图片副本落盘到公开目录，并写入展示 URL（聊天回答内联用）
        if ext in IMAGE_EXTS:
            saved = _save_image_copy(image_dir, file_bytes, ext, file_hash)
            if saved:
                base_meta["图片URL"] = f"{image_url_prefix}/{saved}"

        chunks: list[str] = []
        metas: list[dict] = []

        if ext == "csv":
            chunks = _parse_csv(file_bytes)
            # 保持旧行为：csv 元数据不加 source_type/file_hash（回归零影响）
            metas = [dict(base_meta) for _ in chunks]
        elif ext == "pdf":
            pairs = _parse_pdf(file_bytes, chunk_size, chunk_overlap)
            if not pairs:
                return "failed", 0, "PDF 无文字层（扫描版暂不支持，请提供电子版 PDF）"
            chunks = [t for t, _ in pairs]
            metas = [
                {**base_meta, "source_type": "pdf", "page_no": p, "原始路径": source_path}
                for _, p in pairs
            ]
        elif ext in IMAGE_EXTS:
            img_chunks = _parse_image(
                file_bytes, filename, ocr, vision_embed,
                chunk_size, chunk_overlap, ocr_min_chars,
            )
            if img_chunks:
                chunks = [t for t, _ in img_chunks]
                metas = [
                    {**base_meta, **m, "source_type": "image", "原始路径": source_path}
                    for _, m in img_chunks
                ]
            elif vision_embed is not None:
                # 无文字图片 → 多模态向量索引：img_{hash8} collection，
                # 视觉向量是 SiliconFlow Qwen3-VL 独立空间，查询走 vision_embed.embed_text
                target = collection or f"img_{file_hash}"
                try:
                    store.delete_collection(target)
                except Exception:
                    pass
                [vec] = vision_embed.embed_images([file_bytes])
                meta = {
                    **base_meta,
                    "source_type": "image",
                    "embed_dim": len(vec),
                    "原始路径": source_path,
                }
                store.add(target, [f"{target}:0"], [f"[图片] {filename}"], [meta], [vec])
                return "ready", 1, None
            else:
                return "failed", 0, "图片无文字层或 OCR 未配置（需 OCR 引擎，或开启多模态索引 VISION_EMBED_ENABLED=true）"
        else:
            text = file_bytes.decode("utf-8", errors="replace")
            chunks = _chunk_text(text, chunk_size, chunk_overlap)
            metas = [{**base_meta, "source_type": "text"} for _ in chunks]

        if not chunks:
            return "failed", 0, "文档为空或无法解析出内容"

        target = collection or f"doc_{doc_id}"
        ids = [f"{target}:{i}" for i in range(len(chunks))]
        # 重索引/批量：先清空旧 collection 再入（幂等）
        try:
            store.delete_collection(target)
        except Exception:
            pass
        embeddings = embedder.embed(chunks)
        store.add(target, ids, chunks, metas, embeddings)
        return "ready", len(chunks), None
    except Exception as exc:
        logger.exception("文档 %s 入库失败", filename)
        return "failed", 0, str(exc)
