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
provider, one paragraph per bill file, cited to that file's first page that
shows the date:

    MM/DD/YYYY
    <billing provider> | <the firm's last subsection heading>

    <service as billed> billed on MM/DD/YYYY. (FILE: <bill>, p. <x>)

The sentence states only what the bill page prints, so the claim audit grades
it against that page like any claim, and an entry holding nothing but that
sentence tells the attorney the date is known from a bill alone. It says no
more on purpose: "no visit note is in the records" is a statement about the
whole file that no cited page prints (the audit would grade it unsupported),
and as an ``[NTD: ...]`` note it would be new firm wording every checker prompt
must exempt, while the update lane takes every NTD out of an added entry
anyway (``meta_markers.process_notes``).

A date an entry already carries is covered, whatever that entry's provider
(bills and records name providers differently, so provider is no test). A bill
dated a day off its visit note gets its row: the house style keeps both dates
rather than reconciling them, and the writer's CONFLICTS block names the
disagreement. Amounts and billing codes never reach the row (map-system.md: no
charge amounts in entries). ``is_billed_row`` names such an entry for the
checks that must not count it as a record of a visit (``dos_check``) and for
an update, which drops one whose date the delivered chronology already carries
(``update/collect``).
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


_CITE_PARTS = re.compile(r"^\(FILE:\s*(?P<file>.+?),\s*p\.\s*(?P<page>\d+)")


def _by_file(rows: list[dict[str, str]]) -> dict[str, tuple[str, list[str]]]:
    """file -> (the citation of its first page showing the date, the services
    it bills). One file, one paragraph (the house rule), cited to the first
    page that shows the date: a ledger, its claim form and its EOB each print
    the same line, and a second page of one file adds nothing."""
    out: dict[str, tuple[int, str, list[str]]] = {}
    for r in rows:
        m = _CITE_PARTS.match(r["cite"])
        name, page = (m.group("file").strip(), int(m.group("page"))) if m else (r["cite"], 0)
        cur = out.get(name)
        if cur is None or (page and page < cur[0]):
            out[name] = (page, r["cite"], cur[2] if cur else [])
        svcs = out[name][2]
        if r["service"] and r["service"] not in svcs:
            svcs.append(r["service"])
    return {n: (c, svcs) for n, (_p, c, svcs) in out.items()}


def entry_text(iso: str, provider: str, rows: list[dict[str, str]], heading: str) -> str:
    """One billed date's entry in the writer's house format."""
    us = _us(iso)
    files = _by_file(rows)
    paras = [f"{'; '.join(svcs) or DEFAULT_SERVICE} billed on {us}. {cite}" for cite, svcs in files.values()]
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
    """True when an entry is a carried billed date and nothing else: every
    paragraph under its header is a billed sentence. An entry anything else
    was written into is a record of care."""
    text = (entry or "").strip()
    if DATE_HEAD.match(text) is None or text.count("\n") < 2:
        return False
    paras = [p.strip() for p in re.split(r"\n\s*\n", text.split("\n", 2)[2]) if p.strip()]
    return bool(paras) and all(_SENTENCE.match(p) for p in paras)


def heading_of(subsections: list[str] | tuple[str, ...] | None) -> str:
    """The firm's catch-all subsection: the last heading of its menu."""
    menu = [str(h).strip() for h in (subsections or []) if str(h).strip()]
    return menu[-1] if menu else "All Other Information"
