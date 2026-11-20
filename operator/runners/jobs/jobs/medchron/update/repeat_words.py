"""The repeats veto's word legs: a paragraph may go only when the keeper's
quoted sentences carry every word it says. $0; the legs can only KEEP.

WHY (2026-11-20, a bug review run on the first version): a list of polarity
words let through every difference the list did not name: cervical against
lumbar, one medicine against another, one doctor against another, sprain
against fracture, "two" against "three", mcg against mg, "discontinues"
against "continues", and a paragraph dropped on a one-word quote. No list of
words that matter can be complete, so the rule is the other way round:
EVERY word of the paragraph must be in the quoted sentences, but for the
short fixed list of FUNCTION words below. No stemming, no synonyms. A
negation or state word is never a function word.

* ``tokens``: words and numbers, citations removed, lowercased; a unit after a
  number is a token of its own, so 5 mcg against 5 mg keeps; and each number
  with the token after it must be found so in the quotes (``unit``: "5 mg"
  against "5-10 mg" keeps).
* A quote must be a WHOLE sentence of the keeper, of at least MIN_QUOTE
  tokens (``whole_sentence``).
* ``word_legs``: every token of the paragraph is among the quotes' tokens
  (``words``); every sentence of the paragraph has all its tokens in ONE
  quoted sentence, and a sentence holding a NEGATION token equals a quoted
  sentence token for token, in order (``sentence``: "no left knee pain;
  right knee pain" has the same words as its reverse).
"""

from __future__ import annotations

import re

from . import body as B

#: The only words a paragraph may say that its quotes do not (articles,
#: prepositions, conjunctions, copulas). Never a negation or a state word.
FUNCTION = frozenset(
    {"the", "a", "an", "of", "and", "to", "in", "on", "at", "with", "for", "was", "were", "is", "are", "by",
     "from", "that", "this", "as", "or"}
)  # fmt: skip
NEGATION = frozenset({"no", "not", "without", "denies", "never", "non", "neither", "nor", "negative"})
MIN_QUOTE = 3
_TOKEN = re.compile(r"\d+(?:[.,/:]\d+)*|[a-z]+")
_SENTENCE = re.compile(r"(?<=[.!?;])\s+")


def tokens(text: str) -> list[str]:
    """Words and numbers, in order: citations removed, lowercased, "n't" as "not"."""
    return _TOKEN.findall(B.CITE.sub(" ", text).lower().replace("n't", " not"))


def sentences(text: str) -> list[str]:
    """The sentences of `text`, line by line, citations removed, whitespace collapsed."""
    out: list[str] = []
    for line in B.CITE.sub(" ", text).split("\n"):
        out += [s for s in (" ".join(p.split()) for p in _SENTENCE.split(line)) if s]
    return out


def _bare(s: str) -> str:
    return " ".join(B.CITE.sub(" ", s).split()).rstrip(".;:!? ")


def whole_sentence(quote: str, block: str) -> bool:
    """True when `quote` is one whole sentence of `block`, or a run of whole
    sentences one after another ("A; B."), punctuation at its end aside."""
    q, ss = _bare(quote), sentences(block)
    if not q:
        return False
    for i in range(len(ss)):
        for j in range(i + 1, len(ss) + 1):
            run = _bare(" ".join(ss[i:j]))
            if run == q:
                return True
            if len(run) > len(q):
                break
    return False


def short(quotes: list[str]) -> bool:
    return any(len(tokens(q)) < MIN_QUOTE for q in quotes)


def _content(ts: list[str]) -> set[str]:
    return set(ts) - FUNCTION


def _units(ts: list[str]) -> set[tuple[str, str]]:
    """Each number with the token after it: "5 mg" is not "5-10 mg"."""
    return {(a, b) for a, b in zip(ts, ts[1:]) if a[0].isdigit()}


def word_legs(body: str, quotes: list[str]) -> list[str]:
    """The word legs `body` fails against `quotes`; [] passes."""
    said = [tokens(s) for q in quotes for s in sentences(q)]  # a quoted run counts sentence by sentence
    have = {t for ts in said for t in ts}
    out = []
    if _content(tokens(body)) - have:
        out.append("words")
    if {u for s in sentences(body) for u in _units(tokens(s))} - {u for ts in said for u in _units(ts)}:
        out.append("unit")
    for s in sentences(body):
        mine = tokens(s)
        if not any(_content(mine) <= set(q) for q in said):
            out.append("sentence")
            break
        if NEGATION & set(mine) and mine not in said:
            out.append("sentence")
            break
    return out
