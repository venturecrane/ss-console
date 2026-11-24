"""A ground the phrase book has no sentence for, written for this request
(the S amendment, Captain approved 2026-11-23).

One model call per document writes one sentence per (response, ground) in the
firm's long-form objection shape: the words every objection entry of the book
starts with (``Book.objection_lead``, "Responding Party objects to this
{item} on the ..."). Code keeps a sentence only when it:

* starts with that lead, ``{item}`` filled with the response's word;
* names its ground (the ground was already found in the rule that calls for it,
  ``reading.py``);
* is one sentence, ending in a full stop;
* carries no number, date, dollar figure or quoted term (``checks.tokens``) and
  no capitalised name that the request, the rule and the book do not already hold.

A refused answer is asked once more; a sentence still refused is left, and the
response is named in the notes as having no standard wording
(``phrase_missing``). A sentence kept is named "written for this request".
No book, or a book whose objections share no lead, writes nothing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..revise.checks import tokens
from ..verify.items import parse_json
from .book import Book, fold, norm
from .types import Ask

STAGE = "rulebook-write"
MAX_TOKENS = 4_000


def _words(text: str) -> str:
    """Words only: "attorney-client" and "attorney client" are one ground."""
    return " ".join(re.findall(r"\w+", fold(text)))


_NAME = re.compile(r"\b[A-Z][a-z]+\b")
_ABBREV = re.compile(r"\b(?:No|Nos|Inc|Co|Corp|Ltd|Civ|Proc|Code|Cal|App|Dr|Mr|Mrs|Ms)\.", re.I)


@dataclass(frozen=True)
class Need:
    """One response that needs an objection on a ground the book has no sentence for."""

    item: str
    ground: str
    rule: str
    request: str
    rule_text: str


SYSTEM = (
    "You write one objection sentence for a law firm's discovery response, on a ground its standard wording does "
    'not cover, in the firm\'s own long form. Return ONLY a JSON object {"sentences": [{"item": <response '
    'number>, "ground": <the ground as given>, "sentence": <one sentence>}]}, one per need. Each sentence '
    "starts with the exact lead given, names its ground, is one sentence, and states no fact, name, number or "
    "date: no person, provider, amount or date of the case."
)


def user(needs: list[Need], lead: str, problems: list[str] | None = None) -> str:
    lines = [f"THE LEAD EVERY SENTENCE STARTS WITH: {lead}", "\nTHE NEEDS:"]
    for n in needs:
        lines.append(f"\n- item {n.item}, ground {n.ground!r}, from the rule: {n.rule_text}\n  request: {n.request}")
    if problems:
        lines.append("\nYOUR LAST ANSWER WAS REFUSED BY CODE FOR:\n" + "\n".join(f"- {p}" for p in problems))
    return "\n".join(lines) + "\n"


def check_one(sentence: str, need: Need, lead: str, book: Book) -> str:
    """ "" when the sentence may be written, else what is wrong."""
    s = norm(sentence)
    if not fold(s).startswith(fold(lead)):
        return "it does not start with the lead"
    if f" {_words(need.ground)} " not in f" {_words(s)} ":
        return f"it does not name the ground {need.ground!r}"
    if not s.endswith(".") or re.search(r"[.!?;]\s", _ABBREV.sub("", s[:-1])):
        return "it is not one sentence ending in a full stop"
    known = " ".join([need.request, need.rule_text, *(e.text for e in book.entries.values())])
    extra = tokens(s) - tokens(known)
    if extra:
        return f"it states {sorted(extra)[0]!r}, which the request and the rule do not"
    names = [w for w in _NAME.findall(s[len(lead) :]) if w.casefold() not in fold(known)]
    return f"it names {names[0]!r}, which the request and the rule do not" if names else ""


def _check(raw: Any, needs: list[Need], lead: str, book: Book) -> tuple[dict[tuple[str, str], str], list[str]]:
    rows = raw.get("sentences") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return {}, ['the answer is an object {"sentences": [...]}']
    want = {(n.item, n.ground): n for n in needs}
    out: dict[tuple[str, str], str] = {}
    problems: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        k = (str(row.get("item") or "").strip(), fold(str(row.get("ground") or "")))
        if k not in want or k in out:
            problems.append(f"item {k[0]} ground {k[1]!r}: not one of the needs, or given twice")
            continue
        bad = check_one(str(row.get("sentence") or ""), want[k], lead, book)
        if bad:
            problems.append(f"item {k[0]} ground {k[1]!r}: {bad}")
        else:
            out[k] = norm(str(row["sentence"]))
    problems += [
        f"item {i} ground {g!r}: missing" for (i, g) in want if (i, g) not in out and not _named(problems, i, g)
    ]
    return out, problems


def _named(problems: list[str], item: str, ground: str) -> bool:
    return any(p.startswith(f"item {item} ground {ground!r}:") for p in problems)


def write(needs: list[Need], item_word: str, book: Book, call: Ask | None) -> dict[tuple[str, str], str]:
    """{(item, ground): the sentence} for each need code accepted; the rest are left."""
    lead = book.objection_lead().replace("{item}", item_word)
    if not needs or not lead or call is None:
        return {}
    got: dict[tuple[str, str], str] = {}
    problems: list[str] | None = None
    for _ in range(2):
        todo = [n for n in needs if (n.item, n.ground) not in got]
        if not todo:
            break
        more, problems = _check(
            parse_json(call(STAGE, SYSTEM, user(todo, lead, problems), MAX_TOKENS)), todo, lead, book
        )
        got.update(more)
    return got


def to_json(got: dict[tuple[str, str], str]) -> str:
    return json.dumps([{"item": i, "ground": g, "sentence": s} for (i, g), s in got.items()], indent=1)


def from_json(text: str) -> dict[tuple[str, str], str]:
    return {(r["item"], r["ground"]): r["sentence"] for r in json.loads(text)}
