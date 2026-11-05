"""The two money reads answered in code (``money_reads``): a matter's expense
totals over the whole ledger, and the trust balance against the firm's
authored floor.

What these defend: a total is computed over EVERY page and never given for a
ledger that could not be read to its end; the low-trust decision is
``availableBalance < floor`` made here; an unauthored floor is null with
"no floor authored", never a default; two personas disagreeing is no floor.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from smokeball_connector import expense_ledger, money_reads


class _Client:
    def __init__(self, *, expenses: list[dict[str, Any]] | None = None, balances: list[dict[str, Any]] | None = None):
        self.expenses = expenses or []
        self.balances = balances or []
        self.gets: list[tuple[str, dict[str, Any]]] = []

    def get(self, path: str, **params: Any) -> Any:
        self.gets.append((path, params))
        if path.endswith("/expenses"):
            offset, limit = int(params.get("Offset") or 0), int(params.get("Limit") or 500)
            return {"value": self.expenses[offset : offset + limit]}
        if path.endswith("/matter-balances"):
            return {"value": [dict(b) for b in self.balances]}
        if path.startswith("/matters/"):
            return {"id": path.rsplit("/", 1)[1], "number": "2026-PI-7"}
        raise AssertionError(f"unscripted GET {path}")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> _Client:
    c = _Client()
    monkeypatch.setattr(money_reads, "_client", lambda: c)
    monkeypatch.setattr(money_reads, "_attach_refs", lambda _c, resp, mid: resp.update(matterNumber="2026-PI-7"))
    return c


def _floor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *floors: Any) -> None:
    personas = ""
    for i, floor in enumerate(floors):
        settings = f"\n        settings:\n          trust_floor_usd: {floor}" if floor is not None else ""
        personas += f"  - slug: p{i}\n    skills:\n      - name: trust-balance-nudge\n        enabled: true{settings}\n"
    path = tmp_path / "customer.yaml"
    path.write_text("personas:\n" + personas)
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))


def _expense(i: int, price: Any, **kw: Any) -> dict[str, Any]:
    return {"id": f"e{i}", "price": price, "quantity": 1, **kw}


def test_the_totals_cover_every_page_of_the_ledger(client: _Client) -> None:
    client.expenses = [_expense(i, 1.10, costType="Hard", finalized=True) for i in range(500)]
    client.expenses += [_expense(900, "96.25", costType="Soft"), _expense(901, 10, isDeleted=True)]
    out = money_reads.get_expense_totals("m-7")
    assert out["listingComplete"] is True and out["entries"] == 501 and out["deletedExcluded"] == 1
    assert out["total_amount"] == "646.25"
    assert out["finalized_amount"] == "550.00" and out["unfinalized_amount"] == "96.25"
    assert out["by_cost_type"] == [{"cost_type": "Hard", "amount": "550.00"}, {"cost_type": "Soft", "amount": "96.25"}]
    assert [p["Offset"] for _, p in client.gets] == [0, 500]
    assert out["matterNumber"] == "2026-PI-7"


def test_a_ledger_that_cannot_be_read_to_its_end_has_no_total(client: _Client, monkeypatch: pytest.MonkeyPatch) -> None:
    def incomplete(*_a: Any) -> Any:
        raise expense_ledger.ExpenseListingIncomplete("the expense listing repeated a page")

    monkeypatch.setattr(money_reads, "read_whole_ledger_counted", incomplete)
    out = money_reads.get_expense_totals("m-7")
    assert out["listingComplete"] is False and "total_amount" not in out


def test_an_entry_with_no_readable_amount_gives_no_total(client: _Client) -> None:
    client.expenses = [_expense(1, "96.25"), _expense(2, "n/a")]
    out = money_reads.get_expense_totals("m-7")
    assert out["unreadableAmounts"] == 1 and "total_amount" not in out


def test_a_balance_under_the_floor_is_low_and_says_the_shortfall(
    client: _Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _floor(tmp_path, monkeypatch, 2500)
    client.balances = [{"matterId": "m-7", "balance": 3000, "protectedBalance": 1000, "availableBalance": 2000}]
    out = money_reads.get_matter_balances("bank-1", matter_id="m-7")
    assert out["floor_usd"] == "2500.00" and out["low_trust"] is True and out["shortfall_usd"] == "500.00"
    assert out["value"][0]["low_trust"] is True


def test_a_balance_at_the_floor_is_not_low(client: _Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _floor(tmp_path, monkeypatch, 2500)
    client.balances = [{"matterId": "m-7", "balance": 9000, "protectedBalance": 6500, "availableBalance": 2500}]
    out = money_reads.get_matter_balances("bank-1", matter_id="m-7")
    assert out["low_trust"] is False and out["shortfall_usd"] is None, "balance alone would have read as plenty"


def test_no_authored_floor_decides_nothing(client: _Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _floor(tmp_path, monkeypatch, None)
    client.balances = [{"matterId": "m-7", "availableBalance": 1}]
    out = money_reads.get_matter_balances("bank-1", matter_id="m-7")
    assert out["low_trust"] is None and out["floor_usd"] is None and out["reason"] == "no floor authored"
    assert out["value"][0]["low_trust"] is None


def test_two_personas_with_different_floors_author_no_floor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _floor(tmp_path, monkeypatch, 2500, 3000)
    assert money_reads.trust_floor() == (None, "personas author different trust floors")
    _floor(tmp_path, monkeypatch, 2500, 2500)
    assert money_reads.trust_floor()[0] == 2500


@pytest.mark.parametrize("bad", ["'2500'", "0", "-5", "true"])
def test_a_floor_that_is_not_a_positive_number_is_no_floor(
    bad: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _floor(tmp_path, monkeypatch, bad)
    assert money_reads.trust_floor() == (None, "trust_floor_usd is not a positive number")


def test_an_unreadable_balance_is_never_assumed(
    client: _Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _floor(tmp_path, monkeypatch, 2500)
    client.balances = [{"matterId": "m-7", "availableBalance": None}]
    out = money_reads.get_matter_balances("bank-1", matter_id="m-7")
    assert out["low_trust"] is None and "did not read" in out["reason"]
