"""An attorney's change to a form set the Operator drafted, made from its saved values.

The delivered set's answer values are saved (``saved``). A change request
(the revise job's ``request_text``) is applied to THOSE values, never to the
Word file and never by a fresh draft:

* which sections change is decided by CODE: the sections of the interrogatory
  numbers the change names (``6.4``, "No. 6.4"); when it names none, every
  section. A change that only asks for layout ("each subpart on its own
  line") changes no value at all: the set is re-rendered from the saved
  values by the same code renderer, with no model call;
* each affected section gets ONE call (values only, ``answers.parse_section``),
  told the saved values of its numbers and the attorney's words as the
  instruction. Every cite is verified by the same code (``answer_stage.
  value_ok``) against what the revise job holds: the excerpts verified at
  delivery, and the attorney's own request (file ``ATTORNEY``). A value the
  attorney states is accepted as his statement when its cite quotes his
  words, and the attorney notes say "per attorney"; any other unverified
  value is the firm's no-information sentence, as at delivery;
* an unaffected section keeps its saved values, so its lines render byte for
  byte as delivered.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping

from ..evidence.shapes import DocPages
from . import answer_stage
from .answers import Answer, WorkUnit, work_units
from .catalog import Catalogue
from .saved import answer_from, answer_to

ATTORNEY = "ATTORNEY"
STAGE = "form_revise"
_NUMBER = re.compile(r"(?<![\d.])(\d{1,2}\.\d{1,2})(?![\d])")
#: A sentence about layout alone: it can change no value.
_LAYOUT = re.compile(
    r"(?i)\b(own line|separate line|new line|line break|each subpart|sub-?parts? (?:on|in)|indent|spacing|"
    r"format(?:ting)?|layout|bold|font|run together|one per line|break (?:it|them) out|list(?:ed)? separately)\b"
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


def named_numbers(change: str, checked: tuple[str, ...]) -> tuple[str, ...]:
    """The checked interrogatory numbers the change text names, in its order."""
    return tuple(dict.fromkeys(n for n in _NUMBER.findall(change) if n in checked))


def layout_only(change: str) -> bool:
    """Every sentence of the change asks for layout and nothing else."""
    parts = [p.strip() for p in _SENTENCE.split(change) if p.strip()]
    return bool(parts) and all(_LAYOUT.search(p) for p in parts)


def affected_sections(change: str, checked: tuple[str, ...], cat: Catalogue) -> tuple[WorkUnit, ...]:
    """Code's decision: none for a layout-only change; else the sections of
    the numbers named; else every section."""
    if layout_only(change):
        return ()
    groups = work_units(checked, cat)
    named = named_numbers(change, checked)
    if not named:
        return tuple(groups)
    secs = {cat.get(n).section for n in named}
    return tuple(g for g in groups if g.section in secs)


def corpus_of(answers: tuple[Answer, ...], change: str) -> dict[str, DocPages]:
    """What a revise can verify against: every excerpt verified at delivery,
    on its page, and the attorney's own words."""
    pages: dict[str, dict[int, list[str]]] = {}
    for a in answers:
        for it in a.items:
            for v in it.parts.values():
                for c in v.cites:
                    pages.setdefault(c.file, {}).setdefault(c.page, []).append(c.excerpt)
    out = {f: DocPages(f, {p: "\n".join(x) for p, x in ps.items()}) for f, ps in pages.items()}
    out[ATTORNEY] = DocPages(ATTORNEY, {1: change})
    return out


@dataclass(frozen=True)
class Revised:
    answers: tuple[Answer, ...]
    calls: int
    sections: tuple[str, ...]
    per_attorney: tuple[tuple[str, str], ...]  # (number, subpart) the attorney's words support


SYSTEM = (
    answer_stage.SYSTEM
    + " This is a CHANGE to answers already given: return every number you are given, changed only as the "
    "attorney's request asks and otherwise exactly as it stands. A value the attorney himself states is cited to "
    'file "ATTORNEY", page 1, with his words as the excerpt.'
)


def _user(group: WorkUnit, saved: list[Answer], change: str, inp: Any, cat: Catalogue) -> str:
    return "\n\n".join(
        [
            f"THE RESPONDING PARTY: {inp.responding_party}",
            f"SECTION {group.section} {group.title}: THE INTERROGATORIES (official text):\n\n"
            + answer_stage._numbers_text(group.numbers, cat),
            "THE ANSWERS AS DELIVERED (JSON):\n" + json.dumps({"answers": [answer_to(a) for a in saved]}, indent=1),
            "THE ATTORNEY'S REQUEST (the instruction):\n" + change,
        ]
    )


def revise(
    saved: tuple[Answer, ...],
    change: str,
    inp: Any,
    policy: Any,
    doorway: Any,
    model: str,
    cat: Catalogue,
    log: Callable[[str], None] = lambda _m: None,
) -> Revised:
    by = {a.number: a for a in saved}
    corpus = corpus_of(saved, change)
    groups = affected_sections(change, tuple(inp.checked), cat)
    calls = 0
    for g in groups:
        rest = tuple(n for n in g.numbers if n not in policy.not_applicable and n != answer_stage.ADMISSIONS)
        if not rest:
            continue
        g2 = replace(g, numbers=rest)
        got, c = answer_stage._parsed_with(
            doorway, model, SYSTEM, _user(g2, [by[n] for n in rest], change, inp, cat), rest, cat, STAGE
        )
        calls += c
        by.update({a.number: answer_stage.keep_verified(a, corpus) for a in got})
        log(f"  form revise: section {g.section}, {len(rest)} number(s), {c} call(s)")
    out = tuple(by[n] for n in sorted(by, key=cat.position))
    per = tuple(
        (a.number, k)
        for a in out
        for it in a.items
        for k, v in it.parts.items()
        if any(c.file == ATTORNEY for c in v.cites)
    )
    return Revised(out, calls, tuple(g.section for g in groups), per)


def load_saved(raw: Mapping[str, Any]) -> tuple[Answer, ...]:
    return tuple(answer_from(a) for a in raw["answers"])
