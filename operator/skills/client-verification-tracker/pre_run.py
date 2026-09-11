#!/usr/bin/env python3
"""client-verification-tracker pre-run gate — ADR 0021 Stream B (WP-B, #1889).

Runs BEFORE the Hermes cron daemon wakes the agent. Decides whether any open
verification item actually needs a turn today, so the expensive chase agent does
NOT wake every weekday when nothing is due.

Why this skill graduated off the shared empty-seat gate
-------------------------------------------------------
The shared ``operator/templates/pre_run_gate.py`` wakes on *any* open matter — a
seat with live matters wakes the chase daily even when no chase is due and the
internal escalation-to-a-person email repeats on every wake (observed live:
identical alerts July 6, 7, 8, 14). The template's own docstring calls a deeper
state-delta gate the intended follow-on; this is that follow-on. The chase now
reads the escalation ledger (the shared, broker-owned telemetry state, WP-A) and
the firm's authored cadence/ceiling, and wakes only on a real transition.

Wake / suppress decision
------------------------
For each open verification tracking item the skill maintains:

  (a) CHASE DUE — cadence authored, the last ``chased`` raise is at least
      ``chase_cadence_days`` old (or there is no prior chase and the tracking
      task's authored due date has arrived), and attempts are below the ceiling.
  (b) CEILING HAND-OFF — attempts have reached ``escalate_after_attempts`` and
      the ledger holds no ``handed_off`` event yet: wake ONCE to stop chasing the
      client and hand the open item to the responsible attorney. A ``handed_off``
      item is terminal for autonomous wakes.
  (d) HELD — the ledger carries an open per-MATTER hold (a ``fired`` raise on
      the matter's hold sentinel; see ``HOLD_SOURCE_ID``): a turn that
      inspected the matter found it cannot chase safely (signer unresolved, or
      any other surface-and-ask condition). A held matter NEVER plans a chase
      or a hand-off for any of its verification items; instead the hold
      re-surfaces to a person on the re-fire window until a turn writes
      ``resolved`` on the hold sentinel (ss #2402 — on 2026-08-11 the turn
      surfaced "signer not confirmed" and three days later the next wake
      planned a chase to the unconfirmed signer, because the hold lived only
      in an email).

Plus one seat-level condition:

  (c) CONFIG MISSING — ``chase_cadence_days`` or ``escalate_after_attempts`` is
      not authored: these are client-commitment numbers (File 07), so there is NO
      pack default. Fail-closed: wake to surface "chase cadence / escalation
      attempt-count not authored", record that surface in the ledger, and then
      re-surface on the shared fire-once + re-fire-window rule (every
      ``refire_days``) until the dials are authored — a chase held dark must not
      go permanently silent on one missed notice (#1899). An unset dial holds
      the client chase; it never releases it and never daily-spams.

Everything else -> a ``SUPPRESSED_WAKE`` heartbeat through the broker, then
``{"wakeAgent": false}``. The heartbeat IS the dead-man's-switch: a scheduled
tick with no audit row is the alarm the watcher-health view fires on.

What this gate deliberately does NOT own
----------------------------------------
Deadline-proximity escalation on an unsigned verification (a verification nearing
its authored response deadline; RFA highest severity) is owned by
``deadline-miss-escalator``, which pulls every authored deadline — verification
response deadlines included — and applies its own re-fire policy. The chase does
not run a second deadline pull; its internal escalation references the deadline
lane by pointer (the dedup rule), so a nearing-deadline verification is escalated
once by the owning lane, not duplicated into a second morning email. The
attempt-ceiling hand-off here and the deadline-proximity escalation there remain
the two independent triggers the skill contract promises; only the ownership is
split, which removes the duplicate-signal defect.

Fail direction (per the plan)
-----------------------------
- Ledger unreadable (module load / read failure) -> FIRE-OPEN (wake), the
  pre-graduation behavior. Never silently skip a chase.
- Smokeball pull failure / unrecognized envelope -> FIRE-OPEN (wake).
- Config file unreadable / unauthored -> treat as unauthored: fail-CLOSED hold
  plus the re-fired "config missing" surface (condition (c)), never a silent
  default and never a daily spam loop.

``decide()`` is a pure function (no I/O), unit-tested with fake inputs.
``run_once()`` wires the real verification-task source + broker heartbeat + stdout.

Exit codes:
    0 — decision emitted (wake or suppress)
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol, Sequence

SKILL_NAME = "client-verification-tracker"

# The config-missing surface is seat-level, not per-item. It is remembered in the
# ledger under a stable sentinel item_key so it fires on the re-fire window
# (every refire_days until authored), never daily (#1899).
_CONFIG_SENTINEL_SOURCE_ID = "__chase_config__"
_CONFIG_SENTINEL_LABEL = "chase-config-missing"

# Per-MATTER hold sentinel (ss #2402). A turn that finds a matter unsafe to
# chase (signer unresolved is the founding case) appends a ``fired`` raise on
# the matter's HOLD identity — the matter id plus the fixed source id below —
# via the broker (derive-then-handle, ss #2304). ``decide()`` then refuses to
# plan a chase or hand-off for ANY verification item on that matter until a
# turn appends ``resolved`` on the hold.
#
# The identity is deliberately MATTER-level, not task-level: the founding
# blocker (conflicting Minor/Deceased sub-roles on the plaintiff) is a fact
# about the matter's roles, not about one tracking task. A task-keyed hold
# would evaporate the moment the tracking task is completed, deleted, or
# recreated — the first wake on a replacement task would plan a chase straight
# past the still-unresolved blocker. Matter-level is also fail-closed for
# multi-plaintiff matters: one unresolved signer holds every verification
# chase on the matter, and the re-surface asks a person rather than guessing
# which sibling items are safe.
#
# The constants are the cross-side contract: the turn and this gate must
# derive the same key from the same components, so they live here and are
# cited verbatim in SKILL.md.
HOLD_SOURCE_ID = "__hold__"
_HOLD_LABEL = "chase-hold"

# The role-snapshot projection + hash + connector pull for the signer
# determination (ss #2402 Part 3) live in the sibling ``role_snapshot.py``,
# path-loaded exactly like the vendored ledger below (the scheduler stages
# this file alone; the skill dir carries the siblings). A load failure
# degrades every hash to None (unknown) - fail toward holding, never toward
# trusting. Projection pinned by the 2026-08-31 live probe
# (vfy_01M1CB0NTKCV3ACRY0P6QD6JX7); fixture: tests/role_snapshot_probe.json.


# ---------------------------------------------------------------------------
# Verification-item source protocol — the real adapter reads the open
# verification TRACKING tasks the skill maintains on each matter (one per
# plaintiff/response-set/version). The tracking task's authored due date is the
# first-chase-due date the skill set when it opened the item.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerificationItem:
    """One open verification the skill is tracking.

    Item identity is the tracking task's STABLE Smokeball id (``task_id``); the
    ``item_key`` is ``item_key(matter_id, task_id, label, authored_date)``.
    ``authored_date`` must be a value that does NOT move over the item's life —
    the tracking task's due date is re-dated on each chase, so it is NOT used for
    identity (that would change the key and orphan the ledger history). The pull
    has no separate stable response-set date, so it leaves ``authored_date`` None
    and lets ``task_id`` carry identity; ``label`` is the fixed
    ``"client-verification"``. The agent MUST compute the same tuple when it
    appends a ``chased`` / ``handed_off`` / ``resolved`` event (see SKILL.md).

    ``next_chase_due`` is the tracking task's authored due date: the date the
    skill set for the FIRST chase. Once the item has a ``chased`` event in the
    ledger, cadence is computed from that raise instead (see ``_chase_due``), so
    a stale task date cannot re-open a chased item early. ``task_id`` is ``None``
    only for an item with no stable id (blanket-only, and then not per-item
    tokenizable)."""

    matter_id: str
    task_id: str | None
    next_chase_due: date
    authored_date: date | None = None
    label: str = "client-verification"
    # The firm's human-readable matter number + typed absence, projected in
    # code by the connector's matter.id -> matter.number join during the pull
    # (ss #2390; WS-RENDER — the rendered alert names numbers, never GUIDs).
    matter_number: str | None = None
    matter_number_absent: str | None = None


class VerificationSource(Protocol):
    """Adapter the real Smokeball reader satisfies: one VerificationItem per open
    verification tracking task the skill maintains."""

    def pull_open_verifications(self) -> Sequence[VerificationItem]: ...


# ---------------------------------------------------------------------------
# Chase config — CLIENT-COMMITMENT numbers (File 07). No pack default: unset is
# fail-closed hold + re-fired surface, never a silent interval (ADR 0035).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChaseConfig:
    chase_cadence_days: int | None = None  # days between verification chases; None = unauthored
    escalate_after_attempts: int | None = None  # unanswered chases before hand-off; None = unauthored

    @property
    def authored(self) -> bool:
        """Both dials must be authored for the chase to run at all."""
        return self.chase_cadence_days is not None and self.escalate_after_attempts is not None


# The internal escalation-to-a-person raise (the ceiling hand-off, and the
# config-missing surface) follows the shared fire-once + re-fire-window rule so
# it never repeats on every wake. refire_days is legitimate pack-authored content
# (a repetitive internal alert beats a silent one), read from the top-level
# escalation: block the same way the escalator reads it.
_DEFAULT_REFIRE_DAYS = 3


def _pos_int_or_none(value):
    """A positive int, else None. Any junk (bool, str, <=0, missing) -> None so
    the caller treats the dial as unauthored (fail-closed), never as a default."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def _pos_int(value, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int) and value > 0:
        return value
    return fallback


def _find_skill_settings(data) -> dict:
    """Return this skill's per-skill ``settings:`` block from the materialized
    customer.yaml, searching every persona's ``skills:`` list. Empty dict when
    the entry or its settings are absent/malformed (-> unauthored)."""
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
            if not isinstance(entry, dict) or entry.get("name") != SKILL_NAME:
                continue
            settings = entry.get("settings")
            return settings if isinstance(settings, dict) else {}
    return {}


def load_chase_config(customer_yaml_path: str | None = None) -> tuple[ChaseConfig, int]:
    """Read (ChaseConfig, refire_days) from the trusted volume customer.yaml
    (``SMD_CUSTOMER_YAML_PATH`` — the root-owned copy the ADR-0044 applier
    live-updates, so a value change reaches pre_run without a rebuild).

    ``chase_cadence_days`` / ``escalate_after_attempts`` come from THIS skill's
    per-skill ``settings:`` block (never the top-level ``escalation:`` block —
    that carries the escalator's windows). ``refire_days`` for the internal
    escalation comes from ``escalation.refire_days`` (pack default 3).

    Missing file, missing PyYAML, or an unparseable file -> unauthored config
    (fail-closed) with the pack-default refire window. Config-read failure is the
    unauthored path by design (never a silent cadence)."""
    path = customer_yaml_path or os.environ.get("SMD_CUSTOMER_YAML_PATH")
    if not path:
        return ChaseConfig(), _DEFAULT_REFIRE_DAYS
    try:
        import yaml  # available in the Hermes venv (the overlay's config reader uses it)
    except ImportError:
        return ChaseConfig(), _DEFAULT_REFIRE_DAYS
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError):
        return ChaseConfig(), _DEFAULT_REFIRE_DAYS
    settings = _find_skill_settings(data)
    config = ChaseConfig(
        chase_cadence_days=_pos_int_or_none(settings.get("chase_cadence_days")),
        escalate_after_attempts=_pos_int_or_none(settings.get("escalate_after_attempts")),
    )
    esc = data.get("escalation") if isinstance(data, dict) else None
    refire_days = _pos_int(esc.get("refire_days") if isinstance(esc, dict) else None, _DEFAULT_REFIRE_DAYS)
    return config, refire_days


# ---------------------------------------------------------------------------
# Sibling-module loading. The escalation ledger is a vendored copy of the
# shared module (byte-identical to operator/workspace_broker/
# escalation_ledger.py; test_escalation_ledger_sync); role_snapshot.py is this
# skill's own. Loaded by absolute path because the cron scheduler may run
# pre_run from a staged scripts dir, not the skill dir. If the ledger cannot
# be loaded, the chase fails OPEN — it wakes (the pre-graduation behavior)
# rather than going silent; if role_snapshot cannot be loaded, every snapshot
# hash degrades to unknown (fail toward holding).
# ---------------------------------------------------------------------------


def _load_sibling_module(filename: str, module_name: str):
    import importlib.util

    candidates = [Path(__file__).resolve().parent]
    for base in ("/opt/data/skills", "/app/skills"):
        candidates.append(Path(base) / SKILL_NAME)
    for cand in candidates:
        module_path = cand / filename
        if module_path.is_file():
            spec = importlib.util.spec_from_file_location(module_name, module_path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            # Register BEFORE exec: on Python 3.14 a `@dataclass` under
            # `from __future__ import annotations` resolves its string
            # annotations via sys.modules[cls.__module__] at class-creation time.
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module
    return None


def _load_ledger_module():
    return _load_sibling_module("escalation_ledger.py", "escalation_ledger_vendored_cvt")


def _load_role_snapshot_module():
    return _load_sibling_module("role_snapshot.py", "cvt_role_snapshot")


# ---------------------------------------------------------------------------
# Decision engine — pure, no I/O. Unit-tested directly.
# ---------------------------------------------------------------------------

# Per-item actions the decision can reach.
ACTION_CHASE = "chase"  # a client nudge is due (attempt < ceiling)
ACTION_HANDOFF = "handoff"  # ceiling reached; stop chasing, hand to the attorney (once)
ACTION_SURFACE_CONFIG = "surface_config_missing"  # seat-level, on the refire window
ACTION_SURFACE_HOLD = "surface_hold"  # item held (e.g. signer unresolved); re-surface, never chase
ACTION_SUPPRESS = "suppress"  # nothing due for this item


@dataclass(frozen=True)
class ItemPlan:
    """What the next turn should do for one item, plus the attempt number a chase
    would carry (the ``nudge <#> of <max>`` numerator).

    ``reason`` qualifies a surface plan (today: ``determination_stale`` on a
    hold surface swapped in for a due chase). ``current_role_snapshot_sha256``
    rides on every ``surface_hold`` plan (or None when the pull failed): the
    resolving turn COPIES it into the hold release's determination — never
    computes it. ``determination`` is the consult stamp for a matter whose hold
    carries a recorded determination:
    ``{note, recorded_sha256, status: current|stale|unknown}``.
    """

    matter_id: str
    task_id: str | None
    item_key: str
    action: str
    attempt: int  # for a chase: the nudge number this chase would be
    reason: str = ""
    current_role_snapshot_sha256: str | None = None
    determination: dict | None = None
    # Code-projected matter number + typed absence (ss #2390) and the tracking
    # task's due date, carried so the rendered alert can name the matter and
    # the provenance handoff can seed the (number, date) association
    # (WS-RENDER). Never composed here; copied off the pulled item.
    matter_number: str | None = None
    matter_number_absent: str | None = None
    next_chase_due: str | None = None


@dataclass(frozen=True)
class WakeDecision:
    wake: bool
    decision_basis: str
    pre_run_inputs_digest: bytes
    plans: tuple[ItemPlan, ...] = ()
    extra_metadata: dict = field(default_factory=dict)


def _hold_active(hold_state) -> bool:
    """True iff the item's hold sentinel blocks the chase.

    A hold is open once it has any raise and is not ``resolved``. An ``acked``
    hold stays BLOCKING — ack means "a person saw the surface", not "the
    condition is fixed"; it only snoozes the re-surface (``should_fire``
    handles that). ``handed_off`` likewise blocks and additionally ends
    autonomous re-surfacing: a person owns the item. Only ``resolved`` —
    written by the turn that confirmed the condition is fixed (e.g. the signer
    is confirmed) — releases the chase.
    """
    if hold_state is None or hold_state.attempts == 0:
        return False
    return not hold_state.resolved


def _chase_due(
    state,
    next_chase_due: date,
    today: date,
    *,
    cadence_days: int,
) -> bool:
    """True iff a client chase is due now. With a prior ``chased`` raise, cadence
    is measured from it (the last raise + cadence). With no prior chase, the
    tracking task's authored due date seeds the first chase."""
    if state is None or state.last_raised_date is None:
        return today >= next_chase_due
    return today >= state.last_raised_date + timedelta(days=max(0, cadence_days))


def _determination_stamp(hold_state, current_hash: str | None) -> dict | None:
    """The consult stamp for a matter whose hold sentinel carries a recorded
    determination, or None when it carries none.

    ``status`` compares the determination's ``role_snapshot_sha256`` against
    the CURRENT hash pre_run computed this run: ``current`` (facts unchanged —
    the turn may adopt the determination when its fresh derivation is
    ambiguous), ``stale`` (the roles moved since the determination was
    recorded — the discrepancy is escalated, never silently preferred either
    way), ``unknown`` (the pull failed — the turn treats it as no
    determination: fresh derivation, ambiguity holds).
    """
    determination = getattr(hold_state, "determination", None) if hold_state else None
    if not isinstance(determination, dict):
        return None
    recorded = determination.get("role_snapshot_sha256")
    if current_hash is None:
        status = "unknown"
    elif recorded == current_hash:
        status = "current"
    else:
        status = "stale"
    return {
        "note": determination.get("note"),
        "recorded_sha256": recorded,
        "status": status,
    }


def decide(
    items: Sequence[VerificationItem],
    config: ChaseConfig,
    ledger,
    events: Sequence[dict],
    *,
    raw_inputs_for_digest: bytes,
    today: date,
    refire_days: int,
    role_snapshot_hashes: dict | None = None,
) -> WakeDecision:
    """Pure decision: does any open verification need a turn today?

    ``ledger`` is the loaded ledger module (or None → caller fires open before
    reaching here). ``events`` are the ledger rows. Wake iff any item plan is
    actionable; otherwise suppress. ``role_snapshot_hashes`` maps matter_id ->
    the CURRENT role-snapshot hash (or None when the pull failed), computed by
    the caller for hold-bearing matters only; absent entries read as unknown.
    """
    states = ledger.derive_state(events)
    snapshot_hashes = role_snapshot_hashes or {}

    # (c) Seat-level: config unauthored → fail-closed hold + re-fired surface.
    # The sentinel follows the same fire-once + re-fire-window rule as every
    # other internal raise (never daily, but never once-ever either): a held
    # chase re-surfaces every refire_days until the dials are authored (#1899).
    if not config.authored:
        sentinel_key = ledger.item_key("", _CONFIG_SENTINEL_SOURCE_ID, _CONFIG_SENTINEL_LABEL, "")
        sentinel_state = states.get(sentinel_key)
        if not ledger.should_fire(sentinel_state, today, refire_days=refire_days, ack_snooze_days=refire_days):
            return WakeDecision(
                wake=False,
                decision_basis="chase_config_unauthored_within_refire_window",
                pre_run_inputs_digest=raw_inputs_for_digest,
                extra_metadata={"open_item_count": len(items)},
            )
        return WakeDecision(
            wake=True,
            decision_basis="chase_config_unauthored_surface",
            pre_run_inputs_digest=raw_inputs_for_digest,
            plans=(
                ItemPlan(
                    matter_id="",
                    task_id=None,
                    item_key=sentinel_key,
                    action=ACTION_SURFACE_CONFIG,
                    attempt=ledger.next_attempt(sentinel_state),
                ),
            ),
            extra_metadata={
                "open_item_count": len(items),
                "missing": [
                    name
                    for name, val in (
                        ("chase_cadence_days", config.chase_cadence_days),
                        ("escalate_after_attempts", config.escalate_after_attempts),
                    )
                    if val is None
                ],
            },
        )

    cadence_days = int(config.chase_cadence_days or 0)
    ceiling = int(config.escalate_after_attempts or 0)
    plans: list[ItemPlan] = []
    for item in items:
        key = ledger.item_key(item.matter_id, item.task_id, item.label, item.authored_date)
        state = states.get(key)
        # Terminal: resolved, or already handed off (a person owns it now).
        if state is not None and (state.resolved or state.handed_off):
            continue
        # (d) HELD — an open hold on this MATTER blocks chase AND hand-off for
        # every verification item on it (the ambiguity precedes the count, and
        # it is a fact about the matter, so a recreated tracking task cannot
        # slip past it). Re-surface on the re-fire window so a held matter
        # never goes permanently dark (#1899); release only on a ``resolved``
        # hold event (ss #2402). One surface per held matter per wake, even
        # with several tracked items on it.
        hold_key = ledger.item_key(item.matter_id, HOLD_SOURCE_ID, _HOLD_LABEL, None)
        hold_state = states.get(hold_key)
        current_hash = snapshot_hashes.get(item.matter_id)
        det_stamp = _determination_stamp(hold_state, current_hash)
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
                        current_role_snapshot_sha256=current_hash,
                        determination=det_stamp,
                    )
                )
            continue
        attempts = 0 if state is None else state.attempts
        if attempts >= ceiling:
            # (b) Ceiling reached, not yet handed off → wake once to hand off.
            plans.append(
                ItemPlan(
                    matter_id=item.matter_id,
                    task_id=item.task_id,
                    item_key=key,
                    action=ACTION_HANDOFF,
                    attempt=attempts,
                    determination=det_stamp,
                )
            )
            continue
        # (a) Chase due?
        if _chase_due(state, item.next_chase_due, today, cadence_days=cadence_days):
            if det_stamp is not None and det_stamp["status"] == "stale":
                # The recorded determination's snapshot no longer matches the
                # live roles: the facts the release rested on have moved. Never
                # silently prefer either reading — swap the chase for a hold
                # surface so a person decides, with both readings on the table.
                # The item_key is the HOLD key: the turn's re-surface appends a
                # fresh ``fired`` there, which (post the symmetric-reset fix)
                # re-activates the hold and starts the normal re-fire window.
                if not any(p.item_key == hold_key for p in plans):
                    plans.append(
                        ItemPlan(
                            matter_id=item.matter_id,
                            task_id=item.task_id,
                            item_key=hold_key,
                            action=ACTION_SURFACE_HOLD,
                            attempt=ledger.next_attempt(hold_state),
                            reason="determination_stale",
                            current_role_snapshot_sha256=current_hash,
                            determination=det_stamp,
                        )
                    )
                continue
            plans.append(
                ItemPlan(
                    matter_id=item.matter_id,
                    task_id=item.task_id,
                    item_key=key,
                    action=ACTION_CHASE,
                    attempt=ledger.next_attempt(state),  # the nudge number this chase carries
                    determination=det_stamp,
                )
            )
    # Stamp each plan with its matter's code-projected number + the tracking
    # task's due date (WS-RENDER): the renderer names the matter and the
    # provenance handoff seeds the (number, date) association. One site, so a
    # new plan kind cannot forget the stamp.
    by_matter = {i.matter_id: i for i in items}

    def _stamp(p: ItemPlan) -> ItemPlan:
        it = by_matter.get(p.matter_id)
        if it is None:
            return p
        return replace(
            p,
            matter_number=it.matter_number,
            matter_number_absent=it.matter_number_absent,
            next_chase_due=it.next_chase_due.isoformat(),
        )

    actionable = tuple(_stamp(p) for p in plans if p.action != ACTION_SUPPRESS)
    if actionable:
        chases = sum(1 for p in actionable if p.action == ACTION_CHASE)
        handoffs = sum(1 for p in actionable if p.action == ACTION_HANDOFF)
        holds = sum(1 for p in actionable if p.action == ACTION_SURFACE_HOLD)
        return WakeDecision(
            wake=True,
            decision_basis="verification_action_due",
            pre_run_inputs_digest=raw_inputs_for_digest,
            plans=actionable,
            extra_metadata={
                "chase_due": chases,
                "handoff_due": handoffs,
                "hold_surface_due": holds,
                "open_item_count": len(items),
                "items": [
                    {
                        "matter_id": p.matter_id,
                        "action": p.action,
                        "attempt": p.attempt,
                        "ceiling": ceiling,
                        **({"reason": p.reason} if p.reason else {}),
                    }
                    for p in actionable
                ],
            },
        )
    return WakeDecision(
        wake=False,
        decision_basis="no_verification_action_due",
        pre_run_inputs_digest=raw_inputs_for_digest,
        extra_metadata={"open_item_count": len(items)},
    )


# ---------------------------------------------------------------------------
# Runtime entrypoint — wires the verification source + broker heartbeat + stdout.
# ---------------------------------------------------------------------------


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

_HANDOFF_SKILL = "client-verification-tracker"
_HANDOFF_STARTED_AT = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_pre_run_handoff(payload: dict) -> None:
    """Project the emitted payload down to dates + matter ids + per-matter
    (number, dates) records and hand it off — delegated to the sibling
    ``handoff_writer.py`` (module-size ratchet; the records block landed with
    WS-RENDER because the rendered alert names matter numbers). A missing
    sibling costs the seeding, never the wake: the identifier gate then
    refuses the full body and the skeleton fallback ships."""
    writer = _load_sibling_module("handoff_writer.py", "cvt_handoff_writer")
    if writer is None:
        sys.stderr.write("[pre_run] handoff writer sibling unavailable\n")
        return
    writer.write_pre_run_handoff(payload, skill=_HANDOFF_SKILL, started_at=_HANDOFF_STARTED_AT)


def _emit_suppress() -> int:
    print(json.dumps({"wakeAgent": False}))
    return 0


def _next_scheduled_at(now: datetime, schedule_hours: int = 24) -> str:
    return (now + timedelta(hours=schedule_hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _parse():
    """The pure date/string coercions, in the sibling ``parsing.py`` (module-size
    ratchet). A missing sibling is a deployment fault, not a runtime state, so
    this raises rather than degrading a parse into a silent None."""
    mod = _load_sibling_module("parsing.py", "cvt_parsing")
    if mod is None:
        raise RuntimeError("parsing.py sibling missing")
    return mod


def _emit_wake(
    decision: "WakeDecision | None" = None,
    *,
    basis: str | None = None,
    extra: dict | None = None,
) -> int:
    """Print the wake gate line — WITH the decision's plans (ss #2226).

    Hermes reads only ``wakeAgent`` from the last stdout line and then injects
    the whole stdout verbatim into the woken agent's prompt (the "Script
    Output" block). Emitting a bare ``{"wakeAgent": true}`` therefore threw
    away the one thing the gate computed that the agent cannot cheaply
    re-derive: WHICH items are actionable. A new verification item has no
    escalation-ledger state yet, so a ledger-driven scan never visits its
    matter — on 2026-08-10 the gate woke the agent for exactly one due chase
    and the agent concluded nothing was due (#2226). The plans in this line
    are the woken turn's work list; SKILL.md step 5 consumes them.

    Fail-open callers have no decision — they pass ``basis`` so the agent
    knows it woke blind and must run the full-enumeration fallback.
    """
    payload: dict = {"wakeAgent": True}
    resolved_basis = decision.decision_basis if decision is not None else basis
    if resolved_basis:
        payload["decision_basis"] = resolved_basis
    if decision is not None and decision.plans:
        serialized = []
        for p in decision.plans:
            entry: dict = {
                "matter_id": p.matter_id,
                "matter_number": p.matter_number,
                "matter_number_absent": p.matter_number_absent,
                "next_chase_due": p.next_chase_due,
                "task_id": p.task_id,
                "item_key": p.item_key,
                "action": p.action,
                "attempt": p.attempt,
            }
            if p.reason:
                entry["reason"] = p.reason
            if p.action == ACTION_SURFACE_HOLD:
                # Always present on a hold surface, explicitly null when the
                # snapshot pull failed: the resolving turn COPIES this value
                # into the hold release's determination, and "unknown" must be
                # visible as null rather than silently absent (a release with
                # no hash to copy waits for a run where the pull succeeds —
                # fail toward holding).
                entry["current_role_snapshot_sha256"] = p.current_role_snapshot_sha256
            if p.determination is not None:
                entry["determination"] = p.determination
            serialized.append(entry)
        payload["plans"] = serialized
    if decision is not None and decision.extra_metadata.get("dispatch_expected"):
        # WS-RENDER: a deterministic out-of-turn dispatch is coming; if no
        # dispatch note is injected, the SKILL.md failure-note applies.
        payload["dispatch_expected"] = True
    if extra:
        # Already filtered to blind_wake.WAKE_PAYLOAD_KEYS. Carrying
        # dispatch_expected is what puts the turn on SKILL.md's "a dispatch is
        # coming, compose nothing" branch instead of the one it ignored on
        # 2026-09-02.
        payload.update(extra)
    _write_pre_run_handoff(payload)
    print(json.dumps(payload))
    return 0


def _item_to_dict(item: VerificationItem) -> dict:
    return {
        "matter_id": item.matter_id,
        "task_id": item.task_id,
        # authored_date is None in production (identity is the stable task_id).
        "authored_date": item.authored_date.isoformat() if item.authored_date else None,
        "next_chase_due": item.next_chase_due.isoformat(),
        "label": item.label,
    }


def _snapshot_hashes_for(items, ledger, ledger_events, snapshot_hash_fn) -> dict:
    """role_snapshot.hold_matter_snapshot_hashes via the sibling loader —
    hashes only for hold-bearing matters, pulls serialized (1 vCPU seat).
    No sibling module -> empty map: every consult reads unknown, the
    fail-toward-holding direction. ``snapshot_hash_fn`` is the test seam;
    production leaves it None and uses the sibling's live connector pull."""
    snapshot = _load_role_snapshot_module()
    if snapshot is None:
        return {}
    try:
        return snapshot.hold_matter_snapshot_hashes(
            items,
            ledger,
            ledger_events,
            snapshot_hash_fn or snapshot.pull_role_snapshot_hash,
            hold_source_id=HOLD_SOURCE_ID,
            hold_label=_HOLD_LABEL,
        )
    except Exception:  # noqa: BLE001 — unknown, never a guess
        return {}


async def run_once(
    sources: Sequence[VerificationSource],
    audit_writer_factory,  # () -> SuppressedWakeWriter | None
    *,
    today: date | None = None,
    now: datetime | None = None,
    config: ChaseConfig | None = None,
    refire_days: int | None = None,
    ledger_module=None,
    ledger_events: Sequence[dict] | None = None,
    snapshot_hash_fn=None,
) -> int:
    """Driver. Returns the exit code; emits stdout JSON as a side effect.

    ``audit_writer_factory`` is called only when we would suppress; it may return
    None (dev mode) → suppression falls back to wake (mirror-don't-gate).

    Fail-open on ledger loss: if the ledger module cannot be loaded, wake (the
    pre-graduation behavior). Config-read is the unauthored path on failure, but
    that is handled inside ``decide`` (re-fired surface), not here.
    ``config``/``refire_days``/``ledger_events`` default to the live config +
    on-disk ledger; tests inject them directly. ``snapshot_hash_fn`` defaults to
    the sibling role_snapshot module's live connector pull; tests inject a fake."""
    now = now or datetime.now(timezone.utc)
    today = today or now.date()
    if config is None or refire_days is None:
        loaded_config, loaded_refire = load_chase_config()
        config = config or loaded_config
        refire_days = refire_days if refire_days is not None else loaded_refire

    ledger = ledger_module if ledger_module is not None else _load_ledger_module()
    if ledger is None:
        # Fire-open: a chase watcher that goes silent is the dangerous failure.
        sys.stderr.write("[pre_run] escalation ledger unavailable; waking\n")
        return _blind_wake("ledger_unavailable_fail_open")
    if ledger_events is None:
        ledger_events = ledger.read_ledger()

    items: list[VerificationItem] = []
    raw_input_blob: bytes = b""
    for source in sources:
        pulled = list(source.pull_open_verifications())
        items.extend(pulled)
        raw_input_blob += json.dumps([_item_to_dict(i) for i in pulled], sort_keys=True).encode("utf-8")

    role_snapshot_hashes = _snapshot_hashes_for(items, ledger, ledger_events, snapshot_hash_fn)

    decision = decide(
        items,
        config,
        ledger,
        ledger_events,
        raw_inputs_for_digest=raw_input_blob,
        today=today,
        refire_days=refire_days,
        role_snapshot_hashes=role_snapshot_hashes,
    )
    if decision.wake:
        if decision.plans:
            # WS-RENDER: render the internal escalations into the out-of-turn
            # dispatch envelope. {} on any failure; the wake proceeds
            # undecorated — SKILL.md's plans-without-dispatch_expected branch
            # has the turn send the failure note, and the terminal-state
            # reconcile + the ledger's re-fire property observe the miss.
            envelope_mod = _load_sibling_module("dispatch_envelope.py", "cvt_dispatch_envelope")
            if envelope_mod is not None:
                envelope_meta = envelope_mod.build_and_write(
                    plans=decision.plans,
                    items=items,
                    ledger=ledger,
                    ledger_events=ledger_events,
                    today=today,
                    refire_days=refire_days,
                    ceiling=config.escalate_after_attempts,
                )
                if envelope_meta:
                    decision = replace(
                        decision,
                        extra_metadata={**decision.extra_metadata, **envelope_meta},
                    )
        # The row goes in BEFORE the wake line, and cannot stop it (#2253).
        _wake = _load_sibling_module("blind_wake.py", "cvt_blind_wake")
        if _wake is not None:
            await _wake.try_write_emitted_wake(
                audit_writer_factory,
                decision,
                skill_name=SKILL_NAME,
                next_scheduled_at=_next_scheduled_at(now),
            )
        return _emit_wake(decision)

    writer = audit_writer_factory()
    if writer is None:
        # Mirror-don't-gate: no writer = no heartbeat trail = always wake.
        return _blind_wake("no_audit_writer_fail_open")
    try:
        await writer.write_suppressed_wake(
            skill_name=SKILL_NAME,
            pre_run_inputs=decision.pre_run_inputs_digest,
            decision_basis=decision.decision_basis,
            next_scheduled_at=_next_scheduled_at(now),
            extra_metadata=decision.extra_metadata,
        )
    except Exception:  # noqa: BLE001 — any audit failure → wake (dead-man's-switch)
        return _blind_wake("suppress_heartbeat_failed_fail_open")
    return _emit_suppress()


# ---------------------------------------------------------------------------
# Production wiring. The Smokeball pull runs in the connector's own venv via
# subprocess (smokeball_connector is not importable from the Hermes venv this
# script runs in); the SUPPRESSED_WAKE heartbeat goes through the broker's
# uid-gated `suppressed_wake_append` verb. Every unknown stays conservative:
# pull failure, unrecognized envelope, heartbeat failure — all wake.
# ---------------------------------------------------------------------------

_CONNECTOR_PYTHON_DEFAULT = "/opt/connectors/smokeball/.venv/bin/python"
_PULL_TIMEOUT_SECONDS = 60

# The verification tracking tasks the skill maintains carry a stable marker in
# their subject so the pull can subset them out of the open-task list. The
# skill authors this subject (SKILL.md step 4 / How it works). The exact firm
# convention is connect-verified; the marker match is deliberately broad.
_VERIFICATION_SUBJECT_MARKER = "verification"

# Runs inside the connector venv. Pull failure is REPORTED, not swallowed — a
# partial view must wake, never suppress.
_PULL_SNIPPET = """\
import json
import os

from smokeball_connector.client import build_client_from_env
from smokeball_connector.matter_ref import attach_matter_numbers

client = build_client_from_env()
out = {}
try:
    out["tasks"] = client.get("/tasks", IsCompleted=False, Limit=500)
except Exception as exc:
    out["tasksError"] = str(exc)[:300]
try:
    budget = int(os.environ.get("SMD_MATTER_LOOKUP_BUDGET", "100"))
    envelope = out.get("tasks")
    items = []
    if isinstance(envelope, dict) and isinstance(envelope.get("value"), list):
        items = envelope["value"]
    elif isinstance(envelope, list):
        items = envelope
    out["matterNumberCounts"] = attach_matter_numbers(client, items, budget=budget)
except Exception as exc:
    out["matterRefError"] = str(exc)[:300]
print(json.dumps(out, default=str))
"""

_TASK_DATE_KEYS = ("dueDate", "DueDate", "due_date")
_TASK_SUBJECT_KEYS = ("subject", "Subject", "name", "Name", "title", "Title", "description")
_MATTER_ID_KEYS = ("matterId", "MatterId", "matter_id")
_SOURCE_ID_KEYS = ("id", "Id", "taskId", "TaskId")


def _extract_items(payload) -> list | None:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "value", "results", "tasks", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return None


def _matter_id_of(item: dict) -> str:
    # The live Smokeball /tasks payload carries the matter as a NESTED link
    # object ({"matter": {"id": ..., "href": ...}}), not a flat matterId —
    # found by the WP-D probe when the flat-key miss put "unknown-matter" into
    # every item identity and forked the ledger join (ss #1915).
    matter = item.get("matter") or item.get("Matter")
    if isinstance(matter, dict):
        nested = matter.get("id") or matter.get("Id")
        if isinstance(nested, str) and nested:
            return nested
    for key in _MATTER_ID_KEYS:
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return "unknown-matter"


def _source_id_of(item: dict) -> str | None:
    for key in _SOURCE_ID_KEYS:
        value = item.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value)
    return None


# Rehearsal/self-test artifacts carry "[SMD-PROBE <stamp>]" at the start of
# the subject (after the connector's "[Operator]" provenance stamp) — ss #2403:
# a probe task outlived its test and became THIS skill's live tracking anchor
# (task 28745d01, 2026-08-14). Probe rows are never tracked verifications.
# Position-anchored: a real task quoting the marker mid-subject is not hidden.
_PROBE_MARK = "[SMD-PROBE"
_PROVENANCE_MARK = "[Operator]"


def _is_probe_subject(subject: str) -> bool:
    text = subject.lstrip()
    if text.upper().startswith(_PROVENANCE_MARK.upper()):
        text = text[len(_PROVENANCE_MARK) :].lstrip()
    return text.upper().startswith(_PROBE_MARK.upper())


def _is_verification_task(subject: str) -> bool:
    if _is_probe_subject(subject):
        return False
    return _VERIFICATION_SUBJECT_MARKER in subject.lower()


def _matter_number_of(item: dict) -> tuple[str | None, str | None]:
    """``(matter_number, absent_reason)`` — exactly one is non-None. The
    number is the connector's code-projected ``matterNumber`` (ss #2390);
    never derived here. Same reading as the escalator's."""
    number = item.get("matterNumber")
    if isinstance(number, str) and number:
        return number, None
    absent = item.get("matterNumberAbsent")
    if isinstance(absent, str) and absent:
        return None, absent
    if _matter_id_of(item) == "unknown-matter":
        return None, "no_matter_link"
    return None, "lookup_failed"


def parse_pull(raw: dict, *, today: date) -> tuple[list[VerificationItem], str | None]:
    """Pure parse of the connector pull. Returns (items, problem).

    A non-None problem means the view is partial or unrecognizable and the caller
    MUST wake. Only tasks carrying the verification marker in their subject are
    tracked verifications; other open tasks are ignored (they belong to other
    skills). A verification task with no due date seeds its first chase to today
    (it is already open and overdue for a first touch)."""
    if raw.get("tasksError"):
        return [], f"pull error: tasksError={raw['tasksError']}"
    tasks = _extract_items(raw.get("tasks"))
    if tasks is None:
        return [], "unrecognized pull envelope"
    items: list[VerificationItem] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        subject = _parse().first_str(task, _TASK_SUBJECT_KEYS)
        if not _is_verification_task(subject):
            continue
        due = _parse().first_date(task, _TASK_DATE_KEYS) or today
        number, number_absent = _matter_number_of(task)
        items.append(
            VerificationItem(
                matter_id=_matter_id_of(task),
                task_id=_source_id_of(task),
                next_chase_due=due,
                # authored_date stays None: identity is the stable task_id, never
                # the moving tracking-task due date (see VerificationItem).
                authored_date=None,
                label="client-verification",
                matter_number=number,
                matter_number_absent=number_absent,
            )
        )
    return items, None


class SmokeballSubprocessSource:
    """VerificationSource over a connector-venv subprocess pull."""

    def __init__(self, today: date) -> None:
        self._today = today

    def pull_open_verifications(self) -> Sequence[VerificationItem]:
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
        items, problem = parse_pull(raw, today=self._today)
        if problem:
            raise RuntimeError(problem)
        return items


# The SUPPRESSED/EMITTED_WAKE heartbeat writer (BrokerSuppressedWakeWriter)
# lives in the sibling ``broker_writer.py``, loaded like the vendored ledger —
# this skill deliberately DROPPED the vendored `_writer_factory` /
# `BrokerSuppressedWakeWriter` copies (pre-run-shared-symbols contract
# regenerated; the sibling split is what got pre_run.py back under the
# module-size ratchet). A missing sibling degrades to None, which run_once
# already treats as "no writer wired" — every tick wakes
# (no_audit_writer_fail_open) and the missing heartbeat trail is what the
# watcher-health view alarms on. Loud, never silent.
def _sibling_writer_factory():
    writer_mod = _load_sibling_module("broker_writer.py", "cvt_broker_writer")
    if writer_mod is None:
        return None  # run_once treats None as "no writer wired" → wake
    return writer_mod.writer_factory()


def _blind_wake(basis: str) -> int:
    """Wake with no decision, but never without a trace and never bare.

    The two guarantees, and the 2026-09-02 tick that forced them, live in the
    sibling ``blind_wake.py``. A missing sibling degrades to the pre-fix
    behaviour, which is why that failure is loud rather than swallowed.
    """
    mod = _load_sibling_module("blind_wake.py", "cvt_blind_wake")
    if mod is None:
        sys.stderr.write("[pre_run] blind_wake sibling missing; waking bare\n")
        return _emit_wake(basis=basis)
    return _emit_wake(
        basis=basis,
        extra=mod.wake_blind(
            basis,
            load_sibling=_load_sibling_module,
            writer_factory=_sibling_writer_factory,
            skill_name=SKILL_NAME,
            next_scheduled_at=_next_scheduled_at(datetime.now(timezone.utc)),
        ),
    )


def main() -> int:
    customer_slug = os.environ.get("CUSTOMER_SLUG")
    if not customer_slug:
        sys.stderr.write("[pre_run] CUSTOMER_SLUG unset; falling back to wake\n")
        return _blind_wake("customer_slug_unset_fail_open")
    config, refire_days = load_chase_config()
    today = datetime.now(timezone.utc).date()
    source = SmokeballSubprocessSource(today)
    try:
        return asyncio.run(
            run_once(
                [source],
                _sibling_writer_factory,
                today=today,
                config=config,
                refire_days=refire_days,
            )
        )
    except Exception as exc:  # noqa: BLE001 — any wiring failure → wake
        sys.stderr.write(f"[pre_run] chase pre_run failed ({exc}); waking\n")
        return _blind_wake("pre_run_crashed_fail_open")


if __name__ == "__main__":
    sys.exit(main())
