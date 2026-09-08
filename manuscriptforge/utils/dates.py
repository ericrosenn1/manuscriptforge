from __future__ import annotations

from datetime import UTC, datetime


def run_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def utc_iso() -> str:
    return datetime.now(UTC).isoformat()
