"""Build-independent smoke check of one wheel or sdist in a fresh environment.

Dependency installation may use the package index. The subsequent CLI and demo
checks run outside the checkout, without model credentials or network sockets.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import venv
from pathlib import Path

PROBE = '''
from pathlib import Path
import socket
import sys
import manuscriptforge
from manuscriptforge.cli import app
from manuscriptforge.demo import run_demo
from manuscriptforge.config import validate_project
from manuscriptforge.pipeline.pilot_prep import prep_pilot
from manuscriptforge.research.demo import prepare_research_demo
from manuscriptforge.research.pipeline import research_engine, run_research
from manuscriptforge.research.search import search_run
from typer.testing import CliRunner

def blocked(*args, **kwargs):
    raise AssertionError("Installed smoke check attempted network access")

socket.socket.connect = blocked
socket.create_connection = blocked
module = Path(manuscriptforge.__file__).resolve()
assert module.is_relative_to(Path(sys.prefix).resolve()), str(module)
help_result = CliRunner().invoke(app, ["--help"], terminal_width=120)
assert help_result.exit_code == 0, help_result.output
assert "campaign-" not in help_result.output
assert "demo" in help_result.output
first = run_demo(Path.cwd() / "synthetic-demo")
second = run_demo(Path.cwd() / "synthetic-demo")
assert first == second
pilot = prep_pilot(Path.cwd() / "pilot", "bioinformatics_methods")
assert validate_project(pilot.project_dir).ok
research = prepare_research_demo(Path.cwd() / "research-demo")
run = run_research(research)
assert run.status == "succeeded"
assert research_engine(research).verify_run(run.run_id).valid
assert search_run(research, run.run_id, "records")["hits"]
cached = run_research(research)
assert all(node.status == "cached" for node in cached.nodes.values())
print("Installed import:", module)
print("PASS: installed resources, legacy and research demos, provenance verification, evidence search, full cache reuse")
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("distribution", type=Path)
    args = parser.parse_args()
    distribution = args.distribution.resolve(strict=True)
    if not (distribution.name.endswith(".whl") or distribution.name.endswith(".tar.gz")):
        parser.error("Provide a built wheel or source distribution")
    with tempfile.TemporaryDirectory(prefix="manuscriptforge-installed-") as temporary:
        root = Path(temporary)
        environment = root / "env"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "OPENAI_API_KEY", "OPENAI_MODEL", "MANUSCRIPTFORGE_KEY_FILE"}}
        env["PYTHONNOUSERSITE"] = "1"
        subprocess.run([str(python), "-m", "pip", "install", str(distribution)], cwd=root, env=env, check=True)
        subprocess.run([str(python), "-m", "pip", "check"], cwd=root, env=env, check=True)
        probe = root / "check.py"
        probe.write_text(PROBE, encoding="utf-8")
        subprocess.run([str(python), str(probe)], cwd=root, env=env, check=True)


if __name__ == "__main__":
    main()
