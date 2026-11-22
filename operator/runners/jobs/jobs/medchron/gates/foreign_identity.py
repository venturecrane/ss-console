"""medchron_foreign_identity: does a document's text name a DIFFERENT person
than the client it is about to be read for?

Found live 2026-10-13: an update was about to fold another person's emailed
records into a client's file. An email attachment arrives with no folder to
say whose it is, and a document an agent names by id carries no folder rule
either, so the only evidence left is the document's own text. This reads the
two things a medical record labels its patient with:

* a labeled date of birth ("DOB:", "Date of Birth:", "Birth Date:") that is
  not the client's date of birth; a line that labels a relative or a payer
  ("guarantor", "insured", "subscriber", "parent", "mother", "father",
  "spouse", "emergency contact") is that person's, not the patient's, and is
  not read;
* a labeled patient name ("Patient:", "Patient Name:") that is not the
  client: one without the client's surname, or (2026-11-22) one that carries
  the surname under another first name (a household member treated by the
  same providers reached a delivered chronology twice, 2026-11-19).

The date of birth is read where a form prints it (2026-11-22): on the label's
line after fill underscores, as a numeric or a word-month date, on the next
line, or in a label column whose values are printed below it in the same
order (``_column``, bounded: at most ``COLUMN_MAX`` labels, their values the
lines right after them). A relative's word on a line keeps that line's date
of birth unread unless a patient label is nearer before it; it never hides a
patient name.

A hit is never a silent skip and never a fold. Until 2026-10-24 it was a HOLD
at the caller and the firm was asked whose the record was. Since then (the
Captain: a chronology reaches the firm only when it is delivered) a hit is
WITHHELD BY RULE (``withhold_by_rule``): the document joins no unit, the run
goes on, and the delivered package says it was left out and why, without a
word about the other person. The decision is written where a person's would be
(``identity_release.json`` in the job's matter directory,
``{"release": [...], "withhold": [<id or sha12>, ...], "decisions": [...]}``),
recorded ``by: "rule"``, so a later run never reads it again. A co-plaintiff's
records inside a client's chronology is the defect class (ss#2893), and a
record wrongly withheld is recoverable. Text that names no one is not evidence
of anyone: it passes.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

#: A date-of-birth label; its value is read by ``_date_at`` after it.
DOB_TAG = re.compile(r"(?i)(?<![A-Za-z])(?:D\.?O\.?B\b\.?|Date\s+of\s+Birth|Birth\s*Date)\s*[:#]?")
_MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
#: A date as a form prints a birth date: 08/02/2008, 02-Aug-2008, Aug 2, 2008.
DATE_VALUE = re.compile(
    r"(?i)(?P<m>\d{1,2})[/-](?P<d>\d{1,2})[/-](?P<y>\d{4}|\d{2})(?!\d)"
    r"|(?P<d2>\d{1,2})[\s-]+(?P<mw>[A-Za-z]{3,9})\.?[\s,-]+(?P<y2>\d{4})(?!\d)"
    r"|(?P<mw3>[A-Za-z]{3,9})\.?\s+(?P<d3>\d{1,2}),?\s+(?P<y3>\d{4})(?!\d)"
)
#: What a form prints between a label and its value: spaces, underscores, dots.
_FILL = "\t _."
# The label is case-blind, the name is not: two capitalised words ("Jane Doe",
# "DOE, JANE"), so "Patient: denies pain" is not read as a name. And it is a
# LABEL only at the start of a line or after a field delimiter (a bar, a
# semicolon, a tab, two spaces): a form QUESTION ("Does the patient: Yes No",
# "arranged for the patient:Yes IHSS") held a client's own records twice
# (2026-10-14).
PATIENT_LABEL = re.compile(
    r"(?m)(?:^|(?<=[|;\t])|(?<=\s\s))[ \t]*(?i:patient(?:\s+name)?)\s*[:#]\s*"
    r"([A-Z][A-Za-z'\-]+(?:,\s*|\s+)[A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-.]*){0,2})"
)
#: Form answers and checkbox tokens: a "name" made only of these is a box.
ANSWER = frozenset("yes no n/a na none unknown unk y n x checked unchecked true false".split())
#: Capitalised words a form puts after "Patient:" that are not a name.
NOT_NAME = frozenset(
    "denies reports states presents complains seen here was is has had states noted tolerated returns arrived "
    "advised instructed declined education name information history status signature consent".split()
)
RELATIVE = re.compile(r"(?i)\b(?:guarantor|insured|subscriber|policy ?holder|parent|mother|father|spouse|emergency)\b")
_PATIENT_WORD = re.compile(r"(?i)\bpatient\b")
#: A line that is only a label ("Date of Birth:", "Patient Name: ____").
LABEL_ONLY = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 .#/'()&-]{0,40}?)\s*[:#][\s_.]*$")
#: The most labels a label column is read for (``_column``).
COLUMN_MAX = 12
#: Words a printed name carries that are no given name.
_NOT_GIVEN = frozenset("mr mrs ms miss dr jr sr ii iii iv".split())
RELEASE_FILE = "identity_release.json"
#: How a withheld document is disclosed (orphans.json, the coverage record, an
#: update's skip list). What happened to the file, nothing about whose it is:
#: the person it names is another client's or a stranger's, and the chronology
#: is the wrong place to say either.
WITHHELD_REASON = "left out at the firm's direction"
#: Who records a withhold the rule made (``record_decision``'s ``by``).
RULE_BY = "rule"
#: The rule's two grounds, each with its disclosure. Factual, about the file
#: only: never the other person's name or date of birth.
RULE_ANOTHER_PERSON = "another_person"  # its own text names someone other than the client
RULE_UNPLACED = "unplaced"  # a joint matter's file that no client's folder or name owns
RULE_REASONS = {
    RULE_ANOTHER_PERSON: "names a person other than the client, so it was left out of this chronology",
    RULE_UNPLACED: "could not be placed with one client on this matter, so it was left out of this chronology",
}
#: Every disclosure a withheld document can carry: what counts as accounted for.
WITHHELD_REASONS = frozenset({WITHHELD_REASON, *RULE_REASONS.values()})


def _norm_dob(mo: str, dy: str, yr: str) -> str | None:
    y = int(yr)
    y = y + (1900 if y > 30 else 2000) if y < 100 else y
    try:
        return f"{int(mo):02d}/{int(dy):02d}/{y:04d}"
    except ValueError:
        return None


def _date(m: re.Match[str]) -> str | None:
    """A ``DATE_VALUE`` match as MM/DD/YYYY; None for a word that is no month."""
    if m.group("m"):
        return _norm_dob(m.group("m"), m.group("d"), m.group("y"))
    word, day, year = m.group("mw", "d2", "y2") if m.group("mw") else m.group("mw3", "d3", "y3")
    mo = _MONTHS.get(word[:3].lower())
    return _norm_dob(str(mo), day, year) if mo else None


def _date_at(rest: str, whole: bool = False) -> str | None:
    """The date `rest` starts with, past fill; with `whole`, only when nothing
    but fill follows it either."""
    s = rest.lstrip(_FILL)
    m = DATE_VALUE.match(s)
    if not m or (whole and s[m.end() :].strip(_FILL)):
        return None
    return _date(m)


def _relatives(line: str, at: int) -> bool:
    """Whether the date of birth labelled at `at` is a relative's: the line
    names one, and no patient label stands nearer before the label."""
    if not RELATIVE.search(line):
        return False
    rel = [m.start() for m in RELATIVE.finditer(line[:at])]
    pat = [m.start() for m in _PATIENT_WORD.finditer(line[:at])]
    return not (pat and (not rel or pat[-1] > rel[-1]))


def _column(lines: list[str]) -> list[tuple[str, str]]:
    """("dob" | "name", value) for each date-of-birth or patient label alone
    on its line, read from the line holding its value: a run of such label
    lines (at most ``COLUMN_MAX``) is followed by its values, one per line in
    the same order (one label: the next line). Blank lines are not counted.
    A run naming a relative, or right under a line that does, is not read."""
    rows = [ln for ln in lines if ln.strip()]
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(rows):
        if not LABEL_ONLY.match(rows[i]):
            i += 1
            continue
        j = i
        while j < len(rows) and LABEL_ONLY.match(rows[j]):
            j += 1
        labels, values = rows[i:j], rows[j : j + (j - i)]
        before = rows[i - 1] if i else ""
        kin = any(RELATIVE.search(lb) for lb in labels) or bool(
            RELATIVE.search(before) and not _PATIENT_WORD.search(before)
        )
        if len(labels) <= COLUMN_MAX and len(values) == len(labels) and not kin:
            for lb, val in zip(labels, values):
                m = LABEL_ONLY.match(lb)
                head = m.group(1).strip() if m else ""
                if DOB_TAG.fullmatch(head):
                    d = _date_at(val, whole=True)
                    if d:
                        out.append(("dob", d))
                elif re.fullmatch(r"(?i)patient(?:\s+name)?", head):
                    out.append(("name", val.strip()))
        i = j
    return out


def full_name(name: str, after: str) -> bool:
    """True when the capitalised words after "Patient:" are a person's name:
    none is a form word ("Denies", "Reports"), and the name ends where a name
    ends (the line, a delimiter, another label, or running lowercase text),
    so "Patient: Denies Neck Pain" is not read as someone else."""
    words = re.findall(r"[A-Za-z'\-/]+", name)
    if any(w.lower() in NOT_NAME for w in words) or all(w.lower() in ANSWER for w in words):
        return False
    rest = after.lstrip()
    if not rest or rest[:1].islower():
        return True
    return bool(re.match(r"(?:[|,;:(]|(?i:dob|d\.o\.b|date|mrn|acct|account|sex|age|id)\b)", rest))


def _has(word: str, text: str) -> bool:
    return bool(re.search(rf"(?i)(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", text))


def _first(name: str, sur: set[str]) -> str | None:
    """The given name a printed name leads with ("Doe, Jane A" and "Jane A
    Doe" both: Jane): its first word that is no surname of the job and no
    title."""
    words = re.findall(r"[A-Za-z][A-Za-z'\-.]*", name.split(",", 1)[1] if "," in name else name)
    for w in words:
        if not any(_has(s, w) for s in sur) and w.lower().rstrip(".") not in _NOT_GIVEN:
            return w.rstrip(".")
    return None


def _is_first(word: str, firsts: set[str]) -> bool:
    """`word` is one of `firsts`: the same name, its initial, or the one
    written short ("Chris" for "Christopher", three letters at least)."""
    w = word.lower()
    for f in (x.lower() for x in firsts if x):
        if w == f or (len(w) == 1 and f.startswith(w)):
            return True
        if min(len(w), len(f)) >= 3 and (f.startswith(w) or w.startswith(f)):
            return True
    return False


def _conflicts(name: str, sur: set[str], firsts: set[str], text: str) -> bool:
    """True when a "Patient:" name is someone else's. A name without the
    client's surname that carries the client's FIRST name, in a document that
    names the client's surname elsewhere, is the client written short (a
    billing summary's "Patient: Alpha" held a real update, 2026-10-13); it
    does not conflict. Any other name without the surname does. A name WITH
    the surname conflicts when it leads with a given name that is no unit's
    (2026-11-22: a household member who shares the client's surname); one
    that leads with a unit's first name or its initial is that unit."""
    if not sur:
        return False
    if any(_has(s, name) for s in sur):
        given = _first(name, sur)
        return bool(firsts and given and not _is_first(given, firsts))
    first_only = any(_has(f, name) for f in firsts)
    return not (first_only and any(_has(s, text) for s in sur))


def first_names(units: list[Any]) -> set[str]:
    """The first word of each unit's client name."""
    return {str(u.client_name).split()[0] for u in units if str(getattr(u, "client_name", "") or "").split()}


def findings(
    text: str, surnames: set[str], dobs: set[str], firsts: set[str] | None = None, context: str | None = None
) -> list[str]:
    """What in `text` names someone other than the client (surnames, first
    names and MM/DD/YYYY dobs of every unit on the job); [] when nothing
    does. `context`: the whole document `text` is a page of, where the
    client's surname is looked for (default `text`)."""
    out: list[str] = []
    sur = {s.lower() for s in surnames if s}
    ctx = text if context is None else context
    for kind, value in labelled(text):
        if kind == "dob" and dobs and value not in dobs:
            out.append(f"a date of birth {value} that is not the client's")
        elif kind == "name":
            out += _name_finding(value, sur, firsts or set(), ctx)
    return sorted(set(out))


def labelled(text: str) -> list[tuple[str, str]]:
    """("dob" | "name", value) for every patient identifier `text` labels:
    each date of birth that is not a relative's, and each patient name."""
    out: list[tuple[str, str]] = []
    lines = (text or "").splitlines()
    for line in lines:
        for m in DOB_TAG.finditer(line):
            d = _date_at(line[m.end() :])
            if d and not _relatives(line, m.start()):
                out.append(("dob", d))
        # A patient label is the patient's though its line names a relative too.
        for m in PATIENT_LABEL.finditer(line):
            if full_name(m.group(1), line[m.end() :]):
                out.append(("name", m.group(1)))
    for kind, value in _column(lines):
        if kind == "dob" or (COLUMN_NAME.fullmatch(value) and full_name(value, "")):
            out.append((kind, value))
    return out


def names_client(text: str, surnames: set[str], dobs: set[str], firsts: set[str]) -> bool:
    """Whether `text` labels a patient as the client: the client's date of
    birth, or a patient name that carries the surname and is not another
    person (``_conflicts``)."""
    sur = {s.lower() for s in surnames if s}
    for kind, value in labelled(text):
        if kind == "dob" and value in dobs:
            return True
        if kind == "name" and any(_has(s, value) for s in sur) and not _conflicts(value, sur, firsts, text):
            return True
    return False


#: A value line under a "Patient Name:" label that is a name (``PATIENT_LABEL``'s name).
COLUMN_NAME = re.compile(r"[A-Z][A-Za-z'\-]+(?:,\s*|\s+)[A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-.]*){0,2}")


def _name_finding(name: str, sur: set[str], firsts: set[str], ctx: str) -> list[str]:
    if not _conflicts(name, sur, firsts, ctx):
        return []
    if any(_has(s, name) for s in sur):
        return ["a patient name with the client's surname and another first name"]
    return ["a patient name without the client's surname"]


def _decided(slug_dir: Path, key: str) -> set[str]:
    p = slug_dir / RELEASE_FILE
    if not p.is_file():
        return set()
    try:
        return {str(x) for x in (json.loads(p.read_text(encoding="utf-8")) or {}).get(key) or []}
    except (OSError, ValueError, AttributeError):
        return set()


def released(slug_dir: Path) -> set[str]:
    """Held documents a person read and found to be the client's: folded in."""
    return _decided(slug_dir, "release")


def withheld(slug_dir: Path) -> set[str]:
    """Held documents the firm said to leave out (2026-10-22: an attorney
    answered an identity hold with "Just leave it out of the chronology", and
    the runner had no way to act on it). A withheld document stays out of every
    unit, never holds the run again, and is disclosed as left out."""
    return _decided(slug_dir, "withhold")


def matches(rid: str, ids: set[str]) -> bool:
    """`rid` is named in `ids` by its id or by the sha12 inside a folded
    attachment's ``msgatt-<sha12>`` id; a decision may carry either."""
    rid = str(rid)
    return rid in ids or rid.removeprefix("msgatt-") in ids or f"msgatt-{rid}" in ids


def record_decision(slug_dir: Path, *, action: str, ids: list[str], by: str, source: str, at: str) -> dict[str, Any]:
    """Add one identity decision to `RELEASE_FILE`, keeping every earlier one.

    `action` is ``release`` (the document is the client's) or ``withhold``
    (leave it out). A document is never both: the later decision replaces the
    earlier one, so the file always says what the next run will do. Who decided
    and where they said it ride in ``decisions``, because a withheld record is
    a choice about the firm's own file and has to be traceable to the person
    who made it."""
    if action not in ("release", "withhold"):
        raise ValueError(f"action must be release or withhold, not {action!r}")
    if not ids or not by.strip() or not source.strip():
        raise ValueError("a decision needs the documents, who decided, and where they said it")
    p = slug_dir / RELEASE_FILE
    body: dict[str, Any] = {}
    if p.is_file():
        body = json.loads(p.read_text(encoding="utf-8")) or {}
    other = "withhold" if action == "release" else "release"
    keep = [x for x in body.get(other) or [] if not any(matches(str(x), {i}) or matches(i, {str(x)}) for i in ids)]
    body[other] = keep
    body[action] = sorted({*(str(x) for x in body.get(action) or []), *ids})
    body.setdefault("decisions", []).append({"action": action, "ids": list(ids), "by": by, "source": source, "at": at})
    p.write_text(json.dumps(body, indent=1, sort_keys=True), encoding="utf-8")
    return body


#: The tests a finding comes from, as the codes a withhold records (codes
#: only, never the value found): a labelled date of birth that is not the
#: client's, and a labelled patient name without the client's surname.
TEST_DOB, TEST_NAME = "dob", "name"
ALL_TESTS = frozenset({TEST_DOB, TEST_NAME})
#: Why a by-rule withhold stood on a run that could not run its test again.
KEPT_CANNOT_RETEST = "cannot re-test"


def tests_of(found: list[str]) -> list[str]:
    """The test codes behind `found` (``findings``' own strings)."""
    out = set()
    for f in found:
        if "date of birth" in f:
            out.add(TEST_DOB)
        if "patient name" in f:
            out.add(TEST_NAME)
    return sorted(out)


def runnable(surnames: set[str], dobs: set[str]) -> set[str]:
    """The tests this job can run: the date-of-birth test needs a date of
    birth (``findings`` makes no such finding without one), the name test a
    surname (``_conflicts`` finds none without one)."""
    return ({TEST_DOB} if any(dobs) else set()) | ({TEST_NAME} if any(surnames) else set())


def withhold_by_rule(
    slug_dir: Path, ids: list[str], *, ground: str, at: str | None = None, tests: dict[str, list[str]] | None = None
) -> dict[str, Any]:
    """Withhold `ids` by rule (2026-10-24): the decision
    ``identity_decide --withhold`` records, made by the rule instead of a
    person. ``ground`` is a key of ``RULE_REASONS``; it rides as the decision's
    ``source`` so the disclosure can say why. `tests`: {id: the test codes
    that withheld it} (``tests_of``), recorded on the decision so a later run
    clears it only by running the same test (``clear_by_rule``)."""
    if ground not in RULE_REASONS:
        raise ValueError(f"ground must be one of {sorted(RULE_REASONS)}, not {ground!r}")
    stamp = at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    body = record_decision(slug_dir, action="withhold", ids=list(ids), by=RULE_BY, source=f"rule:{ground}", at=stamp)
    if tests:
        body["decisions"][-1]["tests"] = {str(i): sorted(set(t)) for i, t in tests.items() if str(i) in set(ids)}
        (slug_dir / RELEASE_FILE).write_text(json.dumps(body, indent=1, sort_keys=True), encoding="utf-8")
    return body


def rule_tests(slug_dir: Path, rid: str) -> set[str] | None:
    """The tests the rule's latest withhold of `rid` recorded, or None when
    that decision recorded none (a withhold made before 2026-11-17)."""
    p = slug_dir / RELEASE_FILE
    try:
        decisions = (json.loads(p.read_text(encoding="utf-8")) or {}).get("decisions") or [] if p.is_file() else []
    except (OSError, ValueError, AttributeError):
        return None
    for dec in reversed(decisions):
        if isinstance(dec, dict) and dec.get("action") == "withhold" and dec.get("by") == RULE_BY:
            if any(matches(str(rid), {str(i)}) for i in dec.get("ids") or []):
                got = (dec.get("tests") or {}).get(str(rid)) if isinstance(dec.get("tests"), dict) else None
                return {str(t) for t in got} if isinstance(got, list) and got else None
    return None


def by_rule(slug_dir: Path, rid: str, ground: str) -> bool:
    """Whether `rid` is withheld and the latest decision naming it was the
    rule's on `ground` (never a person's: the firm's direction is sticky)."""
    return matches(str(rid), withheld(slug_dir)) and withheld_reason(slug_dir, rid) == RULE_REASONS[ground]


def clear_by_rule(
    slug_dir: Path, ids: list[str], *, ground: str, can_run: set[str], at: str | None = None
) -> tuple[list[str], list[str]]:
    """Take back the rule's withhold of `ids`, which this run's tests no
    longer find (2026-11-17): (cleared, kept). A WITHHOLD FAILS TOWARD
    WITHHOLDING: it is cleared only when this run could run every test that
    withheld it (`can_run`, ``runnable``) and so its finding nothing means
    something. A job with no date of birth makes no date-of-birth finding at
    all, so a withhold that rested on one (a same-surname relative's record)
    is KEPT, ``KEPT_CANNOT_RETEST``. A withhold that recorded no test is kept
    unless every test can run. Only ids the rule itself withheld on `ground`
    are touched; a person's decision is never undone here. A clear is
    recorded as a decision row (``action: "cleared"``)."""
    mine = [i for i in ids if by_rule(slug_dir, i, ground)]
    kept = [i for i in mine if not (rule_tests(slug_dir, i) or ALL_TESTS) <= can_run]
    gone = [i for i in mine if i not in kept]
    if not gone:
        return [], kept
    p = slug_dir / RELEASE_FILE
    body = json.loads(p.read_text(encoding="utf-8")) or {}
    body["withhold"] = [
        x for x in body.get("withhold") or [] if not any(matches(str(x), {i}) or matches(i, {str(x)}) for i in gone)
    ]
    stamp = at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    body.setdefault("decisions", []).append(
        {"action": "cleared", "ids": gone, "by": RULE_BY, "source": f"rule:{ground}", "at": stamp}
    )
    p.write_text(json.dumps(body, indent=1, sort_keys=True), encoding="utf-8")
    return gone, kept


def withheld_reason(slug_dir: Path, rid: str) -> str:
    """How a withheld document is disclosed: the rule's own wording when the
    latest decision naming it was the rule's, else ``WITHHELD_REASON`` (a
    person recorded the firm's direction)."""
    p = slug_dir / RELEASE_FILE
    decisions: list[Any] = []
    if p.is_file():
        try:
            decisions = (json.loads(p.read_text(encoding="utf-8")) or {}).get("decisions") or []
        except (OSError, ValueError, AttributeError):
            decisions = []
    for dec in reversed(decisions):
        if not isinstance(dec, dict) or not any(matches(str(rid), {str(i)}) for i in dec.get("ids") or []):
            continue
        if dec.get("action") != "withhold" or dec.get("by") != RULE_BY:
            return WITHHELD_REASON
        return RULE_REASONS.get(str(dec.get("source") or "").removeprefix("rule:"), WITHHELD_REASON)
    return WITHHELD_REASON


def identities(units: list[Any]) -> tuple[set[str], set[str]]:
    """(surnames, dobs) of the job's units."""
    return {u.surname for u in units if u.surname}, {u.dob for u in units if u.dob}


def pdf_text(path: Path) -> str:
    """The text layer of a PDF (or a Word file), '' for anything without one."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            from ...matter import pdf as matter_pdf

            return "\n".join(matter_pdf.page_texts(path))
        if suffix == ".docx":
            import docx

            return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    except Exception:  # noqa: BLE001 - an unreadable attachment has no text to name anyone; vision reads it later and the identity stage checks again
        return ""
    return ""
