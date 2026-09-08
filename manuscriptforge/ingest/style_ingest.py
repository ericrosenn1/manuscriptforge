from __future__ import annotations

from pathlib import Path
from typing import Any

from manuscriptforge.config import load_project_config
from manuscriptforge.models.style import StyleChunk
from manuscriptforge.style.document_extractors import (
    SUCCESSFUL_EXTRACTION_STATUSES,
    extract_style_corpus,
)
from manuscriptforge.style.features import (
    classify_section_heading,
    detected_style_features,
    infer_section_type,
    is_probable_title,
)
from manuscriptforge.utils.hashing import sha256_text
from manuscriptforge.utils.ids import stable_id
from manuscriptforge.utils.text import split_paragraphs, split_sentences, word_tokens


def _style_mode_from_path(project_dir: Path, path: Path, fallback: str = "unknown") -> str:
    """Infer a style mode from the first nested style_corpus folder."""
    try:
        parts = path.relative_to(project_dir / "style_corpus").parts
    except ValueError:
        return fallback
    if len(parts) >= 2:
        from manuscriptforge.models.style import normalize_style_mode

        return normalize_style_mode(parts[0], fallback=fallback)
    return fallback


def ingest_style_corpus(project_dir: Path) -> list[dict[str, Any]]:
    project_dir = Path(project_dir)
    config = load_project_config(project_dir)
    style_dir = project_dir / "style_corpus"
    samples: list[dict[str, Any]] = []
    if not style_dir.exists():
        return samples
    if config.style.extraction.enabled:
        extraction = extract_style_corpus(project_dir)
        for item in extraction.extractions:
            if item.extraction_status not in SUCCESSFUL_EXTRACTION_STATUSES:
                continue
            text = extraction.texts_by_relative_path.get(item.relative_path, "")
            if not text.strip():
                continue
            samples.append(
                {
                    "source_path": item.relative_path,
                    "text": text,
                    "content_hash": item.content_hash,
                    "style_mode": item.style_mode,
                    "asset_id": item.asset_id,
                    "source_extension": item.extension,
                    "extraction_status": item.extraction_status,
                    "extracted_text_path": item.extracted_text_path,
                    "extraction_warnings": item.warnings,
                }
            )
        return samples
    # A disabled extraction policy also disables the plain-text ingestion path.
    return samples


def _chunk_from_sample(
    sample: dict[str, Any],
    section_type: str,
    text: str,
    ordinal: int,
) -> StyleChunk | None:
    source_file = str(sample.get("source_path", sample.get("source_file", "style_corpus/unknown.txt")))
    style_mode = str(sample.get("style_mode", "unknown"))
    cleaned = text.strip()
    if not cleaned:
        return None
    content_hash = sha256_text(cleaned)
    return StyleChunk(
        chunk_id=stable_id("sty", f"{source_file}:{section_type}:{ordinal}:{content_hash}"),
        source_file=source_file,
        section_type=section_type,
        style_mode=style_mode,
        source_extension=str(sample.get("source_extension", Path(source_file).suffix.lower())),
        source_asset_id=str(sample.get("asset_id", "")),
        extracted_text_path=sample.get("extracted_text_path"),
        extraction_status=sample.get("extraction_status"),
        text=cleaned,
        token_estimate=len(word_tokens(cleaned)),
        sentence_count=len(split_sentences(cleaned)),
        paragraph_count=len(split_paragraphs(cleaned)),
        detected_features=detected_style_features(cleaned),
        content_hash=content_hash,
    )


def _split_long_text(text: str, max_chunk_words: int) -> list[str]:
    if max_chunk_words <= 0 or len(word_tokens(text)) <= max_chunk_words:
        return [text]
    pieces: list[str] = []
    current: list[str] = []
    current_words = 0
    units = split_paragraphs(text) or split_sentences(text) or [text]
    for unit in units:
        unit_words = len(word_tokens(unit))
        if unit_words > max_chunk_words:
            if current:
                pieces.append("\n\n".join(current))
                current = []
                current_words = 0
            sentence_buffer: list[str] = []
            sentence_words = 0
            for sentence in split_sentences(unit) or [unit]:
                count = len(word_tokens(sentence))
                if sentence_buffer and sentence_words + count > max_chunk_words:
                    pieces.append(" ".join(sentence_buffer))
                    sentence_buffer = []
                    sentence_words = 0
                sentence_buffer.append(sentence)
                sentence_words += count
            if sentence_buffer:
                pieces.append(" ".join(sentence_buffer))
            continue
        if current and current_words + unit_words > max_chunk_words:
            pieces.append("\n\n".join(current))
            current = []
            current_words = 0
        current.append(unit)
        current_words += unit_words
    if current:
        pieces.append("\n\n".join(current))
    return [piece for piece in pieces if piece.strip()]


def split_style_sample(
    sample: dict[str, Any],
    *,
    max_chunk_words: int = 900,
    exclude_references_section: bool = True,
) -> list[StyleChunk]:
    source_file = str(sample.get("source_path", sample.get("source_file", "style_corpus/unknown.txt")))
    text = str(sample.get("text", ""))
    if not text.strip():
        return []

    lines = text.splitlines()
    chunks: list[StyleChunk] = []
    buffer: list[str] = []
    heading_seen = False
    current_section = infer_section_type(source_file, text)
    ordinal = 1
    first_nonempty_consumed = False

    def flush() -> None:
        nonlocal buffer, ordinal
        if current_section == "references" and exclude_references_section:
            buffer = []
            return
        for piece in _split_long_text("\n".join(buffer), max_chunk_words):
            chunk = _chunk_from_sample(sample, current_section, piece, ordinal)
            if chunk is not None:
                chunks.append(chunk)
                ordinal += 1
        buffer = []

    for line in lines:
        heading_section = classify_section_heading(line)
        if heading_section is not None:
            flush()
            current_section = heading_section
            heading_seen = True
            first_nonempty_consumed = True
            continue
        if not first_nonempty_consumed and line.strip():
            first_nonempty_consumed = True
            if is_probable_title(line):
                chunk = _chunk_from_sample(sample, "title", line, ordinal)
                if chunk is not None:
                    chunks.append(chunk)
                    ordinal += 1
                continue
        buffer.append(line)
    flush()

    if not heading_seen and len(chunks) == 1 and chunks[0].section_type == "unknown":
        inferred = infer_section_type(source_file, chunks[0].text)
        if inferred != "unknown":
            chunks[0].section_type = inferred
    return chunks


def build_style_chunks(
    project_dir: Path,
    samples: list[dict[str, Any]] | None = None,
) -> list[StyleChunk]:
    config = load_project_config(project_dir)
    samples = samples if samples is not None else ingest_style_corpus(project_dir)
    chunks: list[StyleChunk] = []
    for sample in samples:
        chunks.extend(
            split_style_sample(
                sample,
                max_chunk_words=config.style.extraction.max_chunk_words,
                exclude_references_section=config.style.extraction.exclude_references_section,
            )
        )
    return chunks
