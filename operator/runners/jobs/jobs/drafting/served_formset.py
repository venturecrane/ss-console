"""A served FORM set's values its text layer cannot hold (the ``find`` stage's form half).

WHY (2026-11-21, two matters stopped ``served_ambiguous`` on the seat). A served
Judicial Council form (DISC-001) is a PDF whose text layer is the printed blank
form; the set number, the parties and the checkmarks are drawn on it, not
typed into it. The finder's one read is a text read, so a form's set number,
count and parties are not in what it reads, and requiring them stopped every
job with a served form set. For a form candidate:

* the count is the $0 pixel read's checked numbers (``served_papers.form_checked``),
  else the read's count when it gave one; neither: ``served_ambiguous``;
* the set number is the read's (``served_find`` checks it against the SET
  lines when the text has any), else one SET line's, else the file name's
  ("Set Two"), else, for the matter's only form candidate with no served form
  response on file, the one number a form draft on file names, or 1 when no
  form draft or response is on file (nothing shows an earlier form set);
  anything else is ``served_ambiguous`` for SMD, never a guess between numbers;
* a party the read leaves empty comes from elsewhere (``served_find._form_parties``):
  the answering party from the job's record, the asking party from the text
  sets served with the form, else the record (``asking``);
* a form set our side propounded is no candidate at all (``ours_form``).

Every value says where it came from in ``found-report.json``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import parties
from . import served_papers as papers

FORM = "form_interrogatories"
_NAME_SET = re.compile(r"(?i)\bSet[\s_-]+(?:No\.?\s*)?([A-Za-z]+|\d+)\b")


def whole(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def lenient(got: dict[str, Any]) -> dict[str, Any]:
    """A form read's values with the ones a form's text cannot hold made optional:
    a set number or count that is not a whole number is None, a party that is
    None is ""."""
    out = dict(got)
    for k in ("set_number", "request_count"):
        out[k] = out[k] if whole(out[k]) else None
    for k in ("propounding_party", "responding_party"):
        out[k] = "" if out[k] is None else out[k]
    return out


def count(path: str, read_count: int | None) -> tuple[int | None, str]:
    """(the form set's request count, where it came from); None: no source."""
    checked = papers.form_checked(path)
    if checked:
        return len(checked), "the $0 pixel read's checked numbers"
    if read_count is not None:
        return read_count, "the read (the pixel read could not read the form)"
    return None, "the form's checks are drawn, not text, and the $0 pixel read could not read them"


def set_number(
    name: str,
    read_n: int | None,
    on_text: list[int],
    *,
    only_form: bool,
    form_responses: bool,
    form_drafts: set[int],
) -> tuple[int | None, str]:
    """(the form set's number, where it came from); None: the reason it is unknown."""
    if read_n is not None:
        return read_n, "the read"
    if len(set(on_text)) == 1:
        return on_text[0], "the form's SET line"
    m = _NAME_SET.search(re.sub(r"_", " ", name or ""))
    n = papers.set_number(m.group(1)) if m else None
    if n:
        return n, "the file name"
    if not only_form or form_responses or len(form_drafts) > 1:
        return None, (
            "its set number is drawn, not text, and the matter file holds "
            + ("another form set" if not only_form else "a served form response or drafts of several form sets")
        )
    if form_drafts:
        return next(iter(form_drafts)), "the one form draft on file (the matter's only form set)"
    return 1, "the matter's only form set; no earlier form set, response or draft is on file"


class Unresolved(ValueError):
    """A form value no source holds: the finder stops ``served_ambiguous`` with this reason."""


@dataclass
class Context:
    """What the matter file shows about form sets, for a form's set number."""

    only_form: bool = True
    form_responses: bool = False
    form_drafts: set[int] = field(default_factory=set)

    @classmethod
    def of(cls, served_kinds: list[str], response_kinds: list[str], form_drafts: set[int]) -> Context:
        return cls(served_kinds.count(FORM) == 1, FORM in response_kinds, set(form_drafts))


def resolve(name: str, path: str, v: dict[str, Any], on_text: list[int], ctx: Context) -> tuple[int, int, dict]:
    """(set number, count, where each came from) for one form candidate; ``Unresolved`` when either has no source."""
    n, n_src = set_number(name, v["set_number"], on_text, only_form=ctx.only_form,
                          form_responses=ctx.form_responses, form_drafts=ctx.form_drafts)  # fmt: skip
    c, c_src = count(path, v["request_count"])
    if n is None or c is None:
        raise Unresolved(n_src if n is None else c_src)
    return n, c, {"set_number_source": n_src, "request_count": c_src}


@dataclass(frozen=True)
class Peer:
    """A text set served on our client, as a form set's possible filing-mate: its
    asking party (checked on its own page), its label, the dates it carries
    (served and listed), and whether its proof of service names form interrogatories."""

    party: str
    label: str
    dates: frozenset[str]
    names_form: bool


_FORM_NAMED = re.compile(r"(?i)\bform\s+interrogator(?:y|ies)\b")
_PROOF = re.compile(r"(?i)\bPROOF\s+OF\s+(?:ELECTRONIC\s+)?SERVICE\b")


def peer(party: str, label: str, dates: list[str | None], text: str) -> Peer:
    proof = _PROOF.search(text or "")
    names = bool(proof and _FORM_NAMED.search(text[proof.end() :]))
    return Peer(party, label, frozenset(d for d in dates if d), names)


def filing(peers: list[Peer], form_dates: list[str | None]) -> list[Peer]:
    """The text sets served in the same filing as a form: one that shares a date
    with it (served or listed), or whose proof of service lists form interrogatories."""
    mine = {d for d in form_dates if d}
    return [p for p in peers if p.names_form or p.dates & mine]


def asking(read: str, peers: list[Peer], record: list[str]) -> tuple[str, str]:
    """(a form set's asking party, where it came from) when the form's text does
    not name it (its caption is drawn, not typed). 2026-11-21: a served form
    whose job had no caption record stopped ``served_ambiguous`` though the
    special interrogatories served with it named the propounding party.

    In order: the text sets served with it (``filing``), else the job's caption
    record, else the read's value for SMD to confirm. The sets served with it
    must name one party, and the record must agree with it; any disagreement,
    or no source at all, is ``Unresolved``: never a pick."""
    named: list[Peer] = []
    for p in peers:
        if not any(parties.same(p.party, x.party) for x in named):
            named.append(p)
    if len(named) > 1:
        raise Unresolved("the sets served with the form name different asking parties: "
                         + ", ".join(f"{p.party[:80]!r} ({p.label})" for p in named))  # fmt: skip
    if named and record and not any(parties.same(named[0].party, r) for r in record):
        raise Unresolved(f"the sets served with the form name {named[0].party[:80]!r} as the asking party and the "
                         f"record names {', '.join(repr(r[:80]) for r in record)}")  # fmt: skip
    if named:
        return named[0].party, f"the text sets served with it ({', '.join(sorted({p.label for p in peers}))})"
    if record:
        return record[0], "record"
    if read:
        return read, "read (not on the form's text nor in the record; SMD confirms)"
    raise Unresolved(
        "the asking party is not on the form's text, in a set served with it, in the read or in the record"
    )


#: A party line's role word ("Plaintiff GAMMA EXAMPLE").
_ROLE = re.compile(r"(?i)^\W*(plaintiff|defendant)s?\b")
#: How a folder names each side ("Pltf Discovery", "Def's Discovery").
_SIDE = {
    "plaintiff": re.compile(r"(?i)\b(?:plaintiffs?|pltfs?|plfs?|ptfs?)\b"),
    "defendant": re.compile(r"(?i)\b(?:defendants?|defts?|dfts?|defs?)\b"),
}
#: A folder that holds what came IN ("Received", "Incoming", "served on us").
_INCOMING = re.compile(r"(?i)\b(?:received|incoming|inbound|served\s+on)\b")


def role(party_lines: list[str]) -> str:
    """Our client's side (``plaintiff`` | ``defendant``) when every party line
    naming our client that carries a role word carries the same one; "" otherwise."""
    roles = {m.group(1).lower() for x in party_lines for m in [_ROLE.match(x or "")] if m}
    return next(iter(roles)) if len(roles) == 1 else ""


def ours_form(name: str, folder: str, *, our_role: str, ours_folders: set[str], served_folders: set[str]) -> str:
    """Why a form candidate is a set OUR side propounded; "" when nothing shows it.

    2026-11-21: a form set our client served on the other side, filed in our
    client's own folder, was counted as served on us, and the served form's
    drawn set number then stopped ``served_ambiguous`` ("another form set").
    The evidence, all before any read: SMD wrote it (``ours_written``); its
    folder holds a set our client propounded and no set served on us; or its
    folder names our client's side, not the other side, and not as what came in."""
    if papers.ours_written(name, folder):
        return "a file SMD wrote"
    if folder in served_folders:
        return ""  # a folder that holds a set served on us shows nothing about this one
    if folder in ours_folders:
        return "its folder holds a set our client propounded and no set served on us"
    other = {"plaintiff": "defendant", "defendant": "plaintiff"}.get(our_role)
    side = _SIDE[our_role].search(folder or "") if other else None
    if other and side and not _SIDE[other].search(folder or "") and not _INCOMING.search(folder or ""):
        return f"its folder names our client's side ({our_role}) and not the other side"
    return ""


def answering(clients: list[str]) -> str:
    """Our client, when the record names exactly one (spellings of one party
    count once); "" otherwise."""
    distinct: list[str] = []
    for c in clients:
        if not any(parties.same(c, x) for x in distinct):
            distinct.append(c)
    return distinct[0] if len(distinct) == 1 else ""
