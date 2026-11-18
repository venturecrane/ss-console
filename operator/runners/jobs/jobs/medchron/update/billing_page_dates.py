"""Dates of service on the billing pages an update set aside. $0, no model.

WHY (2026-11-17). An update reads a provider's "records and billing" file
and sets its claim forms and ledger pages aside (``select``, "billing
page"): they are never itemized as care. Their dates of service were then
evidence only for the handwritten-date check; the billed-date check
(``dos_check``) read only ``billing_extract.jsonl``, which an update fills
only when its request names a billing record (``stages/billing_update``). So
a visit a ledger page bills and no entry carries could pass unseen.

WHAT IT DOES. From the text the update already transcribed
(``text/<id>.txt``), for each billing page of a document the update READ for
this client (``update/new_exhibits.final.json``, the pages ``selection.json``
set aside as "billing page"), the dates of service by code
(``handwritten.service_dates``: a DOS-labelled line, or a dated line item
carrying a code or a charge; print, due and payment dates never count). A
page that names another patient (``gates/foreign_identity.findings``) is
dropped. Written to ``billing/record_page_dates.json`` as
``[{date, provider: "", file, page}]`` (``file``: the pulled file's name as
``extracted.jsonl`` holds it, which may name a provider: the file stays in
the job directory, and nothing copies it into a run block or a log line).
RECORD ONLY (2026-11-17): no saved real job names a billing record, so this
path into the worksheet was never run on real data. Nothing adds these
dates to the billed dates, the worksheet's "Dates billed with no medical
record on file" table, its count or the reply; ``dos_check`` records what
the table WOULD hold (``billed_no_record_dates``: ``as_filed`` and
``with_billing_pages``, a date some billing record already carries counting
once, the page itself never a record page that carries it), so the first
real job that names a billing record says what enforcing it would change.

If the matter has no delivered billing worksheet, the dates are recorded
and nothing else happens: no worksheet is built or refreshed for them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..gates import foreign_identity as FI
from .billing_page import BILLING, is_billing
from .handwritten import service_dates
from .select import pages_of_text

NAME = "billing/record_page_dates.json"


def _read(p: Path, default: Any) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def dates(slug_dir: Path, ud: Path, units: list[Any]) -> list[dict[str, Any]]:
    """[{date (ISO), provider, file, page}] from this client's read documents' billing pages."""
    rows = _read(ud / "new_exhibits.final.json", None)
    if not isinstance(rows, list):
        rows = _read(ud / "new_exhibits.json", [])
    surnames, dobs = FI.identities(units)
    firsts = FI.first_names(units)
    # The file as ``dos_check`` names pages (``extracted.jsonl``), so the page is known there as a bill.
    named = {str(x.get("id")): str(x.get("name") or "") for x in _jsonl(slug_dir / "extracted.jsonl")}
    out: list[dict[str, Any]] = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict) or not r.get("id") or not (r.get("excluded") or {}).get(BILLING):
            continue
        tp = slug_dir / "text" / f"{r['id']}.txt"
        doc = tp.read_text(encoding="utf-8", errors="replace") if tp.is_file() else ""
        pages = pages_of_text(doc)
        read = set(r.get("read") or [])
        for p, text in sorted(pages.items()):
            if p in read or not is_billing(text) or FI.findings(text, surnames, dobs, firsts, doc):
                continue
            for d in sorted(service_dates([], "", [text])):
                name = named.get(str(r["id"])) or str(r["id"])
                out.append({"date": d.isoformat(), "provider": "", "file": name, "page": p})
    return out


def _jsonl(p: Path) -> list[dict[str, Any]]:
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines() if p.is_file() else []:
        try:
            x = json.loads(ln)
        except ValueError:
            continue
        if isinstance(x, dict):
            out.append(x)
    return out


def write(slug_dir: Path, ud: Path, units: list[Any]) -> list[dict[str, Any]]:
    got = dates(slug_dir, ud, units)
    p = slug_dir / NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(got, indent=1), encoding="utf-8")
    return got


def load(slug_dir: Path) -> list[dict[str, Any]]:
    """The recorded dates, parsed, never cast: a malformed file is none."""
    raw = _read(slug_dir / NAME, [])
    if not isinstance(raw, list):
        return []
    return [
        r
        for r in raw
        if isinstance(r, dict)
        and isinstance(r.get("date"), str)
        and isinstance(r.get("page"), int)
        and not isinstance(r.get("page"), bool)
    ]
