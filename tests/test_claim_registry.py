from manuscriptforge.evidence.claim_registry import build_claim_registry
from manuscriptforge.ingest.project_ingest import ingest_project
from manuscriptforge.utils.io import create_run_dir


def test_claim_registry_creates_stable_ids(tmp_path, synthetic_project) -> None:
    project_dir = synthetic_project
    run_a = create_run_dir(tmp_path / "a")
    run_b = create_run_dir(tmp_path / "b")
    claims_a = build_claim_registry(project_dir, run_a, ingest_project(project_dir, run_a))
    claims_b = build_claim_registry(project_dir, run_b, ingest_project(project_dir, run_b))
    assert claims_a
    assert [claim.claim_id for claim in claims_a] == [claim.claim_id for claim in claims_b]
    assert (run_a / "claim_registry.xlsx").exists()


def test_claim_registry_adds_table_aggregate_claims(tmp_path, synthetic_project) -> None:
    project_dir = synthetic_project
    run_dir = create_run_dir(tmp_path)
    claims = build_claim_registry(project_dir, run_dir, ingest_project(project_dir, run_dir))
    claim_text = "\n".join(claim.claim_text for claim in claims)
    assert "met the adjusted_p_value <= 0.05 threshold" in claim_text
    assert "largest absolute log2_fold_change" in claim_text
