"""Opt-in, bounded model-output capture for local evaluation diagnostics.

Only response fields explicitly supplied here are retained. Requests, prompts,
credentials and HTTP headers are never accepted by this recorder.
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any


class ResponseCapture:
    def __init__(self, *, enabled: bool = False, max_bytes: int = 1_000_000) -> None:
        if max_bytes <= 0:
            raise ValueError("Response capture byte limit must be positive")
        self.enabled = enabled
        self.max_bytes = max_bytes
        self.records: list[dict[str, Any]] = []

    def clear(self) -> None:
        self.records = []

    def record(
        self,
        text: str | None,
        *,
        attempt: int,
        response_id: str | None,
        model: str | None,
        status: str,
        schema_name: str | None,
        schema_sha256: str | None,
        refused: bool = False,
        incomplete: bool = False,
        refusal_text: str | None = None,
        error_class: str | None = None,
        error_code: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        raw = text.encode("utf-8") if text is not None else None
        stored = raw[: self.max_bytes].decode("utf-8", errors="ignore") if raw is not None else None
        refusal_raw = refusal_text.encode("utf-8") if refusal_text is not None else None
        refusal_stored = (
            refusal_raw[: self.max_bytes].decode("utf-8", errors="ignore")
            if refusal_raw is not None
            else None
        )
        self.records.append(
            {
                "attempt": attempt,
                "available": text is not None,
                "output_text": stored,
                "output_sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
                "output_bytes": len(raw) if raw is not None else None,
                "stored_bytes": len(stored.encode("utf-8")) if stored is not None else None,
                "truncated": len(raw) > self.max_bytes if raw is not None else False,
                "response_id": response_id,
                "model": model,
                "status": status,
                "schema_name": schema_name,
                "schema_sha256": schema_sha256,
                "refused": refused,
                "incomplete": incomplete,
                "refusal_text": refusal_stored,
                "refusal_sha256": hashlib.sha256(refusal_raw).hexdigest()
                if refusal_raw is not None
                else None,
                "refusal_bytes": len(refusal_raw) if refusal_raw is not None else None,
                "refusal_truncated": len(refusal_raw) > self.max_bytes
                if refusal_raw is not None
                else False,
                "error_class": error_class,
                "error_code": error_code,
            }
        )

    def mark_error(self, *, error_class: str, error_code: str) -> None:
        if self.enabled and self.records:
            self.records[-1].update(error_class=error_class, error_code=error_code)

    def snapshot(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self.records) if self.enabled else []
