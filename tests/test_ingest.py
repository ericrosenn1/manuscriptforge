from manuscriptforge.ingest.project_ingest import ingest_project
from manuscriptforge.ingest.table_ingest import ingest_result_tables
from manuscriptforge.utils.io import create_run_dir


def test_table_ingest_reads_example_csv(synthetic_project) -> None:
    project_dir = synthetic_project
    tables, evidence = ingest_result_tables(project_dir)
    assert tables
    assert tables[0]["row_count"] == 3
    assert evidence


def test_project_ingest_writes_intermediate(tmp_path, synthetic_project) -> None:
    project_dir = synthetic_project
    run_dir = create_run_dir(tmp_path)
    ingested = ingest_project(project_dir, run_dir)
    assert ingested["text_inputs"]["abstract"]["text"]
    assert (run_dir / "intermediate" / "ingested_project.json").exists()
