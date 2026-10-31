"""Conservative numeric surface checks, not semantic entailment validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

NUMBER_RE = re.compile(
    r"(?<![\w])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?\s*%?(?![\w])"
)
UNIT_RE = re.compile(
    r"^\s*(percent|minutes?|mins?|hours?|seconds?|records?|samples?|participants?|mg|kg|ml|mm|cm|miles?)\b",
    re.I,
)
GROUP_RE = re.compile(r"\b(group|cohort|arm|batch|collection)\s+([\w-]+)", re.I)


@dataclass(frozen=True)
class NumericMention:
    value: str
    unit: str
    group: str
    raw: str


def numeric_mentions(text: str) -> list[NumericMention]:
    values = []
    for match in NUMBER_RE.finditer(text):
        raw = match.group().strip()
        unit = "%" if raw.endswith("%") else ""
        cleaned = raw.rstrip("%").strip().replace(",", "")
        try:
            value = str(Decimal(cleaned).normalize())
        except InvalidOperation:
            value = cleaned
        following = UNIT_RE.match(text[match.end() :])
        if following:
            unit = following.group(1).casefold()
            unit = {"percent": "%", "min": "minute", "mins": "minute"}.get(unit, unit.rstrip("s"))
        prefix = text[max(0, match.start() - 140) : match.start()]
        # Associate explicit group labels only; do not pretend to infer arbitrary subjects.
        groups = list(GROUP_RE.finditer(prefix))
        group = " ".join(groups[-1].groups()).casefold() if groups else ""
        values.append(NumericMention(value, unit, group, raw))
    return values
