"""Isolated, original synthetic input for legacy workflow regression tests."""

import socket
from pathlib import Path
from shutil import copytree

import pytest


@pytest.fixture(autouse=True)
def offline_tests(monkeypatch):
    """Ordinary tests never use model credentials or network sockets."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    def blocked(*args, **kwargs):
        raise AssertionError("Network access is disabled in the public test suite")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture
def synthetic_project(tmp_path: Path) -> Path:
    return Path(copytree(Path(__file__).parent / "fixtures" / "synthetic_project", tmp_path / "synthetic_project"))
