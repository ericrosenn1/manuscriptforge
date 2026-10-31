from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil
import pytest
from pydantic import ValidationError

from manuscriptforge.orchestration import (
    NodeSpec,
    Orchestrator,
    PipelineSpec,
    RunLockedError,
    WorkerContext,
)
from manuscriptforge.orchestration.engine import _terminate_process
from manuscriptforge.orchestration.storage import connect, digest_json, hash_file


def write_stage(context: WorkerContext) -> dict:
    started = time.time()
    time.sleep(float(context.parameters.get("sleep", 0)))
    source = context.parameters.get("source")
    text = (
        context.read_input(str(source)).read_text(encoding="utf-8")
        if source
        else str(context.parameters.get("text", "value"))
    )
    context.output_path("value.json").write_text(json.dumps({"value": text}), encoding="utf-8")
    return {"started": started, "ended": time.time(), "pid": os.getpid()}


def join_stage(context: WorkerContext) -> dict:
    values = [
        json.loads(context.dependency_path(name, "value.json").read_text())["value"]
        for name in sorted(context.dependencies)
    ]
    context.output_path("value.json").write_text(
        json.dumps({"value": "+".join(values)}), encoding="utf-8"
    )
    return {"dependencies": sorted(context.dependencies)}


def retry_stage(context: WorkerContext) -> dict:
    if context.attempt <= int(context.parameters.get("fail_until", 1)):
        raise RuntimeError("Deliberate bounded retry fixture")
    return write_stage(context)


def missing_stage(context: WorkerContext) -> dict:
    return {"intentionally_missing": True}


def mutate_stage(context: WorkerContext) -> dict:
    context.read_input("source.txt").write_text("changed snapshot", encoding="utf-8")
    return write_stage(context)


def sleep_stage(context: WorkerContext) -> dict:
    child = None
    if context.parameters.get("child"):
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        context.output_path("child.json").write_text(
            json.dumps(
                {
                    "pid": child.pid,
                    "started": psutil.Process(child.pid).create_time(),
                }
            ),
            encoding="utf-8",
        )
    try:
        time.sleep(float(context.parameters.get("seconds", 0.5)))
        return write_stage(context)
    finally:
        if child is not None:
            child.terminate()
            child.wait(timeout=5)


def scratch_stage(context: WorkerContext) -> dict:
    (context.scratch_dir / "private-copy.txt").write_text("Scratch is not a deliverable")
    return write_stage(context)


HANDLERS = {
    "write": write_stage,
    "join": join_stage,
    "retry": retry_stage,
    "scratch": scratch_stage,
    "missing": missing_stage,
    "mutate": mutate_stage,
    "sleep": sleep_stage,
}


def make_engine(path: Path) -> Orchestrator:
    path.mkdir(parents=True, exist_ok=True)
    return Orchestrator(path, HANDLERS, software_fingerprint={"test_environment": "v1"})


def node(node_id: str = "a", handler: str = "write", **values) -> NodeSpec:
    return NodeSpec(id=node_id, handler=handler, expected_outputs=["value.json"], **values)


def pipeline(*nodes: NodeSpec, max_workers: int = 2) -> PipelineSpec:
    return PipelineSpec(id="test-pipeline", nodes=list(nodes), max_workers=max_workers)


def read_value(record, node_id: str = "a") -> str:
    return json.loads((Path(record.nodes[node_id].artifact_dir) / "value.json").read_text())[
        "value"
    ]


@pytest.mark.parametrize(
    "nodes",
    [
        [node("a", depends_on=["missing"])],
        [node("a", depends_on=["b"]), node("b", depends_on=["a"])],
        [node("a"), node("a")],
    ],
)
def test_invalid_graphs_are_rejected(nodes) -> None:
    with pytest.raises(ValidationError):
        pipeline(*nodes)


@pytest.mark.parametrize(
    "value", ["../source", "/absolute", "C:\\outside", "a/../b", "a//b", "./a"]
)
def test_input_paths_reject_traversal(value) -> None:
    with pytest.raises(ValidationError):
        node(input_paths=[value])


def test_branching_dag_runs_in_parallel_and_joins_verified_artifacts(tmp_path) -> None:
    engine = make_engine(tmp_path / "project")
    spec = pipeline(
        node("a", parameters={"text": "A", "sleep": 0.3}),
        node("b", parameters={"text": "B", "sleep": 0.3}),
        node("joined", "join", depends_on=["a", "b"]),
    )
    result = engine.run(spec)
    assert result.status == "succeeded"
    assert read_value(result, "joined") == "A+B"
    a, b = result.nodes["a"].metadata, result.nodes["b"].metadata
    assert max(a["started"], b["started"]) < min(a["ended"], b["ended"])
    assert engine.verify_run(result.run_id).valid
    pointer = json.loads((engine.project_dir / "outputs/latest.json").read_text())
    assert pointer["manifest_sha256"] == hash_file(Path(pointer["manifest_path"]))


def test_completed_nodes_cache_without_reexecution(tmp_path) -> None:
    engine = make_engine(tmp_path / "project")
    spec = pipeline(node(parameters={"text": "stable"}))
    first = engine.run(spec)
    second = engine.run(spec)
    assert second.status == "succeeded"
    assert second.nodes["a"].status == "cached"
    assert second.nodes["a"].attempts == 0
    assert second.nodes["a"].artifact_dir == first.nodes["a"].artifact_dir
    assert second.nodes["a"].reused_from


def test_optional_curation_changes_invalidate_only_its_descendants(tmp_path) -> None:
    engine = make_engine(tmp_path / "project")
    (engine.project_dir / "source.txt").write_text("source")
    spec = pipeline(
        node("source", input_paths=["source.txt"], parameters={"source": "source.txt"}),
        node("curation", optional_input_paths=["curation.json"]),
        node("joined", "join", depends_on=["source", "curation"]),
    )
    first = engine.run(spec)
    (engine.project_dir / "curation.json").write_text('{"approved":true}')
    second = engine.run(spec)
    assert second.nodes["source"].status == "cached"
    assert second.nodes["curation"].status == "succeeded"
    assert second.nodes["joined"].status == "succeeded"
    assert second.nodes["curation"].fingerprint != first.nodes["curation"].fingerprint
    assert second.nodes["joined"].fingerprint != first.nodes["joined"].fingerprint


def test_retry_is_bounded_and_failed_attempts_remain_inspectable(tmp_path) -> None:
    engine = make_engine(tmp_path / "project")
    result = engine.run(pipeline(node(handler="retry", max_attempts=2)))
    assert result.status == "succeeded"
    assert result.nodes["a"].attempts == 2
    directories = list((Path(result.run_dir) / "attempts/a").iterdir())
    assert len(directories) == 2
    receipts = [json.loads((path / "receipt.json").read_text()) for path in directories]
    assert {receipt["status"] for receipt in receipts} == {"failed", "succeeded"}


def test_retry_exhaustion_blocks_only_dependent_nodes(tmp_path) -> None:
    engine = make_engine(tmp_path / "project")
    result = engine.run(
        pipeline(
            node("failure", "retry", max_attempts=2, parameters={"fail_until": 99}),
            node("independent"),
            node("child", "join", depends_on=["failure"]),
        )
    )
    assert result.status == "failed"
    assert result.nodes["failure"].attempts == 2
    assert result.nodes["failure"].status == "failed"
    assert result.nodes["child"].status == "blocked"
    assert result.nodes["independent"].status == "succeeded"
    assert not (engine.project_dir / "outputs/latest.json").exists()


def test_output_contract_is_checked_before_success(tmp_path) -> None:
    engine = make_engine(tmp_path / "project")
    result = engine.run(pipeline(node(handler="missing")))
    assert result.status == "failed"
    assert "required output" in result.nodes["a"].error


def test_snapshot_mutation_fails_without_changing_original_source(tmp_path) -> None:
    engine = make_engine(tmp_path / "project")
    original = engine.project_dir / "source.txt"
    original.write_text("protected original")
    result = engine.run(pipeline(node(handler="mutate", input_paths=["source.txt"])))
    assert result.status == "failed"
    assert "manifest" in result.nodes["a"].error
    assert original.read_text() == "protected original"


def test_directory_membership_and_parameters_invalidate_cache(tmp_path) -> None:
    engine = make_engine(tmp_path / "project")
    source = engine.project_dir / "corpus"
    source.mkdir()
    (source / "first.txt").write_text("first")
    first = engine.run(pipeline(node(input_paths=["corpus"])))
    (source / "second.txt").write_text("second")
    second = engine.run(pipeline(node(input_paths=["corpus"])))
    third = engine.run(pipeline(node(input_paths=["corpus"], parameters={"text": "changed"})))
    assert second.nodes["a"].status == third.nodes["a"].status == "succeeded"
    assert len({item.nodes["a"].fingerprint for item in (first, second, third)}) == 3


def test_corrupt_cache_is_rejected_and_recomputed_in_new_attempt(tmp_path) -> None:
    engine = make_engine(tmp_path / "project")
    spec = pipeline(node())
    first = engine.run(spec)
    original = Path(first.nodes["a"].artifact_dir) / "value.json"
    original.write_text("corrupt")
    assert not engine.verify_run(first.run_id).valid
    repaired = engine.run(spec, run_id=first.run_id, resume=True)
    assert repaired.status == "succeeded"
    assert repaired.nodes["a"].artifact_dir != first.nodes["a"].artifact_dir
    assert original.read_text() == "corrupt"  # Old evidence is preserved, not overwritten.
    assert read_value(repaired) == "value"


def test_manual_invalidation_reexecutes_descendants_but_preserves_other_branches(tmp_path) -> None:
    engine = make_engine(tmp_path / "project")
    spec = pipeline(node("a"), node("b"), node("child", "join", depends_on=["a"]))
    original = engine.run(spec)
    engine.invalidate(original.run_id, ["a"], "Explicit validation fixture rerun")
    result = engine.run(spec, run_id=original.run_id, resume=True)
    assert result.status == "succeeded"
    assert result.nodes["a"].artifact_dir != original.nodes["a"].artifact_dir
    assert result.nodes["child"].artifact_dir != original.nodes["child"].artifact_dir
    assert result.nodes["b"].artifact_dir == original.nodes["b"].artifact_dir


def test_failed_resume_preserves_prior_published_manifest(tmp_path) -> None:
    engine = make_engine(tmp_path / "project")
    first = engine.run(pipeline(node()))
    pointer_path = engine.project_dir / "outputs/latest.json"
    pointer = pointer_path.read_bytes()
    data = json.loads(pointer)
    manifest = Path(data["manifest_path"])
    result = engine.run(pipeline(node(handler="missing")), run_id=first.run_id, resume=True)
    assert result.status == "failed"
    assert pointer_path.read_bytes() == pointer
    assert hash_file(manifest) == data["manifest_sha256"]
    assert json.loads(manifest.read_text())["status"] == "succeeded"


def process_alive(pid: int, started: float) -> bool:
    try:
        process = psutil.Process(pid)
        return (
            abs(process.create_time() - started) < 0.01 and process.status() != psutil.STATUS_ZOMBIE
        )
    except psutil.Error:
        return False


def kill_identified_process(pid: int, started: float) -> None:
    process = psutil.Process(pid)
    assert abs(process.create_time() - started) < 0.01
    process.kill()


def test_hard_timeout_terminates_worker_and_its_child(tmp_path) -> None:
    engine = make_engine(tmp_path / "project")
    start = time.monotonic()
    result = engine.run(
        pipeline(
            node(handler="sleep", parameters={"seconds": 30, "child": True}, timeout_seconds=3)
        )
    )
    assert result.status == "failed"
    assert time.monotonic() - start < 10
    assert "deadline" in result.nodes["a"].error
    child_files = list(Path(result.run_dir).glob("attempts/a/*/artifacts/child.json"))
    assert child_files, "The child must actually have started for this timeout test"
    child = json.loads(child_files[0].read_text())
    assert not process_alive(child["pid"], child["started"])


def test_cancellation_can_resume_without_rerunning_completed_nodes(tmp_path) -> None:
    engine = make_engine(tmp_path / "project")
    cancellation = threading.Event()
    timer = threading.Timer(2.0, cancellation.set)
    timer.start()
    try:
        result = engine.run(
            pipeline(
                node("complete"),
                node("wait", "sleep", depends_on=["complete"], parameters={"seconds": 30}),
            ),
            cancel_event=cancellation,
        )
    finally:
        timer.cancel()
    assert result.status == "cancelled"
    assert result.nodes["complete"].status == "succeeded"
    assert result.nodes["wait"].status == "cancelled"
    resumed = engine.run(
        pipeline(
            node("complete"),
            node("wait", "sleep", depends_on=["complete"], parameters={"seconds": 0.01}),
        ),
        run_id=result.run_id,
        resume=True,
    )
    assert resumed.status == "succeeded"
    assert resumed.nodes["complete"].artifact_dir == result.nodes["complete"].artifact_dir


def crash_spec() -> PipelineSpec:
    return pipeline(
        node("complete"),
        node("inflight", "sleep", depends_on=["complete"], parameters={"seconds": 2}),
    )


def watchdog_spec() -> PipelineSpec:
    return pipeline(
        node("orphan", "sleep", parameters={"seconds": 30, "child": True}, timeout_seconds=3)
    )


def crash_controller_entry(project: str, watchdog: bool = False) -> None:
    root = Path(project)
    root.mkdir(parents=True, exist_ok=True)
    (root / "controller.json").write_text(
        json.dumps({"pid": os.getpid(), "started": psutil.Process().create_time()})
    )
    make_engine(root).run(watchdog_spec() if watchdog else crash_spec(), run_id="crash-resume")


def wait_until(predicate, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.025)
    raise AssertionError("Condition was not satisfied before its bounded deadline")


def test_controller_crash_adopts_inflight_completion_and_preserves_completed_stage(
    tmp_path,
) -> None:
    project = tmp_path / "project"
    engine = make_engine(project)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        str(Path(path or os.getcwd()).resolve()) for path in sys.path
    )
    command = [
        sys.executable,
        "-c",
        "from test_orchestration import crash_controller_entry; "
        f"crash_controller_entry({str(project)!r})",
    ]
    controller = subprocess.Popen(
        command, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:

        def worker_running():
            with connect(engine.db_path) as connection:
                return (
                    connection.execute(
                        "SELECT 1 FROM attempts WHERE node_id='inflight' AND pid IS NOT NULL"
                    ).fetchone()
                    is not None
                )

        wait_until(worker_running)
        before = engine.status("crash-resume")
        identity = json.loads((project / "controller.json").read_text())
        assert process_alive(identity["pid"], identity["started"])
        kill_identified_process(identity["pid"], identity["started"])
        controller.wait(timeout=5)
        result = engine.run(crash_spec(), run_id="crash-resume", resume=True)
        assert result.status == "succeeded"
        assert result.nodes["complete"].artifact_dir == before.nodes["complete"].artifact_dir
        assert result.nodes["inflight"].attempts == 1
    finally:
        engine.cancel("crash-resume")
        if controller.poll() is None:
            controller.kill()
        controller.wait(timeout=5)


def test_second_scheduler_cannot_take_live_run_ownership(tmp_path) -> None:
    engine = make_engine(tmp_path / "project")
    spec = pipeline(node(handler="sleep", parameters={"seconds": 30}))
    results = []
    failures = []

    def execute():
        try:
            results.append(engine.run(spec, run_id="owned-run"))
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=execute)
    thread.start()
    try:

        def started():
            with connect(engine.db_path) as connection:
                return (
                    connection.execute(
                        "SELECT 1 FROM attempts WHERE run_id='owned-run' AND pid IS NOT NULL"
                    ).fetchone()
                    is not None
                )

        wait_until(started)
        other = make_engine(engine.project_dir)
        with pytest.raises(RunLockedError):
            other.run(spec, run_id="owned-run", resume=True)
    finally:
        engine.cancel("owned-run")
        thread.join(timeout=10)
    assert not thread.is_alive()
    assert not failures
    assert results[0].status == "cancelled"


def test_reused_pid_identity_never_terminates_unrelated_process() -> None:
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        actual = psutil.Process(process.pid).create_time()
        assert not _terminate_process(process.pid, actual - 100, "not-an-owned-job.json")
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_sequential_limit_prevents_stage_overlap(tmp_path) -> None:
    engine = make_engine(tmp_path / "project")
    result = engine.run(
        pipeline(
            node("a", parameters={"sleep": 0.15}),
            node("b", parameters={"sleep": 0.15}),
            max_workers=1,
        )
    )
    a, b = result.nodes["a"].metadata, result.nodes["b"].metadata
    assert a["ended"] <= b["started"]


def test_registry_and_version_control_cache_identity(tmp_path) -> None:
    engine = make_engine(tmp_path / "project")
    with pytest.raises(ValueError, match="trusted registry"):
        engine.run(pipeline(node(handler="unregistered")))
    with pytest.raises(ValueError, match="importable"):
        Orchestrator(engine.project_dir, {"lambda": lambda _context: {}})
    first = engine.run(pipeline(node(version="1", parameters={"x": 1, "y": 2})))
    reordered = engine.run(pipeline(node(version="1", parameters={"y": 2, "x": 1})))
    changed = engine.run(pipeline(node(version="2", parameters={"x": 1, "y": 2})))
    assert reordered.nodes["a"].status == "cached"
    assert reordered.nodes["a"].fingerprint == first.nodes["a"].fingerprint
    assert changed.nodes["a"].status == "succeeded"


def test_operational_settings_do_not_invalidate_successful_artifacts(tmp_path) -> None:
    engine = make_engine(tmp_path / "project")
    first = engine.run(pipeline(node("a", timeout_seconds=30, max_attempts=1), max_workers=1))
    result = engine.run(pipeline(node("a", timeout_seconds=60, max_attempts=3), max_workers=3))
    assert result.nodes["a"].status == "cached"
    assert result.nodes["a"].fingerprint == first.nodes["a"].fingerprint
    assert result.nodes["a"].artifact_dir == first.nodes["a"].artifact_dir


def test_provenance_is_bound_to_node_fingerprint_and_tampering_is_rejected(tmp_path) -> None:
    engine = make_engine(tmp_path / "project")
    result = engine.run(pipeline(node(parameters={"text": "trace me"})))
    provenance_path = Path(result.nodes["a"].provenance_path)
    provenance = json.loads(provenance_path.read_text())
    assert digest_json(provenance["semantic_recipe"]) == result.nodes["a"].fingerprint
    assert provenance["semantic_recipe"]["parameters"] == {"text": "trace me"}
    provenance["semantic_recipe"]["parameters"]["text"] = "changed"
    provenance_path.write_text(json.dumps(provenance))
    assert not engine.verify_run(result.run_id).valid


def test_worker_deadline_survives_scheduler_crash(tmp_path) -> None:
    project = tmp_path / "project"
    engine = make_engine(project)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        str(Path(path or os.getcwd()).resolve()) for path in sys.path
    )
    controller = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from test_orchestration import crash_controller_entry; "
            f"crash_controller_entry({str(project)!r}, watchdog=True)",
        ],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:

        def worker_started():
            with connect(engine.db_path) as connection:
                return (
                    connection.execute(
                        "SELECT 1 FROM attempts WHERE node_id='orphan' AND pid IS NOT NULL"
                    ).fetchone()
                    is not None
                )

        wait_until(worker_started)
        identity = json.loads((project / "controller.json").read_text())
        kill_identified_process(identity["pid"], identity["started"])
        controller.wait(timeout=5)
        with connect(engine.db_path) as connection:
            attempt = connection.execute("SELECT * FROM attempts WHERE node_id='orphan'").fetchone()
        wait_until(lambda: not process_alive(attempt["pid"], attempt["process_start"]), timeout=8)
        child = json.loads((Path(attempt["attempt_dir"]) / "artifacts/child.json").read_text())
        assert not process_alive(child["pid"], child["started"])
        result = engine.run(watchdog_spec(), run_id="crash-resume", resume=True)
        assert result.status == "failed"
        assert result.nodes["orphan"].attempts == 1
    finally:
        engine.cancel("crash-resume")
        if controller.poll() is None:
            controller.kill()
        controller.wait(timeout=5)


def test_concurrent_runs_publish_a_complete_latest_manifest_without_races(tmp_path) -> None:
    first_engine = make_engine(tmp_path / "project")
    second_engine = make_engine(first_engine.project_dir)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            first_engine.run,
            pipeline(node(parameters={"text": "first", "sleep": 0.2})),
            run_id="parallel-a",
        )
        second = pool.submit(
            second_engine.run,
            pipeline(node(parameters={"text": "second", "sleep": 0.2})),
            run_id="parallel-b",
        )
        results = [first.result(timeout=15), second.result(timeout=15)]
    assert all(result.status == "succeeded" for result in results)
    assert all(first_engine.verify_run(result.run_id).valid for result in results)
    expected = max(results, key=lambda item: (item.updated_at, item.run_id))
    pointer = json.loads((first_engine.project_dir / "outputs/latest.json").read_text())
    assert pointer["run_id"] == expected.run_id
    manifest = Path(pointer["manifest_path"])
    assert pointer["manifest_sha256"] == hash_file(manifest)
    assert json.loads(manifest.read_text())["status"] == "succeeded"


def test_private_scratch_does_not_enter_artifact_inventory(tmp_path) -> None:
    engine = make_engine(tmp_path / "project")
    result = engine.run(pipeline(node(handler="scratch")))
    assert result.status == "succeeded"
    assert [artifact.path for artifact in result.nodes["a"].artifacts] == ["value.json"]
    assert (Path(result.nodes["a"].artifact_dir).parent / "scratch/private-copy.txt").exists()


def test_symlink_input_cannot_escape_selected_project(tmp_path) -> None:
    engine = make_engine(tmp_path / "project")
    outside = tmp_path / "outside.txt"
    outside.write_text("Do not ingest")
    link = engine.project_dir / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Host does not permit test symlinks")
    with pytest.raises(ValueError, match="leaves|links|junctions"):
        engine.run(pipeline(node(input_paths=["linked.txt"])))
