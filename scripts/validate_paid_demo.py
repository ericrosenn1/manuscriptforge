"""Opt-in model smoke test using only new original synthetic inputs.

This measures functional integration, not scientific validity. The shared cost
ledger bounds all calls; credentials are never accepted as arguments.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from manuscriptforge.config import load_project_config, write_project_config
from manuscriptforge.llm.budget import BudgetLedger
from manuscriptforge.research.demo import prepare_research_demo
from manuscriptforge.research.pipeline import implementation_version, research_engine, run_research
from manuscriptforge.research.settings import ResearchSettings
from manuscriptforge.utils.io import write_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--budget-usd", required=True)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    settings = ResearchSettings(
        provider="openai",
        model=args.model,
        allow_network=args.allow_network,
        max_workers=args.workers,
        budget_limit_usd=args.budget_usd,
        budget_ledger_path=str(args.ledger.resolve()),
    )
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    project = prepare_research_demo(output / "project")
    config = load_project_config(project).model_copy(deep=True)
    config.privacy.local_only = False
    write_project_config(project, config)
    inputs = {
        path.relative_to(project).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in project.rglob("*")
        if path.is_file()
    }
    ledger = BudgetLedger(args.ledger, args.budget_usd)
    protocol = {
        "experiment": "paid-original-synthetic-workflow-smoke",
        "scientific_validation": "not_established",
        "settings": settings.model_dump(mode="json", exclude={"budget_ledger_path"}),
        "explicit_demo_override": {"privacy.local_only": False},
        "original_demo_manifest_retained": True,
        "source_hashes": inputs,
        "implementation_sha256": implementation_version(),
        "budget_before": ledger.snapshot(),
    }
    write_json(output / "protocol.json", protocol)
    result = run_research(project, settings)
    verified = research_engine(project).verify_run(result.run_id)
    after = {
        path.relative_to(project).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in project.rglob("*")
        if path.is_file() and path.relative_to(project).parts[0] != "outputs"
    }
    report = {
        "run": result.model_dump(mode="json"),
        "verification": verified.model_dump(mode="json"),
        "inputs_preserved": inputs == after,
        "implementation_unchanged": protocol["implementation_sha256"] == implementation_version(),
        "budget_after": ledger.snapshot(),
        "scientific_validation": "not_established",
    }
    write_json(output / "result.json", report)
    print(f"Run {result.run_id}: {result.status}; artifacts valid: {verified.valid}", flush=True)
    print(f"Evidence: {output}", flush=True)
    if (
        result.status != "succeeded"
        or not verified.valid
        or inputs != after
        or not report["implementation_unchanged"]
    ):
        raise SystemExit("Functional paid smoke failed; preserved details are in result.json")


if __name__ == "__main__":
    main()
