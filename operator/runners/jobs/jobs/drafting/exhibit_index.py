"""The Index of Exhibits in a production response, written by code (plan A3).

``exhibit_plan`` placed every pulled file in an exhibit or left it out. Here,
after the gate, code reads which exhibits each response produces from (the
letters its own words name: "Exhibit B", "Exhibits A and C", "Exhibits A
through C") and writes the index region after the last response, replacing any
index the model wrote::

    INDEX OF EXHIBITS

    EXHIBIT B: MEDICAL BILLS

    1. Valley Ortho ledger.pdf (Request Nos. 2, 6)

Every line of an exhibit carries the request numbers that produce from it. An
exhibit no response produces from is not listed (its files are named in the
notes); an exhibit with no document is never listed.

A response that produces from an exhibit holding no document, or one the
firm's table does not have, is a code finding: :func:`watch` writes it into the
served set's audit as a finding row, so the existing converge loop repairs it,
or holds the job when no repair clears it (``converge.served``,
``repair_budget``). One still there at the gate is left as drafted and named in
the notes (``endings``, ``exhibit_unresolved``), never listed.

Each response's letters come from the firm-rules parts reader
(``rulebook.parts.read``) when it reads the document, else from this module's
own reader of ``RESPONSE TO REQUEST FOR PRODUCTION NO. n`` headings.
"""

from __future__ import annotations

import json
import re
import string
from pathlib import Path
from typing import Any, Callable

from . import exhibit_plan
from .endings import FIRM_MESSAGES

RECORD = "exhibit-index.json"
TAG = "code check: exhibits"
_MARKS = re.compile(r"[*_`#]")
_RESPONSE = re.compile(r"^\W*RESPONSE\s+TO\s+REQUEST\s+FOR\s+PRODUCTION(?:\s+OF\s+DOCUMENTS)?\s+NO\.?\s*(\d+)\b", re.I)
_REQUEST = re.compile(r"^\W*(?:REQUEST\s+FOR\s+PRODUCTION(?:\s+OF\s+DOCUMENTS)?|DEMAND)\s+NO\.?\s*\d+\b", re.I)
_CLOSING = re.compile(r"^(DATED|DATE:|RESPECTFULLY|VERIFICATION|PROOF OF SERVICE|DECLARATION OF SERVICE)\b", re.I)
_INDEX = re.compile(r"^(?:[A-Z'’ ]+\s)?INDEX\s+OF\s+EXHIBITS\s*:?\s*$", re.I)
_NOTES = re.compile(r"^=*\s*(?:NOTES\s+FOR\s+THE\s+ATTORNEY|ATTORNEY(?:'S|’S)?\s+NOTES)\b", re.I)
#: "Exhibit B", "Exhibits A and C", "Exhibits A, B, and D", "Exhibits A through C", "Exhibit A-C".
_LETTERS = re.compile(
    r"\bExhibits?\s+([A-Z](?:(?:\s*,\s*(?:and\s+)?|\s+and\s+|\s*&\s*|\s+through\s+|\s*-\s*)[A-Z])*)(?![A-Za-z])"
)
_RANGE = re.compile(r"([A-Z])(?:\s+through\s+|\s*-\s*)([A-Z])")
_PARA = re.compile(r"\S.*?(?=\n[ \t]*\n|\Z)", re.S)


def plain(text: str) -> str:
    return " ".join(_MARKS.sub("", text).split())


def letters_in(text: str) -> list[str]:
    """The exhibit letters a response's words name, in order, once each."""
    out: list[str] = []
    for m in _LETTERS.finditer(text or ""):
        span = m.group(1)
        for a, b in _RANGE.findall(span):
            out += list(string.ascii_uppercase[ord(a) - 65 : ord(b) - 64])
        out += re.findall(r"[A-Z]", _RANGE.sub("", span))
    return list(dict.fromkeys(out))


def _paras(md: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), plain(m.group(0))) for m in _PARA.finditer(md)]


def _stop(p: str) -> bool:
    return bool(_REQUEST.match(p) or _RESPONSE.match(p) or _CLOSING.match(p) or _INDEX.match(p) or _NOTES.match(p))


def read_responses(md: str) -> dict[int, str]:
    """Each production response's own words by number (this module's reader):
    from its heading to the next request heading, closing, index or notes."""
    paras, out = _paras(md), {}
    for i, (_s, _e, p) in enumerate(paras):
        m = _RESPONSE.match(p)
        if not m:
            continue
        body = [p[m.end() :]]
        for _s2, _e2, q in paras[i + 1 :]:
            if _stop(q) or q.startswith("|"):
                break
            body.append(q)
        out.setdefault(int(m.group(1)), " ".join(body).strip())
    return out


def produced(md: str) -> dict[int, list[str]]:
    """Request number -> the exhibit letters its response produces from: the
    firm-rules parts reader when it reads the document, else this module's."""
    from .rulebook import parts

    got = parts.read(md, "rfp_response")
    if got.items:
        out: dict[int, list[str]] = {}
        for label, sentences in got.items.items():
            n = re.search(r"\d+", label)
            if n:
                out[int(n.group(0))] = letters_in(" ".join(s.text for s in sentences))
        if out:
            return out
    return {n: letters_in(t) for n, t in read_responses(md).items()}


# ---- findings: a response producing from an exhibit with no document ------------------
def findings(md: str, plan: dict[str, Any]) -> list[tuple[int, str]]:
    """(request number, the finding) for each exhibit a response names that holds no document."""
    held = {e["letter"]: e for e in plan["exhibits"] if e["files"]}
    titles = {e["letter"]: e["title"] for e in plan["exhibits"]}
    allowed = ", ".join(f"{k} ({v['title']})" for k, v in held.items()) or "none"
    out = []
    for n, letters in sorted(produced(md).items()):
        for x in letters:
            if x in held:
                continue
            what = f"Exhibit {x} ({titles[x]}) holds no document" if x in titles else f"there is no Exhibit {x}"
            out.append(
                (n, f"it produces from Exhibit {x}, and {what}; name only an exhibit that holds documents: {allowed}")
            )
    return out


def audit_rows(md: str, plan: dict[str, Any]) -> list[str]:
    """The findings as audit finding rows the converge loop reads (``compose.FINDING``)."""
    return [_row(n, why) for n, why in findings(md, plan)]


def _row(n: int, why: str) -> str:
    return f"- Request for Production No. {n}: {why} | INVENTED | {TAG}"


def _section_of(draft: str, n: int) -> str | None:
    """The heading of the draft section (``demand.draft.sections``) holding response ``n``."""
    from ..demand import draft as d

    for head, body in d.sections(draft):
        if n in read_responses(body):
            return head
    return None


def add_rows(audit_md: str, draft: str, plan: dict[str, Any]) -> str:
    """The audit with each finding row added to the section that holds its
    response, once (a row already there is not added again)."""
    for n, why in findings(draft.partition("=== ATTORNEY NOTES ===")[0], plan):
        row = _row(n, why)
        if row in audit_md:
            continue
        head = _section_of(draft, n) or "RESPONSES"
        marker = f"## AUDIT: {head}\n"
        at = audit_md.find(marker)
        if at < 0:
            audit_md = audit_md.rstrip("\n") + f"\n\n{marker}\n{row}\n"
        else:
            end = audit_md.find("\n## ", at + len(marker))
            end = len(audit_md) if end < 0 else end
            audit_md = audit_md[:end].rstrip("\n") + f"\n{row}\n" + audit_md[end:]
    return audit_md


def watch(drafter: Any, data: Path) -> None:
    """Every audit of this production unit also carries the exhibit findings."""
    plan = exhibit_plan.load(data)
    if plan is None:
        return
    audit: Callable[..., dict[str, Any]] = drafter.audit

    def audited(version: int, digest: str, corpus_text: str, full: bool = False) -> dict[str, Any]:
        out = audit(version, digest, corpus_text, full=full)
        path, draft = drafter.data / f"audit-v{version}.md", drafter.data / f"draft-v{version}.md"
        if path.is_file() and draft.is_file():
            before = path.read_text(encoding="utf-8")
            after = add_rows(before, draft.read_text(encoding="utf-8"), plan)
            if after != before:
                tmp = path.with_name(f".{path.name}.tmp")
                tmp.write_text(after, encoding="utf-8")
                tmp.replace(path)
        return out

    drafter.audit = audited


def applies(data: Path, set_type: str | None) -> bool:
    return set_type == exhibit_plan.PRODUCTION and exhibit_plan.load(data) is not None


# ---- the index region -------------------------------------------------------------------
def _nos(ns: list[int]) -> str:
    return f"Request No. {ns[0]}" if len(ns) == 1 else "Request Nos. " + ", ".join(map(str, ns))


def index_lines(md: str, plan: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """The index region's paragraphs (after its heading), and the record of what it lists."""
    by_letter: dict[str, list[int]] = {}
    for n, letters in sorted(produced(md).items()):
        for x in letters:
            by_letter.setdefault(x, []).append(n)
    lines: list[str] = []
    listed, unproduced = [], []
    for e in plan["exhibits"]:
        ns = by_letter.get(e["letter"], [])
        if not e["files"]:
            continue
        if not ns:
            unproduced.append(e)
            continue
        lines.append(f"EXHIBIT {e['letter']}: {e['title']}")
        lines += [f"{i}. {f['name']} ({_nos(ns)})" for i, f in enumerate(e["files"], 1)]
        listed.append({"letter": e["letter"], "title": e["title"], "requests": ns,
                       "files": [f["name"] for f in e["files"]]})  # fmt: skip
    rec = {"listed": listed, "unproduced": unproduced, "unresolved": findings(md, plan),
           "left_out": plan["left_out"], "walled": plan.get("walled", 0)}  # fmt: skip
    return lines, rec


def write(doc: str, plan: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The document with code's index region after the last response (the
    model's index, if any, replaced; its heading kept as the model styled it),
    and the record. No response heading: the document is returned unchanged."""
    lines, rec = index_lines(doc, plan)
    doc, heading = _without_index(doc)
    paras = _paras(doc)
    at = _after_responses(paras)
    if at is None or not lines:
        return doc, rec
    return doc[:at] + f"\n\n{heading}\n\n" + "\n\n".join(lines) + doc[at:], rec


def _after_responses(paras: list[tuple[int, int, str]]) -> int | None:
    """Where the last response's words end; None when the document has no response."""
    last = max((i for i, (_s, _e, p) in enumerate(paras) if _RESPONSE.match(p)), default=None)
    if last is None:
        return None
    j = last + 1
    while j < len(paras) and not _stop(paras[j][2]) and not paras[j][2].startswith("|"):
        j += 1
    return paras[j - 1][1]


def _without_index(doc: str) -> tuple[str, str]:
    """The document with the model's index removed (from its heading to the
    closing or the notes), and that heading as the model styled it."""
    paras = _paras(doc)
    last = max((i for i, (_s, _e, p) in enumerate(paras) if _RESPONSE.match(p)), default=-1)
    at = next((i for i in range(last + 1, len(paras)) if _INDEX.match(paras[i][2])), None)
    if at is None:
        return doc, "INDEX OF EXHIBITS"
    k = at + 1
    while k < len(paras) and not (_CLOSING.match(paras[k][2]) or _NOTES.match(paras[k][2])):
        k += 1
    heading = doc[paras[at][0] : paras[at][1]].strip()
    end = paras[k][0] if k < len(paras) else len(doc)
    before = doc[: paras[at][0]].rstrip("\n")
    after = doc[end:]
    return before + ("\n\n" + after if after.strip() else "\n"), heading


def keep(unit_data: Path, rec: dict[str, Any]) -> None:
    p = Path(unit_data) / RECORD
    tmp = p.with_name(f".{RECORD}.tmp")
    tmp.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    tmp.replace(p)


def notes(unit_data: Path) -> list[str]:
    """The attorney notes' lines: every file placed and every file left out, with the reason."""
    p = Path(unit_data) / RECORD
    if not p.is_file():
        return []
    rec = json.loads(p.read_text(encoding="utf-8"))
    out = [
        "Index of Exhibits: written by code from the exhibit each response produces from; "
        f"{sum(len(e['files']) for e in rec['listed'])} documents in {len(rec['listed'])} exhibits"
    ]
    out += [
        f"Exhibit {e['letter']} ({e['title']}), {_nos(e['requests'])}: " + "; ".join(e["files"]) for e in rec["listed"]
    ]
    out += [
        f"Exhibit {e['letter']} ({e['title']}): no response produces from it, so its documents are not in the index: "
        + "; ".join(f["name"] for f in e["files"])
        for e in rec["unproduced"]
    ]
    if rec["left_out"]:
        out.append("Not placed in any exhibit: " + "; ".join(f"{f['name']} ({f['reason']})" for f in rec["left_out"]))
    if rec["walled"]:
        out.append(f"{rec['walled']} documents held back by the privilege wall are never placed in an exhibit")
    msg = FIRM_MESSAGES["exhibit_unresolved"]
    out += [msg.format(item=f"Response to Request for Production No. {n}") for n, _why in rec["unresolved"]]
    return out
