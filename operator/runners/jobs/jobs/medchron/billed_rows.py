"""A date of service only a bill shows becomes a dated row. $0, no model.

WHY (2026-11-21). On a real 986-page job the firm received a chronology
missing four therapy visits that only the provider's billing showed, and added
them by hand after delivery. The writer makes no entry from a bill
(``prompts/map-system.md``, BILLING-ONLY SOURCES), and the date-of-service
check (``dos_check``) named such a date only on the billing worksheet.

THE SOURCE is the deterministic billing extraction ``dos_check`` already reads
(``billing_extract.jsonl``), never the writer's BILLING-DATES block: the same
visit filter (a bill or ledger line item, not a payment, adjustment or a
non-positive charge, ``dos_check._item_is_visit``), the same other-patient
quarantine (``billing_chart.quarantined``), and the same coverage test
(``dos_check.classify``). Only a billed visit that test calls
``billed_no_record`` is carried: no entry is dated on it, no medical-record
page on file carries the date, it is after the incident and nobody explained
it. A bill dated a day off its visit note is such a visit (no record page
carries the billed date); the house style keeps both dates.

THE ROW, decided here. One entry per date and provider, one sentence per
service, each citing the page whose line item shows that service on that date
(never a service read off another page):

    MM/DD/YYYY
    <provider> | <the firm's last subsection heading>

    <service> on MM/DD/YYYY, date of service per the bill. (FILE: <bill>, p. <x>)

The provider is a run entry's provider when the bill names it (the firm's
``billing.provider_match``, an exact name, or every distinctive word of the
bill's name in the entry's), so the row joins that provider's lane; otherwise
the bill's own provider. The service is the line item's description without amounts, codes
(CPT/HCPCS and modifiers, revenue, NDC, ICD) or units. A row whose bill file
is not one of the unit's files is dropped and logged, never left as an
unresolved citation (an update refuses one). ``is_billed_row`` names such an
entry for the checks that must not count it as a record of a visit
(``dos_check``), and an update drops one whose date the delivered chronology
already carries (``update/collect``).
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from .. import wording
from ..stages.base import read_jsonl
from ..stages.billing_chart import dt, quarantined
from ..text_rules import DATE_HEAD
from . import dos_check as DC

DEFAULT_SERVICE = "Services"
#: A carried sentence, cited by file name (assemble) or by exhibit (later).
_SENTENCE = re.compile(r"^.+? on \d{2}/\d{2}/\d{4}, date of service per the bill\.\s*\((?:FILE:|Exhibit ).*\)\s*$")

# The service, in this order: labels, NDC, money, codes, units, stray numbers.
_STRIP = [
    re.compile(r"(?i)\b(?:CPT|HCPCS|Rev(?:enue)?(?: code)?|ICD(?:-10)?(?:-CM)?|NDC|Dx|Code|Qty)\b\s*[:#]?"),
    re.compile(r"\b\d{4,5}-\d{3,4}-\d{1,2}\b|\b\d{11}\b"),  # NDC
    re.compile(r"\$\s*\d[\d,]*(?:\.\d{1,2})?|(?<![\w.-])\d{1,3}(?:,\d{3})*\.\d{2}(?![\w-])"),  # money
    re.compile(r"\b(?:\d{5}|\d{4}[A-Z]|[A-Z]\d{4})(?:[A-Z]{2})?(?:-[A-Z0-9]{2})*\b"),  # CPT/HCPCS + modifiers
    re.compile(r"\b[A-TV-Z]\d[0-9A-Z](?:\.[0-9A-Z]{1,4}|[0-9][0-9A-Z]{0,3})\b"),  # ICD-10
    re.compile(r"(?i)\bx\s?\d+(?:\s*units?)?\b|\b\d+\s*units?\b|\bunits?\s*\d+\b"),  # units
    re.compile(r"(?<![\w-])\d[\d,]*(?:\.\d+)?(?![\w-])"),  # revenue codes, counts, bare amounts
]


def service(text: str | None) -> str:
    """The service as billed, without amounts, codes or units."""
    s = str(text or "")
    for rx in _STRIP:
        s = rx.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip(" ,;:-/#()")
    if not re.search(r"[A-Za-z]{3}", s):
        return ""
    return s[0].upper() + s[1:]


def billed_items(slug_dir: Path, patient: str | None) -> list[dict[str, Any]]:
    """Every billed visit line item with its page: `{iso, provider, file,
    page, service}`. ``dos_check.billed_dates``'s filter; an item with no page
    number shows no page to cite and is left to the worksheet."""
    out: list[dict[str, Any]] = []
    for row in read_jsonl(slug_dir / "billing_extract.jsonl"):
        for c in row.get("chunks") or []:
            if not isinstance(c, dict) or "FAILED_PAGE" in c:
                continue
            if c.get("doc_type") not in DC.DOS_TYPES or quarantined(c, patient):
                continue
            prov = str(c.get("provider") or "").strip()
            for i in c.get("line_items") or []:
                if not isinstance(i, dict) or not DC._item_is_visit(i):
                    continue
                iso, page = dt(i.get("date")), str(i.get("page") or "")
                if iso is None or not page.isdigit() or int(page) < 1:
                    continue
                svc = service(i.get("description"))
                out.append({"iso": iso, "provider": prov, "file": str(row.get("file") or ""), "page": int(page),
                            "service": svc})  # fmt: skip
    return out


def no_record_items(
    slug_dir: Path,
    run_dir: Path,
    incident_iso: str,
    patient: str | None,
    by_date: dict[str, list[str]],
    match: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """The billed items whose visit ``dos_check.classify`` calls
    ``billed_no_record`` against `by_date` (ISO date -> entry providers)."""
    items = billed_items(slug_dir, patient)
    if not items:
        return []
    pages = DC.record_pages(slug_dir, DC._billing_pages(slug_dir))
    explained = DC.explained_dates(run_dir)
    cls = {
        k: DC.classify(k[0], k[1], by_date, incident_iso, explained, pages, match)[0]
        for k in {(i["iso"], i["provider"]) for i in items}
    }
    return [i for i in items if cls[(i["iso"], i["provider"])] == "billed_no_record"]


#: Words a business name carries for its legal form, never its identity.
_FORM = frozenset("llc inc pllc pc pa ltd co corp the and of".split())


def _name_words(name: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", name.lower()) if w not in _FORM}


def _same_provider(entry_prov: str, bill_prov: str, match: dict[str, list[str]]) -> bool:
    """Across dates, the bill names the entry's provider only when the firm's
    ``provider_match`` map lists it, the names are equal, or every word of the
    bill's name but its legal form is in the entry's: one shared word, which
    ``dos_check`` accepts on the same date, would put "Saguaro Imaging" in
    "Saguaro Therapy"'s lane."""
    labels = [entry_prov, *match.get(entry_prov, [])]
    if any(lab.strip().lower() == bill_prov.strip().lower() for lab in labels):
        return True
    want = _name_words(bill_prov)
    return bool(want) and any(want <= _name_words(lab) for lab in labels)


def row_provider(bill_prov: str, providers: list[str], match: dict[str, list[str]]) -> str:
    """The run's own provider the bill names, the most used one first, so the
    row joins that provider's lane; else the bill's provider."""
    if bill_prov:
        hits = Counter(p for p in providers if p and _same_provider(p, bill_prov, match))
        if hits:
            return hits.most_common(1)[0][0]
    return bill_prov or wording.PROVIDER_NOT_STATED


def _us(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{m}/{d}/{y}"


def entry_text(iso: str, provider: str, items: list[dict[str, Any]], heading: str) -> str:
    """One entry: a sentence per distinct service, cited to the first page
    (by file, then page) whose line item shows it."""
    us = _us(iso)
    first: dict[str, dict[str, Any]] = {}
    for i in sorted(items, key=lambda x: (x["file"], x["page"])):
        first.setdefault(i["service"] or f"\x00{i['file']}\x00{i['page']}", i)
    paras = [
        f"{i['service'] or DEFAULT_SERVICE} on {us}, date of service per the bill. (FILE: {i['file']}, p. {i['page']})"
        for i in first.values()
    ]
    return f"{us}\n{provider} | {heading}\n\n" + "\n\n".join(paras)


def carry(
    items: list[dict[str, Any]],
    providers: list[str],
    match: dict[str, list[str]],
    heading: str,
    resolvable: Callable[[str], bool],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(rows to add as `{iso, provider, text, items}` in date order, items
    dropped because their bill file is not a unit file)."""
    keep = [i for i in items if resolvable(i["file"])]
    dropped = [i for i in items if not resolvable(i["file"])]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for i in keep:
        groups.setdefault((i["iso"], row_provider(i["provider"], providers, match)), []).append(i)
    rows = [
        {"iso": iso, "provider": prov, "text": entry_text(iso, prov, grp, heading), "items": grp}
        for (iso, prov), grp in sorted(groups.items())
    ]
    return rows, dropped


def is_billed_row(entry: str) -> bool:
    """True when every paragraph under an entry's header is a carried billed
    sentence. An entry anything else was written into is a record of care."""
    text = (entry or "").strip()
    if DATE_HEAD.match(text) is None or text.count("\n") < 2:
        return False
    paras = [p.strip() for p in re.split(r"\n\s*\n", text.split("\n", 2)[2]) if p.strip()]
    return bool(paras) and all(_SENTENCE.match(p) for p in paras)


def heading_of(subsections: list[str] | tuple[str, ...] | None) -> str:
    """The firm's catch-all subsection: the last heading of its menu."""
    menu = [str(h).strip() for h in (subsections or []) if str(h).strip()]
    return menu[-1] if menu else "All Other Information"
