"""Source-only proposal adapter; benchmark v2 aligns tuples independently of IDs."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal

from manuscriptforge.evaluation.models import Assertion, CandidatePrediction, PublicTask
from manuscriptforge.llm.base import LLMProvider
from manuscriptforge.llm.extraction import (
    ExtractionSource,
    FactExtractionRequest,
    FactExtractionResult,
    extract_fact_proposals,
)


class FactExtractionCandidate:
    """Read raw sources/query only. PublicTask.facts is deliberately never inspected.

    Proposal IDs derive from returned tuples, never from gold IDs. Use a v2 evaluator
    that aligns tuples/source IDs; the original ID-based v1 scorer is incompatible.
    No retrieval ranking is claimed for an extraction-only candidate.
    """

    def __init__(
        self,
        provider: LLMProvider,
        *,
        max_facts: int = 20,
        quantity_protocol: Literal["2.0", "2.1"] = "2.1",
    ) -> None:
        self.provider = provider
        self.max_facts = max_facts
        self.quantity_protocol = quantity_protocol
        self.last_result: FactExtractionResult | None = None

    def __call__(self, task: PublicTask) -> CandidatePrediction:
        self.last_result = None
        request = FactExtractionRequest(
            query=task.query,
            max_facts=self.max_facts,
            sources=[
                ExtractionSource(
                    source_id=source.source_id, text=source.text, content_hash=source.sha256
                )
                for source in task.sources
            ],
        )
        result = extract_fact_proposals(
            request, self.provider, quantity_protocol=self.quantity_protocol
        )
        self.last_result = result
        record = getattr(self.provider, "last_record", None) or {}
        known_usage = isinstance(record.get("input_tokens"), int) and isinstance(
            record.get("output_tokens"), int
        )
        return CandidatePrediction(
            case_id=task.case_id,
            assertions=[
                Assertion(
                    fact_id=fact.proposal_id,
                    source_id=fact.source_id,
                    subject=fact.subject,
                    predicate=fact.predicate,
                    value=fact.value,
                    unit=fact.unit,
                )
                for fact in result.facts
            ],
            abstained=not result.facts,
            text="\n".join(fact.quote for fact in result.facts),
            model=str(
                record.get("response_model") or getattr(self.provider, "model", "offline-fixture")
            ),
            provider=self.provider.name,
            input_tokens=record.get("input_tokens") if known_usage else None,
            output_tokens=record.get("output_tokens") if known_usage else None,
            usage_source="provider_reported" if known_usage else "unavailable",
        )

    def describe(self) -> dict[str, Any]:
        return {
            "method": "source-only-quote-anchored-proposals",
            "max_facts": self.max_facts,
            "quantity_protocol": self.quantity_protocol,
            "gold_facts_used": False,
            "retrieval_ranking": "not performed",
            "implementation_sha256": {
                "extraction.py": hashlib.sha256(
                    Path(__file__).with_name("extraction.py").read_bytes()
                ).hexdigest(),
                "extraction_adapter.py": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            },
            "scientific_acceptance": "requires human review",
        }
