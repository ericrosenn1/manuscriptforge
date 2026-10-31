# Quantities v1 dataset card

[quantities-v1.json](../manuscriptforge/data/benchmarks/quantities-v1.json) is an original synthetic source-only extraction benchmark. It contains invented nonmedical instrument, software, and engineering records. None of its values are empirical research results.

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
```

The generator checks the existing file against deterministic regeneration and refuses to overwrite changed data. The stored dataset includes source hashes and precise source-to-fact links. Its canonical dataset hash at initial construction is `33b13bae58b175c0edc1c88dbfeaeade0f69a6bb6fd9eb71efc756f6c4709636`.

Use this collection for controlled extraction, attribution, abstention, and parser regression tests. It does not establish scientific validity, free-form entailment, domain expertise, real-author style quality, or performance on naturally occurring research documents. Any model comparison should report per-stratum results and compatibility failures separately.
