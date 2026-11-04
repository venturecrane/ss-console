"""Cheapest file first, pre-flight per file, a hold per record (records/search.py).

WHY (2026-11-03, the first A&P dry run): the job priced every candidate's
unread pages up front, nine files and 4,775 pages, and held at pre-flight
having read nothing, though the records sat in files already indexed. Names
here are invented."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jobs import proof_mode
from jobs.records import assemble, search
from jobs.records.run import RecordsRun
from records_testkit import FakeFinderModel, FakeParseClient, FakeSmokeball, job_dir, pdf, setup_env

BIG = 2000
SMALL = pdf(10)


@pytest.fixture
def model() -> FakeFinderModel:
    return FakeFinderModel()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: FakeFinderModel) -> dict[str, Path]:
    monkeypatch.delenv(proof_mode.PROOF_ENV, raising=False)
    return setup_env(tmp_path, monkeypatch, model)


def _run(tmp_path: Path, env: dict[str, Path], sb: Any, parse: Any = None, log: list[str] | None = None, **job: Any):
    r = RecordsRun(
        job_dir(tmp_path, **job),
        inputs_dir=str(env["inputs"]),
        pricing=str(env["pricing"]),
        state_dir=env["state"],
        client=sb,
        model_client=parse or FakeParseClient(),
        log=(log.append if log is not None else lambda _m: None),
    )
    return r, json.loads(r.run().to_json())


def _index_small(tmp_path: Path) -> None:
    """The small file's index, already complete, as a seat holds it after an earlier request."""
    from smokeball_connector import page_finder as pf

    src = tmp_path / "small.pdf"
    src.write_bytes(SMALL)
    work = tmp_path / "pre"
    work.mkdir()
    pf.build_index(src, work, window=None)


def test_a_record_in_a_complete_index_is_found_and_the_big_file_is_never_read(
    tmp_path: Path, env: dict[str, Path], model: FakeFinderModel
) -> None:
    sb = FakeSmokeball()
    big = sb.add(1, "Northside Clinic full chart.pdf", pdf(BIG))  # listed first
    sb.add(2, "Northside Clinic records.pdf", SMALL)
    _index_small(tmp_path)
    model.bodies.clear()
    log: list[str] = []
    r, v = _run(tmp_path, env, sb, log=log)
    assert v["verdict"] == "delivered", v
    assert model.index_calls() == 0  # no page bought: the index was whole
    assert not list((r.data / "locate").rglob(f"{big}.json"))
    assert [i["outcome"] for i in v["items"]] == ["attached", "attached"]
    order = json.loads((r.data / search.ORDER).read_text())
    assert all(ids[0] != big for ids in order.values())
    line = next(m for m in log if m.startswith("[preflight]") and "unread pages" in m)
    assert f"{big[:8]} {BIG}" in line and "Northside" not in line


def test_with_no_index_the_smaller_file_is_read_first_and_the_big_one_never(
    tmp_path: Path, env: dict[str, Path], model: FakeFinderModel
) -> None:
    sb = FakeSmokeball()
    big = sb.add(1, "Northside Clinic full chart.pdf", pdf(BIG))
    sb.add(2, "Northside Clinic records.pdf", SMALL)
    r, v = _run(tmp_path, env, sb)
    assert v["verdict"] == "delivered", v
    assert model.index_calls() == 2  # the 10 pages, 8 per request
    assert not list((r.data / "locate").rglob(f"{big}.json"))


def test_a_record_whose_only_file_would_pass_the_cap_holds_alone(
    tmp_path: Path, env: dict[str, Path], model: FakeFinderModel
) -> None:
    request = "Please send the 3/3/2021 Northside Clinic visit and the Eastgate Imaging MRI report."
    parsed = {
        "items": [
            {
                "asked": "the 3/3/2021 Northside Clinic visit",
                "hints": {"dates": ["3/3/2021"], "provider": "Northside Clinic", "pages": ""},
                "for_another_person": False,
            },
            {
                "asked": "the Eastgate Imaging MRI report",
                "hints": {"dates": [], "provider": "Eastgate Imaging", "pages": ""},
                "for_another_person": False,
            },
        ]
    }
    sb = FakeSmokeball()
    big = sb.add(1, "Eastgate Imaging set.pdf", pdf(BIG))
    sb.add(2, "Northside Clinic records.pdf", SMALL)
    r, v = _run(tmp_path, env, sb, parse=FakeParseClient(parsed), request_text=request)
    assert v["verdict"] == "held" and v["reason"] == f"per_job_cap_usd: {big[:8]} {BIG} pages", v
    assert v["items"] == [{"n": 1, "outcome": "attached", "files": 1}, {"n": 2, "outcome": "held", "files": 0}]
    assert [c["n"] for c in v["cuts"]] == [1]  # the found record still goes with the reply
    assert model.index_calls() == 2  # the small file only
    text = json.loads((r.data / "facts.json").read_text())["reply_text"]
    assert "Attached:" in text and "Not established, still to be read:" in text
    assert "- the Eastgate Imaging MRI report: not established yet; 2,000 pages" in text
    assert text.endswith(assemble.HELD_LINE)
    assert "$" not in text and "per_job_cap_usd" not in text and big[:8] not in text


def test_a_month_already_spent_still_holds_the_whole_job(
    tmp_path: Path, env: dict[str, Path], model: FakeFinderModel
) -> None:
    sb = FakeSmokeball()
    sb.add(2, "Northside Clinic records.pdf", SMALL)
    _r, v = _run(tmp_path, env, sb, month_cents_used=10_000)
    assert v["verdict"] == "held" and v["reason"].startswith("monthly_budget_usd:")
    assert model.bodies == []


def test_order_puts_a_complete_index_before_a_smaller_unread_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = {
        "aaa": {"sha256": "1", "page_count": 900},
        "bbb": {"sha256": "2", "page_count": 5},
        "ccc": {"sha256": "3", "page_count": 40},
    }
    monkeypatch.setattr(search, "unread", lambda meta: 0 if meta["sha256"] == "1" else int(meta["page_count"]))
    assert search.order(["ccc", "bbb", "aaa"], files) == ["aaa", "bbb", "ccc"]
