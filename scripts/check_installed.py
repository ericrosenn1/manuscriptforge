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
print("Installed import:", module)
print("PASS: help, installed resources, original synthetic demo and reproducible rerun")
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
        env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "OPENAI_API_KEY", "OPENAI_MODEL"}}
        env["PYTHONNOUSERSITE"] = "1"
        subprocess.run([str(python), "-m", "pip", "install", str(distribution)], cwd=root, env=env, check=True)
        subprocess.run([str(python), "-m", "pip", "check"], cwd=root, env=env, check=True)
        probe = root / "check.py"
        probe.write_text(PROBE, encoding="utf-8")
        subprocess.run([str(python), str(probe)], cwd=root, env=env, check=True)


if __name__ == "__main__":
    main()
