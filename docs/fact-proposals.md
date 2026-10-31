# Quote-anchored fact proposals

`manuscriptforge.llm.extraction` provides an optional source-only extraction stage. Its request contains source IDs, exact source text, SHA-256 hashes, a query, and a proposal limit. It has no field for gold facts, expected answers, or preferred citations.

```python
from manuscriptforge.llm.extraction import (
    ExtractionSource, FactExtractionRequest, extract_fact_proposals,
)
from manuscriptforge.llm.mock_provider import MockLLMProvider
from manuscriptforge.utils.hashing import sha256_text

text = "Shelf A holds 12 books."
request = FactExtractionRequest(sources=[
    ExtractionSource(source_id="shelf-note", text=text, content_hash=sha256_text(text))
])
provider = MockLLMProvider(json_responses=[{
    "facts": [{
        "source_id": "shelf-note", "subject": "Shelf A", "predicate": "book_count",
        "value": "12", "unit": "books", "quote": text,
    }]
}])
result = extract_fact_proposals(request, provider)
assert result.validation.valid
assert text[result.facts[0].quote_start:result.facts[0].quote_end] == text
```

This example replays an explicit synthetic fixture and makes no model or network call. Network-backed extraction uses the same provider contract as grounded drafting and must be configured through the shared budgeted research provider.

## What is checked

- Source IDs are unique and source SHA-256 values match the exact input text.
- Every returned source ID exists in the request.
- Quotes are exact contiguous source substrings. Offsets are zero-based Unicode character indices with an exclusive end.
- Omitted offsets are filled only when the quote has exactly one occurrence. This technical anchoring is recorded in the validation report. Supplied incorrect offsets are rejected; repeated quotations need correct explicit offsets.
- The proposed subject occurs as a literal phrase in the quotation.
- Numeric values are present in the quotation. Decimal-equivalent spellings can match, but signs and actual values must agree. Numbers embedded in identifiers do not become measurements.
- A declared unit must bind to the same numeric occurrence, preventing a value from borrowing another value's unit. Units are not converted.
- Quantitative relations are retained: ranges, inequalities, approximations, fractions, and explicit uncertainty cannot silently become exact scalar observations. Compound units and concentration bases remain complete.
- Text values must occur literally. Duplicate source/subject/predicate/value/unit tuples and over-limit responses are rejected.

`FactExtractionValidationError` carries the validation report when the proposals fail these checks. Empty output must explain the missing or ambiguous evidence. Input data, returned quotes, and source text are never silently rewritten to make a proposal pass.

The current model-facing contract puts only the numeric expression, interval or qualified quantity in `value`, and the unit in the separate `unit` field. Measurement labels and explanatory prose are excluded from `value`. Closed intervals retain their brackets and bind their trailing unit. This extraction rule is separate from the count-noun handling used when validating narrative paragraphs.

Fresh extraction uses quantity protocol `2.1`. A validation report records that version. The explicit `quantity_protocol="2.0"` compatibility option exists to reproduce historical validation outcomes; it retains the older surface checks and is not the default for new proposals. Source text, hashes, and quoted offsets remain unchanged under either protocol. Known author-year citation forms and section references are distinguished from scientific quantities, so a bibliography year cannot license a new result.

## Limits and human review

All proposals and results require human review. Literal support cannot prove that a predicate label is correct, that the statement is a valid observation rather than a hypothetical example, or that the underlying research is scientifically sound. Negation, uncertainty, pronoun resolution, and context still require review. Prompt/source separation limits accidental instruction following; it is not a mathematical guarantee against prompt injection.

`FactExtractionCandidate` is an adapter for raw-source evaluation. It deliberately ignores `PublicTask.facts`, emits tuples from the actual extraction output, and derives proposal IDs from those tuples. A raw-source evaluator must align returned tuples independently; the original v1 evaluator's gold-ID lookup is incompatible. The adapter does not pretend to perform retrieval ranking.

Returned artifacts include source quotations and may contain private research text. Review them before sharing.

## Raw-source evaluation

`evaluation.extraction_scoring.score_extraction_case(case, prediction, reference_facts, ...)` aligns returned subject/predicate/value/unit/source tuples after inference, without requiring generated proposal IDs to match gold IDs. Pass `extraction_result=adapter.last_result` and the original `sources` to verify quote offsets and hashes independently. Reference facts belong only in this post-hoc scorer, never in the model request.

The scorer reports responsive precision and recall, unsupported or irrelevant tuples, number/unit/source mismatches, duplicate tuples, abstention accuracy, and execution failures. Units and source IDs are case-sensitive. Refusals and failed requests must be passed as `execution_error`; they are not successful abstentions. Missing quote evidence is explicitly `not_evaluated`, and a tuple match alone does not produce complete-case success for an emitted fact. Retrieval metrics are marked `not_applicable`. These measures concern synthetic exactness and source consistency, not scientific entailment.
