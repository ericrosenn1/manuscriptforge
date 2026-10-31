"""Budgeted, reproducible model comparisons over source-only extraction tasks."""

from __future__ import annotations

import json
import os
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any, Literal

from manuscriptforge.evaluation.extraction_runner import (
    LABEL_HINTS,
    CapturePolicy,
    run_extraction_comparison,
)
from manuscriptforge.evaluation.extraction_scoring import (
    ExtractionProtocol,
    validate_extraction_protocol,
)
from manuscriptforge.evaluation.models import (
    CandidatePrediction,
    EvaluationDataset,
    PublicTask,
    Split,
)
from manuscriptforge.evaluation.protocol import dataset_hash, validate_dataset
from manuscriptforge.llm.budget import NANO, BudgetLedger, default_prices
from manuscriptforge.llm.extraction_adapter import FactExtractionCandidate
from manuscriptforge.orchestration.storage import contained_file, hash_file, is_link
from manuscriptforge.research.providers import research_provider
from manuscriptforge.research.settings import ResearchSettings
from manuscriptforge.utils.io import read_json, write_json


class ComparisonRunError(RuntimeError):
    """A failed comparison whose partial evidence remains available."""

    def __init__(self, output_path: Path, message: str) -> None:
        self.output_path = output_path
        super().__init__(f"{message} Diagnostics: {output_path}")


def _policy(settings: ResearchSettings) -> tuple[Path, Decimal]:
    if settings.provider != "openai" or not settings.allow_network:
        raise ValueError("A model comparison requires an explicitly enabled OpenAI provider")
    if not settings.budget_ledger_path or not settings.budget_ledger_path.strip():
        raise ValueError("A model comparison requires an explicit shared budget ledger")
    cap = settings.budget_limit_usd
    if cap is None or not cap.is_finite() or cap <= 0:
        raise ValueError("A model comparison requires a finite positive budget cap")
    path = Path(settings.budget_ledger_path).expanduser().resolve()
    if path.is_dir():
        raise ValueError("The budget ledger must be a file, not a directory")
    return path, cap


def _source_hashes() -> dict[str, str]:
    package = Path(__file__).resolve().parents[1]
    return {
        path.relative_to(package).as_posix(): hash_file(path)
        for path in sorted(package.rglob("*"))
        if path.is_file()
        and path.suffix in {".py", ".json", ".yaml", ".yml"}
        and "__pycache__" not in path.parts
    }


def _message(error: BaseException) -> str:
    value = str(error)
    secret = os.environ.get("OPENAI_API_KEY")
    return value.replace(secret, "[redacted]") if secret else value


class BudgetedExtractionCandidate:
    """Attach measured usage and price provenance to an extraction adapter."""

    def __init__(
        self,
        settings: ResearchSettings,
        ledger: BudgetLedger,
        *,
        quantity_protocol: Literal["2.0", "2.1"] = "2.1",
    ) -> None:
        if quantity_protocol not in {"2.0", "2.1"}:
            raise ValueError("Unsupported candidate quantity protocol")
        path, cap = _policy(settings)
        expected_cap = int((cap * NANO).to_integral_value(rounding=ROUND_CEILING))
        if ledger.path.resolve() != path or ledger.limit_nano != expected_cap:
            raise ValueError("Candidate settings do not match the supplied budget ledger and cap")
        if settings.model not in ledger.prices:
            raise ValueError("The requested model has no verified price policy")
        self.settings = settings.model_copy(update={"budget_ledger_path": str(path)})
        self.ledger = ledger
        self.provider = research_provider(self.settings)
        self.candidate = FactExtractionCandidate(self.provider, quantity_protocol=quantity_protocol)

    def __call__(self, task: PublicTask) -> CandidatePrediction:
        prediction = self.candidate(task)
        usage = getattr(self.provider, "last_record", None) or {}
        if (
            prediction.input_tokens is not None
            and prediction.output_tokens is not None
            and usage.get("response_model") in {None, self.settings.model}
        ):
            estimate = self.ledger.prices[self.settings.model].quote(
                prediction.input_tokens,
                prediction.output_tokens,
                cached=usage.get("cached_input_tokens") or 0,
            )
            prediction = prediction.model_copy(
                update={
                    "cost_usd": float(estimate / NANO),
                    "pricing_source": self.ledger.price_source["source"],
                }
            )
        return prediction

    def describe(self) -> dict[str, Any]:
        return self.candidate.describe() | {
            "model": self.settings.model,
            "reasoning_effort": self.settings.reasoning_effort,
            "max_output_tokens": self.settings.max_output_tokens,
            "request_timeout_seconds": self.settings.request_timeout_seconds,
            "price_source": self.ledger.price_source,
            "cost_kind": "Standard-rate token estimate, not an invoice. Shared ledger reserves conservative bounds including failures.",
        }


def _verified_result(run: Path, root: Path, expected_cases: int) -> dict[str, Any]:
    run = Path(run)
    if not run.is_dir() or is_link(run) or not run.resolve().is_relative_to(root.resolve()):
        raise ValueError("Comparison output is missing or outside the campaign")
    manifest = read_json(contained_file(run, "manifest.json"))
    entries = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(entries, list):
        raise ValueError("Comparison result has no file manifest")
    names = [entry.get("path") for entry in entries if isinstance(entry, dict)]
    if len(names) != len(entries) or len(set(names)) != len(names):
        raise ValueError("Comparison result manifest has malformed or duplicate entries")
    actual = set()
    for path in run.rglob("*"):
        if is_link(path):
            raise ValueError("Linked comparison artifacts are not allowed")
        if path.is_file() and path != run / "manifest.json":
            actual.add(path.relative_to(run).as_posix())
    if actual != set(names):
        raise ValueError("Comparison result manifest does not cover its exact file inventory")
    for entry in entries:
        path = contained_file(run, entry["path"])
        if path.stat().st_size != entry.get("bytes") or hash_file(path) != entry.get("sha256"):
            raise ValueError(f"Comparison result artifact integrity failed: {entry['path']}")
    summary = read_json(contained_file(run, "summary.json"))
    progress = read_json(contained_file(run, "progress.json"))
    if (
        progress.get("status") != "completed"
        or progress.get("planned_calls") != 2 * expected_cases
        or progress.get("completed_calls") != 2 * expected_cases
    ):
        raise ValueError("Comparison reports incomplete call execution")
    for method in ("baseline", "candidate"):
        values = summary.get(method, {})
        if values.get("cases") != expected_cases:
            raise ValueError(f"Comparison {method} case count does not match its planned partition")
        rows = [
            json.loads(line)
            for line in contained_file(run, f"{method}.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        if len(rows) != expected_cases:
            raise ValueError(f"Comparison {method} case records are incomplete")
        failures = sum(bool(row.get("execution_error")) for row in rows)
        if values.get("execution_failures") != failures:
            raise ValueError(f"Comparison {method} failure count does not match its case records")
    return summary


def compare_models(
    dataset: EvaluationDataset,
    baseline_settings: ResearchSettings,
    candidate_settings: ResearchSettings,
    output_root: Path,
    *,
    split: Split = "dev",
    seed: int = 20261031,
    scoring_version: ExtractionProtocol = "2.0-source-only",
    capture_policy: CapturePolicy = "off",
    label_hints: str = LABEL_HINTS,
    baseline_quantity_protocol: Literal["2.0", "2.1"] = "2.1",
    candidate_quantity_protocol: Literal["2.0", "2.1"] = "2.1",
) -> Path:
    """Preserve protocol, every result, failures, and shared-budget snapshots."""
    left_policy, right_policy = _policy(baseline_settings), _policy(candidate_settings)
    if left_policy != right_policy:
        raise ValueError("Both models must use the same budget ledger and cap")
    prices, _ = default_prices()
    if baseline_settings.model not in prices or candidate_settings.model not in prices:
        raise ValueError("Every model requires a verified price policy before comparison")
    validate_extraction_protocol(scoring_version)
    if capture_policy not in {"off", "failures", "all"}:
        raise ValueError("Unsupported response capture policy")
    if not isinstance(label_hints, str):
        raise ValueError("Label hints must be an explicit string")
    if dataset.dataset_id == "original-quantities-v1.1" and label_hints:
        raise ValueError("Quantity v1.1 specifies its own predicates; label hints must be empty")
    if baseline_quantity_protocol not in {"2.0", "2.1"} or candidate_quantity_protocol not in {
        "2.0",
        "2.1",
    }:
        raise ValueError("Unsupported candidate quantity protocol")
    if dataset.dataset_id == "original-quantities-v1" and scoring_version != "2.1-quantities":
        raise ValueError("The quantities benchmark requires explicit protocol 2.1-quantities")
    validate_dataset(dataset)
    if split not in {"train", "dev", "test"}:
        raise ValueError("Comparison split must be train, dev, or test")
    case_count = sum(case.split == split for case in dataset.cases)
    if not case_count:
        raise ValueError(f"No {split} extraction cases")
    ledger = BudgetLedger(*left_policy)
    baseline = BudgetedExtractionCandidate(
        baseline_settings, ledger, quantity_protocol=baseline_quantity_protocol
    )
    candidate = BudgetedExtractionCandidate(
        candidate_settings, ledger, quantity_protocol=candidate_quantity_protocol
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    campaign = Path(output_root) / f"model-comparison-{stamp}-{uuid.uuid4().hex[:8]}"
    campaign.mkdir(parents=True)
    metadata: dict[str, Any] = {
        "status": "running",
        "started_at": datetime.now(UTC).isoformat(),
        "split": split,
        "dataset_sha256": dataset_hash(dataset),
        "planned_cases": case_count,
        "scoring_version": scoring_version,
        "capture_policy": capture_policy,
        "label_hints": label_hints,
        "quantity_protocols": {
            "baseline": baseline_quantity_protocol,
            "candidate": candidate_quantity_protocol,
        },
        "budget_scope": "Shared-ledger snapshots can include concurrent campaigns; they are not per-comparison invoices.",
        "selection_protocol": "Use dev for tuning; lock configuration before test. Completion alone does not prove a prospective test protocol.",
    }
    failure: BaseException | None = None
    try:
        metadata["source_sha256"] = _source_hashes()
        metadata["budget_before"] = ledger.snapshot()
        write_json(campaign / "campaign.json", metadata)
        run = run_extraction_comparison(
            dataset,
            baseline,
            candidate,
            campaign / "results",
            split=split,
            seed=seed,
            baseline_name=f"{baseline.settings.model}:{baseline.settings.reasoning_effort}",
            candidate_name=f"{candidate.settings.model}:{candidate.settings.reasoning_effort}",
            scoring_version=scoring_version,
            capture_policy=capture_policy,
            label_hints=label_hints,
        )
        summary = _verified_result(run, campaign / "results", case_count)
        metadata["result"] = run.relative_to(campaign).as_posix()
        failures = {
            name: int(summary[name]["execution_failures"]) for name in ("baseline", "candidate")
        }
        metadata["execution_failures"] = failures
        metadata["functional_suite_status"] = summary.get("functional_suite_status")
        metadata["status"] = "completed_with_failures" if any(failures.values()) else "completed"
    except BaseException as error:
        failure = error
        metadata["status"] = "failed" if isinstance(error, Exception) else "interrupted"
    try:
        metadata["budget_after"] = ledger.snapshot()
    except Exception as error:
        metadata["budget_snapshot_error"] = _message(error)
        if failure is None:
            failure = error
            metadata["status"] = "failed"
    metadata["finished_at"] = datetime.now(UTC).isoformat()
    try:
        current_hashes = _source_hashes()
        metadata["source_files_changed"] = [
            name
            for name in sorted(current_hashes.keys() | metadata.get("source_sha256", {}).keys())
            if current_hashes.get(name) != metadata.get("source_sha256", {}).get(name)
        ]
        if metadata["source_files_changed"] and failure is None:
            metadata["status"] = "source_changed"
            failure = ValueError("Implementation changed during the comparison")
        if dataset_hash(dataset) != metadata["dataset_sha256"] and failure is None:
            metadata["status"] = "input_changed"
            failure = ValueError("Dataset changed during the comparison")
    except Exception as error:
        metadata["source_audit_error"] = _message(error)
        if failure is None:
            failure = error
            metadata["status"] = "failed"
    if failure is not None:
        metadata["error_type"] = type(failure).__name__
        metadata["error"] = _message(failure)
    try:
        write_json(campaign / "campaign.json", metadata)
        files = [
            {
                "path": path.relative_to(campaign).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": hash_file(path),
            }
            for path in sorted(campaign.rglob("*"))
            if path.is_file() and path != campaign / "manifest.json"
        ]
        write_json(
            campaign / "manifest.json",
            {"schema_version": 1, "status": metadata["status"], "files": files},
        )
    except Exception as artifact_error:
        metadata["status"] = "failed"
        with suppress(Exception):
            write_json(campaign / "campaign.json", metadata)
        if failure is None:
            failure = artifact_error
        else:
            failure.add_note(
                f"Some campaign diagnostics could not be finalized: {type(artifact_error).__name__}"
            )
    if failure is not None:
        if isinstance(failure, Exception):
            raise ComparisonRunError(campaign, _message(failure)) from failure
        failure.add_note(f"Comparison diagnostics: {campaign}")
        raise failure
    return campaign
