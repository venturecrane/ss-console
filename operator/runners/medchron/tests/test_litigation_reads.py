"""The read tools and how answers become state, and the two holds a run can
reach after the reads: an unresolved settlement hit, an unexplained change."""

from __future__ import annotations

import datetime as dt
import json
from email.message import EmailMessage
from pathlib import Path

import pytest

from litigation_testkit import M1, LitSeat, ScriptedLit, good_result, make_inputs, make_job, matter_docs
from medchron.litigation import read, vocab
from medchron.litigation.run import LitigationRun
from medchron.litigation.tools import MatterContext, ReadIncomplete, run_loop
from medchron_testkit import make_pdf

TODAY = dt.date(2026, 10, 7)


@pytest.fixture(autouse=True)
def _pricing(tmp_path: Path, pricing_path: Path) -> None:
    (tmp_path / "pricing.json").write_text(pricing_path.read_text())


def _ctx(tmp_path: Path, seat: LitSeat) -> MatterContext:
    return MatterContext(M1, seat.list_files(M1), tmp_path, 40, seat=seat, log=lambda m: None)


def test_fetch_doc_opens_a_paper_on_request_and_pages_long_text(tmp_path):
    seat = LitSeat({M1: matter_docs()})
    ctx = _ctx(tmp_path, seat)
    n = ctx.by_id["f-a1"]
    out = ctx.fetch_doc({"doc": n})
    assert "Answer Delta.pdf" in out and "ANSWER OF DEFENDANT" in out and "(more: call fetch_doc with offset 40)" in out
    assert seat.mints  # it was fetched from the seat just now
    assert "Filed April 30, 2026" in ctx.fetch_doc({"doc": n, "offset": 40})
    assert "ANSWER OF DEFENDANT" in ctx.search_text({"pattern": "answer of defendant"})
    assert ctx.fetch_doc({"doc": 999}) == "unknown doc number"


def test_a_missing_file_reads_as_missing_never_as_absent(tmp_path):
    seat = LitSeat({M1: matter_docs()})
    seat.gone.add("f-p1")
    ctx = _ctx(tmp_path, seat)
    ctx.chunk_chars = 500
    assert "content is missing" in ctx.fetch_doc({"doc": ctx.by_id["f-p1"]})
    assert ctx.integrity == {"f-p1": "missing_in_smokeball"}


def test_view_page_returns_the_page_image(tmp_path):
    ctx = _ctx(tmp_path, LitSeat({M1: matter_docs()}))
    got = ctx.view_page({"doc": ctx.by_id["f-p1"], "page": 1})
    assert got[1]["type"] == "image" and got[1]["source"]["media_type"] == "image/png"


def test_the_loop_stops_at_its_cap(tmp_path):
    from medchron.ledger import Ledger
    from medchron.llm import Doorway

    ctx = _ctx(tmp_path, LitSeat({M1: matter_docs()}))
    client = ScriptedLit(never_finish=True)
    d = Doorway(ledger=Ledger(tmp_path / "l.jsonl"), client=client, log=lambda m: None)
    with pytest.raises(ReadIncomplete):
        run_loop(
            d,
            "litigation_read",
            model="m",
            system="s",
            user="u",
            ctx=ctx,
            final_tool=read.RECORD_RESULT,
            max_iterations=3,
        )
    assert len(client.calls) == 3
    assert all(not c.get("tools") or c["tools"][-1]["name"] == "record_result" for c in client.calls)


def test_verdicts_overturn_add_and_mark_unclear(tmp_path):
    ctx = _ctx(tmp_path, LitSeat({M1: matter_docs()}))
    n = {k: ctx.by_id[k] for k in ("f-c1", "f-p1", "f-a1")}
    res = read.to_state(ctx, good_result(n), list(vocab.GROUPS))
    log = read.apply_verdicts(
        ctx,
        res,
        [
            {
                "path": "defendants[Delta Example].served",
                "verdict": "overturned",
                "corrected": {"date": "2026-04-09", "method": "substituted"},
                "doc": n["f-p1"],
            },
            {
                "path": "defendants[+]",
                "verdict": "overturned",
                "corrected": {"name": "Zeta Example", "status": vocab.NOT_SERVED},
                "doc": n["f-c1"],
            },
            {"path": "case_status", "verdict": "unclear", "why": "a dismissal is referenced but not in the file"},
            {"path": "complaint_filed", "verdict": "confirmed"},
        ],
        "verify",
    )
    d = res["defendants"]
    assert d[0]["served"]["date"] == "2026-04-09" and d[0]["served"]["source"]["name"] == "POS Delta.pdf"
    assert d[1]["name"] == "Zeta Example" and res["case_status"]["value"] == vocab.UNCLEAR
    assert [x["path"] for x in log] == ["defendants[Delta Example].served", "defendants[Zeta Example]", "case_status"]


def _run(tmp_path, client, seat, job="job", job_id="01KTJ0BX0000000000000000AA"):
    return LitigationRun(
        make_job(tmp_path / job, job_id=job_id),
        inputs_dir=str(make_inputs(tmp_path / "inputs")),
        pricing=str(tmp_path / "pricing.json"),
        state_dir=tmp_path / "state",
        seat_factory=lambda: seat,
        client=client,
        log=lambda m: None,
        today=lambda: TODAY,
        readback_pause=0.0,
    )


def _settle_mail() -> bytes:
    m = EmailMessage()
    m["From"], m["Subject"] = "adjuster@carrier.example", "Notice of settlement"
    m.set_content("Confirming the case has settled; release to follow.")
    return m.as_bytes()


def test_an_unresolved_settlement_hit_holds_and_a_resolved_one_delivers(tmp_path):
    docs = matter_docs() + [("f-s1", "Notice of settlement.eml", _settle_mail(), "2026-09-20T09:00:00")]
    v = _run(tmp_path, ScriptedLit(), LitSeat({M1: docs})).run()
    assert v.verdict == "held" and "settlement-scan hit not resolved" in v.reason
    v2 = _run(tmp_path, ScriptedLit(resolve_hits=True), LitSeat({M1: docs}), job="j2").run()
    assert v2.verdict == "delivered", v2.reason


def test_a_changed_value_with_no_new_document_holds_on_parity(tmp_path):
    seat = LitSeat({M1: matter_docs()})
    assert _run(tmp_path, ScriptedLit(), seat).run().verdict == "delivered"
    seat.per[M1].append(
        {
            "id": "f-p2",
            "name": "POS Zeta",
            "ext": ".pdf",
            "size": 10,
            "created": "2026-09-30T00:00:00",
            "modified": "2026-09-30T00:00:00",
            "folderId": None,
            "deleted": False,
        }
    )
    seat.blobs["f-p2"] = make_pdf(["PROOF OF SERVICE on a different party, 09/29/2026"])
    seat.per[M1][-1]["size"] = len(seat.blobs["f-p2"])

    def drift(n):
        res = good_result(n)
        res["complaint_filed"]["date"] = "2026-03-01"  # same complaint, a different reading
        return res

    v = _run(tmp_path, ScriptedLit(result_fn=drift), seat, job="j2", job_id="01KTJ0BX0000000000000000BB").run()
    assert v.verdict == "held" and v.reason.startswith("parity_hold: ") and "complaint_filed" in v.reason
    assert v.matters_reread == 1
    prior = json.loads((tmp_path / "state" / "matters" / f"{M1}.json").read_text())
    assert prior["complaint_filed"]["date"] == "2026-03-02"  # a held run leaves the last good state alone
