"""Put the audited new entries into the delivered chronology. $0, no model.

Inputs: the delivered chronology's markdown (proven by `docx_md.prove` to
render to the delivered document), the AUDIT document (the new entries only,
audited and repaired, its gate passed, with the audited diagnosis claims and
the dated paragraph of added prior history ahead of its entries).

Output, in build_doc's shape:

* every delivered entry byte-identical, in its delivered order, each new entry
  placed after the last delivered entry dated on or before it;
* the delivered preamble verbatim: its Prior Medical History as delivered,
  then the audited "Additional prior history from records received MM-DD-YY"
  paragraph when the update adds one, then its note that pre-incident
  encounters were not itemized (unless the new entries itemize them);
* the Treatment Timeline computed from the whole entry set (build_doc's
  arithmetic; a delivered row that disagreed with its own entries is reported
  lane by lane, never kept);
* Diagnostic Highlights: the delivered rows, plus a row for each diagnosis
  claim the audit kept (``update/icd.py``; descriptors from the vendored CMS
  tables, never a model, and never blank);
* the delivered Exhibit List plus a row per new exhibit an added entry cites,
  titled the delivered way, and a row naming each firm-numbered file in the
  folder the list never named;
* the delivered Records Reviewed section plus the update's own paragraph.

Refuses (ValueError) when a delivered entry is not carried verbatim or in its
delivered order, the pre-incident note is not carried as it must be, or a new citation names an
exhibit that is neither delivered nor new.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Callable

from . import body as B, icd

TIMELINE_HEAD = "| Medical Provider | Treatment Period | Visits | Reference |"


def _period_has(period: str, mmddyyyy: str) -> bool:
    ds = [p.strip() for p in period.split(" - ")]

    def k(d: str) -> str:
        return d[6:10] + d[0:2] + d[3:5]

    return k(ds[0]) <= k(mmddyyyy) <= k(ds[-1])


def exhibit_providers(exhibit_section: str) -> dict[int, str]:
    """{exhibit: provider} off Exhibit List rows ('| 4 | <Provider> - MM-DD-YYYY ...')."""
    out: dict[int, str] = {}
    for r in B.table_rows(exhibit_section)[1:]:
        c = [x.strip() for x in r.strip().strip("|").split("|")]
        if c and c[0].isdigit() and len(c) > 1:
            out[int(c[0])] = re.split(r" - \d{2}-\d{2}-\d{4}| -\s+\(| \(", c[1])[0].strip()
    return out


def lane_namer(
    headings: set[str], delivered_rows: list[str], exhibit_titles: dict[int, str]
) -> tuple[Callable[[str], str], list[dict[str, Any]]]:
    """name_of(entry) -> the lane an entry belongs to, and the resolution log.

    The provider line's left side, unless it is a subsection heading (an entry
    composed with no provider name): then the delivered row citing the same
    page on a period covering the date, else the delivered lane named for the
    cited exhibit's provider covering the date, else the exhibit's provider
    from the Exhibit List. Each such resolution is logged; none is guessed."""
    rows = []
    for r in delivered_rows:
        c = [x.strip() for x in r.strip().strip("|").split("|")]
        if len(c) >= 4:
            rows.append({"lane": c[0], "period": c[1], "ref": c[3]})
    log: list[dict[str, Any]] = []
    seen: dict[str, str] = {}

    def resolve(e: str, p: str) -> str:
        d = e.splitlines()[0][:10]
        c = B.CITE.search(e)
        ref = c.group(0)[1:-1] if c else ""
        exprov = exhibit_titles.get(int(c.group(1)), "") if c else ""
        hit = next((r["lane"] for r in rows if r["ref"] == ref and _period_has(r["period"], d)), None)
        src = "delivered row citing the same page"
        if hit is None:
            hit = next(
                (r["lane"] for r in rows if exprov and r["lane"] == exprov and _period_has(r["period"], d)), None
            )
            src = "delivered lane of the cited exhibit's provider"
        if hit is None and exprov:
            hit, src = exprov, "cited exhibit's provider (Exhibit List)"
        if hit is None:
            hit, src = p, "UNRESOLVED: heading kept"
        log.append({"date": d, "slot": p, "cites": ref, "lane": hit, "source": src})
        return hit

    def name(e: str) -> str:
        for ln in e.splitlines()[1:5]:
            if "|" in ln:
                left = ln.split("|")[0].strip()
                if left and left not in headings:
                    return left
        p = B.provider_of(e)
        return p if p not in headings else resolve(e, p)

    def name_of(e: str) -> str:
        if e not in seen:
            seen[e] = name(e)
        return seen[e]

    return name_of, log


def provider_rows(entries: list[str], canon: Callable[[str], str], name_of: Callable[[str], str]) -> list[str]:
    """Treatment Timeline rows exactly as build_doc computes them."""
    es = []
    for e in entries:
        c = B.CITE.search(e)
        es.append({"iso": B.iso(e), "provider": name_of(e), "ref": c.group(0)[1:-1] if c else ""})
    es.sort(key=lambda x: x["iso"])
    agg: dict[str, dict[str, Any]] = defaultdict(lambda: {"dates": [], "ref": ""})
    for e in es:
        a = agg[canon(e["provider"])]
        a["dates"].append(e["iso"])
        if not a["ref"]:
            a["ref"] = e["ref"]
    rows = []
    for prov, a in agg.items():
        ds = sorted(a["dates"])
        period = f"{ds[0][5:7]}/{ds[0][8:10]}/{ds[0][:4]}" + (
            f" - {ds[-1][5:7]}/{ds[-1][8:10]}/{ds[-1][:4]}" if ds[-1] != ds[0] else ""
        )
        rows.append((ds[0], prov, period, len(ds), a["ref"]))
    rows.sort()
    return [f"| {p} | {per} | {n} | {ref} |" for _, p, per, n, ref in rows]


def lane_diff(before: list[str], after: list[str]) -> list[dict[str, Any]]:
    """[{lane, before, after}] for every Treatment Timeline lane that differs."""

    def lanes(rows: list[str]) -> dict[str, dict[str, Any]]:
        out = {}
        for r in rows:
            c = [x.strip() for x in r.strip().strip("|").split("|")]
            if len(c) >= 4:
                out[c[0]] = {"period": c[1], "visits": c[2], "reference": c[3]}
        return out

    b, a = lanes(before), lanes(after)
    return [
        {"lane": k, "before": b.get(k), "after": a.get(k)}
        for k in [*b, *[k for k in a if k not in b]]
        if b.get(k) != a.get(k)
    ]


def _mdY(isod: str) -> str:
    return f"{isod[5:7]}-{isod[8:10]}-{isod[:4]}"


def exhibit_titles_for(
    new_exhibits: list[dict[str, Any]], new_ents: list[str], name_of: Callable[[str], str]
) -> tuple[dict[int, str], list[int]]:
    """({number: final title}, [numbers no added entry cites]). A new exhibit
    is titled the delivered way: the provider most entries citing it name,
    and the first and last date they carry."""
    retitled: dict[int, str] = {}
    uncited: list[int] = []
    for r in sorted(new_exhibits, key=lambda x: x["exhibit"]):
        n = r["exhibit"]
        es = [e for e in new_ents if n in B.cited(e)]
        if not es:
            uncited.append(n)
            continue
        prov = Counter(name_of(e) for e in es).most_common(1)[0][0]
        ds = sorted(B.iso(e) for e in es)
        span = _mdY(ds[0]) + ("" if ds[-1] == ds[0] else " - " + _mdY(ds[-1]))
        retitled[n] = re.sub(r"[\\/:*?\"<>|]+", " ", f"{prov} - {span} (Medical Records)").strip()
    return retitled, uncited


def _notes(text: str) -> list[str]:
    return [m.group(0).strip() for m in B.PRE_NOTE.finditer(text or "")]


def _check_carried(md: str, base_ents: list[str], want_notes: list[str]) -> list[str]:
    _, out_ents = B.entries_of(md)
    oc = Counter(out_ents)
    lost = [e[:60] for e, n in Counter(base_ents).items() if oc[e] < n]
    if lost:
        raise ValueError(f"REFUSED: {len(lost)} delivered entr(y/ies) not carried verbatim: {lost[:3]}")
    if _notes(md) != want_notes:
        raise ValueError(
            f"REFUSED: the pre-incident note is not as it must be ({len(_notes(md))} in the update, "
            f"{len(want_notes)} required)"
        )
    base = Counter(base_ents)
    if [e for e in out_ents if e in base] != base_ents:
        raise ValueError("REFUSED: delivered entries are not in their delivered order")
    return out_ents


_ISO_SPAN = re.compile(r"(\d{4}-\d{2}-\d{2})\s+(?:to|-|through)\s+(\d{4}-\d{2}-\d{2})")
_MDY_SPAN = re.compile(r"(\d{2})/(\d{2})/(\d{4})\s+(?:to|-|through)\s+(\d{2})/(\d{2})/(\d{4})")


def note_span(note: str) -> tuple[int, str, str] | None:
    """(N, first iso, last iso) the note states, or None when it states no span
    this can read (then nothing is taken as itemized)."""
    n = re.search(r"\[NTD: (\d+) additional", note)
    m = _ISO_SPAN.search(note)
    if n and m:
        return int(n.group(1)), m.group(1), m.group(2)
    m = _MDY_SPAN.search(note)
    if n and m:
        g = m.groups()
        return int(n.group(1)), f"{g[2]}-{g[0]}-{g[1]}", f"{g[5]}-{g[3]}-{g[4]}"
    return None


def itemizes(note: str, new_ents: list[str]) -> bool:
    """True only when the update's OWN new entries itemize what the note
    says was not: at least N new entries dated inside the span it states.
    An update reads new documents (pages already read in a delivered exhibit
    are not read again), so this is rare; anything less keeps the note."""
    span = note_span(note)
    if span is None:
        return False
    n, a, b = span
    return sum(1 for e in new_ents if a <= B.iso(e) <= b) >= n


#: The dated paragraph of prior history an update adds under the delivered one.
ADDED_HISTORY = re.compile(r"(?m)^Additional prior history from records received \d{2}-\d{2}-\d{2}\s*$")


def _preamble(pre: str, addition: str, new_ents: list[str]) -> tuple[str, bool, list[str], list[str]]:
    """(preamble, prior history added, notes carried, notes the update itemized).

    The delivered Prior Medical History is CARRIED verbatim; the audited
    paragraph of prior history the update adds goes after it, under its own
    dated line. The delivered note that pre-incident encounters were reviewed
    and not itemized is CARRIED verbatim after both, where build_doc puts it,
    unless the update's own entries itemize those encounters. Deleting it
    would leave the firm's document silent on encounters it no longer lists."""
    notes = _notes(pre)
    gone = [n for n in notes if itemizes(n, new_ents)]
    keep = [n for n in notes if n not in gone]
    pre = B.PRE_NOTE.sub("", pre)
    addition = B.PRE_NOTE.sub("", addition or "").strip("\n")
    added = bool(ADDED_HISTORY.search(addition))
    if added:
        pre = pre.rstrip("\n") + ("\n\n" if pre.strip() else "") + addition + "\n"
    if keep:
        pre = pre.rstrip("\n") + ("\n\n" if pre.strip() else "") + "\n\n".join(keep) + "\n"
    return pre, added, keep, gone


def _sections_md(
    b: dict[str, str], timeline: list[str], table: list[str], body: str, ex_sec: str, rr: str | None
) -> str:
    return (
        b["title"]
        + B.SECTIONS[0]
        + "\n\n"
        + "\n".join(timeline)
        + "\n\n"
        + B.SECTIONS[1]
        + "\n\n"
        + "\n".join(table)
        + "\n\n"
        + B.SECTIONS[2]
        + "\n"
        + body
        + B.SECTIONS[3]
        + ex_sec
        + (B.SECTIONS[4] + rr if rr is not None else "")
    )


def _split_audit_pre(audit_pre: str) -> tuple[str, list[tuple[str, str, str]]]:
    """(the dated prior-history paragraph or '', the audited diagnosis
    claims). Anything else ahead of the first new entry refuses: the audit
    document holds nothing else, and text this cannot place must not vanish."""
    rest, claims = icd.split_claims(B.PRE_NOTE.sub("", audit_pre or ""))
    if rest.strip() and not ADDED_HISTORY.search(rest):
        raise ValueError("REFUSED: the audit document carries text ahead of its entries that is not the added history")
    return rest, claims


def assemble(
    base_md: str,
    audit_md: str,
    *,
    describe: icd.Resolve,
    canon: Callable[[str], str],
    headings: set[str],
    new_exhibits: list[dict[str, Any]],
    update_note: Callable[[dict[int, str]], str] | None = None,
    folder: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """(updated markdown, report). Raises ValueError on any refusal.
    `folder`: the chronology folder's numbered files (the update plan's
    exhibits), so a firm-numbered file the delivered list never named gets a
    row and may be cited."""
    from .collect import folder_rows, with_rows

    b = B.sections(base_md)
    pre, base_ents = B.split_body(b["## Medical Chronology"])
    audit_pre, new_ents = B.entries_of(audit_md)
    addition, icd_claims = _split_audit_pre(audit_pre)
    delivered_rows = B.table_rows(b["## Treatment Timeline"])
    name_of, name_log = lane_namer(headings, delivered_rows[1:], exhibit_providers(b["## Exhibit List"]))
    taken = {r["exhibit"] for r in new_exhibits}
    known = B.delivered_numbers(base_md) | taken | {int(r["exhibit"]) for r in folder or []}
    bad = sorted({n for e in new_ents for n in B.cited(e)} - known)
    if bad:
        raise ValueError(f"REFUSED: new entries cite exhibit(s) {bad} that are neither delivered nor new")
    entries = B.merge_entries(base_ents, new_ents)
    table, changed, illegible = icd.rows(B.table_rows(b["## Diagnostic Highlights"]), icd_claims, new_ents, describe)
    pre, added, notes_kept, notes_itemized = _preamble(pre, addition, new_ents)
    body = (pre.rstrip("\n") + "\n\n" if pre.strip() else "\n") + "\n\n".join(entries) + "\n\n"
    final_rows = provider_rows(entries, canon, name_of)
    retitled, uncited = exhibit_titles_for([r for r in new_exhibits if not r.get("reused")], new_ents, name_of)
    firm = folder_rows(base_md, folder or [], taken)
    firm += [f"| {r['exhibit']} | {r['reused']} |" for r in new_exhibits if r.get("reused")]
    ex_sec = with_rows(b["## Exhibit List"], [f"| {n} | {t} |" for n, t in sorted(retitled.items())] + firm)
    rr = b.get(B.SECTIONS[4])
    if rr is not None and update_note and retitled:
        rr = rr.rstrip("\n") + "\n\n" + update_note(retitled) + "\n"
    head = delivered_rows[0] if delivered_rows else TIMELINE_HEAD
    md = _sections_md(b, [head, *final_rows], table, body, ex_sec, rr)
    out_ents = _check_carried(md, base_ents, notes_kept)
    return md, {
        "delivered_entries": len(base_ents),
        "new_entries": len(new_ents),
        "output_entries": len(out_ents),
        "delivered_table_vs_its_own_entries": lane_diff(delivered_rows[1:], provider_rows(base_ents, canon, name_of)),
        "timeline_changes": lane_diff(delivered_rows[1:], final_rows),
        "lanes_named_for_heading_slots": name_log,
        "icd_rows_added_or_moved": changed,
        "icd_codes_not_legible": illegible,
        "pre_note_carried": len(notes_kept),
        "pre_note_itemized": bool(notes_itemized),
        "prior_history_added": added,
        "firm_exhibits_listed": firm,
        "new_exhibits_titled": {str(k): v for k, v in retitled.items()},
        "new_exhibits_uncited": uncited,
    }
