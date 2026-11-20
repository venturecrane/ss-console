"""A checked list of form interrogatories, parsed against the form itself.

``expand("2.1-2.13, 6.1-6.7, 9.1")`` gives the numbers in form order. A range
runs over the numbers the FORM carries between its ends (2.1-2.13 is thirteen
numbers; 3.7-4.2 crosses a section and is three), never over arithmetic, so
a range cannot invent a number the form does not print. Unknown numbers,
reversed ranges and duplicates are errors, each named in a plain sentence.
``validate`` adds the count checksum (the served set's ``request_count``).
"""

from __future__ import annotations

import re

from .catalog import Catalogue, load

_TOKEN = re.compile(r"^(\d{1,3}\.\d{1,2})(?:\s*-\s*(\d{1,3}\.\d{1,2}))?$")


class CheckedError(ValueError):
    """The checked list cannot be used; ``problems`` says why, one sentence each."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def _token(tok: str, cat: Catalogue, problems: list[str]) -> list[str]:
    m = _TOKEN.match(tok)
    if not m:
        problems.append(f"{tok!r} is not an interrogatory number or a range of them (like 6.1-6.7)")
        return []
    lo, hi = m.group(1), m.group(2) or m.group(1)
    unknown = [n for n in (lo, hi) if not cat.has(n)]
    if unknown:
        problems.extend(f"{n} is not an interrogatory on {cat.form} [{cat.revision}]" for n in dict.fromkeys(unknown))
        return []
    a, b = cat.position(lo), cat.position(hi)
    if a > b:
        problems.append(f"the range {lo}-{hi} runs backwards")
        return []
    return list(cat.numbers[a : b + 1])


def expand(spec: str, cat: Catalogue | None = None) -> list[str]:
    """``spec`` (comma separated numbers and ranges) as numbers in form order."""
    cat = cat or load()
    problems: list[str] = []
    seen: list[str] = []
    for tok in (t.strip() for t in spec.replace(";", ",").split(",")):
        if not tok:
            continue
        for n in _token(tok, cat, problems):
            if n in seen:
                problems.append(f"{n} is listed more than once")
            else:
                seen.append(n)
    if not seen and not problems:
        problems.append("the checked list is empty")
    if problems:
        raise CheckedError(problems)
    return sorted(seen, key=cat.position)


def validate(checked: list[str] | tuple[str, ...], expected_count: int, cat: Catalogue | None = None) -> list[str]:
    """``checked`` (already numbers) checked against the form and the count:
    every number on the form, none twice, in form order, and exactly
    ``expected_count`` of them. Returns the list; raises with every problem."""
    cat = cat or load()
    problems: list[str] = []
    for n in dict.fromkeys(x for x in checked if not cat.has(x)):
        problems.append(f"{n} is not an interrogatory on {cat.form} [{cat.revision}]")
    dupes = sorted({n for n in checked if list(checked).count(n) > 1})
    problems.extend(f"{n} is listed more than once" for n in dupes)
    known = [n for n in checked if cat.has(n)]
    if not problems and known != sorted(known, key=cat.position):
        problems.append("the numbers are not in the form's order")
    if len(checked) != expected_count:
        problems.append(f"{len(checked)} numbers are checked but the set's count is {expected_count}")
    if problems:
        raise CheckedError(problems)
    return list(checked)


def compact(checked: list[str] | tuple[str, ...], cat: Catalogue | None = None) -> str:
    """The inverse of ``expand``: runs of consecutive form numbers within one
    section as ranges (a range never crosses a section, for the reader)."""
    cat = cat or load()
    out: list[str] = []
    run: list[str] = []
    for n in sorted(checked, key=cat.position):
        if run and cat.position(n) == cat.position(run[-1]) + 1 and n.split(".")[0] == run[-1].split(".")[0]:
            run.append(n)
            continue
        if run:
            out.append(run[0] if len(run) == 1 else f"{run[0]}-{run[-1]}")
        run = [n]
    if run:
        out.append(run[0] if len(run) == 1 else f"{run[0]}-{run[-1]}")
    return ", ".join(out)
