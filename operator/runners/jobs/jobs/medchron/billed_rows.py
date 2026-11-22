"""A date of service only a bill shows becomes a dated row. $0, no model.

WHY (2026-11-21). The writer is told to make no entry from a bill and to list
each date of service a bill evidences in its BILLING-DATES block
(``prompts/map-system.md``, BILLING-ONLY SOURCES). Until now no code read that
block: ``assemble`` wrote it to ``billing_dates.md`` and nothing else did, so
a visit the bill was the only record of reached no chronology. On a real
986-page job the firm received a chronology missing four therapy visits that
only the provider's billing showed, and added them by hand after delivery.

THE RULE, decided here and never by a model. A billed date of service that no
entry of the run is dated on becomes its own entry, one per date and billing
provider, cited to the bill page that shows it:

    MM/DD/YYYY
    <billing provider> | <the firm's last subsection heading>

    <service as billed> billed on MM/DD/YYYY. (FILE: <bill>, p. <x>)

    <wording.BILLED_NO_NOTE> (FILE: <bill>, p. <x>)

The billed sentence states only what the bill page prints, so the claim audit
can grade it against that page. That no visit note exists is a statement about
the whole file, not about the page, so it is the firm-wording note
(``wording.BILLED_NO_NOTE``), an ``[NTD: ...]`` span the audit never grades.

A date an entry already carries is covered, whatever that entry's provider:
the row says no visit note exists, and on a date an entry carries that would
not be safe to say (bills and records name providers differently). Amounts and
billing codes never reach the row (map-system.md: no charge amounts in
entries). The update lane takes the note out of an added entry, as it does
every ``[NTD: ...]`` (``meta_markers.process_notes``): there the billed
sentence stands alone. ``is_billed_row`` names such an entry for the checks that must not
count it as a record of a visit (``dos_check``) and for an update, which drops
one whose date the delivered chronology already carries (``update/collect``).
"""

from __future__ import annotations

import re
from typing import Any

from .. import wording
from ..text_rules import DATE_HEAD

#: One BILLING-DATES line: `YYYY-MM-DD | provider | [service |] (FILE: ...)`.
#: The service column is new (2026-11-21); a saved map without it still parses.
LINE = re.compile(
    r"^\s*(?:[-*]\s*)?(?P<iso>\d{4}-\d{2}-\d{2})\s*\|\s*(?P<prov>[^|]*?)\s*\|"
    r"(?:\s*(?P<svc>[^|]*?)\s*\|)?\s*(?P<cite>\(FILE:.*\))\s*$"
)
_AMOUNT = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?|\b\d[\d,]*\.\d{2}\b")
#: CPT (5 digits, or 4 and a letter) and HCPCS (a letter and 4 digits) codes.
_CODE = re.compile(r"\b(?:\d{5}|\d{4}[A-Z]|[A-Z]\d{4})\b")
#: A carried billed sentence, cited by file name (assemble) or by exhibit (later).
_SENTENCE = re.compile(r"^(?P<svc>.+?) billed on (?P<us>\d{2}/\d{2}/\d{4})\.\s*\((?:FILE:|Exhibit ).*\)\s*$")
DEFAULT_SERVICE = "Services"


def parse(block: str) -> list[dict[str, str]]:
    """The billed dates a BILLING-DATES block lists, each `{iso, provider,
    service, cite}`. A line that is not one (`none in this chunk`, prose) is
    skipped: the block is the writer's, and only a dated, cited line is a
    billed date."""
    out: list[dict[str, str]] = []
    for line in (block or "").splitlines():
        m = LINE.match(line)
        if m is None:
            continue
        out.append(
            {
                "iso": m.group("iso"),
                "provider": m.group("prov").strip(),
                "service": service(m.group("svc") or ""),
                "cite": m.group("cite").strip(),
            }
        )
    return out


def service(text: str) -> str:
    """The service as billed, without amounts or billing codes."""
    s = _CODE.sub("", _AMOUNT.sub("", text or ""))
    s = re.sub(r"\s+", " ", s).strip(" ,;:-")
    if not re.search(r"[A-Za-z]{3}", s):
        return ""
    return s[0].upper() + s[1:]


def _key(provider: str) -> str:
    return re.sub(r"[^a-z0-9]", "", provider.lower())


def _us(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{m}/{d}/{y}"


def entry_text(iso: str, provider: str, rows: list[dict[str, str]], heading: str) -> str:
    """One billed date's entry in the writer's house format."""
    us = _us(iso)
    by_cite: dict[str, list[str]] = {}
    for r in rows:
        svcs = by_cite.setdefault(r["cite"], [])
        if r["service"] and r["service"] not in svcs:
            svcs.append(r["service"])
    paras = []
    for cite, svcs in by_cite.items():
        what = "; ".join(svcs) or DEFAULT_SERVICE
        paras.append(f"{what} billed on {us}. {cite}")
    paras.append(f"{wording.BILLED_NO_NOTE} {next(iter(by_cite))}")
    prov = provider or wording.PROVIDER_NOT_STATED
    return f"{us}\n{prov} | {heading}\n\n" + "\n\n".join(paras)


def carry(billed: list[dict[str, Any]], dated: set[str], heading: str) -> list[dict[str, Any]]:
    """The rows to add: each billed date no entry is dated on, grouped by date
    and billing provider, as `{iso, provider, text, rows}`, in date order.
    `dated` holds the ISO dates the run's entries carry."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    names: dict[tuple[str, str], str] = {}
    for b in billed:
        if b["iso"] in dated:
            continue
        k = (b["iso"], _key(b["provider"]))
        groups.setdefault(k, []).append(b)
        names.setdefault(k, b["provider"])
    return [
        {"iso": k[0], "provider": names[k], "text": entry_text(k[0], names[k], rows, heading), "rows": rows}
        for k, rows in sorted(groups.items())
    ]


def is_billed_row(entry: str) -> bool:
    """True when an entry is a carried billed date and nothing else: at least
    one billed sentence, and every paragraph under its header a billed
    sentence or the firm's note. An entry anything else was written into is a
    record of care. The note is not required: the update lane takes every
    ``[NTD: ...]`` out of an added entry (``meta_markers.process_notes``), and
    the billed sentence alone still says what the row is."""
    text = (entry or "").strip()
    if DATE_HEAD.match(text) is None or text.count("\n") < 2:
        return False
    paras = [p.strip() for p in re.split(r"\n\s*\n", text.split("\n", 2)[2]) if p.strip()]
    billed = [p for p in paras if _SENTENCE.match(p)]
    return bool(billed) and all(p in billed or p.startswith(wording.BILLED_NO_NOTE) for p in paras)


def heading_of(subsections: list[str] | tuple[str, ...] | None) -> str:
    """The firm's catch-all subsection: the last heading of its menu."""
    menu = [str(h).strip() for h in (subsections or []) if str(h).strip()]
    return menu[-1] if menu else "All Other Information"
