"""Discovery responses: the served sets, named by SMD in a hand file.

WHY (2026-11-19). Every response request that day was answered by hand: the
job could not tell which sets the other side had served, how many requests
each carried, or which of them the Operator can draft (a form interrogatory
set is answered by SMD). A model reading the file for that guesses; SMD knows.
So a ``discovery_response`` job reads ONE thing for it, SMD's ``served.json``
in the job directory, and nothing else (no paper reader, no breakdown)::

    {"sets": [
      {"set_type": "special_interrogatories",     one of SET_TYPES (closed)
       "set_number": 1,                           a whole number, 1 or more
       "propounding_party": "<as served>",
       "responding_party": "<as served>",
       "request_count": 22,                       a whole number, 1 or more
       "request_text": "<the requests, verbatim>"  or "request_text_path":
                                                  "<a file inside the job dir>"
       "drafted_by": "operator",                  "operator" or "smd"
       "prior_draft": "<file name>",              optional, with prior_job: the draft of this
       "prior_job": "<job id>"}                   set an Operator job delivered, on
                                                  the matter (never drafted again)
    ]}

Closed keys at both levels. The endings, each before anything is paid (the
check runs just after preflight, ``run._walk``), each a FAILED job: the firm
hears only the acknowledgment it already had, SMD is paged, and the job is
resumable once SMD writes or fixes the file (the broker's
``drafting_job_resume``):

* ``served_unknown``: no ``served.json`` yet.
* ``served_other_set``: a set type outside ``SET_TYPES``; SMD records such a
  set as type ``other`` with its ``label``, drafted by ``smd`` only, so the
  reply still names it.
* ``served_invalid``: anything else malformed, or no set marked ``operator``;
  an ``operator`` set whose numbered requests ("<label> NO. n" lines) are not
  1 to its ``request_count``, whose responding party is not our client in the
  job's record, or that is a form interrogatory set (answered by SMD until the
  drafting answers dotted numbers, ``FORM_SETS_BY_OPERATOR``). A form set's
  own keys (form, revision, checked numbers, definition boxes, additions) are
  ``served_form.py``'s.

SMD writes and corrects the file with ``python -m jobs.drafting.served``
(``served_cli.py``), never by hand as root. Since 2026-11-20 the ``find`` stage
writes it first, from the matter file itself (``served_find.py``); a file SMD
wrote is the override and the finder leaves it alone.

Each writes ``smd-needs.json`` in the job directory (``stop_note``): the code,
the reason, the shape above, and the job's frozen firm wording rules, so a set
SMD drafts by hand follows the same rules the Operator's drafts do.

A valid file is frozen into ``data/served-plan.json`` the first time it reads,
so a resumed job drafts from the sets it planned even if the hand file is
edited later. The Operator drafts one document per ``operator`` set, each with
that set's verbatim requests in its compose context (``instruction``); the
delivered job writes ``data/served-outcomes.json``, every served set as filed
(with its document's name) or being prepared by us (``outcomes``,
``reply_lines``).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .. import firm_wording
from . import parties, served_form
from .outcome import DraftingFailed

FILE = "served.json"
PLAN = "served-plan.json"
OUTCOMES = "served-outcomes.json"
#: What SMD's page reads for a job stopped before spend (here and ``parties``).
STOP_NOTE = "smd-needs.json"

#: set type -> (the set's label, the request label a response restates).
SET_TYPES: dict[str, tuple[str, str]] = {
    "special_interrogatories": ("Special Interrogatories", "SPECIAL INTERROGATORY"),
    "requests_for_production": ("Requests for Production", "REQUEST FOR PRODUCTION"),
    "form_interrogatories": ("Form Interrogatories", "FORM INTERROGATORY"),
    "requests_for_admission": ("Requests for Admission", "REQUEST FOR ADMISSION"),
}
DRAFTERS = ("operator", "smd")
OPERATOR, SMD = DRAFTERS
#: A served set of a kind outside ``SET_TYPES``, named by its own ``label``:
#: drafted by SMD only, listed in the reply like any other.
OTHER = "other"
#: Who drafts a form interrogatory set is per firm (``forms.lane_hook.by_operator``):
#: the Operator when the firm authored ``form_responses`` AND this global switch
#: is on. This is the off switch for every firm at once; a firm's own section is
#: its on switch, so a firm without one keeps SMD drafting its form sets exactly
#: as before. The finder and the validator are handed the answer
#: (``form_by_operator``) by the lane; a caller with no firm passes nothing and
#: gets SMD.
FORM_SETS_BY_OPERATOR = True
#: Kinds the Operator does not draft: a set of type other.
SMD_ONLY = frozenset({OTHER})
#: Who drafts each kind of set the finder found (``served_find.py``): the
#: Operator drafts special interrogatories and requests for production; SMD the
#: rest. A form set follows the firm (``found_drafted_by``, ``form_by_operator``). A
#: hand file (``served_cli``) says its own ``drafted_by``.
FOUND_DRAFTED_BY = {
    "special_interrogatories": OPERATOR,
    "requests_for_production": OPERATOR,
    "requests_for_admission": SMD,
    OTHER: SMD,
}


def form_drafted_by(form_by_operator: bool = False) -> str:
    """Who drafts a form interrogatory set: the lane's per-firm answer
    (``forms.lane_hook.by_operator``), which already includes the global switch."""
    return OPERATOR if form_by_operator else SMD


def found_drafted_by(kind: str, form_by_operator: bool = False) -> str:
    """Who drafts a set of this kind the finder found."""
    return form_drafted_by(form_by_operator) if kind == "form_interrogatories" else FOUND_DRAFTED_BY[kind]


BEING_PREPARED = "being prepared by us"
FILED = "filed"
#: A served-set row whose draft was already on the matter (``outcomes``).
PRIOR = "prior_draft"
KEYS = (
    "set_type",
    "set_number",
    "propounding_party",
    "responding_party",
    "request_count",
    "request_text",
    "request_text_path",
    "drafted_by",
    "label",
    "source",
    "prior_draft",
    "prior_job",
    *served_form.FORM_KEYS,
)
_REQUIRED = ("set_type", "set_number", "propounding_party", "responding_party", "request_count", "drafted_by")
_NUMBERS = "One Two Three Four Five Six Seven Eight Nine Ten".split()
#: A numbered request in the served text: a line that starts "<label> NO. n"
#: ("REQUEST NO. 3:", "SPECIAL INTERROGATORY NO. 3."), its text the lines after.
#: What ends a label's number: a colon, period or closing paren (bold marks
#: allowed before it), or the end of the line. A number followed by a word is a
#: cross-reference the pleading's wrap put at a line start ("Special
#: Interrogatory No. 3 for which YOU contend", measured 2026-11-26 on a real
#: 35-request set, read as 1,2,3,4,3,5,3...), never a label. Case is no guide:
#: some sets title-case their labels.
LABEL_END = r"(?=[ \t*_]*(?:[:.)]|\r?$))"
_SERVED_ITEM = re.compile(r"(?im)^[\W_]*(?:[a-z][a-z .'’-]{0,60}?\s)?NO\.?\s*(\d+)" + LABEL_END + r"\s*[:.)]?[ \t]*")


@dataclass(frozen=True)
class ServedSet:
    set_type: str
    set_number: int
    propounding_party: str
    responding_party: str
    request_count: int
    request_text: str
    drafted_by: str
    #: The set's own name, for a set of type ``other`` only.
    other_label: str = ""
    #: The matter file's document id the set was read from (``source``; the
    #: finder writes it, ``served_find.py``); "" for a hand-named set.
    source: str = ""
    #: The draft of this set an earlier Operator job DELIVERED (same type, number
    #: and asking party, ``served_find.with_priors``), and that job's id: the set
    #: is not drafted again; the reply names the draft, and a change request in
    #: the delivery's thread revises it (``workspace_broker/drafting_route``).
    #: "" for every other set. Any other draft on the matter stops the job.
    prior_draft: str = ""
    prior_job: str = ""
    #: A form interrogatory set's own keys (``served_form.py``); empty for any
    #: other set, and for an SMD form set that names none. Defaults, so a plan
    #: frozen before these keys existed still loads.
    form: str = ""
    form_revision: str = ""
    checked: tuple[str, ...] = ()
    definition_boxes: tuple[str, ...] = ()
    additions: bool = False

    def __post_init__(self) -> None:
        # a frozen plan reads lists back from JSON
        object.__setattr__(self, "checked", tuple(self.checked))
        object.__setattr__(self, "definition_boxes", tuple(self.definition_boxes))

    @property
    def label(self) -> str:
        """``Responses to Special Interrogatories, Set One``."""
        n = _NUMBERS[self.set_number - 1] if self.set_number <= len(_NUMBERS) else str(self.set_number)
        kind = self.other_label if self.set_type == OTHER else SET_TYPES[self.set_type][0]
        return f"Responses to {kind}, Set {n}"

    @property
    def request_label(self) -> str:
        return SET_TYPES[self.set_type][1] if self.set_type in SET_TYPES else ""


def numbered(text: str) -> dict[int, str]:
    """The served text's requests by number, each its text with whitespace
    normalised (what a restated request is checked against, ``review.py``)."""
    marks = list(_SERVED_ITEM.finditer(text or ""))
    out: dict[int, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.setdefault(int(m.group(1)), " ".join(text[m.end() : end].split()))
    return out


def numbering(text: str) -> list[int]:
    """Every numbered request's number in the served text, in order."""
    return [int(m.group(1)) for m in _SERVED_ITEM.finditer(text or "")]


_numbering = numbering


def _fail(code: str, why: str) -> DraftingFailed:
    return DraftingFailed(f"{code}: {why}; nothing was spent")


def _whole(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def _text(job_dir: Path, i: int, raw: dict[str, Any], problems: list[str]) -> str:
    has_text, has_path = "request_text" in raw, "request_text_path" in raw
    if not (has_text or has_path) and raw.get("set_type") == "form_interrogatories" and raw.get("source"):
        return ""  # a form set the finder found: its checked numbers are the form reader's, not text
    if has_text == has_path:
        problems.append(f"set {i}: exactly one of request_text or request_text_path")
        return ""
    if has_text:
        t = raw["request_text"]
        if not isinstance(t, str) or not t.strip():
            problems.append(f"set {i}: request_text is empty")
            return ""
        return t
    rel = raw["request_text_path"]
    if not isinstance(rel, str) or not rel.strip() or Path(rel).is_absolute() or ".." in Path(rel).parts:
        problems.append(f"set {i}: request_text_path must be a path inside the job directory")
        return ""
    p = Path(job_dir) / rel
    if not p.is_file():
        problems.append(f"set {i}: request_text_path {rel} is not a file in the job directory")
        return ""
    t = p.read_text(encoding="utf-8", errors="replace")
    if not t.strip():
        problems.append(f"set {i}: {rel} is empty")
    return t


def _kind(i: int, raw: dict[str, Any], problems: list[str], other: list[str]) -> str | None:
    """The set's type, checked: a kind outside the list is ``other`` (so the
    job ends ``served_other_set``) unless recorded as type ``other`` with its label."""
    st = raw.get("set_type")
    if isinstance(st, str) and st.strip() and st not in SET_TYPES and st != OTHER:
        other.append(st.strip()[:80])
        return None
    if not isinstance(st, str) or (st not in SET_TYPES and st != OTHER):
        problems.append(f"set {i}: set_type must be one of {[*SET_TYPES, OTHER]}")
        return None
    if st == OTHER and (not isinstance(raw.get("label"), str) or not raw["label"].strip()):
        problems.append(f'set {i}: a set of type "other" needs its "label", the kind as the papers name it')
    if st != OTHER and "label" in raw:
        problems.append(f'set {i}: "label" is for a set of type "other" only')
    if st in SMD_ONLY and raw.get("drafted_by") == OPERATOR:
        problems.append(f'set {i}: a set of type other is drafted by SMD for now; mark it "drafted_by": "smd"')
    return st


def _ours(i: int, responding: str, clients: list[str] | None, problems: list[str]) -> None:
    """An Operator set answers for our client: its responding party is one of
    the client's names in the job's own record, else SMD drafts it."""
    if clients is None:
        return
    if not any(parties.same(responding, c) for c in clients):
        names = "; ".join(clients) or "none"
        problems.append(
            f'set {i}: responding_party "{responding[:80]}" is not our client on this matter (the record names: '
            f'{names[:160]}); mark it "drafted_by": "smd" or correct the party'
        )


def _values(i: int, raw: dict[str, Any], problems: list[str]) -> None:
    """The plain values' shapes: counts, parties, the drafter and the source."""
    for k in ("set_number", "request_count"):
        if k in raw and not _whole(raw[k]):
            problems.append(f"set {i}: {k} must be a whole number, 1 or more")
    for k in ("propounding_party", "responding_party"):
        if k in raw and (not isinstance(raw[k], str) or not raw[k].strip()):
            problems.append(f"set {i}: {k} must name the party")
    if "drafted_by" in raw and raw["drafted_by"] not in DRAFTERS:
        problems.append(f"set {i}: drafted_by must be one of {list(DRAFTERS)}")
    if "source" in raw and (not isinstance(raw["source"], str) or not raw["source"].strip()):
        problems.append(f"set {i}: source must name the matter file's document")
    for k in ("prior_draft", "prior_job"):
        if k in raw and (not isinstance(raw[k], str) or not raw[k].strip()):
            problems.append(f"set {i}: {k} must name the delivered draft's file and its job")
    if ("prior_draft" in raw) != ("prior_job" in raw):
        problems.append(f"set {i}: prior_draft and prior_job go together")


def _one(
    job_dir: Path,
    i: int,
    raw: Any,
    problems: list[str],
    other: list[str],
    clients: list[str] | None = None,
    form_by_operator: bool = False,
) -> ServedSet | None:
    if not isinstance(raw, dict):
        problems.append(f"set {i}: not an object")
        return None
    problems += [f"set {i}: unknown key {k!r} (closed key set)" for k in raw if k not in KEYS]
    problems += [f"set {i}: {k} required" for k in _REQUIRED if k not in raw]
    before = len(problems)
    st = _kind(i, raw, problems, other)
    if st is None and len(problems) == before:
        return None
    _values(i, raw, problems)
    text = _text(job_dir, i, raw, problems)
    if problems or st is None:
        return None
    form = served_form.fields(i, raw, problems, by_operator=form_by_operator)
    if problems:
        return None
    if raw["drafted_by"] == OPERATOR and st == served_form.FORM_TYPE:
        _ours(i, raw["responding_party"].strip(), clients, problems)
        if problems:
            return None
    elif raw["drafted_by"] == OPERATOR:
        found = _numbering(text)
        if found != list(range(1, raw["request_count"] + 1)):
            problems.append(
                f"set {i}: request_count is {raw['request_count']} but the request text numbers {len(found)} "
                'requests (each must start its line "<label> NO. <n>", 1 to the count, in order)'
            )
        _ours(i, raw["responding_party"].strip(), clients, problems)
        if problems:
            return None
    return ServedSet(
        st,
        raw["set_number"],
        raw["propounding_party"].strip(),
        raw["responding_party"].strip(),
        raw["request_count"],
        text,
        raw["drafted_by"],
        str(raw.get("label") or "").strip(),
        str(raw.get("source") or "").strip(),
        str(raw.get("prior_draft") or "").strip(),
        str(raw.get("prior_job") or "").strip(),
        **form,
    )


def read(job_dir: Path, clients: list[str] | None = None, form_by_operator: bool = False) -> list[ServedSet]:
    """The served sets SMD named, validated strictly; ``DraftingFailed`` with
    ``served_unknown``, ``served_other_set`` or ``served_invalid``."""
    p = Path(job_dir) / FILE
    if p.is_symlink() or (p.exists() and not p.is_file()):
        raise _fail("served_invalid", f"{FILE} in the job directory is not a plain file")
    if not p.is_file():
        raise _fail("served_unknown", f"SMD has not named the served sets: no {FILE} in the job directory")
    return parse(p.read_bytes(), Path(job_dir), clients, form_by_operator)


def parse(raw: bytes, base: Path, clients: list[str] | None = None, form_by_operator: bool = False) -> list[ServedSet]:
    """``read``'s validator on a file's bytes; a ``request_text_path`` is read
    from inside ``base`` (the job dir, or the staff file's own folder for the
    CLI). ``clients``: our client's names in the job's record; an Operator set
    must answer for one of them (None: not checked, a laptop rehearsal).
    ``form_by_operator``: the firm's answer (``forms.lane_hook.by_operator``);
    False refuses an Operator form set."""
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise _fail("served_invalid", f"{FILE} is not JSON ({type(exc).__name__})") from None
    if not isinstance(doc, dict) or set(doc) != {"sets"} or not isinstance(doc["sets"], list) or not doc["sets"]:
        raise _fail("served_invalid", f'{FILE} must be exactly {{"sets": [one or more sets]}}')
    problems: list[str] = []
    other: list[str] = []
    out: list[ServedSet] = []
    for i, item in enumerate(doc["sets"], 1):
        before = len(problems)
        got = _one(base, i, item, problems, other, clients, form_by_operator)
        if got is not None and len(problems) == before:
            out.append(got)
    if other:
        raise _fail(
            "served_other_set",
            "the served sets include a kind outside the list: " + ", ".join(other) + ' (record it as type "other" '
            'with its "label", drafted by smd)',
        )
    keys = [(s.set_type, s.other_label.lower(), s.set_number, s.responding_party.lower()) for s in out]
    problems += [f"{t} set {n} to {r} is named twice" for (t, _l, n, r) in {k for k in keys if keys.count(k) > 1}]
    if problems:
        raise _fail("served_invalid", f"{FILE}: " + "; ".join(problems)[:400])
    if not any(s.drafted_by == OPERATOR for s in out):
        raise _fail("served_invalid", f'{FILE} marks no set "drafted_by": "operator"; SMD drafts every one')
    if not any(s.drafted_by == OPERATOR and not s.prior_draft for s in out):
        raise _fail("served_invalid", f"{FILE}: every operator set already has a draft on the matter (prior_draft)")
    return out


def clients_of(data: Path) -> list[str]:
    """Our client's names in the job's own record: the matter record's client
    names (``facts.json``) and the caption record's client-side parties."""
    out: list[str] = []
    for name, keys in (("facts.json", ("client_names",)), ("caption.json", ())):
        p = Path(data) / name
        try:
            doc = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        except ValueError:
            doc = {}
        for k in keys:
            out += [str(x) for x in doc.get(k) or [] if str(x).strip()]
        if doc.get("client_name"):
            out.append(str(doc["client_name"]))
        out += [str(r["name"]) for r in doc.get("parties") or [] if isinstance(r, dict) and r.get("side") == "client"]
    return list(dict.fromkeys(out))


def _write_plan(data: Path, got: list[ServedSet]) -> None:
    p = Path(data) / PLAN
    tmp = p.with_name(f".{PLAN}.tmp")
    tmp.write_text(json.dumps({"sets": [asdict(s) for s in got]}, indent=1), encoding="utf-8")
    tmp.replace(p)


def frozen(data: Path, job_dir: Path, form_by_operator: bool = False) -> list[ServedSet]:
    """The job's served sets: ``data/served-plan.json`` when frozen, else
    ``read`` (checked against our client's names and the firm's form answer)
    and freeze them."""
    p = Path(data) / PLAN
    if p.is_file():
        return [ServedSet(**s) for s in json.loads(p.read_text(encoding="utf-8"))["sets"]]
    got = read(job_dir, clients_of(data), form_by_operator)
    _write_plan(data, got)
    return got


def instruction(s: ServedSet, others: list[str]) -> str:
    """What compose is told for one operator set: what to draft, and the served
    requests verbatim."""
    also = (
        f" The other served sets ({', '.join(others)}) are answered separately; do not answer any of them here."
        if others
        else ""
    )
    return (
        f"THIS DOCUMENT (set by the job, from the served sets SMD named): draft ONLY the {s.label}, as one complete "
        f"paper: {s.responding_party}'s responses to the {SET_TYPES[s.set_type][0]}, Set {s.set_number}, served by "
        f"{s.propounding_party}. It carries exactly {s.request_count} responses, numbered as served from 1 to "
        f"{s.request_count}, one to each request below and in its order; each request is restated verbatim before "
        f"its response, each labeled as the firm's own responses label them (where they show none: "
        f'"{s.request_label} NO. <n>:" and "RESPONSE TO {s.request_label} NO. <n>:").'
        + also
        + "\n\nTHE SERVED REQUESTS (verbatim, as served; never reworded, never renumbered):\n\n"
        + s.request_text.strip()
    )


def has_plan(plan: list[dict[str, Any]]) -> bool:
    """A unit plan made from served sets (each unit names its set's index)."""
    return any(isinstance(p.get("served"), int) for p in plan)


def plan_units(served: list[ServedSet]) -> list[dict[str, Any]]:
    """One unit per operator set, always keyed (so each carries its own
    instruction and its own file name), with its index in the served plan."""
    mine = [(i, s) for i, s in enumerate(served) if s.drafted_by == OPERATOR and not s.prior_draft]
    return [
        {"key": f"set-{n}", "variant": s.set_type, "label": s.label, "served": i} for n, (i, s) in enumerate(mine, 1)
    ]


def review_map(served: list[ServedSet], plan: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Unit key -> what the review's code checks count against."""
    return {
        p["key"]: {
            "set_type": served[p["served"]].set_type,
            "count": served[p["served"]].request_count,
            "requests": numbered(served[p["served"]].request_text),
            # An amended set answers only its challenged numbers (amended.py).
            **({"numbers": list(p["numbers"])} if p.get("numbers") else {}),
            **_form_review(served[p["served"]]),
        }
        for p in plan
        if isinstance(p.get("served"), int)
    }


def _form_review(s: ServedSet) -> dict[str, Any]:
    """A form set's checked numbers and form, for the review's form check."""
    if not s.checked:
        return {}
    return {"checked": list(s.checked), "form": s.form, "form_revision": s.form_revision}


def outcomes(served: list[ServedSet], plan: list[dict[str, Any]], names: dict[str, str]) -> list[dict[str, Any]]:
    """Every served set as filed (with its document's name, ``names`` keyed by
    unit key) or being prepared by us, in the served order. A set with a draft
    already on the matter is that draft, filed and told: the reply names it,
    and nothing is owed on it (``PRIOR``)."""
    by_index = {p["served"]: p["key"] for p in plan if isinstance(p.get("served"), int)}
    out = []
    for i, s in enumerate(served):
        key = by_index.get(i)
        if s.prior_draft:
            out.append({"set": s.label, "drafted_by": OPERATOR, "status": FILED, "document": s.prior_draft,
                        PRIOR: True, "prior_job": s.prior_job, "told": True})  # fmt: skip
        elif s.drafted_by == OPERATOR and key in names:
            out.append({"set": s.label, "drafted_by": s.drafted_by, "status": FILED, "document": names[key]})
        else:
            out.append({"set": s.label, "drafted_by": s.drafted_by, "status": BEING_PREPARED, "document": None})
    return out


def reply_lines(rows: list[dict[str, Any]]) -> list[str]:
    """The delivered reply's line per served set, in code (the renderer reads
    ``data/served-outcomes.json`` rows; nothing is left for a model to word)."""
    return [
        prior_line(r["set"], r["document"])
        if r.get(PRIOR)
        else f'{r["set"]}: filed as "{r["document"]}".'
        if r["status"] == FILED
        else f"{r['set']}: {BEING_PREPARED}."
        for r in rows
    ]


def prior_line(set_label: str, document: str) -> str:
    """A set whose draft was already on the matter, worded by the endings table
    (``endings.FIRM_MESSAGES["prior_draft_routed"]``); the broker's reply
    (``delivered_matter``, ``served_prior``) says the same sentence."""
    from .endings import FIRM_MESSAGES

    return FIRM_MESSAGES["prior_draft_routed"].format(item=f'{set_label}, "{document}"') + "."


def prior_reason(rows: list[dict[str, Any]]) -> str | None:
    """A delivered verdict's reason when a set was named with its draft already
    on the matter (``prior_draft_routed``, ``endings``); None otherwise."""
    named = [str(r["set"]) for r in rows if r.get(PRIOR)]
    return f"prior_draft_routed: {'; '.join(named)} already drafted on the matter" if named else None


def to_file(found: list[ServedSet]) -> str:
    """``served.json`` in its closed shape (``KEYS``), texts inlined: what ``read`` reads back."""
    rows = []
    for s in found:
        drop = {"other_label", "source", "request_text", *([] if s.form else served_form.FORM_KEYS)}
        drop |= set() if s.prior_draft else {"prior_draft", "prior_job"}
        raw = {k: (list(v) if isinstance(v, tuple) else v) for k, v in asdict(s).items() if k not in drop}
        raw.update({"request_text": s.request_text} if s.request_text else {})
        raw.update({"label": s.other_label} if s.set_type == OTHER else {})
        rows.append({**raw, **({"source": s.source} if s.source else {})})
    return json.dumps({"sets": rows}, indent=1)


def added_line(set_label: str, names: list[str]) -> str:
    """The line the attorney is told when SMD files a set it owed: the
    delivered reply's own ``served_filed`` sentence (broker ``delivered_matter``)."""
    return f"{set_label}: filed as " + " and ".join(f'"{n}"' for n in names) + "."


def write_outcomes(data: Path, rows: list[dict[str, Any]]) -> None:
    p = Path(data) / OUTCOMES
    tmp = p.with_name(f".{OUTCOMES}.tmp")
    tmp.write_text(json.dumps({"sets": rows, "lines": reply_lines(rows)}, indent=1), encoding="utf-8")
    tmp.replace(p)


SHAPE = {
    "sets": [
        {
            "set_type": f"one of {list(SET_TYPES)}",
            "set_number": "1 or more",
            "propounding_party": "as served",
            "responding_party": "as served",
            "request_count": "1 or more",
            "request_text": "the requests verbatim (or request_text_path: a file in the job dir)",
            "drafted_by": f"one of {list(DRAFTERS)}",
            "form, form_revision, checked, definition_boxes, additions": (
                "a form interrogatory set only: the form and its revision, the checked numbers in form order "
                "(request_count is their count), the checked Sec. 4(a) boxes, whether extra interrogatories are "
                "attached"
            ),
        }
    ]
}


def stop_note(job_dir: Path, data: Path, reason: str, *, served_shape: bool) -> None:
    """``smd-needs.json`` in the job directory: why the job stopped before
    spend, what SMD writes to resume it (``served.json``'s shape when it is a
    served-set ending), and the job's frozen firm wording rules, for a set SMD
    drafts by hand. Never raises: the page is told the reason either way."""
    try:
        wp = Path(data) / firm_wording.FILE
        rules = json.loads(wp.read_text(encoding="utf-8")) if wp.is_file() else None
        note: dict[str, Any] = {
            "code": reason.split(":", 1)[0],
            "reason": reason,
            "wording_rules": rules,
            "wording_rules_said": (
                "the rules frozen for this job" if rules is not None else "no wording rules frozen for this job"
            ),
        }
        if served_shape:
            note["write"] = {"file": FILE, "shape": SHAPE, "then": "resume the job (drafting_job_resume)"}
        p = Path(job_dir) / STOP_NOTE
        tmp = p.with_name(f".{STOP_NOTE}.tmp")
        tmp.write_text(json.dumps(note, indent=1), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass


# ---- the plan, before anything is paid (``DraftingRun`` mixes this in) ---------------------

#: The classes whose units are planned before anything is paid (``_plan_before_spend``):
#: a response job's served sets only. A propounded set plans after its caption stage.
PLANNED_CLASSES = ("discovery_response",)
#: The finder's stops (``served_find.py``), each before any large spend: no
#: candidate; only scans; two sets code cannot tell apart, a value it cannot
#: confirm, or a response paper it cannot pair; requests that do not number 1 to
#: the count; more than the finder reads; a prior draft of a set (a revision);
#: amended, further or supplemental responses asked for (always SMD's).
FIND_STOPS = (
    "served_none",
    "served_scan",
    "served_ambiguous",
    "served_unnumbered",
    "served_many",
    "served_prior_draft",
    "served_amended",
)
#: The plan's stops: SMD resolves each (a hand file and a resume, or a hand draft).
PLAN_STOPS = ("served_unknown", "served_other_set", "served_invalid", *FIND_STOPS)


def main(argv: list[str] | None = None, client: Any = None) -> int:
    """``python -m jobs.drafting.served``: SMD's command (``served_cli.py``)."""
    from .served_cli import main as cli

    return cli(argv, client)


if __name__ == "__main__":
    raise SystemExit(main())
