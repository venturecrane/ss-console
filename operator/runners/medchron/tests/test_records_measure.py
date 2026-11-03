"""The pre-flight's price of a page (records/measure.py): the higher of the
firm's figure and the lane's own measurement, a declined page counted as a
second attempt, recorded once per finished job. Names are invented."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from medchron import proof_mode
from medchron.records import measure
from medchron.records.run import RecordsRun
from records_testkit import FakeFinderModel, FakeParseClient, FakeSmokeball, job_dir, pdf, setup_env

ONE = {
    "items": [
        {
            "asked": "the 3/3/2021 Northside Clinic visit",
            "hints": {"dates": ["3/3/2021"], "provider": "Northside Clinic", "pages": ""},
            "for_another_person": False,
        }
    ]
}
REQUEST = "Please send the 3/3/2021 Northside Clinic visit."


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: FakeFinderModel, pages: int, log: list[str]):
    monkeypatch.delenv(proof_mode.PROOF_ENV, raising=False)
    env = setup_env(tmp_path, monkeypatch, model)
    sb = FakeSmokeball()
    sb.add(1, "Northside Clinic records.pdf", pdf(pages))
    r = RecordsRun(
        job_dir(tmp_path, request_text=REQUEST),
        inputs_dir=str(env["inputs"]),
        pricing=str(env["pricing"]),
        state_dir=env["state"],
        client=sb,
        model_client=FakeParseClient(ONE),
        log=log.append,
    )
    return r, env, json.loads(r.run().to_json())


def test_the_price_is_the_higher_of_the_firm_and_the_measured_figure(tmp_path: Path) -> None:
    state = tmp_path / "state"
    p = measure.price(state, 0.009)
    # Not yet measured: the firm's figure, at the worst case of 2 attempts a page.
    assert p.per_page == pytest.approx(0.018) and "not yet measured" in p.source
    state.mkdir()
    (state / measure.NAME).write_text(json.dumps({"dollars": 5.09, "attempts": 100, "pages": 46, "refused": 23}))
    p = measure.price(state, 0.009)
    # 100 attempts over 46 distinct pages: 2.17 attempts a page, as measured.
    assert p.per_attempt == pytest.approx(0.0509) and p.attempts_per_page == pytest.approx(100 / 46)
    assert "measured" in p.source
    assert measure.price(state, 0.10).per_page == pytest.approx(0.10 * 100 / 46)  # the firm's rate, measured attempts


def test_a_finished_job_adds_its_measurement_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r, env, _v = _run(tmp_path, monkeypatch, FakeFinderModel(refuse=frozenset({3, 4})), 10, [])
    m = json.loads((env["state"] / measure.NAME).read_text())
    assert m["pages"] == 10 and m["refused"] == 2 and m["attempts"] >= 10 and m["dollars"] > 0
    assert measure.record_job(r.state, r.data, r.ledger.path, r.pricing) is False


def test_the_preflight_logs_the_price_it_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log: list[str] = []
    _run(tmp_path, monkeypatch, FakeFinderModel(), 10, log)
    assert any(
        m.startswith("[preflight] priced at $0.0200 a page (the firm's usd_per_page, not yet measured; 2.00 attempts")
        for m in log
    )


def test_after_the_worst_shape_the_next_projection_still_covers_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One declined page in every batch of 47: 94 attempts. The measurement
    that job leaves must price the same shape at no less than it cost."""
    r, env, _v = _run(tmp_path, monkeypatch, FakeFinderModel(refuse=frozenset({1, 9, 17, 25, 33, 41})), 47, [])
    dollars, attempts = measure.job_spend(r.ledger.path, r.pricing)
    assert attempts == 94
    p = measure.price(env["state"], float(r.firm.usd_per_page))
    assert "measured" in p.source and p.attempts_per_page == pytest.approx(2.0)
    assert 47 * p.per_page >= dollars
