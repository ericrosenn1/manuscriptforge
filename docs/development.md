# Development and validation

Create a virtual environment as shown in the README, then install the development tools:

```text
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
python -m mypy manuscriptforge
python -m build
```

On Windows, use `.\.venv\Scripts\python.exe` in place of `python` when the environment is not activated. Commands must exit successfully before their result is treated as a pass.

The public suite uses original synthetic fixtures and temporary directories. It covers extraction, chunk curation, profile content, project validation, table/source handling, claims, reviews, exports, and package resources. Provider fixtures exercise deterministic test behavior. They do not prove a live provider works or that generated prose is appropriate for a manuscript.

The demo tests check seven chunks from three formats, one explicit duplicate exclusion, six approved section passages, reference exclusion, profile/source relationships, and deterministic summaries across different output directories. They also verify that a repeated run changes no file or modification time and rejects altered or unrelated output without overwriting it.

## Package checks

An editable installation alone can hide missing package resources. Build the wheel and source distribution, inspect their contents, and install the wheel into another environment. Run the installed CLI from outside the checkout:

```text
manuscriptforge --help
manuscriptforge demo ./package-demo
manuscriptforge demo ./package-demo
```

Verify that `manuscriptforge.__file__` points into the new environment. The second demo call should return the same summary and leave files unchanged. The public CI configuration performs tests and demo validation on its declared Windows/Linux Python matrix; inspect actual run results for the commit being assessed. macOS shell syntax is provided for convenience, but macOS is not in that matrix.

## Scope of validation

Software validation checks file integrity, configuration, schema, counts, relationships, and regression behavior. It does not validate linguistic heuristics, scientific assumptions, author preference, or claims in user-supplied documents. Keep substantive scientific decisions under human review.

Use synthetic data for tests and examples. Keep generated outputs, writing samples, credentials, virtual environments, and local review notes outside tracked source. Changes that affect curation or traceability should preserve source identity and retain a regression case demonstrating the corrected behavior.
