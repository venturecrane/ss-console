"""A person the court's caption prints surname first ("DOE, JANE"), as a
request or a drafted set writes them given name first ("Jane Doe").

2026-11-23: a request named four individual defendants given name first; the
caption printed each "SURNAME, GIVEN", so no name matched, the job fell to the
every-defendant path and stopped after it was paid. ``parties`` decides which
caption names are a person printed this way; this module only reads the words.
"""

from __future__ import annotations


def middle_ok(caption: list[str], request: list[str]) -> bool:
    """Middle names that agree: the same words, one word on one side only, or
    one an initial of the other."""
    if caption == request:
        return True
    if not caption or not request:
        return len(caption) + len(request) == 1
    if len(caption) == len(request) == 1:
        a, b = caption[0], request[0]
        return (len(a) == 1 and b.startswith(a)) or (len(b) == 1 and a.startswith(b))
    return False


def spans(words: list[str], given: list[str], surname: list[str], skip: frozenset[str]) -> list[tuple[int, int]]:
    """(first, last) word index of each place ``words`` prints the person given
    name first: the first given name, an agreeing middle name (``middle_ok``;
    never a word in ``skip``, such as "and"), then the whole surname."""
    out: list[tuple[int, int]] = []
    for i, w in enumerate(words):
        if w != given[0]:
            continue
        for n in range(4):  # the middle words the text prints
            j = i + 1 + n
            mid = words[i + 1 : j]
            if words[j : j + len(surname)] == surname and not set(mid) & skip and middle_ok(given[1:], mid):
                out.append((i, j + len(surname) - 1))
                break
    return out


def spellings(name: str, given: str, surname: str) -> list[str]:
    """``name`` as the caption prints it, then given name first, with and
    without a middle name."""
    giv, sur = " ".join(given.split()), " ".join(surname.split())
    out = [name, f"{giv} {sur}"]
    if len(giv.split()) > 1:
        out.append(f"{giv.split()[0]} {sur}")
    return out
