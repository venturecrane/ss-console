#!/usr/bin/env python3
"""Fetch the primary text of legal authorities into a firm's authority store.

    authority_fetch.py --out <store dir> "Howell v. Hamilton Meats & Provisions, Inc. (2011) 52 Cal.4th 541"
    authority_fetch.py --out <store dir> --wanted authorities-wanted.json
    authority_fetch.py --out <store dir> --text saved.txt --source <url> "<citation>"

Run on a laptop or the ops workspace, never in a matter turn (the seat cannot
fetch legal text there, ADR 0099 section 3). It takes CITATION STRINGS ONLY: an
argument that does not look like a citation (a reporter cite, a code section, a
jury instruction or a rule) is refused, so no client fact can ride a query to a
search service. Each authority is written to ``<store dir>/<slug>.md`` with
``citation``, ``source`` and ``fetched_at`` frontmatter, the format
``medchron.drafting.verify.authorities`` reads; the store is then pinned to the
firm's inputs by an engagements PR.

Sources, by kind:

* a case: the CourtListener citation lookup, then the opinion's text
  (``COURTLISTENER_TOKEN`` in the environment raises the rate limit; it is sent
  as a header and never printed);
* a California code section: leginfo.legislature.ca.gov's section page;
* anything else (a CACI instruction, a rule of court): fetch it by hand, save
  the text, and pin it with ``--text``.

The network is one function (``http_get``); everything else is pure, and the
tests stub the network.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

COURTLISTENER = "https://www.courtlistener.com"
LEGINFO = "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml"
HOSTS = ("www.courtlistener.com", "leginfo.legislature.ca.gov")
MAX_CITATION = 240
_REPORTER = re.compile(
    r"\b\d{1,4}\s+(?:Cal\.\s?(?:App\.\s?)?(?:\d(?:st|nd|rd|th|d)|Supp\.?)?|Cal\.\s?Rptr\.(?:\s?\d(?:d|rd|th))?|"
    r"F\.(?:\s?Supp\.)?(?:\s?\d(?:d|th))?|F\.\s?App'x|U\.S\.|S\.\s?Ct\.|P\.(?:\s?\d(?:d|rd))?)\s*\d{1,6}\b"
)
#: California code abbreviations, as briefs print them, to leginfo's lawCode.
CODES = {
    "civ. code": "CIV",
    "civil code": "CIV",
    "code civ. proc.": "CCP",
    "code of civil procedure": "CCP",
    "evid. code": "EVID",
    "evidence code": "EVID",
    "veh. code": "VEH",
    "vehicle code": "VEH",
    "ins. code": "INS",
    "insurance code": "INS",
    "gov. code": "GOV",
    "government code": "GOV",
    "pen. code": "PEN",
    "penal code": "PEN",
    "bus. & prof. code": "BPC",
    "health & saf. code": "HSC",
    "lab. code": "LAB",
    "labor code": "LAB",
}
_SECTION = re.compile(r"(?i)(?:§+|sec(?:tion)?\.?)\s*(\d+(?:\.\d+)?)")
_INSTRUCTION = re.compile(r"(?i)\b(?:CACI|BAJI)\s+No\.?\s*\d+|\bCal\.\s*Rules\s+of\s+Court|\brule\s+\d+\.\d+")

Get = Callable[[str, "bytes | None", "dict[str, str]"], "tuple[int, bytes]"]


class Refused(ValueError):
    """An argument that is not a citation string."""


def slug(citation: str) -> str:
    """Same as ``medchron.drafting.verify.authorities.slug`` (a test pins it)."""
    s = re.sub(r"[^a-z0-9]+", "-", citation.casefold()).strip("-")
    return s[:80].rstrip("-")


def kind(citation: str) -> str:
    c = " ".join(citation.split())
    if _REPORTER.search(c):
        return "case"
    if _code_of(c) and _SECTION.search(c):
        return "statute"
    if _INSTRUCTION.search(c):
        return "instruction"
    return ""


def check_citation(citation: str) -> str:
    """The citation, spacing collapsed, or ``Refused``: one line, short, and
    shaped like a citation. Nothing else is sent anywhere."""
    c = " ".join((citation or "").split())
    if not c or len(c) > MAX_CITATION or "\n" in (citation or "").strip():
        raise Refused(f"not a citation string ({len(c)} characters)")
    if not kind(c):
        raise Refused("not a citation string: no reporter cite, code section, instruction or rule in it")
    return c


def _code_of(c: str) -> str | None:
    low = c.casefold()
    for name, code in sorted(CODES.items(), key=lambda kv: -len(kv[0])):
        if name in low:
            return code
    return None


def statute_url(citation: str) -> str | None:
    code, sec = _code_of(citation), _SECTION.search(citation)
    if not code or not sec:
        return None
    return f"{LEGINFO}?{urllib.parse.urlencode({'lawCode': code, 'sectionNum': sec.group(1)})}"


def html_to_text(raw: str) -> str:
    s = re.sub(r"(?is)<(script|style|nav|header|footer)\b.*?</\1>", " ", raw)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h\d>|</li>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    lines = [" ".join(ln.split()) for ln in s.splitlines()]
    out, blank = [], False
    for ln in lines:
        if ln:
            out.append(ln)
            blank = False
        elif not blank and out:
            out.append("")
            blank = True
    return "\n".join(out).strip()


def render_entry(citation: str, source: str, fetched_at: str, text: str) -> str:
    """Same as ``medchron.drafting.verify.authorities.render_entry``."""
    one = [" ".join(str(v).split()) for v in (citation, source, fetched_at)]
    return f"---\ncitation: {one[0]}\nsource: {one[1]}\nfetched_at: {one[2]}\n---\n{text.strip()}\n"


# ---- the network -----------------------------------------------------------------------------


def http_get(url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    """The one network call. POST when ``data`` is given. Only https to the
    hosts this file names (``HOSTS``)."""
    u = urllib.parse.urlparse(url)
    if u.scheme != "https" or u.hostname not in HOSTS:
        raise LookupError(f"refusing to fetch from {u.scheme}://{u.hostname}")
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "smd-authority-fetch/1", **(headers or {})})  # noqa: S310 -- scheme and host checked against HOSTS above
    try:
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected — scheme and host checked against HOSTS above.
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 -- scheme and host checked against HOSTS above
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b""


def _cl_headers() -> dict[str, str]:
    tok = os.environ.get("COURTLISTENER_TOKEN", "").strip()
    return {"Authorization": f"Token {tok}"} if tok else {}


def fetch_case(citation: str, get: Get) -> tuple[str, str]:
    """(source URL, opinion text) through the CourtListener citation lookup."""
    body = urllib.parse.urlencode({"text": citation}).encode()
    status, raw = get(f"{COURTLISTENER}/api/rest/v4/citation-lookup/", body, _cl_headers())
    if status != 200:
        raise LookupError(f"citation lookup answered {status}")
    found = json.loads(raw.decode("utf-8", errors="replace") or "[]")
    clusters = [c for hit in found if isinstance(hit, dict) for c in hit.get("clusters") or []]
    if len(clusters) != 1:
        raise LookupError(f"citation lookup found {len(clusters)} opinions, not exactly one")
    cl = clusters[0]
    texts = []
    for op in cl.get("sub_opinions") or []:
        url = op if isinstance(op, str) else str(op.get("resource_uri") or "")
        if not url:
            continue
        url = url if url.startswith("http") else COURTLISTENER + url
        st, data = get(url, None, _cl_headers())
        if st != 200:
            raise LookupError(f"opinion fetch answered {st}")
        o = json.loads(data.decode("utf-8", errors="replace"))
        text = o.get("plain_text") or html_to_text(o.get("html_with_citations") or o.get("html") or "")
        if text.strip():
            texts.append(text.strip())
    if not texts:
        raise LookupError("the opinion carries no text")
    return COURTLISTENER + str(cl.get("absolute_url") or ""), "\n\n".join(texts)


def fetch_statute(citation: str, get: Get) -> tuple[str, str]:
    url = statute_url(citation)
    if url is None:
        raise LookupError("no code and section in the citation")
    st, raw = get(url, None, {})
    if st != 200:
        raise LookupError(f"leginfo answered {st}")
    text = html_to_text(raw.decode("utf-8", errors="replace"))
    m = re.search(r"(?s)(\d+(?:\.\d+)?\.\s.*)", text)
    if not m or len(m.group(1)) < 40:
        raise LookupError("leginfo's page carries no section text")
    return url, m.group(1).strip()


def fetch(citation: str, get: Get = http_get) -> tuple[str, str]:
    k = kind(citation)
    if k == "case":
        return fetch_case(citation, get)
    if k == "statute":
        return fetch_statute(citation, get)
    raise LookupError(f"no automatic source for a {k or 'citation of this kind'}: save the text and pin it with --text")


def write_entry(out: Path, citation: str, source: str, text: str, force: bool = False, now: str | None = None) -> Path:
    p = out / f"{slug(citation)}.md"
    if p.exists() and not force:
        raise FileExistsError(f"{p.name} is already in the store (--force replaces it)")
    out.mkdir(parents=True, exist_ok=True)
    when = now or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    p.write_text(render_entry(citation, source, when, text), encoding="utf-8")
    return p


def wanted_citations(path: Path) -> list[str]:
    body = json.loads(path.read_text(encoding="utf-8"))
    return [str(w["citation"]) for w in body.get("wanted") or [] if w.get("citation")]


def main(argv: list[str] | None = None, get: Get = http_get) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--out", required=True, type=Path, help="the authority store directory")
    ap.add_argument("--wanted", type=Path, help="an authorities-wanted.json to fetch")
    ap.add_argument("--text", type=Path, help="pin hand-saved text for ONE citation")
    ap.add_argument("--source", help="where --text came from (a URL)")
    ap.add_argument("--force", action="store_true", help="replace an entry already in the store")
    ap.add_argument("citations", nargs="*")
    a = ap.parse_args(argv)
    cites = list(a.citations) + (wanted_citations(a.wanted) if a.wanted else [])
    if a.text and (len(cites) != 1 or not a.source):
        ap.error("--text pins exactly one citation and needs --source")
    failed = 0
    for raw in cites:
        try:
            c = check_citation(raw)
            if a.text:
                source, text = str(a.source), a.text.read_text(encoding="utf-8", errors="replace")
            else:
                source, text = fetch(c, get)
            p = write_entry(a.out, c, source, text, a.force)
            print(f"ok      {p.name}  ({len(text):,} characters from {source})")
        except (Refused, LookupError, FileExistsError, ValueError) as e:
            failed += 1
            print(f"FAILED  {slug(str(raw))[:60]}: {e}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
