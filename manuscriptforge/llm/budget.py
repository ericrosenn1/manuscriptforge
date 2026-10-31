"""Transactional, fail-closed spending reservations shared by model workers.

Amounts are integer nano-dollars in SQLite, avoiding floating point accounting.
Each attempt reserves a conservative upper bound *before* dispatch. Unknown or
ambiguous outcomes retain that reservation: a network timeout does not prove an
API request was unbilled. The ledger records metadata, never prompts or secrets.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from importlib.resources import files
from pathlib import Path
from typing import Any

NANO = Decimal(1_000_000_000)
MILLION = Decimal(1_000_000)


class BudgetError(RuntimeError):
    """A request cannot safely be admitted under the configured budget."""


def _positive_money(value: str | Decimal | int) -> int:
    amount = Decimal(str(value))
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Budget must be a finite positive USD amount")
    return int((amount * NANO).to_integral_value(rounding=ROUND_CEILING))


def _count(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BudgetError(f"{name} must be a nonnegative integer")
    return value


def _usd(value: int) -> str:
    return format(Decimal(value) / NANO, "f")


@dataclass(frozen=True)
class ModelPrice:
    input: Decimal
    output: Decimal
    cached_input: Decimal
    max_cache_write: Decimal

    def quote(self, inputs: int, outputs: int, *, cached: int = 0, reserve: bool = False) -> int:
        if cached > inputs:
            raise BudgetError("Cached input exceeds total input tokens")
        # Long-context rates apply to the entire request, not just its excess.
        input_multiplier = Decimal(2) if inputs > 272_000 else Decimal(1)
        output_multiplier = Decimal("1.5") if inputs > 272_000 else Decimal(1)
        input_rate = max(self.input, self.max_cache_write) if reserve else self.input
        cost = ((inputs - cached) * input_rate + cached * self.cached_input) * input_multiplier
        cost += outputs * self.output * output_multiplier
        return int((cost / MILLION * NANO).to_integral_value(rounding=ROUND_CEILING))


def default_prices() -> tuple[dict[str, ModelPrice], dict[str, Any]]:
    payload = json.loads(files("manuscriptforge").joinpath("data/model_pricing.json").read_text())
    prices = {
        model: ModelPrice(**{key: Decimal(value) for key, value in rates.items()})
        for model, rates in payload["models"].items()
    }
    return prices, payload


class BudgetLedger:
    """One immutable campaign cap, safely shared across processes and retries."""

    def __init__(self, path: Path, limit_usd: str | Decimal | int = "100") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.limit_nano = _positive_money(limit_usd)
        self.prices, self.price_source = default_prices()
        self.price_hash = hashlib.sha256(
            json.dumps(self.price_source, sort_keys=True).encode()
        ).hexdigest()
        with self._transaction() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS policy (id INTEGER PRIMARY KEY CHECK(id=1), limit_nano INTEGER NOT NULL, pricing_hash TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, created_utc TEXT NOT NULL, model TEXT NOT NULL, request_hash TEXT NOT NULL, attempt INTEGER NOT NULL, input_bound INTEGER NOT NULL, output_bound INTEGER NOT NULL, reserved_nano INTEGER NOT NULL, charged_nano INTEGER, status TEXT NOT NULL, result_json TEXT)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO policy VALUES (1, ?, ?)", (self.limit_nano, self.price_hash)
            )
            row = connection.execute(
                "SELECT limit_nano, pricing_hash FROM policy WHERE id=1"
            ).fetchone()
            if row != (self.limit_nano, self.price_hash):
                raise BudgetError(
                    "Existing budget/pricing policy differs; use the original policy or a new ledger"
                )

    @contextmanager
    def _transaction(self):
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def before_request(self, record: dict[str, Any]) -> str:
        model = str(record.get("model", ""))
        if model not in self.prices:
            raise BudgetError(f"No verified price policy for model {model!r}")
        input_bound = (
            _count(record.get("input_bytes_upper_bound"), "input_bytes_upper_bound") + 2048
        )
        output_bound = _count(record.get("max_output_tokens"), "max_output_tokens")
        if output_bound == 0 or output_bound > 128_000:
            raise BudgetError("Output token bound must be between 1 and 128000")
        if input_bound > 272_000:
            raise BudgetError(
                "Paid campaign requests are limited to the documented short-context tier"
            )
        request_hash = str(record.get("request_hash", ""))
        if len(request_hash) != 64 or any(c not in "0123456789abcdef" for c in request_hash):
            raise BudgetError("A SHA-256 request fingerprint is required")
        attempt = _count(record.get("attempt", 0), "attempt")
        reserved = self.prices[model].quote(input_bound, output_bound, reserve=True)
        identity = uuid.uuid4().hex
        with self._transaction() as connection:
            committed = connection.execute(
                "SELECT COALESCE(SUM(COALESCE(charged_nano, reserved_nano)), 0) FROM requests"
            ).fetchone()[0]
            if committed + reserved > self.limit_nano:
                raise BudgetError(
                    f"Budget would be exceeded: available USD {_usd(self.limit_nano - committed)}, request reservation USD {_usd(reserved)}"
                )
            connection.execute(
                "INSERT INTO requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 'reserved', NULL)",
                (
                    identity,
                    datetime.now(UTC).isoformat(),
                    model,
                    request_hash,
                    attempt,
                    input_bound,
                    output_bound,
                    reserved,
                ),
            )
        return identity

    def on_result(self, record: dict[str, Any]) -> None:
        identity = str(record.get("reservation_id", ""))
        status = str(record.get("status", "error"))
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT model, input_bound, output_bound, reserved_nano, charged_nano, status, result_json FROM requests WHERE id=?",
                (identity,),
            ).fetchone()
            if row is None:
                raise BudgetError("Result refers to an unknown reservation")
            model, input_bound, output_bound, reserved, charged, old_status, old_result = row
            safe = {
                key: record[key]
                for key in (
                    "reservation_id",
                    "model",
                    "response_model",
                    "response_id",
                    "input_tokens",
                    "output_tokens",
                    "cached_input_tokens",
                    "reasoning_tokens",
                    "status",
                    "error_class",
                    "elapsed_seconds",
                )
                if key in record
            }
            encoded = json.dumps(safe, sort_keys=True, allow_nan=False)
            if old_status == "settled":
                if old_result == encoded:
                    return
                raise BudgetError("Reservation already settled with different usage")
            if (
                status == "error"
                or record.get("input_tokens") is None
                or record.get("output_tokens") is None
            ):
                connection.execute(
                    "UPDATE requests SET status='uncertain', result_json=? WHERE id=?",
                    (encoded, identity),
                )
                return
            inputs = _count(record["input_tokens"], "input_tokens")
            outputs = _count(record["output_tokens"], "output_tokens")
            cached = _count(record.get("cached_input_tokens") or 0, "cached_input_tokens")
            if inputs > input_bound or outputs > output_bound:
                connection.execute(
                    "UPDATE requests SET status='uncertain', result_json=? WHERE id=?",
                    (encoded, identity),
                )
                return
            if record.get("model") != model or record.get("response_model") not in {None, model}:
                # A changed/aliased model needs reviewed pricing, not a guessed rate.
                connection.execute(
                    "UPDATE requests SET status='uncertain', result_json=? WHERE id=?",
                    (encoded, identity),
                )
                return
            # Usage counters are not an invoice and may omit separate cache writes.
            actual = self.prices[model].quote(inputs, outputs, cached=cached, reserve=True)
            if actual > reserved:
                raise BudgetError("Reported usage exceeded the admitted reservation")
            connection.execute(
                "UPDATE requests SET status='settled', charged_nano=?, result_json=? WHERE id=?",
                (actual, encoded, identity),
            )

    def snapshot(self) -> dict[str, Any]:
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT status, reserved_nano, charged_nano FROM requests"
            ).fetchall()
        committed = sum(
            charged if charged is not None else reserved for _, reserved, charged in rows
        )
        return {
            "limit_usd": _usd(self.limit_nano),
            "committed_usd": _usd(committed),
            "remaining_usd": _usd(self.limit_nano - committed),
            "settled_usd": _usd(sum(charged or 0 for _, _, charged in rows)),
            "uncertain_reservations": sum(status == "uncertain" for status, _, _ in rows),
            "pending_reservations": sum(status == "reserved" for status, _, _ in rows),
            "attempt_count": len(rows),
            "pricing_sha256": self.price_hash,
            "pricing_source": self.price_source["source"],
            "accounting_kind": "conservative_upper_bound_not_provider_invoice",
        }
