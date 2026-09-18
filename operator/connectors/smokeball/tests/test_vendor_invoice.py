"""Vendor invoice intake: the connector half (read_attachment_text,
stage_vendor_invoice, and get_expenses' soft-delete filter).

The money invariant is what these tests exist for, so it is tested three ways:
by the tool's SIGNATURE (no argument can reach finalized or the four billing
settings), by the POST BODY under hostile config and env (finalized is still
the literal False), and by the READ-BACK (an entry that comes back finalized is
reported, never passed). Every "create nothing" outcome is asserted by the
absence of a POST, not by the returned status alone: a status is the tool's
claim about itself, and the request log is the thing it would have to lie to.

No live calls: an httpx.MockTransport plays the tenant and AgentMail.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from typing import Any

import httpx
import pytest

from smokeball_connector import expense_ledger as ledger
from smokeball_connector import server as srv
from smokeball_connector import vendor_invoice as vi
from smokeball_connector.client import SmokeballClient, SmokeballWriteError

MATTER = "f220c8e4-eab5-4fd9-8f1d-0becf715b390"  # the pilot fixture matter 2026-PI-101
URL = "https://download.agentmail.to/attachments/att_1?token=tok123"
FOLDER_ID = "fold-9"


def _text_pdf(lines: list[str]) -> bytes:
    """A one-page PDF with a real text layer (the read_document test's recipe)."""

    def esc(s: str) -> str:
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    content = "BT /F1 10 Tf 40 760 Td 12 TL\n" + "".join(f"({esc(x)}) Tj T*\n" for x in lines) + "ET"
    return _pdf_from_content([content.encode()])


def _scanned_pdf(pages: int = 2) -> bytes:
    """Pages with no text layer at all: a photograph of paper."""
    return _pdf_from_content([b"BT ET" for _ in range(pages)])


def _pdf_from_content(streams: list[bytes]) -> bytes:
    objs: list[bytes] = [b"", b""]
    kids = []
    for stream in streams:
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        objs.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents "
            + str(len(objs)).encode()
            + b" 0 R /Resources << /Font << /F1 __F__ 0 R >> >> >>"
        )
        kids.append(f"{len(objs)} 0 R")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objs = [o.replace(b"__F__", str(len(objs)).encode()) for o in objs]
    objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = b"<< /Type /Pages /Kids [" + " ".join(kids).encode() + b"] /Count " + str(len(streams)).encode() + b" >>"
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)


INVOICE_PDF = _text_pdf(["Acme Records Inc", "Invoice INV-2026-001", "Date 2026-09-01", "Amount due 1250.00"])
INVOICE_SHA = hashlib.sha256(INVOICE_PDF).hexdigest()


class Tenant:
    """A scripted tenant + AgentMail. Records every request so a test can assert
    what was NOT sent, and serves the ledger in pages the way the vendor does."""

    def __init__(self, ledger_rows: list[dict[str, Any]] | None = None, *, blob: bytes = INVOICE_PDF) -> None:
        self.ledger = list(ledger_rows or [])
        self.blob = blob
        self.requests: list[httpx.Request] = []
        self.posted: list[dict[str, Any]] = []
        self.readback_override: dict[str, Any] | None = None
        self.upload_fails = False
        self.ignore_offset = False
        self.ledger_status = 200
        self.folders: list[dict[str, Any]] = [{"id": FOLDER_ID, "name": "Vendor Invoices"}]

    def client(self) -> SmokeballClient:
        c = SmokeballClient(region="us", environment="staging", client_id="c", client_secret="s", api_key="k")
        c._http = httpx.Client(transport=httpx.MockTransport(self.handle))
        return c

    def posts(self, suffix: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST" and r.url.path.endswith(suffix)]

    def ledger_gets(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "GET" and r.url.path.endswith("/expenses")]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, method = request.url.path, request.method
        if path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if str(request.url) == URL:
            return httpx.Response(200, content=self.blob)
        if path == f"/matters/{MATTER}":
            return httpx.Response(200, json={"id": MATTER, "number": "2026-PI-101"})
        if path.endswith("/expenses") and method == "GET":
            if self.ledger_status != 200:
                return httpx.Response(self.ledger_status, text="boom")
            offset = 0 if self.ignore_offset else int(request.url.params.get("Offset", "0"))
            limit = int(request.url.params.get("Limit", "500"))
            return httpx.Response(200, json={"value": self.ledger[offset : offset + limit]})
        if path.endswith("/expenses") and method == "POST":
            body = json.loads(request.content)
            self.posted.append(body)
            return httpx.Response(202, json={"id": "exp-new", "href": f"/matters/{MATTER}/expenses/exp-new"})
        if path.endswith("/expenses/exp-new") and method == "GET":
            record = dict(self.posted[-1], id="exp-new", isDeleted=False)
            record.update(self.readback_override or {})
            return httpx.Response(200, json=record)
        if path.endswith("/documents/folders"):
            return httpx.Response(200, json={"value": self.folders})
        if path.endswith("/documents/files") and method == "POST":
            if self.upload_fails:
                return httpx.Response(500, text="upload broke")
            return httpx.Response(202, json={"uploadUrl": "https://s3.example.com/up?sig=1", "fileId": "file-new"})
        if method == "PUT" and "s3.example.com" in str(request.url):
            return httpx.Response(200)
        return httpx.Response(404, json={"error": f"unscripted {method} {path}"})


def _stage(tenant: Tenant, config: vi.ExpenseConfig | None = None, **overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "matter_id": MATTER,
        "download_url": URL,
        "file_name": "Acme invoice.pdf",
        "sha256": INVOICE_SHA,
        "vendor": "Acme Records Inc",
        "invoice_number": "INV-2026-001",
        "invoice_date": "2026-09-01",
        "amount": "1250.00",
    }
    args.update(overrides)
    return vi.stage_vendor_invoice(
        tenant.client(),
        verify_reference=srv._verify_matter_reference,
        stamp=srv._stamp,
        config=config if config is not None else vi.ExpenseConfig(),
        **args,
    )


def _row(**fields: Any) -> dict[str, Any]:
    base = {
        "id": "exp-old",
        "subject": "Copies",
        "description": "",
        "expenseDate": "2026-08-01",
        "price": 10,
        "quantity": 1,
    }
    base.update(fields)
    return base


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vi, "READBACK_SECONDS", 0.0)


# ---- The money invariant ------------------------------------------------------


def test_no_tool_argument_can_reach_finalized_or_the_billing_settings() -> None:
    params = set(inspect.signature(srv.stage_vendor_invoice).parameters)
    assert params == {
        "matter_id",
        "download_url",
        "file_name",
        "sha256",
        "vendor",
        "invoice_number",
        "invoice_date",
        "amount",
    }
    # The overlay's draft gate scans write arguments by these names; the entry's
    # text is composed in the connector, so none may exist.
    assert not params & {"description", "subject", "title", "body", "note"}


def test_happy_path_stages_unfinalized_reads_back_and_files() -> None:
    tenant = Tenant()
    out = _stage(tenant)
    assert out["status"] == "staged", out
    (body,) = tenant.posted
    assert body["finalized"] is False
    assert body["price"] == 1250.0 and body["quantity"] == 1
    assert body["expenseDate"] == "2026-09-01"
    assert body["subject"] == "Acme Records Inc invoice INV-2026-001"
    assert body["description"].startswith("[Operator] ")
    assert "Acme invoice.pdf" in body["description"]
    assert not {"costType", "isBillable", "activityCode", "staffId"} & set(body)
    assert out["defaulted"] == ["costType", "isBillable", "activityCode", "staffId"]
    assert out["expense_id"] == "exp-new" and out["file_id"] == "file-new"
    assert out["readback"]["verified"] is True
    assert out["amount"] == "1250.00"


def test_finalized_stays_false_whatever_config_and_env_say(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "customer.yaml"
    cfg.write_text(
        "vendor_invoice_intake:\n"
        "  finalized: true\n"
        "  cost_type: soft\n"
        "  is_billable: false\n"
        "  activity_code: COPY\n"
        "  staff_id: staff-1\n"
    )
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(cfg))
    monkeypatch.setenv("SMOKEBALL_EXPENSE_FINALIZED", "true")
    tenant = Tenant()
    out = vi.stage_vendor_invoice(
        tenant.client(),
        matter_id=MATTER,
        download_url=URL,
        file_name="Acme invoice.pdf",
        sha256=INVOICE_SHA,
        vendor="Acme Records Inc",
        invoice_number="INV-2026-001",
        invoice_date="2026-09-01",
        amount="1250.00",
        verify_reference=srv._verify_matter_reference,
        stamp=srv._stamp,
    )
    (body,) = tenant.posted
    assert body["finalized"] is False
    assert body["costType"] == "Soft" and body["isBillable"] is False
    assert body["activityCode"] == "COPY" and body["staffId"] == "staff-1"
    assert out["defaulted"] == []
    assert out["ignored_config_keys"] == ["finalized"]


def test_malformed_authored_setting_refuses_and_writes_nothing() -> None:
    tenant = Tenant()
    out = _stage(tenant, config=vi.parse_expense_config({"cost_type": "Medium"}))
    assert out["status"] == "refused" and "cost_type" in out["reason"]
    assert tenant.posts("/expenses") == []


@pytest.mark.parametrize(
    "block, problem",
    [
        ({"is_billable": "yes"}, "is_billable"),
        ({"activity_code": ""}, "activity_code"),
        ({"staff_id": 7}, "staff_id"),
        ("not a mapping", "mapping"),
    ],
)
def test_config_parses_never_casts(block: Any, problem: str) -> None:
    assert problem in (vi.parse_expense_config(block).error or "")


def test_unparseable_customer_yaml_is_an_error_not_unauthored(tmp_path) -> None:
    bad = tmp_path / "customer.yaml"
    bad.write_text("vendor_invoice_intake: [unclosed\n")
    assert vi.load_expense_config(str(bad)).error


def test_missing_customer_yaml_is_unauthored(tmp_path) -> None:
    cfg = vi.load_expense_config(str(tmp_path / "absent.yaml"))
    assert cfg.error is None and cfg.fields == {}


def test_readback_that_shows_finalized_is_reported_not_passed() -> None:
    tenant = Tenant()
    tenant.readback_override = {"finalized": True}
    out = _stage(tenant)
    assert out["status"] == "staged_unverified"
    assert any("finalized" in p for p in out["readback"]["problems"])
    assert not [r for r in tenant.requests if r.method == "DELETE"]


def test_readback_amount_mismatch_is_reported() -> None:
    tenant = Tenant()
    tenant.readback_override = {"price": 125.0}
    out = _stage(tenant)
    assert out["status"] == "staged_unverified"
    assert any("amount" in p for p in out["readback"]["problems"])


@pytest.mark.parametrize("amount", ["1,250.00", "12.345", "-5", "0", "0.00", "$10", "1e3", "", " ", "NaN"])
def test_amount_must_be_a_positive_two_decimal_string(amount: str) -> None:
    tenant = Tenant()
    out = _stage(tenant, amount=amount)
    assert out["status"] == "refused", amount
    assert tenant.posts("/expenses") == []


def test_amount_as_a_float_is_refused() -> None:
    tenant = Tenant()
    out = _stage(tenant, amount=1250.0)
    assert out["status"] == "refused"


def test_amount_is_decimal_exact() -> None:
    assert str(vi.parse_amount("1250.5")) == "1250.5"
    assert str(vi.parse_amount("0.10")) == "0.10"


@pytest.mark.parametrize("bad", ["2026-02-30", "09/01/2026", "2026-9-1", ""])
def test_invoice_date_must_be_iso(bad: str) -> None:
    out = _stage(Tenant(), invoice_date=bad)
    assert out["status"] == "refused" and "invoice_date" in out["reason"]


def test_text_naming_another_matter_refuses() -> None:
    tenant = Tenant()
    out = _stage(tenant, file_name="Invoice for 2026-PI-102.pdf")
    assert out["status"] == "refused" and "2026-PI-102" in out["reason"]
    assert tenant.posts("/expenses") == []


# ---- The duplicate check --------------------------------------------------------


def test_pages_past_500_and_finds_a_duplicate_on_page_two() -> None:
    rows = [_row(id=f"r{i}", subject=f"Copies {i}") for i in range(500)]
    rows.append(_row(id="dup", subject="Acme Records invoice INV 2026 001"))
    tenant = Tenant(rows)
    out = _stage(tenant)
    assert out["status"] == "duplicate"
    assert [e["id"] for e in out["existing"]] == ["dup"]
    assert [r.url.params["Offset"] for r in tenant.ledger_gets()] == ["0", "500"]
    assert tenant.posts("/expenses") == []


def test_a_vendor_that_ignores_offset_refuses_rather_than_reading_one_page() -> None:
    tenant = Tenant([_row(id=f"r{i}") for i in range(500)])
    tenant.ignore_offset = True
    out = _stage(tenant)
    assert out["status"] == "refused" and "repeated" in out["reason"]
    assert tenant.posts("/expenses") == []


def test_a_ledger_that_cannot_be_read_refuses() -> None:
    tenant = Tenant()
    tenant.ledger_status = 503
    out = _stage(tenant)
    assert out["status"] == "refused" and "could not be read" in out["reason"]
    assert tenant.posts("/expenses") == []


def test_ledger_past_the_page_cap_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ledger, "MAX_PAGES", 2)
    tenant = Tenant([_row(id=f"r{i}") for i in range(1000)])
    out = _stage(tenant)
    assert out["status"] == "refused"
    assert tenant.posts("/expenses") == []


def test_a_deleted_entry_is_not_a_duplicate() -> None:
    tenant = Tenant([_row(id="gone", subject="Acme Records invoice INV-2026-001", isDeleted=True)])
    out = _stage(tenant)
    assert out["status"] == "staged"


def test_same_vendor_amount_and_date_is_a_duplicate_without_the_number() -> None:
    tenant = Tenant([_row(id="d", subject="ACME RECORDS - copies", expenseDate="2026-09-01T00:00:00", price=1250)])
    out = _stage(tenant)
    assert out["status"] == "duplicate"
    assert tenant.posts("/expenses") == []


@pytest.mark.parametrize(
    "row",
    [
        _row(id="n1", subject="Acme Records copies", expenseDate="2026-08-15", price=1250),  # same amount, new date
        _row(id="n2", subject="Acme Records copies", expenseDate="2026-09-01", price=99.5),  # same date, new amount
    ],
)
def test_near_matches_are_possible_duplicates_and_create_nothing(row: dict[str, Any]) -> None:
    tenant = Tenant([row])
    out = _stage(tenant)
    assert out["status"] == "possible_duplicate"
    assert out["existing"][0]["id"] == row["id"]
    assert tenant.posts("/expenses") == []


def test_an_unrelated_ledger_is_clear() -> None:
    tenant = Tenant([_row(id="x", subject="Filing fee", price=1250, expenseDate="2026-09-01")])
    assert _stage(tenant)["status"] == "staged"


def test_a_short_invoice_number_does_not_match_a_date() -> None:
    rows = [_row(subject="Other Vendor invoice 77", description="[Operator] Vendor invoice dated 2026-09-01")]
    verdict, _ = ledger.classify_against_ledger(
        rows, vendor="Acme", invoice_number="2026", invoice_date="2026-10-01", amount=vi.parse_amount("5.00")
    )
    assert verdict is None


def test_run_sequence_matching() -> None:
    assert ledger.contains_run_sequence("acme inv-2026-001 copies", "inv2026001")
    assert ledger.contains_run_sequence("inv2026001", "inv2026001")
    assert not ledger.contains_run_sequence("invoice 10012", "1001")
    assert not ledger.contains_run_sequence("21001", "1001")
    assert ledger.vendor_key("Acme Records, Inc.") == "acmerecords"


# ---- The bytes and the file ---------------------------------------------------


def test_changed_bytes_refuse_before_anything_is_written() -> None:
    tenant = Tenant(blob=INVOICE_PDF + b"\n% tampered")
    out = _stage(tenant)
    assert out["status"] == "refused" and "changed" in out["reason"]
    assert tenant.posts("/expenses") == []
    assert tenant.posts("/documents/files") == []


def test_filing_failure_after_create_reports_the_expense_and_deletes_nothing() -> None:
    tenant = Tenant()
    tenant.upload_fails = True
    out = _stage(tenant)
    assert out["status"] == "staged_file_failed"
    assert out["expense_id"] == "exp-new"
    assert out["file"]["filed"] is False
    assert not [r for r in tenant.requests if r.method == "DELETE"]


def test_authored_folder_is_used_when_found() -> None:
    tenant = Tenant()
    out = _stage(tenant, config=vi.parse_expense_config({"folder_name": "vendor invoices"}))
    (upload,) = tenant.posts("/documents/files")
    assert json.loads(upload.content)["folderId"] == FOLDER_ID
    assert out["file"]["note"] is None


def test_authored_folder_missing_files_at_root_and_says_so() -> None:
    tenant = Tenant()
    tenant.folders = []
    out = _stage(tenant, config=vi.parse_expense_config({"folder_name": "Vendor Invoices"}))
    (upload,) = tenant.posts("/documents/files")
    assert "folderId" not in json.loads(upload.content)
    assert "not found" in out["file"]["note"]


# ---- read_attachment_text -------------------------------------------------------


def _read(blob: bytes, file_name: str) -> dict[str, Any]:
    return vi.read_attachment(Tenant(blob=blob).client(), URL, file_name)


def test_reads_a_text_pdf_and_hashes_its_bytes() -> None:
    out = _read(INVOICE_PDF, "invoice.pdf")
    assert out["readable"] is True and out["method"] == "pypdf"
    assert "INV-2026-001" in out["text"]
    assert out["sha256"] == INVOICE_SHA and out["byteLength"] == len(INVOICE_PDF)


def test_read_refuses_a_host_off_the_allowlist() -> None:
    with pytest.raises(SmokeballWriteError, match="not an allowed attachment source"):
        vi.read_attachment(Tenant().client(), "https://evil.example.com/invoice.pdf", "invoice.pdf")


@pytest.mark.parametrize(
    "blob, name",
    [
        (b"\xff\xd8\xff\xe0 jpeg bytes", "photo.jpg"),
        (b"\x89PNG\r\n\x1a\n png bytes", "scan"),
        (b"arbitrary", "invoice.PNG"),
    ],
)
def test_images_are_unreadable(blob: bytes, name: str) -> None:
    out = _read(blob, name)
    assert out["readable"] is False and out["reason"] == "image" and out["text"] == ""


@pytest.mark.parametrize(
    "blob, name",
    [
        (b"From: someone@example.com\nSubject: invoice\n\nbody", "forwarded.eml"),
        (b"Received: from mx\nFrom: a@b.c\n\nbody", "attachment"),
        (b"MIME-Version: 1.0\nContent-Type: text/plain\n\nx", "noext"),
    ],
)
def test_email_messages_are_unreadable(blob: bytes, name: str) -> None:
    out = _read(blob, name)
    assert out["readable"] is False and out["reason"] == "email_message"


def test_a_scanned_pdf_is_unreadable_and_never_transcribed(monkeypatch: pytest.MonkeyPatch) -> None:
    import smokeball_connector.vision as vision

    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("read_attachment_text must never initiate a vision read")

    monkeypatch.setattr(vision, "transcribe_pdf", boom)
    out = _read(_scanned_pdf(), "invoice.pdf")
    assert out["readable"] is False and out["reason"] == "scanned" and out["text"] == ""
    assert out["pages"] == 2


def test_a_cached_machine_transcription_is_not_invoice_text(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from smokeball_connector.extract_cache import cache_put

    monkeypatch.setenv("SMOKEBALL_EXTRACT_CACHE_DIR", str(tmp_path))
    blob = _scanned_pdf()
    cache_put(blob, "Invoice 55 total 999.00", pages=2)
    out = _read(blob, "invoice.pdf")
    assert out["method"] == "vision_cached"
    assert out["readable"] is False and out["reason"] == "scanned" and out["text"] == ""


def test_plain_text_invoice_is_readable() -> None:
    out = _read(b"Acme Records\nInvoice 12\nTotal 40.00\n", "invoice.txt")
    assert out["readable"] is True and out["method"] == "plain"


def test_binary_junk_is_unsupported() -> None:
    out = _read(b"\x00\x01\x02 binary", "invoice.bin")
    assert out["readable"] is False and out["reason"] == "unsupported"


# ---- get_expenses drops soft-deleted rows ------------------------------------


def test_get_expenses_drops_and_counts_deleted_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = Tenant([_row(id="live"), _row(id="gone", isDeleted=True)])
    monkeypatch.setattr(srv, "_client", tenant.client())
    out = srv.get_expenses(MATTER)
    assert [r["id"] for r in out["value"]] == ["live"]
    assert out["deletedExcluded"] == 1
    # The reply names the matter by the number this read returned.
    assert out["matterNumber"] == "2026-PI-101"
    assert out["value"][0]["matterNumber"] == "2026-PI-101"
    everything = srv.get_expenses(MATTER, include_deleted=True)
    assert [r["id"] for r in everything["value"]] == ["live", "gone"]
    assert "deletedExcluded" not in everything


def test_drop_deleted_keeps_a_bare_list_a_list() -> None:
    assert ledger.drop_deleted([{"id": 1}, {"id": 2, "isDeleted": True}]) == [{"id": 1}]
