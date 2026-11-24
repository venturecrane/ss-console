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
   admission). A file SMD wrote (``served_papers.ours_written``) is never a
   no-text scan. Byte copies (the same sha256 in a second folder) are one
   candidate, and so are copies with other bytes (an emailed copy) that code
   reads as one set: type, set number, asking party, and requests alike
   (``fold_copies``; the discovery folder's copy is kept). A no-text file
   whose NAME reads as discovery is listed only.
2. A candidate whose title reads RESPONSE(S) TO / ANSWERS TO is a response
   paper: ours (our client responding) answers a served set; the other side's
   is its answer to OUR discovery and is ignored. A set our client propounded
   is not served on us and is dropped before any call, and so is a form set
   code shows is our side's (``served_formset.ours_form``).
3. One capped call per served candidate (``FIND_MAX`` sets, ``FIND_CAP_USD``)
   returns ONLY values (``VALUE_KEYS``); code checks each against the page:
   the type is code's own title match, each party is on the page, the set
   number is on a SET line, the responding party is our client, the date is
   on the page, and the requests sliced by code number 1..N (labelled
   "<label> NO. n" or a bare unbroken "n." run under the section heading) with
   N the call's count. A form set carries no request text: its ``source`` is
   the file, its checked numbers the form reader's, and the values its text
   layer cannot hold (drawn, not typed) are ``served_formset.py``'s; text sets
   are read first, so a form's asking party can come from those served with it.
4. A served set answered by a later response paper of ours (same type, number
   and asking party) that shows it was SERVED (``served_papers.was_served``) is
   dropped; a draft never answers, and a draft of a set still owed (the
   Operator's, or any "DRAFT ... Responses to <kind>, Set <n>") is a revision
   (``served_prior_draft``). A form set the read says our client propounded is
   dropped; one whose read does not name our client as responding stops.

Every stop is a FAILED job before any large spend (``served.FIND_STOPS``),
resumable once SMD writes ``served.json`` with the CLI; the firm hears only its
acknowledgment. ``found-report.json`` (job dir) lists every candidate and why
it was kept or dropped. Runbook: ``docs/runbooks/operator/drafting-served-sets.md``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import parties
from . import served as served_mod
from .outcome import DraftingFailed
from .served import OTHER, SET_TYPES, ServedSet, found_drafted_by
from . import served_formset as formset, served_papers as papers
from .served_docs import EXCERPT_CHARS as EXCERPT_CHARS, Doc, docs as docs, load as _load, other_side as other_side
from .served_slice import RESPONDING as _RESPONDING, slice_requests as _slice
from .served_page import WORD_EXTS as WORD_EXTS, _ASKING, _WORDS, _value, classify as classify
from .served_page import date_on_page as date_on_page, is_form as is_form, norm as norm, on_page as on_page
from .served_page import ours as ours, response_type as response_type, set_numbers as set_numbers
from .served_page import title_type as title_type

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
MAX_TOKENS = 600
VALUE_KEYS = ("set_type", "set_number", "propounding_party", "responding_party", "request_count", "date_served")
SYSTEM = (
    "You read one discovery paper served in a lawsuit: its first pages and its last page. Return ONLY a JSON "
    'object with exactly these keys: "set_type" (one of ' + ", ".join([*SET_TYPES, OTHER, "not_a_set"]) + "), "
    '"set_number" (a whole number), "propounding_party" and "responding_party" (each copied exactly as the paper '
    'names it), "request_count" (how many numbered requests or interrogatories it carries; for a form, how many '
    'are checked), "date_served" (YYYY-MM-DD from its proof of service, or null). No other text.'
)

_DISCOVERY_FOLDER = re.compile(r"(?i)\b(?:discovery|def|deposition)")


class Stop(DraftingFailed):
    """A finder stop: ``<code>: <why>; nothing was spent`` (``served.FIND_STOPS``)."""

    def __init__(self, code: str, why: str) -> None:
        super().__init__(f"{code}: {why}; stopped before any large spend")
        self.code = code


@dataclass
class Found:
    """What one finder pass decided: the report rows and the sets it keeps."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    keep: list[ServedSet] = field(default_factory=list)
    dates: list[str | None] = field(default_factory=list)

    def note(self, doc: Doc | None, kind: str, kept: bool, why: str, **more: Any) -> None:
        head = {"id": doc.id, "name": doc.name, "folder": doc.folder, "copies": doc.copies} if doc else {}
        self.rows.append({**head, "kind": kind, "kept": kept, "why": why, **more})


# ---- the requests, sliced and numbered by code ----------------------------------------------


def slice_requests(d: Doc, kind: str) -> tuple[str, str]:
    """``served_slice.slice_requests`` over one document's pages."""
    return _slice(d.pages, kind)


# ---- the one call per served set, and code's checks of what it returned ---------------------


def parse_values(raw: str, form: bool = False) -> dict[str, Any] | None:
    """The read's values in the closed shape, else None. A form's set number,
    count and parties may be missing (``served_formset.lenient``)."""
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        got = json.loads(m.group(0)) if m else None
    except ValueError:
        return None
    if not isinstance(got, dict) or set(got) != set(VALUE_KEYS):
        return None
    got = formset.lenient(got) if form else got
    whole = all(formset.whole(got[k]) or (form and got[k] is None) for k in ("set_number", "request_count"))
    strs = all(isinstance(got[k], str) for k in ("set_type", "propounding_party", "responding_party"))
    date_ok = got["date_served"] is None or isinstance(got["date_served"], str)
    return got if whole and strs and date_ok else None


def _check_common(d: Doc, kind: str, v: dict[str, Any]) -> str:
    """Why a returned value is not on the page ("" when every value checks)."""
    if v["set_type"] != kind:
        return f"the read says {v['set_type']!r} and the title reads {kind!r}"
    sets_on_page = set_numbers(d.text)
    if v["set_number"] is not None and (kind != "form_interrogatories" or sets_on_page):
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


def _form_parties(
    d: Doc, v: dict[str, Any], clients: list[str], other_side: list[str], peers: list[formset.Peer]
) -> tuple[str, str, str]:
    """A form set's (asking, answering, where the values came from): the form's
    own text when the value is on it. The read's responding party, when it
    names one, must be our client (else ``served_ambiguous``: never a default);
    one it leaves empty (the form's fill-ins are drawn, not text) is the one
    client the record names. An asking party not on the text comes from the
    text sets served with the form, else the record (``served_formset.asking``).
    A set our client propounded is dropped before this (``read_set``)."""
    named = v["responding_party"].strip()
    if (named and not ours(named, clients)) or not (named or formset.answering(clients)):
        raise Stop("served_ambiguous", f"{d.name!r}: the read cannot establish our client as the responding party "
                   f"({named[:80]!r})")  # fmt: skip
    asking = v["propounding_party"] if on_page(v["propounding_party"], d.text) else ""
    answering = (named if on_page(named, d.text) else "") or named or formset.answering(clients)
    if asking:
        return asking, answering, "page" if on_page(named, d.text) else "record"
    try:
        mates = formset.filing(peers, [v["date_served"], d.listed])
        asking, src = formset.asking(v["propounding_party"].strip(), mates, other_side)
    except formset.Unresolved as exc:
        raise Stop("served_ambiguous", f"{d.name!r}: {exc}") from None
    return asking, answering, src


@dataclass
class Reader:
    """The finder's injectable pieces: the call, and what it has cost so far."""

    ask: Callable[[str], str]
    spent: Callable[[], float] = lambda: 0.0
    next_cost: Callable[[int], float] = lambda _chars: 0.0
    calls: int = 0

    def read(self, d: Doc, form: bool = False) -> dict[str, Any]:
        text = d.excerpt()
        cost = self.next_cost(len(text) + len(SYSTEM))
        if self.spent() + cost > FIND_CAP_USD:
            raise Stop("served_many", f"reading {d.name!r} would pass the finder's {FIND_CAP_USD:.2f} USD cap")
        self.calls += 1
        got = parse_values(self.ask(text), form)
        if got is None:
            raise Stop("served_ambiguous", f"the read of {d.name!r} did not return its values in the closed shape")
        return got


def read_set(
    d: Doc,
    kind: str,
    reader: Reader,
    clients: list[str],
    other_side: list[str],
    ctx: formset.Context | None = None,
    peers: list[formset.Peer] | None = None,
) -> tuple[ServedSet | None, dict]:
    """One served candidate, read and checked: the set (None: our client
    propounded it, so it is not served on us), and its report extras."""
    v = reader.read(d, form=kind == "form_interrogatories")
    why = _check_common(d, kind, v)
    if why:
        raise Stop("served_ambiguous", f"{d.name!r}: {why}")
    drafted = found_drafted_by(kind)
    if kind == "form_interrogatories":
        if ours(v["propounding_party"], clients):
            return None, {"values": v}
        return _form_set(d, v, clients, other_side, ctx or formset.Context(), peers or [])
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


def _form_set(d: Doc, v: dict, clients: list[str], other: list[str], ctx: formset.Context, peers: list) -> tuple:
    asking, answering, src = _form_parties(d, v, clients, other, peers)
    try:
        n, count, extra = formset.resolve(d.name, d.path, v, set_numbers(d.text), ctx)
    except formset.Unresolved as exc:
        raise Stop("served_ambiguous", f"{d.name!r}: {exc}") from None
    s = ServedSet("form_interrogatories", n, asking, answering, count, "", found_drafted_by("form_interrogatories"),
                  source=d.id)  # fmt: skip
    return s, {"values": v, "party_source": src, **extra}


# ---- the walk ---------------------------------------------------------------------------------


#: The kinds of set the attorney's request names as what to draft; empty: every
#: unanswered set. A kind named in passing never narrows (``served_papers.draft_kinds``).
request_kinds = papers.draft_kinds


def prior_drafts(data: Path, title: str) -> dict[tuple[str, int], str]:
    """(set type, set number) -> the name of a draft of it already on the matter:
    the Operator's ("<title> - Responses to <kind>, Set <n> - ...", ``finish._names``)
    or any file named "DRAFT ... Responses to <kind>, Set <n>"."""
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
    for r in _load(Path(data) / "manifest.json", []):
        got = papers.draft_of(str(r.get("name") or "")) if isinstance(r, dict) else None
        if got:
            out.setdefault(got, str(r["name"]))
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
    if not papers.was_served(d.name, d.text):
        why = "a draft by its name" if papers.draft_name(d.name) else "no proof of service or dated signature"
        f.note(d, "response", False, f"{why}: not served, so it answers nothing", set_type=kind)
        return
    if not (asking and nums):
        f.note(d, "response", False, "a response paper whose set number or asking party code cannot read")
        raise Stop("served_ambiguous", f"the response paper {d.name!r} cannot be paired: no SET line or asking party")
    out.responses.append((d, kind, nums[0], asking))


def sort_docs(f: Found, all_docs: list[Doc], clients: list[str]) -> Sorted:
    """Every document by code's own reading; a set our client propounded is dropped here."""
    out = Sorted()
    ours_folders: set[str] = set()
    for d in all_docs:
        what, kind = classify(d)
        if what == "response" and kind:
            _response(f, d, kind, clients, out)
        elif what == "served" and kind:
            asking = _value(_ASKING, d.first)
            if asking and ours(asking, clients):
                f.note(d, "served", False, "propounded by our client: not served on us", set_type=kind)
                ours_folders.add(d.folder)
            else:
                out.served.append((d, kind))
        elif what == "other_set":
            out.others.append(d)
        elif what == "scan":
            out.scans.append(d)
    out.served = fold_copies(f, _drop_our_forms(f, out.served, all_docs, clients, ours_folders))
    return out


def _drop_our_forms(
    f: Found, served: list[tuple[Doc, str]], all_docs: list[Doc], clients: list[str], ours_folders: set[str]
) -> list[tuple[Doc, str]]:
    """A form set OUR side propounded is not served on us (``served_formset.ours_form``):
    out before forms are counted, folded or numbered."""
    lines = [x for d in all_docs for x in (_value(_RESPONDING, d.first), _value(_ASKING, d.first)) if ours(x, clients)]
    texts = {d.folder for d, k in served if k != "form_interrogatories"}
    keep = []
    for d, kind in served:
        why = kind == "form_interrogatories" and formset.ours_form(
            d.name, d.folder, our_role=formset.role(lines), ours_folders=ours_folders, served_folders=texts
        )
        if why:
            f.note(d, "served", False, f"our side's form set ({why}): not served on us", set_type=kind)
        else:
            keep.append((d, kind))
    return keep


def _set_key(d: Doc, kind: str) -> tuple[str, int, str, str]:
    """(type, set number, asking party, a signature of its requests), by code.
    The signature is the numbering with the first and last request's opening
    words when code slices requests and reads the SET line and the asking
    party; else the paper's whole text."""
    nums, asking = set_numbers(d.first), _value(_ASKING, d.first)
    found = served_mod.numbered(slice_requests(d, kind)[0]) if nums and asking else {}
    if found:
        ends = [norm(found[n])[:80] for n in (min(found), max(found))]
        return kind, nums[0], asking, ",".join(map(str, sorted(found))) + "|" + "|".join(ends)
    # No SET line: never folded (two form sets differ only in their boxes, which are not text).
    return kind, nums[0] if nums else 0, asking, norm(d.text) if nums else ""


def _home_rank(d: Doc) -> int:
    """A copy in the matter's discovery folder first, an email attachment last."""
    return 2 if d.kind == "attachment" else 0 if _DISCOVERY_FOLDER.search(d.folder) else 1


def fold_copies(f: Found, served: list[tuple[Doc, str]]) -> list[tuple[Doc, str]]:
    """Copies of one set with different bytes (an emailed copy beside the
    filed one, measured 2026-11-20) are one candidate, by code's own reading of
    type, set number and asking party, before any read, when the requests code
    slices number alike (else the paper's whole text matches). A candidate code
    cannot key stays its own, and two different papers under one label still
    stop ``served_ambiguous`` at the read."""
    keys = {id(d): _fold_key(d, kind, served) for d, kind in served}
    groups: list[list[tuple[Doc, str]]] = []
    for d, kind in served:
        home = next((g for g in groups if _same_set(keys[id(d)], keys[id(g[0][0])])), None)
        if home is None:
            groups.append([(d, kind)])
        else:
            home.append((d, kind))
    out: list[tuple[Doc, str]] = []
    for g in groups:
        keep = min(g, key=lambda x: _home_rank(x[0]))
        for d, kind in g:
            if d is not keep[0]:
                keep[0].copies.append(f"{d.folder}/{d.name}")
                f.note(d, "served", False, f"a copy of {keep[0].name!r} (same type, set and asking party); not read",
                       set_type=kind)  # fmt: skip
        out.append(keep)
    return out


def _fold_key(d: Doc, kind: str, served: list[tuple[Doc, str]]) -> tuple[str, int, str, str]:
    """A form beside another form keys by the $0 pixel read (checked numbers and
    revision, ``served_papers.form_signature``): its text cannot tell an emailed
    copy from a different form set. Every other candidate keys by ``_set_key``."""
    if kind == "form_interrogatories" and sum(k == kind for _d, k in served) > 1:
        sig = papers.form_signature(d.path)
        if sig:
            return kind, 0, "", sig
    return _set_key(d, kind)


def _same_set(key: tuple[str, int, str, str], ok: tuple[str, int, str, str]) -> bool:
    if ok[:2] != key[:2] or not key[3] or ok[3] != key[3]:
        return False
    return parties.same(ok[2], key[2]) if ok[2] and key[2] else ok[2] == key[2]


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


def _read_all(
    f: Found, got: Sorted, reader: Reader, clients: list[str], other_side: list[str], form_drafts: set[int]
) -> None:
    seen: dict[tuple[str, int, str], str] = {}
    ctx = formset.Context.of([k for _d, k in got.served], [r[1] for r in got.responses], form_drafts)
    peers: list[formset.Peer] = []
    kept: list[tuple[int, ServedSet, str | None]] = []
    # Text sets first: a form's asking party may come from the sets served with it.
    order = sorted(enumerate(got.served), key=lambda x: x[1][1] == "form_interrogatories")
    for i, (d, kind) in order:
        s, extra = read_set(d, kind, reader, clients, other_side, ctx, peers)
        if s is None:
            f.note(d, "served", False, "propounded by our client (the read): not served on us", set_type=kind)
            continue
        when = extra["values"]["date_served"]
        if kind != "form_interrogatories":
            peers.append(
                formset.peer(s.propounding_party, s.label.removeprefix("Responses to "), [when, d.listed], d.text)
            )
        key = (s.set_type, s.set_number, norm(s.propounding_party))
        if key in seen:
            raise Stop(
                "served_ambiguous", f"{seen[key]!r} and {d.name!r} are both {s.label} from {s.propounding_party}"
            )
        seen[key] = d.name
        kept.append((i, s, when or d.listed))
        f.note(d, "served", True, "served on our client", set_type=kind, label=s.label, **extra)
    for _i, s, when in sorted(kept, key=lambda x: x[0]):  # the matter file's order, as before
        f.keep.append(s)
        f.dates.append(when)


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


def candidates(f: Found, data: Path, request: str, clients: list[str]) -> Sorted:
    """The code half up to the first read: sorted, narrowed, and every
    before-spend stop raised. ``got.served`` is exactly what will be read."""
    kinds = request_kinds(request)
    got = sort_docs(f, docs(data), clients)
    _narrow(f, got, kinds)
    if papers.asks_amended(request):
        # Amended, further or supplemental responses are SMD's, whether or not a
        # served response is on file (else the lane drafts first-time responses).
        raise Stop("served_amended", "the request asks for amended, further or supplemental responses")
    _before_spend(f, got, kinds)
    return got


def planned_reads(data: Path, request: str, clients: list[str]) -> list[str]:
    """The excerpt of every read the finder would make, in order; empty when
    code stops before any read (the replay lists every call it needs at once)."""
    try:
        return [d.excerpt() for d, _k in candidates(Found(), data, request, list(clients)).served]
    except Stop:
        return []


def _walk(f: Found, data: Path, request: str, reader: Reader, clients: list[str], other: list[str], title: str) -> None:
    got = candidates(f, data, request, clients)
    prior = prior_drafts(data, title)
    _read_all(f, got, reader, clients, other, {n for k, n in prior if k == "form_interrogatories"})
    _answered(f, got)
    if not f.keep:
        raise Stop("served_none", "every served set in the matter file is answered by a later response of ours")
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
