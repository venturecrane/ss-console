"""Case sources for ``authority_fetch.py``, tried in order:

1. CourtListener's citation lookup, ONLY when ``COURTLISTENER_TOKEN`` is in
   the environment (the API answers 401 without one). The token is sent as a
   header and never printed.
2. The Caselaw Access Project's static bulk files (static.case.law, Harvard Law
   School Library; no account): the reporter's volume index
   ``<reporter>/<volume>/CasesMetadata.json`` names the case's file, whose HTML
   carries the reporter's page labels, kept in the text as ``[*713]``.
3. When the official volume is not in CAP (Cal.App.5th stops at volume 11),
   the same case through its Cal.Rptr.3d parallel cite, found by scanning the
   Cal.Rptr.3d volumes of the case's year (CAP's Cal.Rptr.3d ends in 2019). Its
   file interleaves both reporters' page labels; each is told apart (``page_labeler``).
4. A California slip opinion PDF from courts.ca.gov, found in the Judicial
   Branch's published-opinion lists by the first party's name and the year
   (those lists reach back about two years). A slip opinion carries no
   reporter pagination, and the entry says so.

Each source sends only the citation or a part of it (a party name, a volume
number). A lookup that does not resolve to exactly one case writes nothing.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

from authority_common import Ctx, Fetched, html_to_text, norm

CAP = "https://static.case.law"
COURTLISTENER = "https://www.courtlistener.com"
COURTS = "https://courts.ca.gov"
SLIP_LISTS = ("/opinions/publishedcitable-opinions", "/opinions/published-opinions-older-120-days")
_CITE = re.compile(r"\b(\d{1,4})\s+([A-Z][A-Za-z.'\s]*?(?:\s?\d(?:st|nd|rd|th|d))?)\s*(\d{1,6})\b")
_YEAR = re.compile(r"\((?:[^()]*\s)?(\d{4})\)")
_SLIP_ROW = re.compile(
    r"\b([A-Z]{1,3}\d{2,7}[A-Z]?(?:-\d+)?)\s+([A-Z][a-z]+ \d{1,2}, (\d{4}))\s+[^•]{3,120}?•\s+Published Opinion\s+"
    r"(.+?)\s+\d{1,2}/\d{1,2}/\d{2}\b"
)
_SKIP_WORDS = {"people", "the", "in", "re", "of", "ex", "rel", "estate", "matter", "city", "county", "state"}


def parse(citation: str) -> dict[str, Any] | None:
    """Volume, reporter, first page, year and party names, or None."""
    m = _CITE.search(citation)
    if not m:
        return None
    y = _YEAR.search(citation)
    name = citation[: (y.start() if y and y.start() < m.start() else m.start())].strip(" ,")
    parties = [p.strip(" ,") for p in re.split(r"\s+v\.?\s+", name, maxsplit=1)] if name else []
    return {
        "volume": m.group(1),
        "reporter": " ".join(m.group(2).split()),
        "page": m.group(3),
        "year": int(y.group(1)) if y else None,
        "name": name,
        "parties": parties,
    }


def _first_word(party: str) -> str:
    for w in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", party or ""):
        if w.casefold() not in _SKIP_WORDS:
            return w
    return ""


def _matches_name(candidate: str, parties: list[str]) -> bool:
    low = candidate.casefold()
    words = [_first_word(p) for p in parties if _first_word(p)]
    return bool(words) and all(w.casefold() in low for w in words)


def fetch(citation: str, ctx: Ctx) -> Fetched:
    c = parse(citation)
    if c is None:
        raise LookupError("no volume, reporter and page in the citation")
    reasons: list[str] = []
    for source in (_courtlistener, _cap_direct, _cap_parallel, _slip_opinion):
        try:
            return source(citation, c, ctx)
        except LookupError as e:
            reasons.append(f"{source.__name__.strip('_').replace('_', ' ')}: {e}")
    raise LookupError("; ".join(reasons))


# ---- CourtListener (token only) ---------------------------------------------------------------


def _courtlistener(citation: str, _c: dict[str, Any], ctx: Ctx) -> Fetched:
    tok = (ctx.env.get("COURTLISTENER_TOKEN") or "").strip()
    if not tok:
        raise LookupError("skipped, no COURTLISTENER_TOKEN in the environment")
    hdr = {"Authorization": f"Token {tok}"}
    body = urllib.parse.urlencode({"text": citation}).encode()
    status, raw = ctx.get(f"{COURTLISTENER}/api/rest/v4/citation-lookup/", body, hdr)
    if status != 200:
        raise LookupError(f"citation lookup answered {status}")
    found = json.loads(raw.decode("utf-8", errors="replace") or "[]")
    clusters = [x for hit in found if isinstance(hit, dict) for x in hit.get("clusters") or []]
    if len(clusters) != 1:
        raise LookupError(f"citation lookup found {len(clusters)} opinions, not exactly one")
    cl = clusters[0]
    texts = []
    for op in cl.get("sub_opinions") or []:
        url = op if isinstance(op, str) else str(op.get("resource_uri") or "")
        if not url:
            continue
        url = url if url.startswith("http") else COURTLISTENER + url
        o = json.loads(ctx.get_ok(url, "opinion fetch", hdr).decode("utf-8", errors="replace"))
        text = o.get("plain_text") or html_to_text(o.get("html_with_citations") or o.get("html") or "")
        if text.strip():
            texts.append(text.strip())
    if not texts:
        raise LookupError("the opinion carries no text")
    return Fetched(COURTLISTENER + str(cl.get("absolute_url") or ""), "\n\n".join(texts), {})


# ---- the Caselaw Access Project ---------------------------------------------------------------


def _reporter_slugs(reporter: str, ctx: Ctx) -> list[str]:
    reps = ctx.get_json(f"{CAP}/ReportersMetadata.json", "CAP reporter index", "cap/ReportersMetadata.json")
    return [str(r["slug"]) for r in reps if norm(str(r.get("short_name") or "")) == norm(reporter)]


def _volume_cases(slug: str, volume: str, ctx: Ctx) -> list[dict[str, Any]] | None:
    """A CAP volume's case index, or None when CAP does not hold the volume."""
    cached = ctx.cache / "cap" / slug / f"{volume}.json"
    if cached.is_file():
        return json.loads(cached.read_text(encoding="utf-8"))
    status, body = ctx.get(f"{CAP}/{slug}/{volume}/CasesMetadata.json", None, {})
    if status == 404:
        return None
    if status != 200:
        raise LookupError(f"CAP volume index answered {status}")
    ctx.cached(f"cap/{slug}/{volume}.json").write_bytes(body)
    return json.loads(body.decode("utf-8", errors="replace"))


def _pick(cases: list[dict[str, Any]], cite: str, parties: list[str]) -> list[dict[str, Any]]:
    want = norm(cite)
    hits = [x for x in cases if any(norm(str(ci.get("cite") or "")) == want for ci in x.get("citations") or [])]
    if len(hits) > 1:
        hits = [x for x in hits if _matches_name(str(x.get("name_abbreviation") or x.get("name") or ""), parties)]
    return hits


def page_labeler(case: dict[str, Any], cited_reporter: str) -> Any:
    """A function from a CAP page label to its text marker. A CAP file can
    interleave two reporters' page labels (an official cite and its Cal.Rptr.
    parallel); a label belongs to the citation with the greatest first page at
    or below it. The cited reporter's pages print ``[*397]``; another's print
    ``[Cal. Rptr. 3d *260]``."""
    firsts: list[tuple[int, str]] = []
    for ci in case.get("citations") or []:
        m = _CITE.search(str(ci.get("cite") or ""))
        if m:
            firsts.append((int(m.group(3)), " ".join(m.group(2).split())))
    firsts.sort()

    def label(m: re.Match[str]) -> str:
        n = m.group(1)
        if not n.isdigit() or not firsts:
            return f" [*{n}] "
        owner = [r for first, r in firsts if first <= int(n)]
        rep = owner[-1] if owner else firsts[0][1]
        return f" [*{n}] " if norm(rep) == norm(cited_reporter) else f" [{rep} *{n}] "

    return label


def _cap_text(slug: str, volume: str, case: dict[str, Any], ctx: Ctx, cited_reporter: str) -> tuple[str, str]:
    url = f"{CAP}/{slug}/{volume}/html/{case['file_name']}.html"
    raw = ctx.get_ok(url, "CAP case file").decode("utf-8", errors="replace")
    raw = re.sub(r'(?is)<a\b[^>]*class="page-label"[^>]*>\s*\*?(\w+)\s*</a>', page_labeler(case, cited_reporter), raw)
    text = html_to_text(raw)
    if len(text) < 200:
        raise LookupError("the CAP case file carries no opinion text")
    return url, text


def _cap_direct(_citation: str, c: dict[str, Any], ctx: Ctx) -> Fetched:
    slugs = _reporter_slugs(c["reporter"], ctx)
    if not slugs:
        raise LookupError(f"CAP has no reporter {c['reporter']!r}")
    cite = f"{c['volume']} {c['reporter']} {c['page']}"
    for slug in slugs:
        cases = _volume_cases(slug, c["volume"], ctx)
        if cases is None:
            continue
        hits = _pick(cases, cite, c["parties"])
        if len(hits) == 1:
            url, text = _cap_text(slug, c["volume"], hits[0], ctx, c["reporter"])
            return Fetched(url, text, {"pagination": f"{cite} pages as [*N]"})
        if hits:
            raise LookupError(f"{len(hits)} cases begin at {cite}, not exactly one")
        raise LookupError(f"no case begins at {cite} in CAP's volume")
    raise LookupError(f"CAP does not hold volume {c['volume']} of {c['reporter']}")


#: California official reporters whose later volumes CAP holds only through
#: their Cal.Rptr.3d parallel cites.
_PARALLEL = "cal-rptr-3d"


def _cap_parallel(_citation: str, c: dict[str, Any], ctx: Ctx) -> Fetched:
    rep = norm(c["reporter"])
    if not rep.startswith("cal") or rep.startswith("calrptr"):
        raise LookupError("not a California official reporter")
    if not c["year"]:
        raise LookupError("no year in the citation to bound the Cal.Rptr.3d search")
    vols = ctx.get_json(
        f"{CAP}/{_PARALLEL}/VolumesMetadata.json", "CAP Cal.Rptr.3d index", f"cap/{_PARALLEL}/volumes.json"
    )
    year = int(c["year"])
    span = [str(v["volume_number"]) for v in vols if (v.get("start_year") or 0) <= year <= (v.get("end_year") or 0)]
    if not span:
        raise LookupError(f"CAP's Cal.Rptr.3d holds no volume for {year}")
    cite = f"{c['volume']} {c['reporter']} {c['page']}"
    for vol in sorted(span, key=int):
        cases = _volume_cases(_PARALLEL, vol, ctx) or []
        hits = _pick(cases, cite, c["parties"])
        if len(hits) == 1:
            official = next((str(x["cite"]) for x in hits[0]["citations"] if x.get("type") == "official"), vol)
            url, text = _cap_text(_PARALLEL, vol, hits[0], ctx, c["reporter"])
            return Fetched(
                url,
                text,
                {
                    "pagination": f"{cite} pages as [*N], parallel {official} pages as [Cal. Rptr. 3d *N] (text from CAP's Cal.Rptr.3d volume)"
                },
            )
        if hits:
            raise LookupError(f"{len(hits)} Cal.Rptr.3d cases carry {cite}, not exactly one")
    raise LookupError(f"no Cal.Rptr.3d case of {year} carries {cite} ({len(span)} volumes searched)")


# ---- California slip opinions ------------------------------------------------------------------


def _slip_opinion(_citation: str, c: dict[str, Any], ctx: Ctx) -> Fetched:
    party = _first_word(c["parties"][0]) if c["parties"] else ""
    if not party or not c["year"]:
        raise LookupError("no party name and year to find a slip opinion by")
    rows: dict[str, tuple[str, str]] = {}
    for path in SLIP_LISTS:
        page = ctx.get_ok(f"{COURTS}{path}?{urllib.parse.urlencode({'title': party})}", "the opinion list")
        text = " ".join(html_to_text(page.decode("utf-8", errors="replace")).split())
        for m in _SLIP_ROW.finditer(text):
            docket, year, title = m.group(1), int(m.group(3)), m.group(4)
            if year == c["year"] and _matches_name(title, c["parties"]):
                rows[docket] = (title, m.group(2))
    if len(rows) != 1:
        raise LookupError(
            f"courts.ca.gov lists {len(rows)} published opinions of {c['year']} by that name, not exactly one"
        )
    docket, (title, filed) = next(iter(rows.items()))
    url = f"https://www.courts.ca.gov/opinions/documents/{docket}.PDF"
    pages = ctx.pdf(ctx.get_ok(url, "the slip opinion"))
    text = "\n\n".join(f"[slip op. p. {i}]\n{p.strip()}" for i, p in enumerate(pages, 1) if p.strip())
    if len(text) < 200:
        raise LookupError("the slip opinion PDF carries no text")
    return Fetched(url, text, {"pagination": f"slip opinion {docket}, filed {filed}; not {c['reporter']} pages"})
