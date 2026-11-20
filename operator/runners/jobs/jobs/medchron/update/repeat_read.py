"""The update's repeats read, $0 half: which added entries are asked about,
what each request shows, and the replies parsed into verdicts a script can
act on. The paid calls are the stage's (``stages/update_repeats``); what may
be dropped is decided by code (``repeat_veto``).

WHY (2026-11-20). A delivered 509-page update listed 38 of its 144 new
entries as a visit some other entry already stated (18 against a delivered
entry, 20 against another new one). Two designs that judged "this paragraph
is a repeat" by matching numbers, terms and runs of words were broken in
review: they dropped "no disc herniation" against an entry stating a
herniation, and a discontinued medicine against an entry where that medicine
was continued and another discontinued. Two entries for one visit are each
written by a model in its own words, so a match cannot tell the same fact
reworded from a different fact worded alike. A reader judges; code acts on
its answer, a second read must agree, and code can veto (keep) a drop.

* CANDIDATES: every added entry (not a fold under a delivered entry) that
  shares its date with another entry, added or delivered. The date alone
  makes a candidate: a provider rule missed 58 of 123 on the 509-page update
  because the two entries' provider headings differ.
* KEEPERS, a fixed set: every delivered entry of the date, and every added
  entry of the date that RANKS above the candidate (``rank``: longer first,
  then document order). So two added entries can never each drop into the
  other, and a candidate with no keeper above it is never asked.
* A request shows the candidate's paragraphs labelled ``[P1]``... and its
  keepers labelled ``[K1]``... whole; keepers past KEEPER_CHARS of text go to
  a further request for the same candidate (a keeper is never cut or left
  out). Text only: no page is shown.
* IN-ENTRY candidates (``within``): an added entry citing two or more
  exhibits is also read against ITSELF, each paragraph against the ones
  before it (the earlier paragraph always keeps). Measured 2026-11-20: one
  visit filed as two exhibits was written by two writer chunks and the merge
  joined them into one entry of 42 paragraphs, 11 of them near copies of
  another; 6 of 222 added entries on three real updates cite two exhibits.
* The reply gives EVERY paragraph label one of VERDICTS, or it is unparsed
  (asked once more, then the stage holds).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..prompts import load
from . import body as B, entry_edits as EE

READ_PROMPT, WITHIN_PROMPT, CONFIRM_PROMPT = "repeats-read-system", "repeats-within-system", "repeats-confirm-system"
VERDICTS = ("adds", "repeats", "conflict", "different_visit")
DELIVERED, ADDED, WITHIN = "delivered", "added", "paragraph"
#: The two kinds of candidate: same date as another entry, or one entry citing two exhibits.
DATE, IN_ENTRY = "date", "within"
#: The keepers' text one request carries at most, in characters (about 6,000
#: input tokens at ``review_pages.CHARS_PER_TOKEN``). CHOSEN, NOT MEASURED: a
#: day of ten entries of about 2,000 characters fits one request, and a
#: request stays a small fraction of the entry reader's (24 shown pages). A
#: keeper longer than this goes alone in its own request, whole.
KEEPER_CHARS = 24_000


class Unparsed(ValueError):
    """The reply is not one complete JSON object giving every label a verdict."""


@dataclass
class Para:
    label: str  # P1, P2...
    index: int  # the block's index in ``entry.split("\\n\\n")`` (0 is the date and provider lines)
    text: str  # the block as it stands, its leading subsection headings included
    body: str  # the block without its leading headings: what the code checks
    heads: int  # how many leading heading lines the block carries


@dataclass
class Keeper:
    kid: str  # K1, K2...
    kind: str  # DELIVERED, ADDED, or WITHIN (an earlier paragraph of the candidate itself)
    index: int  # the entry's index among the delivered entries or among the added ones
    text: str
    block: int = -1  # WITHIN: the paragraph's block index in its entry


@dataclass
class Candidate:
    index: int  # the added entry's index in the audit document
    entry: str
    paras: list[Para]
    keepers: list[Keeper]
    mode: str = DATE

    @property
    def key(self) -> str:
        return f"{'A' if self.mode == DATE else 'W'}{self.index + 1}"


@dataclass
class Part:
    cand: Candidate
    keepers: list[Keeper]
    part: int = 1
    parts: int = 1

    @property
    def name(self) -> str:
        return self.cand.key if self.parts == 1 else f"{self.cand.key}-{self.part}"


@dataclass
class Verdict:
    verdict: str
    keeper: str = ""
    quotes: list[str] = field(default_factory=list)


@dataclass
class Plan:
    candidates: int  # added entries sharing a date with another entry
    asked: list[Candidate]  # those with at least one keeper and one paragraph, and the in-entry ones
    parts: list[Part]
    within: int = 0  # added entries citing two or more exhibits, read against themselves


def paragraphs(entry: str, headings: set[str]) -> list[Para]:
    """The entry's paragraphs below its date and provider lines that carry
    more than a subsection heading."""
    out: list[Para] = []
    for k, block in enumerate(entry.split("\n\n")):
        if k == 0:
            continue
        lines = block.split("\n")
        n = 0
        while n < len(lines) and lines[n].strip() in headings:
            n += 1
        body = "\n".join(lines[n:]).strip()
        if body:
            out.append(Para(f"P{len(out) + 1}", k, block, body, n))
    return out


def rank(ents: list[str]) -> dict[int, int]:
    """Each added entry's place in the keeper order: longer first, then document order."""
    order = sorted(range(len(ents)), key=lambda i: (-len(ents[i]), i))
    return {i: r for r, i in enumerate(order)}


def _keepers(i: int, day: str, ents: list[str], base: list[str], ranks: dict[int, int]) -> list[Keeper]:
    out = [(DELIVERED, k, d) for k, d in enumerate(base) if B.iso(d) == day]
    out += [(ADDED, j, a) for j, a in enumerate(ents) if j != i and B.iso(a) == day and ranks[j] < ranks[i]]
    return [Keeper(f"K{n}", kind, k, text) for n, (kind, k, text) in enumerate(out, 1)]


def _parts(c: Candidate) -> list[Part]:
    groups: list[list[Keeper]] = []
    size = 0
    for k in c.keepers:
        if groups and size + len(k.text) <= KEEPER_CHARS:
            groups[-1].append(k)
            size += len(k.text)
        else:
            groups.append([k])
            size = len(k.text)
    return [Part(c, g, n, len(groups)) for n, g in enumerate(groups, 1)]


def within(i: int, e: str, headings: set[str]) -> Candidate | None:
    """The in-entry candidate of an added entry citing two or more exhibits:
    its keepers are its own paragraphs, a paragraph judged only against
    those BEFORE it (2026-11-20: a visit filed as two exhibits was written
    twice by two writer chunks and joined into one entry, interleaved)."""
    paras = paragraphs(e, headings)
    if EE.is_fold(e) or len(B.cited(e)) < 2 or len(paras) < 2:
        return None
    return Candidate(i, e, paras, [Keeper(q.label, WITHIN, i, q.text, q.index) for q in paras], IN_ENTRY)


def plan(md: str, base_md: str, headings: set[str]) -> Plan:
    """The candidates of the audit document `md` against the delivered `base_md`."""
    ents, base = B.entries_of(md)[1], B.entries_of(base_md)[1]
    days = [B.iso(e) for e in ents] + [B.iso(d) for d in base]
    ranks = rank(ents)
    shared, asked = 0, []
    for i, e in enumerate(ents):
        day = B.iso(e)
        if not day or EE.is_fold(e) or days.count(day) < 2:
            continue
        shared += 1
        c = Candidate(i, e, paragraphs(e, headings), _keepers(i, day, ents, base, ranks))
        if c.paras and c.keepers:
            asked.append(c)
    inner = [w for i, e in enumerate(ents) if (w := within(i, e, headings)) is not None]
    parts = [p for c in asked for p in _parts(c)] + [Part(w, w.keepers) for w in inner]
    return Plan(shared, asked + inner, parts, len(inner))


# ---- the requests ---------------------------------------------------------------------
def _head(entry: str) -> str:
    return "\n".join(entry.split("\n")[:2])


def read_request(p: Part, cfg: Any) -> tuple[str, list[dict[str, Any]]]:
    """(system prompt, user content) of one part's first read."""
    shown = "\n\n".join(f"[{q.label}] {q.text}" for q in p.cand.paras)
    if p.cand.mode == IN_ENTRY:
        text = f"ENTRY {p.cand.key} (added), its paragraphs in order:\n{_head(p.cand.entry)}\n\n{shown}"
        return load(WITHIN_PROMPT, cfg), [{"type": "text", "text": text}]
    split = f" (part {p.part} of {p.parts}: only the keepers below are shown)" if p.parts > 1 else ""
    out: list[dict[str, Any]] = [
        {"type": "text", "text": f"CANDIDATE ENTRY {p.cand.key} (added){split}:\n{_head(p.cand.entry)}\n\n{shown}"},
        {"type": "text", "text": "SAME-DATE ENTRIES (possible keepers):"},
    ]
    out += [{"type": "text", "text": f"[{k.kid}] ({k.kind})\n{k.text}"} for k in p.keepers]
    return load(READ_PROMPT, cfg), out


def confirm_request(c: Candidate, para: Para, keeper: Keeper, cfg: Any) -> tuple[str, list[dict[str, Any]]]:
    """(system prompt, user content) of the second read: the paragraph and the
    one keeper, never the first read's answer."""
    out: list[dict[str, Any]] = [
        {"type": "text", "text": f"PARAGRAPH (from an added entry):\n{_head(c.entry)}\n\n{para.text}"},
        {"type": "text", "text": f"{_OTHER[keeper.kind == WITHIN]}:\n{keeper.text}"},
    ]
    return load(CONFIRM_PROMPT, cfg), out


_OTHER = ("ENTRY (another entry of the same date)", "ENTRY (an earlier paragraph of the same entry)")


# ---- the replies ----------------------------------------------------------------------
def _object(reply: str) -> dict[str, Any]:
    a, b = reply.find("{"), reply.rfind("}")
    if a < 0 or b < a:
        raise Unparsed("no JSON object in the reply")
    try:
        obj = json.loads(reply[a : b + 1])
    except json.JSONDecodeError as exc:
        raise Unparsed(f"the reply is not JSON: {exc.msg}") from exc
    if not isinstance(obj, dict) or obj.get("complete") is not True:
        raise Unparsed("the reply does not say it is complete")
    return obj


def _quotes(raw: object) -> list[str]:
    return [q for q in raw if isinstance(q, str) and q.strip()] if isinstance(raw, list) else []


def parse_read(reply: str, labels: list[str]) -> dict[str, Verdict]:
    """{label: Verdict} for every label, or ``Unparsed``. A malformed item
    (a keeper not shown, no quotes) parses; the code veto keeps it."""
    got = _object(reply).get("paragraphs")
    if not isinstance(got, dict):
        raise Unparsed("the reply gives no verdict per paragraph")
    items = {re.sub(r"[\[\]\s]", "", str(k)): v for k, v in got.items()}
    out: dict[str, Verdict] = {}
    for lb in labels:
        v = items.get(lb)
        verdict = str(v.get("verdict") or "").strip().lower() if isinstance(v, dict) else ""
        if verdict not in VERDICTS or not isinstance(v, dict):
            raise Unparsed(f"the reply gives no verdict for paragraph {lb}")
        keeper = re.sub(r"[\[\]\s]", "", str(v.get("keeper") or ""))
        out[lb] = Verdict(verdict, keeper, _quotes(v.get("quotes")))
    return out


def parse_confirm(reply: str) -> tuple[bool, list[str]]:
    """(states_all, quotes), or ``Unparsed``."""
    obj = _object(reply)
    if not isinstance(obj.get("states_all"), bool):
        raise Unparsed("the reply does not answer states_all")
    return bool(obj["states_all"]), _quotes(obj.get("quotes"))


def combine(parts: list[dict[str, Verdict]], label: str) -> Verdict:
    """One paragraph's verdict over its candidate's parts: a conflict in any
    part keeps it; else the first part's repeat; else a different visit; else adds."""
    got = [p[label] for p in parts if label in p]
    for want in ("conflict", "repeats", "different_visit"):
        hit = next((v for v in got if v.verdict == want), None)
        if hit is not None:
            return hit
    return Verdict("adds")


def parses(labels: list[str]) -> Any:
    def ok(text: str) -> bool:
        try:
            parse_read(text, labels)
        except Unparsed:
            return False
        return True

    return ok


def confirm_parses(text: str) -> bool:
    try:
        parse_confirm(text)
    except Unparsed:
        return False
    return True
