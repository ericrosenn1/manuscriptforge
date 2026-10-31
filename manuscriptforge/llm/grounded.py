"""Typed, source-bound candidate drafting with deterministic provenance checks.

These guards establish referential and numeric consistency, not semantic entailment.
Every model-generated candidate remains subject to expert review.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from manuscriptforge.evidence.quantities import (
    ambiguous_numeric_fragments,
    mask_known_citations,
    unsupported_quantities,
)
from manuscriptforge.llm.base import LLMProvider, LLMResponseError
from manuscriptforge.llm.prompts import GROUNDED_SECTION_SYSTEM
from manuscriptforge.models.claim import SupportStrength
from manuscriptforge.utils.hashing import sha256_text
from manuscriptforge.utils.text import word_tokens

if TYPE_CHECKING:
    from manuscriptforge.pipeline.run_context import ManuscriptContext


class GroundedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GroundedEvidence(GroundedModel):
    evidence_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source_path: str
    content_hash: str


class GroundedFact(GroundedModel):
    subject: str
    predicate: str
    value: str
    unit: str | None = None


class GroundedClaim(GroundedModel):
    claim_id: str = Field(min_length=1)
    claim_text: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    citation_ids: list[str] = Field(default_factory=list)
    support_strength: SupportStrength = "needs_review"
    fact: GroundedFact | None = None


class GroundedCitation(GroundedModel):
    citation_id: str = Field(min_length=1)
    title: str
    authors: list[str] = Field(default_factory=list)
    year: str | None = None
    doi: str | None = None
    pmid: str | None = None


class StyleExample(GroundedModel):
    chunk_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source_file: str
    content_hash: str


class GroundedSectionRequest(GroundedModel):
    section_name: str = Field(min_length=1, max_length=100)
    instructions: str = "Write a concise section using only the supplied evidence."
    claims: list[GroundedClaim] = Field(default_factory=list)
    evidence: list[GroundedEvidence] = Field(default_factory=list)
    citations: list[GroundedCitation] = Field(default_factory=list)
    style_examples: list[StyleExample] = Field(default_factory=list)
    max_words: int = Field(default=800, ge=1, le=5000)
    max_style_overlap_words: int = Field(default=20, ge=5, le=100)

    @model_validator(mode="after")
    def check_references(self) -> GroundedSectionRequest:
        for ids, field in [
            ([item.claim_id for item in self.claims], "claim_id"),
            ([item.evidence_id for item in self.evidence], "evidence_id"),
            ([item.citation_id for item in self.citations], "citation_id"),
            ([item.chunk_id for item in self.style_examples], "chunk_id"),
        ]:
            if len(ids) != len(set(ids)):
                raise ValueError(f"Duplicate {field} values are not allowed")
        evidence_ids = {item.evidence_id for item in self.evidence}
        citation_ids = {item.citation_id for item in self.citations}
        for claim in self.claims:
            if not set(claim.evidence_ids) <= evidence_ids:
                raise ValueError("A claim references evidence not present in the request")
            if not set(claim.citation_ids) <= citation_ids:
                raise ValueError("A claim references citations not present in the request")
        return self


class GroundedParagraph(GroundedModel):
    text: str = Field(min_length=1)
    claim_ids: list[str] = Field(min_length=1)
    citation_ids: list[str] = Field(default_factory=list)
    style_chunk_ids: list[str] = Field(default_factory=list)


class GroundedAssertion(GroundedFact):
    claim_id: str
    evidence_id: str


class GroundedSectionResponse(GroundedModel):
    section_name: str = Field(min_length=1)
    paragraphs: list[GroundedParagraph] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    assertions: list[GroundedAssertion] = Field(default_factory=list)
    ranked_evidence_ids: list[str] = Field(default_factory=list)
    requires_human_review: Literal[True] = True


class GroundedValidationReport(GroundedModel):
    valid: bool
    request_hash: str
    issues: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    requires_human_review: Literal[True] = True
    quantity_protocol: Literal["2.1"] | None = None
    limitation: str = "Referential and numeric checks cannot establish semantic entailment or scientific validity."


class GroundingValidationError(LLMResponseError):
    def __init__(self, report: GroundedValidationReport) -> None:
        super().__init__(
            "Candidate section failed grounding checks: " + "; ".join(report.issues),
            code="grounding_failed",
        )
        self.report = report


DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
PMID_RE = re.compile(r"\bPMID\s*:?\s*(\d{5,9})\b", re.I)
CAUSAL_RE = re.compile(
    r"\b(?:proves?|proved|causes?|caused|causal|cures?|guarantees?|clinically validated)\b", re.I
)


def render_grounded_request(request: GroundedSectionRequest) -> str:
    return json.dumps(
        {"task": "grounded_manuscript_section", "input_data": request.model_dump(mode="json")},
        ensure_ascii=False,
        sort_keys=True,
    )


def validate_grounded_response(
    request: GroundedSectionRequest, response: GroundedSectionResponse
) -> GroundedValidationReport:
    issues: list[str] = []
    if response.section_name.casefold() != request.section_name.casefold():
        issues.append("section_name does not match the requested section")
    claims = {claim.claim_id: claim for claim in request.claims}
    evidence = {item.evidence_id: item for item in request.evidence}
    citations = {item.citation_id: item for item in request.citations}
    styles = {item.chunk_id: item for item in request.style_examples}
    if not set(response.ranked_evidence_ids) <= evidence.keys():
        issues.append("unknown ranked evidence IDs")
    asserted_ids = set()
    referenced_ids = {cid for paragraph in response.paragraphs for cid in paragraph.claim_ids}
    for assertion in response.assertions:
        if assertion.claim_id in asserted_ids:
            issues.append("duplicate structured assertion for one claim")
        asserted_ids.add(assertion.claim_id)
        if assertion.claim_id not in referenced_ids:
            issues.append("structured assertion is not linked to any paragraph")
        claim = claims.get(assertion.claim_id)
        if claim is None or claim.fact is None:
            issues.append("structured assertion has no supplied fact")
            continue
        if assertion.evidence_id not in claim.evidence_ids:
            issues.append("structured assertion cites unrelated evidence")
        if (
            assertion.model_dump(include={"subject", "predicate", "value", "unit"})
            != claim.fact.model_dump()
        ):
            issues.append(
                "structured assertion changes a supplied fact value, unit, subject, or predicate"
            )
    if (
        sum(len(word_tokens(paragraph.text)) for paragraph in response.paragraphs)
        > request.max_words
    ):
        issues.append("candidate exceeds the explicit word limit")
    if not response.paragraphs and not any(
        question.strip() for question in response.unresolved_questions
    ):
        issues.append("empty candidate must explain missing evidence in unresolved_questions")
    for index, paragraph in enumerate(response.paragraphs, 1):
        prefix = f"paragraph {index}: "
        if not paragraph.text.strip() or not paragraph.claim_ids:
            issues.append(prefix + "paragraph needs text and supporting claim IDs")
        unknown = set(paragraph.claim_ids) - claims.keys()
        if unknown:
            issues.append(prefix + "unknown claim IDs")
        selected = [claims[cid] for cid in paragraph.claim_ids if cid in claims]
        if any(claim.fact is not None and claim.claim_id not in asserted_ids for claim in selected):
            issues.append(prefix + "structured factual claim lacks an output assertion")
        if any(
            claim.support_strength not in {"direct", "indirect"} or not claim.evidence_ids
            for claim in selected
        ):
            issues.append(prefix + "weak, unsupported, or unlinked claim was asserted")
        allowed_citations = {cid for claim in selected for cid in claim.citation_ids}
        if not set(paragraph.citation_ids) <= allowed_citations:
            issues.append(prefix + "citation was not linked to the cited claims")
        if not set(paragraph.style_chunk_ids) <= styles.keys():
            issues.append(prefix + "unknown style-example IDs")
        source_text = "\n".join(
            [claim.claim_text for claim in selected]
            + [evidence[eid].text for claim in selected for eid in claim.evidence_ids]
        )
        cited_records = [
            citations[cid].model_dump(mode="json")
            for cid in paragraph.citation_ids
            if cid in citations
        ]
        candidate_quantities = mask_known_citations(paragraph.text, cited_records)
        evidence_quantities = mask_known_citations(source_text, cited_records)
        if ambiguous_numeric_fragments(candidate_quantities) or unsupported_quantities(
            candidate_quantities, evidence_quantities
        ):
            issues.append(
                prefix + "numeric literal, quantitative unit, or bound absent from cited evidence"
            )
        known_dois = set()
        for cid in paragraph.citation_ids:
            citation = citations.get(cid)
            if citation is not None and citation.doi:
                known_dois.add(citation.doi.lower().rstrip(".,;"))
        if any(
            doi.lower().rstrip(".,;") not in known_dois for doi in DOI_RE.findall(paragraph.text)
        ):
            issues.append(prefix + "unregistered DOI")
        known_pmids = {
            citations[cid].pmid
            for cid in paragraph.citation_ids
            if cid in citations and citations[cid].pmid
        }
        if set(PMID_RE.findall(paragraph.text)) - known_pmids:
            issues.append(prefix + "unregistered PMID")
        if CAUSAL_RE.search(paragraph.text) and not all(
            CAUSAL_RE.search(claim.claim_text) for claim in selected
        ):
            issues.append(prefix + "causal or certainty language added beyond the cited claims")
        tokens = word_tokens(paragraph.text)
        size = request.max_style_overlap_words
        for style in request.style_examples:
            style_tokens = word_tokens(style.text)
            windows = {
                tuple(style_tokens[start : start + size])
                for start in range(len(style_tokens) - size + 1)
            }
            if any(
                tuple(tokens[start : start + size]) in windows
                and " ".join(tokens[start : start + size]) not in " ".join(word_tokens(source_text))
                for start in range(len(tokens) - size + 1)
            ):
                issues.append(prefix + "long passage copied from style-only material")
                break
    return GroundedValidationReport(
        valid=not issues,
        quantity_protocol="2.1",
        issues=issues,
        request_hash=sha256_text(render_grounded_request(request)),
        checks=[
            "source ID membership",
            "claim support linkage",
            "claim-specific quantities, units, and bounds",
            "registered citation identifiers",
            "style-only copying",
            "word limit",
        ],
    )


def draft_grounded_section(
    request: GroundedSectionRequest, provider: LLMProvider
) -> GroundedSectionResponse:
    # Revalidate because callers may have mutated a Pydantic object after creation.
    request = GroundedSectionRequest.model_validate(request.model_dump())
    raw = provider.generate_json(
        render_grounded_request(request),
        schema=GroundedSectionResponse,
        system=GROUNDED_SECTION_SYSTEM,
    )
    try:
        response = GroundedSectionResponse.model_validate(raw)
    except ValueError as exc:
        raise LLMResponseError(
            "Candidate section did not satisfy its schema.", code="schema_mismatch"
        ) from exc
    report = validate_grounded_response(request, response)
    if not report.valid:
        raise GroundingValidationError(report)
    return response


def request_from_context(
    context: ManuscriptContext, section_name: str, *, max_words: int = 800
) -> GroundedSectionRequest:
    """Adapt the existing pipeline without changing its deterministic default path."""
    from manuscriptforge.drafting.section_writer import _claims_for_section
    from manuscriptforge.style.retrieval import get_style_exemplars

    claims = _claims_for_section(context, section_name)
    evidence_by_id = {item.evidence_id: item for claim in claims for item in claim.evidence_items}
    citation_ids = {cid for claim in claims for cid in claim.citation_ids}
    available_citations = {citation.citation_id: citation for citation in context.citations}
    # Missing citation records remain unresolved rather than becoming new sources.
    linked_ids = citation_ids & available_citations.keys()
    styles = get_style_exemplars(
        context.style_profile,
        section_name,
        " ".join(claim.claim_text for claim in claims[:3]),
        max_examples=context.config.style.max_style_examples_per_section,
        project_dir=context.project_dir,
    )
    return GroundedSectionRequest(
        section_name=section_name,
        max_words=max_words,
        claims=[
            GroundedClaim(
                claim_id=claim.claim_id,
                claim_text=claim.claim_text,
                evidence_ids=[item.evidence_id for item in claim.evidence_items],
                citation_ids=[cid for cid in claim.citation_ids if cid in linked_ids],
                support_strength=claim.support_strength,
            )
            for claim in claims
        ],
        evidence=[
            GroundedEvidence(
                evidence_id=item.evidence_id,
                text=item.text,
                source_path=item.source_path,
                content_hash=item.content_hash,
            )
            for item in evidence_by_id.values()
        ],
        citations=[
            GroundedCitation(
                citation_id=citation.citation_id,
                title=citation.title,
                authors=citation.authors,
                year=citation.year,
                doi=citation.doi,
                pmid=citation.pmid,
            )
            for citation in context.citations
            if citation.citation_id in linked_ids
        ],
        style_examples=[
            StyleExample(
                chunk_id=item["chunk_id"],
                text=item["text"],
                source_file=item["source_file"],
                content_hash=item["content_hash"],
            )
            for item in styles
        ],
    )


def mock_grounded_provider() -> LLMProvider:
    """Replay exact supplied claims for offline orchestration; never invent fixture facts."""
    from manuscriptforge.llm.mock_provider import MockLLMProvider

    def fixture(prompt: str, schema: type[BaseModel] | None) -> dict[str, Any]:
        if schema is not GroundedSectionResponse:
            raise LLMResponseError(
                "This fixture supports GroundedSectionResponse only.", code="unsupported_fixture"
            )
        request = GroundedSectionRequest.model_validate(json.loads(prompt)["input_data"])
        paragraphs = []
        assertions = []
        unresolved = []
        words = 0
        for claim in request.claims:
            if claim.support_strength not in {"direct", "indirect"} or not claim.evidence_ids:
                unresolved.append(f"Review support for claim {claim.claim_id} before including it.")
                continue
            count = len(word_tokens(claim.claim_text))
            if words + count > request.max_words:
                unresolved.append(f"Claim {claim.claim_id} exceeds the remaining word allowance.")
                continue
            paragraphs.append(
                GroundedParagraph(
                    text=claim.claim_text,
                    claim_ids=[claim.claim_id],
                    citation_ids=claim.citation_ids,
                    style_chunk_ids=[],
                )
            )
            if claim.fact is not None:
                assertions.append(
                    GroundedAssertion(
                        claim_id=claim.claim_id,
                        evidence_id=claim.evidence_ids[0],
                        **claim.fact.model_dump(),
                    )
                )
            words += count
        if not paragraphs and not unresolved:
            unresolved.append("Supply evidence-linked claims before drafting this section.")
        return GroundedSectionResponse(
            section_name=request.section_name,
            paragraphs=paragraphs,
            unresolved_questions=unresolved,
            assertions=assertions,
            ranked_evidence_ids=list(
                dict.fromkeys(
                    eid
                    for paragraph in paragraphs
                    for cid in paragraph.claim_ids
                    for eid in next(c for c in request.claims if c.claim_id == cid).evidence_ids
                )
            ),
        ).model_dump(mode="json")

    return MockLLMProvider(json_factory=fixture)
