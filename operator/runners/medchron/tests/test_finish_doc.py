"""build_doc, the two classifiers, the strip, the coverage gate, the billing
chart and the worksheet, in-process. Real PDFs; a scripted client for the
one paid stage."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import yaml

from medchron import config as config_mod, job as job_mod
from medchron.stages import billing_chart, billing_docx, build_doc, classify, coverage, strip
from medchron.stages.base import StageRun
from medchron_testkit import FIRM_CONFIG, FakeSeat, make_pdf

PROSE = (
    "Patient seen on 01/02/2026 for neck pain after the collision. Blood pressure 120/80. "
    "Ibuprofen 400 mg prescribed. Riverside Imaging ordered an MRI. "
) * 4
ORDER_PAGE = (
    "Order #: 12345\nSubject Name: Alpha Example\nRequested Date Range: 2025-2026\nvendor@example-retrieval.com"
)
INDEX_PAGE = "WORD INDEX\nThis list is computer generated and may contain errors.\nabdomen 4\nankle 9\n" * 3


class Usage:
    input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens = 100, 20, 0, 0


class Scripted:
    def __init__(self, reply):
        self.calls: list[dict] = []
        self._reply = reply
        self.messages = NS(create=self._create, stream=None, batches=None)

    def _create(self, **params):
        self.calls.append(params)
        return self._reply(params, len(self.calls))


@pytest.fixture
def firm(tmp_path: Path) -> Path:
    cfg = json.loads(json.dumps(FIRM_CONFIG))
    cfg["nonrecord"]["page_classes"] = [
        {"name": "INDEX", "patterns": ["this list is computer generated"]},
        {"name": "ORDER", "patterns": [r"order\s*#\s*:.{0,600}example-retrieval\.com"]},
    ]
    cfg["providers"]["aliases"] = [{"match": r"example clinic|ex clinic", "label": "Example Clinic"}]
    cfg["billing"]["provider_match"] = {"Example Clinic": [r"example clinic", r"ex clinic"]}
    p = tmp_path / "firm.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return p


def _sr(job_dir: Path, firm: Path, data_root: Path, client=None, log: list[str] | None = None) -> StageRun:
    job = job_mod.load(job_dir)
    cfg = config_mod.load(str(firm))
    lines = log if log is not None else []
    return StageRun(
        job=job,
        cfg=cfg,
        unit=job.units[0],
        slug_dir=data_root / "example-matter",
        decided={},
        log=lines.append,
        seat_factory=lambda: FakeSeat([], [], {}),
        client_factory=lambda: client,
    )


# ---- build_doc ---------------------------------------------------------------------
ENTRIES = (
    "Prior Medical History\n\nChronic neck pain before the incident. (Exhibit 1 - p. 1)\n\n"
    "01/02/2026\nExample Clinic (Jane Doe, MD) | Patient Complaints & Limitations\n\n"
    "Neck pain since the Subject Incident. (Exhibit 1 - p. 2)\n\n"
    "01/09/2026\nEx Clinic | Medical Diagnoses\n\nCervical strain. (Exhibit 1 - p. 3)\n"
)
MAP_INDEX = (
    "## ENTRIES\nnone in this chunk\n\n## INDEX\n2026-01-02 | Example Clinic | S13.4XXA, M54.2 (as recorded) | clinic note.pdf\n"
    "2026-01-09 | Ex Clinic | S523.3XXA | clinic note.pdf\n2027-01-01 | Other | Z00.0 | other.pdf\n\n## FILES-SEEN\n"
)


def _icd_tables(data_root: Path) -> None:
    d = data_root / "controls" / "icd"
    d.mkdir(parents=True)
    line10 = "{:<5} {:<7} {} {:<60} {}".format
    (d / "icd10cm_order.txt").write_text(
        line10(
            "00001",
            "S134XXA",
            "1",
            "Sprain of lig of cervical spine, init",
            "Sprain of ligaments of cervical spine, initial encounter",
        )
        + "\n"
        + line10("00002", "M542", "1", "Cervicalgia", "Cervicalgia")
        + "\n"
    )
    (d / "CMS32_DESC_LONG_DX.txt").write_text("7242 Lumbago\n")
    (d / "VERSION.json").write_text(json.dumps({"icd10cm": {"label": "test"}}))


def test_build_doc_composes_sections_from_the_entries_and_tables_from_code(
    job_dir: Path, firm: Path, data_root: Path
) -> None:
    sr = _sr(job_dir, firm, data_root)
    rd = sr.slug_dir / "runs" / "alpha"
    rd.mkdir(parents=True)
    (rd / "entries_scoped_final.md").write_text(ENTRIES)
    (rd / "map-01.md").write_text(MAP_INDEX)
    (rd / "preincident_note.txt").write_text("[NTD: 1 additional pre-incident encounter was reviewed.]")
    out = sr.slug_dir / "out" / "alpha"
    out.mkdir(parents=True)
    (out / "page_map.json").write_text(
        json.dumps([{"exhibit": 1, "title": "Exhibit 1 - Example Clinic - 01-02-2026 (Medical Records)", "files": []}])
    )
    _icd_tables(sr.job.data_root)
    assert build_doc.run(sr) == 0
    doc = (rd / "final-chronology.md").read_text()
    assert doc.startswith("Alpha Example - Medical Chronology\n")
    assert (
        "| Example Clinic | 01/02/2026 - 01/09/2026 | 2 | Exhibit 1 - p. 2 |" in doc
    )  # facility level, both spellings
    assert (
        "| S13.4XXA | Sprain of ligaments of cervical spine, initial encounter | 01/02/2026 | Exhibit 1 - p. 2 |" in doc
    )
    assert (
        "| M54.2 | Cervicalgia |" in doc and "S523.3XXA" not in doc and "Z00.0" not in doc
    )  # bad shape and unkept date dropped
    assert "## Medical Chronology\n\nPrior Medical History\n\nChronic neck pain" in doc and "[NTD: 1 additional" in doc
    assert "| 1 | Example Clinic - 01-02-2026 (Medical Records) |" in doc
    assert "## Records Reviewed and Limitations" in doc and "prepared from 0 documents in the matter file" in doc


def test_build_doc_refuses_unrecognised_preamble(job_dir: Path, firm: Path, data_root: Path) -> None:
    sr = _sr(job_dir, firm, data_root)
    rd = sr.slug_dir / "runs" / "alpha"
    rd.mkdir(parents=True)
    (rd / "entries_scoped_final.md").write_text(
        "Some narrative the model wrote first.\n\n" + ENTRIES.split("\n\n", 2)[2]
    )
    (sr.slug_dir / "out" / "alpha").mkdir(parents=True)
    assert build_doc.run(sr) == 1


# ---- the classifiers and the strip -----------------------------------------------------------
def _exhibit(sr: StageRun, pages: list[str]) -> Path:
    out = sr.slug_dir / "out" / "alpha"
    out.mkdir(parents=True, exist_ok=True)
    title = "Exhibit 1 - Example Clinic - 01-02-2026 (Medical Records)"
    pdf = out / f"{title}.pdf"
    pdf.write_bytes(make_pdf(pages))
    (out / "page_map.json").write_text(
        json.dumps(
            [
                {
                    "exhibit": 1,
                    "title": title,
                    "total_pages": len(pages),
                    "files": [{"file": "clinic note.pdf", "old_exhibit": 1, "start_page": 1, "pages": len(pages)}],
                }
            ]
        )
    )
    return pdf


def _doc(sr: StageRun, text: str) -> Path:
    rd = sr.slug_dir / "runs" / "alpha"
    rd.mkdir(parents=True, exist_ok=True)
    p = rd / "final-chronology.md"
    p.write_text(text)
    return p


DOC_3 = (
    "Alpha Example - Medical Chronology\n\n## Treatment Timeline\n\n| Medical Provider | Treatment Period | Visits | Reference |\n"
    "| Example Clinic | 01/02/2026 | 1 | Exhibit 1 - p. 3 |\n\n## Diagnostic Highlights\n\n| ICD Code | Description | First Diagnosed | Reference |\n\n"
    "## Medical Chronology\n\n01/02/2026\nExample Clinic | Medical Diagnoses\n\nCervical strain noted. (Exhibit 1 - p. 3)\n\n"
    "## Exhibit List\n\n| Exhibit No. | Description |\n"
)


def test_classify_nonrecord_fingerprints_classes_and_reports_collisions(
    job_dir: Path, firm: Path, data_root: Path
) -> None:
    sr = _sr(job_dir, firm, data_root)
    _exhibit(sr, [ORDER_PAGE, INDEX_PAGE, PROSE, ""])
    _doc(sr, DOC_3)
    assert classify.run_nonrecord(sr) == 0
    nr = json.loads((sr.slug_dir / "nonrecord.json").read_text())["1"]
    assert nr["drop_pages"] == [1, 2] and nr["unknown"] == [4] and nr["cited_collision"] == []
    assert [b["class"] for b in nr["blocks"]] == ["ORDER", "INDEX"]
    _doc(sr, DOC_3.replace("(Exhibit 1 - p. 3)", "(Exhibit 1 - p. 2)"))
    assert classify.run_nonrecord(sr) == 0
    assert json.loads((sr.slug_dir / "nonrecord.json").read_text())["1"]["cited_collision"] == [2]


def test_classify_scanned_labels_head_pages_and_needs_its_controls(job_dir: Path, firm: Path, data_root: Path) -> None:
    def reply(p, n):
        labels = [b["text"].split()[1].rstrip(":") for b in p["messages"][0]["content"] if b["type"] == "text"]
        answer = {"Ex1p4": "AUTH", "CTL0": "ORDER", "CTL1": "INDEX", "CTL2": "RECORD"}
        return NS(
            content=[NS(type="text", text="\n".join(f"{lb} = {answer.get(lb, 'RECORD')}" for lb in labels))],
            stop_reason="end_turn",
            usage=Usage(),
        )

    client = Scripted(reply)
    sr = _sr(job_dir, firm, data_root, client)
    _exhibit(
        sr, [PROSE, PROSE, PROSE, ""]
    )  # page 4 is blank: unclassifiable by text, within HEAD_PAGES? no (start 1: pages 1-3)
    _doc(sr, DOC_3)
    (sr.slug_dir / "nonrecord.json").write_text(
        json.dumps({"1": {"pages": 4, "blocks": [], "drop_pages": [], "unknown": [1, 4], "cited_collision": []}})
    )
    assert classify.run_scanned(sr) == 1  # controls not authored
    ctl = sr.job.data_root / "controls"
    ctl.mkdir(parents=True)
    (ctl / "control-order.pdf").write_bytes(make_pdf([ORDER_PAGE]))
    (ctl / "control-index.pdf").write_bytes(make_pdf([INDEX_PAGE]))
    (ctl / "controls.json").write_text(
        json.dumps(
            [
                {"pdf": "controls/control-order.pdf", "page": 1, "label": "ORDER"},
                {"pdf": "controls/control-index.pdf", "page": 1, "label": "INDEX"},
            ]
        )
    )
    (sr.slug_dir / "record_control.json").write_text(json.dumps({"exhibit": 1, "page": 2}))
    assert classify.run_scanned(sr) == 0
    labels = json.loads((sr.slug_dir / "scanned_labels.json").read_text())
    assert labels["controls_ok"] is True and labels["labels"]["Ex1p1"] == "RECORD" and labels["nonrecord"] == {}
    sent = [b["text"] for b in client.calls[0]["messages"][0]["content"] if b["type"] == "text"]
    assert sent == ["page Ex1p1:", "page CTL0:", "page CTL1:", "page CTL2:"]  # p.4 is outside the file head


def test_strip_falsifies_dry_runs_and_applies_with_a_verified_remap(job_dir: Path, firm: Path, data_root: Path) -> None:
    sr = _sr(job_dir, firm, data_root)
    pdf = _exhibit(sr, [ORDER_PAGE, INDEX_PAGE, PROSE, PROSE + " second"])
    _doc(sr, DOC_3.replace("(Exhibit 1 - p. 3)", "(Exhibit 1 - p. 3-4)"))
    assert classify.run_nonrecord(sr) == 0
    assert strip.run_falsify(sr) == 0 and not pdf.with_name(pdf.name + ".orig").exists()
    assert strip.run_dry(sr) == 0 and not (sr.slug_dir / "strip_result.json").exists()
    assert strip.run_apply(sr) == 0
    doc = (sr.slug_dir / "runs" / "alpha" / "final-chronology.md").read_text()
    assert "(Exhibit 1 - p. 1-2)" in doc and "| Exhibit 1 - p. 1 |" in doc  # body and table cell both remapped
    result = json.loads((sr.slug_dir / "strip_result.json").read_text())
    assert result["drops"] == {"1": [1, 2]} and result["new_page_counts"] == {"1": 2}
    assert Path(str(pdf) + ".orig").exists()
    import pymupdf

    assert len(pymupdf.open(str(pdf))) == 2
    assert strip.run_apply(sr) == 1  # never twice


def test_strip_refuses_when_every_cited_page_is_dropped(job_dir: Path, firm: Path, data_root: Path) -> None:
    sr = _sr(job_dir, firm, data_root)
    _exhibit(sr, [ORDER_PAGE, PROSE])
    _doc(
        sr,
        DOC_3.replace("(Exhibit 1 - p. 3)", "(Exhibit 1 - p. 1)").replace(
            "| Exhibit 1 - p. 3 |", "| Exhibit 1 - p. 1 |"
        ),
    )
    assert classify.run_nonrecord(sr) == 0
    assert strip.run_dry(sr) == 1


# ---- coverage gate --------------------------------------------------------------------------
def test_coverage_gate_accounts_for_every_pulled_file(job_dir: Path, firm: Path, data_root: Path) -> None:
    sr = _sr(job_dir, firm, data_root)
    d = sr.slug_dir
    (d / "units").mkdir(parents=True)
    (d / "units" / "alpha.json").write_text(
        json.dumps(
            [
                {"id": "a", "name": "clinic note", "ext": ".pdf"},
                {"id": "b", "name": "Retainer signed", "ext": ".pdf"},
                {"id": "c", "name": "mystery scan", "ext": ".pdf"},
            ]
        )
    )
    (d / "raw_manifest.jsonl").write_text(
        "".join(
            json.dumps(r) + "\n"
            for r in [
                {"id": "a", "name": "clinic note", "ext": ".pdf", "ok": True},
                {"id": "b", "name": "Retainer signed", "ext": ".pdf", "ok": True},
                {"id": "c", "name": "mystery scan", "ext": ".pdf", "ok": True},
                {"id": "d", "name": "stray photo", "ext": ".png", "ok": True},
            ]
        )
    )
    rd = d / "runs" / "alpha"
    rd.mkdir(parents=True)
    (rd / "entries_final.md").write_text(
        "01/02/2026\nExample Clinic | Medical Diagnoses\n\nStrain. (Exhibit 1 - p. 1)\n"
    )
    (d / "out" / "alpha").mkdir(parents=True)
    (d / "out" / "alpha" / "page_map.json").write_text(
        json.dumps([{"exhibit": 1, "files": [{"file": "clinic note.pdf"}]}])
    )
    log: list[str] = []
    sr = _sr(job_dir, firm, data_root, log=log)
    assert coverage.run(sr) == 1  # the stray photo is owned by nobody
    assert any("COVERAGE GATE FAIL" in line and "1 pulled file" in line for line in log)
    (d / "orphans.json").write_text(
        json.dumps({"orphans": [{"name": "stray photo.png", "reason": "email signature graphic"}]})
    )
    log.clear()
    assert coverage.run(sr) == 1  # mystery scan is in the unit, uncited, unexplained
    assert json.loads((rd / "coverage_unexplained.json").read_text()) == ["mystery scan.pdf"]
    (d / "billing_docs.json").write_text(json.dumps({"docs": [{"name": "mystery scan", "path": "x", "pages": 1}]}))
    log.clear()
    assert coverage.run(sr) == 0
    assert any("engagement document" in line for line in log) and any("GATE PASS" in line for line in log)


# ---- billing chart and worksheet -------------------------------------------------------------------
def test_money_parses_printed_shapes_and_flags_lost_decimals() -> None:
    sus: list = []
    assert billing_chart.money("$1,800.00", sus, 500_000) == 1800.0
    assert billing_chart.money("112,547,03", sus, 500_000) == 112547.03  # decimal comma
    assert billing_chart.money("$4700 I 00", sus, 500_000) == 4700.0  # CMS-1500 adjacent boxes
    assert (
        billing_chart.money("235000", sus, 500_000) == 235000.0
        and billing_chart.money("$1,?00.00", sus, 500_000) is None
    )
    assert billing_chart.money("1,000,000.00", sus, 500_000) == 1000000.0 and sus == [("1,000,000.00", 1000000.0)]
    assert billing_chart.total_rank("28. TOTAL CHARGE") == 0 and billing_chart.total_rank("Overall - Total") == 3


def test_billing_chart_prefers_a_ledger_total_and_reports_the_rest(job_dir: Path, firm: Path, data_root: Path) -> None:
    sr = _sr(job_dir, firm, data_root)
    _doc(sr, DOC_3)
    rows = [
        {
            "file": "Example Clinic ledger",
            "pages": 2,
            "failures": [],
            "chunks": [
                {
                    "doc_type": "LEDGER",
                    "provider": "Example Clinic",
                    "patient": "Alpha Example",
                    "date_first": "01/02/2026",
                    "date_last": "01/09/2026",
                    "printed_totals": [
                        {"label": "Total Charges", "amount": "$1,800.00", "page": 1},
                        {"label": "Insurance adjustment", "amount": "-$300.00", "page": 2},
                    ],
                },
                {
                    "provider": "",
                    "printed_totals": [{"label": "Overall - Total", "amount": "$2,100.00", "page": 2}],
                    "doc_type": "LEDGER",
                },
            ],
        },
        {
            "file": "claim form",
            "pages": 1,
            "failures": [],
            "chunks": [
                {
                    "doc_type": "MEDICAL_BILL",
                    "provider": "Ex Clinic",
                    "printed_totals": [{"label": "28. TOTAL CHARGE", "amount": "$465.00", "page": 1}],
                }
            ],
        },
        {
            "file": "recovery vendor",
            "pages": 1,
            "failures": [],
            "chunks": [
                {
                    "doc_type": "LIEN_SUBROGATION",
                    "provider": "Example Health Plan",
                    "printed_totals": [{"label": "claim", "amount": "$900.00", "page": 1}],
                }
            ],
        },
        {
            "file": "unknown bill",
            "pages": 1,
            "failures": [],
            "chunks": [
                {
                    "doc_type": "MEDICAL_BILL",
                    "provider": "Somebody Else Imaging",
                    "printed_totals": [{"label": "Balance", "amount": "$50.00", "page": 1}],
                }
            ],
        },
    ]
    (sr.slug_dir / "billing_extract.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert billing_chart.run(sr) == 0
    chart = json.loads((sr.slug_dir / "billing_chart.json").read_text())
    row = chart["rows"][0]
    assert row["provider"] == "Example Clinic" and row["total"] == 2100.0 and row["basis"] == "Example Clinic ledger"
    assert row["adjustments"] == [
        {"amount": 300.0, "label": "Insurance adjustment", "doc": "Example Clinic ledger", "page": 2}
    ]
    assert chart["grand_total"] == 2100.0 and chart["inherited_attribution"] == [
        ["Example Clinic ledger", None, "Example Clinic"]
    ]
    assert (
        chart["unmatched"] == {"Somebody Else Imaging": [50.0]} and chart["subrogation"][0][1] == "Example Health Plan"
    )
    assert billing_docx.run(sr) == 0
    out = list((sr.slug_dir / "out" / "alpha").glob("* - Medical Billing Worksheet *.docx"))
    assert len(out) == 1 and out[0].name.startswith("Alpha Example - Medical Billing Worksheet ")
    from docx import Document

    text = "\n".join(p.text for p in Document(str(out[0])).paragraphs)
    assert "Alpha Example - Medical Billing Summary" in text and "Health-Plan and Subrogation Claims" in text


def test_billing_docx_refuses_a_suspect_amount(job_dir: Path, firm: Path, data_root: Path) -> None:
    sr = _sr(job_dir, firm, data_root)
    sr.slug_dir.mkdir(parents=True, exist_ok=True)
    (sr.slug_dir / "billing_chart.json").write_text(
        json.dumps({"rows": [], "suspect_amounts": [["1,000,000", 1000000.0]]})
    )
    assert billing_docx.run(sr) == 1
