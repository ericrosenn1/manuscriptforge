from __future__ import annotations

from pathlib import Path

from manuscriptforge.models.manuscript import Manuscript
from manuscriptforge.utils.io import write_text


def manuscript_to_markdown(manuscript: Manuscript) -> str:
    lines = [f"# {manuscript.title}", ""]
    if manuscript.title_candidates:
        lines.extend(["## Title Candidates", ""])
        lines.extend(f"- {candidate}" for candidate in manuscript.title_candidates)
        lines.append("")
    if manuscript.abstract:
        lines.extend(["## Abstract", "", manuscript.abstract.strip(), ""])
    for section in manuscript.sections:
        if section.section_name.lower() == "abstract":
            continue
        lines.extend([f"## {section.section_name}", "", section.content.strip(), ""])
        if section.claim_ids or section.citation_ids:
            lines.append("### Traceability")
            if section.claim_ids:
                lines.append(f"- Claims: {', '.join(section.claim_ids)}")
            if section.citation_ids:
                lines.append(f"- Citations: {', '.join(section.citation_ids)}")
            lines.append("")
        if section.unresolved_flags:
            lines.append("**Unresolved flags:**")
            lines.extend(f"- {flag}" for flag in section.unresolved_flags)
            lines.append("")
        if section.revision_notes:
            lines.append("**Revision notes:**")
            lines.extend(f"- {note}" for note in section.revision_notes)
            lines.append("")
    if manuscript.tables:
        lines.extend(["## Table Inventory", ""])
        lines.append("| Table | Rows | Columns | Source |")
        lines.append("| --- | ---: | --- | --- |")
        for table in manuscript.tables:
            columns = ", ".join(table.get("columns", []))
            lines.append(
                f"| {table.get('source_label', 'table')} | {table.get('row_count', 0)} | {columns} | {table.get('source_path', '')} |"
            )
        lines.append("")
    if manuscript.figure_legends:
        lines.extend(["## Figure Legends", ""])
        lines.extend(f"- {legend}" for legend in manuscript.figure_legends)
        lines.append("")
    if manuscript.references:
        lines.extend(["## References", ""])
        for index, citation in enumerate(manuscript.references, start=1):
            authors = ", ".join(citation.authors) if citation.authors else "Unknown authors"
            year = citation.year or "n.d."
            journal = f" {citation.journal}." if citation.journal else ""
            doi = f" doi:{citation.doi}" if citation.doi else ""
            lines.append(f"{index}. {authors} ({year}). {citation.title}.{journal}{doi}")
        lines.append("")
    if manuscript.ai_disclosure:
        lines.extend(["## AI-Use Disclosure Draft", "", manuscript.ai_disclosure.strip(), ""])
    lines.extend(["## Source Traceability Appendix", ""])
    for section in manuscript.sections:
        lines.append(f"### {section.section_name}")
        lines.append(f"- Claim IDs: {', '.join(section.claim_ids) or 'none'}")
        lines.append(f"- Citation IDs: {', '.join(section.citation_ids) or 'none'}")
        lines.append("")
    unresolved = [
        (section.section_name, flag)
        for section in manuscript.sections
        for flag in section.unresolved_flags
    ]
    lines.extend(["## Unresolved Flags Appendix", ""])
    if unresolved:
        for section_name, flag in unresolved:
            lines.append(f"- {section_name}: {flag}")
    else:
        lines.append("No unresolved flags were attached to manuscript sections.")
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def export_markdown(path: Path, manuscript: Manuscript) -> None:
    write_text(path, manuscript_to_markdown(manuscript))
