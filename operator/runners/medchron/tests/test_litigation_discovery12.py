"""The 12-section discovery breakdown (``litigation/discovery12/``), on a fake
seat with a scripted model. Synthetic data only."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from litigation_testkit import JOB, M1, LitSeat, ScriptedLit, first_text, make_inputs, make_job, matter_docs
from medchron_testkit import make_pdf

from medchron import lane_proof
from medchron.litigation import job as job_mod, manifest
from medchron.litigation.discovery12 import book, sections
from medchron.litigation.discovery12.run import ROLE, Discovery12Run, plan_for

FROGS = "Form Interrogatories, Set One, served by mail on March 5, 2026 on defendant Delta."
RESP = "Responses to Form Interrogatories, Set One. Served April 4, 2026."


def disc_docs() -> list[tuple[str, str, bytes, str]]:
    return [
        *matter_docs(),
        ("f-i1", "Form Interrogatories Set One.pdf", make_pdf([FROGS]), "2026-03-05T10:00:00"),
        ("f-i2", "Responses to Form Interrogatories.pdf", make_pdf([RESP]), "2026-04-04T10:00:00"),
    ]


def frogs_result(n: dict[str, int], served: str = "2026-03-05") -> dict[str, Any]:
    entry = {
        "item": "Form Interrogatories, Set One",
        "direction": "propounded by the firm",
        "party": "Delta",
        "served": {"date": served, "doc": n.get("Form Interrogatories Set One.pdf")},
        "due": {"date": None},
        "response": {"value": "yes", "date": "2026-04-04", "doc": n.get("Responses to Form Interrogatories.pdf")},
        "extension": {"date": None, "detail": ""},
        "notes": "",
    }
    return {"sections": {"frogs": [entry]}, "flags": []}


class Scripted12(ScriptedLit):
    """The breakdown's reads: pass 1 records ``fn(doc numbers)``; verify and
    audit record ``verdicts`` / ``audit``."""

    def __init__(self, fn: Any = frogs_result, verdicts: list | None = None, audit: list | None = None) -> None:
        super().__init__()
        self.fn, self.v12, self.a12 = fn, verdicts or [], audit or []

    def _msg(self, params: dict[str, Any]) -> Any:
        system = json.dumps(params.get("system"))
        if "twelve sections" not in system or len(params["messages"]) == 1:
            return super()._msg(params)
        self.calls.append(params)
        usage = SimpleNamespace(
            input_tokens=1000, output_tokens=200, cache_read_input_tokens=0, cache_creation_input_tokens=0
        )
        if "YOUR PASS: determine all twelve" in system:
            import re

            nums = {name.strip(): int(k) for k, name in re.findall(r"\[doc (\d+)\] ([^|]+?) \|", first_text(params))}
            final = {"type": "tool_use", "id": "rec", "name": "record_discovery", "input": self.fn(nums)}
        else:
            vs = self.a12 if "last check before" in system else self.v12
            final = {"type": "tool_use", "id": "rec", "name": "record_verdicts", "input": {"verdicts": vs}}
        return SimpleNamespace(content=[final], stop_reason="tool_use", usage=usage)


@pytest.fixture(autouse=True)
def _pricing(tmp_path: Path, pricing_path: Path) -> None:
    (tmp_path / "pricing.json").write_text(pricing_path.read_text())


def _run(tmp_path: Path, seat: LitSeat, client: Any) -> Discovery12Run:
    import datetime as dt

    return Discovery12Run(
        make_job(tmp_path / "job", report="discovery12"),
        inputs_dir=str(make_inputs(tmp_path / "inputs")),
        pricing=str(tmp_path / "pricing.json"),
        state_dir=tmp_path / "state",
        seat_factory=lambda: seat,
        client=client,
        log=lambda m: None,
        today=lambda: dt.date(2026, 10, 29),
        readback_pause=0.0,
    )


def _discovery_rows(xlsx: bytes) -> list[list[Any]]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(xlsx), read_only=True)
    try:
        return [list(r) for r in wb[book.DISCOVERY].iter_rows(values_only=True)][1:]
    finally:
        wb.close()


def test_a_run_files_twelve_sections_per_case_each_date_citing_its_paper(tmp_path):
    seat = LitSeat({M1: disc_docs()})
    r = _run(tmp_path, seat, Scripted12())
    v = r.run()
    assert v.verdict == "delivered", v.reason
    assert [f["role"] for f in v.files] == [ROLE] and v.files[0]["name"].startswith("Discovery breakdown 2026-10-29")
    rows = _discovery_rows(r._out().read_bytes())
    assert len(rows) == 12  # the FROGs entry, and "None in file" for the eleven empty sections
    frogs = next(x for x in rows if x[3] == sections.LABEL["frogs"])
    assert frogs[7] == "2026-03-05" and "Form Interrogatories Set One.pdf" in frogs[8]
    assert frogs[11] == "yes" and frogs[12] == "2026-04-04"
    assert sum(1 for x in rows if x[4] == sections.NONE_IN_FILE) == 11
    assert v.cents > 0 and manifest.load_prior(tmp_path / "state", M1) is None  # billed; the lane's state untouched


def test_a_date_not_in_its_cited_paper_holds_the_report(tmp_path):
    seat = LitSeat({M1: disc_docs()})
    v = _run(tmp_path, seat, Scripted12(fn=lambda n: frogs_result(n, served="2026-03-09"))).run()
    assert v.verdict == "held" and v.reason.startswith("gate_refused:") and "date not in the cited document" in v.reason
    assert seat.sent == []


def test_a_verify_overturn_replaces_the_section_whole(tmp_path):
    seat = LitSeat({M1: disc_docs()})
    verdicts = [{"section": "frogs", "verdict": "overturned", "corrected": [], "why": "not served"}]
    r = _run(tmp_path, seat, Scripted12(verdicts=verdicts))
    assert r.run().verdict == "delivered"
    rows = _discovery_rows(r._out().read_bytes())
    assert sum(1 for x in rows if x[4] == sections.NONE_IN_FILE) == 12


def test_a_case_with_no_discovery_paper_is_not_read_and_says_so(tmp_path):
    seat = LitSeat({M1: matter_docs()})
    client = Scripted12()
    r = _run(tmp_path, seat, client)
    assert r.run().verdict == "delivered"
    rows = _discovery_rows(r._out().read_bytes())
    assert len(rows) == 1 and rows[0][4] == book.NOT_READ and client.calls == []


def test_the_cap_stops_the_reading(tmp_path):
    seat = LitSeat({M1: disc_docs()})
    import datetime as dt

    r = Discovery12Run(
        make_job(tmp_path / "job", cents=10_000, report="discovery12"),
        inputs_dir=str(make_inputs(tmp_path / "inputs")),
        pricing=str(tmp_path / "pricing.json"),
        state_dir=tmp_path / "state",
        seat_factory=lambda: seat,
        client=Scripted12(),
        log=lambda m: None,
        today=lambda: dt.date(2026, 10, 29),
        readback_pause=0.0,
    )
    v = r.run()
    assert v.verdict == "failed" and v.reason.startswith("limit:") and seat.sent == []


def test_the_book_check_can_fail(tmp_path):
    ms = [{"matter_id": "x", "number": "1", "title": "A v. B", "read": True, "sections": {}, "flags": []}]
    out = tmp_path / "x.xlsx"
    book.build(ms, out, __import__("datetime").date(2026, 10, 29))
    assert book.check(out.read_bytes(), ms) == []
    assert book.check(out.read_bytes(), ms + [{**ms[0], "number": "2"}])


def test_the_plan_takes_every_discovery_paper_uncapped(tmp_path):
    from litigation_testkit import firm_data

    from medchron.litigation import firm as firm_mod

    firm = firm_mod.load(str(make_inputs(tmp_path / "inputs")))
    files = [
        {"id": f"d{i}", "name": f"Request for Production Set {i}", "ext": ".pdf", "modified": "2026-01-01"}
        for i in range(60)
    ]
    p = plan_for(files, firm, __import__("datetime").date(2026, 10, 29))
    assert len(p["candidates"]) == 60 and p["read_groups"] == ["discovery12"] and firm_data()


def test_the_envelope_and_the_proof_key():
    env = {**json.loads(json.dumps(_env())), "report": "discovery12"}
    j = job_mod.parse(env, Path("/x"))
    assert j.report == "discovery12" and j.workbooks == ()
    with pytest.raises(job_mod.LitigationJobError):
        job_mod.parse({**env, "workbooks": ["deadlines"]}, Path("/x"))
    assert lane_proof.lane_key("litigation", env) == "litigation-status.discovery12"


def _env() -> dict[str, Any]:
    from litigation_testkit import envelope

    return {**envelope(JOB), "slug": "example", "month_cents_used": 0}
