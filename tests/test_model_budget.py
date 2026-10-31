from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from manuscriptforge.llm.budget import BudgetError, BudgetLedger


def request(**overrides):
    return {
        "model": "gpt-6-luna",
        "input_bytes_upper_bound": 20,
        "max_output_tokens": 100,
        "request_hash": "a" * 64,
        "attempt": 1,
    } | overrides


def test_shared_ledger_never_admits_more_than_cap(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.sqlite", "0.005")

    def reserve(_):
        try:
            return BudgetLedger(ledger.path, "0.005").before_request(request())
        except BudgetError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        identities = list(pool.map(reserve, range(40)))
    summary = ledger.snapshot()
    assert 0 < len([value for value in identities if value]) < 40
    assert Decimal(summary["committed_usd"]) <= Decimal("0.005")
    assert summary["pending_reservations"] == summary["attempt_count"]


def test_settlement_releases_unused_reservation_and_is_idempotent(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.sqlite", "1")
    identity = ledger.before_request(request())
    before = Decimal(ledger.snapshot()["committed_usd"])
    result = {
        "reservation_id": identity,
        "model": "gpt-6-luna",
        "response_model": "gpt-6-luna",
        "input_tokens": 100,
        "output_tokens": 10,
        "cached_input_tokens": 0,
        "status": "completed",
    }
    ledger.on_result(result)
    after = ledger.snapshot()
    ledger.on_result(result)
    assert ledger.snapshot() == after
    assert Decimal(after["committed_usd"]) < before
    assert after["pending_reservations"] == 0
    assert "not_provider_invoice" in after["accounting_kind"]
    with pytest.raises(BudgetError, match="already settled"):
        ledger.on_result(result | {"output_tokens": 9})


@pytest.mark.parametrize(
    "result",
    [
        {"status": "error", "error_class": "APITimeoutError"},
        {"status": "incomplete", "input_tokens": None, "output_tokens": 20},
        {"status": "completed", "input_tokens": 10_000, "output_tokens": 1},
        {"status": "completed", "input_tokens": 1, "output_tokens": 101},
        {
            "status": "completed",
            "input_tokens": 1,
            "output_tokens": 1,
            "response_model": "unpriced-model",
        },
    ],
)
def test_uncertain_or_out_of_contract_usage_preserves_full_reservation(tmp_path, result):
    ledger = BudgetLedger(tmp_path / "budget.sqlite", "1")
    identity = ledger.before_request(request())
    before = ledger.snapshot()["committed_usd"]
    ledger.on_result({"reservation_id": identity, "model": "gpt-6-luna"} | result)
    after = ledger.snapshot()
    assert after["committed_usd"] == before
    assert after["uncertain_reservations"] == 1


def test_existing_cap_and_price_policy_cannot_be_silently_changed(tmp_path):
    path = tmp_path / "budget.sqlite"
    BudgetLedger(path, "100")
    BudgetLedger(path, "100.00")
    with pytest.raises(BudgetError, match="policy differs"):
        BudgetLedger(path, "101")


@pytest.mark.parametrize(
    "overrides",
    [
        {"model": "unknown"},
        {"input_bytes_upper_bound": -1},
        {"input_bytes_upper_bound": True},
        {"input_bytes_upper_bound": 300_000},
        {"max_output_tokens": 0},
        {"max_output_tokens": 128_001},
        {"request_hash": "invalid"},
    ],
)
def test_unknown_prices_and_invalid_bounds_fail_closed(tmp_path, overrides):
    ledger = BudgetLedger(tmp_path / "budget.sqlite", "1")
    with pytest.raises(BudgetError):
        ledger.before_request(request(**overrides))
    assert ledger.snapshot()["attempt_count"] == 0


def test_each_retry_gets_a_new_reservation(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.sqlite", "1")
    first = ledger.before_request(request())
    ledger.on_result({"reservation_id": first, "status": "error"})
    second = ledger.before_request(request(attempt=2))
    assert first != second
    assert ledger.snapshot()["attempt_count"] == 2
    assert ledger.snapshot()["pending_reservations"] == 1


def test_unrecognized_results_and_inconsistent_cache_usage_raise(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget.sqlite", "1")
    with pytest.raises(BudgetError, match="unknown reservation"):
        ledger.on_result({"reservation_id": "absent"})
    identity = ledger.before_request(request())
    with pytest.raises(BudgetError, match="Cached input"):
        ledger.on_result(
            {
                "reservation_id": identity,
                "model": "gpt-6-luna",
                "status": "completed",
                "input_tokens": 1,
                "output_tokens": 1,
                "cached_input_tokens": 2,
            }
        )
    assert ledger.snapshot()["pending_reservations"] == 1
