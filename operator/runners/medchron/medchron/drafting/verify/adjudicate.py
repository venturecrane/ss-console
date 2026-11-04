"""Two-of-two: a ``wrong`` counts only when an independent second read agrees.

The hand method of 2026-11-04 settled disagreements weakly: the next round
did, and 11 statements were graded confirmed in one round and wrong in the
next. Here every ``wrong`` goes to a second call that sees ONLY the claim (the
sentence as it prints) and the source it cites, never the first reader's note,
grade or fix. Both say wrong: it counts (``Item.counted``), at ``serious`` only
when both said serious. The second says otherwise, or its answer does not
parse: the item becomes ``could_not_verify`` with the disagreement in its note.
A single checker's false positive therefore never reaches the repair or the
brief's blocking count, and is still disclosed.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from . import prompts
from .items import Ask, Cache, Item, parse_json
from .sources import sha


def second_user(claim: str, source: str) -> str:
    return f"THE CLAIM:\n{claim}\n\nTHE SOURCE IT CITES:\n{source or '(no source text)'}\n"


def second_read(claim: str, source: str, ask: Ask, cache: Cache | None = None) -> dict[str, object] | None:
    """One independent read: ``{"wrong": bool, "severity", "why", "fix"}``,
    or None when the answer does not parse."""
    key = sha("\x1f".join(("second", prompts.SECOND_SYSTEM, claim, source)))
    if cache is not None:
        hit = cache.get(key)
        if hit is not None:
            return hit
    got = parse_json(ask(prompts.SECOND_SYSTEM, second_user(claim, source)))
    if got is None or not isinstance(got.get("wrong"), bool):
        return None
    body: dict[str, object] = {
        "wrong": bool(got["wrong"]),
        "severity": str(got.get("severity") or "").lower(),
        "why": " ".join(str(got.get("why") or "").split())[:400],
        "fix": " ".join(str(got.get("fix") or "").split())[:400],
    }
    if cache is not None:
        cache.put(key, body)
    return body


def settle(item: Item, second: dict[str, object] | None) -> Item:
    item.second = dict(second) if second is not None else None
    if second is None:
        item.counted = False
        item.grade = "could_not_verify"
        item.note = f"one reader found it wrong ({item.note}); the second read did not answer"
        return item
    if second["wrong"]:
        item.counted = True
        both = item.severity == "serious" and second.get("severity") == "serious"
        item.severity = "serious" if both else "minor"
        if not item.fix and second.get("fix"):
            item.fix = str(second["fix"])
        return item
    item.counted = False
    item.grade = "could_not_verify"
    item.severity = ""
    item.note = f"one reader found it wrong ({item.note}); an independent second read did not"
    item.fix = ""
    return item


def adjudicate(
    items: list[Item],
    source_for: Callable[[Item], str],
    ask: Ask,
    cache: Cache | None = None,
    parallelism: int = 1,
    log: Callable[[str], None] | None = None,
) -> int:
    """Settle every ``wrong`` in ``items`` in place; the number of calls made."""
    wrong = [i for i in items if i.grade == "wrong" and not i.counted]
    if not wrong:
        return 0

    def one(i: Item) -> None:
        settle(i, second_read(i.preview, source_for(i), ask, cache))

    with ThreadPoolExecutor(max_workers=max(1, parallelism)) as pool:
        list(pool.map(one, wrong))
    if log:
        kept = sum(1 for i in wrong if i.counted)
        log(
            f"CHECK: {len(wrong)} wrong sent to a second read; {kept} confirmed two-of-two, {len(wrong) - kept} dropped"
        )
    return len(wrong)
