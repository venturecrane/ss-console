from __future__ import annotations

import json
from pathlib import Path

import pytest

from medchron import budget as budget_mod, dag
from medchron.state import RunState, state_path
from medchron_testkit import PRICING, write_ledger


# ---- state -------------------------------------------------------------------
def test_state_round_trips_and_is_atomic(tmp_path: Path) -> None:
    p = state_path(tmp_path, "s", "u")
    st = RunState.load_or_new(p, slug="s", unit="u")
    st.start("download", input_sha="abc")
    st.finish("download", status="done", exit_code=0, dollars=0.0, pages=3)
    st.start("vision", input_sha="abc")  # killed mid-stage: left running
    again = RunState.load_or_new(p, slug="s", unit="u")
    assert again.is_done("download")
    assert again.stage("vision").status == "running"
    assert again.stage("vision").attempts == 1
    assert not list(p.parent.glob("*.tmp"))


def test_invalidate_reopens_only_done_stages(tmp_path: Path) -> None:
    st = RunState.load_or_new(state_path(tmp_path, "s", "u"), slug="s", unit="u")
    st.finish("audit", status="done", exit_code=0, dollars=1.0, pages=1)
    st.finish("coverage_gate", status="held", exit_code=1, dollars=1.0, pages=1)
    st.invalidate(["audit", "coverage_gate"])
    assert st.stage("audit").status == "pending"
    assert st.stage("coverage_gate").status == "held"


def test_outcome_vocabulary_is_closed(tmp_path: Path) -> None:
    st = RunState.load_or_new(state_path(tmp_path, "s", "u"), slug="s", unit="u")
    with pytest.raises(ValueError):
        st.end("succeeded")
    with pytest.raises(ValueError):
        st.finish("x", status="ok", exit_code=0, dollars=None, pages=None)


# ---- pricing / budget ----------------------------------------------------------
def _pricing(tmp_path: Path) -> budget_mod.Pricing:
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING))
    return budget_mod.Pricing.load(p)


def test_price_row_matches_the_pipeline_formula(tmp_path: Path) -> None:
    pr = _pricing(tmp_path)
    # 1M in + 1M out on opus-5 = $5 + $25 = $30; batch halves it.
    row = {"model": "claude-opus-5", "in": 1_000_000, "out": 1_000_000, "cache_read": 0, "cache_write": 0}
    assert pr.price_row(row) == pytest.approx(30.0)
    assert pr.price_row({**row, "batch": True}) == pytest.approx(15.0)
    # cache write 1.25x input rate, cache read 0.10x.
    row2 = {"model": "claude-sonnet-5", "in": 0, "out": 0, "cache_read": 1_000_000, "cache_write": 1_000_000}
    assert pr.price_row(row2) == pytest.approx(2.0 * 1.25 + 2.0 * 0.10)


def test_unknown_model_refuses_instead_of_pricing_at_zero(tmp_path: Path) -> None:
    pr = _pricing(tmp_path)
    with pytest.raises(budget_mod.BudgetError, match="refusing to price it at zero"):
        pr.price_row({"model": "claude-fable-9", "in": 10, "out": 10})


def test_dated_model_id_matches_by_prefix(tmp_path: Path) -> None:
    pr = _pricing(tmp_path)
    assert pr.rate_for("claude-haiku-4-5-20251001").input_per_million_cents == 80


def test_missing_multipliers_refuse(tmp_path: Path) -> None:
    bad = {"_meta": {"units": "x"}, "models": PRICING["models"]}
    p = tmp_path / "p.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(budget_mod.BudgetError, match="multipliers"):
        budget_mod.Pricing.load(p)


def test_budget_reads_incrementally_and_trips_the_cap(tmp_path: Path) -> None:
    pr = _pricing(tmp_path)
    root = tmp_path / "data"
    (root / "example-matter").mkdir(parents=True)
    ledger = write_ledger(
        root,
        "alpha",
        [
            {"stage": "compose", "model": "claude-opus-5", "in": 1_000_000, "out": 0},
        ],
    )
    b = budget_mod.Budget(
        pr, cap_usd=8.0, ledgers=[ledger, root / "usage-ledger-orphan.jsonl"], usd_per_million_chars=10.0
    )
    assert b.refresh() == pytest.approx(5.0)
    b.check(stage="vision")  # under the cap
    write_ledger(root, "alpha", [{"stage": "audit", "model": "claude-sonnet-5", "in": 2_000_000, "out": 0}])
    assert b.refresh() == pytest.approx(9.0)
    with pytest.raises(budget_mod.BudgetError, match="cap 8.00 USD reached"):
        b.check(stage="map")
    assert b.by_stage()["audit"] == pytest.approx(4.0)


def test_budget_counts_the_orphan_ledger(tmp_path: Path) -> None:
    pr = _pricing(tmp_path)
    root = tmp_path / "data"
    root.mkdir()
    orphan = root / "usage-ledger-orphan.jsonl"
    orphan.write_text(json.dumps({"stage": "merge", "model": "claude-sonnet-5", "in": 500_000, "out": 0}) + "\n")
    b = budget_mod.Budget(pr, cap_usd=100.0, ledgers=[orphan], usd_per_million_chars=10.0)
    assert b.refresh() == pytest.approx(1.0)


def test_projection_refuses_before_the_first_paid_stage(tmp_path: Path) -> None:
    pr = _pricing(tmp_path)
    b = budget_mod.Budget(pr, cap_usd=20.0, ledgers=[], usd_per_million_chars=10.0)
    with pytest.raises(budget_mod.BudgetError, match="projection 30.00 USD"):
        b.check(stage="vision", extracted_chars=3_000_000)


def test_pages_and_chars_from_extracted(tmp_path: Path) -> None:
    p = tmp_path / "extracted.jsonl"
    p.write_text(json.dumps({"id": "a", "pages": 3, "chars": 100}) + "\n" + json.dumps({"id": "b", "pages": 4}) + "\n")
    assert budget_mod.pages_read(p) == 7
    assert budget_mod.extracted_chars(p) == 100


# ---- dag ---------------------------------------------------------------------
def test_dag_is_well_formed() -> None:
    assert dag.validate_dag() == []


def test_dag_encodes_the_runbook_invariants() -> None:
    o = dag.ORDER
    assert o.index("vision") < o.index("billing_extract") < o.index("build_units")
    assert o.index("map") < o.index("repair_truncated") < o.index("assemble")
    assert o.index("filter") < o.index("exhibits")
    assert o.index("condense") < o.index("summarize")
    assert o.index("strip_falsify") < o.index("strip_dry") < o.index("strip_apply")
    assert o.index("coverage_gate") < o.index("audit") < o.index("render")
    assert "audit" in dag.BY_NAME["build_doc"].invalidates
    assert dag.BY_NAME["repair_truncated"].requires == ("map",)  # unconditional, never gated on map output


def test_paid_stages_are_the_model_stages() -> None:
    paid = {s.name for s in dag.STAGES if s.paid}
    assert paid == {
        "vision",
        "billing_extract",
        "map",
        "repair_truncated",
        "merge",
        "filter",
        "condense",
        "summarize",
        "classify_scanned",
        "audit",
    }
