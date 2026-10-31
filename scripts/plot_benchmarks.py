"""Render reviewed timing JSON with matplotlib (optional report dependency)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", type=Path, required=True)
    parser.add_argument("--retrieval", type=Path, required=True)
    parser.add_argument("--output-stem", type=Path, required=True)
    args = parser.parse_args()
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    matplotlib.rcParams.update({"font.size": 10, "svg.hashsalt": "manuscriptforge"})
    workflow = json.loads(args.workflow.read_text(encoding="utf-8"))
    if isinstance(workflow, dict):
        workflow = workflow["rows"]
    retrieval = json.loads(args.retrieval.read_text(encoding="utf-8"))
    figure, axes = plt.subplots(1, 3, figsize=(12, 4.3))
    for axis, mode, title in zip(
        axes[:2], ("cold", "warm"), ("Cold workflow", "Verified cache reuse"), strict=True
    ):
        rows = sorted(
            (row for row in workflow if row["mode"] == mode), key=lambda row: row["workers"]
        )
        medians = [row["median_seconds"] for row in rows]
        errors = [
            [row["median_seconds"] - row["min_seconds"] for row in rows],
            [row["max_seconds"] - row["median_seconds"] for row in rows],
        ]
        bars = axis.bar(
            [str(row["workers"]) for row in rows],
            medians,
            yerr=errors,
            capsize=4,
            color="#246a73",
            width=0.6,
        )
        axis.bar_label(bars, labels=[f"{value:.2f}" for value in medians], padding=5)
        axis.set(title=title, xlabel="Worker processes", ylabel="Seconds (median of two)")
        axis.set_ylim(0, max(row["max_seconds"] for row in rows) * 1.25)
    names = ("legacy_scan", "indexed_jaccard", "indexed_bm25")
    medians = [retrieval["median_seconds"][name] * 1000 for name in names]
    bars = axes[2].bar(
        ("Scan", "Jaccard", "BM25"), medians, color=("#697586", "#246a73", "#376ca6"), width=0.6
    )
    axes[2].bar_label(bars, labels=[f"{value:.2f}" for value in medians], padding=5)
    axes[2].set(title="Local evidence retrieval", ylabel="Milliseconds (median query)")
    axes[2].set_ylim(0, max(medians) * 1.25)
    for axis in axes:
        axis.spines[["top", "right"]].set_visible(False)
        axis.set_axisbelow(True)
        axis.grid(axis="y", alpha=0.18)
    figure.suptitle(
        "Measured workflow and retrieval performance", fontsize=16, fontweight="bold", y=0.99
    )
    figure.text(
        0.5,
        0.91,
        "Synthetic offline workload · Windows x64 · Hardware-specific observations",
        ha="center",
        color="#52606d",
    )
    figure.text(
        0.5,
        0.02,
        "Workflow whiskers: observed min–max. Retrieval: 10,000 records / 30 queries; Jaccard top-10 matches the scan. No relevance-quality claim.",
        ha="center",
        fontsize=8,
    )
    figure.tight_layout(rect=(0, 0.06, 1, 0.9))
    args.output_stem.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(
        args.output_stem.with_suffix(".svg"), metadata={"Date": None, "Creator": "ManuscriptForge"}
    )
    svg = args.output_stem.with_suffix(".svg")
    svg.write_text(
        "\n".join(line.rstrip() for line in svg.read_text(encoding="utf-8").splitlines()) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    figure.savefig(
        args.output_stem.with_suffix(".png"), dpi=160, metadata={"Software": "ManuscriptForge"}
    )
    plt.close(figure)
    print(args.output_stem.with_suffix(".svg"))


if __name__ == "__main__":
    main()
