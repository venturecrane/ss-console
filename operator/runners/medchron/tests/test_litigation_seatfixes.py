"""The 2026-10-08 seat rehearsal's defects, each pinned: the fetch is capped
and prioritized, a struck seed group is read, long scans are transcribed at
the head and tail only, the read starts from the papers, a failure path
still writes the report, a redo clears the per-matter reads, and a replay
needs no seat."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from litigation_testkit import M1, LitSeat, ScriptedLit, first_text, make_inputs, matter_docs
from medchron.litigation import extract, manifest, rehearse, vocab
from medchron.litigation.firm import load
from medchron_testkit import make_pdf

TODAY = dt.date(2026, 10, 7)


def _f(fid: str, name: str, ext: str, day: str) -> dict:
    return {"id": fid, "name": name, "ext": ext, "modified": f"{day}T09:00:00", "created": f"{day}T09:00:00"}


def test_the_fetch_is_capped_and_prioritized_by_class(tmp_path):
    firm = load(
        make_inputs(tmp_path / "in", fetch_caps={"court": 3, "discovery": 2, "server": 1, "email": 1, "total": 9})
    )
    files = [_f(f"c{i}", f"POS {i}", ".pdf", f"2026-0{1 + i % 9}-01") for i in range(8)]
    files.append(_f("comp", "Complaint", ".pdf", "2024-01-01"))
    files += [_f(f"d{i}", f"Interrogatories set {i}", ".pdf", "2026-05-01") for i in range(5)]
    files += [_f(f"s{i}", f"Example Legal Process invoice {i}", ".pdf", "2026-05-02") for i in range(3)]
    files += [_f(f"e{i}", f"Re: lunch {i}", ".msg", f"2026-09-{10 + i}") for i in range(3)]
    files.append(_f("old", "Example Legal Process service done", ".msg", "2023-01-01"))  # an old server email: out
    sel = manifest.select(files, firm, TODAY)
    classes = [c for _f, c in sel]
    assert sel[0] == ("comp", "court")  # the earliest complaint is never capped away
    assert classes.count("court") == 3 and classes.count("discovery") == 2 and classes.count("server") == 1
    assert classes.count("newest_email") == 2  # email_recent_n in the testkit
    assert "old" not in [f for f, _c in sel] and len(sel) <= 9


def test_an_emptied_seed_marker_reads_every_group(tmp_path):
    firm = load(make_inputs(tmp_path / "in"))
    files = [_f("a", "Complaint", ".pdf", "2026-03-01")]
    prior = {"fields_read": [], "defendants": [{"name": "Delta"}], "case_status": {"value": vocab.ACTIVE}}
    p = manifest.plan_matter(files, prior, {"a": ""}, firm, TODAY)
    assert p["read_groups"] == list(vocab.GROUPS)  # defendants included: the marker was struck, not absent
    legacy = {k: v for k, v in prior.items() if k != "fields_read"}
    legacy["discovery_propounded"], legacy["discovery_served_on_client"] = [], []
    assert "defendants" not in manifest.plan_matter(files, legacy, {"a": ""}, firm, TODAY)["read_groups"]


def test_a_long_scan_is_transcribed_at_its_head_and_tail_only(tmp_path):
    seen = []
    pdf = make_pdf([""] * 9)
    p = tmp_path / "scan.pdf"
    p.write_bytes(pdf)
    rec = extract.extract_file(p, ".pdf", tmp_path / "t.txt", lambda png: seen.append(1) or "PAGE TEXT here")
    assert rec["ok"] and rec["scanned_pages"] == 5 and len(seen) == 5
    text = (tmp_path / "t.txt").read_text()
    assert text.count("PAGE TEXT") == 5 and "[scanned page 4: not transcribed; call view_page" in text
    assert extract.ocr_pages(["", "x" * 30, ""]) == [0, 2]  # a short document: every scanned page


def test_extract_runs_documents_concurrently_and_records_each(tmp_path):
    rows = []
    for i in range(6):
        p = tmp_path / f"d{i}.pdf"
        p.write_bytes(make_pdf([f"ANSWER number {i} filed on the date shown"]))
        rows.append({"id": f"d{i}", "name": f"Answer {i}", "ok": True, "path": str(p), "ext": ".pdf"})
    out = extract.extract_matter(tmp_path, rows, None, concurrency=4)
    assert [r["file_id"] for r in out] == [f"d{i}" for i in range(6)] and all(r["ok"] for r in out)


def test_the_read_is_handed_the_papers_up_front(tmp_path, pricing_path):
    from medchron.litigation.read import file_list
    from medchron.litigation.tools import MatterContext

    seat = LitSeat({M1: matter_docs()})
    ctx = MatterContext(M1, seat.list_files(M1), tmp_path, 500, seat=seat, log=lambda m: None)
    cands = ["f-a1", "f-p1", "f-c1"]
    out = file_list(ctx, cands, [], context_chars=10_000, doc_chars=60)
    assert "DOCUMENT TEXTS (3 of 3" in out and "ANSWER OF DEFENDANT" in out and "PROOF OF SERVICE" in out
    assert out.index("Answer Delta.pdf") < out.index("POS Delta.pdf")  # priority order, not doc order
    tight = file_list(ctx, cands, [], context_chars=60, doc_chars=60)
    assert "DOCUMENT TEXTS (1 of 3" in tight


@pytest.fixture
def replay(tmp_path: Path, pricing_path: Path, monkeypatch):
    """A finished seat job (inventory, fetch, extract done), its raw files
    gone, then replayed offline from read1."""
    from test_litigation_rehearse import _inputs, _rehearsal, _seat

    monkeypatch.setenv(manifest.DATA_ENV, str(tmp_path / "volume"))
    (tmp_path / "pricing.json").write_text(pricing_path.read_text())
    r = _rehearsal(tmp_path, ScriptedLit(), _seat())
    r.run()
    for raw in (r.data / "m").glob("*/raw/*"):
        raw.unlink()
    client = ScriptedLit()
    jd = r.job.job_dir
    r2 = rehearse.RehearsalRun(
        jd,
        report_path=tmp_path / "replay.json",
        inputs_dir=str(_inputs(tmp_path)),
        pricing=str(tmp_path / "pricing.json"),
        state_dir=tmp_path / "rstate",
        client=client,
        log=lambda m: None,
        offline=True,
    )
    return r2, client


def test_an_offline_replay_reads_from_cached_text_and_needs_no_seat(replay):
    r, client = replay
    assert (r.data / "m" / M1 / "read1.json").is_file()
    r.reopen(["read1"])
    assert not (r.data / "m" / M1 / "read1.json").exists() and not (r.data / "m" / M1 / "read3.json").exists()
    assert r._state()["gates"]["status"] == "reopened" and r._state()["extract"]["status"] == "done"
    v = r.run()
    assert v.verdict == "held" and v.reason.startswith("rehearsal: ")
    assert client.calls and all("ANSWER OF DEFENDANT" in first_text(c) for c in client.calls if c.get("system"))
    assert isinstance(r.seat, rehearse.OfflineSeat)
    rep = json.loads((r.report_path).read_text())
    assert rep["matters"]["100001"]["read_calls"]["litigation_read"] >= 1


def test_a_failed_rehearsal_still_writes_its_report(replay, monkeypatch):
    r, _ = replay
    r.reopen(["read1"])

    def boom(_self):
        raise RuntimeError("the read stage broke")

    monkeypatch.setattr(type(r), "_read1", boom)
    v = r.run()
    assert v.verdict == "failed" and v.stage == "read1"
    rep = json.loads(r.report_path.read_text())
    assert rep["rehearsal"]["verdict"]["verdict"] == "failed" and "counts" in rep


def test_view_page_offline_says_the_image_is_not_available(tmp_path):
    from medchron.litigation.tools import MatterContext

    ctx = MatterContext(M1, LitSeat({M1: matter_docs()}).list_files(M1), tmp_path, 500, seat=None, log=lambda m: None)
    assert "not available in this run" in ctx.view_page({"doc": 1, "page": 1})


def test_court_papers_are_interleaved_by_kind_so_an_old_proof_survives_the_cap(tmp_path):
    firm = load(make_inputs(tmp_path / "in", fetch_caps={"court": 4, "total": 20}))
    files = [_f(f"a{i}", f"Answer {i}", ".pdf", f"2026-0{1 + i}-01") for i in range(6)]
    files.append(_f("old-pos", "POS Delta", ".pdf", "2024-10-17"))
    ids = [f for f, c in manifest.select(files, firm, TODAY) if c == "court"]
    assert "old-pos" in ids and len(ids) == 4


def test_parity_matches_a_defendant_across_spellings():
    from medchron.litigation.parity import party_key

    assert party_key("Andrea DeFelice (Deflice), NP") == party_key("Andrea DeFelice, N.P.")
    assert party_key("Syed Haider (Raider/Hrider), MD") == party_key("Syed Haider, M.D.")
    assert party_key("MarketOne Builders") != party_key("Auberge Resorts LLC")


def test_an_uncited_prior_value_may_be_replaced_by_a_cited_one():
    from medchron.litigation import parity

    prior = {"case_status": {"value": vocab.ACTIVE, "source": None}}
    new = {"case_status": {"value": vocab.SETTLED_OWED, "source": {"file_id": "f1"}}}
    assert not parity.unexplained(parity.compare(prior, new, moved_files=set(), overturns=[], today=TODAY))
    cited = {"case_status": {"value": vocab.ACTIVE, "source": {"file_id": "f0"}}}
    assert parity.unexplained(parity.compare(cited, new, moved_files=set(), overturns=[], today=TODAY))


def test_a_kind_keeps_its_oldest_paper_under_a_cap(tmp_path):
    firm = load(make_inputs(tmp_path / "in", fetch_caps={"court": 3, "total": 20}))
    files = [_f(f"p{i}", f"POS {i}", ".pdf", f"2025-{1 + i:02d}-01") for i in range(10)]
    ids = [f for f, _c in manifest.select(files, firm, TODAY)]
    assert ids[:3] == ["p9", "p0", "p8"]  # newest, oldest, next newest


def test_an_answer_written_as_json_text_is_taken():
    from types import SimpleNamespace

    from medchron.litigation.tools import _json_answer

    msg = SimpleNamespace(content=[{"type": "text", "text": 'Here it is: {"verdicts": []} done'}])
    assert _json_answer(msg) == {"verdicts": []}
    assert _json_answer(SimpleNamespace(content=[{"type": "text", "text": "no json"}])) is None
