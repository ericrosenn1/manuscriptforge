# Development and validation

Create a virtual environment as shown in the [README](../README.md), then install the development tools:

```text
python -m pip install -e ".[dev]"
python -m pip check
python -m pytest
python -m ruff check .
python -m mypy manuscriptforge
python -m mypy --platform linux manuscriptforge
python -m build
```

On Windows, use `.\.venv\Scripts\python.exe` in place of `python` when the environment is not activated.

The Linux-targeted mypy command checks platform-specific type branches from the same checkout. It complements the CI operating-system matrix and does not substitute for executing tests on Linux.

The public suite uses original synthetic fixtures and temporary directories. It covers extraction, chunk curation, profile content, project validation, table/source handling, claims, reviews, exports, and package resources. Provider fixtures exercise deterministic mock behavior.

The demo tests check seven chunks from three formats, one explicit duplicate exclusion, six approved section passages, reference exclusion, profile/source relationships, and deterministic summaries across different output directories. They also verify that a repeated run changes no file or modification time and rejects altered or unrelated output without overwriting it.

## Research engine checks

The Python suite includes orchestration failure/recovery, snapshot and cache integrity, grounded output rejection, numeric and citation consistency, grouped evaluation leakage, and learned preference-model serialization. Model/provider tests use explicit replay fixtures; paid APIs are not required for the suite.

Coverage configuration enables worker subprocess instrumentation through the [coverage.py subprocess patch](https://coverage.readthedocs.io/en/latest/subprocess.html). Use `python -m pytest --cov=manuscriptforge --cov-report=term-missing` for an instrumented run and inspect the resulting report's scope. Reports from older parent-only runs are not directly comparable with combined worker measurements. No coverage percentage is inferred from configuration alone.

To reproduce the selected adversarial invariants against any source checkout:

```bash
python scripts/benchmark_invariants.py probe --source-root . --output local/candidate-invariants.json
python scripts/benchmark_invariants.py probe --source-root ../baseline-checkout --revision fbd7b17397b3947298f1b1191e2a607c3c7127f7 --output local/baseline-invariants.json
python scripts/benchmark_invariants.py compare --baseline local/baseline-invariants.json --candidate local/candidate-invariants.json --output local/invariant-comparison.json
```

Prepare the baseline checkout from the named commit before running it. The script verifies which source tree it imports and records source-file and script hashes. It refuses to overwrite existing reports. Eleven cases have predeclared functional invariants; a separate source-map probe documents the limits of field-presence scoring. These are targeted regressions, not estimates of general model reliability.

The local performance scripts use original synthetic inputs and make no model calls:

```bash
python scripts/benchmark_workflow.py --output local/workflow-timing --workers 1 2 4 --repetitions 2
python scripts/benchmark_retrieval.py --output local/retrieval-timing.json --records 10000 --queries 30
```

Choose new output paths. The workflow benchmark records cold and verified-cache runs, machine information and implementation hashes. The retrieval benchmark checks exact legacy-Jaccard top-10 parity and times the indexed alternatives. Two repetitions and a single synthetic workload support a local observation, not a universal performance claim. The [release notes](release-0.2.0.md) and [benchmark archives](https://github.com/ericrosenn1/manuscriptforge/tree/main/benchmarks/2026-10-31) identify the implementation snapshots for reported measurements.

Run synthetic preference learning through the [Python API](learning.md). Tests verify that changing test labels or features cannot change fitted coefficients, scales, or the selected stopping iteration. The report distinguishes author-heldout evaluation from same-author document holdout.

Verify the checked-in evidence without calling a model:

```bash
python scripts/verify_benchmarks.py benchmarks/2026-10-31
```

This rehashes the exact payload inventory and each archived source member without extracting it.

Browser checks remain separate:

```bash
npm ci --prefix web
npm run format:check --prefix web
npm test --prefix web
npm run build --prefix web
npm run test:e2e --prefix web
```

Playwright runs the production build on an isolated local port. A successful browser suite does not qualify the Python research engine or model outputs.

## Package checks

An editable installation alone can hide missing package resources. Build the wheel and source distribution, inspect their contents, and install the wheel into another environment. Run the installed CLI from outside the checkout:

```text
manuscriptforge --help
manuscriptforge demo ./package-demo
manuscriptforge demo ./package-demo
```

Verify that `manuscriptforge.__file__` points into the new environment. The second demo call should return the same summary and leave files unchanged. The public CI configuration performs tests and demo validation on its declared Windows/Linux Python matrix; inspect actual run results for the commit being assessed. macOS shell syntax is provided for convenience, but macOS is not in that matrix.

## Scope of validation

These checks validate file integrity, configuration, schemas, counts, source relationships, and regression behavior. They do not measure linguistic accuracy or live-model writing quality.

Use synthetic data for tests and examples. Keep generated outputs, writing samples, credentials, virtual environments, and local review notes outside tracked source. Changes that affect curation or traceability should preserve source identity and retain a regression case demonstrating the corrected behavior.
