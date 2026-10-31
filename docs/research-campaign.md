# Validation and reproducibility

The research engine separates software correctness, model-output consistency, preference learning, and execution performance. Each has a distinct fixture, endpoint, and report. A generated manuscript or passing process is not evidence of scientific validity.

## Reproducible inputs

Public examples and benchmarks use original synthetic nonmedical records, not private writing or empirical study results. Datasets retain source hashes, locations, explicit expected outputs, and versioned query contracts. Connected document families, authors, duplicate text, and shared sources remain in the same partition.

The adversarial software comparison uses baseline revision `fbd7b17397b3947298f1b1191e2a607c3c7127f7`. The [development guide](development.md) provides its probe commands, local performance scripts, package checks, and offline test instructions. Each new report records its input and implementation identity rather than overwriting a previous result. The [reviewed benchmark package](https://github.com/ericrosenn1/manuscriptforge/tree/main/benchmarks/2026-10-31) provides summaries, original archives, and manifests for the historical measurements.

## What the checks establish

| Layer | Verification | Interpretation limit |
| --- | --- | --- |
| Execution | Dependency ordering, worker isolation, bounded retries, cancellation, snapshot integrity, cache invalidation, and bundle hashes | Computational correctness, not validity of research inputs or conclusions |
| Structured output | Known references, quote anchors, numeric relations, explicit units, bounded table-row associations, and output limits | Syntactic/source consistency; unresolved grammar and semantics remain visible |
| Author decisions | Source-bound claim reviews, stale-review detection, and explicit section routing | Records author judgment; neither model output nor a fixture approval establishes scientific validity |
| Model comparison | Same public inputs, withheld expected outputs, paired cases, abstention and resource accounting | Evidence applies to the specified synthetic contract and split |
| Preference learning | Explicit labels, grouped holdouts, train-only scaling, calibration, and model serialization | Synthetic mechanism checks do not establish a real author's preferences |
| Performance | Declared workload, hardware, worker count, repetitions, cold/warm state, and output consistency | Local measurements; no universal speed or quality claim |

Table-row checks publish their evaluated coverage and review reasons. A globally valid candidate can still contain `not_evaluated` row associations. Count nouns in prose are not treated as physical units, but their semantic correctness is not inferred. All candidate prose remains subject to author review.

## Experimental qualification

Model and prompt selection belongs on train/development data. Freeze dataset hashes, query/guard/scoring versions, model settings, and implementation before evaluating a final test split. The report retains failed calls, missing usage, null denominators, paired uncertainty, and source changes during execution.

A defective input contract invalidates comparative interpretation. The initial quantity-v1 development experiment is retained as a contract diagnostic because query wording conflicted with unit placement and exact predicate naming. [Quantity v1.1](quantity-benchmark.md) changes only the query contract and version metadata; sources, gold facts, partitions, and endpoints remain unchanged. Results under the two contracts must not be presented as a model improvement.

The [evaluation protocol](evaluation.md) distinguishes supplied-fact tests, raw-source extraction, ranking, and explicit rescoring. Optional raw-response capture supports diagnosis without another model call; local captured text is reviewed before any publication.

## Cost and artifact accounting

Optional model calls reserve a conservative upper cost in a shared ledger before dispatch. Provider-reported usage and token-based cost estimates remain distinct from invoices and from unresolved reservations. An execution budget is a cap, not a target or a guarantee of answer quality.

Reports retain provenance, results, and manifests. Public derivatives identify any path sanitization and preserve original artifact hashes. Readers should use the [release notes](release-0.2.0.md) for measured outcomes and their limitations; unfinished experiments are not reported as validated results.
