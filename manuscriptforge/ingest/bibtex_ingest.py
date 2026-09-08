from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from manuscriptforge.intake.scaffold import is_scaffold_file
from manuscriptforge.models.citation import CitationRecord
from manuscriptforge.utils.hashing import sha256_file
from manuscriptforge.utils.ids import slugify, stable_id


def _fallback_parse_bibtex(text: str) -> list[dict[str, str]]:
    """Parse simple BibTeX entries when the optional parser fails."""
    entries: list[dict[str, str]] = []
    for match in re.finditer(r"@(?P<kind>\w+)\s*\{\s*(?P<key>[^,]+),(?P<body>.*?)\n\}", text, re.S):
        body = match.group("body")
        fields = {"ID": match.group("key").strip(), "ENTRYTYPE": match.group("kind")}
        for field_match in re.finditer(r"(\w+)\s*=\s*[\{\"](.+?)[\}\"]\s*,?", body, re.S):
            fields[field_match.group(1).lower()] = re.sub(r"\s+", " ", field_match.group(2)).strip()
        entries.append(fields)
    return entries


DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.I)
PMID_RE = re.compile(r"\bPMID[:\s]*(\d{5,9})\b|\bpmid[:\s]*(\d{5,9})\b")
PMCID_RE = re.compile(r"\bPMC\d+\b", re.I)
ARXIV_RE = re.compile(r"\barXiv[:\s]*([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)\b", re.I)
URL_RE = re.compile(r"https?://\S+")


def _parse_authors(authors_raw: str) -> list[str]:
    """Split BibTeX or semicolon-separated author strings."""
    if not authors_raw:
        return []
    if " and " in authors_raw:
        return [author.strip() for author in authors_raw.split(" and ") if author.strip()]
    return [author.strip() for author in authors_raw.split(";") if author.strip()]


def _metadata_notes(entry: dict[str, Any]) -> str:
    """Describe missing source metadata for audit and validation messages."""
    missing = [
        field
        for field in ["title", "author", "year", "journal"]
        if not str(entry.get(field) or "").strip()
    ]
    notes = ["Parsed from BibTeX. Metadata only unless abstract/full text is supplied."]
    if missing:
        notes.append("Missing metadata fields: " + ", ".join(missing) + ".")
    if not entry.get("doi") and not entry.get("pmid"):
        notes.append("No DOI or PMID supplied.")
    return " ".join(notes)


def _record(
    *,
    key: str,
    title: str,
    file_hash: str,
    rel_path: str,
    source_kind: str,
    authors: list[str] | None = None,
    year: str | None = None,
    doi: str | None = None,
    pmid: str | None = None,
    pmcid: str | None = None,
    arxiv_id: str | None = None,
    url: str | None = None,
    journal: str | None = None,
    abstract: str | None = None,
    raw_bibtex: str | None = None,
    notes: str = "",
) -> CitationRecord:
    """Build a normalized citation record from parsed source fields."""
    confidence = "exact_identifier" if doi or pmid or pmcid or arxiv_id else "weak_match"
    return CitationRecord(
        citation_id=stable_id("cit", key + title + file_hash, length=8),
        title=title or "Untitled source",
        authors=authors or [],
        year=year,
        doi=doi,
        pmid=pmid,
        pmcid=pmcid,
        arxiv_id=arxiv_id,
        url=url,
        journal=journal,
        raw_bibtex=raw_bibtex,
        source_path=rel_path,
        abstract=abstract,
        metadata_status="parsed",
        metadata_confidence=confidence,  # type: ignore[arg-type]
        retrieval_notes="Metadata enrichment not requested.",
        source_kind=source_kind,  # type: ignore[arg-type]
        notes=notes,
    )


def parse_bibtex_file(path: Path, project_dir: Path) -> list[CitationRecord]:
    """Parse one BibTeX file into citation records."""
    raw = path.read_text(encoding="utf-8")
    try:
        import bibtexparser

        entries = bibtexparser.loads(raw).entries
    except Exception:
        entries = _fallback_parse_bibtex(raw)

    records: list[CitationRecord] = []
    rel_path = path.relative_to(project_dir).as_posix()
    file_hash = sha256_file(path)
    for entry in entries:
        title = str(entry.get("title") or "Untitled source").strip("{} ")
        key = str(entry.get("ID") or slugify(title))
        authors_raw = str(entry.get("author") or "")
        authors = _parse_authors(authors_raw)
        raw_entry_match = re.search(rf"@\w+\s*\{{\s*{re.escape(key)}\s*,.*?\n\}}", raw, re.S)
        raw_entry = raw_entry_match.group(0) if raw_entry_match else None
        records.append(
            _record(
                key=key,
                title=title,
                file_hash=file_hash,
                rel_path=rel_path,
                source_kind="bibtex",
                authors=authors,
                year=str(entry.get("year")) if entry.get("year") else None,
                doi=str(entry.get("doi")) if entry.get("doi") else None,
                pmid=str(entry.get("pmid")) if entry.get("pmid") else None,
                pmcid=str(entry.get("pmcid")) if entry.get("pmcid") else None,
                arxiv_id=str(entry.get("eprint")) if entry.get("eprint") else None,
                url=str(entry.get("url")) if entry.get("url") else None,
                journal=str(entry.get("journal")) if entry.get("journal") else None,
                raw_bibtex=raw_entry,
                abstract=str(entry.get("abstract")) if entry.get("abstract") else None,
                notes=_metadata_notes(entry),
            )
        )
    return records


def parse_citation_json_file(path: Path, project_dir: Path) -> list[CitationRecord]:
    """Parse one JSON citation file into citation records."""
    import json

    raw = path.read_text(encoding="utf-8")
    loaded = json.loads(raw)
    entries = loaded if isinstance(loaded, list) else loaded.get("citations", [])
    if not isinstance(entries, list):
        raise ValueError(f"Expected a list or citations list in {path}")
    rel_path = path.relative_to(project_dir).as_posix()
    file_hash = sha256_file(path)
    records: list[CitationRecord] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "Untitled source").strip()
        key = str(entry.get("citation_id") or entry.get("id") or slugify(title) or f"citation-{index}")
        authors_raw = entry.get("authors", [])
        if isinstance(authors_raw, str):
            authors = _parse_authors(authors_raw)
        elif isinstance(authors_raw, list):
            authors = [str(author).strip() for author in authors_raw if str(author).strip()]
        else:
            authors = []
        records.append(
            _record(
                key=key,
                title=title,
                file_hash=file_hash,
                rel_path=rel_path,
                source_kind="json",
                authors=authors,
                year=str(entry.get("year")) if entry.get("year") else None,
                doi=str(entry.get("doi")) if entry.get("doi") else None,
                pmid=str(entry.get("pmid")) if entry.get("pmid") else None,
                pmcid=str(entry.get("pmcid")) if entry.get("pmcid") else None,
                arxiv_id=str(entry.get("arxiv_id")) if entry.get("arxiv_id") else None,
                url=str(entry.get("url")) if entry.get("url") else None,
                journal=str(entry.get("journal")) if entry.get("journal") else None,
                abstract=str(entry.get("abstract")) if entry.get("abstract") else None,
                notes="Parsed from JSON citation source. " + _metadata_notes(entry),
            )
        )
    return records


def parse_ris_file(path: Path, project_dir: Path) -> list[CitationRecord]:
    """Parse one RIS file into citation records."""
    text = path.read_text(encoding="utf-8")
    rel_path = path.relative_to(project_dir).as_posix()
    file_hash = sha256_file(path)
    entries = re.split(r"\nER\s*-\s*", text)
    records: list[CitationRecord] = []
    for index, entry_text in enumerate(entries, start=1):
        if not entry_text.strip():
            continue
        fields: dict[str, list[str]] = {}
        for line in entry_text.splitlines():
            match = re.match(r"^([A-Z0-9]{2})\s*-\s*(.*)$", line.strip())
            if match:
                fields.setdefault(match.group(1), []).append(match.group(2).strip())
        title = (fields.get("TI") or fields.get("T1") or [f"Untitled RIS source {index}"])[0]
        year_values = fields.get("PY") or fields.get("Y1") or []
        doi_values = fields.get("DO") or []
        journal_values = fields.get("JO") or fields.get("T2") or []
        url_values = fields.get("UR") or []
        year = year_values[0] if year_values else None
        doi = doi_values[0] if doi_values else None
        journal = journal_values[0] if journal_values else None
        url = url_values[0] if url_values else None
        authors = fields.get("AU", [])
        records.append(
            _record(
                key=f"ris-{index}",
                title=title,
                file_hash=file_hash,
                rel_path=rel_path,
                source_kind="ris",
                authors=authors,
                year=year[:4] if year else None,
                doi=doi,
                url=url,
                journal=journal,
                notes="Parsed from RIS source.",
            )
        )
    return records


def _clean_reference_line(line: str) -> str:
    """Remove common bullet or numbered-list prefixes from a source line."""
    return re.sub(r"^\s*(?:[-*]\s+|\d+[\.)]\s*)", "", line).strip()


def parse_text_reference_file(path: Path, project_dir: Path) -> list[CitationRecord]:
    """Parse plain-text DOI, PMID, URL, or manual source-list entries."""
    if is_scaffold_file(path):
        return []
    text = path.read_text(encoding="utf-8")
    rel_path = path.relative_to(project_dir).as_posix()
    file_hash = sha256_file(path)
    records: list[CitationRecord] = []
    for index, raw_line in enumerate(text.splitlines(), start=1):
        line = _clean_reference_line(raw_line)
        if not line or line.startswith("#"):
            continue
        doi_match = DOI_RE.search(line)
        pmid_match = PMID_RE.search(line)
        pmcid_match = PMCID_RE.search(line)
        arxiv_match = ARXIV_RE.search(line)
        url_match = URL_RE.search(line)
        year_match = re.search(r"\b(19|20)\d{2}\b", line)
        doi = doi_match.group(0).rstrip(".,;") if doi_match else None
        pmid = next((group for group in (pmid_match.groups() if pmid_match else []) if group), None)
        pmcid = pmcid_match.group(0).upper() if pmcid_match else None
        arxiv_id = arxiv_match.group(1) if arxiv_match else None
        url = url_match.group(0).rstrip(".,;") if url_match else None
        title = line
        if doi and line.lower().strip().startswith("doi"):
            title = f"DOI {doi}"
        elif pmid and re.fullmatch(r"PMID[:\s]*\d{5,9}", line, re.I):
            title = f"PMID {pmid}"
        else:
            title = DOI_RE.sub("", title)
            title = PMID_RE.sub("", title)
            title = PMCID_RE.sub("", title)
            title = ARXIV_RE.sub("", title)
            title = URL_RE.sub("", title)
            title = re.sub(r"\s+", " ", title).strip(" .;-") or line
        if doi and title == f"DOI {doi}":
            source_kind = "doi_list"
        elif pmid and title == f"PMID {pmid}":
            source_kind = "pmid_list"
        elif path.suffix.lower() == ".md":
            source_kind = "manual_list"
        else:
            source_kind = "plain_text"
        records.append(
            _record(
                key=f"{path.name}-{index}",
                title=title,
                file_hash=file_hash,
                rel_path=rel_path,
                source_kind=source_kind,
                year=year_match.group(0) if year_match else None,
                doi=doi,
                pmid=pmid,
                pmcid=pmcid,
                arxiv_id=arxiv_id,
                url=url,
                notes="Parsed from plain-text source list. Verify metadata before submission.",
            )
        )
    return records


def ingest_bibtex(project_dir: Path) -> list[CitationRecord]:
    """Ingest all supported citation source files from project inputs."""
    inputs = project_dir / "inputs"
    records: list[CitationRecord] = []
    for path in sorted(inputs.glob("*.bib")):
        records.extend(parse_bibtex_file(path, project_dir))
    for path in sorted(set(inputs.glob("citations*.json")) | set(inputs.glob("references.json"))):
        records.extend(parse_citation_json_file(path, project_dir))
    for path in sorted(inputs.glob("*.ris")):
        records.extend(parse_ris_file(path, project_dir))
    for name in ["references.txt", "citation_list.txt", "source_list.md"]:
        path = inputs / name
        if path.exists():
            records.extend(parse_text_reference_file(path, project_dir))
    return records
