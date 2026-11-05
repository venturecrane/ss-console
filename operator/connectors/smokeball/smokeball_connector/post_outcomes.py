"""The post journal's record of one letter, derived from what the tools recorded.

WHY (the model-as-logic audit, 2026-11-04). ``record_post_batch`` took each
letter's ``placed_on`` (the matter number it was filed on), its ``issue`` and
its ``candidates`` from the model, and the model typed them from its memory of
the turn. The tools that resolved, filed and keyed each letter knew all three,
and now each writes its own outcome row (``outcome_ledger``). This module turns
those rows into the record. Nothing here reads the model's account of what
happened; the one fact it takes from the model is the letter's ``kind``, the
judgement of what the paper is, and that judgement decides only which keying
act the letter needed.

THE TABLE, applied to the rows recorded for exactly this letter's pages:

* FILED (a ``file_attachment_pages_to_matter`` row ``filed``, or a
  ``stage_vendor_invoice`` row that wrote the expense): ``placed_on`` is that
  row's matter number. Then the act the letter's kind requires (``ACTS``):

  - a ``medical_bill`` needs an ``add_medicals_row`` row ``written``;
  - a ``vendor_bill`` needs the stage itself ``staged``;
  - a ``settlement_offer`` needs an ``add_negotiation_rows`` row ``written``;
  - any other kind needs nothing more.

  The act recorded as done is ``issue ""``. No row for it is the kind's own
  issue (``tab_not_written``, ``expense_not_staged``, ``offer_not_written``)
  with the detail "no attempt recorded". A row that did not land is that
  issue, or the row's own narrower word (``figure_not_saved``,
  ``possible_duplicate``), with the tool's own reason.
* NOT FILED, with a resolve verdict for these pages: ``ambiguous`` and its
  candidates, ``one_candidate`` (a ``none`` naming one matter), ``no_match``,
  or ``search_failed``; a ``unique`` verdict whose filing did not happen is
  ``filing_refused`` with the filing's reason.
* NOT FILED, no verdict, a refused filing: ``filing_refused``.
* Nothing recorded at all: ``not_attempted``, which the daily list asks a
  person about. A run that skipped a letter, or a ledger that could not be
  written, is visible rather than silent.
"""

from __future__ import annotations

from typing import Any

from . import outcome_ledger as ol

#: A letter's kind -> (the tool that keys it, the issue when it did not).
ACTS: dict[str, tuple[str, str]] = {
    "medical_bill": (ol.MEDICALS, "tab_not_written"),
    "vendor_bill": (ol.STAGE, "expense_not_staged"),
    "settlement_offer": (ol.NEGOTIATION, "offer_not_written"),
}
#: Outcome words that mean the act landed.
DONE = frozenset({"written", "staged"})
#: Outcome words that keep their own name as the letter's issue.
OWN_ISSUE = frozenset({"figure_not_saved", "possible_duplicate"})
#: The filing statuses that put the pages on the matter.
FILED = frozenset({"filed", "link_not_visible", "readback_mismatch"})
#: Filing statuses that put the pages there without a whole read-back.
FILED_UNCONFIRMED = frozenset({"link_not_visible", "readback_mismatch"})
NO_ATTEMPT = "no attempt recorded"


def _span(row: dict[str, Any]) -> tuple[Any, Any]:
    return row.get("first_page"), row.get("last_page")


def _filing(row: dict[str, Any]) -> bool:
    """A row that put these pages on a matter."""
    if row.get("tool") == ol.FILE:
        return row.get("status") in FILED
    return row.get("tool") == ol.STAGE and row.get("status") in ("staged", "figure_not_saved")


def _placed_issue(kind: str, filing: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[str, str]:
    """(issue, detail) for a letter that was filed."""
    if filing.get("tool") == ol.FILE and filing.get("status") in FILED_UNCONFIRMED:
        return "figure_not_saved", "the filed document did not read back as uploaded; check it on the matter"
    act = ACTS.get(kind)
    if act is None:
        return "", ""
    tool, missing = act
    if tool == ol.STAGE:
        if filing.get("tool") == ol.STAGE:
            return ("", "") if filing.get("status") == "staged" else ("figure_not_saved", filing.get("detail") or "")
        attempts = [r for r in rows if r.get("tool") == ol.STAGE]
    else:
        attempts = [r for r in rows if r.get("tool") == tool and r.get("matter_id") == filing.get("matter_id")]
    if any(r.get("status") in DONE for r in attempts):
        return "", ""
    if not attempts:
        return missing, NO_ATTEMPT
    last = attempts[-1]
    status = str(last.get("status") or "")
    return (status if status in OWN_ISSUE else missing), str(last.get("detail") or status)


def _held(rows: list[dict[str, Any]]) -> tuple[str, list[str], str]:
    """(issue, candidates, detail) for a letter that was not filed."""
    verdicts = [r for r in rows if r.get("tool") == ol.RESOLVE]
    refusals = [r for r in rows if r.get("tool") in (ol.FILE, ol.STAGE) and not _filing(r)]
    refused = str(refusals[-1].get("detail") or refusals[-1].get("status")) if refusals else ""
    if refusals and refusals[-1].get("status") == "possible_duplicate":
        number = refusals[-1].get("matter_number") or "the resolved matter"
        refused = f"{refused or 'a matching expense is already entered'} on matter {number}; nothing was filed"
    if not verdicts:
        return ("filing_refused", [], refused) if refusals else ("not_attempted", [], "")
    verdict = verdicts[-1]
    status, candidates = verdict.get("status"), [c for c in verdict.get("candidates") or [] if c]
    if status == "ambiguous":
        return "ambiguous", candidates, ""
    if status == "none":
        return ("one_candidate", candidates, "") if len(candidates) == 1 else ("no_match", [], "")
    if status == "unique":
        return "filing_refused", [], refused or "the matter resolved but no filing was recorded"
    return "search_failed", [], str(verdict.get("detail") or status)


def derive(kind: str, first: int, last: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """``placed_on``, ``issue``, ``candidates`` and the code's ``detail`` for
    the letter on pages ``first``..``last``, from the rows recorded for exactly
    those pages. See the module docstring for the table."""
    mine = [r for r in rows if _span(r) == (first, last)]
    filings = [r for r in mine if _filing(r)]
    if filings:
        filing = filings[-1]
        issue, detail = _placed_issue(kind, filing, mine)
        number = str(filing.get("matter_number") or "") or "(number not on record)"
        return {"placed_on": number, "issue": issue, "candidates": [], "detail": detail}
    issue, candidates, detail = _held(mine)
    return {"placed_on": "", "issue": issue, "candidates": candidates, "detail": detail}


def span_problem(first: int, last: int, rows: list[dict[str, Any]]) -> str | None:
    """The refusal when a document filed from this bundle overlaps the letter's
    pages without being exactly them: the record must describe the letters as
    they were filed."""
    for row in rows:
        lo, hi = _span(row)
        if not _filing(row) or not isinstance(lo, int) or not isinstance(hi, int) or (lo, hi) == (first, last):
            continue
        if lo <= last and first <= hi:
            return f"pages {lo}-{hi} were filed as one document; record that letter as pages {lo}-{hi}"
    return None


def kind_problem(kind: str, first: int, last: int, rows: list[dict[str, Any]]) -> str | None:
    """A letter staged as a vendor's bill is one: its kind cannot be recorded
    as anything else, because the record would then miss the expense check."""
    staged = any(r.get("tool") == ol.STAGE and _filing(r) and _span(r) == (first, last) for r in rows)
    if staged and kind != "vendor_bill":
        return f"pages {first}-{last} were staged as a vendor's bill; record their kind as vendor_bill"
    return None


__all__ = ["ACTS", "NO_ATTEMPT", "derive", "kind_problem", "span_problem"]
