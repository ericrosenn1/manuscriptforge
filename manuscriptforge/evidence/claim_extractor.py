from __future__ import annotations

from pathlib import Path
from typing import Any

from manuscriptforge.evidence.strength_classifier import (
    allowed_language_for_support,
    classify_sentence_support,
    forbidden_language_for_support,
)
from manuscriptforge.models.claim import EvidenceItem, ScientificClaim, SupportStrength
from manuscriptforge.utils.ids import stable_id
from manuscriptforge.utils.text import normalize_space, split_sentences

P_VALUE_COLUMNS = [
    "adjusted_p_value",
    "padj",
    "q_value",
    "qvalue",
    "fdr",
    "p_adj",
    "p_value",
    "pvalue",
    "p",
]
EFFECT_COLUMNS = [
    "log2_fold_change",
    "log2fc",
    "fold_change",
    "effect_size",
    "estimate",
    "difference",
]
DESCRIPTOR_COLUMNS = ["marker", "gene", "feature", "comparison", "outcome", "group", "contrast"]
SIGNIFICANCE_THRESHOLD = 0.05


def _as_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_value(value: Any) -> str:
    number = _as_float(value)
    if number is None:
        return str(value)
    if abs(number) < 0.001 and number != 0:
        return f"{number:.2e}"
    return f"{number:.3g}"


def _role_column(role_columns: dict[str, list[str]], roles: list[str]) -> str | None:
    for role in roles:
        columns = role_columns.get(role, [])
        if columns:
            return columns[0]
    return None


def _matching_column(
    row: dict[str, Any], candidates: list[str], role_columns: dict[str, list[str]] | None = None
) -> str | None:
    role_columns = role_columns or {}
    role_match = _role_column(role_columns, candidates)
    if role_match and role_match in row:
        return role_match
    lower_to_key = {str(key).lower(): str(key) for key in row}
    for candidate in candidates:
        if candidate in lower_to_key:
            return lower_to_key[candidate]
    return None


def _descriptor(row: dict[str, Any], role_columns: dict[str, list[str]] | None = None) -> str:
    role_columns = role_columns or {}
    parts = []
    for role in ["feature", "comparison", "group", "direction", "figure_reference"]:
        column = _role_column(role_columns, [role])
        if column and row.get(column) not in {None, ""}:
            parts.append(f"{role.replace('_', ' ')} {row[column]}")
    lower_to_key = {str(key).lower(): str(key) for key in row}
    for candidate in DESCRIPTOR_COLUMNS:
        key = lower_to_key.get(candidate)
        if key and row.get(key) not in {None, ""} and f"{candidate.replace('_', ' ')} {row[key]}" not in parts:
            parts.append(f"{candidate.replace('_', ' ')} {row[key]}")
    if parts:
        return ", ".join(parts[:3])
    for key, value in row.items():
        if _as_float(value) is None and value not in {None, ""}:
            parts.append(f"{key} {value}")
    return ", ".join(parts[:3]) or "the reported row"


def _detect_group_mean_columns(row: dict[str, Any]) -> tuple[str | None, str | None]:
    keys = [str(key) for key in row]
    response = next((key for key in keys if "response" in key.lower() and "mean" in key.lower()), None)
    nonresponse = next(
        (
            key
            for key in keys
            if ("nonresponse" in key.lower() or "non_response" in key.lower())
            and "mean" in key.lower()
        ),
        None,
    )
    if response == nonresponse:
        nonresponse = None
    return response, nonresponse


def _threshold_for_column(column: str | None, thresholds: dict[str, float]) -> float:
    if not column:
        return SIGNIFICANCE_THRESHOLD
    return thresholds.get(column) or thresholds.get("p_value") or thresholds.get("adjusted_p_value") or SIGNIFICANCE_THRESHOLD


def _row_claim_text(
    table_label: str,
    row: dict[str, Any],
    numeric_columns: list[str],
    role_columns: dict[str, list[str]] | None = None,
    thresholds: dict[str, float] | None = None,
) -> str:
    role_columns = role_columns or {}
    thresholds = thresholds or {}
    descriptor = _descriptor(row, role_columns)
    p_col = _matching_column(row, P_VALUE_COLUMNS + ["p_value", "adjusted_p_value"], role_columns)
    effect_col = _matching_column(row, EFFECT_COLUMNS + ["effect_size", "estimate"], role_columns)
    response_col, nonresponse_col = _detect_group_mean_columns(row)
    response_value = _as_float(row.get(response_col)) if response_col else None
    nonresponse_value = _as_float(row.get(nonresponse_col)) if nonresponse_col else None

    parts: list[str] = []
    if response_col and nonresponse_col and response_value is not None and nonresponse_value is not None:
        direction = "higher" if response_value > nonresponse_value else "lower"
        parts.append(
            f"{response_col} was {direction} than {nonresponse_col} "
            f"({_format_value(response_value)} vs {_format_value(nonresponse_value)})"
        )
    if effect_col and row.get(effect_col) not in {None, ""}:
        parts.append(f"{effect_col} was {_format_value(row[effect_col])}")
    if p_col and row.get(p_col) not in {None, ""}:
        p_value = _as_float(row[p_col])
        threshold = _threshold_for_column(p_col, thresholds)
        threshold_text = ""
        if p_value is not None:
            threshold_text = (
                f", {'meeting' if p_value <= threshold else 'not meeting'} "
                f"the <= {threshold} threshold"
            )
        parts.append(f"{p_col} was {_format_value(row[p_col])}{threshold_text}")
    if not parts:
        parts = [
            f"{column} was {_format_value(row[column])}"
            for column in numeric_columns
            if row.get(column) not in {None, ""}
        ]
    if parts:
        return f"In {table_label}, {descriptor} had " + ", ".join(parts) + "."
    return f"The {table_label} table reports {descriptor}."


def _row_notes(
    row: dict[str, Any],
    role_columns: dict[str, list[str]] | None = None,
    thresholds: dict[str, float] | None = None,
) -> str:
    p_col = _matching_column(row, P_VALUE_COLUMNS + ["p_value", "adjusted_p_value"], role_columns)
    effect_col = _matching_column(row, EFFECT_COLUMNS + ["effect_size", "estimate"], role_columns)
    notes = ["Generated from a result table row; verify table labels and analysis context."]
    if p_col:
        p_value = _as_float(row.get(p_col))
        if p_value is not None:
            threshold = _threshold_for_column(p_col, thresholds or {})
            notes.append(
                f"{p_col}={_format_value(p_value)} "
                f"({'below' if p_value <= threshold else 'above'} "
                f"the {threshold} threshold)."
            )
    if effect_col:
        notes.append(f"Effect column detected: {effect_col}.")
    return " ".join(notes)


def _aggregate_claims_for_table(
    table: dict[str, Any],
    summary_evidence: EvidenceItem,
) -> list[ScientificClaim]:
    rows = table.get("rows", [])
    schema = table.get("schema") or {}
    claim_generation = schema.get("claim_generation", {}) if isinstance(schema, dict) else {}
    if claim_generation.get("generate_table_summary_claims") is False:
        return []
    claims: list[ScientificClaim] = [
        _claim_from_text(
            f"The {table['source_label']} table contains {table.get('row_count', 0)} result row(s) and {len(table.get('columns', []))} column(s).",
            "Results",
            summary_evidence,
            "result",
            "table",
            support_override="direct",
            needs_human_review=False,
            notes="Table-level inventory claim generated from file structure.",
        )
    ]
    if not rows:
        return claims
    p_values: list[tuple[str, float, dict[str, Any], float]] = []
    effects: list[tuple[str, float, dict[str, Any]]] = []
    role_columns = table.get("role_columns", {})
    thresholds = table.get("schema", {}).get("thresholds", {}) if table.get("schema") else {}
    for row in rows:
        p_col = _matching_column(row, P_VALUE_COLUMNS + ["p_value", "adjusted_p_value"], role_columns)
        effect_col = _matching_column(row, EFFECT_COLUMNS + ["effect_size", "estimate"], role_columns)
        p_value = _as_float(row.get(p_col)) if p_col else None
        effect_value = _as_float(row.get(effect_col)) if effect_col else None
        if p_col and p_value is not None:
            p_values.append((p_col, p_value, row, _threshold_for_column(p_col, thresholds)))
        if effect_col and effect_value is not None:
            effects.append((effect_col, effect_value, row))
    if p_values:
        significant = [item for item in p_values if item[1] <= item[3]]
        p_col, _, _, threshold = p_values[0]
        claims.append(
            _claim_from_text(
                f"In {table['source_label']}, {len(significant)} of {len(p_values)} row(s) met the {p_col} <= {threshold} threshold.",
                "Results",
                summary_evidence,
                "result",
                "table",
                support_override="direct",
                needs_human_review=False,
                notes="Aggregate threshold claim generated from detected p-value column.",
            )
        )
    if effects:
        effect_col, effect_value, row = max(effects, key=lambda item: abs(item[1]))
        claims.append(
            _claim_from_text(
                f"In {table['source_label']}, {_descriptor(row, role_columns)} had the largest absolute {effect_col} ({_format_value(effect_value)}) among the table rows.",
                "Results",
                summary_evidence,
                "result",
                "table",
                support_override="direct",
                needs_human_review=False,
                notes="Aggregate effect-size claim generated from detected effect column.",
            )
        )
    return claims


def _claim_from_text(
    text: str,
    section_target: str,
    evidence_item: EvidenceItem,
    claim_type: str,
    source_kind: str,
    needs_citation: bool = False,
    support_override: SupportStrength | None = None,
    needs_human_review: bool | None = None,
    notes: str | None = None,
) -> ScientificClaim:
    claim_text = normalize_space(text)
    support = support_override or classify_sentence_support(source_kind, claim_text)
    return ScientificClaim(
        claim_id=stable_id("clm", claim_text + evidence_item.content_hash),
        claim_text=claim_text,
        normalized_claim=claim_text.lower(),
        section_target=section_target,
        evidence_items=[evidence_item],
        evidence_type=evidence_item.evidence_type,
        support_strength=support,
        claim_type=claim_type,  # type: ignore[arg-type]
        allowed_language=allowed_language_for_support(support),
        forbidden_language=forbidden_language_for_support(support),
        citation_ids=[],
        needs_citation=needs_citation,
        needs_human_review=needs_human_review
        if needs_human_review is not None
        else support in {"weak", "unsupported", "needs_review"},
        notes=notes or "Generated from supplied project input; verify wording and scope.",
    )


def claims_from_table_summaries(
    tables: list[dict[str, Any]], evidence_items: list[EvidenceItem]
) -> list[ScientificClaim]:
    claims: list[ScientificClaim] = []
    evidence_by_locator = {
        (item.metadata.get("table_id"), item.metadata.get("row_index")): item
        for item in evidence_items
        if item.evidence_type == "table"
    }
    summary_evidence = {
        item.metadata.get("table_id"): item
        for item in evidence_items
        if item.evidence_type == "table" and item.locator == "table_summary"
    }
    for table in tables:
        table_id = table["table_id"]
        rows = table.get("rows", [])
        numeric_columns = table.get("numeric_columns", [])
        schema = table.get("schema") or {}
        role_columns = table.get("role_columns", {})
        thresholds = schema.get("thresholds", {}) if isinstance(schema, dict) else {}
        claim_generation = schema.get("claim_generation", {}) if isinstance(schema, dict) else {}
        generate_rows = claim_generation.get("generate_row_claims", True)
        max_row_claims = int(claim_generation.get("max_row_claims", 25))
        if table_id in summary_evidence:
            claims.extend(_aggregate_claims_for_table(table, summary_evidence[table_id]))
        if not generate_rows:
            continue
        for index, row in enumerate(rows[:25], start=1):
            if index > max_row_claims:
                break
            evidence = evidence_by_locator.get((table_id, index)) or summary_evidence.get(table_id)
            if evidence is None:
                continue
            text = _row_claim_text(table["source_label"], row, numeric_columns, role_columns, thresholds)
            claims.append(
                _claim_from_text(
                    text,
                    "Results",
                    evidence,
                    "result",
                    "table",
                    support_override="direct",
                    needs_human_review=False,
                    notes=_row_notes(row, role_columns, thresholds),
                )
            )
    return claims


def claims_from_text_evidence(evidence_items: list[EvidenceItem]) -> list[ScientificClaim]:
    claims: list[ScientificClaim] = []
    for item in evidence_items:
        if item.evidence_type == "table" or item.evidence_type == "source_pdf":
            continue
        sentences = split_sentences(item.text) or [item.text]
        for sentence in sentences[:20]:
            if not sentence.strip():
                continue
            if item.evidence_type == "method_note":
                section, claim_type, needs_citation = "Methods", "method", False
            elif item.evidence_type == "figure":
                section, claim_type, needs_citation = "Figure Legends", "result", False
            elif item.evidence_type == "interpretation_note":
                lower = sentence.lower()
                if "limitation" in lower or "need" in lower or "avoid" in lower:
                    section, claim_type = "Limitations", "limitation"
                else:
                    section, claim_type = "Discussion", "interpretation"
                needs_citation = False
            else:
                section, claim_type, needs_citation = "Introduction", "background", True
            claims.append(
                _claim_from_text(
                    sentence,
                    section,
                    item,
                    claim_type,
                    item.evidence_type,
                    needs_citation=needs_citation,
                )
            )
    return claims


def extract_claims(
    project_dir: Path,
    ingested: dict[str, Any],
) -> list[ScientificClaim]:
    del project_dir
    evidence_items = [
        item if isinstance(item, EvidenceItem) else EvidenceItem.model_validate(item)
        for item in ingested.get("evidence_items", [])
    ]
    claims = claims_from_table_summaries(ingested.get("tables", []), evidence_items)
    claims.extend(claims_from_text_evidence(evidence_items))
    deduped: dict[str, ScientificClaim] = {}
    for claim in claims:
        deduped.setdefault(claim.claim_id, claim)
    return list(deduped.values())
