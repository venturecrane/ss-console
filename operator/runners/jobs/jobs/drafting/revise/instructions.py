"""The attorney's own instructions in a revision email, read once per job and
checked by code before any of them changes a word (design A6, "request items").

"For No. 9 add a relevance objection" is an instruction about one response of
one document. One model call reads the email and returns items::

    {"items": [{"quote": "<his words, copied>", "numbers": ["9"],
                "set_type": "<only when his words name the set>",
                "action": "use_phrase" | "add_objection" | "remove_objection" | "remove_sentence" | "add_fact",
                "keys": ["<phrase book keys>"],
                "text": "<add_fact only: the sentence stating his fact>"}]}

Code keeps an item only when:

* the quote is in the email (words compared, ``checklist._quoted``);
* the response numbers code reads in the quote (``numbers.py``) are exactly the
  numbers returned, and each is a response of exactly ONE document in scope (of
  the set the quote or the item names, when one is named);
* the action is in the closed set, the book keys exist and are of the kind the
  action writes, and each key's short name is in the quote: his words chose
  the objection, the model only found its key;
* the quote does not say the opposite: a negator against an add, an add word
  against a removal;
* ``add_fact``: every figure and every capitalised name in the sentence is in
  his quote. That sentence is the one place a model's words reach a filed
  response, and only with nothing in it he did not say; the notes name it as
  per attorney.

And every response number the email names must sit inside a kept quote, so no
instruction he gave is dropped. Anything refused is asked once more, told what
failed; refused again, nothing is filed (``InstructionsRefused``). An email that
names no response number makes no call at all: it carries no instruction this
reads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from ..rulebook.book import fold
from ..rulebook.types import Instruction
from ..verify.items import parse_json
from . import numbers as num, set_units as su
from .checklist import _quoted, _words

Ask = Callable[[str, str, str, int], str]
STAGE = "revise-request"
MAX_TOKENS = 4_000
SOURCE = "email"
#: The closed set of what an instruction may ask. ``add_fact`` is applied by
#: this package (``facts``); the rest go to the firm-rules stage as Instructions.
ACTS = ("use_phrase", "add_objection", "remove_objection", "remove_sentence", "add_fact")
#: The book kind each act's keys must be.
KEY_KIND = {"use_phrase": "disposition", "add_objection": "objection", "remove_objection": "objection"}
_NEGATOR = re.compile(r"(?i)\b(?:not|don'?t|doesn'?t|never|no longer|without|instead of|rather than)\b")
_ADD = re.compile(r"(?i)\b(?:add|include|insert|assert|put in)\b")
_REMOVE = re.compile(r"(?i)\b(?:remove|delete|drop|take out|strike|withdraw|cut|omit)\b")
_FIGURE = re.compile(r"\$?\d[\d,./-]*")
_CAPITAL = re.compile(r"\b[A-Z][A-Za-z'’-]+\b")
#: Capitalised words a sentence about a response may carry without his saying
#: them: role words and the sentence's own grammar.
_PLAIN = {
    "responding", "party", "plaintiff", "plaintiffs", "defendant", "defendants", "the", "a", "an", "client", "he",
    "she", "they", "it", "no", "yes", "on", "in", "at", "as", "this", "that", "his", "her", "their", "request",
    "response", "interrogatory", "exhibit", "i", "we", "our",
}  # fmt: skip
FACT_MAX = 500


@dataclass(frozen=True)
class Fact:
    """A fact the attorney stated for one response, written by ``facts.py``."""

    unit: str
    number: str
    text: str
    quote: str


@dataclass(frozen=True)
class Read:
    """Per document key, its instructions; and the attorney's stated facts."""

    instructions: dict[str, tuple[Instruction, ...]]
    facts: tuple[Fact, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "instructions": {k: [vars(i) | {"numbers": list(i.numbers), "keys": list(i.keys),
                                            "grounds": list(i.grounds)} for i in v]
                             for k, v in self.instructions.items()},
            "facts": [vars(f) for f in self.facts],
        }  # fmt: skip

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> "Read":
        ins = {
            k: tuple(
                Instruction(
                    **{
                        **i,
                        "numbers": tuple(i["numbers"]),
                        "keys": tuple(i["keys"]),
                        "grounds": tuple(i.get("grounds") or ()),
                    }
                )
                for i in v
            )
            for k, v in (raw.get("instructions") or {}).items()
        }
        return Read(ins, tuple(Fact(**f) for f in raw.get("facts") or []))


@dataclass(frozen=True)
class Doc:
    """A document in scope: its key, set type and response numbers."""

    key: str
    set_type: str
    numbers: tuple[str, ...]


class InstructionsRefused(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems)[:480])
        self.problems = problems


SYSTEM = (
    "You read a law firm attorney's email asking for changes to discovery responses the firm already filed, and "
    "list each instruction he gives about a numbered response. You never write the firm's wording. Return ONLY a "
    'JSON object {"items": [...]}, each {"quote": <his words for this instruction, copied exactly from the email, '
    'including the response number(s)>, "numbers": [<each response number his quote names, as a string; a range such as 7-9 is each number in it>], '
    '"set_type": <only when his words name the set: one of special_interrogatories, form_interrogatories, '
    'requests_for_production, requests_for_admission; else "">, "action": one of use_phrase (use the firm\'s '
    "standard disposition wording), add_objection, remove_objection, remove_sentence, add_fact (he states a fact "
    'the response must say), "keys": [<for use_phrase and add_objection: the key of each phrase-book entry his '
    'words ask for, from the list given>], "grounds": [<add_objection only: each objection ground he asks for that no '
    'phrase-book entry covers, in his own words as the quote says it>], "text": <add_fact only: one sentence stating his fact, using only his '
    "words, names and figures>}. List every instruction that names a response number; leave out general rules "
    "that name none."
)


def user(request_text: str, docs: list[Doc], book: Any, problems: list[str] | None = None) -> str:
    lines = ["THE ATTORNEY'S EMAIL:", request_text.strip(), "", "THE DOCUMENTS IT MAY CHANGE:"]
    lines += [f"- {d.set_type}: responses {', '.join(d.numbers)}" for d in docs]
    lines.append("\nTHE FIRM'S PHRASE BOOK (key, kind, its short names):")
    for e in book.entries.values() if book is not None else ():
        lines.append(f"- {e.key} ({e.kind}): {', '.join(e.names) or e.key}")
    if problems:
        lines.append("\nYOUR LAST ANSWER WAS REFUSED BY CODE FOR:\n" + "\n".join(f"- {p}" for p in problems))
    return "\n".join(lines) + "\n"


def _named_set(quote: str) -> set[str]:
    return su.named_set_types(quote)


def _home(nums: tuple[str, ...], docs: list[Doc], set_type: str) -> tuple[Doc | None, str]:
    """The one document every number belongs to, or (None, why)."""
    pool = [d for d in docs if not set_type or d.set_type == set_type]
    homes: set[str] = set()
    for n in nums:
        hits = [d for d in pool if n in d.numbers]
        if len(hits) != 1:
            where = "no document in scope" if not hits else f"{len(hits)} documents (say which set)"
            return None, f"response {n} is in {where}"
        homes.add(hits[0].key)
    if len(homes) != 1:
        return None, "its numbers are in different documents (one instruction per set)"
    return next(d for d in pool if d.key in homes), ""


def _grounds_ok(act: str, grounds: tuple[str, ...], quote: str, book: Any) -> str:
    if grounds and act != "add_objection":
        return "grounds are given only for an objection"
    entries = book.entries.values() if book is not None else ()
    for g in grounds:
        if not g or f" {_words(g)} " not in f" {_words(quote)} ":
            return f"ground {g!r} is not named in the quote"
        for e in entries:
            if e.kind == "objection" and _words(g) in {_words(n) for n in (*e.names, e.key.replace("_", " "))}:
                return f"ground {g!r} is the phrase-book entry {e.key!r}; give its key"
    return ""


def _keys_ok(act: str, keys: tuple[str, ...], quote: str, book: Any, grounds: tuple[str, ...] = ()) -> str:
    if act in ("remove_sentence", "add_fact"):
        return "keys are given only for a phrase or an objection" if keys else ""
    if act == "use_phrase" and not keys:
        return f"{act} names at least one phrase-book key"
    if act == "add_objection" and not keys and not grounds:
        return f"{act} names at least one phrase-book key or ground"
    for k in keys:
        e = (book.entries if book is not None else {}).get(k)
        if e is None:
            return f"key {k!r} is not in the firm's phrase book"
        if e.kind != KEY_KIND[act]:
            return f"key {k!r} is a {e.kind}, not a {KEY_KIND[act]}"
        names = [*e.names, e.key.replace("_", " ")]
        if not any(_words(n) and f" {_words(n)} " in f" {_words(quote)} " for n in names):
            return f"key {k!r}: none of its names ({', '.join(e.names) or e.key}) is in the quote"
    return ""


def _sense_ok(act: str, quote: str) -> str:
    if act == "add_fact":
        return ""  # his fact may itself be a negative ("did not have UM coverage")
    if act in ("add_objection", "use_phrase"):
        if _NEGATOR.search(quote):
            return "the quote says not to; an add is refused"
    elif _ADD.search(quote) and not _REMOVE.search(quote):
        return "the quote asks to add, not to remove"
    return ""


def _fact_ok(text: str, quote: str) -> str:
    if not text or len(text) > FACT_MAX or "{{" in text or "}}" in text or not text.rstrip().endswith("."):
        return f"add_fact carries one sentence of at most {FACT_MAX} characters, ending in a period"
    said = f" {_words(quote)} "
    for f in _FIGURE.findall(text):
        if f" {_words(f)} " not in said:
            return f"the figure {f!r} is not in the attorney's words"
    for i, m in enumerate(_CAPITAL.finditer(text)):
        w = m.group(0)
        if (i == 0 and m.start() == 0) or w.casefold() in _PLAIN:
            continue
        if f" {_words(w)} " not in said:
            return f"the name {w!r} is not in the attorney's words"
    return ""


def _item(n: int, it: Any, request_text: str, docs: list[Doc], book: Any) -> tuple[Any, str]:
    """((its document's key, an Instruction or a Fact), "") or (None, why it was refused)."""
    if not isinstance(it, dict):
        return None, f"item {n} is an object"
    quote = " ".join(str(it.get("quote") or "").split())
    act, where = it.get("action"), f"item {n} ({quote[:60]!r})"
    if not quote or not _quoted(quote, request_text):
        return None, f"{where}: the quote is not copied from the email"
    if act not in ACTS:
        return None, f"{where}: action is one of {list(ACTS)}"
    got = it.get("numbers")
    if not isinstance(got, list) or not all(isinstance(x, (str, int)) and not isinstance(x, bool) for x in got):
        return None, f"{where}: numbers is a list of response numbers"
    nums, read = tuple(str(x).strip() for x in got), num.numbers_in(quote)
    if not nums or set(nums) != set(read):
        return None, f"{where}: the quote names responses {list(read)}, the item {list(nums)}"
    st = str(it.get("set_type") or "")
    named = _named_set(quote)
    if st and st not in su.NAMED:
        return None, f"{where}: set_type {st!r} is not a set type"
    if named and len(named) == 1:
        st = st or next(iter(named))
        if st not in named:
            return None, f"{where}: the quote names {sorted(named)}, the item {st}"
    home, why = _home(read, docs, st)
    if home is None:
        return None, f"{where}: {why}"
    keys = tuple(str(k) for k in it.get("keys") or [])
    raw_g = it.get("grounds") or []
    grounds = tuple(fold(str(g)) for g in raw_g) if isinstance(raw_g, list) else ()
    bad = (
        _keys_ok(str(act), keys, quote, book, grounds)
        or _grounds_ok(str(act), grounds, quote, book)
        or _sense_ok(str(act), quote)
    )
    if bad:
        return None, f"{where}: {bad}"
    if act == "add_fact":
        text = " ".join(str(it.get("text") or "").split())
        bad = _fact_ok(text, quote) or ("" if len(read) == 1 else "a fact is stated for one response")
        return (None, f"{where}: {bad}") if bad else ((home.key, Fact(home.key, read[0], text, quote)), "")
    return (home.key, Instruction(SOURCE, quote, read, home.set_type, str(act), keys, grounds)), ""


def _spans(quote: str, text: str) -> list[tuple[int, int]]:
    """Where ``quote``'s words stand in ``text``, as character spans."""
    words = [(m.start(), m.end(), m.group(0).lower()) for m in re.finditer(r"\w+", text)]
    want = _words(quote).split()
    out = []
    for i in range(len(words) - len(want) + 1):
        if want and [w[2] for w in words[i : i + len(want)]] == want:
            start = words[i][0]
            # "#22" is one mention starting at the "#"; a quote's words start at the digits.
            while start and text[start - 1] == "#":
                start -= 1
            out.append((start, words[i + len(want) - 1][1]))
    return out


def uncovered(request_text: str, quotes: list[str]) -> list[str]:
    """Each place the email names a response number outside every kept quote."""
    spans = [s for q in quotes for s in _spans(q, request_text)]
    out = []
    for s, e, nums in num.mentions(request_text):
        if not any(a <= s and e <= b for a, b in spans):
            out.append(f"the email names response(s) {', '.join(nums)} in words no instruction kept")
    return out


def check(raw: Any, request_text: str, docs: list[Doc], book: Any) -> tuple[Read, list[str]]:
    got = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(got, list):
        return Read({}), ['the answer is an object {"items": [...]}']
    kept: dict[str, list[Instruction]] = {}
    facts: list[Fact] = []
    quotes: list[str] = []
    problems: list[str] = []
    for n, it in enumerate(got, 1):
        got_one, why = _item(n, it, request_text, docs, book)
        if why:
            problems.append(why)
            continue
        key, val = got_one
        quotes.append(val.quote)
        if isinstance(val, Fact):
            facts.append(val)
        else:
            kept.setdefault(key, []).append(val)
    problems += uncovered(request_text, quotes)
    return Read({k: tuple(v) for k, v in kept.items()}, tuple(facts)), problems


def read(request_text: str, docs: list[Doc], book: Any, ask: Ask) -> Read:
    """The email's instructions; ``InstructionsRefused`` after the one retry.
    No call when the email names no response number."""
    if not num.mentions(request_text):
        return Read({})
    problems: list[str] | None = None
    for _ in range(2):
        raw = parse_json(ask(STAGE, SYSTEM, user(request_text, docs, book, problems), MAX_TOKENS))
        got, problems = check(raw, request_text, docs, book)
        if not problems:
            return got
    raise InstructionsRefused(problems or ["no answer"])


def plan_chars(request_text: str, docs: list[Doc], book: Any) -> int:
    """The call and its retry, priced before any; 0 when no call is made."""
    if not num.mentions(request_text):
        return 0
    return 2 * (len(SYSTEM) + len(user(request_text, docs, book)) + 5 * MAX_TOKENS)


__all__ = ["ACTS", "Doc", "Fact", "InstructionsRefused", "Read", "check", "plan_chars", "read", "uncovered"]
