"""Research commands: execution, resumption, verification, and evaluation."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Literal, cast

import typer
from rich.console import Console
from rich.table import Table

from manuscriptforge.llm.budget import BudgetLedger
from manuscriptforge.orchestration.models import RunRecord
from manuscriptforge.research.pipeline import build_research_plan, research_engine, run_research
from manuscriptforge.research.providers import load_configured_key, research_provider
from manuscriptforge.research.settings import ResearchSettings

app = typer.Typer(help="Resumable multi-node research workflows and auditable evaluations.")
console = Console()


def _verified_claims(project: Path, run_id: str, *, raw: bool = False):
    from manuscriptforge.models.claim import ScientificClaim
    from manuscriptforge.orchestration.storage import contained_file, hash_file
    from manuscriptforge.utils.io import read_json

    engine = research_engine(project)
    run = engine.status(run_id)
    node = run.nodes.get("claims")
    if node is None or node.status not in {"succeeded", "cached"} or not node.artifact_dir:
        raise ValueError("The selected run has no completed claim registry")
    name = "claim_registry_unreviewed.json" if raw else "claim_registry.json"
    artifact = next((item for item in node.artifacts if item.path == name), None)
    if artifact is None:
        raise ValueError("This run predates claim review; create a new research run")
    directory = Path(node.artifact_dir)
    if not directory.resolve().is_relative_to(engine.state_dir.resolve()):
        raise ValueError("Claim registry leaves the workflow state directory")
    path = contained_file(directory, name)
    if hash_file(path) != artifact.sha256:
        raise ValueError("Claim registry failed its recorded hash check")
    values = read_json(path)
    if hash_file(path) != artifact.sha256 or not isinstance(values, list):
        raise ValueError("Claim registry changed or is malformed")
    return [ScientificClaim.model_validate(value) for value in values]


def _show(result: RunRecord) -> None:
    table = Table(title=f"Research run {result.run_id}: {result.status}")
    for column in ("Node", "Status", "Attempts", "Artifacts"):
        table.add_column(column)
    for node in result.nodes.values():
        table.add_row(node.id, node.status, str(node.attempts), str(len(node.artifacts)))
    console.print(table)
    console.print(f"Run: {result.run_dir}")
    console.print(
        "Computational completion does not establish scientific validity. Review candidate findings."
    )
    if result.status != "succeeded":
        for node in result.nodes.values():
            if node.error:
                console.print(f"[red]{node.id}: {node.error}[/red]")
        raise typer.Exit(1)


@app.command("plan")
def plan(project: Path) -> None:
    """Show the trusted workflow and declared inputs without executing it."""
    console.print_json(build_research_plan(project, ResearchSettings()).model_dump_json())


@app.command("claims")
def claims(project: Path, run_id: str) -> None:
    """Inspect verified extracted claims and their current review status."""
    try:
        values = _verified_claims(project, run_id)
        console.print_json(
            json.dumps(
                [
                    {
                        "claim_id": item.claim_id,
                        "section": item.section_target,
                        "support": item.support_strength,
                        "needs_human_review": item.needs_human_review,
                        "text": item.claim_text,
                    }
                    for item in values
                ]
            )
        )
    except (ValueError, OSError) as error:
        console.print(f"Claim inspection failed: {error}", style="red", markup=False)
        raise typer.Exit(1) from error


@app.command("review-claim")
def review_claim(
    project: Path,
    run_id: str,
    claim_id: str,
    decision: Annotated[str, typer.Option()],
    support: Annotated[str, typer.Option()],
    reviewer: Annotated[str, typer.Option()],
    reason: Annotated[str, typer.Option()],
    section: str | None = None,
) -> None:
    """Record an explicit, source-bound author decision; never edit source claims."""
    from manuscriptforge.config import load_project_config
    from manuscriptforge.models.claim import SupportStrength
    from manuscriptforge.research.claim_reviews import append_claim_review

    try:
        original = _verified_claims(project, run_id, raw=True)
        selected = next((item for item in original if item.claim_id == claim_id), None)
        if selected is None:
            raise ValueError("Claim ID is not present in the selected run")
        if (
            section is not None
            and section not in load_project_config(project).manuscript.include_sections
        ):
            raise ValueError("The requested section is not enabled in project.yaml")
        review = append_claim_review(
            project.resolve() / "claim_reviews.jsonl",
            selected,
            decision=cast(Literal["approve", "reject"], decision),
            support_strength=cast(SupportStrength, support),
            reviewer=reviewer,
            reason=reason,
            section_target=section,
        )
    except (ValueError, OSError) as error:
        console.print(f"Claim review failed: {error}", style="red", markup=False)
        raise typer.Exit(1) from error
    console.print(f"Recorded review {review.review_id}. Run or resume the workflow to apply it.")


@app.command("demo")
def demo(
    project: Path, run: bool = True, workers: Annotated[int, typer.Option(min=1, max=8)] = 2
) -> None:
    """Create an original synthetic research project and optionally run it offline."""
    from manuscriptforge.research.demo import prepare_research_demo

    selected = prepare_research_demo(project)
    console.print(f"Synthetic project ready: {selected}")
    if run:
        _show(run_research(selected, ResearchSettings(max_workers=workers)))


@app.command("run")
def run(
    project: Path,
    provider: str = "mock",
    model: str = "gpt-6-luna",
    workers: Annotated[int, typer.Option(min=1, max=8)] = 2,
    allow_network: bool = False,
    budget_usd: str | None = None,
    ledger: Path | None = None,
    no_cache: bool = False,
) -> None:
    """Execute an immutable multi-node manuscript run; paid calls need consent and a ledger."""
    try:
        settings = ResearchSettings(
            provider=cast(Literal["mock", "openai"], provider),
            model=model,
            max_workers=workers,
            allow_network=allow_network,
            use_cache=not no_cache,
            budget_limit_usd=Decimal(budget_usd) if budget_usd else None,
            budget_ledger_path=str(ledger.resolve()) if ledger else None,
        )
        _show(run_research(project, settings))
    except (ValueError, OSError, RuntimeError, InvalidOperation) as error:
        console.print(f"[red]Research run failed: {error}[/red]")
        raise typer.Exit(1) from error


@app.command("resume")
def resume(project: Path, run_id: str) -> None:
    """Resume with recorded settings; reuse only verified successful nodes."""
    try:
        _show(run_research(project, run_id=run_id, resume=True))
    except (ValueError, OSError, RuntimeError) as error:
        console.print(f"[red]Could not resume: {error}[/red]")
        raise typer.Exit(1) from error


@app.command("status")
def status(project: Path, run_id: str) -> None:
    """Read durable state without launching workers."""
    console.print_json(research_engine(project).status(run_id).model_dump_json())


@app.command("cancel")
def cancel(project: Path, run_id: str) -> None:
    """Request termination of this run's owned workers; completed work is retained."""
    research_engine(project).cancel(run_id)
    console.print(f"Cancellation requested for {run_id}.")


@app.command("verify")
def verify(project: Path, run_id: str) -> None:
    """Rehash completed node artifacts and report integrity errors."""
    result = research_engine(project).verify_run(run_id)
    console.print_json(result.model_dump_json())
    if not result.valid:
        raise typer.Exit(1)


@app.command("budget")
def budget(ledger: Path, limit_usd: str = "100") -> None:
    """Inspect conservative committed cost without altering an existing cap."""
    console.print_json(json.dumps(BudgetLedger(ledger, limit_usd).snapshot()))


@app.command("search")
def search(
    project: Path,
    run_id: str,
    query: str,
    limit: Annotated[int, typer.Option(min=1, max=100)] = 5,
) -> None:
    """Search the verified evidence index from one successful run."""
    from manuscriptforge.research.search import search_run

    try:
        console.print_json(json.dumps(search_run(project, run_id, query, limit)))
    except (ValueError, OSError, RuntimeError) as error:
        console.print(f"[red]Evidence search failed: {error}[/red]")
        raise typer.Exit(1) from error


@app.command("propose-facts")
def propose_fact_command(
    project: Path,
    run_id: str,
    query: str,
    output: Path,
    provider: str = "mock",
    model: str = "gpt-6-luna",
    allow_network: bool = False,
    budget_usd: str | None = None,
    ledger: Path | None = None,
    limit: Annotated[int, typer.Option(min=1, max=20)] = 5,
) -> None:
    """Extract quote-anchored candidates without promoting them into accepted claims."""
    from manuscriptforge.research.proposals import propose_facts

    try:
        settings = ResearchSettings.model_validate(
            {
                "provider": provider,
                "model": model,
                "allow_network": allow_network,
                "budget_limit_usd": budget_usd,
                "budget_ledger_path": str(ledger.resolve()) if ledger else None,
            }
        )
        result = propose_facts(project, run_id, query, output, settings, limit=limit)
    except (ValueError, OSError, RuntimeError) as error:
        console.print(f"[red]Fact proposal extraction failed: {error}[/red]")
        raise typer.Exit(1) from error
    console.print(f"Candidate proposals and provenance: {result}")


@app.command("evaluate")
def evaluate(
    output: Path,
    candidate: str = "mock",
    model: str = "gpt-6-luna",
    allow_network: bool = False,
    budget_usd: str | None = None,
    ledger: Path | None = None,
    dataset: Path | None = None,
    credentials_from: Path = Path("."),
) -> None:
    """Run a held-out paired evaluation; negative results stay in the report."""
    from manuscriptforge.evaluation.models import EvaluationDataset
    from manuscriptforge.evaluation.runner import run_comparison
    from manuscriptforge.evaluation.synthetic import build_synthetic_dataset
    from manuscriptforge.llm.evaluation_adapter import GroundedTaskCandidate
    from manuscriptforge.llm.grounded import mock_grounded_provider

    selected = (
        EvaluationDataset.model_validate_json(dataset.read_text(encoding="utf-8"))
        if dataset
        else build_synthetic_dataset()
    )
    settings = ResearchSettings(
        provider=cast(Literal["mock", "openai"], candidate),
        model=model,
        allow_network=allow_network,
        budget_limit_usd=Decimal(budget_usd) if budget_usd else None,
        budget_ledger_path=str(ledger.resolve()) if ledger else None,
    )
    if candidate == "openai" and not load_configured_key(credentials_from):
        raise typer.BadParameter(
            "OPENAI_API_KEY is not configured; do not pass secrets as CLI arguments"
        )
    result = run_comparison(
        selected,
        GroundedTaskCandidate(mock_grounded_provider()),
        GroundedTaskCandidate(research_provider(settings)),
        output,
        baseline_name="extractive_replay",
        candidate_name=f"openai:{model}" if candidate == "openai" else "extractive_replay",
    )
    console.print(f"Evaluation artifacts: {result}")
    console.print(
        "Inspect summary.json and the Markdown report. Synthetic functional results do not establish scientific validity."
    )


@app.command("compare-models")
def compare_model_command(
    output: Path,
    ledger: Path,
    budget_usd: str,
    baseline: str = "gpt-6-luna",
    candidate: str = "gpt-6.1-sol",
    split: str = "dev",
    allow_network: bool = False,
    dataset: Path | None = None,
    credentials_from: Path = Path("."),
    seed: int = 20261031,
    scoring_version: str = "2.0-source-only",
    capture_policy: str = "off",
    baseline_quantity_protocol: str = "2.1",
    candidate_quantity_protocol: str = "2.1",
    label_hints: str | None = None,
    no_label_hints: bool = False,
) -> None:
    """Compare models on raw sources and hidden reference facts under one cost cap."""
    from manuscriptforge.evaluation.extraction_runner import LABEL_HINTS, CapturePolicy
    from manuscriptforge.evaluation.extraction_scoring import ExtractionProtocol
    from manuscriptforge.evaluation.models import EvaluationDataset, Split
    from manuscriptforge.evaluation.synthetic import build_synthetic_dataset
    from manuscriptforge.research.comparison import compare_models

    if split not in {"train", "dev", "test"}:
        raise typer.BadParameter("Split must be train, dev, or test")
    if scoring_version not in {"2.0-source-only", "2.1-quantities"}:
        raise typer.BadParameter("Unsupported scoring version")
    if capture_policy not in {"off", "failures", "all"}:
        raise typer.BadParameter("Capture policy must be off, failures, or all")
    if no_label_hints and label_hints is not None:
        raise typer.BadParameter("Choose either --label-hints or --no-label-hints")
    if any(
        value not in {"2.0", "2.1"}
        for value in (baseline_quantity_protocol, candidate_quantity_protocol)
    ):
        raise typer.BadParameter("Quantity protocols must be 2.0 or 2.1")
    try:
        common = {
            "provider": "openai",
            "allow_network": allow_network,
            "budget_limit_usd": budget_usd,
            "budget_ledger_path": str(ledger.resolve()),
            "max_output_tokens": 2048,
        }
        left = ResearchSettings.model_validate(common | {"model": baseline})
        right = ResearchSettings.model_validate(common | {"model": candidate})
        if not load_configured_key(credentials_from):
            raise ValueError(
                "OPENAI_API_KEY is not configured; do not pass secrets as CLI arguments"
            )
        selected = (
            EvaluationDataset.model_validate_json(dataset.read_text(encoding="utf-8"))
            if dataset
            else build_synthetic_dataset()
        )
        result = compare_models(
            selected,
            left,
            right,
            output,
            split=cast(Split, split),
            seed=seed,
            scoring_version=cast(ExtractionProtocol, scoring_version),
            capture_policy=cast(CapturePolicy, capture_policy),
            baseline_quantity_protocol=cast(Literal["2.0", "2.1"], baseline_quantity_protocol),
            candidate_quantity_protocol=cast(Literal["2.0", "2.1"], candidate_quantity_protocol),
            label_hints=""
            if no_label_hints
            else LABEL_HINTS
            if label_hints is None
            else label_hints,
        )
    except (ValueError, OSError, RuntimeError) as error:
        console.print(f"[red]Model comparison failed: {error}[/red]")
        raise typer.Exit(1) from error
    console.print(f"Source-only comparison and budget provenance: {result}")


@app.command("train-style")
def train_style(
    output: Path, dataset: Path | None = None, synthetic: bool = False, seed: int = 20261031
) -> None:
    """Fit a regularized preference model from explicit labeled pairs."""
    from manuscriptforge.learning import (
        LearningConfig,
        PairDataset,
        fit_ranker,
        synthetic_preferences,
        write_training_run,
    )

    if (dataset is None) == (not synthetic):
        raise typer.BadParameter("Choose either --dataset PATH or --synthetic, never both")
    selected = (
        PairDataset.model_validate_json(dataset.read_text(encoding="utf-8"))
        if dataset
        else synthetic_preferences()
    )
    model, report = fit_ranker(selected, LearningConfig(seed=seed))
    destination = write_training_run(output, selected, model, report)
    console.print(f"Preference model and held-out report: {destination}")
    console.print(
        "Synthetic training verifies the learning mechanism, not real-author effectiveness."
    )


@app.command("rank-style")
def rank_style(model: Path, candidates: Path, output: Path | None = None) -> None:
    """Rank supplied prose without rewriting it or asserting factual correctness."""
    from manuscriptforge.learning import load_model, rank_texts
    from manuscriptforge.utils.io import write_json

    if candidates.stat().st_size > 10 * 1024 * 1024:
        raise typer.BadParameter("Candidate JSON exceeds 10 MB")
    texts = json.loads(candidates.read_text(encoding="utf-8"))
    if (
        not isinstance(texts, list)
        or not 1 <= len(texts) <= 1000
        or not all(isinstance(text, str) and text.strip() for text in texts)
    ):
        raise typer.BadParameter("Candidates must be a JSON list of 1–1000 nonempty strings")
    result = {
        "ranking": rank_texts(load_model(model), texts),
        "interpretation": "Preference ranking only; no factual or scientific validation.",
    }
    if output:
        if output.exists():
            raise typer.BadParameter("Output exists; choose a new path")
        write_json(output, result)
        console.print(f"Ranking saved: {output}")
    else:
        console.print_json(json.dumps(result))
