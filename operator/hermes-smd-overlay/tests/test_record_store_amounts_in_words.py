"""A stored dollar figure is handed to the model in words, or not changed at all.

WHY (ss-console #3946, review of #3963). ``record_store.amounts_in_words``
rewrites ``$40k`` as ``40 thousand dollars`` so a reply that repeats a
representative's own figure does not trip the invented-price marker. A
half rewrite is worse than none: ``$5 Million`` once became
``5 dollars Million`` and ``$0.125`` became ``0.12 dollars5``, a different
number. So every scale word is read in any case (k, m, mm, b, bn, thousand,
million, billion), every decimal digit is kept, and a figure whose tail the
code does not recognise is left exactly as written; the reply gate then holds
a reply that quotes it, which is the safe failure.

FALSIFIER: on the regex as it was (case-sensitive scale, no billion, two
decimal digits, ``\\b`` after a one-letter scale), the seven "once rewritten
wrongly" rows and the five "untouched" rows fail; the rest pass.
"""

from __future__ import annotations

import pytest

from shared.record_store import amounts_in_words

CASES = [
    # once rewritten wrongly
    ("deal $5 Million", "deal 5 million dollars"),
    ("deal $1.2 billion", "deal 1.2 billion dollars"),
    ("deal $3B", "deal 3 billion dollars"),
    ("deal $5MM", "deal 5 million dollars"),
    ("fee $0.125 each", "fee 0.125 dollars each"),
    ("deal $2bn total", "deal 2 billion dollars total"),
    ("deal $7 THOUSAND", "deal 7 thousand dollars"),
    # a tail the code does not recognise: the original text, untouched
    ("code $5Mx here", "code $5Mx here"),
    ("odd $5,00 grouping", "odd $5,00 grouping"),
    ("odd $1,2345 grouping", "odd $1,2345 grouping"),
    ("odd $1.2.3 version", "odd $1.2.3 version"),
    ("odd $3Bn2 code", "odd $3Bn2 code"),
    # already worked, still work
    ("deal around $1,250.50.", "deal around 1,250.50 dollars."),
    ("deal $40000, follow up", "deal 40000 dollars, follow up"),
    ("deal $40k", "deal 40 thousand dollars"),
    ("deal $1.5M", "deal 1.5 million dollars"),
    ("deal $1.2 million", "deal 1.2 million dollars"),
    ("lunch $ 12.50 today", "lunch 12.50 dollars today"),
    ("deal $40,000, follow up", "deal 40,000 dollars, follow up"),
    ("costs $40 for lunch", "costs 40 dollars for lunch"),
    ("no figure here", "no figure here"),
]


@pytest.mark.parametrize(("text", "expected"), CASES)
def test_a_figure_is_in_words_or_untouched(text: str, expected: str) -> None:
    assert amounts_in_words(text) == expected
