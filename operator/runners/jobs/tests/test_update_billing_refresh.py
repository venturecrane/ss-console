"""An update that names a billing record refreshes the billing worksheet, end to end (2026-11-17).

WHY. An update named two patient ledgers whose dates of service are in no
medical record on file; the firm's billing rule dropped them by name, no
billing stage ran, and those dates reached no filed document. The real
stages, a fake seat and a scripted model; every name, date and amount is
invented.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from jobs.stages import billing_docx, billing_update, update_supersede
from jobs.stages.review import docx_text
from jobs.medchron.update import review_list as RL
from jobs_testkit import doc_row, make_pdf
from test_update_e2e import CHRON_FOLDER, MEDICAL, _driver, _Model, _text, _upload_pause, scene  # noqa: F401 - scene is a pytest fixture the tests request by name

LEDGER = "Example Clinic Ledger"
STATEMENT = "Example Clinic Billing Statement"
WORKSHEET = "Alpha Example - Medical Billing Worksheet 09-01-26.docx"
REVIEW = "Alpha Example - Records Review 09-01-26.docx"
WS_BYTES, RV_BYTES = b"PK delivered worksheet", b"PK delivered records review"
BILL = {
    "doc_type": "LEDGER",
    "provider": "Example Clinic",
    "patient": "Alpha Example",
    "date_first": "12/01/2025",
    "date_last": "03/03/2026",
    "printed_totals": [{"label": "Total charges", "amount": "$300.00", "page": 1}],
    "printed_payments": [],
    "line_items": [
        {"date": "12/01/2025", "description": "office visit", "charge": "$150.00", "page": 1},
        {"date": "03/03/2026", "description": "office visit", "charge": "$150.00", "page": 1},
    ],
    "notes": "",
}


class _Billing(_Model):
    """The update's model, which also reads billing pages (no system prompt)."""

    def create(self, **kw):
        if not kw.get("tool_choice") and "doc_type" in json.dumps(kw.get("messages"), default=str):
            self.calls.append({"billing": True})
            return _text(json.dumps(BILL))
        return super().create(**kw)


def _scene(scene, firm: Path, *, recorded: dict[str, str]):
    job_dir, seat = scene
    ledger, statement = make_pdf(["Ledger. 03/03/2026 office visit $150.00"]), make_pdf(["Statement of charges"])
    seat.docs += [
        doc_row("lg", f"{LEDGER}.pdf", MEDICAL, len(ledger)),
        doc_row("bs", f"{STATEMENT}.pdf", MEDICAL, len(statement)),
        doc_row("w1", WORKSHEET, CHRON_FOLDER, len(WS_BYTES)),
        doc_row("r1", REVIEW, CHRON_FOLDER, len(RV_BYTES)),
    ]
    seat.blobs.update({"lg": ledger, "bs": statement, "w1": WS_BYTES, "r1": RV_BYTES})
    body = yaml.safe_load((job_dir / "job.yaml").read_text())
    body["selection"]["include_file_ids"].append("lg")
    body["delivered_files"] = recorded
    (job_dir / "job.yaml").write_text(yaml.safe_dump(body, sort_keys=False))
    cfg = yaml.safe_load(firm.read_text())
    cfg["coverage"]["exclusions"].append({"match": r"(?i)\bbill|ledger", "reason": "billing or lien record"})
    firm.write_text(yaml.safe_dump(cfg, sort_keys=False))
    return job_dir, seat


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def test_a_named_ledger_refreshes_the_worksheet_and_retires_the_unedited_one(
    scene, data_root, firm_config_path, pricing_path, monkeypatch, tmp_path
) -> None:
    job_dir, seat = _scene(scene, firm_config_path, recorded={"w1": _sha(WS_BYTES), "r1": "0" * 64})
    _upload_pause(monkeypatch)
    model = _Billing()
    o = _driver(job_dir, firm_config_path, pricing_path, seat, model).run()[0]
    assert o.outcome == "delivered", o
    sd = data_root / "example-matter"
    ud = sd / "runs" / "alpha" / "update"
    # Every billing record on the matter was read, the delivered one included; nothing else paid for billing.
    assert sum(1 for c in model.calls if c.get("billing")) == 2
    # A billing record is never a chronology source and never goes to vision.
    raw = [json.loads(x) for x in (sd / "raw_manifest.jsonl").read_text().splitlines()]
    assert not {"lg", "bs"} & {r["id"] for r in raw}
    assert not {"lg", "bs"} & {r["id"] for r in json.loads((sd / "scan_queue.json").read_text())}
    # The refreshed worksheet lists the billed date no record carries.
    ws = next((sd / "out" / "alpha").glob("Alpha Example - Medical Billing Worksheet *.docx"), None)
    assert ws is not None and ws.name in [s["name"] for s in seat.sent]
    text = docx_text(ws)
    assert billing_docx.NO_RECORD_HEADING in text and "03/03/2026" in text
    # The unedited delivered worksheet is retired by id; the records review no record names is left beside.
    assert set(seat.deleted) == {"c1", "w1"}
    sup = json.loads((ud / "superseded.json").read_text())
    assert [x["name"] for x in sup["retired"]] == [WORKSHEET]
    (left,) = sup["left"]
    assert (left["name"], left["kind"], left["why"], left["id"]) == (
        REVIEW,
        update_supersede.RECORDS_REVIEW,
        "changed since delivery",
        "r1",
    )
    assert (
        left["remove_with"].startswith("python -m jobs.medchron.update_retire ")
        and "--file-id r1" in left["remove_with"]
    )
    # The records review says what the ledger fed.
    review = docx_text(
        sd / "out" / "alpha" / next(n for n in (s["name"] for s in seat.sent) if " - Records Review " in n)
    )
    assert RL.BILLING_ROW in review
    # The reply's billing sentence is code's.
    line = billing_update.reply_line(ud)
    assert line is not None and "1 date(s) of service appear only in billing records" in line
    assert "still in the folder" not in line  # the worksheet was SMD's own, and it is retired
    # The page charge never counts a billing page.
    assert o.pages == _pages_without_billing(sd)


def test_an_edited_worksheet_is_left_beside_the_new_one_and_the_reply_says_so(
    scene, data_root, firm_config_path, pricing_path, monkeypatch
) -> None:
    job_dir, seat = _scene(scene, firm_config_path, recorded={"w1": "1" * 64})
    _upload_pause(monkeypatch)
    o = _driver(job_dir, firm_config_path, pricing_path, seat, _Billing()).run()[0]
    assert o.outcome == "delivered", o
    assert "w1" not in seat.deleted and "c1" in seat.deleted
    line = billing_update.reply_line(data_root / "example-matter" / "runs" / "alpha" / "update")
    assert line is not None and "The earlier billing worksheet is still in the folder" in line


def test_an_update_naming_no_billing_record_refreshes_nothing(
    scene, data_root, firm_config_path, pricing_path, monkeypatch
) -> None:
    job_dir, seat = scene
    _upload_pause(monkeypatch)
    model = _Billing()
    o = _driver(job_dir, firm_config_path, pricing_path, seat, model).run()[0]
    assert o.outcome == "delivered", o
    ud = data_root / "example-matter" / "runs" / "alpha" / "update"
    assert not any(c.get("billing") for c in model.calls)
    assert billing_update.reply_line(ud) is None
    assert not any("Billing Worksheet" in s["name"] for s in seat.sent)


def _pages_without_billing(sd: Path) -> int:
    """The page charge (``update.charged``) is counted off ``extracted.jsonl``;
    a billing page never enters it, so it is never charged."""
    from jobs import budget
    from jobs.medchron.update import charged

    rows = [json.loads(x) for x in (sd / "extracted.jsonl").read_text().splitlines()]
    assert not {"lg", "bs"} & {str(r.get("id")) for r in rows}
    return charged.charged_pages(sd, "alpha", budget.pages_read(sd / "extracted.jsonl"))


class _Forbidden:
    """A model client every call of which fails the test (and is counted)."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.messages = self

    def create(self, **_kw):
        self.calls.append("create")
        raise AssertionError("a rehearsal called the model")

    def stream(self, **_kw):
        self.calls.append("stream")
        raise AssertionError("a rehearsal called the model")


def _no_seat():
    raise AssertionError("a rehearsal opened the seat")


# ---- jobs rehearse walks the billing stages on a copy -------------------------------------
def test_rehearse_reports_what_billing_would_pay_and_walks_the_free_stages_once_settled(
    scene, data_root, firm_config_path, pricing_path, monkeypatch
) -> None:
    from jobs.medchron import rehearsal
    from jobs.medchron.state import RunState

    job_dir, seat = _scene(scene, firm_config_path, recorded={})
    _upload_pause(monkeypatch)
    monkeypatch.setenv("JOBS_PRICING_JSON", str(pricing_path))  # the seat's pricing table, as the image sets it
    assert _driver(job_dir, firm_config_path, pricing_path, seat, _Billing()).run()[0].outcome == "delivered"
    sd = data_root / "example-matter"
    st = RunState.load_or_new(sd / "runs" / "alpha" / "state.json", slug="example-matter", unit="alpha")
    after = ("update_billing_docs", "update_billing_extract", "update_dos_check", "update_billing_chart")
    for name in (*after, "update_billing_docx", "update_manifest", "review", "upload"):
        st.stages[name].status = "pending"
    st.outcome = st.outcome_reason = None
    st.save()
    kw = {"firm_config": str(firm_config_path), "pricing": str(pricing_path), "log": lambda *_: None}
    # Settled: every billing file is pulled and read on this matter, so the free stages run in the copy.
    model = _Forbidden()
    _w, outs = rehearsal.run(job_dir, seat_factory=_no_seat, client=model, **kw)
    notes = "\n".join(outs[0].notes)
    assert model.calls == [] and outs[0].stage == "review", notes
    for name in (*after, "update_billing_docx", "update_manifest"):
        assert f"rehearse {name}: ok" in notes, notes
    # Not settled: the billing read is reported with what it would pay, and the walk stops there.
    (sd / "billing_extract.jsonl").write_text("")
    _w, outs = rehearsal.run(job_dir, seat_factory=_no_seat, client=model, **kw)
    notes = "\n".join(outs[0].notes)
    assert outs[0].stage == "update_billing_extract" and model.calls == [], notes
    assert "rehearse update_billing_extract: billing: 2 file(s), 2 page(s) on disk to read: about" in notes
