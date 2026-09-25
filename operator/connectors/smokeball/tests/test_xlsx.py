"""Excel read and write (2026-09-25).

Before this change an .xlsx took the DOCX road in ``extract`` (both carry
``[Content_Types].xml``), failed to parse, and poisoned the drafting record
check for every draft on its matter; and the Operator could not build a
workbook at all. These tests pin both halves, and each one names the behaviour
that would make it fail."""

from __future__ import annotations

import datetime as dt
import hashlib
import io

import pytest

openpyxl = pytest.importorskip("openpyxl")

from smokeball_connector import server, vendor_invoice, workbook_tools  # noqa: E402
from smokeball_connector.extract import (  # noqa: E402
    METHOD_XLSX,
    METHOD_XLSX_OPERATOR,
    UnsupportedDocumentError,
    extract_text_ex,
)
from smokeball_connector.xlsx_io import (  # noqa: E402
    NO_SAVED_VALUE,
    OLE_MAGIC,
    OPERATOR_CREATOR,
    WorkbookRefused,
    build_workbook,
    normalize_sheets,
    xlsx_text,
)


def _firm_workbook(*, formula_without_value: bool = False) -> bytes:
    """A workbook as a person at the firm would save it (creator not ours)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Advances"
    ws.append(["Date", "Description", "Amount"])
    ws.append([dt.date(2025, 2, 3), "Mileage", 412.50])
    ws.append([])  # an empty row is dropped
    ws.append([dt.date(2025, 3, 2), "Office supplies CK 1001", 1200])
    if formula_without_value:
        ws["C5"] = "=SUM(C2:C4)"
    costs = wb.create_sheet("Costs")
    costs.append(["Vendor", "Invoice", "Amount"])
    costs.append(["Acme Reporting", "INV-1001", 300])
    wb.properties.creator = "Front Desk"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---- reading ----------------------------------------------------------------


def test_a_workbook_extracts_every_sheet_as_rows() -> None:
    result = extract_text_ex(_firm_workbook(), file_name="Sample costs.xlsx", file_extension="xlsx")
    assert result.method == METHOD_XLSX
    assert result.text.splitlines() == [
        "## Sheet: Advances",
        "Date | Description | Amount",
        "2025-02-03 | Mileage | 412.5",
        "2025-03-02 | Office supplies CK 1001 | 1200",
        "## Sheet: Costs",
        "Vendor | Invoice | Amount",
        "Acme Reporting | INV-1001 | 300",
    ]


def test_an_xlsx_with_no_extension_hint_no_longer_takes_the_docx_road() -> None:
    """The regression: extension "" plus [Content_Types].xml used to route to
    python-docx and raise 'DOCX could not be parsed'."""
    result = extract_text_ex(_firm_workbook(), file_name="download", file_extension="")
    assert result.method == METHOD_XLSX
    assert "Acme Reporting | INV-1001 | 300" in result.text


def test_a_formula_the_file_never_computed_reads_as_a_gap_not_a_blank() -> None:
    text = xlsx_text(_firm_workbook(formula_without_value=True))
    assert text.splitlines()[4] == f" |  | {NO_SAVED_VALUE}"


def test_legacy_and_encrypted_office_files_are_refused_by_name() -> None:
    with pytest.raises(UnsupportedDocumentError, match="legacy Office file"):
        extract_text_ex(OLE_MAGIC + b"\x00" * 600, file_name="old.xls", file_extension="xls")


def test_a_powerpoint_is_refused_by_name_not_as_a_broken_docx() -> None:
    buf = io.BytesIO()
    import zipfile

    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("ppt/presentation.xml", "<p/>")
    with pytest.raises(UnsupportedDocumentError, match="PowerPoint"):
        extract_text_ex(buf.getvalue(), file_name="deck.pptx", file_extension="pptx")


def test_a_docx_still_takes_the_docx_road() -> None:
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    doc.add_paragraph("Dear Counsel,")
    buf = io.BytesIO()
    doc.save(buf)
    assert extract_text_ex(buf.getvalue(), file_name="letter.docx", file_extension="docx").method == "docx"


# ---- the drafting record check ---------------------------------------------


class _MatterClient:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    def get(self, path, **_):
        return {"value": [{"id": n, "name": n, "fileExtension": n.rsplit(".", 1)[-1]} for n in self.files]}

    def download_file(self, _matter_id, file_id):
        return {}, self.files[file_id]


def _operator_workbook() -> bytes:
    return build_workbook(
        normalize_sheets([{"name": "S", "columns": [{"header": "Amount", "format": "currency"}], "rows": [[5]]}])
    )


def test_record_check_reads_a_firm_workbook_and_skips_the_operators_own(monkeypatch) -> None:
    """Without the xlsx road the firm's workbook lands in ``unextractable`` and
    refuses the draft; without the creator exclusion the Operator's own figures
    would count as the firm's record."""
    fake = _MatterClient({"costs.xlsx": _firm_workbook(), "ours.xlsx": _operator_workbook()})
    monkeypatch.setattr(server, "_get_client", lambda: fake)
    sources, vision, unextractable = server._collect_matter_sources("m-1")
    assert [name for name, _ in sources] == ["costs.xlsx"]
    assert unextractable == []
    assert vision == []


def test_the_operators_workbook_is_readable_but_named_as_its_own() -> None:
    result = extract_text_ex(_operator_workbook(), file_name="ours.xlsx", file_extension="xlsx")
    assert result.method == METHOD_XLSX_OPERATOR
    assert "5" in result.text


# ---- attachments ------------------------------------------------------------


def test_an_emailed_workbook_is_readable(monkeypatch) -> None:
    blob = _firm_workbook()
    monkeypatch.setattr(vendor_invoice, "fetch_bytes", lambda _c, _u: blob)
    out = vendor_invoice.read_attachment(object(), "spool:tok", "costs.xlsx")
    assert out["readable"] is True
    assert out["method"] == METHOD_XLSX
    assert "Acme Reporting | INV-1001 | 300" in out["text"]


# ---- building ---------------------------------------------------------------

_SHEETS = [
    {
        "name": "Advances",
        "columns": [
            {"header": "Date", "format": "date"},
            {"header": "Description", "format": "text"},
            {"header": "Amount", "format": "currency"},
            {"header": "Count", "format": "integer"},
        ],
        "rows": [
            ["2025-02-03", "Mileage", "$412.50", 1],
            ["2025-03-02", '=HYPERLINK("http://x")', "1,200.00", "2"],
        ],
        "totals": ["Amount", "Count"],
    }
]


class _Recorder:
    def __init__(self) -> None:
        self.seen: dict = {}

    def add_file(self, matter_id, file_name, data, *, folder_id=None):
        self.seen = dict(matter_id=matter_id, file_name=file_name, data=data, folder_id=folder_id)
        return {"fileId": "f-9", "matterId": matter_id, "fileName": file_name, "uploaded": True}


def test_add_workbook_files_typed_cells_and_code_computed_totals(monkeypatch) -> None:
    rec = _Recorder()
    monkeypatch.setattr(workbook_tools, "_client", lambda: rec)
    out = workbook_tools.add_workbook("m-1", "Sample costs", _SHEETS, folder_id="fld-1")

    assert out["refusals"] == []
    assert rec.seen["file_name"] == "Sample costs.xlsx"
    assert rec.seen["folder_id"] == "fld-1"
    assert out["sha256"] == hashlib.sha256(rec.seen["data"]).hexdigest()
    assert out["sheets"] == [{"name": "Advances", "rows": 2, "totals": {"Amount": 1612.5, "Count": 3}}]

    wb = openpyxl.load_workbook(io.BytesIO(rec.seen["data"]))
    assert wb.properties.creator == OPERATOR_CREATOR
    ws = wb["Advances"]
    assert ws["A2"].value == dt.datetime(2025, 2, 3)
    assert ws["C2"].value == 412.50 and ws["C2"].number_format == '"$"#,##0.00'
    assert ws["D3"].value == 2
    assert ws["A4"].value == "Total" and ws["C4"].value == 1612.5 and ws["D4"].value == 3
    # formula injection: stored as literal text, never a formula
    assert ws["B3"].value == '=HYPERLINK("http://x")' and ws["B3"].data_type == "s"


@pytest.mark.parametrize(
    ("sheets", "reason"),
    [
        ([], "1 to 50 sheets"),
        ([{"name": "A/B", "columns": [{"header": "x"}], "rows": []}], "31 characters"),
        ([{"name": "A", "columns": [{"header": "x"}]}, {"name": "a", "columns": [{"header": "x"}]}], "used twice"),
        ([{"name": "A", "columns": [{"header": "x", "format": "money"}]}], "format must be one of"),
        ([{"name": "A", "columns": [{"header": "x"}], "rows": [["1", "2"]]}], "at most 1 values"),
        ([{"name": "A", "columns": [{"header": "x", "format": "date"}], "rows": [["1/14/26"]]}], "not an ISO date"),
        ([{"name": "A", "columns": [{"header": "x", "format": "currency"}], "rows": [["ten"]]}], "row 1, column 'x'"),
        ([{"name": "A", "columns": [{"header": "x"}], "rows": [[{"a": 1}]]}], "single value"),
        ([{"name": "A", "columns": [{"header": "l"}, {"header": "x"}], "totals": ["x"]}], "not numeric"),
        ([{"name": "A", "columns": [{"header": "x", "format": "integer"}], "rows": [[1.5]]}], "whole number"),
    ],
)
def test_add_workbook_refuses_and_uploads_nothing(monkeypatch, sheets, reason) -> None:
    rec = _Recorder()
    monkeypatch.setattr(workbook_tools, "_client", lambda: rec)
    out = workbook_tools.add_workbook("m-1", "x.xlsx", sheets)
    assert out["fileId"] is None and out["uploaded"] is False
    assert reason in out["refusals"][0]
    assert rec.seen == {}


def test_a_workbook_that_does_not_read_back_as_sent_is_never_uploaded(monkeypatch) -> None:
    rec = _Recorder()
    monkeypatch.setattr(workbook_tools, "_client", lambda: rec)
    monkeypatch.setattr(workbook_tools, "verify_workbook", lambda _d, _s: ["row 2 column 3: wrote 1, expected 2"])
    out = workbook_tools.add_workbook("m-1", "x.xlsx", _SHEETS)
    assert "did not read back as sent" in out["refusals"][0]
    assert rec.seen == {}


def test_verify_catches_a_changed_cell() -> None:
    from smokeball_connector.xlsx_io import verify_workbook

    normalized = normalize_sheets(_SHEETS)
    data = build_workbook(normalized)
    normalized[0]["rows"][0][2] = 412.51
    assert any("expected 412.51" in p for p in verify_workbook(data, normalized))


def test_a_built_workbook_round_trips_through_the_reader() -> None:
    text = xlsx_text(build_workbook(normalize_sheets(_SHEETS)))
    assert "2025-02-03 | Mileage | 412.5 | 1" in text
    assert "Total |  | 1612.5 | 3" in text


def test_normalize_rejects_a_total_on_the_label_column() -> None:
    with pytest.raises(WorkbookRefused, match="Total label"):
        normalize_sheets([{"name": "A", "columns": [{"header": "x", "format": "number"}], "totals": ["x"]}])


def test_a_spreadsheet_is_never_staged_as_an_invoice(monkeypatch) -> None:
    """Readable is not stageable: a money write only comes from an invoice
    document's own text layer."""
    blob = _firm_workbook()
    sha = hashlib.sha256(blob).hexdigest()
    monkeypatch.setattr(vendor_invoice, "fetch_bytes", lambda _c, _u: blob)
    monkeypatch.setattr(vendor_invoice, "verify_resolution", lambda _t, _m: object())
    monkeypatch.setattr(vendor_invoice, "_preflight", lambda *a, **k: None)
    consumed: list = []
    monkeypatch.setattr(vendor_invoice, "consume_resolution", lambda *a: consumed.append(a))
    out = vendor_invoice.stage_vendor_invoice(
        object(),
        matter_id="m-1",
        matter_resolution="tok",
        download_url="spool:tok",
        file_name="invoice.xlsx",
        sha256=sha,
        vendor="Acme Reporting",
        invoice_number="INV-1001",
        invoice_date="2026-07-01",
        amount="300.00",
        verify_reference=lambda *_a, **_k: None,
        stamp=lambda _s: None,
        config=vendor_invoice.ExpenseConfig(),
    )
    assert out["status"] == "refused"
    assert "spreadsheet" in out["reason"]
    assert consumed == [], "the resolution must not be spent on a refused spreadsheet"
