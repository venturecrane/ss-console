"""The model's side of a propounded set's revision (``propounded.py``): the
four prompts, each answer checked by code with one retry, and the oracle that
keeps every answer so a resume asks nothing twice.

The model names values; code decides what they change (Law 18): the plan's
ops in the email's own words (``propounded.check_plan``), a definition that
opens with its term, requests code labels and numbers, a ranking that names
each request once and leaves enough that no kept request cites.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping
from typing import Any, Callable

from .. import set_items
from ..verify.items import parse_json
from .set_units import named_set_types
from .propounded import FOLLOW_UP, Held, Op, Refused, Unit, check_numbers, check_plan, cut, definition_ok, scope
from .propounded_define import defined, defs_region
from .propounded_numbers import add_count, cut_drop

STAGE_PLAN, STAGE_DEFINE, STAGE_ADD, STAGE_CUT = "revise-plan", "revise-define", "revise-add", "revise-cut"
MAX_TOKENS = 4_000
#: A model's output is priced at about five times its input (``core.OUTPUT_WEIGHT``).
OUTPUT_WEIGHT = 5
Ask = Callable[[str, str, str, int], str]


PLAN_SYSTEM = """You read an attorney's email replying about discovery sets his firm already drafted for the other
side. You name what he asks, in his own words; code makes every change.

Return JSON only:
{"ops": [{"op": "define" | "add" | "cut", "set_types": [...], "party_quote": "", "term": "", "number_quote": "",
          "quote": ""}],
 "other": [{"quote": ""}]}

- "define": add a defined term. "term": the term in capitals, spelled correctly, keeping a plural "(S)" as the
  email writes it.
- "add": write more requests. "number_quote": the email's words with how many ("five more", "add 10"), or "".
- "cut": remove requests. "number_quote": the email's words with the number, INCLUDING "to" or "by"
  ("down to 120-125", "cut by 10"). Never a number the email does not say.
- "quote": the exact words of the email that ask for it, copied character for character.
- "set_types": the sets the ask is about, from special_interrogatories, requests_for_admission,
  requests_for_production, form_interrogatories; [] when the email does not say.
- "party_quote": the email's exact words naming the party the sets are served on, or "" when it names none.
- "other": every other ask in the email (a question, rewording or changing a request, anything that is not adding
  a definition, adding requests or removing requests), each with its exact words. Greetings and thanks are not asks.
"""


def plan_user(email: str, units: list[Unit], problems: list[str] | None = None) -> str:
    rows = [
        f"{k + 1}. {u.name}: {u.set_type.replace('_', ' ')}, responding party {u.party or 'not read'}, "
        f"{u.count} requests"
        for k, u in enumerate(units)
    ]
    out = f"THE EMAIL:\n{email.strip()}\n\nTHE FILED SETS:\n" + "\n".join(rows)
    if problems:
        out += "\n\nYOUR LAST ANSWER WAS REFUSED BY CODE:\n" + "\n".join(f"- {p}" for p in problems)
    return out


def ask_plan(email: str, units: list[Unit], ask: Ask) -> tuple[list[Op], list[str]]:
    """The ops and the other asks; ``Refused`` after the one retry. An op whose
    words are still not the email's after the retry is an ask code cannot
    verify: an ``other``, so the job holds rather than drop it."""
    problems: list[str] | None = None
    for attempt in range(2):
        raw = parse_json(ask(STAGE_PLAN, PLAN_SYSTEM, plan_user(email, units, problems), MAX_TOKENS))
        ops, other, problems = check_plan(raw, email)
        if not problems:
            return ops, other
        if isinstance(raw, dict) and attempt == 1:
            return ops, [*other, *problems]
    raise Refused("revise_unchecked", "the email could not be read into ops code accepts: " + "; ".join(problems or []))


def settle_plan(email: str, sets: list[Unit], ask: Ask) -> tuple[list[Op], list[list[int]]]:
    """The email's ops and the sets each changes, settled before any write
    call. ``Held(revise_set_unsupported)`` when the email asks anything that is
    none of the three, or nothing; the scope and number holds of
    ``propounded.scope`` and ``check_numbers``."""
    ops, other = ask_plan(email, sets, ask)
    if other or not ops:
        said = "; ".join(f'"{q}"' for q in other) or "no change code makes"
        raise Held(
            "revise_set_unsupported",
            f"the email asks what is not adding a definition, adding requests or removing them ({said})",
        )
    scoped: list[list[int]] = []
    for op in ops:
        scoped.append(_inherited(op, ops[: len(scoped)], scoped) or scope(op, sets, email))
    check_numbers(ops, scoped, sets)
    return ops, scoped


def _inherited(op: Op, before: list[Op], scoped: list[list[int]]) -> list[int] | None:
    """A cut or an add whose words name no set and no party (a second email's
    bare "120-125") takes the sets of the last ask of its kind before it that
    named its sets: the asks are read per job, not per email."""
    if op.op == "define" or named_set_types(op.quote) or op.party_quote:
        return None
    for prior, ks in zip(reversed(before), reversed(scoped)):
        if prior.op == op.op and (named_set_types(prior.quote) or prior.party_quote):
            return list(ks)
    return None


DEFINE_SYSTEM = """You write one definition for a set of discovery requests the attorney's firm is serving.
Return JSON only: {"definition": "\\"TERM\\" means ..."} - one paragraph, opening with the term in capitals in
double quotes, in the plain style of a California discovery definition. Nothing else."""
ADD_SYSTEM = """You write new requests for a set of discovery the attorney's firm is serving, in the set's own style,
using its defined terms in capitals. Each request is one paragraph, with no label and no number, and repeats no
request the set already has. Return JSON only: {"items": ["...", ...]} with at most the number asked."""
FOLLOW_SYSTEM = """You write follow-up requests for a set of discovery the attorney's firm is serving, in the set's own
style, using its defined terms in capitals. A follow-up asks what the request it follows leaves open (the facts,
the witnesses, the documents behind an answer). Choose the requests that need one; give each follow-up the number
of the request it follows. Each is one paragraph, with no label and no number. Return JSON only:
{"items": [{"after": <request number>, "text": "..."}, ...]}."""
CUT_SYSTEM = """You rank the requests of a discovery set the attorney's firm is serving, from least important to the
case to most important, so the least important can be removed. Return JSON only: {"ranking": [numbers...]} with
EVERY request number of the set, least important first."""


def _retry(problems: list[str] | None) -> str:
    return "\n\nYOUR LAST ANSWER WAS REFUSED BY CODE:\n" + "\n".join(f"- {p}" for p in problems) if problems else ""


# ---- what each call sends: only what it needs (2026-11-26) -------------------------------
# No call sends a whole filed set: the plan reads the sets' names, types and
# counts; a definition reads the email alone; an add or a follow-up reads the
# set's Definitions as filed and its requests; a ranking reads the requests alone. The
# caption, instructions and signature are never sent. ``edit_chars`` prices
# these same texts, so the estimate is what the calls send.


def requests_text(md: str, variant: str) -> str:
    """The set's requests, each its label and words on one line."""
    return "THE REQUESTS:\n" + "\n".join(" ".join(it.text(md).split()) for it in set_items.read(md, variant))


def _terms_and_requests(md: str, variant: str) -> str:
    """The set's Definitions as filed, verbatim (what YOU or a defined property
    means, read whether or not each paragraph parses), then its requests. A set
    whose Definitions code cannot find sends the term names it can read."""
    region = defs_region(md)
    if region and md[region[0] : region[1]].strip():
        head = f"THE SET'S DEFINITIONS, AS FILED:\n{md[region[0] : region[1]].strip()}\n\n"
    else:
        terms = defined(md)
        head = f"THE SET'S DEFINED TERMS: {', '.join(terms)}\n\n" if terms else ""
    return head + requests_text(md, variant)


def define_user(term: str, quote: str, email: str) -> str:
    return f'THE EMAIL:\n{email.strip()}\n\nTHE ASK: "{quote}"\nTHE TERM: "{term.upper()}"'


def add_user(md: str, variant: str, n: int, quote: str) -> str:
    plural = set_items.NAMES[variant][1]
    return f'THE ASK: "{quote}"\nWRITE AT MOST: {n} new {plural}\n\n{_terms_and_requests(md, variant)}'


def follow_user(md: str, variant: str, n: int | None, quote: str) -> str:
    most = f"\nWRITE AT MOST: {n}" if n else ""
    return f'THE ASK: "{quote}"{most}\n\n{_terms_and_requests(md, variant)}'


def rank_user(
    md: str, variant: str, n: int, protected: set[int], families: Mapping[int, list[int]] | None = None
) -> str:
    keep = f"\nNEVER FIRST (cited by other requests): {sorted(protected)}" if protected else ""
    if families:
        keep += "\nA FOLLOW-UP GOES WITH THE REQUEST IT FOLLOWS; rank only these: " + str(
            sorted(it.number for it in set_items.read(md, variant) if it.number not in _children(families))
        )
    return f"REMOVE: {n} of the set's {set_items.NAMES[variant][1]}{keep}\n\n{requests_text(md, variant)}"


def ask_definition(term: str, quote: str, email: str, ask: Ask) -> str:
    problems: list[str] | None = None
    for _ in range(2):
        user = define_user(term, quote, email) + _retry(problems)
        raw = parse_json(ask(STAGE_DEFINE, DEFINE_SYSTEM, user, MAX_TOKENS))
        got = definition_ok(term, raw.get("definition", "") if isinstance(raw, dict) else "")
        if got:
            return got
        problems = [f'the definition must be one paragraph opening with "{term.upper()}" in double quotes']
    raise Refused("revise_unchecked", f'the definition of "{term.upper()}" was refused by code after its retry')


def ask_additions(md: str, variant: str, n: int, quote: str, ask: Ask) -> list[str]:
    problems: list[str] | None = None
    for _ in range(2):
        user = add_user(md, variant, n, quote) + _retry(problems)
        raw = parse_json(ask(STAGE_ADD, ADD_SYSTEM, user, MAX_TOKENS))
        items = raw.get("items") if isinstance(raw, dict) else None
        if isinstance(items, list) and items and all(isinstance(t, str) and t.strip() for t in items):
            return [str(t) for t in items][:n]
        problems = ['the answer is {"items": [one request per string, at least one]}']
    raise Refused("revise_unchecked", f"the new {set_items.NAMES[variant][1]} were refused by code after the retry")


FOLLOW_TOKENS = 16_000


def _children(families: Mapping[int, list[int]]) -> set[int]:
    return {c for cs in families.values() for c in cs}


def check_follow_ups(raw: Any, md: str, variant: str, n: int | None) -> tuple[list[tuple[int, str]], list[str]]:
    """Follow-ups each keyed to a request the set has; at most ``n`` (or one
    per request when the email names no number)."""
    nums = {it.number for it in set_items.read(md, variant)}
    got = raw.get("items") if isinstance(raw, dict) else None
    rows = [x for x in got if isinstance(x, dict)] if isinstance(got, list) else []
    pairs = [
        (x["after"], " ".join(x["text"].split()))
        for x in rows
        if isinstance(x.get("after"), int)
        and x["after"] in nums
        and isinstance(x.get("text"), str)
        and x["text"].strip()
    ]
    if not pairs or len(pairs) != len(got or []):
        return [], ['the answer is {"items": [{"after": a request number of the set, "text": "..."}]}, at least one']
    return pairs[: len(nums) if n is None else n], []


def ask_follow_ups(md: str, variant: str, n: int | None, quote: str, ask: Ask) -> list[tuple[int, str]]:
    problems: list[str] | None = None
    for _ in range(2):
        user = follow_user(md, variant, n, quote) + _retry(problems)
        raw = parse_json(ask(STAGE_ADD, FOLLOW_SYSTEM, user, FOLLOW_TOKENS))
        got, problems = check_follow_ups(raw, md, variant, n)
        if not problems:
            return got
    raise Refused(
        "revise_unchecked", f"the follow-up {set_items.NAMES[variant][1]} were refused by code after the retry"
    )


def check_ranking(
    raw: Any, md: str, variant: str, n: int, protected: set[int], families: Mapping[int, list[int]] | None = None
) -> tuple[list[int], list[str]]:
    nums = {it.number for it in set_items.read(md, variant)}
    got = raw.get("ranking") if isinstance(raw, dict) else None
    if not isinstance(got, list) or not all(isinstance(x, int) and not isinstance(x, bool) for x in got):
        return [], ['the answer is {"ranking": [request numbers]}']
    if len(set(got)) != len(got) or not set(got) <= nums:
        return [], [f"the ranking names each of the set's numbers ({min(nums)} to {max(nums)}) once"]
    _md, dropped = cut(md, variant, got, n, protected, families)
    if len(dropped) < n:
        return [], [f"the ranking leaves fewer than {n} requests that can be removed (no kept request may cite one)"]
    return got, []


def ask_ranking(
    md: str, variant: str, n: int, protected: set[int], ask: Ask, families: Mapping[int, list[int]] | None = None
) -> list[int]:
    problems: list[str] | None = None
    for _ in range(2):
        user = rank_user(md, variant, n, protected, families) + _retry(problems)
        got, problems = check_ranking(
            parse_json(ask(STAGE_CUT, CUT_SYSTEM, user, MAX_TOKENS)), md, variant, n, protected, families
        )
        if not problems:
            return got
    raise Refused("revise_unchecked", f"the ranking to remove {n} requests was too short after its retry")


@dataclass
class ModelOracle:
    """The model through ``ask``, every answer kept in ``answers`` (one
    definition per term for the whole job; one set of requests and one ranking
    per op on each file, keyed by the caller's ``unit#op`` key, so two asks of
    one set never share an answer) and handed to ``saved`` so a resume asks
    nothing twice."""

    ask: Ask
    email: str
    answers: dict[str, Any] = field(default_factory=dict)
    saved: Callable[[dict[str, Any]], None] = lambda _a: None

    def _keep(self, key: str, value: Any) -> Any:
        self.answers[key] = value
        self.saved(self.answers)
        return value

    def definition(self, term: str, quote: str) -> str:
        key = f"definition:{term.upper()}"
        if key in self.answers:
            return str(self.answers[key])
        return self._keep(key, ask_definition(term, quote, self.email, self.ask))

    def additions(self, unit: str, md: str, variant: str, n: int, quote: str) -> list[str]:
        key = f"additions:{unit}"
        if key in self.answers:
            return list(self.answers[key])
        return self._keep(key, ask_additions(md, variant, n, quote, self.ask))

    def follow_ups(self, unit: str, md: str, variant: str, n: int | None, quote: str) -> list[tuple[int, str]]:
        key = f"follow-ups:{unit}"
        if key in self.answers:
            return [(int(a), str(t)) for a, t in self.answers[key]]
        return self._keep(key, ask_follow_ups(md, variant, n, quote, self.ask))

    def ranking(
        self, unit: str, md: str, variant: str, n: int, protected: set[int], families: Mapping[int, list[int]]
    ) -> list[int]:
        key = f"ranking:{unit}"
        if key in self.answers:
            return [int(x) for x in self.answers[key]]
        return self._keep(key, ask_ranking(md, variant, n, protected, self.ask, families))


#: The output each call is allowed for, in characters: a plan, a definition,
#: one new request, a ranking's number.
_PLAN_OUT, _DEFINE_OUT, _PER_REQUEST_OUT, _PER_RANK_OUT = 800, 600, 400, 8
#: A call returns no more than its max tokens allow: about four characters each.
_CHARS_PER_TOKEN = 4


def plan_chars(email: str, units: list[Unit]) -> int:
    """The plan call, priced before it is made: the email and each set's name,
    type and count (no set text), with its retry. The edits are priced after
    it, before the first write call (``edit_chars``)."""
    return 2 * (len(PLAN_SYSTEM) + len(plan_user(email, units)) + OUTPUT_WEIGHT * _PLAN_OUT)


def edit_chars(email: str, mds: list[str], units: list[Unit], ops: list[Op], scoped: list[list[int]]) -> int:
    """The write calls the settled plan makes, priced before the first, each with
    its retry, on exactly the text each sends: one definition per term (no set
    text); per set in scope, an add or follow-up on its terms and requests and a
    ranking on its requests, only where a cut removes some. A set out of scope
    costs nothing; a request an earlier add writes is priced into the ranking."""
    terms = {o.term.upper(): o for o in ops if o.op == "define"}
    total = sum(len(DEFINE_SYSTEM) + len(define_user(t, o.quote, email)) + OUTPUT_WEIGHT * _DEFINE_OUT
                for t, o in terms.items())  # fmt: skip
    for k, (md, unit) in enumerate(zip(mds, units)):
        total += _set_chars(md, unit.set_type, [o for o, ks in zip(ops, scoped) if k in ks and o.op != "define"])
    return 2 * total


def _set_chars(md: str, v: str, ops: list[Op]) -> int:
    """One set's adds, follow-ups and rankings, in the order the revision makes them."""
    total, grown, have = 0, 0, set_items.count(md, v)
    adds = [o for o in ops if o.op == "add" and not FOLLOW_UP.search(o.quote)]
    follows = [o for o in ops if o.op == "add" and FOLLOW_UP.search(o.quote)]
    for o in adds:
        n = add_count(o.number_quote, have) or 0
        if n:
            out = min(_PER_REQUEST_OUT * n, _CHARS_PER_TOKEN * MAX_TOKENS)
            total += len(ADD_SYSTEM) + len(add_user(md, v, n, o.quote)) + grown + OUTPUT_WEIGHT * out
            grown, have = grown + out, have + n
    for o in follows:
        n = add_count(o.number_quote, have) if o.number_quote else None
        out = min(_PER_REQUEST_OUT * (n or have), _CHARS_PER_TOKEN * FOLLOW_TOKENS)
        total += len(FOLLOW_SYSTEM) + len(follow_user(md, v, n, o.quote)) + grown + OUTPUT_WEIGHT * out
        grown, have = grown + out, have + (n or have)
    for o in (o for o in ops if o.op == "cut"):
        drop = cut_drop(o.number_quote, have)
        if drop:
            total += len(CUT_SYSTEM) + len(rank_user(md, v, drop, set())) + grown + OUTPUT_WEIGHT * _PER_RANK_OUT * have
    return total


__all__ = [
    "Ask",
    "ModelOracle",
    "ask_additions",
    "ask_definition",
    "ask_follow_ups",
    "check_follow_ups",
    "ask_plan",
    "ask_ranking",
    "check_ranking",
    "add_user",
    "define_user",
    "edit_chars",
    "follow_user",
    "plan_chars",
    "rank_user",
    "requests_text",
    "settle_plan",
]
