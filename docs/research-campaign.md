# Research engine campaign

This campaign strengthens the Python research tool. The browser Studio remains available;
its presence is not evidence of model quality or scientific validity.

The starting revision is `fbd7b17397b3947298f1b1191e2a607c3c7127f7`.
Work began on 31 October 2026, with a soft 24-hour development budget and a separately
authorized maximum of USD 100 for paid model evaluations. Private writing is not included
in benchmark data. Successful existing runs and source inputs are preserved.

## Required outcomes

1. A real, typed, evidence-grounded generation path, with explicit model/provider versions,
   bounded output, refusal/truncation handling, and auditable token/cost records.
2. A resumable multi-node workflow with bounded parallel processes, dependency validation,
   content-addressed caching, real cancellation/timeouts, and immutable stage results.
3. Valid evaluation protocols: document-family/author/hash-disjoint splits, withheld labels,
   exact factual and citation checks, abstention measurement, and paired baseline comparisons.
4. Evidence that failure recovery, cache invalidation, concurrency, exports, and provenance
   work under deliberate fault injection, not only on a successful demonstration.
5. An organized package, useful CLI, documented architecture, reproducible benchmarks,
   and a verified final handoff containing code, results, manifests, and limitations.

## Acceptance and interpretation

- Existing behavior remains compatible except for documented bug fixes. Corrected tests
  must become stricter; thresholds are never relaxed to obtain a passing report.
- Empty, unavailable, corrupted, unsupported, or semantically unrelated output cannot earn
  a positive quality conclusion merely because it parsed or a process exited successfully.
- Changing a source, review decision, model, prompt, parameter, or implementation must
  invalidate every affected cached descendant. Unaffected successful nodes must be reused.
- Cancellation and timeouts terminate owned workers. Interrupted runs must be resumable
  from verified completed artifacts without treating partial files as success.
- Serial and parallel execution must preserve substantive output and provenance. Performance
  reports record workload, hardware, process count, repetitions, warm/cold state, and timing.
- Model experiments report grounding, citation correctness, numeric fidelity, coverage,
  abstention, latency, and cost separately. Schema conformance is not factual correctness.
- Baseline and candidate see the same public inputs. Test labels are withheld from model
  prompts and tuning; duplicate, author, and source-family leakage is checked explicitly.
- An improvement is reported only when supported by the recorded comparison. Null results,
  failures, and regressions remain in the report. Synthetic evaluations do not establish
  clinical, scientific, or author-imitation validity.

## Execution record

Detailed mutable campaign state and raw experimental artifacts live outside the source
package. Reproducible benchmark definitions and compact, reviewed results belong in the
repository. Paid requests must reserve their maximum configured cost in a shared ledger
before dispatch; ambiguous failures retain that reservation until reconciled.

No paid evaluation is reported as completed until credentials, usage, outputs, and
validation evidence are available. The USD 100 authorization is a cap, not a spending target.
