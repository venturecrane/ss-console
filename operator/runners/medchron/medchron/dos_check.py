"""Did every billed date of service reach the chronology? $0, no model.

The claim audit proves each claim traces to its cited page, and the coverage
gate proves each pulled document is cited or excluded. Neither can see a date
of service that never became a claim: a 40-page record holding six visits and
a chronology mentioning five passes both. This check closes that gap with the
one list of dates of service the run already extracts deterministically-ish,
the billing stage's line items, and the page text on disk.

A billed visit, one per (date, provider), is placed in exactly one class, in
this order:

  in_chronology     an entry on that date names the billing provider (the
                    firm's `billing.provider_match` map, else a shared
                    distinctive name word).
  pre_incident      before the incident: `summarize` folds those entries into
                    the Prior Medical History block, so absence is by design.
  explained         a person recorded why (`billed_dates_explained.json`,
                    written by `medchron explain-date`).
  billed_no_record  no non-billing page in the file carries the date. The
                    composer is told to keep billing-only dates OUT of entries
                    (prompts/map-system.md), so this is a records gap in the
                    firm's file, not a chronology error.
  provider_unmatched  entries carry the date, none names the billing provider,
                    and the medical records DO name it: two providers seen one
                    day and the chronology may hold only one. (A billing name
                    no record page uses cannot be in a record-built
                    chronology, so the covered date counts as in_chronology.)
                    Reported, never held, because names differ between bills
                    and records.
  missed_visit      a record page carries the date and no entry does. This is
                    the class the check exists for.

A billed date is a line item on a MEDICAL_BILL or LEDGER (never a vendor
invoice, lien, certificate or records-only page), with payments, adjustments
and non-positive charges dropped, deduplicated to (date, provider). A bill
with no line items contributes its printed first and last dates, because the
itemless bills are the serial-care ledgers where a missed visit would hide.
Undated items, unreadable dates, itemless bills and failed pages are counted,
never dropped, and so are the bill chunks read and a missing extraction: zero
billed visits because nothing was billed must not look like zero because the
billing stage produced nothing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .stages.base import read_json, read_jsonl
from .stages.billing_chart import ADJ_LABEL, dt, quarantined
from .stages.build_doc import parse_entries

DOS_TYPES = {"MEDICAL_BILL", "LEDGER"}
NOT_A_VISIT = re.compile(r"payment|paid|pmt|refund|credit|balance|statement|posted|transfer|interest", re.I)
PAGE = re.compile(r"\[p\.(\d+)\]")
EXPLAINED_FILE = "billed_dates_explained.json"
CLASSES = ("in_chronology", "pre_incident", "explained", "provider_unmatched", "billed_no_record", "missed_visit")
# Words that name no provider in particular, so sharing one proves nothing.
GENERIC = frozenset(
    "medical center centers health healthcare hospital clinic clinics group llc inc pllc the and "
    "services associates care physicians physician imaging radiology therapy physical".split()
)


def _charge(value: Any) -> float | None:
    t = str(value or "").replace("$", "").replace(",", "").strip()
    neg = t.startswith("(") and t.endswith(")")
    try:
        v = float(t.strip("()"))
    except ValueError:
        return None
    return -v if neg else v


def _item_is_visit(item: dict[str, Any]) -> bool:
    desc = str(item.get("description") or "")
    if ADJ_LABEL.search(desc) or NOT_A_VISIT.search(desc):
        return False
    c = _charge(item.get("charge"))
    return c is None or c > 0


def billed_dates(slug_dir: Path, patient: str | None) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, int]]:
    """{(iso, provider): {file, page}} plus data-quality counts."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    src = slug_dir / "billing_extract.jsonl"
    quality = {
        "billing_extract_missing": 0 if src.is_file() else 1,
        "bill_chunks": 0,
        "undated_items": 0,
        "unreadable_dates": 0,
        "itemless_bills": 0,
        "failed_pages": 0,
    }
    for row in read_jsonl(src):
        for c in row.get("chunks") or []:
            if "FAILED_PAGE" in c:
                quality["failed_pages"] += 1
                continue
            if not isinstance(c, dict) or c.get("doc_type") not in DOS_TYPES or quarantined(c, patient):
                continue
            quality["bill_chunks"] += 1
            prov = str(c.get("provider") or "").strip()
            items = [i for i in c.get("line_items") or [] if isinstance(i, dict)]
            if not items:
                quality["itemless_bills"] += 1
                items = [
                    {"date": c.get(k), "charge": None, "page": c.get("page_first")} for k in ("date_first", "date_last")
                ]
            for i in items:
                if not _item_is_visit(i):
                    continue
                iso = dt(i.get("date"))
                if iso is None:
                    quality["unreadable_dates" if i.get("date") else "undated_items"] += 1
                    continue
                out.setdefault((iso, prov), {"file": row.get("file"), "page": i.get("page")})
    return out, quality


def _date_forms(iso: str) -> tuple[str, ...]:
    y, m, d = iso.split("-")
    mi, di = int(m), int(d)
    return (f"{m}/{d}/{y}", f"{mi}/{di}/{y}", f"{mi}/{di}/{y[2:]}", f"{m}/{d}/{y[2:]}", iso)


def record_pages(slug_dir: Path, billing_pages: set[tuple[str, int]]) -> dict[str, list[tuple[int, str]]]:
    """Every extracted page that is not a billing page: name -> [(page, text)]."""
    pages: dict[str, list[tuple[int, str]]] = {}
    for rec in read_jsonl(slug_dir / "extracted.jsonl"):
        path = slug_dir / "text" / f"{rec.get('id')}.txt"
        if not path.is_file():
            continue
        name = str(rec.get("name") or "")
        parts = PAGE.split(path.read_text(encoding="utf-8", errors="replace"))
        for num, body in zip(parts[1::2], parts[2::2]):
            if (name, int(num)) not in billing_pages:
                pages.setdefault(name, []).append((int(num), body))
    return pages


def _billing_pages(slug_dir: Path) -> set[tuple[str, int]]:
    out: set[tuple[str, int]] = set()
    for row in read_jsonl(slug_dir / "billing_extract.jsonl"):
        for c in row.get("chunks") or []:
            if isinstance(c, dict) and c.get("doc_type") and c.get("doc_type") != "RECORDS_ONLY":
                for i in c.get("line_items") or []:
                    if isinstance(i, dict) and str(i.get("page") or "").isdigit():
                        out.add((str(row.get("file") or ""), int(i["page"])))
                for t in c.get("printed_totals") or []:
                    if isinstance(t, dict) and str(t.get("page") or "").isdigit():
                        out.add((str(row.get("file") or ""), int(t["page"])))
    return out


def _date_pattern(iso: str) -> re.Pattern[str]:
    # Bounded on both sides: "1/2/26" must not match inside "11/2/26" or
    # "1/2/2601", which would report a record page for a different date.
    alts = "|".join(re.escape(f) for f in _date_forms(iso))
    return re.compile(rf"(?<![\d/])(?:{alts})(?![\d/])")


def _on_record_page(iso: str, pages: dict[str, list[tuple[int, str]]]) -> tuple[str, int] | None:
    pat = _date_pattern(iso)
    for name, plist in pages.items():
        for num, body in plist:
            if pat.search(body):
                return name, num
    return None


def _nearest_days(iso: str, entry_isos: list[str]) -> int | None:
    """Days to the closest chronology entry of any provider: a hint for the
    reviewer ("dated differently" vs "absent"), never an excuse."""
    from datetime import date

    if not entry_isos:
        return None
    d0 = date.fromisoformat(iso)
    return min(abs((date.fromisoformat(e) - d0).days) for e in entry_isos)


def _words(name: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", name.lower()) if w not in GENERIC}


def _names_provider(entry_prov: str, bill_prov: str, match: dict[str, list[str]]) -> bool:
    """Does a chronology entry's provider name the billing provider? The
    firm's authored map first, then any shared distinctive word. A bill with
    no provider cannot be told apart, so any entry that day names it."""
    labels = [entry_prov, *match.get(entry_prov, [])]
    if not bill_prov or any(lab.strip().lower() == bill_prov.lower() for lab in labels):
        return True
    return bool(_words(bill_prov) & set().union(*(_words(lab) for lab in labels)))


def _named_in_records(bill_prov: str, pages: dict[str, list[tuple[int, str]]]) -> bool:
    """Does any medical-record page name the billing provider (every
    distinctive word of it)? A billing company the records never mention (a
    physician group invoicing for hospital care) cannot be named by a
    chronology built from those records, so it is no evidence of a miss."""
    want = _words(bill_prov)
    return bool(want) and any(want <= _words(t) for plist in pages.values() for _, t in plist)


def check(
    slug_dir: Path,
    run_dir: Path,
    incident_iso: str,
    patient: str | None,
    entries_text: str,
    provider_match: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """The report: counts per class, the data-quality counts, one row per
    missed visit (file and page of the record carrying it, nearest entry) and
    one per provider_unmatched visit."""
    billed, quality = billed_dates(slug_dir, patient)
    by_date: dict[str, list[str]] = {}
    for e in parse_entries(entries_text):
        by_date.setdefault(e["iso"], []).append(e["provider"])
    entry_isos = sorted(by_date)
    match = provider_match or {}
    explained = {str(r.get("date")) for r in read_json(run_dir / EXPLAINED_FILE, []) or [] if isinstance(r, dict)}
    pages = record_pages(slug_dir, _billing_pages(slug_dir))
    counts = dict.fromkeys(CLASSES, 0)
    missed: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for iso, prov in sorted(billed):
        if iso in by_date and (
            any(_names_provider(p, prov, match) for p in by_date[iso]) or not _named_in_records(prov, pages)
        ):
            cls = "in_chronology"
        elif iso < incident_iso:
            cls = "pre_incident"
        elif iso in explained:
            cls = "explained"
        elif iso in by_date:
            cls = "provider_unmatched"
            unmatched.append({"date": iso, "billing_provider": prov, "entry_providers": by_date[iso]})
        else:
            hit = _on_record_page(iso, pages)
            cls = "missed_visit" if hit else "billed_no_record"
            if hit:
                missed.append(
                    {
                        "date": iso,
                        "record_file": hit[0],
                        "record_page": hit[1],
                        "nearest_entry_days": _nearest_days(iso, entry_isos),
                    }
                )
        counts[cls] += 1
    return {
        "billed_dates": sum(counts.values()),
        "classes": counts,
        "quality": quality,
        "missed_visits": missed,
        "provider_unmatched": unmatched,
    }


def write(run_dir: Path, report: dict[str, Any]) -> Path:
    path = run_dir / "dos_report.json"
    path.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    return path
