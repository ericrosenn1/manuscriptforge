# Evaluation protocol

The engine has two different kinds of evaluation. They answer different questions and must not be combined into a claim of validated writing quality.

| Evaluation | What it measures | What it does not establish |
| --- | --- | --- |
| Versioned functional protocol | Exact supplied fact tuples, source references, ranking, abstention, and execution failures on grouped held-out cases | Scientific truth, arbitrary prose entailment, author imitation, or publishable manuscript quality |
| Legacy style diagnostics | Sentence/paragraph statistics, phrase patterns, recorded metadata fields, and lexical alignment | Held-out style discrimination or human preference |

## Held-out functional cases

`manuscriptforge.evaluation` defines Pydantic schemas for source records, fact tuples, evaluation cases, and candidate outputs. Facts identify a subject, predicate, value, unit, source, and source locator. Every source has a SHA-256 digest and provenance label.

The original synthetic fixture generator creates 30 document families with eight cases each: direct numeric retrieval, counterfactual values, unit binding, counterfactual units, subject binding, an instruction embedded in source text, missing evidence, and conflicting evidence. These are invented nonmedical records, not measured studies or real author samples. The cases vary entities and values within fixed templates; unseen-template and natural-language generalization are not established.

Sources sharing a document family, author, exact content hash, normalized text hash, or a case are assigned as one connected group. Groups are deterministically assigned to train, development, and test using the recorded seed. Validation rejects cross-partition overlap, invalid source hashes, duplicate IDs, and broken fact/source links. Tiny datasets without three independent groups are rejected.

Tune methods and prompts using train/development data only. Freeze the dataset digest and configuration before opening final test results. Repeated optimization against final test outcomes turns that test into development data; use a new untouched test set before making further generalization claims.

The runner supplies only `PublicTask`: a task-kind tag, query, source records, and source facts. Expected fact IDs, relevant-source labels, abstention labels, strata, and split labels stay in the evaluator. Exported evaluation datasets include labels for reproducibility and must not be supplied to candidate prompts.

```python
from pathlib import Path
from manuscriptforge.evaluation.synthetic import build_synthetic_dataset
from manuscriptforge.evaluation.runner import run_comparison

dataset = build_synthetic_dataset(seed=20261031)
# baseline and candidate are callables: PublicTask -> CandidatePrediction.
run_dir = run_comparison(
    dataset, baseline, candidate, Path("local/evaluation-runs"),
    split="test", baseline_name="frozen baseline", candidate_name="candidate",
)
```

The evaluation runner itself opens no model connection. A caller may provide a deterministic adapter or an explicitly configured model provider. Paid calls require the configured budget gate and shared ledger.

## Scores and denominators

- **Complete-case correctness** requires the requested supported fact set, exact source associations, and the correct abstention decision. Duplicate/unknown ranking IDs invalidate the case. Empty output does not pass a case requiring an answer.
- **Factual precision** counts emitted structured assertions whose subject, predicate, value, unit, and source match supplied facts. Decimal spelling can normalize; units are never silently converted. This is syntactic consistency with the supplied record, not narrative entailment.
- **Fact recall** is the recovered expected fact count divided by the expected fact count.
- **Citation precision/recall** describe exact source attachment on structured assertions. A real, relevant-sounding paper is not automatically supporting evidence.
- **MRR and Recall@1/3** use the withheld relevant-source labels. Invalid rankings remain explicit failures; their excluded ranking denominator is reported.
- **Abstention and calibration** include required/observed abstentions, decision correctness, Brier score, and fixed confidence bins. Confidence is optional; missing confidence is not scored as zero.

The summary includes numerators and denominators, per-stratum results, case outputs, and execution errors. Zero denominators remain `null`. Complete-case Wilson intervals are an IID approximation; the paired bootstrap samples independent source groups. Related cases are not independent experimental replications. Latency is elapsed wall time around each callback; randomized interleaved call order reduces order bias but does not normalize hardware or network effects. Tokens require provider-reported usage. Missing cost remains unknown, and reported cost requires a pricing source.

Each run has a new directory containing the dataset snapshot, baseline/candidate JSONL, summary, Markdown report, evaluator source hashes, and a file manifest. Existing runs are never overwritten. A passing functional suite means every declared structured case passed. The report still states `scientific_validation: NOT_EVALUATED` and `narrative_entailment_evaluated: false`.

## Legacy diagnostic changes

Legacy style reports use schema 2.0. Metric names remain available, but an empty manuscript or absent measured style targets yield `overall_score: null` with `qualification_status: inconclusive`. Other numerical results are explicitly `diagnostic_only`.

Zero-overlap ties and identical distractors are inconclusive. Ranking options are deterministically shuffled, and section alignment requires a unique positive match. These changes repair false positive checks; they do not turn lexical overlap into a scientifically validated style classifier.

Evaluation and style-memory artifacts must belong to the evaluated run snapshot. A later run or mutable project-level memory cannot silently change an old benchmark. Recorded-field completeness is labeled as field presence; it does not verify the referenced IDs or factual support.

## Explicit protocol 1.1 rescoring

Grounded answer abstention and source retrieval are different outputs. A response can correctly withhold a factual answer while retaining references to conflicting source records. Protocol 1.0's strict complete-case rule required an empty ranking for abstentions, even though the public task did not prohibit preserving that provenance.

The optional version 1.1 rescorer adds an `answer_case_correct` facet. It permits known source IDs alongside an empty, explicitly abstained answer; unknown or duplicate source IDs remain invalid. It preserves the original `complete_case_correct` as `legacy_complete_case_correct`. Narrative correctness remains unassessed.

```bash
python scripts/rescore_evaluation.py --input-run SAVED_RUN --output-root local/rescores
```

The rescorer checks the original manifest and dataset fingerprint, makes no provider calls, and copies original artifacts into a new result directory. Its summary records the exact changed definition and input hashes. An absolute-score change under this facet is an evaluator correction, not model improvement. Compare methods only within the same definition.

Original ranking labels are never changed by rescoring. `as_retrieval_dataset_v11()` creates a separately versioned source-ranking dataset in which conflicting source records are relevant to detecting the conflict. Missing requested evidence still has no relevant source. The new dataset has a different identifier, revision, and fingerprint.

## Source-only extraction protocol 2.0

The source-only runner evaluates a harder, separate boundary: candidates receive source text and a query, without the reference fact objects. Generic hints standardize the labels `count` and `duration`; they do not supply any expected value, unit, answer ID, or abstention label.

`run_extraction_comparison()` in `manuscriptforge.evaluation.extraction_runner` accepts baseline and candidate callables, an explicit dataset, an output directory, and a partition (development by default). It works with `FactExtractionCandidate` and wrappers exposing their underlying adapter through `.candidate`.

Returned proposal tuples are aligned to withheld reference tuples after execution, independently of proposal IDs. A fresh extraction result is required for quote assessment. Source hashes and Unicode quote offsets are rechecked against a separate copy of the original text. The runner clears cached extraction results before each call and after exceptions so a prior successful result cannot validate a later failure.

Each completed call is flushed to JSONL and counted in `progress.json`. Interruptions preserve completed rows; these journals support inspection, not automatic resumption of a paid experiment. The final directory contains the dataset and protocol, candidate outputs, separate resource accounting, a summary, Markdown report, and manifest.

Complete-case results require responsive tuple matching and successful source-quote checks. Execution failures remain explicit. Retrieval metrics are not applicable to this extraction-only task. Exact quotations do not establish that a predicate is semantically correct or that a source describes valid science. No such inference is made by the report.

## Quantity protocol 2.1 and response diagnostics

Use `scoring_version="2.1-quantities"` explicitly for the [quantity dataset](quantity-benchmark.md). The corrected v1.1 query revision also requires `label_hints=""` (CLI `--no-label-hints`); legacy defaults remain unchanged for earlier datasets. Version 2.1 compares whole finite scalar values numerically, including Unicode and spaced unary minus. Units remain case-sensitive; interval and qualifier strings retain literal comparison. The dataset's labels and existing reports are not rewritten. Summaries reject mixed scoring versions. The original v1 quantity run is a contract diagnostic because its query wording conflicted with field separation and exact predicate naming; its strict pass rates are not comparative model-quality evidence.

The extraction guard has its own version. `FactExtractionCandidate(..., quantity_protocol="2.1")` uses the repaired quantity guard; `"2.0"` supports explicit historical comparisons. Each row records the actual guard version separately from its scoring version. Independent quote checks follow the guard version recorded in the extraction result; an absent version in an old result is identified as legacy 2.0. Configured guard versions, scoring version, and implementation hashes are recorded in the protocol.

Grounded prose has an additional, bounded `table_binding_checks` report. It derives bindings only from explicit ingested row metadata, not guessed facts from rendered table text. A supported row/value mismatch rejects a candidate. Read `status`, `evaluated_mentions`, and `review_reasons` together: `not_evaluated` is not a successful field association, and the overall grounding `valid` flag does not imply complete table coverage. Exact field assignments can be checked against one column; ordinary labeled clauses may check only row membership. Pronouns, nonunique labels, compound subjects, comparative clauses, field aliases, and spelled-out numbers are outside this check. The row checker does not infer units; quantitative unit checks remain a separate constraint.

Narrative quantity comparison allows a declared list of common count nouns to follow a number without becoming physical units. It does not certify that “records,” “rows,” or another counted entity is semantically correct. Explicit physical/compound units remain constrained, and structured fact extraction continues to require bound units. These checks and the row report supplement source consistency; they do not establish free-form entailment.

For local diagnosis, `run_extraction_comparison(..., capture_policy="failures")` saves raw model responses for cases that fail the complete-case check. `"all"` retains every response and `"off"` is the default. Supported providers capture exact returned text before JSON/schema and source-guard validation, so a schema failure can be distinguished from a correct answer rejected by a guard. This capture does not repair or reclassify an answer.

Response sidecars contain text, response/model identifiers, schema identity, error/refusal/incomplete metadata, byte counts and hashes. They do not contain request prompts, credentials or HTTP headers. Returned text may itself quote source material, so these artifacts stay in the local evaluation output unless deliberately reviewed for publication. The recorder is cleared per request. Missing text, an explicitly empty response, and truncated capture are distinct states. The default capture limit is one million UTF-8 bytes per text field; truncation retains the full received text's hash and original length while marking the stored prefix. Providers without this facility are labeled unavailable.

Sidecar paths and hashes are referenced from incremental JSONL rows and included in the final manifest. Capture policy is recorded in both protocol and manifest. None of these diagnostics require an additional model call.

## Locked quantity comparison

Under the corrected v1.1 query contract, both models passed 84/84 development cases. The [selection lock](https://github.com/ericrosenn1/manuscriptforge/blob/main/benchmarks/2026-10-31/evidence/quantity-v11-selection.summary.json) chose Luna before test using the lower development token-cost estimate to break the tie. The [locked test](https://github.com/ericrosenn1/manuscriptforge/blob/main/benchmarks/2026-10-31/evidence/quantity-v11-test.summary.json) then recorded Luna 83/84 and Sol 84/84; the one Luna negative-sign error was rejected, and no incorrect tuple was accepted. The report labels `baseline` and `candidate` refer to Luna and Sol, not to an assumed quality order.

The paired Sol-minus-Luna complete-case difference was 1.19 percentage points. Its group-bootstrap 95% interval was 0–3.57 points, with only six independent test families behind 84 correlated cases. Keep the pre-test selection unchanged and treat this as a bounded synthetic result, not evidence that one model is generally superior. The original v1 invalid-contract outcomes remain separate.
