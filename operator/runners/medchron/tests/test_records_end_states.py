"""One test per end state of a (record, file) pair (records/search.py's table,
A to I), the call and spend bounds, and the seat's real shape from the second
A&P dry run (2026-11-03: a 47-page file, 23 pages declined, the record found
on the read pages; the loop retried the declined pages for 149 requests).
Names are invented."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from medchron import proof_mode
from medchron.ledger import read_rows
from medchron.records import assemble, search
from medchron.records.run import RecordsRun
from records_testkit import FakeFinderModel, FakeParseClient, FakeSmokeball, job_dir, pdf, setup_env

VISIT = "the 3/3/2021 Northside Clinic visit"
DISCHARGE = "the discharge summary"
XRAY = "the Northside Clinic x-ray"


def _rec(asked: str, provider: str = "Northside Clinic", dates: list[str] | None = None) -> dict[str, Any]:
    return {
        "asked": asked,
        "hints": {"dates": dates or [], "provider": provider, "pages": ""},
        "for_another_person": False,
    }


def _sid(secs: str, word: str) -> str | None:
    lines = [line for line in secs.splitlines() if " | " in line]  # the section lines, not the "SECTIONS:" header
    return next((line.split(" | ")[0] for line in lines if word in line.casefold()), None)


def by_sections(what: str, secs: str) -> list[dict[str, Any]]:
    """A match that answers from the file's own sections: found only where
    its section is in this file; the closest is the file's first section."""
    word = {VISIT: "progress note", DISCHARGE: "discharge"}.get(what, "x-ray")
    sid = _sid(secs, word)
    if sid:
        return [{"asked": what, "found": True, "sections": [sid], "reason": sid}]
    return [{"asked": what, "found": False, "sections": [], "reason": "not here", "closest": [_sid(secs, "s") or "S1"]}]


@pytest.fixture(autouse=True)
def _no_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(proof_mode.PROOF_ENV, raising=False)


class Calls:
    """Counts the lane's page-finder calls per file sha: builds and matches."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, force_retry: bool = False) -> None:
        from smokeball_connector import page_finder as pf

        self.builds: dict[str, int] = {}
        self.matches: dict[str, int] = {}
        real_build, real_match = pf.build_index, pf.locate_index

        def build(source: Path, *a: Any, **k: Any) -> Any:
            if force_retry:
                k["retry_refused"] = True  # the old loop's semantics: declined pages retried
            out = real_build(source, *a, **k)
            sha = out["index"]["sha256"]
            self.builds[sha] = self.builds.get(sha, 0) + 1
            return out

        def match(sha: str, *a: Any, **k: Any) -> Any:
            self.matches[sha] = self.matches.get(sha, 0) + 1
            return real_match(sha, *a, **k)

        monkeypatch.setattr(pf, "build_index", build)
        monkeypatch.setattr(pf, "locate_index", match)


def _job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model: FakeFinderModel,
    sb: FakeSmokeball,
    records: list[dict[str, Any]],
    request: str,
    **job: Any,
) -> tuple[RecordsRun, dict[str, Any]]:
    env = setup_env(tmp_path, monkeypatch, model)
    r = RecordsRun(
        job_dir(tmp_path, request_text=request, **job),
        inputs_dir=str(env["inputs"]),
        pricing=str(env["pricing"]),
        state_dir=env["state"],
        client=sb,
        model_client=FakeParseClient({"items": records}),
        log=lambda _m: None,
    )
    return r, json.loads(r.run().to_json())


def _facts(r: RecordsRun) -> str:
    return json.loads((r.data / "facts.json").read_text())["reply_text"]


def _sha(r: RecordsRun, file_id: str) -> str:
    return json.loads((r.data / "files.json").read_text())[file_id]["sha256"]


def _page_asks(model: FakeFinderModel) -> list[int]:
    return [
        int(b["text"].split()[1].rstrip(":"))
        for body in model.bodies
        if "pages" in body["output_config"]["format"]["schema"]["properties"]
        for b in body["messages"][0]["content"]
        if b["type"] == "text" and b["text"].startswith("Page ")
    ]


def _page_spend(r: RecordsRun) -> tuple[float, int]:
    rows = [row for row in read_rows(r.ledger.path) if row.get("pages")]
    return sum(r.pricing.price_row(row) for row in rows), sum(int(row["pages"]) for row in rows)


# ---- A to I ---------------------------------------------------------------------------


def test_A_every_page_read_and_found_is_cut_and_attached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    model = FakeFinderModel(match_fn=by_sections)
    sb = FakeSmokeball()
    sb.add(1, "Northside Clinic records.pdf", pdf(10))
    r, v = _job(tmp_path, monkeypatch, model, sb, [_rec(VISIT, dates=["3/3/2021"])], f"Please send {VISIT}.")
    assert v["verdict"] == "delivered" and v["items"] == [{"n": 1, "outcome": "attached", "files": 1}]
    assert [c["pages"] for c in v["cuts"]] == ["1-4"]


def test_B_read_and_established_absent_says_not_found_and_tries_the_next_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = FakeFinderModel(match_fn=by_sections)
    sb = FakeSmokeball()
    first = sb.add(1, "Northside Clinic discharge summary index.pdf", pdf(4))  # scores higher, holds only the visit
    second = sb.add(2, "Northside Clinic chart.pdf", pdf(10))
    r, v = _job(tmp_path, monkeypatch, model, sb, [_rec(DISCHARGE)], f"Please send {DISCHARGE}.")
    assert json.loads((r.data / search.ORDER).read_text())["1"] == [first, second]
    assert v["verdict"] == "delivered" and v["items"] == [{"n": 1, "outcome": "attached", "files": 1}]
    assert [(c["file_id"], c["pages"]) for c in v["cuts"]] == [(second, "5-10")]
    assert "not found in Northside Clinic discharge summary index.pdf (all 4 pages were read)" in _facts(r)


def test_C_found_on_the_read_pages_is_cut_says_the_gap_and_never_rereads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = FakeFinderModel(match_fn=by_sections, refuse=frozenset({3, 4}))
    sb = FakeSmokeball()
    sb.add(1, "Northside Clinic records.pdf", pdf(10))
    r, v = _job(tmp_path, monkeypatch, model, sb, [_rec(VISIT)], f"Please send {VISIT}.")
    assert v["verdict"] == "delivered" and v["items"][0]["outcome"] == "attached"
    assert "- 2 pages of Northside Clinic records.pdf could not be read" in _facts(r)
    assert max(_page_asks(model).count(p) for p in (3, 4)) <= 2  # its batch, then alone, never again


def test_D_not_found_beside_declined_pages_is_not_established_with_the_closest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = FakeFinderModel(match_fn=by_sections, refuse=frozenset(range(5, 11)))
    sb = FakeSmokeball()
    sb.add(1, "Northside Clinic records.pdf", pdf(10))
    r, v = _job(tmp_path, monkeypatch, model, sb, [_rec(DISCHARGE)], f"Please send {DISCHARGE}.")
    assert v["items"] == [{"n": 1, "outcome": "not_established", "files": 0}]
    text = _facts(r)
    assert "Not established, 6 pages of Northside Clinic records.pdf could not be read:" in text
    assert "Closest: pages 1-4" in text
    assert max(_page_asks(model).count(p) for p in range(5, 11)) <= 2


def test_E_a_page_never_reached_stays_unread_found_records_go_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from smokeball_connector import page_finder as pf

    model = FakeFinderModel(match_fn=by_sections, fail=frozenset({10}))
    sb = FakeSmokeball()
    f = sb.add(1, "Northside Clinic records.pdf", pdf(10))
    r, v = _job(tmp_path, monkeypatch, model, sb, [_rec(VISIT)], f"Please send {VISIT}.")
    assert v["verdict"] == "delivered", v
    assert "- 1 page of Northside Clinic records.pdf was not read" in _facts(r)
    index = pf.load_index(_sha(r, f))
    assert "10" not in index["unreadable"] and "10" not in index["pages"]  # the shared index is not told


def test_E_not_found_with_pages_never_reached_holds_that_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = FakeFinderModel(match_fn=by_sections, fail=frozenset({10}))
    sb = FakeSmokeball()
    f = sb.add(1, "Northside Clinic records.pdf", pdf(10))
    r, v = _job(tmp_path, monkeypatch, model, sb, [_rec(XRAY)], f"Please send {XRAY}.")
    assert v["verdict"] == "held" and v["reason"] == f"match_unsettled: {f[:8]} 1 pages not read", v
    assert "not established yet; 1 page of the file it is in is not read yet" in _facts(r)


def test_F_a_first_read_over_the_cap_holds_that_record_before_any_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from smokeball_connector import page_finder as pf

    model = FakeFinderModel(match_fn=by_sections)
    sb = FakeSmokeball()
    small = sb.add(1, "Northside Clinic records.pdf", pdf(10))
    big = sb.add(2, "Eastgate Imaging set.pdf", pdf(2313))
    recs = [_rec(VISIT), _rec("the Eastgate Imaging MRI", provider="Eastgate Imaging")]
    r, v = _job(tmp_path, monkeypatch, model, sb, recs, f"Please send {VISIT} and the Eastgate Imaging MRI.")
    assert v["reason"] == f"per_job_cap_usd: {big[:8]} 2313 pages"
    assert [i["outcome"] for i in v["items"]] == ["attached", "held"] and [c["file_id"] for c in v["cuts"]] == [small]
    assert not (pf.index_dir() / f"{_sha(r, big)}.json").exists()  # never read, nothing written


def test_G_the_cap_tripping_mid_file_keeps_its_pages_and_the_other_record_goes_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from smokeball_connector import page_finder as pf

    model = FakeFinderModel(match_fn=by_sections)
    env = setup_env(tmp_path, monkeypatch, model)
    (env["inputs"] / "records-firm.yaml").write_text(
        "per_job_cap_usd: 5.0\nmonthly_budget_usd: 100.0\nusd_per_page: 0.0001\n", encoding="utf-8"
    )
    small = pdf(10)
    pre = tmp_path / "pre.pdf"
    pre.write_bytes(small)
    work = tmp_path / "pre-work"
    work.mkdir()
    pf.locate(pre, DISCHARGE, work)  # an earlier request: index complete, this match cached
    sb = FakeSmokeball()
    big = sb.add(1, "Eastgate Imaging set.pdf", pdf(40))
    sb.add(2, "Northside Clinic discharge records.pdf", small)
    recs = [_rec("the Eastgate Imaging MRI", provider="Eastgate Imaging"), _rec(DISCHARGE)]
    r = RecordsRun(
        job_dir(tmp_path, request_text=f"Please send the Eastgate Imaging MRI and {DISCHARGE}.", per_job_cap_usd=0.015),
        inputs_dir=str(env["inputs"]),
        pricing=str(env["pricing"]),
        state_dir=env["state"],
        client=sb,
        model_client=FakeParseClient({"items": recs}),
        log=lambda _m: None,
    )
    v = json.loads(r.run().to_json())
    assert v["verdict"] == "held" and v["reason"].startswith(f"per_job_cap_usd: {big[:8]}")
    assert [i["outcome"] for i in v["items"]] == ["held", "attached"] and [c["n"] for c in v["cuts"]] == [2]
    assert len(pf.load_index(_sha(r, big))["pages"]) == 8  # the batch read before the cap stays


def test_H_a_match_that_fails_tries_the_next_file_and_holds_only_that_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def flaky(what: str, secs: str) -> Any:
        return None if what == XRAY else by_sections(what, secs)

    model = FakeFinderModel(match_fn=flaky)
    sb = FakeSmokeball()
    f = sb.add(1, "Northside Clinic records.pdf", pdf(10))
    r, v = _job(tmp_path, monkeypatch, model, sb, [_rec(VISIT), _rec(XRAY)], f"Please send {VISIT} and {XRAY}.")
    assert v["verdict"] == "held" and v["reason"] == f"match_unsettled: {f[:8]} the match did not answer"
    assert [i["outcome"] for i in v["items"]] == ["attached", "held"]
    assert not search.locate_path(r, 2, f).exists()  # a failed match is not saved: a re-run asks again


def test_I_one_file_for_two_records_is_read_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    model = FakeFinderModel(match_fn=by_sections)
    sb = FakeSmokeball()
    sb.add(1, "Northside Clinic records.pdf", pdf(10))
    calls = Calls(monkeypatch)
    r, v = _job(
        tmp_path, monkeypatch, model, sb, [_rec(VISIT), _rec(DISCHARGE)], f"Please send {VISIT} and {DISCHARGE}."
    )
    assert v["verdict"] == "delivered" and model.index_calls() == 2  # the 10 pages, once
    assert model.match_calls() == 2 and list(calls.matches.values()) == [2]


# ---- bounds and the seat's shape ------------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    [{"refuse": frozenset({3, 4})}, {"refuse": frozenset(range(5, 11))}, {"fail": frozenset({10})}],
    ids=["C", "D", "E"],
)
def test_calls_and_spend_are_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: dict[str, Any]) -> None:
    model = FakeFinderModel(match_fn=by_sections, **kind)
    sb = FakeSmokeball()
    f = sb.add(1, "Northside Clinic records.pdf", pdf(10))
    calls = Calls(monkeypatch)
    r, _v = _job(tmp_path, monkeypatch, model, sb, [_rec(DISCHARGE)], f"Please send {DISCHARGE}.")
    sha = _sha(r, f)
    assert calls.builds[sha] + calls.matches[sha] <= -(-10 // 100) + 2
    dollars, attempts = _page_spend(r)
    assert dollars <= attempts * r.firm.usd_per_page * 1.5


def test_the_bound_holds_even_if_declined_pages_were_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The old loop's semantics (every pass retries the declined pages): the
    lane's own bound still ends it at ceil(pages / 100) + 2 calls."""
    model = FakeFinderModel(match_fn=by_sections, refuse=frozenset(range(15, 38)))
    sb = FakeSmokeball()
    f = sb.add(1, "Northside Clinic records.pdf", pdf(47))
    calls = Calls(monkeypatch, force_retry=True)
    r, _v = _job(tmp_path, monkeypatch, model, sb, [_rec(VISIT)], f"Please send {VISIT}.")
    sha = _sha(r, f)
    assert calls.builds[sha] <= -(-47 // 100) + 1 and calls.matches[sha] == 1


def test_the_seats_47_page_shape_reads_no_page_and_asks_one_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """23 read, 23 declined and settled (the index as the seat holds it), the
    record on the read pages: zero page calls, one match, then the cut."""
    model = FakeFinderModel(match_fn=by_sections, refuse=frozenset(range(15, 38)))
    sb = FakeSmokeball()
    f = sb.add(1, "Northside Clinic records.pdf", pdf(47))
    (tmp_path / "earlier").mkdir()
    _job(tmp_path / "earlier", monkeypatch, model, sb, [_rec(DISCHARGE)], f"Please send {DISCHARGE}.")
    import shutil

    shutil.copytree(tmp_path / "earlier" / "index", tmp_path / "index")  # the seat's index, as it stands
    model.bodies.clear()
    r, v = _job(tmp_path, monkeypatch, model, sb, [_rec(VISIT)], f"Please send {VISIT}.")
    assert model.index_calls() == 0 and model.match_calls() == 1
    assert v["verdict"] == "delivered" and [c["file_id"] for c in v["cuts"]] == [f]
    assert "23 pages of Northside Clinic records.pdf could not be read" in _facts(r)


def test_a_record_no_file_is_named_for_holds_and_prices_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = FakeFinderModel(match_fn=by_sections)
    sb = FakeSmokeball()
    sb.add(1, "Northside Clinic records.pdf", pdf(10))
    recs = [_rec(VISIT), _rec("the Westfield Dental panorex", provider="Westfield Dental")]
    r, v = _job(tmp_path, monkeypatch, model, sb, recs, f"Please send {VISIT} and the Westfield Dental panorex.")
    assert v["reason"] == search.NO_FILE and [i["outcome"] for i in v["items"]] == ["attached", "held"]
    assert json.loads((r.data / "candidates.json").read_text())["2"] == []
    assert "- the Westfield Dental panorex: not established yet; no file on the matter is named for it" in _facts(r)
    assert _facts(r).endswith(assemble.HELD_LINE)


def test_measure_lists_each_records_ranked_files_and_runs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = FakeFinderModel(match_fn=by_sections)
    env = setup_env(tmp_path, monkeypatch, model)
    sb = FakeSmokeball()
    f = sb.add(1, "Northside Clinic records.pdf", pdf(10))
    jd = job_dir(tmp_path, request_text=f"Please send {VISIT}.")
    (jd / "data" / "records.json").write_text(json.dumps([{**_rec(VISIT), "n": 1}]))
    r = RecordsRun(
        jd,
        inputs_dir=str(env["inputs"]),
        pricing=str(env["pricing"]),
        state_dir=env["state"],
        client=sb,
        model_client=FakeParseClient(),
        log=lambda _m: None,
    )
    out = r.measure()
    assert f"  {f[:8]} score 4 pages 10 read 0 unreadable 0 unread 10" in out
    assert model.bodies == [] and not (jd / "data" / "state.json").exists() and "Northside" not in out
