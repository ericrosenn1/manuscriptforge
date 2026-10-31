"""Bounded row association checks, without field aliases or semantic entailment.

Only explicit ingested row metadata supplies values. Exact entity labels scope
simple clauses; unsupported grammar and ambiguous labels remain unevaluated.
Matching an unlabeled number to a row does not establish its field or unit.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from manuscriptforge.evidence.quantities import canonical_number, quantity_mentions
from manuscriptforge.models.claim import EvidenceItem


class TableRowBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=1)
    table_id: str = Field(min_length=1)
    row_index: int = Field(ge=1, strict=True)
    entity_labels: dict[str, str]
    numeric_values: dict[str, str]

    @field_validator("entity_labels", "numeric_values")
    @classmethod
    def nonempty_mapping(cls, value: dict[str, str]) -> dict[str, str]:
        if not value or any(not key.strip() or not item.strip() for key, item in value.items()):
            raise ValueError("Row bindings require nonempty exact fields and values")
        return value

    @field_validator("numeric_values")
    @classmethod
    def finite_numbers(cls, value: dict[str, str]) -> dict[str, str]:
        return {key: canonical_number(item) for key, item in value.items()}

    @model_validator(mode="after")
    def distinct_fields(self) -> TableRowBinding:
        if self.entity_labels.keys() & self.numeric_values.keys():
            raise ValueError("A row field cannot be both entity and numeric")
        return self


class TableBindingReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["consistent", "inconsistent", "not_evaluated"]
    issues: list[str] = Field(default_factory=list)
    evaluated_mentions: int = 0
    review_reasons: list[str] = Field(default_factory=list)
    limitation: str = (
        "Checks only explicit row/value associations in supported clauses; "
        "does not infer field aliases, units, word numbers, or semantic entailment."
    )


def derive_table_bindings(evidence_items: Sequence[EvidenceItem]) -> list[TableRowBinding]:
    """Use typed cells from ingested row metadata, never parse rendered evidence prose.

    Numeric-looking strings remain text labels; booleans, nulls, nested values,
    invalid row identities, and nonfinite numeric rows cannot establish bindings.
    """
    bindings: list[TableRowBinding] = []
    seen: set[str] = set()
    for evidence in evidence_items:
        if evidence.evidence_type != "table" or evidence.evidence_id in seen:
            continue
        metadata = evidence.metadata
        row = metadata.get("row")
        index = metadata.get("row_index")
        identity = metadata.get("table_id")
        if (
            not isinstance(row, dict)
            or not isinstance(index, int)
            or isinstance(index, bool)
            or index < 1
            or not isinstance(identity, str)
            or not identity.strip()
        ):
            continue
        labels: dict[str, str] = {}
        numbers: dict[str, str] = {}
        invalid = False
        for key, value in row.items():
            if not isinstance(key, str) or not key.strip():
                invalid = True
                break
            if isinstance(value, str) and value.strip():
                # Numeric-only identifiers cannot safely anchor prose clauses.
                if not re.fullmatch(r"[+\-\d.,\s]+", value):
                    labels[key] = value
            elif isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
                if not Decimal(str(value)).is_finite():
                    invalid = True
                    break
                numbers[key] = canonical_number(str(value))
        if labels and numbers and not invalid:
            bindings.append(
                TableRowBinding(
                    evidence_id=evidence.evidence_id,
                    table_id=identity,
                    row_index=index,
                    entity_labels=labels,
                    numeric_values=numbers,
                )
            )
            seen.add(evidence.evidence_id)
    return bindings


def _literal(value: str) -> str:
    return r"(?<!\w)" + re.escape(value) + r"(?!\w)"


_PREDICATE = re.compile(
    r"\s*(?:had|has|have|contains|contained|comprises|comprised|was|is|reported)\b",
    re.I,
)
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)|[\r\n]")
_OTHER_SCOPE = re.compile(
    r"\b(?:respectively|versus|compared|overall|total|difference|ratio|after|before|during|among)\b"
    r"|\bout\s+of\b|\bof\s+the\b",
    re.I,
)


def _threshold_reference(text: str, start: int, end: int) -> bool:
    """Identify explicit cutoff context, not the numeric value itself.

    Thresholds are analysis settings, not necessarily observed row cells. Their
    actual values remain subject to the independent quantitative source guard.
    """
    if re.search(r"\b(?:threshold|cutoff)s?\b", text[start:end], re.I):
        return True
    if re.match(
        r"\s+(?:as\s+(?:the\s+)?)?(?:(?:significance|configured)\s+)?(?:threshold|cutoff)s?\b",
        text[end:],
        re.I,
    ):
        return True
    # A forward threshold word must directly introduce this number, rather
    # than appear in an earlier clause such as "threshold: ... A had 12".
    prefix = text[max(0, start - 80) : start]
    return bool(
        re.search(
            r"\b(?:threshold|cutoff)s?\s*(?:(?:of|at|was|is)\s*)?(?:[=:<>≤≥]+\s*)?$",
            prefix,
            re.I,
        )
    )


def check_table_bindings(
    candidate_text: str, bindings: Sequence[TableRowBinding]
) -> TableBindingReport:
    """Reject observable row swaps; explicitly disclose incomplete checks.

    Supported owners include ``collection A had ...``, ``A had ...``, and
    ``collection=A; exact_field=...``. Compound subjects, pronouns, respectively
    lists, comparative/subordinate scopes, and nonunique labels are not resolved.
    Exact field assignments additionally check the numeric field. Otherwise only
    row membership is checked, with field association retained for human review.
    """
    reasons: list[str] = []
    issues: list[str] = []
    if not bindings:
        return TableBindingReport(
            status="not_evaluated", review_reasons=["No explicit row metadata bindings supplied"]
        )
    # Every exact label occurrence is a boundary, including unsupported owners.
    # This prevents a preceding row from accidentally absorbing another row's numbers.
    events: dict[tuple[int, int], list[tuple[int, bool]]] = {}
    for row_id, binding in enumerate(bindings):
        for field, label in binding.entity_labels.items():
            for match in re.finditer(_literal(label), candidate_text):
                prefix = candidate_text[: match.start()]
                following = candidate_text[match.end() :]
                qualified = re.search(_literal(field) + r"\s*(?:=|:)?\s*$", prefix, re.I)
                explicit_fields = "|".join(_literal(key) for key in binding.numeric_values)
                field_follows = re.match(
                    rf"\s*[;,:]\s*(?:{explicit_fields})\s*(?:=|:|was\b|is\b)",
                    following,
                    re.I,
                )
                recognized = bool(_PREDICATE.match(following) or (qualified and field_follows))
                events.setdefault(match.span(), []).append((row_id, recognized))
    quantities = quantity_mentions(candidate_text)
    covered: set[tuple[int, int]] = set()
    evaluated = 0
    ordered = sorted(events)
    for index, (start, label_end) in enumerate(ordered):
        stop = ordered[index + 1][0] if index + 1 < len(ordered) else len(candidate_text)
        sentence = _SENTENCE_END.search(candidate_text, label_end, stop)
        if sentence:
            stop = sentence.start()
        local = [q for q in quantities if label_end <= q.start and q.end <= stop]
        if not local:
            continue
        candidates = {row_id for row_id, recognized in events[(start, label_end)] if recognized}
        if len(candidates) != 1:
            reasons.append("Exact row label has ambiguous identity or unsupported owner grammar")
            continue
        binding = bindings[next(iter(candidates))]
        segment = candidate_text[label_end:stop]
        # A preceding unresolved subject joined by and/or makes the ownership unclear.
        compound = False
        if index:
            previous_start, previous_end = ordered[index - 1]
            between = candidate_text[previous_end:start]
            compound = bool(re.fullmatch(r"\s*(?:,?\s*(?:and|or)|,)\s*", between, re.I))
        if compound or _OTHER_SCOPE.search(segment):
            reasons.append("Compound, comparative, grouped, or subordinate row scope not evaluated")
            continue
        available = set(binding.numeric_values.values())
        for quantity in local:
            before_number = candidate_text[label_end : quantity.start]
            exact_fields = [
                field
                for field in binding.numeric_values
                if re.search(
                    _literal(field) + r"\s*(?:=|:|was\b|is\b)\s*$",
                    before_number,
                    re.I,
                )
            ]
            if not exact_fields and _threshold_reference(
                candidate_text, quantity.start, quantity.end
            ):
                reasons.append(
                    "Explicit threshold context is outside observed row-value binding; "
                    "its value requires the independent quantitative source check"
                )
                continue
            if len(exact_fields) == 1:
                allowed = {binding.numeric_values[exact_fields[0]]}
            else:
                allowed = available
                reasons.append(
                    "Numeric field association is not evaluated without an exact field name"
                )
            evaluated += 1
            covered.add((quantity.start, quantity.end))
            if any(value not in allowed for value in quantity.values):
                field_note = f", field {exact_fields[0]!r}" if len(exact_fields) == 1 else ""
                issues.append(
                    f"Row/value mismatch for evidence {binding.evidence_id!r}, "
                    f"table {binding.table_id!r}, row {binding.row_index}{field_note}: "
                    f"{quantity.raw!r} is absent from the explicit row binding"
                )
    if len(covered) < len(quantities):
        reasons.append("Some numeric mentions could not be assigned to one explicit row")
    if not evaluated:
        reasons.append("No supported row-scoped numeric mentions evaluated")
    unique_reasons = list(dict.fromkeys(reasons))
    return TableBindingReport(
        status="inconsistent" if issues else "not_evaluated" if unique_reasons else "consistent",
        issues=list(dict.fromkeys(issues)),
        evaluated_mentions=evaluated,
        review_reasons=unique_reasons,
    )
