# Quantities v1 and v1.1 dataset card

[quantities-v1.json](../manuscriptforge/data/benchmarks/quantities-v1.json) is an original synthetic source-only extraction benchmark. It contains invented nonmedical instrument, software, and engineering records. None of its values are empirical research results.

[quantities-v1.1.json](../manuscriptforge/data/benchmarks/quantities-v1.1.json) corrects only the public query contract and dataset version metadata. The source records, reference facts, case IDs, partitions, expected answers, abstention rules, and scoring endpoints are identical to v1. The original file and its recorded model responses remain preserved.

## Size and split

| Item | Count |
| --- | ---: |
| Independent document/author families | 30 |
| Cases | 420 |
| Source records | 540 |
| Reference facts | 480 |
| Required abstentions | 60 |
| Train / development / test cases | 252 / 84 / 84 |

Each of 14 strata contributes 30 cases: signed decimals, Unicode minus, spaced minus, scientific notation, compound units, unit case, closed intervals, inequalities, qualified estimates, nearby measurements, absent values, conflicting evidence, source instructions, and primary-source attribution.

Families are grouped across all their strata. The deterministic partition uses the evaluation protocol's author/document-family/source-hash grouping checks. Development and test each contain six independent families. Cases in a family are correlated; 84 cases do not represent 84 independent experiments. Templates are shared across partitions, so this tests new entities and values within controlled templates, not unseen linguistic genres.

## Literal ground truth

Queries name a subject and predicate and request preservation of signs, units, qualifiers, and intervals. They contain no expected numerical value. Reference facts and source labels are retained for post-hoc evaluation and must not be passed to a source-only candidate.

## Query contract and validity

The original v1 query asked for units inside `value`, conflicting with the separate `unit` field and gold representation. The runner's inherited count/duration hints could also contradict the exact requested predicate. Its first paid development run is therefore retained as a **contract diagnostic**, not a comparative model-quality result. The [archived diagnostic summary](https://github.com/ericrosenn1/manuscriptforge/blob/main/benchmarks/2026-10-31/evidence/quantity-v1-invalid-contract.summary.json) preserves that designation. Raw-response review found faithful numerical content alongside representation and instruction conflicts; strict pass-rate differences from that run do not establish model superiority or inferiority.

Version 1.1 makes the contract explicit: return the requested subject and predicate exactly; keep units only in `unit`; preserve numeric signs, bracketed interval expressions, inequalities and approximation words in `value`; exclude descriptive measurement labels. When a unique numeric quantity is absent, return empty facts and explain the gap in `unresolved_questions`. Source instructions remain untrusted data. No gold numerical examples are inserted into the query.

Use `scoring_version="2.1-quantities", label_hints=""` with the source-only runner. The v1.1 dataset rejects inherited label hints or a different scoring version before any model call. The exact hints, query hashes, dataset revision, runtime versions, and implementation hashes are recorded. Any comparison between v1 and v1.1 changes the input contract and cannot be described as a model improvement.

## Locked v1.1 experiment

Both models completed [84/84 development cases](https://github.com/ericrosenn1/manuscriptforge/blob/main/benchmarks/2026-10-31/evidence/quantity-v11-dev.summary.json). The [pre-test selection](https://github.com/ericrosenn1/manuscriptforge/blob/main/benchmarks/2026-10-31/evidence/quantity-v11-selection.summary.json) chose Luna using the lower measured token cost as the tie-break. With the configuration locked, [test results](https://github.com/ericrosenn1/manuscriptforge/blob/main/benchmarks/2026-10-31/evidence/quantity-v11-test.summary.json) were Luna 83/84 and Sol 84/84. One Luna response omitted a negative sign; source validation rejected it. No incorrect tuple was accepted in the test set, and the pre-test model selection was not changed.

The paired Sol-minus-Luna difference was 1.19 percentage points, with a group-bootstrap 95% interval of 0–3.57 points over six independent test families. The 84 cases are correlated and share controlled templates. These results describe this synthetic extraction contract; the single-case difference does not establish model superiority or performance on natural research text.

## Literal examples

Representative lexemes from the first family are:

| Source lexeme | Reference value | Unit | Intended comparison |
| --- | --- | --- | --- |
| `-0.125 mV` | `-0.125` | `mV` | Signed scalar |
| `−0.375 V` | `-0.375` | `V` | Unicode minus and ASCII minus denote the same negative scalar |
| `- 0.625 mV` | `-0.625` | `mV` | An explicitly unary, spaced minus denotes a negative scalar |
| `+1.200E-3 s` | `0.00120` | `s` | Equivalent decimal/scientific notation |
| `14.25 MiB/s` | `14.25` | `MiB/s` | Compound unit retained exactly |
| `[18.50, 19.25] °C` | `[18.50, 19.25]` | `°C` | Closed-interval syntax and endpoints preserved |
| `<= 9.75 ms` | `<= 9.75` | `ms` | Inequality retained in the value |
| `approximately 32.50 MiB` | `approximately 32.50` | `MiB` | Qualifier retained in the value |

The unit-case stratum distinguishes `mW` from `MW`. The nearby-measurement stratum places velocity and temperature in one statement without licensing a unit or subject swap. Conflicting records require withholding a unique answer. Missing values remain missing. Source-instruction records are explicitly instructions rather than measurements, and their invented number is never a reference fact. Attribution cases require the primary log rather than secondary commentary containing the same quantity.

## Versioned quantity normalization

The dataset schema can represent all of these reference values. At initial construction, two implementation limitations were observed independently of any model:

- The original literal extraction validator accepted a leading Unicode minus, but the original evaluation canonicalizer did not equate that glyph with ASCII minus.
- The original extraction validator rejected an otherwise correct scalar with a spaced unary minus in its source quote.

These were parser/scorer compatibility limitations. Protocol `2.1-quantities` repairs scalar comparison explicitly; protocol `2.0-source-only` retains its original scoring definition. The dataset and earlier reports remain unchanged. Extraction guard version and scoring version are reported separately, and independent quote verification uses the guard version recorded with the extraction result.

The scalar equivalence rule maps ASCII hyphen-minus and U+2212 minus, permits whitespace between an unambiguous unary sign and the scalar, and compares finite decimals/scientific notation numerically. Original quotes, source hashes, and character offsets remain unchanged. The guard distinguishes unary signs from interval/range separators. The scorer does not convert units, change unit case, remove inequalities or approximation qualifiers, or infer missing values. Interval and qualified string values retain their declared literal representation. A deterministic replay of all 420 gold fixtures checks parser/scorer compatibility; it is not a model-performance result.

## Reproduction and intended use

```bash
python scripts/generate_quantity_benchmark.py --check
python scripts/generate_quantity_benchmark_v11.py --check
```

Both generators check existing data and refuse to overwrite changed files. The stored datasets include source hashes and precise source-to-fact links. Canonical dataset hashes are `33b13bae58b175c0edc1c88dbfeaeade0f69a6bb6fd9eb71efc756f6c4709636` for v1 and `e7ec83fc058fa80a93a34b7263f4f2b0540c501e864130f36e66792f4a276a14` for v1.1. The commands also report the actual on-disk byte hash; line-ending differences can change that hash without changing the canonical dataset.

Use this collection for controlled extraction, attribution, abstention, and parser regression tests. It does not establish scientific validity, free-form entailment, domain expertise, real-author style quality, or performance on naturally occurring research documents. Any model comparison should report per-stratum results and compatibility failures separately.
