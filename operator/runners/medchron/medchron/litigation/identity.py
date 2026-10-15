"""One paper, one party, however a read words them.

WHY (2026-10-14 proof run). The parity gate held a list with 29 values
"changed with no document cited", and nearly all of them were the same paper
in different words: "Form Interrogatories - General, Set One" against "Form
Interrogatories, Set One"; "Demand for Production and Inspection of
Documents" against "Requests for Production"; a deposition notice spelled
out with its date in a parenthetical; one combined name for four sets
("Form Interrogatories, Special Interrogatories, Requests for Admission and
Requests for Production, Set One") against the four separate rows the last
list carried; a party "X, Inc. (erroneously sued as Y)" against "X, Inc.".
And one defendant appeared twice because one paper dropped a letter of his
first name.

The rules here are the identity every comparison uses (parity, the update
guard, the carry-forward):

* a discovery set is its KIND and its SET NUMBER (``set_identity``); words in
  parentheses (dates, notes) are not identity;
* a combined name for several written-discovery kinds is that many sets
  (``split_rows``), and it is split where the read's answer becomes state, so
  the workbook lists one row per set, as the firm's papers are served;
* a party is ``party_key``: letters and digits, parenthetical aliases, a
  leading role word, credentials and entity suffixes dropped. A Doe number is
  identity only when it is the whole name ("Doe 2"), never beside a real name
  ("Acme LP (Doe 2)");
* two defendants on one matter whose names differ by one letter of one given
  name, with the same surname, are one person (``merge_defendants``); any
  wider difference is two parties.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from . import vocab

_NOISE = {
    "md",
    "do",
    "np",
    "pa",
    "rn",
    "dds",
    "inc",
    "llc",
    "lp",
    "llp",
    "corp",
    "co",
    "the",
    "dba",
    "aka",
    "sued",
    "as",
}
_LEADING_ROLE = re.compile(r"(?i)^\s*(?:defendants?|plaintiffs?|cross-?(?:defendants?|complainants?)|respondents?)\b")
ROLE_WORDS = re.compile(r"(?i)\b(defendants?|plaintiffs?|cross-?(?:defendants?|complainants?)|respondents?)\b")
_PARENS = re.compile(r"\([^)]*\)")


def _words(name: str) -> list[str]:
    """The name's identifying words: parentheticals, a leading role word,
    initials, credentials and entity suffixes dropped. Digits are kept (a
    number in a name is part of it)."""
    bare = _LEADING_ROLE.sub(" ", _PARENS.sub(" ", name.lower()))
    return [w for w in re.findall(r"[a-z0-9]+", bare) if (w.isdigit() or len(w) > 1) and w not in _NOISE]


def party_key(name: str) -> str:
    """One party across two lists whose names are spelled differently
    ("X (sued as Y), M.D." / "X, MD"; "Defendant X" / "X")."""
    words = _words(name)
    if words == ["doe"]:  # "Doe 2" is its number; "(Doe 2)" beside a name is an alias
        words = ["doe", *re.findall(r"\d+", name)]
    return "".join(words) or name


# ---- discovery sets ------------------------------------------------------------------
#: (kind, pattern). Order matters only for the label of a single-kind name.
_SET_KINDS = (
    ("form_interrogatories_employment", re.compile(r"(?i)form interrogator\w*\W+employment")),
    ("form_interrogatories", re.compile(r"(?i)form interrogator|\bfrogs?\b")),
    ("special_interrogatories", re.compile(r"(?i)special interrogator|\bsprogs?\b|\bsrogs?\b")),
    ("production", re.compile(r"(?i)production|inspection|\brfps?\b|\brpds?\b|\brfpds?\b")),
    ("admission", re.compile(r"(?i)admission|\brfas?\b")),
    ("deposition", re.compile(r"(?i)deposition|\bdepo\b")),
    ("subpoena", re.compile(r"(?i)subpoena")),
)
#: The kinds one combined name can carry several of (a deposition notice that
#: asks for documents is one paper, not a notice plus a production request).
_WRITTEN = (
    "form_interrogatories_employment",
    "form_interrogatories",
    "special_interrogatories",
    "admission",
    "production",
)
_LABEL = {
    "form_interrogatories_employment": "Form Interrogatories - Employment",
    "form_interrogatories": "Form Interrogatories",
    "special_interrogatories": "Special Interrogatories",
    "admission": "Requests for Admission",
    "production": "Requests for Production",
}
_NUMBERS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6"}
_SET = re.compile(r"(?i)\bset\s+(?:no\.?\s*)?(\w+)")


def set_kinds(name: str) -> list[str]:
    """Every discovery kind a set's name carries, parentheticals ignored."""
    bare = _PARENS.sub(" ", name or "")
    found = [k for k, rx in _SET_KINDS if rx.search(bare)]
    if "form_interrogatories_employment" in found:
        found.remove("form_interrogatories")
    for single in ("deposition", "subpoena"):
        if single in found:
            return [single]
    if found:
        return [k for k in _WRITTEN if k in found]
    return [re.sub(r"\W+", " ", bare.lower()).strip()]


def set_number(name: str) -> str:
    m = _SET.search(_PARENS.sub(" ", name or ""))
    return _NUMBERS.get(m.group(1).lower(), m.group(1).lower()) if m else ""


def set_identity(name: str) -> str:
    """One discovery set however a read words it: its kind and set number.
    "Form Interrogatories - General, Set One" and "Form Interrogatories, Set 1"
    are one set (2026-10-09 replay)."""
    return f"{set_kinds(name)[0]}#{set_number(name)}"


def split_rows(rows: Any) -> list[dict[str, Any]]:
    """Each row naming several written-discovery kinds becomes one row per
    kind, every value copied; a single-kind row is returned as it is."""
    out: list[dict[str, Any]] = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        name = str(r.get("set") or "")
        kinds = set_kinds(name)
        if len(kinds) < 2:
            out.append(r)
            continue
        m = _SET.search(_PARENS.sub(" ", name))
        tail = f", Set {m.group(1).capitalize()}" if m else ""
        for k in kinds:
            out.append({**copy.deepcopy(r), "set": _LABEL[k] + tail})
    return out


def row_party(r: dict[str, Any]) -> str:
    return str(r.get("served_on") or r.get("served_by") or "")


def row_key(r: dict[str, Any]) -> str:
    """``kind#number|party`` for one (already split) discovery row."""
    return f"{set_identity(str(r.get('set') or ''))}|{party_key(ROLE_WORDS.sub(' ', row_party(r)))}"


def _party_words(r: dict[str, Any]) -> frozenset[str]:
    return frozenset(w for w in _words(ROLE_WORDS.sub(" ", row_party(r))) if w != "and")


def pair_rows(old: Any, new: Any) -> dict[str, str]:
    """``{new key: old key}`` for sets whose party a read wrote shorter or
    longer than the last list did: the same kind and set number, one party's
    words all within the other's ("Bartholome, Ramirez and Roseville City
    School District" / "Bill Bartholome, Susanna Ramirez, Roseville City
    School District"; 2026-10-14 proof run). Only a key neither list shares,
    and only when exactly one row on each side can be its pair; any other
    reading of the parties stays two sets."""
    olds = {k: r for k, r in keyed_rows(old)}
    news = {k: r for k, r in keyed_rows(new)}
    lone_old = {k: r for k, r in olds.items() if k not in news}
    lone_new = {k: r for k, r in news.items() if k not in olds}

    def fits(a: dict[str, Any], b: dict[str, Any]) -> bool:
        if set_identity(str(a.get("set") or "")) != set_identity(str(b.get("set") or "")):
            return False
        wa, wb = _party_words(a), _party_words(b)
        return bool(wa and wb) and (wa <= wb or wb <= wa)

    out: dict[str, str] = {}
    for nk, nr in lone_new.items():
        cands = [ok for ok, orow in lone_old.items() if fits(orow, nr)]
        if len(cands) == 1 and [k for k, r in lone_new.items() if fits(lone_old[cands[0]], r)] == [nk]:
            out[nk] = cands[0]
    return out


def keyed_rows(rows: Any) -> list[tuple[str, dict[str, Any]]]:
    """(key, row) for every set in ``rows``, combined names split. Two rows
    that share a key (two deposition notices to one party) are told apart by
    their dates."""
    pairs = [(row_key(r), r) for r in split_rows(rows)]
    count: dict[str, int] = {}
    for k, _r in pairs:
        count[k] = count.get(k, 0) + 1
    return [(k if count[k] == 1 else f"{k}@{r.get('date')}", r) for k, r in pairs]


# ---- defendants ----------------------------------------------------------------------
def _one_edit(a: str, b: str) -> bool:
    """``a`` and ``b`` differ by exactly one inserted, dropped or changed letter."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i + (len(a) == len(b)) :] == b[i + 1 :]


def same_person(a: str, b: str) -> bool:
    """One defendant: the same ``party_key``, or the same surname (last
    word) with one given name a one-letter typo of the other. Both names
    need at least two words, the typo'd word at least four letters, and
    every other word must match; a different surname, a different number,
    or two differing words is two parties."""
    if party_key(a) == party_key(b):
        return True
    wa, wb = _words(a), _words(b)
    if len(wa) < 2 or len(wa) != len(wb) or wa[-1] != wb[-1]:
        return False
    diff = [(x, y) for x, y in zip(wa[:-1], wb[:-1]) if x != y]
    if len(diff) != 1:
        return False
    x, y = diff[0]
    return min(len(x), len(y)) >= 4 and not (x.isdigit() or y.isdigit()) and _one_edit(x, y)


def _strength(d: dict[str, Any]) -> tuple[int, int]:
    dated = sum(1 for f in ("served", "answered") if (d.get(f) or {}).get("date") and (d.get(f) or {}).get("source"))
    return (int(d.get("status") not in (None, "", vocab.D_UNCLEAR)), dated)


def merge_defendants(ds: Any) -> list[dict[str, Any]]:
    """The defendants with every near-duplicate (``same_person``) folded into
    the better-read entry: a known status over Unclear, then more cited
    dates. The kept entry takes any cited value only the other carried, and
    both entries' flags."""
    out: list[dict[str, Any]] = []
    for d in ds if isinstance(ds, list) else []:
        if not isinstance(d, dict):
            continue
        i = next(
            (i for i, k in enumerate(out) if same_person(str(k.get("name") or ""), str(d.get("name") or ""))), None
        )
        if i is None:
            out.append(d)
            continue
        keep, other = (out[i], d) if _strength(out[i]) >= _strength(d) else (d, out[i])
        keep = copy.deepcopy(keep)
        for f, key in (("served", "date"), ("answered", "date"), ("out_for_service", "value")):
            mine, theirs = keep.get(f) or {}, other.get(f) or {}
            if not mine.get(key) and theirs.get(key) and theirs.get("source"):
                keep[f] = copy.deepcopy(theirs)
        keep["flags"] = list(dict.fromkeys([*(keep.get("flags") or []), *(other.get("flags") or [])]))
        out[i] = keep
    return out
