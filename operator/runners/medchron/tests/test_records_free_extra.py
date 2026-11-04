"""Every matter PDF whose page index is already complete is a free candidate
for EVERY record (records/free.py): the seat's 2026-11-03 measurement, where
the answer to a record was a whole 35-page file with a complete index whose
one-word name never scored into the candidates. Names are invented."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from medchron import proof_mode
from medchron.records import free, search
from medchron.records.run import RecordsRun
from records_testkit import MATTER, FakeFinderModel, FakeParseClient, FakeSmokeball, job_dir, pdf, setup_env
from test_records_end_states import DISCHARGE, _rec, by_sections

PANOREX = "the Westfield Dental panorex"
OTHER_MATTER = "99999999-8888-7777-6666-555555555555"


@pytest.fixture(autouse=True)
def _no_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(proof_mode.PROOF_ENV, raising=False)


def _preindex(tmp: Path, data: bytes, matter: str) -> None:
    """A complete page index for ``data``, as an earlier job left it on the seat."""
    from smokeball_connector import page_finder as pf

    src = tmp / f"pre-{len(data)}.pdf"
    src.write_bytes(data)
    work = tmp / f"pre-work-{len(data)}"
    work.mkdir()
    pf.build_index(src, work, window=None, matter_id=matter)


def _run(tmp: Path, model: FakeFinderModel, env: dict[str, Path], sb: FakeSmokeball, rec: dict[str, Any]) -> Any:
    model.bodies.clear()
    r = RecordsRun(
        job_dir(tmp, request_text=f"Please send {rec['asked']}."),
        inputs_dir=str(env["inputs"]),
        pricing=str(env["pricing"]),
        state_dir=env["state"],
        client=sb,
        model_client=FakeParseClient({"items": [rec]}),
        log=lambda _m: None,
    )
    return r, json.loads(r.run().to_json())


def _westfield(asked: str) -> dict[str, Any]:
    return _rec(asked, provider="Westfield Dental")


def test_a_one_word_file_with_a_complete_index_answers_a_record_it_does_not_score_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = FakeFinderModel(match_fn=by_sections)
    env = setup_env(tmp_path, monkeypatch, model)
    data = pdf(12)
    _preindex(tmp_path, data, MATTER)
    sb = FakeSmokeball()
    scan = sb.add(1, "Scan.pdf", data)
    r, v = _run(tmp_path, model, env, sb, _westfield(DISCHARGE))
    assert json.loads((r.data / search.SCORES).read_text())["1"] == {scan: 0}  # it scores nothing for the record
    assert model.index_calls() == 0 and model.match_calls() == 1
    assert v["verdict"] == "delivered" and [c["file_id"] for c in v["cuts"]] == [scan]
    assert not search.item_holds(r.data)


def test_a_complete_index_for_another_matter_is_not_tried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    model = FakeFinderModel(match_fn=by_sections)
    env = setup_env(tmp_path, monkeypatch, model)
    data = pdf(12)
    _preindex(tmp_path, data, OTHER_MATTER)
    sb = FakeSmokeball()
    sb.add(1, "Scan.pdf", data)
    r, v = _run(tmp_path, model, env, sb, _westfield(DISCHARGE))
    assert json.loads((r.data / "candidates.json").read_text())["1"] == []
    assert sb.downloads == [] and model.bodies == []  # not downloaded, not matched
    assert v["reason"] == search.NO_FILE


def test_at_most_six_free_extras_a_record_most_pages_first_and_a_miss_is_not_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = FakeFinderModel(match_fn=by_sections)
    env = setup_env(tmp_path, monkeypatch, model)
    sb = FakeSmokeball()
    ids = {}
    for i, pages in enumerate(range(11, 19), start=1):
        data = pdf(pages)
        _preindex(tmp_path, data, MATTER)
        ids[pages] = sb.add(i, f"Scan{i}.pdf", data)
    r, v = _run(tmp_path, model, env, sb, _westfield(PANOREX))
    order = json.loads((r.data / search.ORDER).read_text())["1"]
    assert order == [ids[p] for p in range(18, 12, -1)] and free.FREE_EXTRA == 6
    assert model.index_calls() == 0 and model.match_calls() == 6
    # Six misses on files never named for the record: nothing saved, nothing
    # named in the reply, and the record is held as no file named for it.
    assert not (r.data / "locate").exists() and v["reason"] == search.NO_FILE
    reply = json.loads((r.data / "facts.json").read_text())["reply_text"]
    assert "Scan" not in reply


def test_order_is_free_scored_then_free_extra_then_partly_indexed_then_priced() -> None:
    files = {
        "scored": {"sha256": "1", "page_count": 3},
        "scrap": {"sha256": "2", "page_count": 2},
        "whole": {"sha256": "3", "page_count": 35},
        "part": {"sha256": "4", "page_count": 2313},
        "none": {"sha256": "5", "page_count": 40},
    }
    read = {"1": 3, "2": 2, "3": 35, "4": 300, "5": 0}

    import medchron.records.search as s

    mp = pytest.MonkeyPatch()
    mp.setattr(s, "unread", lambda meta: int(meta["page_count"]) - read[meta["sha256"]])
    mp.setattr(s, "read_none", lambda meta: read[meta["sha256"]] == 0)
    try:
        got = s.order(list(files), files, {"scored": 2, "scrap": 0, "whole": 0, "part": 4, "none": 4})
        assert got == ["scored", "whole", "scrap", "part", "none"]
    finally:
        mp.undo()
