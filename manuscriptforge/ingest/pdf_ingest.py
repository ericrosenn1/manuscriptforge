from __future__ import annotations

from pathlib import Path

from manuscriptforge.models.claim import EvidenceItem
from manuscriptforge.models.source import PDFSourceSummary
from manuscriptforge.utils.hashing import sha256_file, sha256_text
from manuscriptforge.utils.ids import stable_id
from manuscriptforge.utils.text import normalize_space


class OCRProvider:
    name = "base"

    def extract_text(self, path: Path) -> str:
        raise NotImplementedError


class NoOpOCRProvider(OCRProvider):
    name = "noop"

    def extract_text(self, path: Path) -> str:
        del path
        return ""


def extract_pdf_pages(path: Path) -> list[str]:
    try:
        from pypdf import PdfReader
    except Exception as exc:
        raise RuntimeError("Install pypdf to extract PDF text") from exc
    reader = PdfReader(str(path))
    return [normalize_space(page.extract_text() or "") for page in reader.pages]


def extract_pdf_text(path: Path) -> str:
    return normalize_space("\n".join(extract_pdf_pages(path)))


def pdf_page_count(path: Path) -> int | None:
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(path)).pages)
    except Exception:
        return None


def summarize_pdf(path: Path, project_dir: Path, ocr_provider: OCRProvider | None = None) -> PDFSourceSummary:
    rel_path = path.relative_to(project_dir).as_posix()
    file_hash = sha256_file(path)
    warnings: list[str] = []
    pages: list[str] = []
    page_count = pdf_page_count(path)
    try:
        pages = extract_pdf_pages(path)
    except Exception as exc:
        warnings.append(f"PDF text extraction failed: {exc}")
        status = "failed"
    else:
        page_count = len(pages)
        empty_pages = sum(1 for page in pages if not page.strip())
        extracted_chars = sum(len(page) for page in pages)
        if page_count and empty_pages == page_count:
            status = "empty"
            warnings.append("No extractable text was found on any page.")
        elif page_count and extracted_chars / max(page_count, 1) < 50:
            status = "likely_scanned"
            warnings.append("Very little text was extracted per page; OCR may be required.")
        elif empty_pages:
            status = "partial"
            warnings.append(f"{empty_pages} page(s) had no extractable text.")
        else:
            status = "extracted"
    text = normalize_space("\n".join(pages))
    extracted_char_count = len(text)
    needs_ocr = status in {"failed", "empty", "likely_scanned"}
    if needs_ocr:
        ocr_provider = ocr_provider or NoOpOCRProvider()
        ocr_text = ocr_provider.extract_text(path)
        if ocr_text:
            text = normalize_space(text + "\n" + ocr_text)
            extracted_char_count = len(text)
            warnings.append(f"OCR text was supplied by {ocr_provider.name}.")
    return PDFSourceSummary(
        path=rel_path,
        page_count=page_count,
        extracted_char_count=extracted_char_count,
        extraction_status=status,  # type: ignore[arg-type]
        needs_ocr=needs_ocr,
        warnings=warnings,
        first_text_snippet=text[:1000],
        content_hash=file_hash,
    )


def ingest_pdfs(project_dir: Path, ocr_provider: OCRProvider | None = None) -> list[EvidenceItem]:
    pdf_dir = project_dir / "inputs" / "source_pdfs"
    evidence: list[EvidenceItem] = []
    if not pdf_dir.exists():
        return evidence
    for path in sorted(pdf_dir.glob("*.pdf")):
        rel_path = path.relative_to(project_dir).as_posix()
        file_hash = sha256_file(path)
        summary = summarize_pdf(path, project_dir, ocr_provider=ocr_provider)
        snippet = summary.first_text_snippet if summary.first_text_snippet else "; ".join(summary.warnings)
        evidence.append(
            EvidenceItem(
                evidence_id=stable_id("ev", rel_path + file_hash),
                evidence_type="source_pdf",
                source_path=rel_path,
                source_label=path.stem,
                locator="full_text_excerpt",
                text=snippet,
                metadata={
                    "pdf_summary": summary.model_dump(mode="json"),
                    "extracted_characters": summary.extracted_char_count,
                    "page_count": summary.page_count,
                    "extraction_status": summary.extraction_status,
                    "needs_ocr": summary.needs_ocr,
                    "notes": " ".join(summary.warnings),
                },
                content_hash=sha256_text(snippet + file_hash),
            )
        )
    return evidence
