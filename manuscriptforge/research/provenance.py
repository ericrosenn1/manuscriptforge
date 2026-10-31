"""Portable ancestor provenance for a bundle created before finalization."""

from __future__ import annotations

from pathlib import Path

from manuscriptforge.orchestration import WorkerContext
from manuscriptforge.orchestration.models import RunRecord
from manuscriptforge.orchestration.storage import contained_file, digest_json
from manuscriptforge.utils.io import read_json, write_json


def copy_stage_provenance(context: WorkerContext, package: Path) -> dict[str, int]:
    """Copy verified immutable receipts; never label in-progress export as final."""
    run_root = context.project_dir.parent.parent
    state_root = run_root.parent.parent
    run = RunRecord.model_validate(read_json(contained_file(run_root, "run.json")))
    if run.run_id != context.run_id:
        raise ValueError("Export provenance belongs to another run")
    records = []
    for node_id, node in sorted(run.nodes.items()):
        if node_id == context.node_id:
            continue
        if node.status not in {"succeeded", "cached"} or not node.provenance_path:
            raise ValueError(f"Ancestor provenance is not complete: {node_id}")
        path = Path(node.provenance_path)
        if not path.is_absolute() or not path.resolve().is_relative_to(state_root.resolve()):
            raise ValueError("Ancestor provenance leaves the workflow state directory")
        path = contained_file(state_root, path.relative_to(state_root).as_posix())
        receipt = read_json(path)
        if (
            receipt.get("semantic_fingerprint") != node.fingerprint
            or digest_json(receipt.get("semantic_recipe")) != node.fingerprint
        ):
            raise ValueError(f"Ancestor provenance fingerprint mismatch: {node_id}")
        write_json(package / "provenance" / f"{node_id}.json", receipt)
        records.append(
            {
                "node_id": node_id,
                "status": node.status,
                "fingerprint": node.fingerprint,
                "reused_from": node.reused_from,
                "artifacts": [artifact.model_dump(mode="json") for artifact in node.artifacts],
            }
        )
    write_json(
        package / "execution_snapshot.json",
        {
            "schema_version": 1,
            "run_id": run.run_id,
            "pipeline_id": run.pipeline_id,
            "snapshot_fingerprint": run.snapshot_fingerprint,
            "record_kind": "completed_ancestors_before_export_finalization",
            "nodes": records,
            "final_status_location": "Authoritative finalized run manifest remains in the workflow engine; this bundle cannot contain its own final hash.",
        },
    )
    return {"ancestor_receipts": len(records)}
