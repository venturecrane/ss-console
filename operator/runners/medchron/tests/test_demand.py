"""The demand job (medchron/demand/): the DAG on synthetic fixtures, the
privilege wall, the pre-paid estimate hold, the premise gate, the format check
on a rendered fixture, and the drafting gate before filing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from demand_testkit import (
    DRAFT,
    FOOTER,
    SIGNATURE,
    ScriptedClient,
    make_inputs,
    make_job,
    make_pdf,
    seat_with,
    standard_docs,
)
from medchron_testkit import PRICING
from medchron.demand import firm as firm_mod, format_check, preflight, pull
from medchron.demand.run import DemandRun

CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"


@pytest.fixture(autouse=True)
def _checker(monkeypatch):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


@pytest.fixture
def pricing(tmp_path: Path) -> Path:
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    return p


def _run(tmp_path: Path, pricing: Path, seat, client, **job_kw):
    inputs = tmp_path / "inputs"
    if not inputs.is_dir():
        make_inputs(inputs)
    jd = make_job(tmp_path / "job", **job_kw)
    log: list[str] = []
    r = DemandRun(
        jd,
        inputs_dir=str(inputs),
        pricing=str(pricing),
        seat_factory=lambda: seat,
        client=client,
        log=log.append,
        readback_pause=0.0,
    )
    return r, r.run(), log


# ---- the firm inputs -------------------------------------------------------------
def test_the_firm_inputs_validate_and_every_file_is_pinned(tmp_path):
    root = make_inputs(tmp_path / "in")
    assert firm_mod.load(root).model("compose") == "claude-sonnet-5"
    (root / "voice_profile.md").write_text("edited by hand", encoding="utf-8")
    with pytest.raises(firm_mod.DemandConfigError, match="does not match the pin"):
        firm_mod.load(root)


def test_an_unknown_key_refuses_rather_than_defaulting(tmp_path):
    root = make_inputs(tmp_path / "in")
    data = yaml.safe_load((root / "demand-firm.yaml").read_text())
    data["budget"]["per_job_cap"] = 5
    (root / "demand-firm.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(firm_mod.DemandConfigError, match="unknown key"):
        firm_mod.load(root)
    assert firm_mod.main([str(root)]) == 1


# ---- the DAG end to end ------------------------------------------------------------
def test_a_demand_runs_pull_to_read_back_and_files_both_deliverables(tmp_path, pricing):
    seat = seat_with(standard_docs())
    client = ScriptedClient()
    r, v, log = _run(tmp_path, pricing, seat, client)
    assert v.outcome == "delivered", (v.reason, log[-5:])
    names = sorted(f["name"] for f in v.files)
    assert names == sorted(
        [
            "Gap Audit - 100001 - " + r.date_stamp + ".docx",
            "Demand.Alpha Example.docx",
            "Demand.Alpha Example - attorney notes.docx",
        ]
    )
    # read back on the matter, in one dated folder this job created
    assert len(seat.created) == 1 and seat.created[0]["name"].startswith("Demand Prep ")
    assert {s["name"] for s in seat.sent} == set(names)
    # the vendor chronology folder was never pulled
    assert ["d4"] not in seat.mints and all("d4" not in m for m in seat.mints)
    # stages in order, every call live and streamed (no batch API exists on the client)
    stages = client.stages()
    assert stages[0] == "DIGEST" and stages.index("GAP") < stages.index("COMPOSE") < stages.index("AUDIT")
    assert all(c["max_tokens"] > 0 for c in client.calls)
    assert v.dollars > 0 and v.coverage_report is False
    # the rendered demand passes the firm's format check as filed
    demand = tmp_path / "job" / "data" / "out" / "demand" / "Demand.Alpha Example.docx"
    res = format_check.check(
        demand,
        signature=SIGNATURE,
        signer_title="Attorney at Law",
        footer_markers=["Settlement Communication", "1119, 1152"],
    )
    assert res.ok, res.fails
    assert any("open item" in n for n in res.notes)  # the NOT IN RECORD marker is counted, not refused
    bills = json.loads((tmp_path / "job" / "data" / "preflight.json").read_text())["bills"]
    assert bills["tab_providers_without_a_bill_in_file"] == []  # matched on the bill's letterhead, not its file name
    state = json.loads((tmp_path / "job" / "data" / "state.json").read_text())
    assert {"facts", "pull", "preflight", "premise", "transcribe", "summarize", "gate", "format"} <= set(state)


def test_a_resume_after_delivery_pays_for_nothing_twice(tmp_path, pricing):
    seat = seat_with(standard_docs())
    client = ScriptedClient()
    _run(tmp_path, pricing, seat, client)
    n = len(client.calls)
    r, v, _ = _run(tmp_path, pricing, seat, client)
    assert v.outcome == "delivered" and len(client.calls) == n
    assert len(seat.sent) == 3  # nothing re-sent: the delivery record says it went


def test_the_estimate_holds_before_anything_is_paid(tmp_path, pricing):
    make_inputs(tmp_path / "inputs", budget={"per_job_cap_usd": 1.0})
    seat = seat_with(standard_docs())
    client = ScriptedClient()
    _r, v, _ = _run(tmp_path, pricing, seat, client)
    assert v.outcome == "failed" and v.stage == "per_job_cap_usd"
    assert "estimate before anything was paid" in v.reason
    assert client.calls == [] and v.dollars == 0


def test_the_months_demand_spend_holds_the_estimate_too(tmp_path, pricing):
    seat = seat_with(standard_docs())
    client = ScriptedClient()
    _r, v, _ = _run(tmp_path, pricing, seat, client, cents=19_900)
    assert v.outcome == "failed" and v.stage == "monthly_budget_usd" and client.calls == []


def test_a_premise_failure_files_a_coverage_report_and_pays_nothing(tmp_path, pricing):
    docs = standard_docs() + [
        ("d5", "Demand Acceptance letter.pdf", make_pdf(["We accept the policy limits demand."]), "f-corr")
    ]
    seat = seat_with(docs)
    client = ScriptedClient()
    _r, v, _ = _run(tmp_path, pricing, seat, client)
    assert v.outcome == "delivered" and v.coverage_report is True
    assert client.calls == [] and v.dollars == 0
    assert [f["name"].startswith("Coverage Posture Report") for f in v.files] == [True]
    report = (tmp_path / "job" / "data" / "coverage-report.md").read_text()
    assert "acceptance: Demand Acceptance letter" in report and "No demand was drafted" in report


def test_a_written_denial_fails_g2(tmp_path, pricing):
    docs = standard_docs() + [
        ("d6", "Carrier letter 2.pdf", make_pdf(["Your claim number CLM-0001. Coverage is denied."]), "f-corr")
    ]
    _r, v, _ = _run(tmp_path, pricing, seat_with(docs), ScriptedClient())
    assert v.coverage_report is True
    decision = json.loads((tmp_path / "job" / "data" / "premise.json").read_text())
    assert [g["passed"] for g in decision["gates"]] == [True, False, True]


def test_a_truncated_digest_is_split_and_redone_without_a_hand_step(tmp_path, pricing):
    client = ScriptedClient(truncate_digest_once=True)
    _r, v, _ = _run(tmp_path, pricing, seat_with(standard_docs()), client)
    assert v.outcome == "delivered"
    assert client.stages().count("DIGEST") == 3  # the chunk, then its two halves
    assert list((tmp_path / "job" / "data" / "digest").glob("truncated-*.md"))


def test_the_drafting_gate_refuses_before_anything_is_filed(tmp_path, pricing):
    bad = DRAFT.replace(
        "Demand is hereby made",
        'The record says "the patient reported severe pain radiating to both arms daily" and demand is made',
    )
    seat = seat_with(standard_docs())
    _r, v, _ = _run(tmp_path, pricing, seat, ScriptedClient(draft=bad))
    assert v.outcome == "held" and "drafting gate refused" in v.reason
    assert seat.sent == [] and seat.created == []


def test_an_off_format_demand_is_refused_before_filing(tmp_path, pricing):
    bad = DRAFT.replace("## Liability", "## Fault")
    seat = seat_with(standard_docs())
    _r, v, _ = _run(tmp_path, pricing, seat, ScriptedClient(draft=bad))
    assert v.outcome == "held" and "format check refused" in v.reason and "Liability" in v.reason
    assert seat.sent == []


# ---- the privilege wall -------------------------------------------------------------
FIRM = ("firm.example",)


@pytest.mark.parametrize(
    "sender,recipients,client,want",
    [
        ("client@mail.example", ["atty@firm.example"], {"client@mail.example"}, "client to firm"),
        ("atty@firm.example", ["client@mail.example"], {"client@mail.example"}, "firm to client"),
        ("atty@firm.example", ["para@firm.example"], {"client@mail.example"}, "firm internal"),
        ("adjuster@carrier.example", ["atty@firm.example"], {"client@mail.example"}, None),
        ("atty@firm.example", ["adjuster@carrier.example", "para@firm.example"], {"client@mail.example"}, None),
    ],
)
def test_the_wall_is_decided_by_addresses(sender, recipients, client, want):
    assert pull.wall_reason(sender, recipients, FIRM, client) == want


def test_with_no_client_address_a_consumer_mailbox_is_walled_as_possibly_the_client():
    assert (
        pull.wall_reason("someone@mail.example", ["atty@firm.example"], FIRM, set(), ("mail.example",))
        == "client to firm"
    )
    assert pull.wall_reason("adjuster@carrier.example", ["atty@firm.example"], FIRM, set(), ("mail.example",)) is None


def test_walled_email_never_reaches_the_corpus_and_kept_mail_brings_its_body(tmp_path, monkeypatch):
    root = make_inputs(tmp_path / "in")
    firm = firm_mod.load(root)
    data = tmp_path / "data"
    (data / "raw").mkdir(parents=True)
    rows = []
    msgs = {
        "m1": {
            "subject": "my injuries",
            "sender": "client@mail.example",
            "recipients": ["atty@firm.example"],
            "date": "",
            "body": "Privileged words the client wrote to her lawyer.",
            "attachments": [("x.pdf", b"%PDF" * 9000)],
        },
        "m2": {
            "subject": "claim CLM-0001 limits",
            "sender": "adj@carrier.example",
            "recipients": ["atty@firm.example"],
            "date": "",
            "body": "The carrier's position.",
            "attachments": [("dec page.pdf", b"%PDF-dec" * 9000)],
        },
    }
    for mid in msgs:
        p = data / "raw" / f"{mid}.msg"
        p.write_bytes(b"x")
        rows.append(
            {
                "id": mid,
                "name": mid,
                "ext": ".msg",
                "folder": "/Correspondence",
                "ok": True,
                "path": str(p),
                "sha256": mid,
            }
        )
    monkeypatch.setattr(pull, "_open_msg", lambda path: msgs[path.stem])
    out = pull.split_emails(rows, data, firm, {"client@mail.example"})
    assert [w["reason"] for w in out["walled"]] == ["client to firm"]
    kept_names = [k["name"] for k in out["kept"]]
    assert kept_names == ["Email: claim CLM-0001 limits", "dec page.pdf"]
    assert "Privileged words" not in json.dumps(out["kept"])


# ---- preflight ------------------------------------------------------------------------
def test_a_control_character_page_goes_to_transcription_before_any_summary(tmp_path):
    good = "Exampletown ER itemized statement with enough ordinary words to read as English text here."
    assert not preflight.junk_page(good)
    assert preflight.junk_page("\x01\x02\x03\x04\x05\x06 \x07\x08\x0b\x0c\x0e ok")
    assert preflight.junk_page(" ")


def test_the_estimate_counts_characters_pages_and_the_drafting_tail(tmp_path):
    firm = firm_mod.load(make_inputs(tmp_path / "in"))
    rows = [{"chars": 1_000_000, "pages": 10, "transcribe": [2, 3]}, {"chars": 0, "pages": 4, "transcribe": "all"}]
    e = preflight.estimate(rows, firm)
    assert e["transcription_pages"] == 6 and e["pages"] == 14
    assert e["usd"] == round((1_000_000 + 6 * 2500) / 1e6 * 6.0 + 6 * 0.01 + 4.0, 2)


def test_the_bill_reconciliation_flags_both_directions():
    medicals = [{"provider": "Exampletown Emergency", "charges": []}, {"provider": "Northfield Imaging", "charges": []}]
    rows = [{"name": "Exampletown Emergency bill"}, {"name": "Ridgeview Chiropractic ledger"}]
    rec = preflight.reconcile(medicals, rows)
    assert rec["tab_providers_without_a_bill_in_file"] == ["Northfield Imaging"]
    assert rec["bills_whose_provider_is_not_on_the_tab"] == ["Ridgeview Chiropractic ledger"]


# ---- the format check ------------------------------------------------------------------
def test_the_format_check_refuses_a_leftover_template_slot_and_counts_open_items(tmp_path):
    from medchron.demand import house

    ref = tmp_path / "ref.docx"
    from demand_testkit import make_reference

    make_reference(ref)
    kw = dict(signature=SIGNATURE, signer_title="Attorney at Law", author="Example")
    path, _ = house.render(DRAFT, ref, tmp_path / "ok", **kw)
    ok = format_check.check(path, signature=SIGNATURE, signer_title="Attorney at Law", footer_markers=["1119, 1152"])
    assert ok.ok, ok.fails
    path2, _ = house.render(
        DRAFT.replace("Demand is hereby made", "{{CLIENT_NAME}} Demand is hereby made"), ref, tmp_path / "bad", **kw
    )
    bad = format_check.check(path2, signature=SIGNATURE, signer_title="Attorney at Law", footer_markers=["1119, 1152"])
    assert any("curly-brace marker" in f for f in bad.fails)
    assert FOOTER  # the reference's footer carries the markers the check reads


def _scanned_pdf() -> bytes:
    """Page 1 has a text layer; page 2 is an image with no text (a scanned bill)."""
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Exampletown ER itemized statement page one with readable words.", fontsize=11)
    page = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
    pix.clear_with(200)
    page.insert_image(pymupdf.Rect(72, 72, 300, 300), pixmap=pix)
    data = doc.tobytes()
    doc.close()
    return data


def test_an_image_only_page_is_transcribed_and_merged_under_its_own_marker(tmp_path, pricing):
    docs = standard_docs() + [("d7", "Exampletown ER bill page 2.pdf", _scanned_pdf(), "f-med")]
    client = ScriptedClient()
    _r, v, _ = _run(tmp_path, pricing, seat_with(docs), client)
    assert v.outcome == "delivered"
    assert client.stages().count("VISION") == 1  # one page, not the file
    assert client.stages().index("VISION") < client.stages().index("DIGEST")
    text = (tmp_path / "job" / "data" / "text" / "d7.txt").read_text()
    assert "[p.2] (machine transcription)\ntranscribed page text" in text and "readable words" in text
    assert json.loads((tmp_path / "job" / "data" / "transcribed.json").read_text()) == ["Exampletown ER bill page 2"]


def test_an_open_item_in_the_expiry_line_is_counted_not_refused(tmp_path):
    """Live smoke 2026-10-06: a brief that asks for {{FILL}} on firm-supplied
    values put one in the banner's expiry date; the shape check still holds."""
    from demand_testkit import make_reference
    from medchron.demand import house

    ref = tmp_path / "ref.docx"
    make_reference(ref)
    md = DRAFT.replace("Friday, November 6, 2026", "{{FILL: weekday and date 30 days after the letter date}}")
    path, _ = house.render(md, ref, tmp_path / "o", signature=SIGNATURE, signer_title="Attorney at Law", author="E")
    res = format_check.check(path, signature=SIGNATURE, signer_title="Attorney at Law", footer_markers=["1119, 1152"])
    assert res.ok, res.fails
    md2 = DRAFT.replace("This Demand Expires at 5:00 P.M. Pacific Time on Friday, November 6, 2026", "Respond soon")
    path2, _ = house.render(md2, ref, tmp_path / "o2", signature=SIGNATURE, signer_title="Attorney at Law", author="E")
    assert any(
        "expiry line" in f
        for f in format_check.check(
            path2, signature=SIGNATURE, signer_title="Attorney at Law", footer_markers=["1119, 1152"]
        ).fails
    )
