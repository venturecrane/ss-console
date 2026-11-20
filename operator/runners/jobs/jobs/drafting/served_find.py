"""Discovery responses: the Operator finds the served sets itself (the ``find`` stage).

WHY (2026-11-20). "Draft responses to the discovery on matter N" names no set;
until this stage SMD named every one by hand (``served_cli``). The sets are in
the matter file, so code finds them there, after preflight and before the plan,
and writes the SAME ``served.json`` the hand command writes: ``served.frozen``
and everything downstream are unchanged. A ``served.json`` already in the job
directory is SMD's and is the override: the stage then does nothing.

What code decides, and what the one model call per set returns:

1. **Candidates, by code only**, from ``extracted.jsonl``'s first two pages: a
   file with an asking-party line (PROPOUNDING, REQUESTING or DEMANDING PARTY,
   measured on 2026-11-20: half the real sets say REQUESTING) and a RESPONDING
   PARTY line and a title from the closed list; or a page that reads as the
   Judicial Council form (``DISC-001`` and "Page N of 8"), which types the file
   form interrogatories before any title (the form's body names requests for
   admission). Byte copies (the same sha256 in a second folder) are one
   candidate. A no-text file whose NAME reads as discovery is listed only.
2. A candidate whose title reads RESPONSE(S) TO / ANSWERS TO is a response
   paper: ours (our client responding) answers a served set; the other side's
   is its answer to OUR discovery and is ignored. A set our client propounded
   is not served on us and is dropped before any call.
3. One capped call per served candidate (``FIND_MAX`` sets, ``FIND_CAP_USD``)
   returns ONLY values (``VALUE_KEYS``); code checks each against the page:
   the type is code's own title match, each party is on the page, the set
   number is on a SET line, the responding party is our client, the date is
   on the page, and the requests sliced by code number 1..N (labelled
   "<label> NO. n" or a bare unbroken "n." run under the section heading) with
   N the call's count. A form set carries no request text: its ``source`` is
   the file, its checked numbers the form reader's.
4. A served set answered by a later response paper of ours (same type, number
   and asking party) is dropped; a prior Operator draft of a set still owed is
   a revision (``served_prior_draft``).

Every stop is a FAILED job before any large spend (``served.FIND_STOPS``),
resumable once SMD writes ``served.json`` with the CLI; the firm hears only its
acknowledgment. ``found-report.json`` (job dir) lists every candidate and why
it was kept or dropped. Runbook: ``docs/runbooks/operator/drafting-served-sets.md``.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import parties, sets
from . import served as served_mod
from .outcome import DraftingFailed
from .served import OTHER, SET_TYPES, ServedSet, found_drafted_by

REPORT = "found-report.json"
#: The ledger's stage name for the finder's calls (the budget sums by it).
STAGE = "served-find"
#: At most this many served candidates are read; more goes to SMD (``served_many``):
#: a matter with more is unusual enough that a person should look.
FIND_MAX = 12
#: The stage's hard dollar cap, every call checked against it BEFORE it is made:
#: a dozen reads of 12,000 characters cost well under this; a stage that would
#: pass it is reading something it should not, so SMD looks (``served_many``).
FIND_CAP_USD = 0.50
#: What one call reads: the first three pages and the last page, at most this.
EXCERPT_CHARS = 12_000
MAX_TOKENS = 600
VALUE_KEYS = ("set_type", "set_number", "propounding_party", "responding_party", "request_count", "date_served")
SYSTEM = (
    "You read one discovery paper served in a lawsuit: its first pages and its last page. Return ONLY a JSON "
    'object with exactly these keys: "set_type" (one of ' + ", ".join([*SET_TYPES, OTHER, "not_a_set"]) + "), "
    '"set_number" (a whole number), "propounding_party" and "responding_party" (each copied exactly as the paper '
    'names it), "request_count" (how many numbered requests or interrogatories it carries; for a form, how many '
    'are checked), "date_served" (YYYY-MM-DD from its proof of service, or null). No other text.'
)

_PAGE = re.compile(r"(?m)^\[p\.(\d+)\]$")
_ASKING = re.compile(r"(?im)^[ \t]*(?:PROPOUNDING|REQUESTING|DEMANDING)[ \t]+PART(?:Y|IES)[ \t]*:[ \t]*(.*)$")
_RESPONDING = re.compile(r"(?im)^[ \t]*RESPONDING[ \t]+PART(?:Y|IES)[ \t]*:[ \t]*(.*)$")
_SET_LINE = re.compile(r"(?im)^[ \t]*SET[ \t]*(?:NO\.?|NUMBER)?[ \t]*:?[ \t]*([A-Za-z]+|\d+)\b")
_FORM = (re.compile(r"DISC-001"), re.compile(r"(?i)\bPage\s+\d+\s+of\s+8\b"))
#: The closed title list, upper case as a caption prints it (never body prose).
_TITLES = {
    "form_interrogatories": r"FORM\s+INTERROGATOR(?:Y|IES)",
    "special_interrogatories": r"SPECIAL(?:LY\s+PREPARED)?\s+INTERROGATOR(?:Y|IES)",
    "requests_for_production": r"(?:REQUESTS?|DEMANDS?)\s+FOR\s+(?:THE\s+)?(?:PRODUCTION|INSPECTION)",
    "requests_for_admission": r"REQUESTS?\s+FOR\s+ADMISSIONS?",
}
_TITLE = {k: re.compile(v) for k, v in _TITLES.items()}
#: A response paper's title. "RESPONSE TO REQUEST FOR PRODUCTION NO. 3:" is a
#: served set's blank response line, not a title (``(?!\s+NO\.)``).
_RESPONSE = {
    k: re.compile(
        r"\b(?:RESPONSES?|ANSWERS?)(?:\s+AND\s+OBJECTIONS?)?\s+TO\s+(?:[A-Z0-9.,'’&-]+\s+){0,8}?" + v + r"(?!\s+NO\.)"
    )
    for k, v in _TITLES.items()
}
_NAME_VOCAB = re.compile(
    r"(?i)\b(?:interrogator(?:y|ies)|rogs?|srogs?|frogs?|requests?\s+for\s+(?:production|admissions?)|"
    r"demands?\s+for\s+(?:production|inspection)|rf[ap]s?|rpds?|discovery)\b"
)
_DISCOVERY_FOLDER = re.compile(r"(?i)\b(?:discovery|def|deposition)")
_LABELLED = re.compile(
    r"(?im)^[\W_]*(?:SPECIAL(?:LY\s+PREPARED)?\s+)?(?:INTERROGATORY|REQUEST\s+FOR\s+(?:PRODUCTION(?:\s+OF\s+DOCUMENTS)?"
    r"|ADMISSION)|DEMAND\s+FOR\s+(?:PRODUCTION|INSPECTION))\s+NO\.?\s*(\d+)\s*[:.)]?"
)
_BARE = re.compile(r"(?m)^[ \t]*(\d{1,3})[ \t]*[.)][ \t]+(?=\S)")
_END = re.compile(r"(?m)^[ \t]*(?:DATED|Dated)\b|^[ \t]*PROOF\s+OF\s+SERVICE\b")
#: A blank response line some served sets print after each request.
_BLANK_RESPONSE = re.compile(r"(?im)^[\W_]*RESPONSE(?:\s+TO\s+[^\n:]{0,80})?\s*:\s*$\n?")
_MARGIN = re.compile(r"(?m)^[ \t]*\d{1,2}[ \t]*$\n?")
_WORDS = {w.lower(): i for i, w in enumerate("One Two Three Four Five Six Seven Eight Nine Ten".split(), 1)}
_FORM_REQUEST = re.compile(r"(?i)\bform\s+interrogator(?:y|ies)\b|\bf\.?\s?rogs?\b")


class Stop(DraftingFailed):
    """A finder stop: ``<code>: <why>; nothing was spent`` (``served.FIND_STOPS``)."""

    def __init__(self, code: str, why: str) -> None:
        super().__init__(f"{code}: {why}; stopped before any large spend")
        self.code = code


@dataclass
class Doc:
    id: str
    name: str
    folder: str
    pages: dict[int, str]
    listed: str | None
    sha: str | None
    copies: list[str] = field(default_factory=list)

    @property
    def first(self) -> str:
        return "\n".join(self.pages.get(n, "") for n in (1, 2))

    @property
    def text(self) -> str:
        return "\n".join(self.pages[n] for n in sorted(self.pages))

    def excerpt(self) -> str:
        nums = sorted(self.pages)
        pick = [*nums[:3], *([nums[-1]] if len(nums) > 3 else [])]
        out = "\n".join(f"[p.{n}]\n{self.pages[n]}" for n in pick)
        return out if len(out) <= EXCERPT_CHARS else out[: EXCERPT_CHARS - 3000] + "\n...\n" + out[-3000:]


@dataclass
class Found:
    """What one finder pass decided: the report rows and the sets it keeps."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    keep: list[ServedSet] = field(default_factory=list)
    dates: list[str | None] = field(default_factory=list)

    def note(self, doc: Doc | None, kind: str, kept: bool, why: str, **more: Any) -> None:
        head = {"id": doc.id, "name": doc.name, "folder": doc.folder, "copies": doc.copies} if doc else {}
        self.rows.append({**head, "kind": kind, "kept": kept, "why": why, **more})


# ---- reading the job's disk ----------------------------------------------------------------


def _pages(text: str) -> dict[int, str]:
    marks = list(_PAGE.finditer(text))
    if not marks:
        return {1: text} if text.strip() else {}
    return {int(m.group(1)): text[m.end() : (marks[i + 1].start() if i + 1 < len(marks) else len(text))].strip()
            for i, m in enumerate(marks)}  # fmt: skip


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default
    except ValueError:
        return default


def docs(data: Path) -> list[Doc]:
    """Every extracted document but email bodies, byte copies folded into one."""
    listing = {str(r.get("id")): r for r in _load(Path(data) / "manifest.json", []) if isinstance(r, dict)}
    shas = {str(r.get("id")): r.get("sha256") for r in _load(Path(data) / "corpus.json", []) if isinstance(r, dict)}
    p = Path(data) / "extracted.jsonl"
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.is_file() else []
    out: list[Doc] = []
    by_sha: dict[str, Doc] = {}
    for r in rows:
        if r.get("kind") == "email_body":
            continue
        rid, tp = str(r.get("id")), r.get("text_path")
        text = Path(tp).read_text(encoding="utf-8", errors="replace") if tp and Path(tp).is_file() else ""
        lst = listing.get(rid) or {}
        d = Doc(rid, str(r.get("name") or ""), str(r.get("folder") or ""), _pages(text),
                str(lst.get("created") or lst.get("modified") or "")[:10] or None, shas.get(rid))  # fmt: skip
        if d.sha and d.sha in by_sha:
            by_sha[d.sha].copies.append(f"{d.folder}/{d.name}")
            continue
        by_sha.update({d.sha: d} if d.sha else {})
        out.append(d)
    return out


def other_side(data: Path) -> list[str]:
    """The other side's parties in the job's caption record, when one is on disk
    (a form set's asking party when its form fields carry no text)."""
    cap = _load(Path(data) / "caption.json", {})
    rows = cap.get("parties") if isinstance(cap, dict) else None
    return [str(r["name"]) for r in rows or [] if isinstance(r, dict) and r.get("side") != "client" and r.get("name")]


# ---- code's own reading of a page ------------------------------------------------------------


def norm(s: str) -> str:
    return " ".join(str(s or "").split()).casefold()


def _value(rx: re.Pattern[str], text: str) -> str:
    m = rx.search(text)
    if not m:
        return ""
    if m.group(1).strip():
        return m.group(1).strip()
    rest = [ln.strip() for ln in text[m.end() :].splitlines() if ln.strip()]
    return rest[0] if rest else ""


def set_numbers(text: str) -> list[int]:
    """The numbers the page's SET lines carry ("SET ONE", "SET NO.: 2")."""
    out = []
    for m in _SET_LINE.finditer(text):
        w = m.group(1).lower()
        n = int(w) if w.isdigit() else _WORDS.get(w)
        out += [n] if n else []
    return out


def is_form(d: Doc) -> bool:
    return any(all(rx.search(t) for rx in _FORM) for t in d.pages.values())


def title_type(text: str) -> str | None:
    hits = [(m.start(), k) for k, rx in _TITLE.items() for m in [rx.search(text)] if m]
    return min(hits)[1] if hits else None


def response_type(text: str) -> str | None:
    hits = [(m.start(), k) for k, rx in _RESPONSE.items() for m in [rx.search(text)] if m]
    return min(hits)[1] if hits else None


def classify(d: Doc) -> tuple[str, str | None]:
    """(``served`` | ``response`` | ``other_set`` | ``scan`` | ``none``, set type), by code only."""
    head = d.first
    responding = _RESPONDING.search(head)
    lined = bool(_ASKING.search(head) and responding)
    title_area = head[: responding.end()] if responding else ""  # the caption and its party block
    if lined and response_type(title_area):
        return "response", response_type(title_area)
    if is_form(d):
        return "served", "form_interrogatories"
    if lined:
        t = title_type(head)
        return ("served", t) if t else ("other_set", None)
    if len(head.strip()) < 40 and _NAME_VOCAB.search(re.sub(r"[_\-.]+", " ", d.name)):
        return "scan", None
    return "none", None


def ours(name: str, clients: list[str]) -> bool:
    problems: list[str] = []
    served_mod._ours(0, name, clients, problems)
    return bool(name.strip()) and not problems


def on_page(value: str, text: str) -> bool:
    return bool(norm(value)) and norm(value) in norm(text)


def date_on_page(iso: str, text: str) -> bool:
    try:
        d = dt.date.fromisoformat(iso)
    except (TypeError, ValueError):
        return False
    month = d.strftime("%B")
    forms = [iso, f"{d.month}/{d.day}/{d.year}", f"{d.month:02d}/{d.day:02d}/{d.year}", f"{month} {d.day}, {d.year}",
             f"{month[:3]}. {d.day}, {d.year}", f"{d.day} {month} {d.year}"]  # fmt: skip
    t = norm(text)
    return any(norm(f) in t for f in forms)


# ---- the requests, sliced and numbered by code ----------------------------------------------


def _clean(text: str) -> str:
    return _MARGIN.sub("", _BLANK_RESPONSE.sub("", text))


def _bare_start(body: str, kind: str) -> int:
    """Where the bare-numbered run begins: past the last section heading (a
    short title line) that a "1." line follows; else the start of the body."""
    best = 0
    for m in _TITLE[kind].finditer(body):
        line_end = body.find("\n", m.end())
        line = body[body.rfind("\n", 0, m.start()) + 1 : line_end if line_end >= 0 else len(body)]
        first = _BARE.search(body, m.end())
        if len(line.strip()) <= 80 and "SET" not in line.upper().split() and first and first.group(1) == "1":
            best = line_end if line_end >= 0 else len(body)
    return best


def slice_requests(d: Doc, kind: str) -> tuple[str, str]:
    """(the requests as ``served.numbering`` reads them, ``labelled`` | ``bare`` | "").

    Labelled ("<label> NO. n") text is kept as served; a bare "n." run is
    labelled with the set's own request label, each request's words as served.
    A blank "RESPONSE:" line after a request is stripped either way."""
    text = d.text
    head = _RESPONDING.search(text)
    body = _clean(text[head.end() if head else 0 :])
    end = _END.search(body)
    body = body[: end.start()] if end else body
    lab = list(_LABELLED.finditer(body))
    if lab:
        return body[lab[0].start() :].strip(), "labelled"
    start = _bare_start(body, kind)
    marks = list(_BARE.finditer(body, start))
    if not marks:
        return "", ""
    label = SET_TYPES[kind][1]
    items = [
        f"{label} NO. {m.group(1)}: " + " ".join(body[m.end() : (marks[i + 1].start() if i + 1 < len(marks) else len(body))].split())
        for i, m in enumerate(marks)
    ]  # fmt: skip
    return "\n\n".join(items), "bare"


# ---- the one call per served set, and code's checks of what it returned ---------------------


def parse_values(raw: str) -> dict[str, Any] | None:
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        got = json.loads(m.group(0)) if m else None
    except ValueError:
        return None
    if not isinstance(got, dict) or set(got) != set(VALUE_KEYS):
        return None
    whole = all(
        isinstance(got[k], int) and not isinstance(got[k], bool) and got[k] >= 1
        for k in ("set_number", "request_count")
    )
    strs = all(isinstance(got[k], str) for k in ("set_type", "propounding_party", "responding_party"))
    date_ok = got["date_served"] is None or isinstance(got["date_served"], str)
    return got if whole and strs and date_ok else None


def _check_common(d: Doc, kind: str, v: dict[str, Any]) -> str:
    """Why a returned value is not on the page ("" when every value checks)."""
    if v["set_type"] != kind:
        return f"the read says {v['set_type']!r} and the title reads {kind!r}"
    sets_on_page = set_numbers(d.text)
    if kind != "form_interrogatories" or sets_on_page:
        if v["set_number"] not in sets_on_page:
            return f"set number {v['set_number']} is on no SET line of the paper"
    if v["date_served"] is not None and not date_on_page(v["date_served"], d.text):
        return f"date served {v['date_served']!r} is not on the paper"
    return ""


def _check_parties(d: Doc, v: dict[str, Any], clients: list[str]) -> str:
    for k in ("propounding_party", "responding_party"):
        if not on_page(v[k], d.text):
            return f"{k} {v[k][:80]!r} is not on the paper"
    if not ours(v["responding_party"], clients):
        return f"the responding party {v['responding_party'][:80]!r} is not our client on this matter"
    return ""


def _form_parties(d: Doc, v: dict[str, Any], clients: list[str], other_side: list[str]) -> tuple[str, str, str]:
    """A form set's (asking, answering, where each came from): the form's own
    text when the value is on it, else the matter's record, never a stop."""
    asking = v["propounding_party"] if on_page(v["propounding_party"], d.text) else ""
    answering = (
        v["responding_party"] if on_page(v["responding_party"], d.text) and ours(v["responding_party"], clients) else ""
    )
    src = "page" if asking and answering else "record"
    asking = asking or (other_side[0] if other_side else v["propounding_party"])
    answering = answering or (clients[0] if clients else v["responding_party"])
    if not other_side and not on_page(v["propounding_party"], d.text):
        src = "read (not on the form's text nor in the record; SMD confirms)"
    return asking, answering, src


@dataclass
class Reader:
    """The finder's injectable pieces: the call, and what it has cost so far."""

    ask: Callable[[str], str]
    spent: Callable[[], float] = lambda: 0.0
    next_cost: Callable[[int], float] = lambda _chars: 0.0
    calls: int = 0

    def read(self, d: Doc) -> dict[str, Any]:
        text = d.excerpt()
        cost = self.next_cost(len(text) + len(SYSTEM))
        if self.spent() + cost > FIND_CAP_USD:
            raise Stop("served_many", f"reading {d.name!r} would pass the finder's {FIND_CAP_USD:.2f} USD cap")
        self.calls += 1
        got = parse_values(self.ask(text))
        if got is None:
            raise Stop("served_ambiguous", f"the read of {d.name!r} did not return its values in the closed shape")
        return got


def read_set(d: Doc, kind: str, reader: Reader, clients: list[str], other_side: list[str]) -> tuple[ServedSet, dict]:
    """One served candidate, read and checked: the set, and its report extras."""
    v = reader.read(d)
    why = _check_common(d, kind, v)
    if why:
        raise Stop("served_ambiguous", f"{d.name!r}: {why}")
    drafted = found_drafted_by(kind)
    if kind == "form_interrogatories":
        asking, answering, src = _form_parties(d, v, clients, other_side)
        s = ServedSet(kind, v["set_number"], asking, answering, v["request_count"], "", drafted, source=d.id)
        return s, {"values": v, "party_source": src, "request_count": "the form reader's"}
    why = _check_parties(d, v, clients)
    if why:
        raise Stop("served_ambiguous", f"{d.name!r}: {why}")
    text, style = slice_requests(d, kind)
    found = served_mod.numbering(text)
    if found != list(range(1, v["request_count"] + 1)):
        raise Stop("served_unnumbered", f"{d.name!r}: the read counts {v['request_count']} requests and the text "
                   f"numbers {found[:6]}{'...' if len(found) > 6 else ''} ({style or 'no numbered requests'})")  # fmt: skip
    s = ServedSet(kind, v["set_number"], v["propounding_party"], v["responding_party"], v["request_count"], text,
                  drafted, source=d.id)  # fmt: skip
    return s, {"values": v, "numbered_as": style}


# ---- the walk ---------------------------------------------------------------------------------


def request_kinds(request: str) -> list[str]:
    """The kinds of set the attorney's request names; empty: every unanswered set."""
    kinds = [u.key for u in sets.variants(request)]
    return [*kinds, *(["form_interrogatories"] if _FORM_REQUEST.search(request or "") else [])]


def prior_drafts(data: Path, title: str) -> dict[tuple[str, int], str]:
    """(set type, set number) -> the name of an Operator draft of it already on
    the matter: "<title> - Responses to <kind>, Set <n> - ..." (``finish._names``)."""
    kinds = {label.lower(): k for k, (label, _r) in SET_TYPES.items()}
    rx = re.compile(rf"^{re.escape(title)} - Responses to (?P<kind>.+?), Set (?P<n>\w+) - ")
    out: dict[tuple[str, int], str] = {}
    for r in _load(Path(data) / "manifest.json", []):
        m = rx.match(str(r.get("name") or "")) if isinstance(r, dict) else None
        if not m or m.group("kind").lower() not in kinds:
            continue
        w = m.group("n")
        n = int(w) if w.isdigit() else _WORDS.get(w.lower())
        if n:
            out[(kinds[m.group("kind").lower()], n)] = str(r["name"])
    return out


@dataclass
class Sorted:
    served: list[tuple[Doc, str]] = field(default_factory=list)
    responses: list[tuple[Doc, str, int, str]] = field(default_factory=list)
    scans: list[Doc] = field(default_factory=list)
    others: list[Doc] = field(default_factory=list)


def _response(f: Found, d: Doc, kind: str, clients: list[str], out: Sorted) -> None:
    head = d.first
    responding, asking, nums = _value(_RESPONDING, head), _value(_ASKING, head), set_numbers(head)
    if responding and not ours(responding, clients):
        f.note(d, "response", False, "the other side's response to our client's discovery; ignored", set_type=kind)
        return
    if not (asking and nums):
        f.note(d, "response", False, "a response paper whose set number or asking party code cannot read")
        raise Stop("served_ambiguous", f"the response paper {d.name!r} cannot be paired: no SET line or asking party")
    out.responses.append((d, kind, nums[0], asking))


def sort_docs(f: Found, all_docs: list[Doc], clients: list[str]) -> Sorted:
    """Every document by code's own reading; a set our client propounded is dropped here."""
    out = Sorted()
    for d in all_docs:
        what, kind = classify(d)
        if what == "response" and kind:
            _response(f, d, kind, clients, out)
        elif what == "served" and kind:
            asking = _value(_ASKING, d.first)
            if asking and ours(asking, clients):
                f.note(d, "served", False, "propounded by our client: not served on us", set_type=kind)
            else:
                out.served.append((d, kind))
        elif what == "other_set":
            out.others.append(d)
        elif what == "scan":
            out.scans.append(d)
    return out


def _narrow(f: Found, got: Sorted, kinds: list[str]) -> None:
    if not kinds:
        return
    for d, k in [x for x in got.served if x[1] not in kinds]:
        f.note(d, "served", False, f"the request names {', '.join(kinds)}, not this kind", set_type=k)
    got.served = [x for x in got.served if x[1] in kinds]
    for d in got.others:
        f.note(d, "other_set", False, "a set of another kind; the request names " + ", ".join(kinds))
    got.others = []


def _before_spend(f: Found, got: Sorted, kinds: list[str]) -> None:
    """Every stop code can call before a single read is paid for."""
    for d in got.scans:
        f.note(d, "scan", False, "its name reads as discovery and its first pages have no text; not read")
    if got.others:
        for d in got.others:
            f.note(d, "other_set", False, "a served set of a kind outside the list")
        names = ", ".join(repr(d.name) for d in got.others)
        raise Stop("served_other_set", f"the served sets include a kind outside the list: {names} "
                   '(record it as type "other", drafted by smd)')  # fmt: skip
    if not got.served:
        if any(_DISCOVERY_FOLDER.search(d.folder) for d in got.scans):
            raise Stop(
                "served_scan", "no served set reads as text and a no-text discovery file is in a discovery folder"
            )
        what = f"none of the kinds the request names ({', '.join(kinds)})" if kinds else "no served set"
        raise Stop("served_none", f"the matter file holds {what} code can find")
    if len(got.served) > FIND_MAX:
        raise Stop("served_many", f"{len(got.served)} served sets found; the finder reads at most {FIND_MAX}")


def _read_all(f: Found, got: Sorted, reader: Reader, clients: list[str], other_side: list[str]) -> None:
    seen: dict[tuple[str, int, str], str] = {}
    for d, kind in got.served:
        s, extra = read_set(d, kind, reader, clients, other_side)
        key = (s.set_type, s.set_number, norm(s.propounding_party))
        if key in seen:
            raise Stop(
                "served_ambiguous", f"{seen[key]!r} and {d.name!r} are both {s.label} from {s.propounding_party}"
            )
        seen[key] = d.name
        f.keep.append(s)
        f.dates.append(extra["values"]["date_served"] or d.listed)
        f.note(d, "served", True, "served on our client", set_type=kind, label=s.label, **extra)


def _answered(f: Found, got: Sorted) -> None:
    """Drop each served set a later response paper of ours answers."""
    keep: list[ServedSet] = []
    dates: list[str | None] = []
    for s, when in zip(f.keep, f.dates):
        hits = [
            r
            for r in got.responses
            if (r[1], r[2]) == (s.set_type, s.set_number) and parties.same(r[3], s.propounding_party)
        ]
        if hits and (not when or any(not r[0].listed for r in hits)):
            raise Stop("served_ambiguous", f"a response paper to {s.label} has no date code can compare")
        later = [r for r in hits if when and r[0].listed and r[0].listed > when]
        if later:
            f.note(later[0][0], "response", True, f"answers {s.label}, served {when}; that set is dropped")
            continue
        keep.append(s)
        dates.append(when)
    f.keep, f.dates = keep, dates


def find(
    data: Path,
    job_dir: Path,
    request: str,
    reader: Reader,
    *,
    clients: list[str],
    other_side: list[str] | None = None,
    title: str = "Discovery Responses",
) -> Found | None:
    """The served sets, found and written as ``served.json``; None (nothing
    done) when the hand file is already there. Raises ``Stop`` on every stop;
    ``found-report.json`` is written either way."""
    if (Path(job_dir) / served_mod.FILE).exists():
        return None
    f = Found()
    stop: Stop | None = None
    try:
        _walk(f, data, request, reader, list(clients), list(other_side or []), title)
    except Stop as exc:
        stop = exc
        raise
    finally:
        _report(job_dir, f, stop, reader)
    _atomic(Path(job_dir) / served_mod.FILE, served_mod.to_file(f.keep))
    return f


def _walk(f: Found, data: Path, request: str, reader: Reader, clients: list[str], other: list[str], title: str) -> None:
    kinds = request_kinds(request)
    got = sort_docs(f, docs(data), clients)
    _narrow(f, got, kinds)
    _before_spend(f, got, kinds)
    _read_all(f, got, reader, clients, other)
    _answered(f, got)
    if not f.keep:
        raise Stop("served_none", "every served set in the matter file is answered by a later response of ours")
    prior = prior_drafts(data, title)
    for s in f.keep:
        if (s.set_type, s.set_number) in prior:
            raise Stop(
                "served_prior_draft",
                f"{s.label} has a prior draft on the matter ({prior[(s.set_type, s.set_number)]!r}): a revision",
            )


def _report(job_dir: Path, f: Found, stop: Stop | None, reader: Reader) -> None:
    doc = {
        "stop": stop.code if stop else None,
        "reason": str(stop) if stop else None,
        "reads": reader.calls,
        "spent_usd": round(reader.spent(), 4),
        "planned": [s.label for s in f.keep] if not stop else [],
        "candidates": f.rows,
    }
    _atomic(Path(job_dir) / REPORT, json.dumps(doc, indent=1))


def _atomic(p: Path, text: str) -> None:
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)
