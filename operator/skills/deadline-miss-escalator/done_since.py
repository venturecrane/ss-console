"""The "Done since last time" lines (case-manager spec, Job 3), in code.

CANONICAL SOURCE: ``operator/skills/task-list-keeper/done_since.py``. The
deadline-miss-escalator and date-prep-brief carry byte-identical copies,
discovered and pinned by ``operator/tests/test_done_since_sync.py``. Edit here,
restamp the copies.

Work the Operator finished without asking anyone is told once, as one line in
the next message that person gets (the task review, the date-prep brief, or
the daily deadline digest), and never as a message of its own. Two kinds of
work qualify, both read off the casework ledger and nothing else:

* a task it closed on the record's evidence (``closed_by_record`` then
  ``completed``), rendered with the document that showed it done;
* a date-prep step it ran itself at "Handles it" (``step_ran``), rendered from
  the step's catalog id and parameters as the row recorded them.

EVERY PHRASE HERE IS AUTHORED CLIENT-FACING CONTENT under the no-fabrication
policy (CLAUDE.md): the words are this file's constants, and every value is one
the ledger row carries (a close day, a document kind and day, a provider name
and a record day from the step's parameters) or a matter number the routine
read this run. A step whose catalog id has no phrase here is not rendered at
all: an unknown step is never described by a guess. No em dashes.

The caller marks each rendered row ``mentioned`` through the broker after the
message that carries it is sent, so the line is told exactly once. Stdlib only;
the ledger module is passed in, never imported (each skill path-loads its own
vendored copy).
"""

from __future__ import annotations

import re
from datetime import date

#: The overlay's envelope bound for one line (``casework_rules._MAX_SHORT_LINE``).
LINE_MAX = 160
_PROVIDER_MAX = 60

#: Same words as the task review's close lines (``lines._EVIDENCE_PHRASES``;
#: ``test_task_list_keeper.py`` pins the two tables equal).
EVIDENCE_PHRASES = {
    "proof_of_service": "a proof of service",
    "verification": "a verification",
    "records": "a records file",
}

#: One closed phrase per catalog step (date-prep-brief ``catalog.STEP_SKILLS``),
#: in the words of ``references/decision-catalog.md`` "What each step does".
STEP_PHRASES = {
    "binder_assemble": "I assembled the trial binder index",
    "witness_list_finalize": "I staged a finalized witness list for review",
    "exhibit_list_finalize": "I staged a finalized exhibit list for review",
    "motion_calendar_refresh": "I refreshed the motion calendar",
    "discovery_status_refresh": "I refreshed the discovery status",
}
_CHASE = "I chased {provider} for the outstanding records"
_UPDATE = "I asked {provider} for records dated after {day}"
_UPDATE_UNDATED = "I asked {provider} for updated records"
_SOME_PROVIDER = "a provider"

_SPACES = re.compile(r"\s+")


def matter_head(number: str | None) -> str:
    """``matter <number>``, or the authored absence (the escalator's wording)."""
    return f"matter {number}" if number else "matter number unavailable"


def _iso(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def evidence_text(atoms) -> str | None:
    """The first document atom as words ("a proof of service dated D")."""
    for atom in atoms or ():
        parts = str(atom).split(":")
        if len(parts) == 3 and parts[0] == "document" and parts[1] in EVIDENCE_PHRASES:
            return f"{EVIDENCE_PHRASES[parts[1]]} dated {parts[2]}"
    return None


def evidence_day(atoms) -> date | None:
    """The day of the first document atom (it is rendered, so it is seeded)."""
    for atom in atoms or ():
        parts = str(atom).split(":")
        if len(parts) == 3 and parts[0] == "document":
            return _iso(parts[2])
    return None


def _fit(primary: str, fallback: str) -> str:
    return primary if len(primary) <= LINE_MAX else fallback[:LINE_MAX]


def task_line(head: str, closed_on: date | None, atoms) -> str:
    """A task closed on the record's evidence in an earlier run, told once."""
    when = f" on {closed_on.isoformat()}" if closed_on else ""
    evidence = evidence_text(atoms)
    why = f", {evidence} was on file" if evidence else ""
    return _fit(f"{head}: a task I closed{when}{why}", f"{head}: a task I closed{when}")


def _provider(params: dict) -> str:
    raw = params.get("provider")
    if not isinstance(raw, str):
        return _SOME_PROVIDER
    text = _SPACES.sub(" ", raw.replace("\u2014", " ").replace("\u2013", " ")).strip()
    return text if text and len(text) <= _PROVIDER_MAX else _SOME_PROVIDER


def _params(step) -> dict:
    raw = step.get("params") if isinstance(step, dict) else None
    return raw if isinstance(raw, dict) else {}


def step_phrase(step) -> str | None:
    """The closed phrase for one recorded step, or None for a step this file
    has no words for (never rendered, never guessed)."""
    if not isinstance(step, dict) or not isinstance(step.get("catalog_id"), str):
        return None
    base = step["catalog_id"].split(":", 1)[0]
    params = _params(step)
    if base in STEP_PHRASES:
        return STEP_PHRASES[base]
    if base != "records_refresh":
        return None
    provider = _provider(params)
    if params.get("mode") == "chase":
        return _CHASE.format(provider=provider)
    if params.get("mode") == "update":
        newest = _iso(params.get("newest_record"))
        if newest is None:
            return _UPDATE_UNDATED.format(provider=provider)
        return _UPDATE.format(provider=provider, day=newest.isoformat())
    return None


def step_days(step) -> list[date]:
    """Every day a step's line renders besides its run day (seeded with it)."""
    newest = _iso(_params(step).get("newest_record"))
    return [newest] if newest else []


def step_line(head: str, runs: list[dict]) -> str | None:
    """One line for the unmentioned steps on one date item: ``head: on D I ...``.
    None when no step has a phrase. The day shows only when every run shares it."""
    phrases = [p for p in (step_phrase(r.get("step")) for r in runs) if p]
    if not phrases:
        return None
    days = {r.get("day") for r in runs}
    when = next(iter(days)) if len(days) == 1 else None
    lead = f"{head}: on {when.isoformat()} " if isinstance(when, date) else f"{head}: "
    body = "; ".join(dict.fromkeys(phrases))
    count = len(runs)
    fallback = f"{head}: I ran {count} prep step{'s' if count != 1 else ''} before the date"
    return _fit(lead + body, fallback)


def _row(state, number, line: str, days: list) -> dict:
    row = {
        "item_key": state.item_key,
        "matter_id": state.matter_id,
        "kind": state.kind,
        "source_id": state.source_id,
        "line": line,
        "_number": number,
        "_days": [d for d in days if isinstance(d, date)],
    }
    if state.kind == "task":
        row["task_id"] = state.source_id
    return row


def rows(ledger, states: dict, numbers: dict, *, kinds=("task", "date")) -> list[dict]:
    """Every unmentioned done item, as rows ready for an envelope.

    ``numbers`` maps matter_id to the matter number this run read (None when
    the matter has none); a matter absent from it is not this run's to tell,
    and its rows wait for a message that reads it. Task closes first (oldest
    close first), then date steps (oldest run first), then by key, so the
    order is stable. A task row carries ``_evidence`` for the matter memo."""
    out: list[tuple] = []
    for state in states.values():
        if state.kind not in kinds or state.matter_id not in numbers or not ledger.needs_mention(state):
            continue
        head = matter_head(numbers[state.matter_id])
        if state.kind == "task":
            atoms = (state.record_payload or {}).get("evidence") or []
            line = task_line(head, state.last_completed_date, atoms)
            row = _row(state, numbers[state.matter_id], line, [state.last_completed_date, evidence_day(atoms)])
            row["_evidence"] = evidence_text(atoms)
            out.append((0, state.last_completed_date or date.min, state.item_key, row))
            continue
        runs = list(state.unmentioned_steps)
        line = step_line(head, runs)
        if line is None:
            continue
        days = [r.get("day") for r in runs] + [d for r in runs for d in step_days(r.get("step"))]
        first = min((r.get("day") for r in runs if isinstance(r.get("day"), date)), default=date.min)
        out.append((1, first, state.item_key, _row(state, numbers[state.matter_id], line, days)))
    return [entry[-1] for entry in sorted(out, key=lambda e: e[:3])]


def public(row: dict) -> dict:
    """The row as an envelope carries it: no underscore-prefixed working keys."""
    return {k: v for k, v in row.items() if not k.startswith("_")}


def seed_nodes(row: dict) -> list[dict]:
    """``(matter_number, day)`` pairs a rendered line needs in the provenance
    handoff, shaped like the digest items the escalator's handoff walker reads."""
    number = row.get("_number")
    if not number:
        return []
    return [{"matter_number": number, "authored_date": d.isoformat()} for d in row.get("_days") or []]
