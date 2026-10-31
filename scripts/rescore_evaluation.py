"""Add version 1.1 answer facets to saved outputs without model calls."""

from __future__ import annotations

import argparse
from pathlib import Path

from manuscriptforge.evaluation.rescore import rescore_saved_run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-run", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = rescore_saved_run(args.input_run, args.output_root)
    print(f"Rescore artifacts: {result}")
    print(
        "Original results preserved. No provider calls. Score changes reflect evaluator definitions, not model improvement."
    )


if __name__ == "__main__":
    main()
