# Benchmark evidence — 2026-10-31

These are **synthetic model and engineering measurements**, with their original artifacts and implementation fingerprints. Each record applies to its identified source snapshot and workload. None is a real-author study or evidence of scientific validity. No new API calls were made to assemble this evidence package.

## Release validation and current workflow

| Evidence | Observed result | Scope |
| --- | --- | --- |
| [Local software validation](evidence/software-validation-local.json) | 592 passed, one Windows privilege skip; 86% line coverage including subprocesses | Runtime source checkpoint, followed by four passing, stricter CLI-argument checks. Final CI reports its own complete totals. |
| [Reviewed synthetic manuscript](evidence/paid-reviewed-demo.summary.json) | All 14 nodes and seven manuscript sections completed; inputs unchanged, bundle verified | Explicitly scripted fixture reviews; author review and scientific validation remain required. |
| [Workflow timing, fixture v2](evidence/workflow-performance-v2.summary.json) | Cold medians 26.28 / 21.15 / 19.08 s at 1 / 2 / 4 workers; cache reuse 1.01–1.14 s | Two repetitions per setting; local application-cache timings, not an across-version speed comparison. |
| [Current retrieval timing](evidence/retrieval-performance-final.json) | Scan 66.95 ms; indexed Jaccard 2.10 ms; identical top-10 results | 10,000 synthetic records, 30 queries. BM25 2.20 ms; relevance quality not measured here. |
| [Shared API accounting](evidence/api-budget-final.json) | Conservative token-cost accounting against a fixed cap | Not a provider billing invoice; includes failed attempts and all campaign experiments. |

[Verified workflow timing source](archives/workflow-performance-v2.zip) · [Reviewed demo bundle](archives/paid-reviewed-demo.zip)

## Earlier and model-specific measurements

| Evidence | Observed result | What it establishes |
| --- | --- | --- |
| [Basic source-only DEV](evidence/source-only-dev.summary.json) | Both models passed 48/48 cases | Source-only extraction worked on this small synthetic development suite; six independent source groups and a ceiling result do not establish model superiority. |
| [Quantity v1 — **INVALID CONTRACT**](evidence/quantity-v1-invalid-contract.summary.json) | 168 attempted evaluations; conflicting instructions identified | A format-contract diagnostic only. Do not use its scores, failure rates, or paired statistics to rank model quality. |
| [Corrected quantity v1.1 DEV](evidence/quantity-v11-dev.summary.json) | Luna 84/84; Sol 84/84 | Equal development accuracy; the predeclared token-cost tie-break selected Luna before TEST. |
| [Locked quantity v1.1 TEST](evidence/quantity-v11-test.summary.json) | Luna 83/84; Sol 84/84 | One Luna sign mutation was rejected; no wrong tuple was accepted by either model. The prelocked selection remains unchanged. |
| [Frozen workflow timing](evidence/workflow-performance-v1.summary.json) | Cold medians: 30.50 s / 24.17 s / 18.08 s with 1 / 2 / 4 workers. Warm medians: 1.08–1.09 s | Local mock-provider orchestration and cache behavior, two repetitions per setting on one Windows host. All 12 recorded runs succeeded and their artifact checks passed. |
| [Retrieval timing](evidence/retrieval-performance-v2.json) | 10,000 generated records, 30 queries: median scan 69.85 ms; indexed Jaccard 2.23 ms; BM25 2.35 ms | Timings only. Jaccard top-10 results matched the scan; BM25 relevance was not evaluated. Index build costs were 0.295 s / 0.323 s. |
| [Targeted invariant comparison](evidence/engine-invariants.summary.json) | Baseline 0/11; candidate 11/11 | Eleven deliberately targeted synthetic regression cases, not defect prevalence. Field presence still does not prove provenance integrity. |
| [Synthetic preference ranker](evidence/style-learning.summary.json) | DEV 22/24; TEST 24/24 pairs | Learning on one synthetic author, with six held-out document groups. Real-author efficacy is **not established**. |

The source-only dataset contains 240 cases and 120 source records across all partitions. Quantity v1 contains 420 cases and 540 source records; v1.1 retains those sources, gold facts, IDs, and partitions while correcting instructions. The original basic and invalid v1 comparisons used only DEV; the corrected v1.1 comparison includes separate DEV and TEST runs. The preference dataset contains 120 pairs split 72/24/24; test labels were not used for fitting or early stopping. Counts are per dataset and are not summed as unique independent sources.

## Corrected quantity v1.1 and the locked selection

[Selection record and source comparison](evidence/quantity-v11-selection.summary.json) preserve the decision: tied 84/84 DEV accuracy, then Luna's lower estimated token cost ($0.0130264 versus $0.237178 for Sol). The lock was recorded 13.025888 seconds before TEST started. Both models were evaluated once per TEST case. TEST was not used to tune or reselect the model.

Luna's sole TEST failure, `case_8718c2dfa36d28ed`, proposed positive `1.175 mV` from a source stating `- 1.175 mV`. The quantitative guard rejected the proposal before any assertion was accepted. This was a real sign error, not a guard false positive. Both models correctly abstained on all 12 required TEST cases. Luna's accepted-tuple recall was 71/72; its zero accepted wrong tuples do not mean every case succeeded.

The observed TEST difference is one case out of 84; the paired family-bootstrap interval includes zero, with only six independent source groups. No model-superiority conclusion is drawn. One Luna TEST case has missing usage in the scoring report, so its reported cost covers 83 cases rather than all 84.

All 13 evaluator-file hashes and both predictor metadata records match DEV to TEST. Across the 150 package-file fingerprints, the sole change was an unrelated grounded-manuscript table-threshold helper, explicitly recorded in the selection lock before TEST; it is not called by the extraction predictor. The source archive preserves the frozen TEST implementation. Original study protocol v2 preceded DEV; v2.1 was a metadata-only canonical-digest correction during DEV, not a new selection rule. Both originals are included.

## Original artifacts

- [Complete basic source-only campaign](archives/source-only-dev.zip): original dataset, both case-level outputs, protocol, summary, campaign record, and manifests.
- [Complete quantity v1 campaign and contract diagnostic](archives/quantity-v1-invalid-contract.zip): original raw captures and failed outcomes preserved. **Invalid contract; not model-quality evidence.**
- [Complete corrected quantity v1.1 DEV campaign](archives/quantity-v11-dev.zip).
- [Complete locked quantity v1.1 TEST campaign](archives/quantity-v11-test.zip), including the rejected sign-error response.
- [Frozen evaluation source and decision records](archives/evaluation-frozen-v11.zip): 150 source files, source manifest, launch file, original study v2/v2.1 protocols, and selection lock. No interpreter, environment, dependency cache, or compiled files.
- [Frozen workflow protocol, all timing rows, and summary](archives/workflow-performance-v1.zip).
- [Original baseline, candidate, and invariant comparison reports](archives/engine-invariants.zip).
- [Preference dataset, model, report, model card, and manifest](archives/style-learning.zip).

Each archive includes an `ARCHIVE_CONTENTS.json` derivative listing the exact SHA-256 and size of each original member. Original file bytes and pre-existing manifests were not rewritten. The workflow protocol retains its machine-local workspace snapshot reference as provenance; display summaries replace that location with a clearly labeled portable reference. No private user paths or API-key patterns were found.

## Provenance and interpretation

The invariant baseline records revision `fbd7b17397b3947298f1b1191e2a607c3c7127f7`. Its candidate records `engine-campaign-working-snapshot`, which is a label rather than an exact commit. All snapshots retain their recorded per-file implementation SHA-256 values in the summaries or original protocols. Do not substitute the current Git revision for a measured snapshot.

Canonical dataset SHA-256 identifies normalized structured content; file-byte SHA-256 identifies the archived serialization. Both are labeled separately, and both model datasets' canonical identities were reproduced using explicit UTF-8 reads. Exact file-byte hashes match the original manifests. Read JSON as UTF-8 when reproducing these checks.

Costs in model reports are token-based estimates, not invoices. Missing usage remains missing. Shared campaign-ledger snapshots can include concurrent work. The ranker's perfect small-sample test result and degenerate bootstrap interval do not establish certainty for future authors. No model-superiority or general performance claim is made here.

## Verification

[Manifest](manifest.json) · [Compact TSV manifest](MANIFEST.tsv) · [Archive verification](archive-validation.json) · [Final verification](verification.json) · [Machine-readable index](summary.json)

All ZIPs in the current manifest pass CRC, exact member-inventory, and original-member hash checks. The historical run/model manifests contributed 1,076 verified file-size/hash entries, plus 150 frozen implementation-file checks. [Additional v1.1 verification](quantity-v11-validation.json) preserves those phase checks. The current [verification record](verification.json) includes the later workflow and reviewed-demo archives. Source artifacts were read only; earlier phase-specific records remain unchanged.

Recheck this package with `python scripts/verify_benchmarks.py benchmarks/2026-10-31`.
