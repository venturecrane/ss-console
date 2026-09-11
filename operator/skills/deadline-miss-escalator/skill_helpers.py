"""Shared pre_run helpers for the substantive PI-pack skills.

CANONICAL SOURCE is this file, ``operator/templates/skill_helpers.py``. Each
skill that uses it carries a byte-identical vendored copy at
``operator/skills/<skill>/skill_helpers.py``, loaded at run time through the
skill's ``_load_sibling_module`` (which looks beside ``pre_run.py``, then under
``/opt/data/skills/<skill>`` and ``/app/skills/<skill>``, the two places the
seat image and the volume seed put a skill's files). The copies exist because
the scheduler stages a skill directory on its own; the gate that keeps them
identical is ``operator/tests/test_skill_helpers_sync.py``, which globs every
``skills/*/skill_helpers.py`` against this file and also refuses any
``pre_run.py`` that still carries a private copy of a function exported here.

Before 2026-09-11 these fifteen bodies were copied byte-for-byte across six
``pre_run.py`` files (176 redundant lines; code review 2026-09-10,
Architecture 4). Anything a helper needs from the skill (its name, its probe
marks, its handoff identity, its writer class, its serialisation cap) is a
parameter here; the skill keeps a one-line wrapper under the old private name
so its call sites and tests do not move.

Edit this file, then re-stamp every vendored copy byte-for-byte.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Sequence


def emit_suppress() -> int:
    """The scheduler's suppress verdict: one JSON line, exit 0."""
    print(json.dumps({"wakeAgent": False}))
    return 0


def extract_items(payload: Any) -> list | None:
    """The list a connector answered with, whichever of the usual keys it used."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "value", "results", "tasks", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return None


def find_skill_settings(data: Any, skill_name: str) -> dict:
    """The authored ``settings`` block for ``skill_name`` in a parsed customer.yaml, or ``{}``."""
    if not isinstance(data, dict):
        return {}
    personas = data.get("personas")
    if not isinstance(personas, list):
        return {}
    for persona in personas:
        if not isinstance(persona, dict):
            continue
        skills = persona.get("skills")
        if not isinstance(skills, list):
            continue
        for entry in skills:
            if not isinstance(entry, dict) or entry.get("name") != skill_name:
                continue
            settings = entry.get("settings")
            return settings if isinstance(settings, dict) else {}
    return {}


def parse_iso_date(value: Any) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def first_date(item: dict, keys: Sequence[str]) -> date | None:
    for key in keys:
        parsed = parse_iso_date(item.get(key))
        if parsed is not None:
            return parsed
    return None


def handoff_values(node: Any, key: str, out: list) -> list:
    """Every ``key`` string in a nested payload, deduped, first-seen order."""
    if isinstance(node, dict):
        value = node.get(key)
        if isinstance(value, str) and value and value not in out:
            out.append(value)
        for child in node.values():
            handoff_values(child, key, out)
    elif isinstance(node, list):
        for child in node:
            handoff_values(child, key, out)
    return out


def is_iso_day(value: str) -> bool:
    """YYYY-MM-DD and nothing else. The register must never learn a non-date."""
    return len(value) == 10 and value[4] == "-" and value[7] == "-" and value.replace("-", "").isdigit()


def is_probe_subject(subject: str, probe_mark: str, provenance_mark: str) -> bool:
    """A subject the Operator itself planted (a probe), possibly behind its provenance mark."""
    text = subject.lstrip()
    if text.upper().startswith(provenance_mark.upper()):
        text = text[len(provenance_mark) :].lstrip()
    return text.upper().startswith(probe_mark.upper())


def next_scheduled_at(now: datetime, schedule_hours: int = 24) -> str:
    return (now + timedelta(hours=schedule_hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def plan_counts_total(decision: Any) -> dict:
    """``plans_total`` only: the skill hands every plan over, so nothing is truncated."""
    if not decision.plans:
        return {}
    return {"plans_total": len(decision.plans)}


def plan_counts_capped(decision: Any, max_serialized: int) -> dict:
    """The cap's own accounting, computed the one way the wake line computes it.

    Duplicating the slice in the audit path would let the row and the wake line
    disagree about how much was handed over, a discrepancy nobody would look
    for, in the one record kept to catch discrepancies.
    """
    if not decision.plans:
        return {}
    emitted = len(decision.plans[:max_serialized])
    return {
        "plans_total": len(decision.plans),
        "plans_emitted": emitted,
        "plans_truncated": emitted < len(decision.plans),
    }


def pos_int(value: Any, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int) and value > 0:
        return value
    return fallback


def pos_int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def source_id_of(item: dict, keys: Sequence[str]) -> str | None:
    for key in keys:
        value = item.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value)
    return None


def write_pre_run_handoff(payload: dict, skill: str, started_at: str) -> None:
    """Project the emitted payload down to dates + matter ids and hand it off."""
    try:
        record = {
            "skill": skill,
            "started_at": started_at,
            "dates": [d for d in handoff_values(payload, "authored_date", []) if is_iso_day(d)],
            "matter_ids": handoff_values(payload, "matter_id", []),
        }
        directory = Path(os.environ.get("HERMES_HOME") or "/opt/data") / ".smd" / "pre_run"
        # Modes are set AT CREATION, never by a follow-up chmod: umask can only
        # remove bits, so the result is at most 0700/0600 and there is no window
        # in which the file is readable by anyone else. It names the matters the
        # firm is working on. An already-existing directory keeps whatever mode
        # it has; the file's own 0600 is the load-bearing half.
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = directory / ("." + skill + ".json.tmp")
        # O_EXCL so the open cannot follow a pre-planted symlink, preceded by an
        # unlink so a temp file left by a crashed run cannot wedge the writer
        # for good. missing_ok: there is usually nothing to remove.
        tmp.unlink(missing_ok=True)
        handle = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        os.replace(tmp, directory / (skill + ".json"))
    except Exception as exc:  # noqa: BLE001 - never change stdout or the wake; the handoff is best-effort
        sys.stderr.write("[pre_run] handoff write failed (" + str(exc) + ")\n")


def writer_factory(writer_cls: Callable[[str, str], Any]) -> Any | None:
    """The broker-backed audit writer, or None when no broker socket is staged."""
    socket_path = os.environ.get("SMD_AUDIT_BROKER_SOCKET") or os.environ.get("SMD_WORKSPACE_BROKER_SOCKET")
    if not socket_path:
        return None
    return writer_cls(socket_path, os.environ.get("CUSTOMER_SLUG", ""))


def warn_observability_failure(exc: BaseException, what: str) -> None:
    """Observability never gates the wake, but a failed write must not be silent.

    Before this line existed the audit-row write was swallowed with ``pass`` in
    every skill, so a writer failing on a live seat looked identical to a wake
    that never ran, on the very rows built to make wake behaviour auditable.
    stderr is the scheduler's log channel; stdout stays the verdict.
    """
    sys.stderr.write("[pre_run] " + what + " write failed (" + type(exc).__name__ + ": " + str(exc) + ")\n")


def hold_active(hold_state: Any) -> bool:
    """True iff the item's hold sentinel blocks the chase.

    A hold is open once it has any raise and is not ``resolved``. An ``acked``
    hold stays BLOCKING: ack means "a person saw the surface", not "the
    condition is fixed"; it only snoozes the re-surface (``should_fire``
    handles that). ``handed_off`` likewise blocks and additionally ends
    autonomous re-surfacing: a person owns the item. Only ``resolved``,
    written by the turn that confirmed the condition is fixed, releases the
    chase (ss #2402).
    """
    if hold_state is None or hold_state.attempts == 0:
        return False
    return not hold_state.resolved


def matter_id_of(item: dict, keys: Sequence[str]) -> str:
    """The matter an item belongs to, or ``"unknown-matter"``.

    The live Smokeball /tasks payload carries the matter as a NESTED link
    object (``{"matter": {"id": ..., "href": ...}}``), not a flat matterId;
    found by the WP-D probe when the flat-key miss put "unknown-matter" into
    every item identity and forked the ledger join (ss #1915). The flat
    ``keys`` stay as fallbacks, in the skill's own order.
    """
    matter = item.get("matter") or item.get("Matter")
    if isinstance(matter, dict):
        nested = matter.get("id") or matter.get("Id")
        if isinstance(nested, str) and nested:
            return nested
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return "unknown-matter"


def matter_number_of(item: dict, matter_id_keys: Sequence[str]) -> tuple[str | None, str | None]:
    """``(matter_number, absent_reason)``: exactly one is non-None.

    The number is the connector's code-projected ``matterNumber`` (ss #2390),
    never derived here. The absence reasons are the connector's typed ones
    (``matterNumberAbsent``); an item with neither annotation came from a pull
    where the enrichment step itself never ran or crashed wholesale, which for
    the degraded-run judgment IS a resolution failure, with one carve: an item
    that names no matter at all can only ever be "no_matter_link".
    """
    number = item.get("matterNumber")
    if isinstance(number, str) and number:
        return number, None
    absent = item.get("matterNumberAbsent")
    if isinstance(absent, str) and absent:
        return None, absent
    if matter_id_of(item, matter_id_keys) == "unknown-matter":
        return None, "no_matter_link"
    return None, "lookup_failed"


async def try_write_emitted_wake(
    audit_writer_factory: Callable[[], Any],
    decision: Any,
    *,
    skill_name: str,
    next_scheduled_at: str,
    plan_counts: Callable[[Any], dict],
) -> None:
    """Best-effort EMITTED_WAKE row for a real-decision wake (#2253).

    The suppress path logged its reasoning and the wake path logged nothing, so
    the ledger held a record of every tick the gate stayed quiet and no record
    of the ticks it fired. On 2026-08-10 the escalator woke with its connector
    down and sent an alert stating a date it could not read; the only way
    anyone found it was reading the mailbox.

    BEST-EFFORT IS THE CONTRACT, and it inverts the suppress path's on purpose.
    There, an audit failure escalates to a wake, because a silent suppress is
    indistinguishable from a broken gate. Here the wake is already the decision,
    so every failure (no writer wired, socket down, broker refusal, a writer
    object too old to have the method) is caught, reported to stderr, and the
    wake proceeds. A wake that a failed audit write could suppress or delay
    would be a gate made of observability.

    It is not free, and the cost is stated rather than assumed away: whatever
    writer is wired blocks the wake for its own timeout (the broker-socket
    writer caps at ``_HEARTBEAT_TIMEOUT_SECONDS``). Bounded by that writer's
    ceiling, and never a change of decision.

    Not called on the fail-open paths: ``no_audit_writer_fail_open`` fires
    because there is no writer to call, and ``suppress_heartbeat_failed_fail_open``
    fires because a write to that writer just failed. ``plan_counts`` is the
    skill's own accounting (total, or capped the way its wake line caps), so
    the row and the wake line cannot disagree about how much was handed over.
    """
    try:
        writer = audit_writer_factory()
        if writer is None:
            return
        await writer.write_emitted_wake(
            skill_name=skill_name,
            pre_run_inputs=decision.pre_run_inputs_digest,
            decision_basis=decision.decision_basis,
            next_scheduled_at=next_scheduled_at,
            extra_metadata={**decision.extra_metadata, **plan_counts(decision)},
        )
    except Exception as exc:  # noqa: BLE001 - observability never gates the wake; the failure is written to stderr and the wake proceeds
        warn_observability_failure(exc, "emitted-wake row")
