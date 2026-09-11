from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from medchron import (
    budget as budget_mod,
    config as config_mod,
    dag,
    decisions,
    driver as driver_mod,
    job as job_mod,
    llm as llm_mod,
)
from medchron.state import RunState, state_path
from medchron_testkit import (
    FIRM_CONFIG,
    FakeSeat,
    calls,
    doc_row,
    job_yaml,
    make_pdf,
    seed_folders,
    seed_raw_manifest,
    write_ledger,
)

PROSE = (
    "Patient seen in clinic today for follow up of neck pain after the collision. "
    "The patient reports that the pain is improving with therapy and has no new complaints. "
) * 6


def _cfg(firm_config_path: Path) -> config_mod.FirmConfig:
    return config_mod.load(str(firm_config_path))


# ---- decisions ---------------------------------------------------------------
def test_selection_is_subtraction_with_disclosure(job_dir: Path, data_root: Path, firm_config_path: Path) -> None:
    seed_folders(data_root, ["MEDICAL", "INVOICES", "VENDOR CHRON", "CLIENT CORRESPONDENCE", "PHOTOS", "LIENS"])
    d = decisions.selection(job_mod.load(job_dir), _cfg(firm_config_path), data_root / "example-matter", dry_run=False)
    assert not d.held
    assert d.payload["include_prefixes"] == ["/INVOICES", "/LIENS", "/MEDICAL"]
    assert d.payload["_decided"]["excluded_top_level"] == ["CLIENT CORRESPONDENCE", "PHOTOS", "VENDOR CHRON"]
    assert (data_root / "example-matter" / "include.json").is_file()


def test_selection_joint_matter_uses_unit_folders_and_shared(
    tmp_path: Path, data_root: Path, firm_config_path: Path
) -> None:
    jd = tmp_path / "job"
    jd.mkdir()
    (jd / "job.yaml").write_text(job_yaml(data_root, joint=True))
    seed_folders(data_root, ["Alpha_Example", "Beta_Example", "EMAILS ALL", "PHOTOS", "MISC"])
    d = decisions.selection(job_mod.load(jd), _cfg(firm_config_path), data_root / "example-matter", dry_run=True)
    assert d.payload["include_prefixes"] == ["/Alpha_Example", "/Beta_Example", "/EMAILS ALL"]
    assert any("MISC" in n for n in d.notes)
    assert not (data_root / "example-matter" / "include.json").exists()  # dry run writes nothing


def test_selection_holds_when_a_unit_folder_is_missing(tmp_path: Path, data_root: Path, firm_config_path: Path) -> None:
    jd = tmp_path / "job"
    jd.mkdir()
    (jd / "job.yaml").write_text(job_yaml(data_root, joint=True))
    seed_folders(data_root, ["Alpha_Example", "EMAILS"])
    d = decisions.selection(job_mod.load(jd), _cfg(firm_config_path), data_root / "example-matter", dry_run=False)
    assert d.held and "Beta_Example" in d.holds[0]


def test_fold_keeps_everything_but_skip_types_and_discloses_encrypted(
    job_dir: Path, data_root: Path, firm_config_path: Path
) -> None:
    # The shape stages/msg.py writes: kept attachments only, with the pull's
    # name when the bytes are already in the corpus; encrypted ones by name.
    (data_root / "example-matter" / "msg_attachments.json").write_text(
        json.dumps(
            {
                "comparable": True,
                "attachments": [
                    {"sha256": "a" * 64, "local": "aaaaaaaaaaaa.pdf", "kind": "pdf", "already_pulled_as": None},
                    {"sha256": "c" * 64, "local": "cccccccccccc.png", "kind": "image", "already_pulled_as": None},
                    {
                        "sha256": "e" * 64,
                        "local": "eeeeeeeeeeee.pdf",
                        "kind": "pdf",
                        "already_pulled_as": "filed copy.pdf",
                    },
                ],
                "encrypted": [{"email": "RE: records", "attachment": "secure.rpmsg", "bytes": 30000}],
            }
        )
    )
    d = decisions.fold(job_mod.load(job_dir), _cfg(firm_config_path), data_root / "example-matter", dry_run=False)
    assert d.payload["fold"] == ["a" * 12, "c" * 12]
    assert d.payload["_disclosed_encrypted"] == ["secure.rpmsg"]
    assert any("1 encrypted attachment" in n for n in d.notes)


def test_billing_docs_carries_the_pulled_path_and_no_invented_page_count(
    job_dir: Path, data_root: Path, firm_config_path: Path
) -> None:
    """billing_extract renders `path`; a row without a real file is the seat
    KeyError of 2026-09-04. The manifest carries no page count, so the row
    carries none either (the stage counts what it renders)."""
    sd = data_root / "example-matter"
    (sd / "raw").mkdir()
    invoice = sd / "raw" / "f2.pdf"
    invoice.write_bytes(make_pdf(["Total charges $1,800.00"]))
    seed_raw_manifest(
        data_root,
        [
            {"id": "f1", "name": "clinic note", "ok": True, "path": str(sd / "raw" / "f1.pdf")},
            {"id": "f2", "name": "Example Clinic invoice", "ok": True, "path": str(invoice)},
            {"id": "f3", "name": "Example Clinic invoice", "ok": True, "path": None, "duplicate_of": "f2"},
            {"id": "f4", "name": "old bill", "ok": False, "error": "no url"},
        ],
    )
    d = decisions.billing_docs(job_mod.load(job_dir), _cfg(firm_config_path), sd, dry_run=False)
    assert not d.held
    assert d.payload["docs"] == [{"id": "f2", "name": "Example Clinic invoice", "path": str(invoice)}]
    assert Path(d.payload["docs"][0]["path"]).is_file()
    assert json.loads((sd / "billing_docs.json").read_text())["docs"] == d.payload["docs"]
    # A matched row the pull recorded without a local path is a HOLD naming
    # it, never a row the paid stage would fail on mid-run.
    seed_raw_manifest(data_root, [{"id": "f5", "name": "ledger scan", "ok": True}])
    d = decisions.billing_docs(job_mod.load(job_dir), _cfg(firm_config_path), sd, dry_run=False)
    assert d.held and "ledger scan" in d.holds[0]


def test_orphans_explains_by_config_reason_and_holds_on_residue(
    job_dir: Path, data_root: Path, firm_config_path: Path
) -> None:
    sd = data_root / "example-matter"
    (sd / "units").mkdir()
    (sd / "units" / "alpha.json").write_text(json.dumps([{"id": "f1", "name": "clinic note", "pages": 3}]))
    seed_raw_manifest(
        data_root,
        [
            {"id": "f1", "name": "clinic note.pdf", "ok": True},
            {"id": "f2", "name": "Retainer signed.pdf", "ok": True},
            {"id": "f3", "name": "unknown scan.pdf", "ok": True},
        ],
    )
    job = job_mod.load(job_dir)
    d = decisions.orphans(job, _cfg(firm_config_path), sd, job.units[0], dry_run=False)
    assert d.held and "unknown scan.pdf" in d.holds[0]
    assert not (sd / "orphans.json").exists()
    seed_raw_manifest(
        data_root,
        [
            {"id": "f1", "name": "clinic note.pdf", "ok": True},
            {"id": "f2", "name": "Retainer signed.pdf", "ok": True},
            {"id": "f3", "name": "image001", "ext": ".png", "size_got": 1168, "ok": True},
        ],
    )
    d = decisions.orphans(job, _cfg(firm_config_path), sd, job.units[0], dry_run=False)
    assert not d.held
    reasons = [o["reason"] for o in json.loads((sd / "orphans.json").read_text())["orphans"]]
    assert reasons[0].startswith("engagement document")
    assert reasons[1].startswith("email signature or spacer graphic (1168 bytes .png)")


def test_control_picks_the_page_with_most_native_text(job_dir: Path, data_root: Path, firm_config_path: Path) -> None:
    sd = data_root / "example-matter"
    (sd / "out" / "alpha").mkdir(parents=True)
    (sd / "out" / "alpha" / "page_map.json").write_text(
        json.dumps(
            [
                {"exhibit": 1, "files": [{"file": "sparse scan.pdf", "start_page": 1, "pages": 10}]},
                {"exhibit": 2, "files": [{"file": "dense notes.pdf", "start_page": 1, "pages": 5}]},
            ]
        )
    )
    (sd / "extracted.jsonl").write_text(
        json.dumps({"name": "sparse scan", "pages": 10, "chars": 400})
        + "\n"
        + json.dumps({"name": "dense notes", "pages": 5, "chars": 12000})
        + "\n"
    )
    job = job_mod.load(job_dir)
    d = decisions.control(job, _cfg(firm_config_path), sd, job.units[0], dry_run=False)
    assert d.payload["exhibit"] == 2 and d.payload["page"] == 2
    assert (sd / "record_control.json").is_file()


# ---- driver ------------------------------------------------------------------
def _seat() -> FakeSeat:
    """One MEDICAL folder with a dense seven-page record whose FIRST page is a
    blank cover sheet (no text layer: the one page the scanned-page classifier
    has to look at, so the run reaches its controls gate with a real target),
    a one-page invoice (matched by the firm's billing name rule, so the paid
    billing_extract stage opens a real pulled file), and a one-page
    engagement document (excluded from composition by the firm's name rule
    and explained to the coverage gate by its exclusion reason); the map
    cites the record, so every stage after composition has real artifacts to
    work on."""
    f1 = make_pdf([""] + [f"{PROSE} Page {i} of the record." for i in range(2, 8)])
    f2, f9 = make_pdf(["Example Clinic\nTotal charges $1,800.00"]), make_pdf([PROSE])
    docs = [
        doc_row("f1", "f1.pdf", "fold-med", len(f1)),
        doc_row("f2", "Example Clinic invoice.pdf", "fold-med", len(f2)),
        doc_row("f9", "Retainer signed.pdf", "fold-med", len(f9)),
    ]
    return FakeSeat(
        docs,
        [{"id": "fold-med", "name": "MEDICAL", "parentId": None, "path": "/MEDICAL"}],
        {"f1": f1, "f2": f2, "f9": f9},
    )


REAL_MAP = (
    "## ENTRIES\n01/20/2026\nExample Clinic | Patient Complaints & Limitations\n\n"
    "The patient reports neck pain after the collision rated 6 of 10. (FILE: f1.pdf, p. 2)\n\n"
    "Medical Diagnoses\n\nCervical strain. (FILE: f1.pdf, p. 3)\n\n"
    "## INDEX\n2026-01-20 | Example Clinic | S13.4XXA | f1.pdf\n\n## BILLING-DATES\nnone in this chunk\n\n"
    "## CONFLICTS / REFERENCED-BUT-ABSENT\nnone observed\n\n## FILES-SEEN\n"
    "=== FILE: f1.pdf (fileId f1) === entries: 1\n"
)
SUPPORTED = {"verdict": "SUPPORTED", "unsupported_assertions": [], "contradictions": [], "note": "on the page"}
UNSUPPORTED = {"verdict": "UNSUPPORTED", "unsupported_assertions": ["x"], "contradictions": [], "note": "not this page"}
BILL = {
    "doc_type": "MEDICAL_BILL",
    "provider": "Example Clinic",
    "patient": "Alpha Example",
    "date_first": "01/20/2026",
    "date_last": "01/20/2026",
    "printed_totals": [{"label": "Total charges", "amount": "$1,800.00", "page": 1}],
    "line_items": [{"date": "01/20/2026", "description": "visit", "charge": "$1,800.00", "page": 1}],
    "notes": "",
}


class _Usage:
    input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens = 10, 5, 0, 0


def _text_msg(text: str):
    return type(
        "M",
        (),
        {"content": [type("B", (), {"type": "text", "text": text})()], "stop_reason": "end_turn", "usage": _Usage()},
    )()


def _tool_msg(payload: dict):
    return type(
        "M",
        (),
        {
            "content": [type("B", (), {"type": "tool_use", "input": payload})()],
            "stop_reason": "tool_use",
            "usage": _Usage(),
        },
    )()


class _Stream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


class _NoNetwork:
    """Answers each paid shape the run makes from a script; anything else
    reaching the SDK fails loudly. Streams are the compose; a forced tool is
    an audit verdict (a control, cited to the other exhibit, is refused);
    the classifier's system prompt gets labels; a billing transcription
    request (its first block names the document) gets one printed total;
    nothing else is expected."""

    def __init__(self) -> None:
        self.messages = self
        self.calls: list[dict] = []
        self.classified: list[str] = []  # every page label the classifier was asked about

    def _system(self, kw) -> str:
        sysm = kw.get("system")
        if isinstance(sysm, list):
            return sysm[0].get("text", "")
        return sysm or ""

    def create(self, **kw):
        self.calls.append(kw)
        if kw.get("tool_choice"):
            tail = kw["messages"][0]["content"][-1]["text"]
            return _tool_msg(UNSUPPORTED if "cited to Exhibit 2 p.1)" in tail else SUPPORTED)
        if "classify single pages" in self._system(kw):
            labels = [b["text"].split()[1].rstrip(":") for b in kw["messages"][0]["content"] if b["type"] == "text"]
            self.classified.extend(labels)
            answer = {"CTL0": "ORDER", "CTL1": "INDEX"}
            return _text_msg("\n".join(f"{lb} = {answer.get(lb, 'RECORD')}" for lb in labels))
        head = kw["messages"][0]["content"][0]
        if head.get("type") == "text" and head["text"].startswith("Document: "):
            return _text_msg(json.dumps(BILL))
        raise AssertionError("a driver test reached the SDK client with an unexpected shape")

    def stream(self, **kw):
        self.calls.append(kw)
        return _Stream(_text_msg(REAL_MAP))


def _controls(install_root: Path) -> None:
    """The scanned-page classifier's falsifier pages and the ICD tables, as an
    install carries them under <install_root>/controls/: the laptop's data
    root, or the seat's run dir seeded from the vault (never a job's own
    data_root, which is fresh per job on a seat)."""
    ctl = install_root / "controls"
    ctl.mkdir(parents=True, exist_ok=True)
    (ctl / "control-order.pdf").write_bytes(
        make_pdf(["Order #: 1\nRequested Date Range: 2026\nvendor@example-retrieval.com"])
    )
    (ctl / "control-index.pdf").write_bytes(make_pdf(["This list is computer generated\nabdomen 4\n"]))
    (ctl / "controls.json").write_text(
        json.dumps(
            [
                {"pdf": "controls/control-order.pdf", "page": 1, "label": "ORDER"},
                {"pdf": "controls/control-index.pdf", "page": 1, "label": "INDEX"},
            ]
        )
    )
    icd = ctl / "icd"
    icd.mkdir(exist_ok=True)
    (icd / "icd10cm_order.txt").write_text(
        "{:<5} {:<7} {} {:<60} {}\n".format(
            "00001", "S134XXA", "1", "Sprain of lig", "Sprain of ligaments of cervical spine, initial encounter"
        )
    )
    (icd / "CMS32_DESC_LONG_DX.txt").write_text("7242 Lumbago\n")
    (icd / "VERSION.json").write_text("{}")


def _driver(
    job_dir: Path, firm_config_path: Path, pricing_path: Path, break_at: str | None = None, **kw
) -> driver_mod.Driver:
    kw.setdefault("seat_factory", _seat)
    kw.setdefault("client", _NoNetwork())
    _controls(job_mod.load(job_dir).install_root)
    d = driver_mod.Driver(
        job_dir, firm_config=str(firm_config_path), pricing=str(pricing_path), log=lambda *_: None, **kw
    )
    if break_at:
        # One in-process crash at the named stage, then the real runner: how a
        # kill mid-stage looks to the state file.
        real = dag.BY_NAME[break_at].runner
        fired = {"n": 0}

        def once(sr):
            if fired["n"] == 0:
                fired["n"] += 1
                raise RuntimeError("boom")
            return real(sr)

        d._runner_override = {break_at: once}
    return d


def test_dry_run_authors_nothing_and_runs_nothing(
    job_dir: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    seed_folders(data_root, ["MEDICAL"])
    outs = _driver(job_dir, firm_config_path, pricing_path, dry_run=True).run()
    assert outs[0].outcome == "dry_run"
    # Every hook still reports; a would-hold is a note, so the hold rate of a
    # rule can be read off delivered matters without a run.
    assert any(n.startswith("WOULD HOLD at decide_control") for n in outs[0].notes)
    assert not (data_root / "example-matter" / "include.json").exists()
    assert calls(data_root) == []


def test_the_whole_dag_runs_in_process_without_the_frozen_pipeline(
    tmp_path: Path, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MEDCHRON_PIPELINE_DIR", raising=False)  # no frozen checkout anywhere
    # The seat's layout: a fresh per-job data_root and a separate install root
    # carrying the controls and ICD tables. Nothing under data_root/controls.
    job_dir, install_root = tmp_path / "job", tmp_path / "install"
    job_dir.mkdir()
    (job_dir / "job.yaml").write_text(job_yaml(data_root, install_root=install_root), encoding="utf-8")
    sd = data_root / "example-matter"
    client = _NoNetwork()
    outs = _driver(job_dir, firm_config_path, pricing_path, client=client).run()
    o = outs[0]
    assert not (data_root / "controls").exists()
    # Every stage ran: the ported ones for real (the map cites the record, so
    # an exhibit is built, classified, stripped of nothing, covered, charted
    # and audited), the frozen ones (identity, render, manifest) as fakes.
    assert o.outcome == "delivered" and o.stage is None, o
    assert (sd / "orphans.json").is_file()
    page_map = json.loads((sd / "out" / "alpha" / "page_map.json").read_text())
    assert len(page_map) == 1 and page_map[0]["total_pages"] == 7 and page_map[0]["provider"] == "Example Clinic"
    doc = (sd / "runs" / "alpha" / "final-chronology.md").read_text()
    assert "(Exhibit 1 - p. 2)" in doc and "| S13.4XXA | Sprain of ligaments" in doc
    assert json.loads((sd / "nonrecord.json").read_text())["1"]["drop_pages"] == []
    assert json.loads((sd / "scanned_labels.json").read_text())["controls_ok"] is True
    audit_rows = [json.loads(line) for line in (sd / "out" / "alpha" / "audit-results.jsonl").read_text().splitlines()]
    assert [r["verdict"] for r in audit_rows if r["kind"] == "real"] == [
        "SUPPORTED"
    ]  # "Cervical strain." is under the 30-char floor
    assert "GATE PASS" in (sd / "runs" / "alpha" / "log-audit.txt").read_text()
    # The worksheet read the invoice the run transcribed: one claim form, no
    # ledger, so the total is stated as not derivable rather than invented.
    chart = json.loads((sd / "billing_chart.json").read_text())["rows"][0]
    assert chart["basis"].startswith("NOT DERIVABLE") and "1 claim form(s)" in chart["basis"]
    # The ported $0 stages ran for real: listing, pull, extract, the email index.
    assert json.loads((sd / "manifest.json").read_text())["count"] == 3
    pulled = {r["id"] for r in map(json.loads, (sd / "raw_manifest.jsonl").read_text().splitlines()) if r["ok"]}
    assert pulled == {"f1", "f2", "f9"}
    extracted = {r["name"]: r for r in map(json.loads, (sd / "extracted.jsonl").read_text().splitlines())}
    assert extracted["f1"]["pages"] == 7 and extracted["f1"]["chars"] > 5600 and extracted["f1"]["empty_pages"] == [1]
    assert json.loads((sd / "msg_attachments.json").read_text())["comparable"] is True
    assert (sd / "runs" / "alpha" / "log-download.txt").read_text().startswith("example-matter: 3 targets")
    # billing_extract opened the pulled invoice by the path decide_billing_docs
    # authored off the manifest row (a row without one KeyErrored the paid
    # stage on the seat, 2026-09-04) and counted its pages by rendering it.
    billing = json.loads((sd / "billing_docs.json").read_text())["docs"]
    assert [(b["id"], b["name"]) for b in billing] == [("f2", "Example Clinic invoice")]
    assert Path(billing[0]["path"]).is_file() and "pages" not in billing[0]
    extract = json.loads((sd / "billing_extract.jsonl").read_text().splitlines()[-1])
    assert (
        extract["file"] == "Example Clinic invoice"
        and extract["pages"] == 1
        and extract["chunks"][0]["doc_type"] == "MEDICAL_BILL"
    )
    assert "  1pp  Example Clinic invoice" in (sd / "runs" / "alpha" / "log-billing_extract.txt").read_text()
    # The scanned-page classifier reached its controls gate with a real target
    # (the record's blank cover sheet) and read the falsifier from the INSTALL
    # root, not the job's data_root: two authored controls plus the run's own
    # record control, all answered, all held.
    scanned_log = (sd / "runs" / "alpha" / "log-classify_scanned.txt").read_text()
    assert "1 scanned page(s) to classify, +3 control(s)" in scanned_log
    assert sorted(client.classified) == ["CTL0", "CTL1", "CTL2", "Ex1p1"]
    assert json.loads((sd / "nonrecord.json").read_text())["1"]["unknown"] == [1]
    assert json.loads((sd / "scanned_labels.json").read_text())["labels"]["Ex1p1"] == "RECORD"
    # The ICD tables resolved against the install root too: the descriptor in
    # the doc above came from there (nothing under data_root could supply it)
    # and the once-per-machine fetch was skipped as present, not attempted.
    icd_state = RunState.load_or_new(state_path(data_root, "example-matter", "alpha"), slug="x", unit="y").stage(
        "icd_tables"
    )
    assert icd_state.status == "skipped" and icd_state.note == "present"
    # The paid $0-in-this-run stages ran in-process too: no scans, the invoice
    # marked billing-only (compose skip from evidence), one unit.
    unit_rows = {r["id"]: r for r in json.loads((sd / "units" / "alpha.json").read_text())}
    assert (
        sorted(unit_rows) == ["f1", "f2"] and unit_rows["f2"]["compose"] is False and "compose" not in unit_rows["f1"]
    )
    assert json.loads((sd / "orphans.json").read_text())["orphans"][0]["reason"].startswith("engagement document")
    assert calls(data_root) == []  # nothing runs as a subprocess any more
    out = sd / "out" / "alpha"
    assert list(out.glob("Alpha Example - Medical Chronology *.docx"))
    manifest = json.loads((out / "upload_manifest.json").read_text())
    assert [m["name"][:9] for m in manifest] == [
        "Alpha Exa",
        "Alpha Exa",
        "Exhibit 1",
    ]  # chronology, worksheet, exhibit and all(m["sha256"] for m in manifest)
    assert (out / "img" / "timeline.png").is_file()
    st = RunState.load_or_new(state_path(data_root, "example-matter", "alpha"), slug="x", unit="y")
    assert st.outcome == "delivered"
    for name in (
        "list_matter",
        "download",
        "extract_after_fold",
        "vision",
        "billing_extract",
        "build_units",
        "map",
        "repair_truncated",
        "assemble",
        "merge",
        "group",
        "filter",
        "exhibits",
        "condense",
        "summarize",
        "build_doc",
        "classify_nonrecord",
        "strip_apply",
        "coverage_gate",
        "billing_chart",
        "billing_docx",
        "audit",
        "manifest",
    ):
        assert st.is_done(name), name
    assert doc.startswith("Alpha Example - Medical Chronology") and "## Records Reviewed and Limitations" in doc
    assert "This chronology was prepared from 3 documents" in doc
    assert (sd / "runs" / "alpha" / "entries_condensed.md").is_file()
    assert (sd / "runs" / "alpha" / "map-01.md").read_text() == REAL_MAP
    assert (sd / "runs" / "alpha" / "merged.md").read_text() == ""
    assert o.dollars > 0  # the compose, classify and audit calls were ledgered and priced
    assert st.pipeline_sha.startswith("medchron-")


def test_resume_skips_done_stages_after_a_kill(
    job_dir: Path, data_root: Path, firm_config_path: Path, pricing_path: Path, fake_pipeline: Path
) -> None:
    seed_folders(data_root, ["MEDICAL"])
    # a crash mid-run: the render stage raises once (a broken chronology file)
    sd = data_root / "example-matter"
    outs = _driver(job_dir, firm_config_path, pricing_path, start=None, break_at="render").run()
    assert outs[0].outcome == "failed" and outs[0].stage == "render"
    before = len(calls(data_root))
    st = RunState.load_or_new(state_path(data_root, "example-matter", "alpha"), slug="x", unit="y")
    st.outcome = None
    st.save()
    _driver(job_dir, firm_config_path, pricing_path).run()
    assert calls(data_root)[before:] == []
    st = RunState.load_or_new(state_path(data_root, "example-matter", "alpha"), slug="x", unit="y")
    assert st.outcome == "delivered"
    assert st.stage("download").attempts == 1  # the pull was not repeated
    assert st.stage("build_units").attempts == 1 and st.stage("render").attempts == 2


def test_cap_refuses_before_the_first_paid_stage(
    job_dir: Path, data_root: Path, firm_config_path: Path, pricing_path: Path, fake_pipeline: Path
) -> None:
    seed_folders(data_root, ["MEDICAL"])
    write_ledger(
        data_root, "alpha", [{"stage": "compose", "model": "claude-opus-5", "in": 40_000_000, "out": 0}]
    )  # $200 already
    outs = _driver(job_dir, firm_config_path, pricing_path).run()
    o = outs[0]
    # 2026-09-09: a limit is a HOLD, not a refusal, and its reason names the
    # setting and carries no figure (the seat's content gates refuse a
    # drafted dollar amount, so a reason with one cannot be relayed at all).
    assert o.outcome == "held" and o.stage == "vision"
    assert o.reason.startswith("per_job_cap_usd: ") and "USD" not in o.reason and "$" not in o.reason
    assert "vision_scan.py" not in [c["script"] for c in calls(data_root)]


def test_exit_code_map_yields_the_vocabulary(
    job_dir: Path, data_root: Path, firm_config_path: Path, pricing_path: Path, fake_pipeline: Path
) -> None:
    seed_folders(data_root, ["MEDICAL"])
    outs = _driver(job_dir, firm_config_path, pricing_path, break_at="build_units").run()
    assert outs[0].outcome == "failed" and outs[0].stage == "build_units" and "boom" in outs[0].reason


def test_unknown_model_in_ledger_refuses_the_run(
    job_dir: Path, data_root: Path, firm_config_path: Path, pricing_path: Path, fake_pipeline: Path
) -> None:
    seed_folders(data_root, ["MEDICAL"])
    write_ledger(data_root, "alpha", [{"stage": "compose", "model": "claude-mystery-7", "in": 10, "out": 0}])
    with pytest.raises(budget_mod.BudgetError, match="refusing to price it at zero"):
        _driver(job_dir, firm_config_path, pricing_path).run()


def test_job_cap_overrides_the_firm_default(
    tmp_path: Path, data_root: Path, firm_config_path: Path, pricing_path: Path, fake_pipeline: Path
) -> None:
    jd = tmp_path / "job"
    jd.mkdir()
    (jd / "job.yaml").write_text(job_yaml(data_root, cap=1.0))
    seed_folders(data_root, ["MEDICAL"])
    write_ledger(
        data_root, "alpha", [{"stage": "compose", "model": "claude-sonnet-5", "in": 1_000_000, "out": 0}]
    )  # $2
    outs = _driver(jd, firm_config_path, pricing_path).run()
    assert outs[0].outcome == "held" and outs[0].reason.startswith("per_job_cap_usd: ")
    assert yaml.safe_load((jd / "job.yaml").read_text())["cap_usd"] == 1.0


# ---- the routine-11 limits, at the driver (2026-09-09) -----------------------
def _firm(tmp_path: Path, *, levers: dict | None = None, **budget) -> Path:
    """A firm config with the budget block overridden. Written per test so a
    boundary can be tested at values a real posture would never carry."""
    cfg = json.loads(json.dumps(FIRM_CONFIG))
    cfg["budget"].update(budget)
    if levers:
        cfg["levers"].update(levers)
    p = tmp_path / f"firm-{len(list(tmp_path.glob('firm-*.yaml')))}.yaml"
    p.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return p


def _seed_extracted(data_root: Path, *, pages: int, scanned: int = 0, chars: int = 0) -> None:
    """The extract stage's own output, seeded so a limit can be measured
    without paying for a pull. `start="vision"` then begins at the first paid
    stage with these counts already on disk."""
    rows = [{"id": "f1", "name": "f1", "pages": pages - scanned, "chars": chars}]
    if scanned:
        rows.append({"id": "f2", "name": "f2", "pages": scanned, "scan": True})
    (data_root / "example-matter" / "extracted.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )


def _limit_run(job_dir: Path, firm: Path, pricing_path: Path, **kw):
    return _driver(job_dir, firm, pricing_path, start="vision", **kw).run()[0]


def _job(tmp_path: Path, data_root: Path, name: str, **fields) -> Path:
    jd = tmp_path / name
    jd.mkdir()
    body = yaml.safe_load(job_yaml(data_root))
    body.update(fields)
    (jd / "job.yaml").write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    return jd


def test_a_large_matter_is_not_held_by_any_per_matter_page_line(
    job_dir: Path, data_root: Path, tmp_path: Path, pricing_path: Path, fake_pipeline: Path
) -> None:
    """Through the driver, not just the Limits object: a firm config carries no
    per-matter page ceiling any more, so a big matter reaches the paid stages.

    Falsifier: put a per-matter page gate back into the firm schema and this
    run holds at vision instead of proceeding.
    """
    firm = _firm(tmp_path)
    _seed_extracted(data_root, pages=12_000)
    assert _limit_run(job_dir, firm, pricing_path).outcome != "held"


def test_the_month_page_allowance_proceeds_at_the_remainder_and_holds_one_page_over(
    tmp_path: Path, data_root: Path, pricing_path: Path, fake_pipeline: Path
) -> None:
    firm = _firm(tmp_path)
    _seed_extracted(data_root, pages=10)
    for remaining in (10, 9):
        jd = _job(
            tmp_path,
            data_root,
            f"job-allow-{remaining}",
            allowance_remaining_pages=remaining,
            month_cents_used=0,
            allowance_month="2026-09",
        )
        o = _limit_run(jd, firm, pricing_path)
        if remaining == 9:
            assert o.outcome == "held" and o.reason.startswith("chronology_package_page_allowance_per_month: ")
            assert "9 pages remain in 2026-09's allowance" in o.reason
        else:
            assert o.outcome != "held", o.reason


def test_the_month_budget_holds_before_the_first_paid_stage_from_the_projection(
    tmp_path: Path, data_root: Path, pricing_path: Path, fake_pipeline: Path
) -> None:
    """The month's spend to date plus THIS run's projection. Neither alone
    crosses the budget; together they do, and the run never starts."""
    firm = _firm(tmp_path, monthly_budget_usd=10.0, usd_per_scanned_page=1.0)
    _seed_extracted(data_root, pages=8, scanned=8)  # projects 8.00
    jd = _job(tmp_path, data_root, "job-budget", month_cents_used=300, allowance_month="2026-09")
    o = _limit_run(jd, firm, pricing_path)
    assert o.outcome == "held" and o.reason.startswith("monthly_budget_usd: ")
    assert "was not started before vision" in o.reason and "$" not in o.reason
    assert calls(data_root) == []


def test_the_envelope_can_lower_the_firm_cap_but_never_raise_it(
    tmp_path: Path, data_root: Path, firm_config_path: Path, pricing_path: Path, fake_pipeline: Path
) -> None:
    """The envelope used to win outright, which made the firm's cap advisory:
    anything that could author an envelope could author its way past it."""
    jd = tmp_path / "job-999"
    jd.mkdir()
    (jd / "job.yaml").write_text(job_yaml(data_root, cap=999.0))
    seed_folders(data_root, ["MEDICAL"])
    write_ledger(data_root, "alpha", [{"stage": "compose", "model": "claude-opus-5", "in": 40_000_000, "out": 0}])
    o = _driver(jd, firm_config_path, pricing_path).run()[0]  # firm cap 150, ledger 200
    assert o.outcome == "held" and o.reason.startswith("per_job_cap_usd: ")


# ---- the doorway hook: a limit reached INSIDE a stage -------------------------
class _Meter:
    """A client whose every call costs the same measurable amount, so a trip
    can be read as a call count: 50,000 input tokens of claude-sonnet-5 at the
    test pricing table is exactly 0.10."""

    class _U:
        input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens = 50_000, 0, 0, 0

    def __init__(self) -> None:
        self.messages = self
        self.calls: list[dict] = []

    def create(self, **kw):
        self.calls.append(kw)
        return type(
            "M",
            (),
            {
                "content": [type("B", (), {"type": "text", "text": "ok"})()],
                "stop_reason": "end_turn",
                "usage": self._U(),
            },
        )()


def _spender(n: int = 20):
    """A stage that keeps calling the doorway. Without the hook it spends the
    whole way through; with it, the run stops at the first call past the line."""

    def run(sr) -> int:
        for _ in range(n):
            sr.doorway.call(
                "vision", model="claude-sonnet-5", messages=[{"role": "user", "content": "x"}], max_tokens=16
            )
        return 0

    return run


def _in_stage(tmp_path: Path, data_root: Path, pricing_path: Path, firm: Path, name: str, **job_fields):
    jd = _job(tmp_path, data_root, name, **job_fields)
    _seed_extracted(data_root, pages=4)
    client = _Meter()
    d = _driver(jd, firm, pricing_path, start="vision", client=client)
    d._runner_override = {"vision": _spender()}
    return d.run()[0], client


def test_the_cap_stops_a_run_inside_a_stage_within_one_paid_call(
    tmp_path: Path, data_root: Path, pricing_path: Path, fake_pipeline: Path
) -> None:
    firm = _firm(tmp_path, per_job_cap_usd=0.25, usd_per_scanned_page=0.0001, usd_per_million_chars=0.0)
    o, client = _in_stage(tmp_path, data_root, pricing_path, firm, "job-cap-instage")
    assert o.outcome == "held" and o.stage == "vision"
    assert o.reason.startswith("per_job_cap_usd: ") and "during vision; the run stopped" in o.reason
    # Three calls at 0.10 = 0.30. The fourth is the one the hook refuses, so
    # the overshoot is bounded to ONE call past the line, not one stage.
    assert len(client.calls) == 3
    assert 0.29 < o.dollars < 0.31


def test_the_month_budget_stops_a_run_inside_a_stage(
    tmp_path: Path, data_root: Path, pricing_path: Path, fake_pipeline: Path
) -> None:
    firm = _firm(tmp_path, monthly_budget_usd=0.50, usd_per_scanned_page=0.0001, usd_per_million_chars=0.0)
    o, client = _in_stage(
        tmp_path, data_root, pricing_path, firm, "job-budget-instage", month_cents_used=30, allowance_month="2026-09"
    )
    assert o.outcome == "held" and o.reason.startswith("monthly_budget_usd: ")
    assert len(client.calls) == 2  # 0.30 carried plus 0.20 spent reaches the line
    assert "$" not in o.reason


def test_seat_mode_refuses_a_job_that_carries_no_month_state(
    job_dir: Path,
    data_root: Path,
    firm_config_path: Path,
    pricing_path: Path,
    fake_pipeline: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unmetered is not a state a client seat may run in. On a laptop the same
    envelope is fine."""
    monkeypatch.setenv("MEDCHRON_SEAT", "client")
    with pytest.raises(driver_mod.DriverError, match="refusing to run unmetered"):
        _driver(job_dir, firm_config_path, pricing_path)
    monkeypatch.delenv("MEDCHRON_SEAT")
    _driver(job_dir, firm_config_path, pricing_path)


def test_a_dry_run_measures_the_limits_and_spends_nothing(tmp_path: Path, data_root: Path, pricing_path: Path) -> None:
    firm = _firm(tmp_path, per_job_cap_usd=0.01)
    jd = _job(tmp_path, data_root, "job-dry")
    seed_folders(data_root, ["MEDICAL"])
    _seed_extracted(data_root, pages=40, scanned=40)
    o = _driver(jd, firm, pricing_path, dry_run=True, start="vision").run()[0]
    assert o.outcome == "dry_run"
    assert any(n.startswith("WOULD HOLD at vision: per_job_cap_usd: ") for n in o.notes)
    assert calls(data_root) == []


# ---- batch mode: the limit binds before the submission -------------------------
class _BatchClient:
    """Counts batch submissions. Nothing here should ever be reached by a batch
    the limits refuse, which is the whole property."""

    def __init__(self) -> None:
        self.messages = self
        self.batches = self
        self.submitted: list[Any] = []

    def create(self, **kw):
        self.submitted.append(kw)
        raise RuntimeError("stub: a batch reached the SDK")


def _batcher(n: int):
    """A stage that hands the doorway one batch of n page-shaped items."""

    def run(sr) -> int:
        items = [
            llm_mod.Item(
                custom_id=f"p{i}",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "x"}},
                            {"type": "text", "text": "transcribe this page"},
                        ],
                    }
                ],
            )
            for i in range(n)
        ]
        sr.doorway.batch_call(
            "vision", items, lambda *a: None, model="claude-sonnet-5", max_tokens=16, batch_dir=sr.slug_dir / "batch"
        )
        return 0

    return run


def test_a_batch_that_would_cross_the_cap_holds_before_it_is_submitted(
    tmp_path: Path, data_root: Path, pricing_path: Path, fake_pipeline: Path
) -> None:
    """Batch mode has no per-call seam: nothing checks between a batch's items
    and the whole thing is billed. So the limits see the batch's PROJECTED cost
    before submission, and an overshoot is bounded to one batch. Without this a
    firm that opted into batching would silently lose the per-call bound.
    """
    firm = _firm(
        tmp_path,
        per_job_cap_usd=1.0,
        usd_per_scanned_page=0.02,
        usd_per_million_chars=0.0,
        levers={"batch_stages": ["vision"]},
    )
    jd = _job(tmp_path, data_root, "job-batch-over")
    _seed_extracted(data_root, pages=4)
    client = _BatchClient()
    d = _driver(jd, firm, pricing_path, start="vision", client=client)
    d._runner_override = {"vision": _batcher(80)}  # 80 pages x 0.02 = 1.60, over a 1.00 cap
    o = d.run()[0]
    assert o.outcome == "held" and o.stage == "vision"
    assert o.reason.startswith("per_job_cap_usd: ") and "the batch was not submitted" in o.reason
    assert "$" not in o.reason and "USD" not in o.reason
    assert client.submitted == []  # nothing was ever sent


def test_a_batch_inside_the_cap_is_submitted(
    tmp_path: Path, data_root: Path, pricing_path: Path, fake_pipeline: Path
) -> None:
    """The pass direction: the same wiring must not refuse a batch that fits,
    or the check above could pass by refusing everything."""
    firm = _firm(
        tmp_path,
        per_job_cap_usd=1.0,
        usd_per_scanned_page=0.02,
        usd_per_million_chars=0.0,
        levers={"batch_stages": ["vision"]},
    )
    jd = _job(tmp_path, data_root, "job-batch-under")
    _seed_extracted(data_root, pages=4)
    client = _BatchClient()
    d = _driver(jd, firm, pricing_path, start="vision", client=client)
    d._runner_override = {"vision": _batcher(40)}  # 40 pages x 0.02 = 0.80, inside the cap
    o = d.run()[0]
    # The batch reached the submission, which is the property under test. The
    # stub raises there rather than faking a whole batch lifecycle, so the run
    # ends `failed` -- what matters is that no LIMIT refused it.
    assert len(client.submitted) == 1
    assert o.outcome != "held", o.reason
