#!/usr/bin/env python3
"""Fetch the primary text of legal authorities into a firm's authority store.

    authority_fetch.py --out <store dir> "Howell v. Hamilton Meats & Provisions, Inc. (2011) 52 Cal.4th 541"
    authority_fetch.py --out <store dir> --wanted authorities-wanted.json
    authority_fetch.py --out <store dir> --text saved.txt --source <url> "<citation>"

Run on a laptop or the ops workspace, never in a matter turn (the seat cannot
fetch legal text there, ADR 0099 section 3). It takes CITATION STRINGS ONLY: an
argument that does not look like a citation (a reporter cite, a code section, a
C.F.R. section, a jury instruction or a rule) is refused, so no client fact can
ride a query to a public service. Each authority is written to
``<store dir>/<slug>.md`` with ``citation``, ``source`` and ``fetched_at``
frontmatter (plus ``edition``, ``printed_pages`` or ``pagination`` where the
source has them), the format ``jobs.drafting.verify.authorities`` reads; the
store is then pinned to the firm's inputs by an engagements PR.

Sources, by kind, all public and needing no account:

* a case: CourtListener only when ``COURTLISTENER_TOKEN`` is set, then the
  Caselaw Access Project's static files, then its Cal.Rptr.3d parallel volumes,
  then a courts.ca.gov slip opinion (``authority_cases.py``);
* a California code section: the Legislature's bulk law data, read by range
  requests (``authority_codes.py``); leginfo's section page answers 403 to a
  script, so it is not used;
* a C.F.R. section: GPO's annual edition through govinfo (``authority_codes.py``);
* a CACI instruction: cut from the Judicial Council's PDF (``authority_caci.py``);
* anything else (a rule of court, a BAJI instruction): read it in a browser,
  save the text, and pin it with ``--text``.

A source that blocks a script (a 403, a challenge page) is reported as it
answered; the tool never retries around a block. Downloads are cached under
``--cache`` (default ``~/.cache/smd-authority-fetch``).

The network is one function (``http_get``); the tests stub it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Mapping

# The tool runs as a script and is loaded by path; its sibling modules are imported from its own directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import authority_caci
import authority_cases
import authority_codes
from authority_common import HOSTS, Ctx, Fetched, Get, html_to_text, http_get  # noqa: F401 -- re-exported for callers and tests of this script

LEGINFO = "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml"
MAX_CITATION = 240
_REPORTER = re.compile(
    r"\b\d{1,4}\s+(?:Cal\.\s?(?:App\.\s?)?(?:\d(?:st|nd|rd|th|d)|Supp\.?)?|Cal\.\s?Rptr\.(?:\s?\d(?:d|rd|th))?|"
    r"F\.(?:\s?Supp\.)?(?:\s?\d(?:d|th))?|F\.\s?App'x|U\.S\.|S\.\s?Ct\.|P\.(?:\s?\d(?:d|rd))?)\s*\d{1,6}\b"
)
#: California code abbreviations, as briefs print them, to the Legislature's code ids.
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
_SECTION = re.compile(r"(?i)(?:§+|sec(?:tion)?\.?)\s*(\d+(?:\.\d+)*[a-z]?)\b")
_INSTRUCTION = re.compile(r"(?i)\b(?:CACI|BAJI)\s+No\.?\s*\d+|\bCal\.\s*Rules\s+of\s+Court|\brule\s+\d+\.\d+")
#: Extra frontmatter a source may record, in the order it is written.
EXTRA_KEYS = ("edition", "printed_pages", "pagination")


class Refused(ValueError):
    """An argument that is not a citation string."""


def slug(citation: str) -> str:
    """Same as ``jobs.drafting.verify.authorities.slug`` (a test pins it)."""
    s = re.sub(r"[^a-z0-9]+", "-", citation.casefold()).strip("-")
    return s[:80].rstrip("-")


def kind(citation: str) -> str:
    c = " ".join(citation.split())
    if authority_codes.parse_cfr(c):
        return "regulation"
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
        raise Refused("not a citation string: no reporter cite, code or C.F.R. section, instruction or rule in it")
    return c


def _code_of(c: str) -> str | None:
    low = c.casefold()
    for name, code in sorted(CODES.items(), key=lambda kv: -len(kv[0])):
        if name in low:
            return code
    return None


def statute_parts(citation: str) -> tuple[str, str] | None:
    code, sec = _code_of(citation), _SECTION.search(citation)
    return (code, sec.group(1)) if code and sec else None


def statute_url(citation: str) -> str | None:
    """leginfo's section page: where a person reads the statute in a browser
    (it answers 403 to a script; the tool reads the bulk data instead)."""
    p = statute_parts(citation)
    if p is None:
        return None
    return f"{LEGINFO}?{urllib.parse.urlencode({'lawCode': p[0], 'sectionNum': p[1]})}"


def render_entry(citation: str, source: str, fetched_at: str, text: str, extra: Mapping[str, str] | None = None) -> str:
    """Same as ``jobs.drafting.verify.authorities.render_entry``."""
    one = [" ".join(str(v).split()) for v in (citation, source, fetched_at)]
    more = "".join(f"{k}: {' '.join(str(v).split())}\n" for k, v in (extra or {}).items() if str(v).strip())
    return f"---\ncitation: {one[0]}\nsource: {one[1]}\nfetched_at: {one[2]}\n{more}---\n{text.strip()}\n"


def fetch_statute(citation: str, ctx: Ctx) -> Fetched:
    p = statute_parts(citation)
    if p is None:
        raise LookupError("no code and section in the citation")
    return authority_codes.fetch_statute(p[0], p[1], ctx)


def fetch(citation: str, get: Get = http_get, ctx: Ctx | None = None, caci_url: str | None = None) -> Fetched:
    ctx = ctx or Ctx(get=get)
    k = kind(citation)
    if k == "case":
        return authority_cases.fetch(citation, ctx)
    if k == "statute":
        return fetch_statute(citation, ctx)
    if k == "regulation":
        return authority_codes.fetch_cfr(citation, ctx)
    if k == "instruction" and authority_caci.number(citation):
        return authority_caci.fetch(citation, ctx, caci_url or authority_caci.CACI_URL)
    raise LookupError(f"no automatic source for this {k or 'kind of citation'}: save the text and pin it with --text")


def write_entry(
    out: Path,
    citation: str,
    source: str,
    text: str,
    force: bool = False,
    now: str | None = None,
    extra: Mapping[str, str] | None = None,
) -> Path:
    p = out / f"{slug(citation)}.md"
    if p.exists() and not force:
        raise FileExistsError(f"{p.name} is already in the store (--force replaces it)")
    out.mkdir(parents=True, exist_ok=True)
    when = now or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    given = extra or {}
    ordered = {k: given[k] for k in EXTRA_KEYS if given.get(k)}
    p.write_text(render_entry(citation, source, when, text, ordered), encoding="utf-8")
    return p


def wanted_citations(path: Path) -> list[str]:
    body = json.loads(path.read_text(encoding="utf-8"))
    return [str(w["citation"]) for w in body.get("wanted") or [] if w.get("citation")]


def main(argv: list[str] | None = None, get: Get = http_get, ctx: Ctx | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--out", required=True, type=Path, help="the authority store directory")
    ap.add_argument("--wanted", type=Path, help="an authorities-wanted.json to fetch")
    ap.add_argument("--text", type=Path, help="pin hand-saved text for ONE citation")
    ap.add_argument("--source", help="where --text came from (a URL)")
    ap.add_argument("--force", action="store_true", help="replace an entry already in the store")
    ap.add_argument("--cache", type=Path, help="download cache (default ~/.cache/smd-authority-fetch)")
    ap.add_argument("--caci-url", help="a CACI edition other than the current one")
    ap.add_argument("citations", nargs="*")
    a = ap.parse_args(argv)
    cites = list(a.citations) + (wanted_citations(a.wanted) if a.wanted else [])
    if a.text and (len(cites) != 1 or not a.source):
        ap.error("--text pins exactly one citation and needs --source")
    ctx = ctx or Ctx(get=get)
    if a.cache:
        ctx.cache = a.cache
    failed = 0
    for raw in cites:
        try:
            c = check_citation(raw)
            if a.text:
                got = Fetched(str(a.source), a.text.read_text(encoding="utf-8", errors="replace"), {})
            else:
                got = fetch(c, ctx=ctx, caci_url=a.caci_url)
            p = write_entry(a.out, c, got.source, got.text, a.force, extra=got.meta)
            print(f"ok      {p.name}  ({len(got.text):,} characters from {got.source})")
        except (Refused, LookupError, FileExistsError, ValueError) as e:
            failed += 1
            print(f"FAILED  {slug(str(raw))[:60]}: {e}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
