#!/usr/bin/env python3
"""medical-records-chaser pre-run gate — the ledger graduation (ss #2404).

Runs BEFORE the Hermes cron daemon wakes the agent. Decides whether any open
records-request roster item actually needs a chase today, and hands the woken
turn the STATE the email must copy — so the chase's history is broker-validated
ledger fact, never model recall.

Why this skill graduated off the shared empty-seat gate
-------------------------------------------------------
The founding defect (ss #2404): the 2026-08-18 Valley Imaging chase email
asserted "the last chase staged for this matter was July 13" and "no chase in
the last five weeks" — contradicting the seat's own 2026-08-11 chase email for
the same matter. Chase count, cadence position, and last-chase date were
re-derived by the model from matter memos each run and asserted from recall,
threaded by a model-improvised ``[op-mrc:...]`` email tag whose schema drifted
week to week (it appears nowhere in this repo — the model invented it each
run). The Sutter chain happened to remember correctly the same day; recall is
per-run luck, and this gate replaces it. The template is the sibling
``client-verification-tracker/pre_run.py`` (WP-B, #1889), including its #2402
matter-level hold.

Wake / suppress decision
------------------------
For each open roster item (a firm-authored records-request tracking task):

  (a) CHASE DUE — cadence authored, the last ``chased`` raise is at least
      ``chase_cadence_days`` old (or there is no prior chase and the tracking
      task's confirm-by date has arrived). The plan carries ``attempt`` (the
      chase number this chase would be), ``last_chased`` (the date of the last
      ``chased`` raise, or null when the ledger holds none), and
      ``days_past_confirm_by`` (computed HERE, from the task's authored due
      date — never by the turn). NO attempt ceiling: the records chase runs on
      cadence until the records land; escalation is stall-based and turn-owned
      (SKILL.md "Escalation").
  (d) HELD — the ledger carries an open per-MATTER hold (a ``fired`` raise on
      this skill's hold sentinel, ``HOLD_SOURCE_ID``): a turn found the matter
      unsafe to chase (no authored roster, roster without addresses, ambiguous
      receipt match pending a human). A held matter NEVER plans a chase;
      the hold re-surfaces on the re-fire window until a turn writes
      ``resolved`` on it (the ss #2402 rule, ported).

Plus two seat-level conditions:

  (c) CONFIG MISSING — ``chase_cadence_days`` is not authored: a
      client-commitment number with NO pack default. Fail-closed: wake once to
      surface it, remember the surface on a ledger sentinel, re-surface every
      ``escalation.refire_days`` until authored (#1899).
  (e) NO ROSTER TASKS — the pull returned open tasks on the seat but ZERO
      carry the roster marker. On a real firm this most likely means the
      firm's task convention does not match the connect-authored marker — and
      with zero items every wake would otherwise suppress forever, which is
      the worst fail direction (permanent silent darkness). Surface it on the
      same fire-once + re-fire-window rule, on its own sentinel.

Everything else -> a ``SUPPRESSED_WAKE`` heartbeat through the broker, then
``{"wakeAgent": false}``. The heartbeat IS the dead-man's-switch.

Sentinel namespaces are PER-SKILL on purpose
--------------------------------------------
``item_key`` hashes (matter_id, source_id, authored_date) and IGNORES label
(ss #2151), and ``derive_state`` joins on item_key alone — so two skills using
the same sentinel source_id would share one ledger identity: acking the
verification tracker's config sentinel would silence THIS skill's config
surface, and a verification signer-hold would block records chases. Every
sentinel here is therefore namespaced ``__mrc_*``, distinct from the
verification tracker's ``__chase_config__`` / ``__hold__``. Stall escalations
the turn raises must use ``STALL_SOURCE_PREFIX`` for the same reason: a
``fired`` on the chase item's own key would inflate the chase counter
(attempts counts fired+chased), so the numerator "chase N" would drift.

Fail direction
--------------
- Ledger unreadable -> FIRE-OPEN (wake). Never silently skip a chase.
- Smokeball pull failure / unrecognized envelope / a full 500-row page (the
  subset may be truncated; trusting it could silently drop providers) ->
  FIRE-OPEN (wake).
- Config unreadable / unauthored -> fail-CLOSED hold + re-fired surface (c).

``decide()`` is a pure function (no I/O), unit-tested with fake inputs.
``run_once()`` wires the real roster source + broker heartbeat + stdout.

Exit codes:
    0 — decision emitted (wake or suppress)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol, Sequence


def _load_skill_helpers():
    """The shared helpers vendored beside this file (canonical: operator/templates/skill_helpers.py).

    Looked up beside pre_run.py first, then under /opt/data/skills and /app/skills,
    the two places the seat image and the volume seed put this skill's files.
    A missing copy is a packaging defect, not a runtime condition to tolerate.
    """
    import importlib.util
    import sys as _sys
    from pathlib import Path as _Path

    candidates = [_Path(__file__).resolve().parent]
    for base in ("/opt/data/skills", "/app/skills"):
        candidates.append(_Path(base) / _SKILL_DIRNAME)
    for cand in candidates:
        module_path = cand / "skill_helpers.py"
        if not module_path.is_file():
            continue
        spec = importlib.util.spec_from_file_location("skill_helpers_" + _SKILL_DIRNAME.replace("-", "_"), module_path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        _sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    raise RuntimeError("skill_helpers.py is missing beside pre_run.py for " + _SKILL_DIRNAME)


_SKILL_DIRNAME = "medical-records-chaser"
_H = _load_skill_helpers()

SKILL_NAME = "medical-records-chaser"

# Seat-level sentinels (namespaced __mrc_* — see the module docstring).
_CONFIG_SENTINEL_SOURCE_ID = "__mrc_chase_config__"
_CONFIG_SENTINEL_LABEL = "mrc-chase-config-missing"
_NO_ROSTER_SENTINEL_SOURCE_ID = "__mrc_no_roster__"
_NO_ROSTER_SENTINEL_LABEL = "mrc-no-roster-tasks"

# Per-matter hold sentinel (the ss #2402 rule, ported; matter-level for the
# same reason as the sibling: the blocker is a fact about the matter, and it
# must survive the tracking task being completed, deleted, or recreated).
HOLD_SOURCE_ID = "__mrc_hold__"
_HOLD_LABEL = "mrc-chase-hold"

# Stall escalations the TURN raises (provider non-responsive, attorney should
# assess a records subpoena) must be keyed here, never on the chase item's own
# key — attempts counts every raise, and a stall ``fired`` on the item key
# would inflate the "chase N" numerator the email copies.
STALL_SOURCE_PREFIX = "__mrc_stall__"


# ---------------------------------------------------------------------------
# Roster-item source protocol — the real adapter reads the firm-authored
# records-request roster tasks (one open task per provider request).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RosterItem:
    """One open records-request roster task the firm maintains.

    Item identity is the roster task's STABLE Smokeball id (``task_id``);
    ``authored_date`` stays None — the confirm-by date on the task is an admin
    date the firm may refresh, and a re-dated task must not change identity
    (the sibling's rule). ``confirm_by`` is the task's authored due date: it
    seeds the FIRST chase and anchors ``days_past_confirm_by``. Once the item
    has a ``chased`` raise, cadence is measured from that raise instead.
    """

    matter_id: str
    task_id: str | None
    confirm_by: date
    authored_date: date | None = None
    label: str = "records-chase"


class RosterSource(Protocol):
    def pull_open_roster_items(self) -> "RosterPull": ...


@dataclass(frozen=True)
class RosterPull:
    """What one pull observed: the marker-matched items AND the total open-task
    count, so ``decide()`` can distinguish an empty seat (quiet) from a seat
    whose tasks carry no marker (surface — condition (e))."""

    items: tuple[RosterItem, ...]
    open_task_count: int


# ---------------------------------------------------------------------------
# Chase config — a CLIENT-COMMITMENT number. No pack default: unset is
# fail-closed hold + re-fired surface, never a silent interval (ADR 0035).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChaseConfig:
    chase_cadence_days: int | None = None  # days between chases; None = unauthored

    @property
    def authored(self) -> bool:
        return self.chase_cadence_days is not None


_DEFAULT_REFIRE_DAYS = 3


_pos_int_or_none = _H.pos_int_or_none


_pos_int = _H.pos_int


def _find_skill_settings(data) -> dict:
    return _H.find_skill_settings(data, SKILL_NAME)


def load_chase_config(customer_yaml_path: str | None = None) -> tuple[ChaseConfig, int]:
    """Read (ChaseConfig, refire_days) from the trusted volume customer.yaml.
    Missing file / PyYAML / parse failure -> unauthored (fail-closed)."""
    path = customer_yaml_path or os.environ.get("SMD_CUSTOMER_YAML_PATH")
    if not path:
        return ChaseConfig(), _DEFAULT_REFIRE_DAYS
    try:
        import yaml
    except ImportError:
        return ChaseConfig(), _DEFAULT_REFIRE_DAYS
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError):
        return ChaseConfig(), _DEFAULT_REFIRE_DAYS
    settings = _find_skill_settings(data)
    config = ChaseConfig(chase_cadence_days=_pos_int_or_none(settings.get("chase_cadence_days")))
    esc = data.get("escalation") if isinstance(data, dict) else None
    refire_days = _pos_int(esc.get("refire_days") if isinstance(esc, dict) else None, _DEFAULT_REFIRE_DAYS)
    return config, refire_days


# ---------------------------------------------------------------------------
# Escalation ledger — vendored byte-identical copy (test_escalation_ledger_sync).
# ---------------------------------------------------------------------------


def _load_ledger_module():
    import importlib.util

    candidates = [Path(__file__).resolve().parent]
    for base in ("/opt/data/skills", "/app/skills"):
        candidates.append(Path(base) / SKILL_NAME)
    for cand in candidates:
        module_path = cand / "escalation_ledger.py"
        if module_path.is_file():
            spec = importlib.util.spec_from_file_location("escalation_ledger_vendored_mrc", module_path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module
    return None


# ---------------------------------------------------------------------------
# Decision engine — pure, no I/O.
# ---------------------------------------------------------------------------

ACTION_CHASE = "chase"  # a provider chase is due
ACTION_SURFACE_CONFIG = "surface_config_missing"  # seat-level, refire window
ACTION_SURFACE_NO_ROSTER = "surface_no_roster_tasks"  # seat-level, refire window
ACTION_SURFACE_HOLD = "surface_hold"  # matter held; re-surface, never chase
ACTION_SUPPRESS = "suppress"


@dataclass(frozen=True)
class ItemPlan:
    """What the next turn should do for one item — including the STATE the
    email must copy verbatim (``attempt``, ``last_chased``,
    ``days_past_confirm_by``): the turn never recomputes or recalls these."""

    matter_id: str
    task_id: str | None
    item_key: str
    action: str
    attempt: int
    last_chased: str | None = None  # ISO date of the last chased raise, or None
    days_past_confirm_by: int | None = None  # computed here, never by the turn


@dataclass(frozen=True)
class WakeDecision:
    wake: bool
    decision_basis: str
    pre_run_inputs_digest: bytes
    plans: tuple[ItemPlan, ...] = ()
    extra_metadata: dict = field(default_factory=dict)


_hold_active = _H.hold_active


def _chase_due(state, confirm_by: date, today: date, *, cadence_days: int) -> bool:
    if state is None or state.last_raised_date is None:
        return today >= confirm_by
    return today >= state.last_raised_date + timedelta(days=max(0, cadence_days))


def _seat_sentinel_decision(
    ledger,
    states,
    *,
    source_id: str,
    label: str,
    action: str,
    basis_surface: str,
    basis_quiet: str,
    today: date,
    refire_days: int,
    raw_inputs_for_digest: bytes,
    extra: dict,
) -> WakeDecision:
    """Shared shape for the two seat-level surfaces (config missing, no roster
    tasks): fire-once + re-fire-window on a stable sentinel (#1899)."""
    key = ledger.item_key("", source_id, label, "")
    state = states.get(key)
    if not ledger.should_fire(state, today, refire_days=refire_days, ack_snooze_days=refire_days):
        return WakeDecision(
            wake=False,
            decision_basis=basis_quiet,
            pre_run_inputs_digest=raw_inputs_for_digest,
            extra_metadata=extra,
        )
    return WakeDecision(
        wake=True,
        decision_basis=basis_surface,
        pre_run_inputs_digest=raw_inputs_for_digest,
        plans=(
            ItemPlan(
                matter_id="",
                task_id=None,
                item_key=key,
                action=action,
                attempt=ledger.next_attempt(state),
            ),
        ),
        extra_metadata=extra,
    )


def decide(
    pull: RosterPull,
    config: ChaseConfig,
    ledger,
    events: Sequence[dict],
    *,
    raw_inputs_for_digest: bytes,
    today: date,
    refire_days: int,
) -> WakeDecision:
    """Pure decision: does any roster item need a chase today?"""
    states = ledger.derive_state(events)
    items = pull.items

    # (c) Seat-level: cadence unauthored → fail-closed hold + re-fired surface.
    if not config.authored:
        return _seat_sentinel_decision(
            ledger,
            states,
            source_id=_CONFIG_SENTINEL_SOURCE_ID,
            label=_CONFIG_SENTINEL_LABEL,
            action=ACTION_SURFACE_CONFIG,
            basis_surface="chase_config_unauthored_surface",
            basis_quiet="chase_config_unauthored_within_refire_window",
            today=today,
            refire_days=refire_days,
            raw_inputs_for_digest=raw_inputs_for_digest,
            extra={
                "open_item_count": len(items),
                "missing": ["chase_cadence_days"],
            },
        )

    # (e) Seat-level: open tasks exist but none carry the roster marker. Zero
    # items must never read as "nothing due" — at a real firm this is most
    # likely a marker-convention mismatch, and silence would be permanent.
    if not items and pull.open_task_count > 0:
        return _seat_sentinel_decision(
            ledger,
            states,
            source_id=_NO_ROSTER_SENTINEL_SOURCE_ID,
            label=_NO_ROSTER_SENTINEL_LABEL,
            action=ACTION_SURFACE_NO_ROSTER,
            basis_surface="no_roster_tasks_surface",
            basis_quiet="no_roster_tasks_within_refire_window",
            today=today,
            refire_days=refire_days,
            raw_inputs_for_digest=raw_inputs_for_digest,
            extra={"open_task_count": pull.open_task_count},
        )

    cadence_days = int(config.chase_cadence_days or 0)
    plans: list[ItemPlan] = []
    for item in items:
        key = ledger.item_key(item.matter_id, item.task_id, item.label, item.authored_date)
        state = states.get(key)
        if state is not None and (state.resolved or state.handed_off):
            continue
        # (d) HELD — an open hold on this MATTER blocks the chase for every
        # roster item on it. One surface per held matter per wake.
        hold_key = ledger.item_key(item.matter_id, HOLD_SOURCE_ID, _HOLD_LABEL, None)
        hold_state = states.get(hold_key)
        if _hold_active(hold_state):
            already_surfacing = any(p.item_key == hold_key for p in plans)
            if (
                not already_surfacing
                and not hold_state.handed_off
                and ledger.should_fire(hold_state, today, refire_days=refire_days, ack_snooze_days=refire_days)
            ):
                plans.append(
                    ItemPlan(
                        matter_id=item.matter_id,
                        task_id=item.task_id,
                        item_key=hold_key,
                        action=ACTION_SURFACE_HOLD,
                        attempt=ledger.next_attempt(hold_state),
                    )
                )
            continue
        # (a) Chase due?
        if _chase_due(state, item.confirm_by, today, cadence_days=cadence_days):
            last = None
            if state is not None and state.last_raised_date is not None:
                last = state.last_raised_date.isoformat()
            plans.append(
                ItemPlan(
                    matter_id=item.matter_id,
                    task_id=item.task_id,
                    item_key=key,
                    action=ACTION_CHASE,
                    attempt=ledger.next_attempt(state),
                    last_chased=last,
                    days_past_confirm_by=max(0, (today - item.confirm_by).days),
                )
            )
    if plans:
        chases = sum(1 for p in plans if p.action == ACTION_CHASE)
        holds = sum(1 for p in plans if p.action == ACTION_SURFACE_HOLD)
        return WakeDecision(
            wake=True,
            decision_basis="records_chase_due",
            pre_run_inputs_digest=raw_inputs_for_digest,
            plans=tuple(plans),
            extra_metadata={
                "chase_due": chases,
                "hold_surface_due": holds,
                "open_item_count": len(items),
                "items": [
                    {
                        "matter_id": p.matter_id,
                        "action": p.action,
                        "attempt": p.attempt,
                        "last_chased": p.last_chased,
                        "days_past_confirm_by": p.days_past_confirm_by,
                    }
                    for p in plans
                ],
            },
        )
    return WakeDecision(
        wake=False,
        decision_basis="no_records_chase_due",
        pre_run_inputs_digest=raw_inputs_for_digest,
        extra_metadata={"open_item_count": len(items)},
    )


# ---------------------------------------------------------------------------
# Runtime entrypoint.
# ---------------------------------------------------------------------------


_next_scheduled_at = _H.next_scheduled_at


# ---------------------------------------------------------------------------
# Pre-run handoff (ss#2547)
# ---------------------------------------------------------------------------
# The dates this script emits were READ from the firm's record. On the woken
# turn they arrive as prompt text, and prompt text is not a source: on
# 2026-08-19 the escalator's digest was refused five times by the identifier
# gate for the very dates this script had just read, and the escalation nobody
# received was a court date seven days out
# (docs/runbooks/operator/incidents/2026-08-19-gate-muted-escalator.md).
#
# This file is the seam that turns the script's read into a source. The READER
# is the overlay's ``shared/pre_run_handoff.take_handoff``, which binds the
# handoff to the one session started inside its window, seeds only the date
# atoms into the provenance register, and consumes it. The same block is copied
# verbatim into every bespoke pre_run that emits authored record dates: this
# script runs as a subprocess under the connector interpreter and cannot import
# the overlay.
#
# Best-effort by construction. Any failure goes to stderr and changes neither
# stdout nor the wake decision, because a routine that cannot write a handoff
# still has to wake.

_HANDOFF_SKILL = "medical-records-chaser"
_HANDOFF_STARTED_AT = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


_handoff_values = _H.handoff_values


_is_iso_day = _H.is_iso_day


def _write_pre_run_handoff(payload: dict) -> None:
    _H.write_pre_run_handoff(payload, _HANDOFF_SKILL, _HANDOFF_STARTED_AT)


def _emit_wake(decision: "WakeDecision | None" = None, *, basis: str | None = None) -> int:
    """Print the wake line WITH the decision's plans (ss #2226): the plans are
    the woken turn's work list, and for a chase they carry the state the email
    copies. Fail-open callers pass ``basis`` so the agent knows it woke blind
    and must enumerate the roster itself."""
    payload: dict = {"wakeAgent": True}
    resolved_basis = decision.decision_basis if decision is not None else basis
    if resolved_basis:
        payload["decision_basis"] = resolved_basis
    if decision is not None and decision.plans:
        payload["plans"] = [
            {
                "matter_id": p.matter_id,
                "task_id": p.task_id,
                "item_key": p.item_key,
                "action": p.action,
                "attempt": p.attempt,
                "last_chased": p.last_chased,
                "days_past_confirm_by": p.days_past_confirm_by,
            }
            for p in decision.plans
        ]
    _write_pre_run_handoff(payload)
    print(json.dumps(payload))
    return 0


async def _try_write_emitted_wake(
    audit_writer_factory,
    decision: "WakeDecision",
    *,
    skill_name: str,
    now: datetime,
) -> None:
    await _H.try_write_emitted_wake(
        audit_writer_factory,
        decision,
        skill_name=skill_name,
        next_scheduled_at=_next_scheduled_at(now),
        plan_counts=_H.plan_counts_total,
    )


_emit_suppress = _H.emit_suppress


def _item_to_dict(item: RosterItem) -> dict:
    return {
        "matter_id": item.matter_id,
        "task_id": item.task_id,
        "authored_date": item.authored_date.isoformat() if item.authored_date else None,
        "confirm_by": item.confirm_by.isoformat(),
        "label": item.label,
    }


async def run_once(
    sources: Sequence[RosterSource],
    audit_writer_factory,
    *,
    today: date | None = None,
    now: datetime | None = None,
    config: ChaseConfig | None = None,
    refire_days: int | None = None,
    ledger_module=None,
    ledger_events: Sequence[dict] | None = None,
) -> int:
    """Driver. Fail-open on ledger loss; config-read failure is the unauthored
    path (handled in ``decide``)."""
    now = now or datetime.now(timezone.utc)
    today = today or now.date()
    if config is None or refire_days is None:
        loaded_config, loaded_refire = load_chase_config()
        config = config or loaded_config
        refire_days = refire_days if refire_days is not None else loaded_refire

    ledger = ledger_module if ledger_module is not None else _load_ledger_module()
    if ledger is None:
        sys.stderr.write("[pre_run] escalation ledger unavailable; waking\n")
        return _emit_wake(basis="ledger_unavailable_fail_open")
    if ledger_events is None:
        ledger_events = ledger.read_ledger()

    all_items: list[RosterItem] = []
    open_task_count = 0
    raw_input_blob: bytes = b""
    for source in sources:
        pulled = source.pull_open_roster_items()
        all_items.extend(pulled.items)
        open_task_count += pulled.open_task_count
        raw_input_blob += json.dumps([_item_to_dict(i) for i in pulled.items], sort_keys=True).encode("utf-8")

    decision = decide(
        RosterPull(items=tuple(all_items), open_task_count=open_task_count),
        config,
        ledger,
        ledger_events,
        raw_inputs_for_digest=raw_input_blob,
        today=today,
        refire_days=refire_days,
    )
    if decision.wake:
        await _try_write_emitted_wake(audit_writer_factory, decision, skill_name=SKILL_NAME, now=now)
        return _emit_wake(decision)

    writer = audit_writer_factory()
    if writer is None:
        return _emit_wake(basis="no_audit_writer_fail_open")
    try:
        await writer.write_suppressed_wake(
            skill_name=SKILL_NAME,
            pre_run_inputs=decision.pre_run_inputs_digest,
            decision_basis=decision.decision_basis,
            next_scheduled_at=_next_scheduled_at(now),
            extra_metadata=decision.extra_metadata,
        )
    except Exception:  # noqa: BLE001 — any audit failure → wake
        return _emit_wake(basis="suppress_heartbeat_failed_fail_open")
    return _emit_suppress()


# ---------------------------------------------------------------------------
# Production wiring — the connector-venv pull + broker heartbeat writer.
# ---------------------------------------------------------------------------

_CONNECTOR_PYTHON_DEFAULT = "/opt/connectors/smokeball/.venv/bin/python"
_PULL_TIMEOUT_SECONDS = 60
_HEARTBEAT_TIMEOUT_SECONDS = 10
_PULL_PAGE_LIMIT = 500

# The firm-authored records-request roster tasks carry this marker in their
# subject (connect-step contract; the pilot's seed convention is
# "Medical records outstanding - <provider> (request roster)"). A seat whose
# open tasks NEVER match the marker surfaces condition (e) rather than going
# quietly dark — the marker being wrong must be loud.
_ROSTER_SUBJECT_MARKER = "request roster"

_PULL_SNIPPET = """\
import json

from smokeball_connector.client import build_client_from_env

client = build_client_from_env()
out = {}
try:
    out["tasks"] = client.get("/tasks", IsCompleted=False, Limit=500)
except Exception as exc:
    out["tasksError"] = str(exc)[:300]
print(json.dumps(out, default=str))
"""

_TASK_DATE_KEYS = ("dueDate", "DueDate", "due_date")
_TASK_SUBJECT_KEYS = ("subject", "Subject", "name", "Name", "title", "Title", "description")
_MATTER_ID_KEYS = ("matterId", "MatterId", "matter_id")
_SOURCE_ID_KEYS = ("id", "Id", "taskId", "TaskId")


_extract_items = _H.extract_items


_parse_iso_date = _H.parse_iso_date


_first_date = _H.first_date


def _first_str(item: dict, keys: Sequence[str]) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _matter_id_of(item: dict) -> str:
    return _H.matter_id_of(item, _MATTER_ID_KEYS)


def _source_id_of(item: dict) -> str | None:
    return _H.source_id_of(item, _SOURCE_ID_KEYS)


# Rehearsal/self-test artifacts carry "[SMD-PROBE <stamp>]" at the start of
# the subject (after the connector's "[Operator]" provenance stamp) — ss #2403.
# Probe rows are never roster items. Position-anchored: a real task quoting
# the marker mid-subject is not hidden.
_PROBE_MARK = "[SMD-PROBE"
_PROVENANCE_MARK = "[Operator]"


def _is_probe_subject(subject: str) -> bool:
    return _H.is_probe_subject(subject, _PROBE_MARK, _PROVENANCE_MARK)


def _is_roster_task(subject: str) -> bool:
    if _is_probe_subject(subject):
        return False
    return _ROSTER_SUBJECT_MARKER in subject.lower()


def parse_pull(raw: dict, *, today: date) -> tuple[RosterPull, str | None]:
    """Pure parse of the connector pull. Returns (pull, problem).

    A non-None problem means the view is partial or unrecognizable and the
    caller MUST wake — including a FULL page (exactly the page limit): the
    subset may be truncated, and trusting it could silently drop providers.
    A roster task with no due date seeds its first chase to today."""
    if raw.get("tasksError"):
        return RosterPull((), 0), f"pull error: tasksError={raw['tasksError']}"
    tasks = _extract_items(raw.get("tasks"))
    if tasks is None:
        return RosterPull((), 0), "unrecognized pull envelope"
    if len(tasks) >= _PULL_PAGE_LIMIT:
        return (
            RosterPull((), len(tasks)),
            f"pull returned a full page ({len(tasks)} rows); subset may be truncated",
        )
    items: list[RosterItem] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        subject = _first_str(task, _TASK_SUBJECT_KEYS)
        if not _is_roster_task(subject):
            continue
        due = _first_date(task, _TASK_DATE_KEYS) or today
        items.append(
            RosterItem(
                matter_id=_matter_id_of(task),
                task_id=_source_id_of(task),
                confirm_by=due,
                authored_date=None,
                label="records-chase",
            )
        )
    return RosterPull(tuple(items), len(tasks)), None


class SmokeballSubprocessSource:
    """RosterSource over a connector-venv subprocess pull."""

    def __init__(self, today: date) -> None:
        self._today = today

    def pull_open_roster_items(self) -> RosterPull:
        connector_python = os.environ.get("SMD_CONNECTOR_VENV_PYTHON", _CONNECTOR_PYTHON_DEFAULT)
        result = subprocess.run(  # raises on timeout → caller wakes
            # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args — argv[0] is the module-constant connector-venv interpreter, overridable only via SMD_CONNECTOR_VENV_PYTHON from the Machine's own boot env (same trust domain; the test seam). The snippet is a module constant; no request/agent-controlled data reaches argv.
            [connector_python, "-c", _PULL_SNIPPET],
            capture_output=True,
            text=True,
            timeout=_PULL_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            raise RuntimeError(f"smokeball pull exit {result.returncode}: {(result.stderr or '').strip()[:500]}")
        raw = json.loads((result.stdout or "").strip().splitlines()[-1])
        pull, problem = parse_pull(raw, today=self._today)
        if problem:
            raise RuntimeError(problem)
        return pull


class BrokerSuppressedWakeWriter:
    """Heartbeat writer over the broker's uid-gated verbs (#2253)."""

    def __init__(self, socket_path: str, customer_slug: str) -> None:
        self._socket_path = socket_path
        self._customer_slug = customer_slug

    async def write_suppressed_wake(
        self,
        *,
        skill_name: str,
        pre_run_inputs: bytes,
        decision_basis: str,
        next_scheduled_at: str,
        extra_metadata: dict | None = None,
    ) -> str:
        return self._append(
            verb="suppressed_wake_append",
            action_type="SUPPRESSED_WAKE",
            skill_name=skill_name,
            pre_run_inputs=pre_run_inputs,
            decision_basis=decision_basis,
            next_scheduled_at=next_scheduled_at,
            extra_metadata=extra_metadata,
        )

    async def write_emitted_wake(
        self,
        *,
        skill_name: str,
        pre_run_inputs: bytes,
        decision_basis: str,
        next_scheduled_at: str,
        extra_metadata: dict | None = None,
    ) -> str:
        return self._append(
            verb="emitted_wake_append",
            action_type="EMITTED_WAKE",
            skill_name=skill_name,
            pre_run_inputs=pre_run_inputs,
            decision_basis=decision_basis,
            next_scheduled_at=next_scheduled_at,
            extra_metadata=extra_metadata,
        )

    def _append(
        self,
        *,
        verb: str,
        action_type: str,
        skill_name: str,
        pre_run_inputs: bytes,
        decision_basis: str,
        next_scheduled_at: str,
        extra_metadata: dict | None,
    ) -> str:
        request = {
            "action": verb,
            "row": {
                "action_type": action_type,
                "actor": "agent",
                "actor_role": "agent",
                "skill_name": skill_name,
                "input_digest": hashlib.sha256(pre_run_inputs).hexdigest(),
                "metadata": json.dumps(
                    {
                        "decision_basis": decision_basis,
                        "next_scheduled_at": next_scheduled_at,
                        "platform": "cron-pre-run",
                        "customer": self._customer_slug,
                        **(extra_metadata or {}),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ),
            },
        }
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(_HEARTBEAT_TIMEOUT_SECONDS)
            sock.connect(self._socket_path)
            sock.sendall(json.dumps(request).encode("utf-8") + b"\n")
            raw = b""
            while not raw.endswith(b"\n"):
                chunk = sock.recv(4096)
                if not chunk:
                    break
                raw += chunk
        response = json.loads(raw.decode("utf-8"))
        if response.get("ok") is not True:
            raise RuntimeError(f"heartbeat rejected: {response}")
        return str(response.get("id", ""))


def _writer_factory():
    return _H.writer_factory(BrokerSuppressedWakeWriter)


def main() -> int:
    customer_slug = os.environ.get("CUSTOMER_SLUG")
    if not customer_slug:
        sys.stderr.write("[pre_run] CUSTOMER_SLUG unset; falling back to wake\n")
        return _emit_wake(basis="customer_slug_unset_fail_open")
    config, refire_days = load_chase_config()
    today = datetime.now(timezone.utc).date()
    source = SmokeballSubprocessSource(today)
    try:
        return asyncio.run(
            run_once(
                [source],
                _writer_factory,
                today=today,
                config=config,
                refire_days=refire_days,
            )
        )
    except Exception as exc:  # noqa: BLE001 — any wiring failure → wake
        sys.stderr.write(f"[pre_run] records-chaser pre_run failed ({exc}); waking\n")
        return _emit_wake(basis="pre_run_crashed_fail_open")


if __name__ == "__main__":
    sys.exit(main())
