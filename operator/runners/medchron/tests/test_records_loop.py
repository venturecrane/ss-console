"""The second A&P dry run (2026-11-03, proof 20261103T213633Z): a 47-page file
with 23 pages the model declined, read until the cap was gone (149 requests,
$5.09), the record found on the readable pages, and the job FAILED
match_unsettled over one page the wire never returned. Pinned here, with
invented names:

* a declined page is never asked for again, and the loop ends when the file is
  settled or a pass makes no progress, inside ``ceil(pages / 100) + 2`` calls;
* a page never reached is recorded unreadable ("not reached"), never retried;
* a record found on the readable pages is DELIVERED, cut, and the reply says
  how many pages of the file could not be read;
* every page request leaves a ledger row with its tokens and its page count,
  and the job's cents are that ledger, priced;
* the pre-flight prices a page at the higher of the firm's figure and this
  lane's own measurement, counting a declined page as a second attempt.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from medchron import proof_mode
from medchron.ledger import read_rows
from medchron.records import measure
from medchron.records.run import RecordsRun
from records_testkit import FakeFinderModel, FakeParseClient, FakeSmokeball, job_dir, pdf, setup_env

PAGES = 47
#: 23 of 47 declined, as on the seat: every page from 15 to 37.
REFUSED = frozenset(range(15, 38))
ONE_ITEM = {
    "items": [
        {
            "asked": "the 3/3/2021 Northside Clinic visit",
            "hints": {"dates": ["3/3/2021"], "provider": "Northside Clinic", "pages": ""},
            "for_another_person": False,
        }
    ]
}


def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: FakeFinderModel) -> dict[str, Path]:
    monkeypatch.delenv(proof_mode.PROOF_ENV, raising=False)
    return setup_env(tmp_path, monkeypatch, model)


def _run(tmp_path: Path, env: dict[str, Path], sb: Any, *, parse: Any = None, log: list[str] | None = None, **job: Any):
    r = RecordsRun(
        job_dir(tmp_path, **job),
        inputs_dir=str(env["inputs"]),
        pricing=str(env["pricing"]),
        state_dir=env["state"],
        client=sb,
        model_client=parse or FakeParseClient(ONE_ITEM),
        log=(log.append if log is not None else lambda _m: None),
    )
    return r, json.loads(r.run().to_json())


def _seat(pages: int = PAGES) -> FakeSmokeball:
    sb = FakeSmokeball()
    sb.add(1, "Northside Clinic records.pdf", pdf(pages))
    return sb


def test_declined_pages_are_never_asked_for_again_and_the_calls_are_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = FakeFinderModel(refuse=REFUSED)
    env = _env(tmp_path, monkeypatch, model)
    r, v = _run(tmp_path, env, _seat())
    calls = model.index_calls()
    # One pass: 6 batches of 8; each batch holding a declined page is then read
    # a page at a time (8 + 8 + 8 + 1 + ... at most 47), and never again.
    assert calls <= 6 + PAGES
    asked = [
        int(b["text"].split()[1].rstrip(":"))
        for body in model.bodies
        if "pages" in body["output_config"]["format"]["schema"]["properties"]
        for b in body["messages"][0]["content"]
        if b["type"] == "text" and b["text"].startswith("Page ")
    ]
    assert max(asked.count(p) for p in REFUSED) <= 2  # its batch, then alone: once each
    # The same request again: every page already settled, nothing is read.
    model.bodies.clear()
    r2, _ = _run(tmp_path / "again", env, _seat())
    assert model.index_calls() == 0
    # Spend is the ledger, priced, and every page request left a row with tokens.
    rows = [row for row in read_rows(r.ledger.path) if row.get("pages")]
    assert len(rows) == calls and all(row["in"] > 0 for row in rows)
    assert v["cents"] == int(round(r.budget.refresh() * 100)) and v["cents"] > 0


def test_a_record_found_on_the_readable_pages_is_delivered_and_the_gap_is_said(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = FakeFinderModel(refuse=frozenset({3, 4}))
    env = _env(tmp_path, monkeypatch, model)
    r, v = _run(tmp_path, env, _seat(10))
    assert v["verdict"] == "delivered", v
    assert v["items"] == [{"n": 1, "outcome": "attached", "files": 1}]
    assert [c["pages"] for c in v["cuts"]] and all(c["pages"] for c in v["cuts"])
    text = json.loads((r.data / "facts.json").read_text())["reply_text"]
    assert "Could not be read:\n- 2 pages of Northside Clinic records.pdf could not be read" in text


def test_a_page_the_wire_never_returns_is_recorded_not_reached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from smokeball_connector import page_finder as pf

    model = FakeFinderModel(fail=frozenset({47}))
    env = _env(tmp_path, monkeypatch, model)
    log: list[str] = []
    r, v = _run(tmp_path, env, _seat(), log=log)
    assert v["verdict"] == "delivered", v  # never match_unsettled over one page
    sha = json.loads((r.data / "files.json").read_text())[_seat().rows[0]["id"]]["sha256"]
    index = pf.load_index(sha)
    assert index["unreadable"]["47"] == "not reached" and index["refusals"]["47"] == pf.REFUSAL_TRIES
    # Bounded: ceil(47 / 100) + 1 build passes, the last asking only for page 47.
    assert sum(1 for m in log if "not reached" in m) == 1
    calls = model.index_calls()
    model.bodies.clear()
    _run(tmp_path / "again", env, _seat())
    # Pass 1: 6 batches, the last (41-47) failing and read alone (7); pass 2:
    # page 47 alone, no progress, recorded not reached. Then nothing again.
    assert model.index_calls() == 0 and calls == 6 + 7 + 1


def test_the_cap_hooks_ledger_rows_carry_tokens_and_page_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from medchron.records.run import CapHook

    model = FakeFinderModel()
    env = _env(tmp_path, monkeypatch, model)
    r = RecordsRun(
        job_dir(tmp_path),
        inputs_dir=str(env["inputs"]),
        pricing=str(env["pricing"]),
        state_dir=env["state"],
        client=_seat(),
        model_client=FakeParseClient(ONE_ITEM),
        log=lambda _m: None,
    )
    # The page finder's real usage shape (page_finder._post: the Messages usage keys, plus pages).
    CapHook(r).after("claude-opus-5", {"input_tokens": 12_800, "output_tokens": 640, "pages": 8})
    (row,) = read_rows(r.ledger.path)
    assert row["in"] == 12_800 and row["out"] == 640 and row["pages"] == 8 and row["model"] == "claude-opus-5"
    assert r.budget.refresh() > 0


def test_the_preflight_prices_at_the_higher_of_the_firm_and_the_measured_figure(tmp_path: Path) -> None:
    state = tmp_path / "state"
    # No measurement yet: the firm's figure stands.
    p = measure.price(state, 0.009)
    assert p.per_page == pytest.approx(0.009) and "firm" in p.source
    # A measured lane: $5.09 over 100 page attempts, 23 of 46 pages declined.
    state.mkdir()
    (state / measure.NAME).write_text(json.dumps({"dollars": 5.09, "attempts": 100, "pages": 46, "refused": 23}))
    p = measure.price(state, 0.009)
    assert p.per_attempt == pytest.approx(0.0509) and p.attempts_per_page == pytest.approx(1.5)
    assert p.per_page == pytest.approx(0.07635) and "measured" in p.source
    # A firm figure above the measured one wins, still with the declined share.
    assert measure.price(state, 0.10).per_page == pytest.approx(0.15)


def test_a_finished_job_adds_its_measurement_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    model = FakeFinderModel(refuse=REFUSED)
    env = _env(tmp_path, monkeypatch, model)
    r, _v = _run(tmp_path, env, _seat())
    m = json.loads((env["state"] / measure.NAME).read_text())
    assert m["pages"] == PAGES and m["refused"] == len(REFUSED) and m["attempts"] >= PAGES and m["dollars"] > 0
    assert measure.record_job(r.state, r.data, r.ledger.path, r.pricing) is False  # once per job


def test_the_preflight_logs_the_price_it_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    model = FakeFinderModel()
    env = _env(tmp_path, monkeypatch, model)
    log: list[str] = []
    _run(tmp_path, env, _seat(10), log=log)
    assert any(m.startswith("[preflight] priced at $0.0100 a page (the firm's usd_per_page") for m in log)
