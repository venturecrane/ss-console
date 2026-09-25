"""``add_workbook``: build an Excel workbook from structured data and file it.

The Operator could read a matter and compose a letter, but it could not hand the
firm a spreadsheet: the costs-and-advances workbooks the firm received in
September 2026 were built off the seat. ``add_file`` cannot fill the gap on its
own, because a workbook is binary and model-written base64 corrupts silently
(#2055). This tool takes rows as JSON and does the encoding, the typing and the
arithmetic in code (``xlsx_io``).

Classified INTERNAL_WRITE at the overlay, like ``add_file``: the agent saving its
own work product into the firm's record. Its cells are identifier-scanned there
(the ``sheets`` argument), in the default blocking posture of
``render_docx_draft``.

Registered from ``attachment_tools.register`` only because that is the one
registrar ``server.py`` already calls; ``server.py`` is size-ratcheted and cannot
take a new import and call of its own.
"""

from __future__ import annotations

import hashlib
from typing import Any

from .xlsx_io import WorkbookRefused, build_workbook, normalize_sheets, verify_workbook


def _client() -> Any:
    from . import server

    return server._get_client()


def _refused(matter_id: str, file_name: str, reason: str) -> dict[str, Any]:
    return {
        "matterId": matter_id,
        "fileName": file_name,
        "fileId": None,
        "uploaded": False,
        "sha256": None,
        "sizeBytes": None,
        "refusals": [reason],
    }


def add_workbook(
    matter_id: str,
    file_name: str,
    sheets: list[dict[str, Any]],
    folder_id: str | None = None,
) -> Any:
    """Build an Excel workbook (.xlsx) from rows you supply and file it on a
    matter. Use it for any tabular work product: an itemized cost list, a
    ledger, a comparison. Never hand-build a spreadsheet through ``add_file``.

    ``sheets`` is a list of sheets, each::

        {"name": "Advances",                      # <= 31 chars, unique
         "columns": [{"header": "Date", "format": "date"},
                     {"header": "Description", "format": "text"},
                     {"header": "Amount", "format": "currency"}],
         "rows": [["2025-02-03", "Mileage", 412.50], ...],
         "totals": ["Amount"]}                    # optional

    ``format`` is one of ``text``, ``number``, ``integer``, ``currency``,
    ``date`` (default ``text``). Dates are ISO ``YYYY-MM-DD``; numbers may be
    numbers or numeric strings (``"1,090.00"``, ``"$781.42"``). Put each figure
    exactly as you read it from the record; never round or recompute it.

    ``totals`` names numeric columns to sum. THE SUM IS COMPUTED HERE, in code,
    and written as a bold final row labelled ``Total``: never add a column of
    figures yourself, and never put a total row in ``rows``.

    A text cell is always literal text: a value starting with ``=`` is stored as
    text, never run as a formula. ``.xlsx`` is appended to ``file_name`` if
    missing; ``folder_id`` is optional (matter root if omitted; see
    ``list_folders`` / ``create_folder``).

    Every cell of the built file is read back and compared to what you sent
    before anything is uploaded. Returns ``fileId``, ``sha256``, ``sizeBytes``
    and a per-sheet ``rows`` count and ``totals``. On a problem nothing is
    uploaded and ``refusals`` names it (the sheet, row and column)."""
    name = file_name.strip()
    if not name:
        return _refused(matter_id, file_name, "file_name is required")
    if not name.lower().endswith(".xlsx"):
        name += ".xlsx"
    try:
        normalized = normalize_sheets(sheets)
    except WorkbookRefused as exc:
        return _refused(matter_id, name, str(exc))
    data = build_workbook(normalized)
    mismatches = verify_workbook(data, normalized)
    if mismatches:
        return _refused(matter_id, name, "the built workbook did not read back as sent: " + "; ".join(mismatches[:5]))
    result = _client().add_file(matter_id, name, data, folder_id=folder_id)
    out = dict(result) if isinstance(result, dict) else {"result": result}
    out["sha256"] = hashlib.sha256(data).hexdigest()
    out["sizeBytes"] = len(data)
    out["sheets"] = [{"name": s["name"], "rows": len(s["rows"]), "totals": s["totals"]} for s in normalized]
    out["refusals"] = []
    return out


def register(server: Any) -> None:
    """Register ``add_workbook``. Called once, from ``attachment_tools.register``."""
    server.tool()(add_workbook)


__all__ = ["add_workbook", "register"]
