"""Score a drafted brief against a key of known errors.

A key row says what a draft got wrong once and what is right:

    {"id": "K07", "type": "figure", "fact": "the hospital's total charges",
     "correct_value": "$8,300.00", "source_file": "Hospital Bill.pdf", "source_page": 1,
     "wrong_as_stated": "$83,000.00", "wrong_pattern": "83,000", "correct_pattern": "8,300"}

``type`` is ``figure``, ``date``, ``quote``, ``pin``, ``fact`` (the record's
facts) or a judgment row: ``characterization``, ``omission``, ``authority``.

Each row is LOCATED in the draft by what it is about, not by its old words: a
body sentence citing the row's file within a page of ``source_page``, or one
carrying the row's correct or wrong value (money read by ``howell.money``,
dates by ``howell.parse_date``, quotes by word overlap of at least 0.8), or one
about the row's ``fact`` (overlap 0.5). Then:

* ``missed``: the wrong statement stands, unflagged: its ``wrong_pattern``
  matches (a pin: its wrong page is cited); never word overlap;
* ``caught``: it stands, but the attorney notes or an attorney marker flag it;
* ``fixed``: the right value stands where the row is about, the wrong one not;
* ``disclosed``: neither stands in the body, and the notes name it;
* ``absent``: the draft no longer makes the statement at all (a record row);
  for an ``omission`` row, absent is ``missed``.

Pure: no network, no model. The medchron parsers come from the runner tree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Mapping

import runner_path

OUTCOMES = ("fixed", "caught", "disclosed", "missed", "absent")
RECORD_TYPES = ("figure", "date", "quote", "pin", "fact")
JUDGMENT_TYPES = ("characterization", "omission", "authority")
#: The hand-verification key's own type names (2026-11-04), read as the
#: scorer's; any other type the key carries is a judgment row.
TYPE_ALIASES = {"wrong_pin_cite": "pin", "misquote": "quote", "wrong_number_or_count": "figure"}
QUOTE_OVERLAP = 0.8
FACT_OVERLAP = 0.5
_WORD = re.compile(r"[a-z0-9]+")
_MONEY = re.compile(r"\(?-?\$\s?[\d,]+(?:\.\d{2})?\)?|\b\d{1,3}(?:,\d{3})+(?:\.\d{2})?\b")
_DATE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b|"
    r"\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b"
)
#: A document cite, ``(FILE: name, p. N)`` or the older lane's ``(name, p. N)``.
_CITE = re.compile(
    r"(?:FILE:\s*|(?<=[(;])\s*(?!FILE:))(?P<name>[^,;()]+?)\s*,\s*pp?\.\s*(?P<pages>\d+(?:\s*[-–,]\s*\d+)*)", re.I
)
#: A deposition cite, ``(Surname Dep. 44:15-17)``: the transcript page.
_DEP_CITE = re.compile(r"\b(?P<who>[A-Z][A-Za-z'-]+)\s+Dep\.\s*(?P<page>\d+):\d+")
_NOTES = re.compile(r"(?im)^#{1,6}\s*(?:attorney\s+notes|notes\s+to\s+the\s+attorney|source\s+check)\b.*$")
_MARKER = re.compile(r"\{\{\s*ATTORNEY\s*:", re.I)
_STOP = frozenset("the a an of to and or in on at for by with was is were be that this from as it its".split())


@dataclass(frozen=True)
class KeyRow:
    id: str
    type: str
    fact: str
    correct_value: str
    source_file: str
    source_page: int | None
    wrong_as_stated: str
    wrong_pattern: str
    correct_pattern: str

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> "KeyRow":
        page = d.get("source_page")
        return cls(
            id=str(d["id"]),
            type=key_type(d.get("type")),
            fact=str(d.get("fact") or ""),
            correct_value=str(d.get("correct_value") or ""),
            source_file=str(d.get("source_file") or ""),
            source_page=int(page) if isinstance(page, int) or (isinstance(page, str) and page.isdigit()) else None,
            wrong_as_stated=str(d.get("wrong_as_stated") or ""),
            wrong_pattern=str(d.get("wrong_pattern") or ""),
            correct_pattern=str(d.get("correct_pattern") or ""),
        )


def key_type(raw: Any) -> str:
    t = str(raw or "fact").strip().lower()
    t = TYPE_ALIASES.get(t, t)
    return t if t in RECORD_TYPES or t in JUDGMENT_TYPES else "characterization"


def load_key(text: str) -> list[dict[str, Any]]:
    """The key's rows: a JSON list, ``{"rows": [...]}``, or JSONL (one row a line)."""
    import json

    try:
        body = json.loads(text)
    except ValueError:
        return [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    rows = body["rows"] if isinstance(body, dict) and "rows" in body else body
    return rows if isinstance(rows, list) else [rows]


@dataclass
class RowScore:
    row: KeyRow
    outcome: str
    why: str
    where: list[str] = field(default_factory=list)


@dataclass
class Score:
    rows: list[RowScore]

    def counts(self) -> dict[str, int]:
        out = {o: 0 for o in OUTCOMES}
        for r in self.rows:
            out[r.outcome] += 1
        return out

    def by_type(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for r in self.rows:
            d = out.setdefault(r.row.type, {o: 0 for o in OUTCOMES})
            d[r.outcome] += 1
        return out

    def to_json(self) -> dict[str, Any]:
        return {
            "counts": self.counts(),
            "by_type": self.by_type(),
            "rows": [{"id": r.row.id, "type": r.row.type, "outcome": r.outcome, "why": r.why} for r in self.rows],
        }


# ---- reading the draft -----------------------------------------------------------------------


def stem(name: str) -> str:
    s = re.sub(r"\.(pdf|docx?|msg|txt|rtf|jpe?g|png|tiff?)$", "", name.strip(), flags=re.I)
    return " ".join(_WORD.findall(s.casefold()))


def split_notes(text: str) -> tuple[str, str]:
    """(body, attorney notes): the notes start at their heading."""
    m = _NOTES.search(text)
    return (text[: m.start()], text[m.start() :]) if m else (text, "")


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])[\"”')\]]*\s+(?=[A-Z\"“(\[{])|\n\s*\n|\n(?=\s*(?:[-*+]|\d+\.|\|)\s)", text)
    return [" ".join(p.split()) for p in parts if p and re.search(r"[A-Za-z]", p)]


def pages_of(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        nums = [int(x) for x in re.findall(r"\d+", part)]
        if len(nums) == 2 and 0 <= nums[1] - nums[0] < 200:
            out += list(range(nums[0], nums[1] + 1))
        elif nums:
            out.append(nums[0])
    return out


def cites(sentence: str) -> list[tuple[str, int]]:
    """(file stem, page) for each ``FILE: name, p. N`` (or ``(name, p. N)``)
    in the sentence, and ("dep surname", transcript page) for each deposition
    cite."""
    out = [(stem(m.group("name")), p) for m in _CITE.finditer(sentence) for p in pages_of(m.group("pages"))]
    return out + [(f"dep {m.group('who').casefold()}", int(m.group("page"))) for m in _DEP_CITE.finditer(sentence)]


def same_file(cited: str, file: str) -> bool:
    """A cite names the key row's file: by stem, or a deposition by the
    deponent's surname in the transcript's file name."""
    if cited == file:
        return True
    return cited.startswith("dep ") and cited[4:] in file.split()


def tokens(s: str) -> set[str]:
    return {t for t in _WORD.findall(s.casefold()) if t not in _STOP}


def overlap(needle: str, hay: str) -> float:
    n = tokens(needle)
    return len(n & tokens(hay)) / len(n) if n else 0.0


def money_values(s: str) -> set[Decimal]:
    h = runner_path.howell()
    out = set()
    for m in _MONEY.finditer(s):
        v = h.money(m.group(0))
        if v is not None:
            out.add(abs(v))
    return out


def date_values(s: str) -> set[str]:
    h = runner_path.howell()
    out = set()
    for m in _DATE.finditer(s):
        d = h.parse_date(m.group(0).replace(".", ""))
        if d is not None:
            out.add(d.isoformat())
    return out


def has_value(kind: str, value: str, sentence: str) -> bool:
    """Whether ``sentence`` carries ``value``, read the way the row's type is."""
    if not value.strip():
        return False
    if kind == "figure":
        want = money_values(value)
        if want:
            return bool(want & money_values(sentence))
    if kind == "date":
        want_d = date_values(value)
        if want_d:
            return bool(want_d & date_values(sentence))
    if kind == "pin":
        return False
    floor = QUOTE_OVERLAP if kind in ("quote", "fact") else FACT_OVERLAP
    return overlap(value, sentence) >= floor


def _pattern(p: str, s: str) -> bool:
    if not p:
        return False
    try:
        return re.search(p, s, re.I) is not None
    except re.error:
        return p.casefold() in s.casefold()


def _page_ok(want: int | None, got: int, pad: int) -> bool:
    return want is None or abs(got - want) <= pad


def _pin(v: str) -> int | None:
    n = re.findall(r"\d+", v or "")
    return int(n[-1]) if n else None


# ---- scoring -----------------------------------------------------------------------------------


def locate(row: KeyRow, sents: Iterable[str]) -> list[str]:
    file = stem(row.source_file)
    out = []
    for s in sents:
        about = overlap(row.fact, s) >= FACT_OVERLAP if row.fact else False
        cited = any(same_file(st, file) and _page_ok(row.source_page, p, 1) for st, p in cites(s)) if file else False
        valued = row.type != "pin" and (
            has_value(row.type, row.correct_value, s) or has_value(row.type, row.wrong_as_stated, s)
        )
        if about or (cited and row.type != "pin") or valued or _pattern(row.wrong_pattern, s):
            out.append(s)
    return out


def _wrong(row: KeyRow, s: str) -> bool:
    """The wrong statement stands: a pin by its wrong page, anything else ONLY
    by the row's ``wrong_pattern``. Word overlap with the wrong wording is not
    a test: a corrected sentence shares most of its words (2026-11-04: it
    flagged a corrected control exactly as it flagged the draft)."""
    if row.type == "pin":
        bad = _pin(row.wrong_as_stated)
        return bad is not None and any(same_file(st, stem(row.source_file)) and p == bad for st, p in cites(s))
    return _pattern(row.wrong_pattern, s)


def _right(row: KeyRow, s: str) -> bool:
    if row.type == "pin":
        good = _pin(row.correct_value) or row.source_page
        return good is not None and any(same_file(st, stem(row.source_file)) and p == good for st, p in cites(s))
    if row.type == "omission":
        return _pattern(row.correct_pattern, s) or overlap(row.correct_value, s) >= FACT_OVERLAP
    return _pattern(row.correct_pattern, s) or has_value(row.type, row.correct_value, s)


def _noted(row: KeyRow, notes: list[str]) -> bool:
    for s in notes:
        if _pattern(row.wrong_pattern, s) or _pattern(row.correct_pattern, s):
            return True
        if row.fact and overlap(row.fact, s) >= FACT_OVERLAP:
            return True
    return False


def score_row(row: KeyRow, body: list[str], notes: list[str]) -> RowScore:
    here = locate(row, body)
    wrong = [s for s in here if _wrong(row, s)]
    right = [s for s in here if _right(row, s)]
    noted = _noted(row, notes) or any(_MARKER.search(s) for s in wrong)
    if wrong:
        return RowScore(row, "caught" if noted else "missed", "the wrong statement stands", wrong[:3])
    if right:
        return RowScore(row, "fixed", "the right value stands where the row is about", right[:3])
    if noted:
        return RowScore(row, "disclosed", "the notes name it")
    if row.type == "omission":
        return RowScore(row, "missed", "the contrary record is neither in the brief nor its notes")
    return RowScore(row, "absent", "the draft no longer makes this statement")


def score(rows: Iterable[KeyRow | Mapping[str, Any]], draft: str) -> Score:
    body_text, notes_text = split_notes(draft)
    body, notes = sentences(body_text), sentences(notes_text)
    keyed = [r if isinstance(r, KeyRow) else KeyRow.from_json(r) for r in rows]
    return Score([score_row(r, body, notes) for r in keyed])


# ---- the evidence lane's drafts: scored from the render span map ---------------------------


def _relevant(row: KeyRow, spans: Iterable[Any], ledger: Any, page: int | None) -> list[Any]:
    """Spans with a fact in the row's file, on ``page`` (any page when None)."""
    file = stem(row.source_file)
    out = []
    for sp in spans:
        for fid in sp.ids:
            f = ledger.get(fid)
            if f is not None and stem(f.file) == file and (page is None or f.page == page):
                out.append(sp)
                break
    return out


def _record_from_spans(row: KeyRow, spans: list[Any], ledger: Any) -> RowScore:
    if row.type == "pin":
        good = _pin(row.correct_value) or row.source_page
        bad = _pin(row.wrong_as_stated)
        if good is not None and _relevant(row, spans, ledger, good):
            return RowScore(row, "fixed", "a code-rendered cite of the file is on the right page")
        if bad is not None and _relevant(row, spans, ledger, bad):
            return RowScore(row, "missed", "a code-rendered cite of the file is on the wrong page")
        return RowScore(row, "absent", "no rendered span cites the file there")
    here = _relevant(row, spans, ledger, row.source_page)
    if not here:
        return RowScore(row, "absent", "no rendered span traces to the row's file and page")
    texts = [sp.text for sp in here]
    if row.type == "figure" and money_values(row.correct_value):
        ok = any(money_values(row.correct_value) & money_values(t) for t in texts)
    elif row.type == "date" and date_values(row.correct_value):
        ok = any(date_values(row.correct_value) & date_values(t) for t in texts)
    elif row.type == "quote" and row.correct_value:
        ok = any(overlap(row.correct_value, t) >= QUOTE_OVERLAP for t in texts)
    else:
        ok = True  # a fact traced to its file and page
    if ok:
        return RowScore(row, "fixed", "the rendered value is the right one", texts[:3])
    return RowScore(row, "missed", "a rendered span at the row's file and page carries another value", texts[:3])


def _judged(row: KeyRow, check: Mapping[str, Any] | None, notes: list[str]) -> RowScore:
    items = [i for i in (check or {}).get("items") or [] if not i.get("control")]
    for i in items:
        if i.get("grade") not in ("wrong", "framing"):
            continue
        text = " ".join(str(i.get(k) or "") for k in ("sentence", "preview", "note"))
        if _pattern(row.wrong_pattern, text) or (row.fact and overlap(row.fact, text) >= FACT_OVERLAP):
            return RowScore(row, "caught", f"the source check graded it {i.get('grade')}", [text[:300]])
    if _noted(row, notes):
        return RowScore(row, "disclosed", "the attorney notes name it")
    return RowScore(row, "missed", "neither the source check nor the notes name it")


def score_spans(
    rows: Iterable[KeyRow | Mapping[str, Any]],
    spans: Iterable[Any],
    ledger: Any,
    check: Mapping[str, Any] | None = None,
    notes: str = "",
) -> Score:
    """A new-lane draft scored from what code rendered: each record row by the
    spans whose facts sit at the row's file and page (fixed, missed or
    absent); each judgment row by the source check's report (caught) and the
    attorney notes (disclosed)."""
    sp = list(spans)
    note_sents = sentences(notes)
    out = []
    for r in rows:
        row = r if isinstance(r, KeyRow) else KeyRow.from_json(r)
        if row.type in RECORD_TYPES:
            out.append(_record_from_spans(row, sp, ledger))
        else:
            out.append(_judged(row, check, note_sents))
    return Score(out)
