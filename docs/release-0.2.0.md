# 0.2.0: research engine

Version 0.2.0 adds an inspectable research-writing workflow around the existing corpus tools. It supports resumable execution, source-linked candidate drafting, lexical retrieval, explicit preference learning, and versioned evaluation. The browser Studio remains a separate optional interface.

## Implemented changes

- A trusted dependency graph with isolated workers, bounded retries, cancellation, input snapshots, hash-verified cache reuse, and immutable finalized bundles.
- Typed generation and extraction boundaries with quote anchors, source hashes, citation membership, quantitative relation checks, and human-review status.
- A shared budget ledger for optional model calls, recording measured usage and conservative reservations separately.
- BM25 and indexed Jaccard retrieval, metadata filters, optional diversity selection, and validated JSON indexes.
- A trained NumPy logistic preference ranker using explicit labels, grouped splits, training-only scaling, calibration, and portable checksummed JSON.
- Grouped paired evaluation with withheld labels, versioned scoring, null denominators, source provenance, latency/cost records, and optional local raw-response diagnostics.

## Measured evidence

These are different experiments with different endpoints. They must not be combined into one quality score.

| Experiment | Observed result | What it supports |
| --- | --- | --- |
| Selected adversarial invariants | Pinned pre-campaign implementation: 0/11; repaired implementation: 11/11. | Eleven targeted software corrections, not general model reliability. |
| Basic source-only development set | `gpt-6-luna:low` and `gpt-6.1-sol:low` each passed 48/48 cases; paired difference 0. Each produced 36 responsive facts and correctly withheld answers in 12 cases. | Synthetic tuple, source-quote, and abstention consistency across six independent families. This is development data, not final held-out scientific validation. |
| Learned preference mechanism | 24/24 held-out synthetic pairs versus 11/24 for the training-majority baseline, across six document groups. | Learning and evaluation work on a disclosed synthetic labeling rule; real-author efficacy is unmeasured. |
| Synthetic lexical relevance | No difference between BM25 and Jaccard in the controlled held-out comparison. | A retained null relevance result. Speed and relevance are separate endpoints. |
| Indexed Jaccard timing | About 70 ms to 2.23 ms median per query on 10,000 synthetic records and 30 queries, with identical top-10 rankings. | Faster execution of that exact lexical ranking on the measured machine, not improved retrieval quality. |

The basic source-only experiment used protocol `2.0-source-only`. Its standard-rate token cost estimates were USD 0.0066794 for Luna and USD 0.111098 for Sol; these are estimates rather than invoices. The conservative cumulative campaign-budget snapshot immediately before the subsequent quantity development experiment was USD 0.62693575. This is a dated spending snapshot, not the final campaign total.

## Workflow timing

The following medians come from a frozen pre-quantity implementation snapshot, using an original synthetic mock-provider project on Windows 11 / Python 3.13.15. There were two repetitions per configuration.

| Workers | Cold workflow (seconds) | Verified warm-cache workflow (seconds) |
| ---: | ---: | ---: |
| 1 | 30.501 | 1.078 |
| 2 | 24.172 | 1.092 |
| 4 | 18.081 | 1.078 |

This measures local orchestration and I/O. It does not measure live-model latency, represent every project, or establish the performance of later code changes. Two repetitions are insufficient for a broad performance claim.

## Quantity evaluation and compatibility

The original [quantity dataset](quantity-benchmark.md) contains 420 cases in 14 strata across 30 independent document/author families. A gold-fixture replay validates parser/scorer compatibility; it is not a model experiment. No result from the stronger quantity-model experiment is included in this release note snapshot.

Scoring `2.1-quantities` adds explicit whole-scalar numerical equivalence for Unicode/spaced signs. Extraction guard and scoring versions are recorded separately. Existing `2.0-source-only` reports and dataset labels remain unchanged. Legacy style scores are diagnostic or inconclusive when evidence is absent; they are not universal quality ratings.

Python 3.11–3.13 is supported. The original corpus CLI remains available. Optional paid-provider use requires the `openai` extra and explicit network/budget configuration. Raw-response capture defaults to off and writes only local evaluation artifacts when enabled.

See the [workflow](research-workflow.md), [evaluation protocol](evaluation.md), and [development checks](development.md) for reproducibility and interpretation limits.
