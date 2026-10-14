"""Put the audited new entries into the delivered chronology. $0, no model.

Inputs: the delivered chronology's markdown (proven by `docx_md.prove` to
render to the delivered document), the AUDIT document (the new entries only,
audited and repaired, its gate passed), and the compose pass's INDEX rows.

Output, in build_doc's shape:

* every delivered entry byte-identical, in its delivered order, each new entry
  placed after the last delivered entry dated on or before it;
* the delivered preamble minus the omission note, its Prior Medical History
  block replaced by the audited rewrite when the update made one;
* the Treatment Timeline computed from the whole entry set (build_doc's
  arithmetic; a delivered row that disagreed with its own entries is reported
  lane by lane, never kept);
* Diagnostic Highlights: the delivered rows, plus a row for each code the new
  entries record first (build_doc's first-date rule; descriptors from the
  vendored CMS tables, never a model);
* the delivered Exhibit List plus a row per new exhibit an added entry cites,
  titled the delivered way;
* the delivered Records Reviewed section plus the update's own paragraph.

Refuses (ValueError) when a delivered entry is not carried verbatim or in its
delivered order, the omission note survives, or a new citation names an
exhibit that is neither delivered nor new.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Callable

from . import body as B

ICD_SHAPE = re.compile(r"[A-Z]\d{2}(\.[0-9A-Z]{1,4}|[0-9A-Z]{0,4})|\d{3}(\.\d{1,2})?|[VE]\d{2,3}(\.\d)?")
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


def index_codes(index_rows: list[str], kept_dates: set[str]) -> dict[str, tuple[str, str, str]]:
    """{dotless: (first iso, code, provider)} from INDEX rows, build_doc's cleaning rules."""
    icd: dict[str, tuple[str, str, str]] = {}
    for line in index_rows:
        p = [x.strip() for x in line.split("|")]
        if len(p) < 3 or not re.match(r"^\d{4}-\d{2}-\d{2}$", p[0]) or p[0] not in kept_dates:
            continue
        for code in re.split(r"[,/;]\s*", p[2]):
            code = re.sub(r"\s*\(.*?\)", "", code).strip()
            code = re.sub(r"(?i)\s+(?:as recorded|history).*$", "", code).strip()
            if not code or code == "--" or not ICD_SHAPE.fullmatch(code):
                continue
            key = code.replace(".", "")
            prior = icd.get(key)
            if prior is None or p[0] < prior[0]:
                icd[key] = (p[0], code, p[1])
            elif p[0] == prior[0] and "." in code and "." not in prior[1]:
                icd[key] = (prior[0], code, prior[2])
    return icd


def _toks(s: str) -> set[str]:
    return set(re.findall(r"[a-z]{4,}", (s or "").lower()))


def icd_rows(
    base_rows: list[str], new_icd: dict[str, tuple[str, str, str]], entries: list[str], describe: Callable[[str], str]
) -> tuple[list[str], list[dict[str, Any]]]:
    """Delivered rows kept as delivered; a code first recorded by a new entry
    gets build_doc's row. Sorted by (first date, code), as build_doc sorts."""
    rows: dict[str, tuple[str, str, str]] = {}
    for line in base_rows[1:]:
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        m = re.match(r"(\d{2})/(\d{2})/(\d{4})", c[2] if len(c) > 2 else "")
        first = f"{m.group(3)}-{m.group(1)}-{m.group(2)}" if m else "9999-12-31"
        rows[c[0].replace(".", "")] = (first, c[0], line.strip())
    es = [{"iso": B.iso(e), "provider": B.provider_of(e), "ref": (B.CITE.search(e) or [""])[0][1:-1]} for e in entries]
    changed = []
    for key, (first, code, prov) in new_icd.items():
        if key in rows and rows[key][0] <= first:
            continue
        same = [e for e in es if e["iso"] == first]
        best = max(same, key=lambda e: len(_toks(e["provider"]) & _toks(prov)), default=None)
        line = f"| {code} | {describe(code)} | {first[5:7]}/{first[8:10]}/{first[:4]} | {best['ref'] if best else ''} |"
        changed.append({"code": code, "was": rows[key][2] if key in rows else None, "now": line})
        rows[key] = (first, code, line)
    head = base_rows[:1] or ["| ICD Code | Description | First Diagnosed | Reference |"]
    return head + [r[2] for r in sorted(rows.values(), key=lambda r: (r[0], r[1]))], changed


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


def _check_carried(md: str, base_ents: list[str]) -> list[str]:
    _, out_ents = B.entries_of(md)
    oc = Counter(out_ents)
    lost = [e[:60] for e, n in Counter(base_ents).items() if oc[e] < n]
    if lost:
        raise ValueError(f"REFUSED: {len(lost)} delivered entr(y/ies) not carried verbatim: {lost[:3]}")
    if B.PRE_NOTE.search(md):
        raise ValueError("REFUSED: the omission note survived")
    base = Counter(base_ents)
    if [e for e in out_ents if e in base] != base_ents:
        raise ValueError("REFUSED: delivered entries are not in their delivered order")
    return out_ents


def _preamble(pre: str, audit_pre: str) -> tuple[str, bool]:
    pre = B.PRE_NOTE.sub("", pre)
    if not B.PMH_HEAD.search(audit_pre or ""):
        return pre, False
    m = B.PMH_HEAD.search(pre)
    return (pre[: m.start()] if m else pre) + audit_pre.strip("\n") + "\n", True


def assemble(
    base_md: str,
    audit_md: str,
    index_rows: list[str],
    *,
    describe: Callable[[str], str],
    canon: Callable[[str], str],
    headings: set[str],
    new_exhibits: list[dict[str, Any]],
    update_note: Callable[[dict[int, str]], str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """(updated markdown, report). Raises ValueError on any refusal."""
    b = B.sections(base_md)
    pre, base_ents = B.split_body(b["## Medical Chronology"])
    audit_pre, new_ents = B.entries_of(audit_md)
    delivered_rows = B.table_rows(b["## Treatment Timeline"])
    name_of, name_log = lane_namer(headings, delivered_rows[1:], exhibit_providers(b["## Exhibit List"]))
    known = B.delivered_numbers(base_md) | {r["exhibit"] for r in new_exhibits}
    bad = sorted({n for e in new_ents for n in B.cited(e)} - known)
    if bad:
        raise ValueError(f"REFUSED: new entries cite exhibit(s) {bad} that are neither delivered nor new")
    entries = B.merge_entries(base_ents, new_ents)
    icd, changed = icd_rows(
        B.table_rows(b["## Diagnostic Highlights"]),
        index_codes(index_rows, {B.iso(e) for e in new_ents}),
        entries,
        describe,
    )
    pre, pmh_replaced = _preamble(pre, audit_pre)
    body = (pre.rstrip("\n") + "\n\n" if pre.strip() else "\n") + "\n\n".join(entries) + "\n\n"
    final_rows = provider_rows(entries, canon, name_of)
    retitled, uncited = exhibit_titles_for(new_exhibits, new_ents, name_of)
    ex_sec = b["## Exhibit List"]
    from .collect import with_rows

    ex_sec = with_rows(ex_sec, [f"| {n} | {t} |" for n, t in sorted(retitled.items())])
    rr = b.get(B.SECTIONS[4])
    if rr is not None and update_note and retitled:
        rr = rr.rstrip("\n") + "\n\n" + update_note(retitled) + "\n"
    head = delivered_rows[0] if delivered_rows else TIMELINE_HEAD
    md = (
        b["title"]
        + B.SECTIONS[0]
        + "\n\n"
        + "\n".join([head, *final_rows])
        + "\n\n"
        + B.SECTIONS[1]
        + "\n\n"
        + "\n".join(icd)
        + "\n\n"
        + B.SECTIONS[2]
        + "\n"
        + body
        + B.SECTIONS[3]
        + ex_sec
        + (B.SECTIONS[4] + rr if rr is not None else "")
    )
    out_ents = _check_carried(md, base_ents)
    return md, {
        "delivered_entries": len(base_ents),
        "new_entries": len(new_ents),
        "output_entries": len(out_ents),
        "delivered_table_vs_its_own_entries": lane_diff(delivered_rows[1:], provider_rows(base_ents, canon, name_of)),
        "timeline_changes": lane_diff(delivered_rows[1:], final_rows),
        "lanes_named_for_heading_slots": name_log,
        "icd_rows_added_or_moved": changed,
        "pre_note_removed": bool(B.PRE_NOTE.search(b["## Medical Chronology"])),
        "prior_medical_history_replaced": pmh_replaced,
        "new_exhibits_titled": {str(k): v for k, v in retitled.items()},
        "new_exhibits_uncited": uncited,
    }
