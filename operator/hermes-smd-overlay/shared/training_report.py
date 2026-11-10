"""The weekly representative progress report to a guide seat's administrators (ss-console #3946).

RENDERED BY CODE, FROM THE PROGRESS STORE ONLY. The ``rep-training`` skill's
scheduled job runs this module from its ``pre_run.py``. It reads the enrolled
list (``shared.sender_enrollment``), and for each person their progress record
in the managed progress store (:data:`shared.training.PROGRESS_STORE`, through
``record_store.code_read``), computes readiness with
:func:`shared.training.readiness`, and renders one email to the seat's
administrators (``scope.admins``). No model composes any of it.

A NOTE OR A LEAD CANNOT ENTER IT. This module opens exactly one store, the
progress store, by its constant name, and that store holds ids and counts only
(:func:`shared.training.clean_progress` re-cleans every record it reads, so even
an edited file contributes nothing but counts). The only other inputs are the
enrolled address and the name SMD issued the person's access for. There is no
code path here that names another store, and the test pins that.

HOW IT IS SENT. As a pre-rendered envelope (``shared.prerendered_dispatch``):
the pre_run writes ``<persona home>/.smd/pre_run/rep-training.dispatch.json``
and wakes the turn; the trust plugin's ``pre_llm_call`` dispatches it through
the full send gate before the model is asked anything, ``templated=True``. The
model composes nothing. When nobody is enrolled, or no administrator is
authored, nothing is written and the turn is not woken (``wakeAgent: false``),
so no model call is made. Every run leaves one wake row with counts only.

The body carries no date, no clock time, no amount and no exclamation mark, so
nothing in it can be held by the reply checks; the test runs those checks on a
rendered report.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared import training

logger = logging.getLogger(__name__)

SKILL = training.SKILL
SUBJECT = "Representative progress and readiness"
ROUTING_LEG = "central"
INTRO = (
    "Progress and readiness for each person enrolled on the product guide, from what the "
    "guide records. Readiness counts four things the guide checks itself: every lesson "
    "complete, the quiz passed, one whole mock pitch answered, and no misses on the most "
    "recent tone check. Notes and leads are never part of this report."
)
SKELETON = "Hello,\n\nThis week's representative progress report could not be sent in full."
_BROKER_TIMEOUT_SECONDS = 10


def _person_lines(
    label: str, cur: training.Curriculum, progress: dict[str, Any] | None
) -> list[str]:
    if progress is None:
        return [label, "- No progress recorded yet."]
    ready, criteria = training.readiness(cur, progress)
    lines = [label, "- Readiness: " + ("ready" if ready else "not ready yet")]
    for c in criteria:
        lines.append(f"- {c.value[0].upper()}{c.value[1:]}" + (" (met)" if c.met else ""))
    return lines


def render(cur: training.Curriculum, people: list[tuple[str, dict[str, Any] | None]]) -> str:
    """The report body. ``people`` is ``[(label, progress or None)]`` in order."""
    ready = sum(1 for _, p in people if p is not None and training.readiness(cur, p)[0])
    n = len(people)
    lines = [
        "Hello,",
        "",
        INTRO,
        "",
        f"{n} {'person' if n == 1 else 'people'} enrolled, {ready} ready.",
    ]
    for label, progress in people:
        lines.append("")
        lines.extend(_person_lines(label, cur, progress))
    return "\n".join(lines)


def _label(address: str) -> str:
    from shared import sender_enrollment

    name = sender_enrollment.enrolled_name(address)
    return f"{name} ({address})" if name else address


def gather(
    cur: training.Curriculum, *, config: Any = None
) -> list[tuple[str, dict[str, Any] | None]]:
    """Every enrolled person, with their progress read from the progress store
    only. Sorted by address so the report is stable week to week."""
    from shared import sender_enrollment

    return [
        (_label(address), training.read_progress(address, cur, config=config))
        for address in sender_enrollment.enrolled_addresses()
    ]


#: The entry's machine list of the people its words name, as recipient
#: digests (``shared.sent_lines.recipient_digest``). The purge
#: (``workspace_broker/person_purge.py``, ``NAMED_FIELD``) removes an entry when
#: the removed person's digest is here, exact match only, and never searches
#: words. A test pins the two names equal.
NAMED_FIELD = "named_sha256"


def build_envelope(
    recipients: list[str], body: str, started_at: str, named: list[str] | None = None
) -> dict[str, Any]:
    from shared.prerendered_dispatch import canonical_body_sha256
    from shared.sent_lines import recipient_digest

    dispatch = {
        "recipients": list(recipients),
        "cc": [],
        "routing_leg": ROUTING_LEG,
        "subject": SUBJECT,
        "full_body": body,
        "skeleton_body": SKELETON,
        "body_sha256_full": canonical_body_sha256(body),
        "body_sha256_skeleton": canonical_body_sha256(SKELETON),
        "appends": [],
        NAMED_FIELD: sorted({recipient_digest(a) for a in named or []}),
    }
    return {
        "skill": SKILL,
        "render_mode": "templated",
        "started_at": started_at,
        "dispatches": [dispatch],
        "unroutable": [],
        "memo_matters": [],
        "in_turn": [{"name": "rep_progress_skeleton", "template": SKELETON, "slots": {}}],
        "in_turn_enforce": True,
    }


def write_envelope(payload: dict[str, Any], hermes_home: str | None = None) -> Path:
    """Atomic 0600 write beside the provenance handoff, under the persona home
    the scheduler runs this pre_run with."""
    from shared.prerendered_dispatch import envelope_path

    target = envelope_path(SKILL, hermes_home)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = target.with_name("." + target.name + ".tmp")
    tmp.unlink(missing_ok=True)
    handle = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.replace(tmp, target)
    return target


def _wake_row(
    verb: str, action_type: str, basis: str, extra: dict[str, Any], customer: str = ""
) -> bool:
    """One wake row through the broker's agent-uid verb, counts only. True on ack."""
    from shared import workspace_broker

    metadata = {"decision_basis": basis, "platform": "cron-pre-run", "customer": customer, **extra}
    row = {
        "action_type": action_type,
        "actor": "agent",
        "actor_role": "agent",
        "skill_name": SKILL,
        "metadata": json.dumps(metadata, sort_keys=True, separators=(",", ":")),
    }
    try:
        answer = workspace_broker.request(
            {"action": verb, "row": row}, timeout=_BROKER_TIMEOUT_SECONDS
        )
        return answer.get("ok") is True
    except Exception:  # noqa: BLE001 - a heartbeat failure is reported, never raised
        return False


def run(
    *,
    clock: Any = None,
    config: Any = None,
    training_dir: Path | None = None,
    practice_dir: Path | None = None,
) -> dict[str, Any]:
    """Decide, render and write the week's report. Returns the verdict line
    the pre_run prints (counts only). Never raises past :func:`main`."""
    from shared.customer_config import CustomerConfig

    cfg = config if config is not None else CustomerConfig.from_volume()
    if not training.authored(cfg):
        _wake_row(
            "suppressed_wake_append", "SUPPRESSED_WAKE", "training_not_authored", {}, cfg.slug
        )
        return {"wakeAgent": False, "status": "training_not_authored"}
    admins = list(cfg.admins)
    if not admins:
        _wake_row("suppressed_wake_append", "SUPPRESSED_WAKE", "no_administrator", {}, cfg.slug)
        return {"wakeAgent": False, "status": "no_administrator"}
    cur = training.load_curriculum(training_dir, practice_dir)
    people = gather(cur, config=cfg)
    if not people:
        _wake_row(
            "suppressed_wake_append", "SUPPRESSED_WAKE", "nobody_enrolled", {"people": 0}, cfg.slug
        )
        return {"wakeAgent": False, "status": "nobody_enrolled", "people": 0}
    body = render(cur, people)
    now = clock() if callable(clock) else datetime.now(timezone.utc)
    stamp = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    from shared import sender_enrollment

    envelope = build_envelope(admins, body, stamp, sender_enrollment.enrolled_addresses())
    write_envelope(envelope)
    ready = sum(1 for _, p in people if p is not None and training.readiness(cur, p)[0])
    counts = {"people": len(people), "ready": ready, "dispatch_count": 1}
    _wake_row("emitted_wake_append", "EMITTED_WAKE", "report_ready", counts, cfg.slug)
    return {"wakeAgent": True, "dispatch_expected": True, "status": "report_ready", **counts}


def main() -> int:
    """The pre_run entry point: print one verdict line, exit 0."""
    try:
        verdict = run()
    except Exception as exc:  # noqa: BLE001 - a crashed report is a recorded verdict, never a woken turn
        sys.stderr.write("[pre_run] rep-training report failed: " + type(exc).__name__ + "\n")
        _wake_row("suppressed_wake_append", "SUPPRESSED_WAKE", "report_crashed", {})
        verdict = {"wakeAgent": False, "status": "report_crashed"}
    print(json.dumps(verdict, sort_keys=True))
    return 0


__all__ = [
    "NAMED_FIELD",
    "INTRO",
    "SKELETON",
    "SUBJECT",
    "build_envelope",
    "gather",
    "main",
    "render",
    "run",
    "write_envelope",
]
