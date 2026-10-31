"""Conservative quantitative surface matching without unit conversion.

Whole numeric tokens, attached signs, compound units, ranges and common qualifiers
are retained. These checks reject changed surfaces; they do not infer scientific
meaning, recover malformed measurements, or establish semantic entailment.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

_NUMBER = re.compile(
    r"(?<![\w.,])(?:[+\-−]\s*)?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)"
    r"(?:[eE][+\-−]?\s*\d+)?(?!\d|\.\d|,\d)"
)
_POWER = r"(?:\s*\^\s*[+\-−]?\d+|[−-]?\d+|[⁻⁺]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+)?"
_UNIT_PART = r"[A-Za-zµμ°%]+" + _POWER
_PAREN_UNIT = r"\([A-Za-zµμ°%0-9^+\-−*/·\s⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+\)"
_UNIT = re.compile(
    r"\s*("
    + _UNIT_PART
    + r"(?:\s*(?:[/·*]|\bper\b)\s*(?:"
    + _UNIT_PART
    + "|"
    + _PAREN_UNIT
    + r"))*(?:\s+[A-Za-zµμ°]+(?:\^[−-]\d+|[−-]\d+|⁻[⁰¹²³⁴⁵⁶⁷⁸⁹]+))*)"
)
_SI_SYMBOLS = frozenset(
    [
        "m",
        "s",
        "g",
        "kg",
        "mg",
        "µg",
        "μg",
        "ng",
        "L",
        "mL",
        "l",
        "ml",
        "N",
        "Pa",
        "kPa",
        "MPa",
        "J",
        "W",
        "mW",
        "MW",
        "V",
        "mV",
        "A",
        "mA",
        "K",
        "mol",
        "mmol",
        "Hz",
        "mm",
        "cm",
        "h",
        "min",
        "Hg",
    ]
)
_PERCENT_BASIS = re.compile(
    r"\s*(?:\(\s*)?(?:(?:w|v|m)/(?:w|v|m)|(?:mass|weight|volume)/(?:mass|weight|volume))(?:\s*\))?"
)
_NON_UNITS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "before",
        "between",
        "by",
        "compared",
        "contains",
        "describes",
        "equals",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "its",
        "less",
        "more",
        "no",
        "not",
        "of",
        "or",
        "per",
        "reads",
        "reported",
        "than",
        "that",
        "the",
        "then",
        "through",
        "to",
        "up",
        "was",
        "were",
        "when",
        "which",
        "while",
        "with",
        "without",
        "we",
    ]
)
_LOCATOR = re.compile(
    r"\b(?:sections?|fig(?:ure)?s?\.?|tables?|appendix|appendices)\s+[A-Za-z]?\s*$", re.I
)
_LOCATOR_SPAN = re.compile(
    r"\b(?:sections?|fig(?:ure)?s?\.?|tables?|appendix|appendices)\s+[A-Za-z]?\d+(?:\.\d+)*(?:\s*[-–—]\s*[A-Za-z]?\d+(?:\.\d+)*)?",
    re.I,
)
_NUMERIC_LIKE = re.compile(
    r"(?<![\w.,])(?:[+\-−]\s*)?(?:\d[\d.,]*|\.\d[\d.,]*)(?:[eE][+\-−]?\s*\d+)?"
)
_QUALIFIERS = (
    (
        "le",
        r"(?:<=|≤|\bat\s+most|\bup\s+to|\bno\s+more\s+than|\bless\s+than\s+or\s+equal\s+to)\s*$",
    ),
    ("ge", r"(?:>=|≥|\bat\s+least|\bno\s+less\s+than|\bgreater\s+than\s+or\s+equal\s+to)\s*$"),
    ("lt", r"(?:<|\bless\s+than|\bbelow|\bunder)\s*$"),
    ("gt", r"(?:>|\bgreater\s+than|\bmore\s+than|\babove|\bover)\s*$"),
    ("approx", r"(?:≈|~|\babout|\bapproximately|\broughly|\baround|\bnearly)\s*$"),
)


def canonical_number(raw: str) -> str:
    cleaned = re.sub(r"\s+", "", raw).replace(",", "").replace("−", "-")
    try:
        number = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError("Unsupported numeric token") from exc
    if not number.is_finite():
        raise ValueError("Non-finite numeric token")
    return str(number.normalize())


def canonical_unit(raw: str) -> str:
    """Whitespace/sign normalization only; SI prefix case and dimensions remain exact."""
    return re.sub(r"\s+", "", raw).replace("−", "-")


@dataclass(frozen=True)
class QuantityMention:
    values: tuple[str, ...]
    unit: str
    relation: str
    start: int
    end: int
    raw: str

    @property
    def signature(self) -> tuple[tuple[str, ...], str, str]:
        return self.values, self.unit, self.relation


@dataclass(frozen=True)
class _Atom:
    value: str
    unit: str
    relation: str
    start: int
    number_end: int
    end: int


def _read_unit(text: str, start: int, end: int) -> tuple[str, int]:
    match = _UNIT.match(text, end)
    if match:
        raw = match.group(1)
        if raw.casefold() not in _NON_UNITS:
            unit_end = match.end()
            if raw == "%":
                basis = _PERCENT_BASIS.match(text, unit_end)
                if basis:
                    raw += basis.group()
                    unit_end = basis.end()
            if raw in _SI_SYMBOLS:
                for _ in range(4):
                    following = _UNIT.match(text, unit_end)
                    if following is None or following.start(1) == unit_end:
                        break
                    first_symbol = re.match(r"[A-Za-zµμ°]+", following.group(1))
                    if first_symbol is None or first_symbol.group() not in _SI_SYMBOLS:
                        break
                    raw += " " + following.group(1)
                    unit_end = following.end()
            return canonical_unit(raw), unit_end
    prefix = re.search(r"([$€£])\s*$", text[:start])
    return (prefix.group(1), end) if prefix else ("", end)


def quantity_mentions(text: str, *, ignore_locators: bool = True) -> list[QuantityMention]:
    atoms: list[_Atom] = []
    consumed = 0
    scannable = (
        _LOCATOR_SPAN.sub(lambda match: " " * len(match.group()), text) if ignore_locators else text
    )
    for match in _NUMBER.finditer(scannable):
        start, number_end = match.span()
        if start < consumed:
            continue
        # Do not recover a numeric suffix from identifiers such as record-12.
        if (
            start
            and text[start - 1] in "-−"
            and start > 1
            and (text[start - 2].isalpha() or text[start - 2] == "_")
        ):
            continue
        prefix = text[max(0, start - 100) : start]
        if ignore_locators and _LOCATOR.search(prefix):
            continue
        if re.match(r"\s*[eE]\s*[+\-−]?\s*\d", text[number_end:]):
            continue  # A broken scientific-notation token is not a smaller measurement.
        unit, end = _read_unit(text, start, number_end)
        relation = "exact"
        for label, pattern in _QUALIFIERS:
            if re.search(pattern, prefix, re.I):
                relation = label
                break
        atoms.append(_Atom(canonical_number(match.group()), unit, relation, start, number_end, end))
        consumed = end
    results: list[QuantityMention] = []
    index = 0
    while index < len(atoms):
        first = atoms[index]
        if index + 1 < len(atoms):
            second = atoms[index + 1]
            gap = text[first.end : second.start]
            prefix = text[max(0, first.start - 30) : first.start]
            paired_relation: str | None = None
            if (
                re.fullmatch(r"\s*(?:-|–|—|to|through)\s*", gap, re.I)
                or re.search(r"\bbetween\s*$", prefix, re.I)
                and re.fullmatch(r"\s+and\s+", gap, re.I)
            ):
                paired_relation = "range"
            elif re.fullmatch(r"\s*(?:±|\+/-)\s*", gap):
                paired_relation = "uncertainty"
            elif re.fullmatch(r"\s*/\s*", gap):
                paired_relation = "fraction"
            if paired_relation:
                unit = second.unit or first.unit
                if first.unit and second.unit and first.unit != second.unit:
                    unit = first.unit + ".." + second.unit
                results.append(
                    QuantityMention(
                        (first.value, second.value),
                        unit,
                        paired_relation,
                        first.start,
                        second.end,
                        text[first.start : second.end],
                    )
                )
                index += 2
                continue
        results.append(
            QuantityMention(
                (first.value,),
                first.unit,
                first.relation,
                first.start,
                first.end,
                text[first.start : first.end],
            )
        )
        index += 1
    return results


def quantity_proposal_supported(value: str, unit: str | None, quote: str) -> bool | None:
    """Return None for nonnumeric text; quantitative proposals retain full relations."""
    proposed = quantity_mentions(value + (" " + unit if unit else ""), ignore_locators=False)
    if not proposed:
        return False if re.match(r"\s*[+\-−]?(?:\d|\.)", value) else None
    available = {mention.signature for mention in quantity_mentions(quote, ignore_locators=False)}
    return all(mention.signature in available for mention in proposed)


def unsupported_quantities(candidate: str, evidence: str) -> list[QuantityMention]:
    available = {mention.signature for mention in quantity_mentions(evidence)}
    return [
        mention for mention in quantity_mentions(candidate) if mention.signature not in available
    ]


def ambiguous_numeric_fragments(text: str, *, ignore_locators: bool = True) -> list[str]:
    """Find numeric-looking surfaces the whole-token parser could not account for."""
    scannable = (
        _LOCATOR_SPAN.sub(lambda match: " " * len(match.group()), text) if ignore_locators else text
    )
    mentions = quantity_mentions(text, ignore_locators=ignore_locators)
    fragments = []
    for match in _NUMERIC_LIKE.finditer(scannable):
        start = match.start()
        if (
            start
            and text[start - 1] in "-−"
            and start > 1
            and (text[start - 2].isalpha() or text[start - 2] == "_")
        ):
            continue
        raw = match.group().rstrip(".,")
        end = start + len(raw)
        if raw and not any(mention.start <= start and end <= mention.end for mention in mentions):
            fragments.append(raw)
    return fragments


def mask_known_citations(text: str, citations: Iterable[Mapping[str, Any]]) -> str:
    """Mask only known citation identifiers and author-year shapes, never arbitrary years."""
    masked = text
    for citation in citations:
        patterns: list[str] = []
        identity = str(citation.get("citation_id") or "")
        if identity:
            patterns.append(
                r"\[\s*" + re.escape(identity) + r"\s*\]"
                if identity.isdigit()
                else r"(?<!\w)" + re.escape(identity) + r"(?!\w)"
            )
        doi = citation.get("doi")
        if doi:
            patterns.append(re.escape(str(doi)))
        pmid = citation.get("pmid")
        if pmid:
            patterns.append(r"\bPMID\s*:?\s*" + re.escape(str(pmid)) + r"\b")
        year = str(citation.get("year") or "")
        if re.fullmatch(r"\d{4}", year):
            for author in citation.get("authors") or []:
                pieces = re.findall(r"[A-Za-z][A-Za-z'-]*", str(author).split(",")[0])
                if not pieces:
                    continue
                surname = re.escape(pieces[-1])
                name = surname + r"(?:\s+et\s+al\.?)?"
                patterns.extend(
                    [
                        r"\b" + name + r"\s*\(\s*" + year + r"[a-z]?\s*\)",
                        r"\(\s*" + name + r"\s*,?\s*" + year + r"[a-z]?\s*\)",
                    ]
                )
        for pattern in patterns:
            masked = re.sub(pattern, lambda match: " " * len(match.group()), masked, flags=re.I)
    return masked
