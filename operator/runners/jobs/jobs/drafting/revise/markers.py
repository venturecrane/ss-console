"""A drafted response's candidate-objection markers, settled by code.

A first draft marks each objection it proposes, never takes it:
``{{ATTORNEY: CANDIDATE OBJECTION: privacy | the basis ...}}`` (or the shell's
``{{CANDIDATE OBJECTION: ground | ...}}``). When the firm's rules or the
attorney's own instruction name that ground, the objection is taken: code
writes the phrase book's long form for it where the marker stood and the marker
goes. Nothing else is ever touched: every other ``{{ATTORNEY: ...}}`` and
``{{CLIENT: ...}}`` marker stays exactly as drafted.

MEASURED (2026-11-25, the firm's own revise lane rehearsed at $0 on three real
revision requests with the seat's live rules and phrase book: 1 of 23 required
changes made). The causes, each settled here:

* a ground matching TWO book entries (the firm keeps a short and a long form of
  attorney-client and of work product) was refused as ambiguous. Now one is
  chosen in code (:func:`choose`): the entry whose sentence the marker already
  holds, else the long-form entry, else the first in book order; the choice is
  recorded;
* a ground no book entry carries was never adopted, though a firm rule says to
  adopt the candidate objections. With such a rule (``adopt_own``), the
  marker's own sentence is adopted as written, its marker syntax stripped: it
  is the firm's own candidate wording, so nothing is invented;
* a rule naming "Privilege" allowed no entry, since no entry carries that
  name. Grounds and entries now also match through one table of synonyms
  (:data:`GROUNDS`);
* a ground the response already states (a rule's grid wrote it) left its
  marker beside it. The "already" check now runs for every marker, so a
  ground is never written twice.

A marker is left as drafted, and named, when its ground matches no objection in
the book and no rule adopts the candidate's own words (``phrase_missing``), or
when no rule in force and no instruction for that response names the ground
(``ground_not_named``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from ..rulebook.book import GROUNDS, entry_groups, ground_groups
from ..rulebook.types import ITEM_WORD, SET_DOC
from .checklist import _words

#: The opening of a candidate-objection marker, after its ``{{``.
_OPEN = re.compile(r"\{\{\s*(?:ATTORNEY\s*:\s*)?CANDIDATE\s+OBJECTION\b\s*[:\-–—]?\s*", re.I)
#: The word the book's ``{item}`` slot takes, per set type: the stage's own (``rulebook.types``).
ITEM = {kind: ITEM_WORD[doc] for kind, doc in SET_DOC.items()}
PHRASE_MISSING = "phrase_missing"
NOT_NAMED = "ground_not_named"
#: A marker with no ``|`` whose run holds no objection sentence to adopt.
NO_SENTENCE = "no_objection_sentence"
#: How a marker was settled when it was converted.
ONLY, HELD, LONG_FORM, BOOK_ORDER, OWN, ALREADY = "only", "held", "long_form", "book_order", "own_words", "already"

#: A firm rule that tells a draft to include objections generally, so the
#: draft's candidate objections are taken (the one place this is decided). By
#: meaning, not by one word: the firm's own rule says "include all legally
#: applicable California objections for each response" (2026-11-25), and never
#: "adopt" or "candidate". A verb of including, then objections in the plural or
#: with a word of generality ("all", "any", "applicable", "candidate"). A rule
#: asking for one ground ("add a privacy objection") is not one.
_INCLUDE = (
    r"\b(?:include|includes|including|add|adds|use|uses|assert|asserts|raise|raises|adopt|adopts|interpose|state)\b"
)
ADOPTS = re.compile(
    rf"(?i){_INCLUDE}[^.]{{0,60}}?(?:\bobjections\b|\b(?:all|any|every|each|applicable|appropriate|candidate)\b"
    rf"[^.]{{0,40}}?\bobjections?\b)"
)
#: The start of an objection sentence in a marker's run ("Responding Party objects", "Plaintiff further objects").
_OBJECTS = re.compile(r"^(?:[A-Z][\w'’-]*\s+){0,4}(?:further\s+|also\s+)?objects?\b")
#: A trailing note to the attorney: never adopted.
_NOTE = re.compile(r"(?i)^(?:\[|note\b|attorney\b|confirm\b|consider\b|verify\b|check\b|todo\b|n\.?b\.?\b)")


@dataclass(frozen=True)
class Settled:
    number: str
    key: str
    #: "" when converted; else the reason code it was left.
    left: str = ""
    ground: str = ""
    #: When converted, how: one entry, the one the marker held, the long form,
    #: the first in book order, the marker's own words, or already stated.
    how: str = ""
    #: The entries the ground matched, when more than one.
    among: tuple[str, ...] = ()


def spans(text: str) -> list[tuple[int, int, str]]:
    """Each candidate-objection marker as (start, end, its ground words): the
    marker runs to its balanced ``}}`` (a basis may carry a marker of its
    own); the ground is its text before the first ``|``."""
    return [(s, e, body.split("|", 1)[0].strip()) for s, e, body in _bodies(text)]


def _bodies(text: str) -> list[tuple[int, int, str]]:
    out = []
    for m in _OPEN.finditer(text):
        depth, i = 1, m.end()
        while i < len(text) and depth:
            if text.startswith("{{", i):
                depth, i = depth + 1, i + 2
            elif text.startswith("}}", i):
                depth, i = depth - 1, i + 2
            else:
                i += 1
        if depth:
            continue  # an unclosed marker is never cut
        out.append((m.start(), i, text[m.end() : i - 2]))
    return out


def _named(text: str, names: Iterable[str]) -> bool:
    hay = f" {_words(text)} "
    return any(_words(n) and f" {_words(n)} " in hay for n in names)


def _matches(text: str, book: Any) -> list[str]:
    groups = ground_groups(text)
    return [
        k
        for k, e in book.entries.items()
        if e.kind == "objection" and (_named(text, [*e.names]) or bool(groups & entry_groups(e)))
    ]


def ground_keys(ground: str, book: Any) -> list[str]:
    """The book's objection entries the marker's ground names: by an entry's
    short names, or by a synonym of its ground (:data:`GROUNDS`); book order."""
    return _matches(ground, book)


def named_by(texts: Iterable[str], book: Any) -> set[str]:
    """The objection keys any of ``texts`` (the rules in force) names, by a
    short name or a synonym of its ground."""
    return set(_matches(" ".join(texts), book))


_NEGATED = re.compile(r"(?i)\b(?:do\s+not|don't|never|no|without|avoid)\s+(?:\w+\s+){0,2}$")


def adopts(rules: Iterable[dict[str, Any]]) -> bool:
    """A rule in force tells the draft to include objections generally
    (:data:`ADOPTS`), so its candidate objections are taken. "Do not include
    boilerplate objections" is the opposite and never counts."""
    for r in rules:
        text = " ".join(str(w) for w in r.get("wording") or [])
        if any(not _NEGATED.search(text[: m.start()]) for m in ADOPTS.finditer(text)):
            return True
    return False


def _long_form(text: str, lead: str) -> bool:
    return bool(lead) and _words(text).startswith(_words(lead.split("{item}", 1)[0]))


def choose(keys: list[str], sentence: str, book: Any) -> tuple[str, str]:
    """(the entry, how) for a ground several entries match: the one whose
    sentence the marker already holds, else the long-form entry, else the
    first in book order."""
    if len(keys) == 1:
        return keys[0], ONLY
    said = f" {_words(sentence)} "
    held = [k for k in keys if _words(book.text(k, item="")) and _held(book.entries[k].text, said)]
    if held:
        return held[0], HELD
    lead = book.objection_lead()
    longs = [k for k in keys if _long_form(book.entries[k].text, lead)]
    if len(longs) != 1:  # the lead does not tell them apart: the one entry clearly longer than the rest
        pool = longs or keys
        sizes = sorted(((len(_words(book.entries[k].text)), -pool.index(k), k) for k in pool), reverse=True)
        longs = [sizes[0][2]] if sizes[0][0] > sizes[1][0] else []
    if longs:
        return longs[0], LONG_FORM
    return keys[0], BOOK_ORDER


def _held(entry_text: str, said: str) -> bool:
    """The marker's sentence holds the entry's words (its ``{item}`` slot any word)."""
    pieces = [p for p in (_words(x) for x in entry_text.split("{item}")) if p]
    at = 0
    for p in pieces:
        at = said.find(f" {p} ", at)
        if at < 0:
            return False
        at += len(p)
    return bool(pieces)


def own_sentence(body: str) -> str:
    """The marker's own candidate wording, its marker syntax stripped: the text
    after its first ``|``, as a sentence; or, in a marker with no ``|`` (one
    run of text), its objection sentences (:func:`objection_sentences`)."""
    if "|" not in body:
        return objection_sentences(body)
    text = " ".join(body.split("|", 1)[1].split()).strip(" ;,")
    if not text:
        return ""
    text = text[:1].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


def objection_sentences(run: str) -> str:
    """The full sentences of ``run`` that state an objection ("Responding Party
    objects ...", "Responding Party further objects ..."), from the first, while
    they last: a trailing note to the attorney ("Note: ...", "Confirm ...",
    "[...]"), or anything else, ends them. "" when the run holds none.
    2026-11-25: 7 markers had no ``|``, 3 a lone objection sentence of about 38
    words, 4 one followed by a note to the attorney."""
    text = " ".join(run.split())
    start = re.search(r"(?:[A-Z][\w'’-]*\s+){0,4}(?:further\s+|also\s+)?objects?\b", text)
    if not start:
        return ""
    kept = []
    for sentence in _sentences(text[start.start() :]):
        if _NOTE.match(sentence) or not _OBJECTS.match(sentence) or not sentence.endswith((".", "!", "?")):
            break
        kept.append(sentence)
    return " ".join(kept)


# Citation abbreviations a sentence never ends on ("Evid. Code", "Code Civ.
# Proc.", "Cal. App."). 2026-11-25: splitting at every ". " cut 9 delivered
# objection sentences after "(Evid." and "Code Civ.".
_ABBREV = re.compile(
    r"\b(?:Evid|Civ|Proc|Cal|App|Gov|Bus|Prof|Pen|Fam|Lab|Ins|Veh|Rptr|Supp|Fed|Stat|"
    r"subd|seq|Inc|Co|No|Nos|Id|id|v|vs|e\.g|i\.e|U\.S|Sec|Art|para|Corp|Ltd)\.$"
)


def _sentences(text: str) -> list[str]:
    """``text`` split into sentences: an end mark followed by space, outside
    parentheses, and not closing a citation abbreviation."""
    out, at, depth = [], 0, 0
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif (
            ch in ".!?"
            and depth == 0
            and i + 1 < len(text)
            and text[i + 1] == " "
            and not _ABBREV.search(text[at : i + 1])
        ):
            out.append(text[at : i + 1].strip())
            at = i + 1
    tail = text[at:].strip()
    return [*out, tail] if tail else out


def _unmarked(block: str) -> str:
    out, at = [], 0
    for s, e, _body in _bodies(block):
        out.append(block[at:s])
        at = e
    return "".join([*out, block[at:]])


def _tidy(block: str) -> str:
    return re.sub(r"\s+([.;,])", r"\1", " ".join(block.split()))


def _one(
    body: str, ground: str, book: Any, item: str, allowed: set[str], adopt_own: bool, whole: str
) -> tuple[str, Settled]:
    """(the words that take the marker's place, how it settled) for one marker.
    A marker with no ``|`` is one run of the draft's own objection wording, its
    ground not set apart: under a rule that includes objections its objection
    sentences are taken as written; otherwise it is settled only when its words
    name exactly one book entry."""
    piped = "|" in body
    keys = ground_keys(ground, book)
    if not piped and (adopt_own or len(keys) != 1):
        keys = []
    texts = {k: book.text(k, item=item) for k in keys}
    own = own_sentence(body) if adopt_own and not keys else ""
    stated = [k for k, t in texts.items() if f" {_words(t)} " in whole]
    if stated or (own and f" {_words(own)} " in whole):  # the response already states this ground: never twice
        return "", Settled("", stated[0] if stated else "", "", ground, ALREADY, tuple(keys))
    if not keys:
        if own:
            return own, Settled("", "", "", ground, OWN)
        why = NO_SENTENCE if adopt_own and not piped else PHRASE_MISSING
        return body, Settled("", "", why, ground)
    among = tuple(keys) if len(keys) > 1 else ()
    # A rule adopting the candidates allows every entry the ground names; else only the named ones.
    candidates = keys if adopt_own else [k for k in keys if k in allowed]
    if not candidates:
        return body, Settled("", keys[0], NOT_NAMED, ground, "", among)
    key, how = choose(candidates, body.split("|", 1)[1] if piped else body, book)
    return texts[key], Settled("", key, "", ground, how, among)


def settle(
    number: str, blocks: list[str], book: Any, set_type: str, allowed: set[str], adopt_own: bool = False
) -> tuple[list[str], list[Settled]]:
    """One response's paragraphs with its candidate markers settled. ``allowed``:
    the objection keys a rule in force or this response's instruction names.
    ``adopt_own``: a rule in force adopts the draft's candidate objections."""
    item = ITEM.get(set_type, "Request")
    # What the response states outside its markers: a marker's own words never count as stated.
    whole = " " + " ".join(_words(_unmarked(b)) for b in blocks) + " "
    out: list[str] = []
    done: list[Settled] = []
    for b in blocks:
        cuts = _bodies(b)
        if not cuts:
            out.append(b)
            continue
        new, at = [], 0
        for s, e, body in cuts:
            text, how = _one(body, body.split("|", 1)[0].strip(), book, item, allowed, adopt_own, whole)
            done.append(Settled(number, how.key, how.left, how.ground, how.how, how.among))
            if how.left:
                continue
            new += [b[at:s], text]
            at = e
            whole += f"{_words(text)} "
        new.append(b[at:])
        joined = _tidy("".join(new))
        if joined.strip(" .;,"):
            out.append(joined if joined != _tidy(b) else b)
    return out, done


__all__ = [
    "ADOPTS",
    "GROUNDS",
    "ITEM",
    "NOT_NAMED",
    "NO_SENTENCE",
    "PHRASE_MISSING",
    "Settled",
    "adopts",
    "choose",
    "ground_keys",
    "named_by",
    "objection_sentences",
    "own_sentence",
    "settle",
    "spans",
]
