"""A propounded set checked against the firm's rules, never written (design C:
"Propounded sets: check-only").

Code cannot write a request, so a set gets FINDINGS, closed codes the set
lane's repair acts on:

* ``count_short`` / ``count_over``: a ``count`` clause's figures are read by
  code from the rule's own quote ("generally 50-75", "up to 50", "at least
  35") and compared with the requests the set holds. A quote with no figure
  code can read is no finding and names itself in the notes.
* ``forbid_topic``: one model call per set names the requests that ask about a
  topic a ``forbid_topic`` clause rules out; code keeps a number only when the
  set holds it. Refused after one retry: ``rule_unchecked``.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..outcome import DraftingFailed
from ..revise import inplace, responses as rs
from ..verify.items import parse_json
from .reading import save
from .types import SET_OF, Ask, Clause, Finding, StageResult

STAGE = "rulebook-set"
MAX_TOKENS = 3_000
_RANGE = re.compile(r"(\d+)\s*(?:-|–|to|through)\s*(\d+)")
_MOST = re.compile(r"(?:up to|no more than|at most|maximum of|not (?:to )?exceed|fewer than)\s+(\d+)")
_LEAST = re.compile(r"(?:at least|minimum of|no fewer than|no less than)\s+(\d+)")

SYSTEM = (
    "You read a law firm's propounded discovery requests and the firm's rules that rule out topics. Return ONLY "
    'a JSON object {"clauses": [{"clause": <clause number>, "numbers": [<the number of each request that asks '
    "about the topic the clause rules out, as headed>]}]}, one entry per clause, an empty list when none does."
)


def requests(md: str) -> tuple[str, list[tuple[str, str]]]:
    """(the set type, [(request number, its words)]) of a propounded set: every
    request heading that is not a response heading, with the blocks after it."""
    blocks = inplace.blocks(md)
    kinds: set[str] = set()
    out: list[tuple[str, str]] = []
    for b in blocks:
        h = rs._head(b)
        if h is not None and not h[0] and h[1] in SET_OF:
            kinds.add(h[1])
            out.append((h[2], h[3]))
        elif h is None and out and not b.startswith("|"):
            n, words = out[-1]
            out[-1] = (n, f"{words} {rs.plain(b)}".strip())
    return (next(iter(kinds)) if len(kinds) == 1 else ""), out


def figures(quote: str) -> tuple[int | None, int | None] | None:
    """(fewest, most) a count clause's own words state, or None."""
    m = _RANGE.search(quote)
    if m:
        return int(m.group(1)), int(m.group(2))
    most, least = _MOST.search(quote), _LEAST.search(quote)
    if not (most or least):
        return None
    return (int(least.group(1)) if least else None), (int(most.group(1)) if most else None)


def _count(c: Clause, n: int) -> tuple[list[Finding], str]:
    got = figures(c.quote)
    src = f"rule:{c.rule}"
    if got is None:
        return [], f"Rule {c.rule}: its count names no figure code can read; the set was not checked against it."
    low, high = got
    if low is not None and n < low:
        return [Finding(src, "count_short", "", f"{n} requests; the rule asks at least {low}")], ""
    if high is not None and n > high:
        return [Finding(src, "count_over", "", f"{n} requests; the rule allows at most {high}")], ""
    return [], ""


def _user(clauses: list[Clause], reqs: list[tuple[str, str]], problems: list[str] | None) -> str:
    lines = ["THE RULE CLAUSES:"] + [f"[{i}] {c.quote}" for i, c in enumerate(clauses, 1)]
    lines += ["\nTHE REQUESTS:"] + [f"No. {n}: {w}" for n, w in reqs]
    if problems:
        lines.append("\nYOUR LAST ANSWER WAS REFUSED BY CODE FOR:\n" + "\n".join(f"- {p}" for p in problems))
    return "\n".join(lines) + "\n"


def _check(raw: Any, clauses: list[Clause], numbers: set[str]) -> tuple[dict[int, list[str]], list[str]]:
    rows = raw.get("clauses") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return {}, ['the answer is an object {"clauses": [...]}']
    out: dict[int, list[str]] = {}
    problems: list[str] = []
    for row in rows:
        k = row.get("clause") if isinstance(row, dict) else None
        got = row.get("numbers") if isinstance(row, dict) else None
        if not isinstance(k, int) or not 1 <= k <= len(clauses) or k in out or not isinstance(got, list):
            problems.append(f"entry {row!r:.60}: a clause number listed once, with a list of numbers")
            continue
        bad = [str(n) for n in got if str(n) not in numbers]
        if bad:
            problems.append(f"clause {k}: request {bad[0]} is not in the set")
            continue
        out[k] = [str(n) for n in got]
    problems += [f"clause {k}: missing" for k in range(1, len(clauses) + 1) if k not in out]
    return out, problems


def _topics(data: Path, clauses: list[Clause], reqs: list[tuple[str, str]], ask: Ask | None) -> dict[int, list[str]]:
    asked = _user(clauses, reqs, None)
    p = Path(data) / "rulebook" / f"set-{hashlib.sha256(asked.encode()).hexdigest()[:16]}.json"
    if p.is_file():
        return {int(k): v for k, v in json.loads(p.read_text(encoding="utf-8")).items()}
    if ask is None:
        raise DraftingFailed("rule_unchecked: the set's topic rules are unchecked and no model call is available")
    problems: list[str] | None = None
    for _ in range(2):
        got, problems = _check(parse_json(ask(STAGE, SYSTEM, _user(clauses, reqs, problems), MAX_TOKENS)),
                               clauses, {n for n, _ in reqs})  # fmt: skip
        if not problems:
            save(p, json.dumps(got))
            return got
    raise DraftingFailed(f"rule_unchecked: the set's topic rules were refused: {'; '.join(problems or [])}"[:480])


def run(data: Path, md: str, clauses: list[Clause], ask: Ask | None) -> StageResult:
    """The set's findings; its markdown is returned as given."""
    set_type, reqs = requests(md)
    kind = SET_OF.get(set_type, "")
    mine = [c for c in clauses if c.standing and kind in c.doc_types]
    findings: list[Finding] = []
    notes: list[str] = []
    for c in (c for c in mine if c.act == "count"):
        got, note = _count(c, len(reqs))
        findings += got
        notes += [note] if note else []
    topics = [c for c in mine if c.act == "forbid_topic"]
    if topics and reqs:
        for k, numbers in _topics(data, topics, reqs, ask).items():
            c = topics[k - 1]
            findings += [Finding(f"rule:{c.rule}", "forbid_topic", n, c.quote) for n in numbers]
    return StageResult(md=md, findings=tuple(findings), notes=tuple(notes))
