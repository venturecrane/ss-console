"""Vendor invoice intake: read an emailed invoice, stage it as an UNFINALIZED expense.

A firm's office manager forwards the vendor bills that arrive by email (records
copying, court reporters, filing services) and somebody keys each one into the
matter's expenses by hand. This module is the connector half of doing that for
her: ``read_attachment`` turns the emailed PDF into text the skill can extract
from, and ``stage_vendor_invoice`` writes one unfinalized expense and files the
PDF beside it. The skill decides WHICH matter; this module decides nothing
about money it was not handed and never finalizes anything.

THE MONEY INVARIANT IS STRUCTURAL, NOT A RULE THE MODEL FOLLOWS.

* ``finalized`` is always ``False``. No tool argument, no config key, and no
  environment variable reaches it. Finalizing an entry is the firm's act,
  never the Operator's.
* ``costType``, ``isBillable``, ``activityCode`` and ``staffId`` come ONLY from
  the seat's authored ``vendor_invoice_intake`` block in its live customer.yaml
  (the same config-as-data read ``library.py`` does for the document library).
  The tool has no parameter for any of them, so the model cannot set them.
  Unauthored, each is OMITTED and Smokeball applies its own default; the result
  names every field left to the default (``defaulted``) so the reply says so.
  An authored value that does not parse REFUSES the write: a typo in how the
  firm bills is not the same fact as the firm never saying.
* The subject and description are composed HERE from the four extracted facts
  and the file name. No free-text argument exists for either.

EVERY WAY TO BE UNSURE CREATES NOTHING. A duplicate, a near duplicate, a ledger
that could not be read to its end, an attachment whose bytes changed between
the read and the write, and text naming another matter's number all return a
status and write nothing. After the write, the entry is read back and checked
(finalized false, amount equal); a check that fails is reported plainly and the
entry is never deleted on the connector's own motion. If filing the PDF fails
after the expense exists, the result says so with the expense id.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from .client import SmokeballApiError, SmokeballWriteError
from .expense_ledger import classify_against_ledger, read_whole_ledger, row_amount
from .library import CUSTOMER_YAML_ENV, DEFAULT_CUSTOMER_YAML, find_folder_id
from .task_update import MatterReferenceMismatch

# ---- Reading the attachment -----------------------------------------------

#: The only extraction roads whose text may feed a money write: the file's own
#: text layer. A machine transcription of a scan (``vision``/``vision_cached``)
#: is never one of them, however it got into the cache.
_READABLE_METHODS = frozenset({"pypdf", "docx", "plain"})

#: Why an attachment is unreadable. Closed set; the skill turns each into a
#: flag line, never into a guess at the content.
REASON_IMAGE = "image"
REASON_EMAIL_MESSAGE = "email_message"
REASON_SCANNED = "scanned"
REASON_UNSUPPORTED = "unsupported"
REASON_EMPTY = "empty"

_IMAGE_EXTENSIONS = frozenset({"jpg", "jpeg", "png", "gif", "bmp", "tif", "tiff", "heic", "heif", "webp"})
_IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"II*\x00", b"MM\x00*")
_EMAIL_EXTENSIONS = frozenset({"eml", "msg"})
_EMAIL_HEADER = re.compile(
    rb"^(?:Received|Return-Path|Delivered-To|MIME-Version|Message-ID|From|To|Subject|Date|X-[A-Za-z0-9-]+):",
    re.IGNORECASE,
)

#: An invoice is a page or two. Anything longer is truncated, and says so.
MAX_TEXT_CHARS = 100_000


def _extension(file_name: str) -> str:
    return file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""


def _refused_type(blob: bytes, file_name: str) -> str | None:
    """An image or an email message is refused BEFORE extraction runs, because
    the generic plain-text fallback would otherwise hand back an .eml's raw
    headers as if they were an invoice."""
    ext = _extension(file_name)
    if ext in _IMAGE_EXTENSIONS or blob.startswith(_IMAGE_MAGIC):
        return REASON_IMAGE
    if ext in _EMAIL_EXTENSIONS or (not blob.startswith(b"%PDF") and _EMAIL_HEADER.match(blob[:200])):
        return REASON_EMAIL_MESSAGE
    return None


def read_attachment(client: Any, download_url: str, file_name: str) -> dict[str, Any]:
    """Fetch an emailed attachment through the connector's allowlisted fetch and
    return its TEXT, or an explicit unreadable marker. Never initiates a vision
    read, and never returns a machine transcription as text.

    ``sha256`` is over the fetched bytes; ``stage_vendor_invoice`` fetches again
    and refuses if the bytes differ, so what was read is what gets filed."""
    from .extract import UnsupportedDocumentError, extract_text_ex

    blob = client.fetch_attachment_url(download_url)
    out: dict[str, Any] = {
        "fileName": file_name,
        "sha256": hashlib.sha256(blob).hexdigest(),
        "byteLength": len(blob),
        "readable": False,
        "method": None,
        "text": "",
        "pages": None,
        "reason": None,
        "truncated": False,
    }
    refused = _refused_type(blob, file_name)
    if refused is not None:
        out["reason"] = refused
        return out
    try:
        result = extract_text_ex(blob, file_name=file_name, file_extension=_extension(file_name), allow_vision=False)
    except UnsupportedDocumentError:
        out["reason"] = REASON_UNSUPPORTED
        return out
    out["method"], out["pages"] = result.method, result.pages
    if result.method not in _READABLE_METHODS:
        out["reason"] = REASON_SCANNED
        return out
    if not result.text.strip():
        out["reason"] = REASON_EMPTY
        return out
    out["readable"] = True
    out["text"] = result.text[:MAX_TEXT_CHARS]
    out["truncated"] = len(result.text) > MAX_TEXT_CHARS
    return out


# ---- The seat's authored expense settings --------------------------------

CONFIG_BLOCK = "vendor_invoice_intake"

#: customer.yaml key -> Smokeball body field. The ONLY path by which any of
#: these four reaches a request body.
_CONFIG_FIELDS = {
    "cost_type": "costType",
    "is_billable": "isBillable",
    "activity_code": "activityCode",
    "staff_id": "staffId",
}
_CONFIG_KEYS = frozenset({*_CONFIG_FIELDS, "folder_name"})
_COST_TYPES = {"hard": "Hard", "soft": "Soft"}


@dataclass(frozen=True)
class ExpenseConfig:
    """What the firm authored. ``fields`` holds only authored body fields."""

    fields: dict[str, Any] = field(default_factory=dict)
    folder_name: str | None = None
    error: str | None = None
    ignored_keys: tuple[str, ...] = ()

    @property
    def defaulted(self) -> list[str]:
        return [wire for wire in _CONFIG_FIELDS.values() if wire not in self.fields]


def _parse_setting(key: str, value: Any) -> tuple[Any, str | None]:
    """One authored value, parsed (never cast). Returns ``(value, problem)``."""
    if key == "cost_type":
        norm = _COST_TYPES.get(value.strip().lower()) if isinstance(value, str) else None
        return (norm, None) if norm else (None, "cost_type must be Hard or Soft")
    if key == "is_billable":
        return (value, None) if isinstance(value, bool) else (None, "is_billable must be true or false")
    if isinstance(value, str) and value.strip():
        return value.strip(), None
    return None, f"{key} must be a non-empty string"


def parse_expense_config(block: Any) -> ExpenseConfig:
    """Parse the authored block. Absent is unauthored; malformed is an error."""
    if block is None:
        return ExpenseConfig()
    if not isinstance(block, dict):
        return ExpenseConfig(error=f"{CONFIG_BLOCK} must be a mapping")
    fields: dict[str, Any] = {}
    folder_name: str | None = None
    for key in sorted(_CONFIG_KEYS & set(block)):
        value, problem = _parse_setting(key, block[key])
        if problem:
            return ExpenseConfig(error=f"{CONFIG_BLOCK}.{problem}")
        if key == "folder_name":
            folder_name = value
        else:
            fields[_CONFIG_FIELDS[key]] = value
    ignored = tuple(sorted(str(k) for k in block if k not in _CONFIG_KEYS))
    return ExpenseConfig(fields=fields, folder_name=folder_name, ignored_keys=ignored)


def load_expense_config(path: str | None = None) -> ExpenseConfig:
    """Read the block off the seat's live customer.yaml. A file that is not
    there is unauthored (a local run has none); a file that will not parse is
    an error, because then nobody can say what the firm authored."""
    path = path or os.environ.get(CUSTOMER_YAML_ENV) or DEFAULT_CUSTOMER_YAML
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        return ExpenseConfig()
    try:
        import yaml

        data = yaml.safe_load(raw) or {}
    except Exception as exc:  # noqa: BLE001 - an unparseable config refuses the write, reported
        return ExpenseConfig(error=f"customer.yaml not parseable: {exc.__class__.__name__}")
    return parse_expense_config(data.get(CONFIG_BLOCK) if isinstance(data, dict) else None)


# ---- The invoice facts the skill extracted -------------------------------

_AMOUNT_RE = re.compile(r"^\d{1,9}(?:\.\d{1,2})?$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_NAME = 120


@dataclass(frozen=True)
class InvoiceFacts:
    vendor: str
    invoice_number: str
    invoice_date: str
    amount: Decimal


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else ""


def parse_amount(raw: Any) -> Decimal | None:
    """A positive dollar amount written as a string with at most two decimal
    places ("1250", "1250.5", "1250.50"). Anything else is None: no commas, no
    currency sign, no float, because each is a place a figure can change shape."""
    if not isinstance(raw, str) or not _AMOUNT_RE.match(raw.strip()):
        return None
    try:
        value = Decimal(raw.strip())
    except InvalidOperation:
        return None
    return value if value > 0 else None


def _valid_date(raw: str) -> bool:
    if not _DATE_RE.match(raw):
        return False
    try:
        date.fromisoformat(raw)
    except ValueError:
        return False
    return True


def parse_invoice(
    *, vendor: Any, invoice_number: Any, invoice_date: Any, amount: Any, sha256: Any
) -> tuple[InvoiceFacts | None, str | None]:
    """Validate the extracted facts. Returns ``(facts, None)`` or ``(None, why)``."""
    vendor_s, number_s, date_s = _clean(vendor), _clean(invoice_number), _clean(invoice_date)
    parsed_amount = parse_amount(amount)
    problems = [
        "vendor is empty" if not vendor_s else "",
        "invoice_number is empty or has no letters or digits" if not re.search(r"[A-Za-z0-9]", number_s) else "",
        "vendor or invoice_number is too long" if max(len(vendor_s), len(number_s)) > _MAX_NAME else "",
        "invoice_date must be an ISO date (YYYY-MM-DD)" if not _valid_date(date_s) else "",
        "amount must be a positive figure with at most two decimals, e.g. 1250.00" if parsed_amount is None else "",
        "sha256 must be the 64-character hash read_attachment_text returned"
        if not _SHA_RE.match(_clean(sha256))
        else "",
    ]
    problems = [p for p in problems if p]
    if problems or parsed_amount is None:
        return None, "; ".join(problems)
    return InvoiceFacts(vendor_s, number_s, date_s, parsed_amount), None


# ---- Staging ----------------------------------------------------------------

#: The entry may take a moment to become readable after the 202; a 404 inside
#: this window is "not yet", anything else is an answer.
READBACK_ATTEMPTS = 5
READBACK_SECONDS = 1.0


def _refused(reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": "refused", "reason": reason, "created": False, **extra}


def compose_entry(facts: InvoiceFacts, file_name: str, stamp: Callable[[str], str | None]) -> tuple[str, str]:
    """The subject and description, composed in code from the extracted facts.
    The description carries the machine-provenance mark so a person reading the
    ledger can tell the Operator's entry from a colleague's."""
    subject = f"{facts.vendor} invoice {facts.invoice_number}"
    description = stamp(
        f"Vendor invoice dated {facts.invoice_date}, source file {file_name}. Staged unfinalized for review."
    )
    return subject, description or ""


def build_expense_body(facts: InvoiceFacts, subject: str, description: str, cfg: ExpenseConfig) -> dict[str, Any]:
    """The POST body. ``finalized`` is the literal False, and it is written LAST
    so no merge of authored fields can overwrite it."""
    price = float(facts.amount)
    if Decimal(repr(price)) != facts.amount:  # the wire number must be the exact figure
        raise ValueError(f"amount {facts.amount} does not survive JSON encoding exactly")
    body: dict[str, Any] = {
        "expenseDate": facts.invoice_date,
        "subject": subject,
        "description": description,
        "quantity": 1,
        "price": price,
    }
    body.update({k: v for k, v in cfg.fields.items() if k in ("costType", "isBillable", "activityCode", "staffId")})
    body["finalized"] = False
    return body


def _read_back(client: Any, matter_id: str, expense_id: str, facts: InvoiceFacts) -> dict[str, Any]:
    record: Any = None
    for attempt in range(READBACK_ATTEMPTS):
        try:
            record = client.get(f"/matters/{matter_id}/expenses/{expense_id}")
            break
        except SmokeballApiError as exc:
            if exc.status != 404:
                return {"verified": False, "problems": [f"the read-back failed: HTTP {exc.status}"]}
        if attempt < READBACK_ATTEMPTS - 1:
            time.sleep(READBACK_SECONDS)
    if not isinstance(record, dict):
        return {"verified": False, "problems": ["the entry could not be read back"]}
    problems = []
    if record.get("finalized") is not False:
        problems.append(f"the entry reads back finalized={record.get('finalized')!r}, expected false")
    amount = row_amount(record)
    if amount != facts.amount:
        problems.append(f"the entry reads back amount {amount}, expected {facts.amount}")
    return {"verified": not problems, "problems": problems, "finalized": record.get("finalized")}


def _file_invoice(client: Any, matter_id: str, file_name: str, blob: bytes, cfg: ExpenseConfig) -> dict[str, Any]:
    folder_id = find_folder_id(client, matter_id, cfg.folder_name) if cfg.folder_name else None
    note = None
    if cfg.folder_name and folder_id is None:
        note = f"folder {cfg.folder_name!r} was not found on this matter, so the file went to the matter root"
    try:
        result = client.add_file(matter_id, file_name, blob, folder_id=folder_id)
    except Exception as exc:  # noqa: BLE001 - the expense already exists; the failure is reported, never raised
        return {"filed": False, "error": f"{exc.__class__.__name__}: {str(exc)[:300]}"}
    return {"filed": True, "fileId": result.get("fileId"), "folderId": folder_id, "note": note}


def _preflight(
    client: Any, matter_id: str, facts: InvoiceFacts, subject: str, description: str, verify_reference: Callable
) -> dict[str, Any] | None:
    """Every check that must pass before anything is written. None means go."""
    try:
        verify_reference(client, matter_id, subject, description)
    except MatterReferenceMismatch as exc:
        return _refused(str(exc))
    try:
        rows = read_whole_ledger(client, matter_id)
    except Exception as exc:  # noqa: BLE001 - "could not check" refuses; it never reads as "no duplicate"
        return _refused(f"the matter's expenses could not be read to the end ({exc.__class__.__name__}: {exc})")
    verdict, matches = classify_against_ledger(
        rows,
        vendor=facts.vendor,
        invoice_number=facts.invoice_number,
        invoice_date=facts.invoice_date,
        amount=facts.amount,
    )
    if verdict:
        return {"status": verdict, "created": False, "matter_id": matter_id, "existing": matches}
    return None


def stage_vendor_invoice(
    client: Any,
    *,
    matter_id: str,
    download_url: str,
    file_name: str,
    sha256: str,
    vendor: str,
    invoice_number: str,
    invoice_date: str,
    amount: str,
    verify_reference: Callable,
    stamp: Callable[[str], str | None],
    config: ExpenseConfig | None = None,
) -> dict[str, Any]:
    """Validate, de-duplicate, re-verify the bytes, write one unfinalized
    expense, read it back, and file the PDF. See the module docstring."""
    facts, problem = parse_invoice(
        vendor=vendor, invoice_number=invoice_number, invoice_date=invoice_date, amount=amount, sha256=sha256
    )
    if facts is None:
        return _refused(problem or "invalid invoice facts")
    if not _clean(matter_id) or not _clean(file_name):
        return _refused("matter_id and file_name are required")
    cfg = config if config is not None else load_expense_config()
    if cfg.error:
        return _refused(f"the seat's expense settings are malformed: {cfg.error}")
    subject, description = compose_entry(facts, file_name, stamp)
    blocked = _preflight(client, matter_id, facts, subject, description, verify_reference)
    if blocked is not None:
        return blocked
    try:
        blob = client.fetch_attachment_url(download_url)
    except SmokeballWriteError as exc:
        return _refused(f"the attachment could not be fetched again: {exc}")
    if hashlib.sha256(blob).hexdigest() != _clean(sha256):
        return _refused("the attachment's bytes changed since it was read; read it again before staging")
    accepted = client.request(
        "POST", f"/matters/{matter_id}/expenses", json=build_expense_body(facts, subject, description, cfg)
    )
    expense_id = str(accepted.get("id") or "") if isinstance(accepted, dict) else ""
    readback = (
        _read_back(client, matter_id, expense_id, facts)
        if expense_id
        else {"verified": False, "problems": ["Smokeball accepted the entry but returned no id to read back"]}
    )
    filed = _file_invoice(client, matter_id, file_name, blob, cfg)
    status = "staged" if readback["verified"] else "staged_unverified"
    if status == "staged" and not filed["filed"]:
        status = "staged_file_failed"
    return {
        "status": status,
        "created": True,
        "expense_id": expense_id or None,
        "matter_id": matter_id,
        "amount": str(facts.amount),
        "subject": subject,
        "file_id": filed.get("fileId"),
        "file": filed,
        "readback": readback,
        "defaulted": cfg.defaulted,
        "ignored_config_keys": list(cfg.ignored_keys),
    }
