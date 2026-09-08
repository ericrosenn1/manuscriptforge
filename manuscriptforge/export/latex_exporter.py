from __future__ import annotations

from pathlib import Path

from manuscriptforge.models.manuscript import Manuscript
from manuscriptforge.utils.io import write_text

LATEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def latex_escape(text: str) -> str:
    return "".join(LATEX_ESCAPES.get(char, char) for char in text)


def manuscript_to_latex(manuscript: Manuscript) -> str:
    lines = [
        r"\documentclass[11pt]{article}",
        r"\usepackage[margin=1in]{geometry}",
        r"\usepackage{hyperref}",
        r"\begin{document}",
        rf"\title{{{latex_escape(manuscript.title)}}}",
        r"\author{Human author to confirm}",
        r"\date{\today}",
        r"\maketitle",
    ]
    if manuscript.abstract:
        lines.extend([r"\begin{abstract}", latex_escape(manuscript.abstract), r"\end{abstract}"])
    if manuscript.title_candidates:
        lines.extend([r"\section*{Title Candidates}", r"\begin{itemize}"])
        for candidate in manuscript.title_candidates:
            lines.append(r"\item " + latex_escape(candidate))
        lines.append(r"\end{itemize}")
    for section in manuscript.sections:
        if section.section_name.lower() == "abstract":
            continue
        lines.extend([rf"\section{{{latex_escape(section.section_name)}}}", latex_escape(section.content)])
        if section.claim_ids or section.citation_ids:
            lines.append(
                latex_escape(
                    "Traceability: "
                    + f"claims={', '.join(section.claim_ids) or 'none'}; "
                    + f"citations={', '.join(section.citation_ids) or 'none'}"
                )
            )
        if section.unresolved_flags:
            lines.extend([r"\paragraph{Unresolved Flags}", r"\begin{itemize}"])
            for flag in section.unresolved_flags:
                lines.append(r"\item " + latex_escape(flag))
            lines.append(r"\end{itemize}")
    if manuscript.tables:
        lines.extend([r"\section{Table Inventory}", r"\begin{itemize}"])
        for table in manuscript.tables:
            lines.append(
                r"\item "
                + latex_escape(
                    f"{table.get('source_label', 'table')}: {table.get('row_count', 0)} row(s), columns {', '.join(table.get('columns', []))}."
                )
            )
        lines.append(r"\end{itemize}")
    if manuscript.figure_legends:
        lines.append(r"\section{Figure Legends}")
        for legend in manuscript.figure_legends:
            lines.append(latex_escape(legend) + r"\\")
    if manuscript.references:
        lines.extend([r"\section{References}", r"\begin{enumerate}"])
        for citation in manuscript.references:
            authors = ", ".join(citation.authors) if citation.authors else "Unknown authors"
            year = citation.year or "n.d."
            lines.append(
                r"\item "
                + latex_escape(
                    f"{authors} ({year}). {citation.title}. {citation.journal or ''}"
                    + (f" doi:{citation.doi}" if citation.doi else "")
                )
            )
        lines.append(r"\end{enumerate}")
    lines.extend([r"\appendix", r"\section{Audit Traceability Appendix}", r"\begin{itemize}"])
    for section in manuscript.sections:
        lines.append(
            r"\item "
            + latex_escape(
                f"{section.section_name}: claims={', '.join(section.claim_ids) or 'none'}; citations={', '.join(section.citation_ids) or 'none'}"
            )
        )
    lines.append(r"\end{itemize}")
    lines.extend([r"\end{document}", ""])
    return "\n\n".join(lines)


def export_latex(path: Path, manuscript: Manuscript) -> None:
    write_text(path, manuscript_to_latex(manuscript))
