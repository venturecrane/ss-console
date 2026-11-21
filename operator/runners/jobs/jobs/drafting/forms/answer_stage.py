"""Form interrogatory answers from the matter file: one model call per section.

The unit path for a served form set (``lane_hook``): no compose, no audit, no
repair loop. Per ``answers.work_units(checked)`` section, ONE call is given the
section's official text and the shape of its subparts, the matter record and
the Medicals tab, the one responding party, the firm's wording rules placed
for form sets, and the section's source text (``pack_for``). It returns
values only (``answers.parse_section``). Code then:

* verifies every cite's excerpt by its words on the page it names
  (``evidence/verify.py``: ``resolve``, ``clean_page``, ``locate``), and every
  dollar figure in a value against its excerpts (no arithmetic: a total is
  stated only where a page prints it);
* gives the numbers whose values failed ONE scoped retry, those numbers only;
* turns each value that still fails into the firm's no-information sentence
  (never a stop, never an invented value);
* fills by code, with no call, every number the firm's policy fixes as not
  applicable for its role, and 17.1 (the policy's sentence when no requests
  for admission were served in the plan, an attorney marker when they were).

``pack_for`` is the response scope's pack for the section's group
(``drafting/response_scope.pack``, ``group_of_section``).
"""

from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .. import response_scope
from ..evidence import verify
from ..evidence.shapes import DocPages
from .answers import Answer, AnswerError, Cite, Item, Value, WorkUnit, parse_section, work_units
from .catalog import Catalogue
from .policy import ResponsePolicy

STAGE = "form_answers"
MAX_TOKENS = 16000
#: The default pack's ceiling (design D: form packs run 100k to 400k characters).
PACK_MAX_CHARS = 400_000
ADMISSIONS = "17.1"
ADMISSIONS_MARKER = (
    "{{ATTORNEY: requests for admission were served with these interrogatories; answer 17.1 for each response "
    "that is not an unqualified admission}}"
)
_MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?")
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


@dataclass(frozen=True)
class Inputs:
    """What every call of one form set is given."""

    checked: tuple[str, ...]
    responding_party: str
    admissions_served: bool
    matter_record: tuple[str, ...]
    medicals: Any
    rules_block: str
    definition_boxes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Result:
    answers: tuple[Answer, ...]
    calls: int
    failed_values: int  # values that became the no-information sentence after the retry
    cites: tuple[tuple[str, str, str, int], ...]  # (number, subpart, file, page), verified, in order


def pack_for(unit: Any, group: WorkUnit, corpus: Mapping[str, DocPages]) -> str:
    """The source text for one section group: the response scope's pack for
    the section's group (``response_scope.pack``) from the job's data directory
    (the unit's parent), ranked by the matter's providers. A call with no unit
    (no job around it) reads the whole extracted file, capped."""
    if unit is None:
        return _whole_file(corpus)
    data = Path(unit.data).parent if getattr(unit, "key", "") else Path(unit.data)
    fp = data / "facts.json"
    facts = json.loads(fp.read_text(encoding="utf-8")) if fp.is_file() else {}
    got = response_scope.pack(
        data,
        response_scope.FORM,
        response_scope.group_of_section(group.section),
        providers=response_scope.providers_of(facts),
        terms=(),
    )
    return got.text


def _whole_file(corpus: Mapping[str, DocPages]) -> str:
    """Every extracted page, file by file, capped at ``PACK_MAX_CHARS``."""
    parts: list[str] = []
    size = 0
    for name, doc in corpus.items():
        for n in sorted(doc.pages):
            chunk = f"=== FILE: {name} | page {n} ===\n{doc.pages[n].strip()}\n"
            if size + len(chunk) > PACK_MAX_CHARS:
                return "".join(parts)
            parts.append(chunk)
            size += len(chunk)
    return "".join(parts)


def _numbers_text(nums: tuple[str, ...], cat: Catalogue) -> str:
    out = []
    for n in nums:
        it = cat.get(n)
        subs = "".join(f"\n  ({k}) {t}" for k, t in it.subparts)
        shape = f"subparts {list(it.letters)}, each answered in parts" if it.letters else "no subparts: answer in text"
        out.append(f"{n} [{shape}; one item per {it.repeats_over}]: {it.stem}{subs}")
    return "\n\n".join(out)


SYSTEM = (
    "You answer checked Judicial Council form interrogatories for the responding party from the matter file only. "
    "Reply with JSON only, in exactly this shape: "
    '{"answers": [{"number": "<n>", "kind": "answered", "items": [{"name": "<who or what>", "parts": {"<letter>": '
    '{"text": "<value>", "cites": [{"file": "<file name as given>", "page": <page>, "excerpt": "<words copied from '
    'that page>"}]}}}]}, {"number": "<n>", "kind": "none" | "no_information"}]}. '
    "An interrogatory without subparts gives each item text and cites instead of parts. "
    "Rules: give one answer for every number you are given and no other. kind is none when the file shows the "
    "answer is no, no_information when the file does not say; never guess a value the file does not state. "
    "Give one item for each provider, employer, person or item the interrogatory repeats over, each with its "
    "name; leave out a subpart the file does not answer. Every value carries at least one cite whose excerpt is "
    "copied word for word from the cited page. Never add numbers together or compute a total: state a total only "
    "where a page prints it. No line breaks, no markdown, no lettered labels and no {{ }} markers in any value."
)


def _user(inp: Inputs, group: WorkUnit, nums: tuple[str, ...], cat: Catalogue, pack: str, fix: str = "") -> str:
    defs = "\n".join(f"({k}) {t}" for k, t in cat.definitions)
    return "\n\n".join(
        x
        for x in (
            f"THE RESPONDING PARTY (the one person answering): {inp.responding_party}",
            "THE MATTER RECORD:\n" + "\n".join(inp.matter_record),
            "THE MEDICALS TAB (structured, from the matter record):\n" + json.dumps(inp.medicals, indent=1),
            f"DEFINITION BOX CHECKED ON THE SERVED FORM: {', '.join(inp.definition_boxes) or 'neither'}",
            "THE FORM'S DEFINITIONS:\n" + defs,
            inp.rules_block,
            f"SECTION {group.section} {group.title}: THE INTERROGATORIES TO ANSWER (official text):\n\n"
            + _numbers_text(nums, cat),
            fix,
            "THE SOURCE TEXT (each page under its FILE and page heading):\n\n" + pack,
        )
        if x
    )


def _call(doorway: Any, model: str, user: str, system: str = SYSTEM, stage: str = STAGE) -> Any:
    res = doorway.call(
        stage, model=model, system=system, messages=[{"role": "user", "content": user}], max_tokens=MAX_TOKENS
    )
    text = _FENCE.sub("", str(getattr(res, "text", "") or "")).strip()
    try:
        return json.loads(text)
    except ValueError:
        return {"unparsed": text[:200]}


def _parsed_with(
    doorway: Any, model: str, system: str, user: str, nums: tuple[str, ...], cat: Catalogue, stage: str = STAGE
) -> tuple[list[Answer], int]:
    """One call, and one retry told every problem when the reply is not in the shape."""
    raw = _call(doorway, model, user, system, stage)
    try:
        return parse_section(raw, nums, cat), 1
    except AnswerError as exc:
        fix = "YOUR LAST REPLY WAS NOT USED, for these problems; reply again in the shape: " + "; ".join(exc.problems)
        try:
            return parse_section(_call(doorway, model, user + "\n\n" + fix, system, stage), nums, cat), 2
        except AnswerError as again:
            from ..compose import DraftingError

            raise DraftingError(f"the {stage} reply was not in the shape after one retry: {again}") from None


def _parsed(doorway: Any, model: str, user: str, nums: tuple[str, ...], cat: Catalogue) -> tuple[list[Answer], int]:
    return _parsed_with(doorway, model, SYSTEM, user, nums, cat)


def cite_ok(c: Cite, corpus: Mapping[str, DocPages]) -> bool:
    name = verify.resolve(c.file, corpus)
    page = corpus[name].pages.get(c.page) if name else None
    return page is not None and verify.locate(c.excerpt, verify.clean_page(page, False)) is not None


def value_ok(v: Value, corpus: Mapping[str, DocPages]) -> bool:
    if not all(cite_ok(c, corpus) for c in v.cites):
        return False
    printed = {re.sub(r"[^\d.]", "", m) for c in v.cites for m in _MONEY.findall(c.excerpt)}
    return all(re.sub(r"[^\d.]", "", m) in printed for m in _MONEY.findall(v.text))


def _failing(a: Answer, corpus: Mapping[str, DocPages]) -> int:
    return sum(not value_ok(v, corpus) for it in a.items for v in it.parts.values())


def keep_verified(a: Answer, corpus: Mapping[str, DocPages]) -> Answer:
    """``a`` with every unverified value removed: a lettered subpart then renders
    the no-information sentence; an item with nothing left goes; a number with
    no item left is ``no_information``."""
    if a.kind != "answered":
        return a
    items = []
    for it in a.items:
        parts = {k: v for k, v in it.parts.items() if value_ok(v, corpus)}
        if parts:
            items.append(Item(it.name, parts))
    return dataclasses.replace(a, items=tuple(items)) if items else Answer(a.number, "no_information")


def _by_code(n: str, inp: Inputs, policy: ResponsePolicy) -> Answer | None:
    if n in policy.not_applicable:
        return Answer(n, "not_applicable")
    if n == ADMISSIONS:
        return Answer(n, "none")  # its sentence is the effective policy's (``effective_form``)
    return None


def effective_form(policy: ResponsePolicy, inp: Inputs) -> Any:
    """The firm's sentences with 17.1 set by code from the plan."""
    s = ADMISSIONS_MARKER if inp.admissions_served else policy.no_admissions_17_1
    per = {**{k: dict(v) for k, v in policy.form.per_number.items()}, ADMISSIONS: {"none": s}}
    return dataclasses.replace(policy.form, per_number=per)


def _section(
    group: WorkUnit,
    inp: Inputs,
    unit: Any,
    corpus: Mapping[str, DocPages],
    doorway: Any,
    model: str,
    cat: Catalogue,
) -> tuple[list[Answer], int, int]:
    pack = pack_for(unit, group, corpus)
    first, calls = _parsed(doorway, model, _user(inp, group, group.numbers, cat, pack), group.numbers, cat)
    by = {a.number: a for a in first}
    redo = tuple(n for n in group.numbers if _failing(by[n], corpus))
    if redo:
        fix = (
            "THESE NUMBERS ARE ASKED AGAIN: a cite's excerpt was not found word for word on the page it names, or a "
            "dollar figure is not printed in its excerpt. Answer only these, with excerpts copied from the page."
        )
        again, more = _parsed(doorway, model, _user(inp, group, redo, cat, pack, fix), redo, cat)
        calls += more
        by.update({a.number: a for a in again})
    failed = sum(_failing(by[n], corpus) for n in group.numbers)
    return [keep_verified(by[n], corpus) for n in group.numbers], calls, failed


def answer(
    inp: Inputs,
    policy: ResponsePolicy,
    corpus: Mapping[str, DocPages],
    doorway: Any,
    model: str,
    cat: Catalogue,
    unit: Any = None,
    log: Callable[[str], None] = lambda _m: None,
) -> Result:
    """Every checked number answered, in form order."""
    out: dict[str, Answer] = {}
    calls = failed = 0
    for group in work_units(inp.checked, cat):
        coded = {n: a for n in group.numbers if (a := _by_code(n, inp, policy)) is not None}
        out.update(coded)
        rest = tuple(n for n in group.numbers if n not in coded)
        if not rest:
            continue
        got, c, f = _section(dataclasses.replace(group, numbers=rest), inp, unit, corpus, doorway, model, cat)
        out.update({a.number: a for a in got})
        calls, failed = calls + c, failed + f
        log(f"  form answers: section {group.section}, {len(rest)} number(s), {c} call(s), {f} value(s) unverified")
    ordered = tuple(out[n] for n in sorted(out, key=cat.position))
    return Result(ordered, calls, failed, cites_of(ordered))


def cites_of(answers: tuple[Answer, ...]) -> tuple[tuple[str, str, str, int], ...]:
    """(number, subpart, file, page) of every verified value, in order, once each."""
    cites = (
        (a.number, k, c.file, c.page)
        for a in answers
        for it in a.items
        for k, v in it.parts.items()
        for c in dict.fromkeys(v.cites)
    )
    return tuple(dict.fromkeys(cites))
