#!/usr/bin/env python3
"""lien-ledger-tracker pre-run gate — the settlement-closeout obligation ledger (ss #2455).

Runs BEFORE the Hermes cron daemon wakes the agent. Decides whether any provider
still owed money on a settled-undistributed matter needs chasing today, and hands
the woken turn the STATE its outreach must copy — so "chase 3, last chased July 2"
is broker-validated ledger fact, never model recall.

What changed, and why this skill graduated
------------------------------------------
The firm's #1 job is the settled-but-undistributed backlog: cases where the money
is in trust and cannot be released because provider bills are un-negotiated. Two
facts reshaped this skill for it.

  1. SCOPE. This routine was authored around statutory lienholders. At the first
     firm, 76% of the outstanding balance sits on obligations with NO lien
     asserted — ordinary unpaid provider invoices. The firm's own practice-
     management tab models ``Providers[]`` and ``OtherLiensAndBalances[]`` side
     by side, so the ledger here is every obligation blocking disbursement, not
     only the ones that happen to carry a lien.
  2. SOURCE. The obligations are structured and readable
     (``GET /matters/{id}/layouts`` → ``PersonalInjurySettlementDetailsItem``),
     one item PER PLAINTIFF. They no longer have to be figures a person types in.

The chase unit is the PROVIDER, not the obligation
--------------------------------------------------
Exposure concentrates hard: at the first firm a single payer appears on 22
separate matters. Chasing per obligation would send that payer 22 messages in one
pass, which is both the wrong commercial move (one negotiation clears 22 files)
and the kind of machine-noise that ends a pilot. So the ledger carries two item
families:

  * OBLIGATION items, keyed ``(matter_id, "sct:<provider entity id>")`` — what is
    owed, and whether it has cleared. One per matter per provider.
  * PROVIDER-CHASE items, keyed ``("", "__sct_provider__<normalized name>")`` —
    the outreach cadence and attempt count for ONE consolidated contact covering
    every matter that provider appears on.

A provider group plans at most one chase per run, and the plan carries every
matter in the group so the turn writes one message naming all of them.

Identity is read off the record, never composed
-----------------------------------------------
``item_key`` hashes (matter_id, source_id, authored_date) and IGNORES label
(ss #2151), so the provider must ride ``source_id``. It rides
``Providers[n]/Provider/MatterEntityId`` — the practice-management system's own
per-provider identifier, observed populated on every provider row. That matters
beyond tidiness: a name-derived key would change the day the firm corrects a
spelling, orphaning that obligation's whole negotiation history at the moment the
correction succeeded. Where the id is genuinely absent the fallback is the raw
display name, casefold and whitespace-collapsed only — no punctuation stripping,
no suffix dropping, no fuzzy matching — and the absence is reported, never
silently papered over.

Cross-matter GROUPING is a different question from identity, and it is
deliberately looser: a misspelled provider is a different contact record with a
different entity id, so grouping falls back to the normalized display name. That
grouping is a proposal the register shows with both raw spellings; it never
silently merges two ledger identities.

Wake / suppress decision
------------------------
  (a) CHASE DUE — a provider group holds at least one unresolved obligation with
      a positive balance, and either has never been chased or its last chase is
      at least ``chase_cadence_days`` old. The plan carries ``attempt``,
      ``last_chased`` and the matters in the group. No attempt ceiling: the
      chase runs on cadence until the balance clears; escalation is stall-based.
  (d) HELD — the ledger carries an open per-MATTER hold: a turn found the matter
      unsafe to chase. A held matter's obligations never join a chase group, and
      the hold re-surfaces on the re-fire window until a turn resolves it.
  (f) STALLED — a matter whose obligations have all gone quiet longer than
      ``stall_days``. Matter-level on purpose: a quiet matter is one thing to
      tell a person, and a per-obligation stall sentinel would roughly double a
      ledger every skill on the seat parses whole on every run.

Seat-level:

  (c) CONFIG MISSING — ``trigger_status`` or ``chase_cadence_days`` unauthored.
      Both are firm facts with no pack default: which status means
      settled-undistributed, and how often this firm wants a payer contacted.
      Fail-closed, surfaced once, re-surfaced every ``escalation.refire_days``
      until authored (#1899). ``stall_days`` is NOT in this class — unauthored,
      it degrades: no stall flags, and tracking and chasing continue.
  (g) NO OBLIGATIONS READ — the trigger-status cohort is non-empty but every
      deep read came back with no provider detail. At a real firm that is most
      likely a layout or permissions mismatch, and with zero obligations every
      wake would otherwise suppress forever. Surfaced on its own sentinel.

Everything else → a ``SUPPRESSED_WAKE`` heartbeat through the broker, then
``{"wakeAgent": false}``.

Fail direction
--------------
- Ledger unreadable → FIRE-OPEN (wake).
- Pull failure, unrecognized envelope, or a full page (the cohort may be
  truncated) → FIRE-OPEN (wake).
- Deep-read budget exhausted → NOT a failure: plan what was read, and report the
  shortfall as coverage so the turn can state it. A partial view that says it is
  partial beats a raised run.
- Config unreadable / unauthored → fail-CLOSED surface (c).

``decide()`` is pure (no I/O) and unit-tested with fake inputs. ``run_once()``
wires the real source, the broker heartbeat and stdout.

Exit codes:
    0 — decision emitted (wake or suppress)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
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


_SKILL_DIRNAME = "lien-ledger-tracker"
_H = _load_skill_helpers()

SKILL_NAME = "lien-ledger-tracker"

# Seat-level sentinels, namespaced __sct_* (settlement closeout). item_key
# ignores label and derive_state joins on the key alone, so a sentinel source_id
# shared with another skill would share one ledger identity: acking their config
# surface would silence ours.
_CONFIG_SENTINEL_SOURCE_ID = "__sct_config__"
_CONFIG_SENTINEL_LABEL = "sct-config-missing"
_NO_OBLIGATIONS_SENTINEL_SOURCE_ID = "__sct_no_obligations__"
_NO_OBLIGATIONS_SENTINEL_LABEL = "sct-no-obligations-read"

# Per-matter hold: a turn found this matter unsafe to chase. Matter-level so it
# survives an obligation being cleared, re-added, or re-keyed.
HOLD_SOURCE_ID = "__sct_hold__"
_HOLD_LABEL = "sct-chase-hold"

# Matter-level stall. Kept OFF the obligation and provider keys on purpose:
# attempts counts every raise, so a stall fired on a chase key would inflate the
# "chase N" numerator the outreach copies.
STALL_SOURCE_PREFIX = "__sct_stall__"

# The periodic register. Its own sentinel so "when did the firm last get the
# standing picture" is a fact rather than an inference from chase activity.
REGISTER_SOURCE_ID = "__sct_register__"
_REGISTER_LABEL = "sct-register"

# Provider-chase family. The group's cadence and attempt count live here.
PROVIDER_SOURCE_PREFIX = "__sct_provider__"
_PROVIDER_LABEL = "sct-provider-chase"

# Obligation family: one per (matter, provider).
_OBLIGATION_SOURCE_PREFIX = "sct:"
_OBLIGATION_LABEL = "sct-obligation"


# ---------------------------------------------------------------------------
# Obligation source protocol.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Obligation:
    """One provider's outstanding position on one settled-undistributed matter.

    ``provider_key`` is the practice-management entity id where one exists; the
    display name, casefold+collapsed, only where it does not. ``id_source`` says
    which, so a run can report how much of its own identity is second-best.
    ``plaintiff_index`` is carried as an ATTRIBUTE, never part of the key:
    removing a plaintiff renumbers the survivors, and identity must not move
    because a sibling was deleted.
    """

    matter_id: str
    provider_key: str
    provider_display: str
    balance: float
    plaintiff_index: int = 0
    lien_asserted: bool = False
    id_source: str = "entity_id"  # entity_id | display_name

    @property
    def outstanding(self) -> bool:
        return self.balance > 0


@dataclass(frozen=True)
class CohortRow:
    """One matter at the trigger status, from the bulk pass.

    The matter record carries NO last-activity field (only opened/closed dates,
    vfy_01M0E061XAJRNB2194NC6KM0R1), so the register ranks the cohort by AGE and
    says plainly that quiet time is not available at this altitude. It is not
    silently substituted with the opened date wearing another label.
    """

    matter_id: str
    number: str
    title: str
    clients: str
    responsible: str
    opened: str


class ObligationSource(Protocol):
    def pull_obligations(self) -> "ObligationPull": ...


@dataclass(frozen=True)
class ObligationPull:
    """What one pull observed.

    ``cohort_size`` is every matter at the trigger status; ``deep_read`` is how
    many of them we actually opened this run. The gap is the coverage the turn
    must state — a register that does not say how much it looked at reads as
    complete when it is not.
    """

    obligations: tuple[Obligation, ...]
    cohort_size: int
    deep_read: int
    unreadable: int = 0
    name_keyed: int = 0  # obligations that fell back to a name-derived key
    cohort: tuple[CohortRow, ...] = ()


# ---------------------------------------------------------------------------
# Config. trigger_status and chase_cadence_days are CLIENT-COMMITMENT facts with
# no pack default (ADR 0035). stall_days degrades instead: an unauthored stall
# threshold withholds stall flags and says so, and never stops the tracking.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CloseoutConfig:
    trigger_status: str | None = None
    chase_cadence_days: int | None = None
    stall_days: int | None = None
    register_days: int | None = None

    @property
    def authored(self) -> bool:
        return bool(self.trigger_status) and self.chase_cadence_days is not None

    @property
    def missing(self) -> list[str]:
        gaps = []
        if not self.trigger_status:
            gaps.append("trigger_status")
        if self.chase_cadence_days is None:
            gaps.append("chase_cadence_days")
        return gaps


_DEFAULT_REFIRE_DAYS = 3
_STATUS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 _-]{0,63}$")


_pos_int_or_none = _H.pos_int_or_none


_pos_int = _H.pos_int


def _status_or_none(value):
    """A status crosses into a subprocess environment, so it is shape-checked
    here rather than trusted for being authored."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if _STATUS_RE.match(text) else None


def _find_skill_settings(data) -> dict:
    return _H.find_skill_settings(data, SKILL_NAME)


def load_closeout_config(customer_yaml_path: str | None = None) -> tuple[CloseoutConfig, int]:
    """Read (CloseoutConfig, refire_days) from the trusted-volume customer.yaml.
    Missing file / PyYAML / parse failure → unauthored (fail-closed)."""
    path = customer_yaml_path or os.environ.get("SMD_CUSTOMER_YAML_PATH")
    if not path:
        return CloseoutConfig(), _DEFAULT_REFIRE_DAYS
    try:
        import yaml
    except ImportError:
        return CloseoutConfig(), _DEFAULT_REFIRE_DAYS
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError):
        return CloseoutConfig(), _DEFAULT_REFIRE_DAYS
    settings = _find_skill_settings(data)
    config = CloseoutConfig(
        trigger_status=_status_or_none(settings.get("trigger_status")),
        chase_cadence_days=_pos_int_or_none(settings.get("chase_cadence_days")),
        stall_days=_pos_int_or_none(settings.get("stall_days")),
        register_days=_pos_int_or_none(settings.get("register_days")),
    )
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
            spec = importlib.util.spec_from_file_location("escalation_ledger_vendored_llt", module_path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module
    return None


# ---------------------------------------------------------------------------
# Grouping. Deterministic code, never model judgment (#2390).
# ---------------------------------------------------------------------------

_GROUP_STRIP_RE = re.compile(r"[^a-z0-9 ]+")
_GROUP_NOISE_RE = re.compile(r"\b(inc|llc|llp|pc|corp|co|the|of|and|dba)\b")


def normalize_provider_name(name: str) -> str:
    """Collapse a display name to a grouping token.

    Used ONLY for cross-matter grouping and for the fallback identity when the
    record carries no entity id. Never used to merge two entity ids: a firm that
    keeps two contact records is telling us something, and the register proposes
    the merge with both raw spellings rather than deciding it here.
    """
    text = _GROUP_STRIP_RE.sub(" ", (name or "").casefold())
    text = _GROUP_NOISE_RE.sub(" ", text)
    return " ".join(text.split())


def fallback_provider_key(name: str) -> str:
    """Identity of last resort: casefold and whitespace-collapse only. No
    punctuation stripping and no suffix dropping, so the key stays a faithful
    function of what the record says."""
    return " ".join((name or "").casefold().split())


# ---------------------------------------------------------------------------
# Decision engine — pure, no I/O.
# ---------------------------------------------------------------------------

ACTION_CHASE_PROVIDER = "chase_provider"
ACTION_SURFACE_CONFIG = "surface_config_missing"
ACTION_SURFACE_NO_OBLIGATIONS = "surface_no_obligations_read"
ACTION_SURFACE_HOLD = "surface_hold"
ACTION_SURFACE_STALL = "surface_stall"
ACTION_EMIT_REGISTER = "emit_register"
ACTION_SUPPRESS = "suppress"


@dataclass(frozen=True)
class ChasePlan:
    """One consolidated provider outreach, or one surface.

    ``matters`` carries every matter the outreach must name, so the turn writes
    one message for the group instead of one per obligation.
    """

    item_key: str
    action: str
    attempt: int
    provider_display: str = ""
    provider_group: str = ""
    matter_id: str = ""
    matters: tuple[str, ...] = ()
    outstanding_total: float = 0.0
    last_chased: str | None = None


@dataclass(frozen=True)
class WakeDecision:
    wake: bool
    decision_basis: str
    pre_run_inputs_digest: bytes
    plans: tuple[ChasePlan, ...] = ()
    extra_metadata: dict = field(default_factory=dict)
    register: dict | None = None


_hold_active = _H.hold_active


def _chase_due(state, today: date, *, cadence_days: int) -> bool:
    if state is None or state.last_raised_date is None:
        return True
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
    """Fire-once + re-fire-window on a stable seat sentinel (#1899)."""
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
        plans=(ChasePlan(item_key=key, action=action, attempt=ledger.next_attempt(state)),),
        extra_metadata=extra,
    )


def obligation_key(ledger, obligation: Obligation) -> str:
    return ledger.item_key(
        obligation.matter_id,
        _OBLIGATION_SOURCE_PREFIX + obligation.provider_key,
        _OBLIGATION_LABEL,
        None,
    )


def provider_key(ledger, group: str) -> str:
    return ledger.item_key("", PROVIDER_SOURCE_PREFIX + group, _PROVIDER_LABEL, "")


REGISTER_TOP_N = 20


def _age_days(opened: str, today: date) -> int | None:
    try:
        return (today - date.fromisoformat(opened[:10])).days
    except (TypeError, ValueError):
        return None


def build_register(pull: ObligationPull, config: CloseoutConfig, today: date) -> dict:
    """The standing picture, bounded and honest about its own edges.

    Three disciplines, all of them the difference between a register a firm can
    act on and one that quietly overstates:

    * Blank is never zero. A matter whose settlement detail has not been read
      carries ``outstanding: None`` and ``detail: "not read"``, never 0.00.
    * Every ranking names its rule on the artifact, so nobody has to infer why a
      row is at the top.
    * What we could not see is listed, with the reason. A register that omits
      its own gaps reads as complete.
    """
    by_matter: dict[str, list[Obligation]] = {}
    for obligation in pull.obligations:
        by_matter.setdefault(obligation.matter_id, []).append(obligation)

    rows = []
    for row in pull.cohort:
        members = by_matter.get(row.matter_id)
        outstanding = round(sum(m.balance for m in members), 2) if members is not None else None
        rows.append(
            {
                "matter": row.number,
                "client": row.clients,
                "responsible": row.responsible,
                "opened": row.opened,
                "age_days": _age_days(row.opened, today),
                "obligations": len(members) if members is not None else None,
                "outstanding": outstanding,
                "detail": "read" if members is not None else "not read",
            }
        )

    read_rows = [r for r in rows if r["detail"] == "read"]
    groups: dict[str, dict] = {}
    for obligation in pull.obligations:
        if not obligation.outstanding:
            continue
        group = groups.setdefault(
            normalize_provider_name(obligation.provider_display),
            {"provider": obligation.provider_display, "matters": set(), "outstanding": 0.0},
        )
        group["matters"].add(obligation.matter_id)
        group["outstanding"] = round(group["outstanding"] + obligation.balance, 2)

    providers = sorted(
        (
            {
                "provider": g["provider"],
                "matters": len(g["matters"]),
                "outstanding": g["outstanding"],
            }
            for g in groups.values()
        ),
        key=lambda g: -g["outstanding"],
    )

    unavailable = [
        "quiet time: the matter record carries no last-activity field, so the cohort is ranked by age instead",
        "client trust ledger balance: not held in the practice-management system",
    ]
    if config.stall_days is None:
        unavailable.append("stall flags: no stall threshold is authored for this firm")
    if config.register_days is None:
        unavailable.append(
            "a periodic cadence for this register: none is authored, so it appears "
            "only when the Operator wakes for other work"
        )

    return {
        "as_of": today.isoformat(),
        "ranking_rule": (
            "oldest opened first across the whole set; recorded exposure shown only "
            "where the settlement detail has been read this cycle"
        ),
        "coverage": {
            "matters_at_status": pull.cohort_size,
            "detail_read": len(read_rows),
            "detail_not_read": max(0, pull.cohort_size - len(read_rows)),
            "unreadable": pull.unreadable,
            "obligations": len(pull.obligations),
            "name_keyed_obligations": pull.name_keyed,
        },
        "recorded_outstanding_total": round(sum(r["outstanding"] or 0.0 for r in read_rows), 2),
        "oldest": sorted(rows, key=lambda r: (r["age_days"] is None, -(r["age_days"] or 0)))[:REGISTER_TOP_N],
        "largest_recorded_exposure": sorted(read_rows, key=lambda r: -(r["outstanding"] or 0.0))[:REGISTER_TOP_N],
        "providers_by_exposure": providers[:REGISTER_TOP_N],
        "unavailable": unavailable,
    }


def decide(
    pull: ObligationPull,
    config: CloseoutConfig,
    ledger,
    events: Sequence[dict],
    *,
    raw_inputs_for_digest: bytes,
    today: date,
    refire_days: int,
) -> WakeDecision:
    """Pure decision: does any provider need chasing today?"""
    states = ledger.derive_state(events)

    # (c) Seat-level: the firm facts are unauthored → fail-closed surface.
    if not config.authored:
        return _seat_sentinel_decision(
            ledger,
            states,
            source_id=_CONFIG_SENTINEL_SOURCE_ID,
            label=_CONFIG_SENTINEL_LABEL,
            action=ACTION_SURFACE_CONFIG,
            basis_surface="closeout_config_unauthored_surface",
            basis_quiet="closeout_config_unauthored_within_refire_window",
            today=today,
            refire_days=refire_days,
            raw_inputs_for_digest=raw_inputs_for_digest,
            extra={"missing": config.missing, "cohort_size": pull.cohort_size},
        )

    # (g) Seat-level: a cohort exists but nothing in it carried obligations.
    if not pull.obligations and pull.cohort_size > 0 and pull.deep_read > 0:
        return _seat_sentinel_decision(
            ledger,
            states,
            source_id=_NO_OBLIGATIONS_SENTINEL_SOURCE_ID,
            label=_NO_OBLIGATIONS_SENTINEL_LABEL,
            action=ACTION_SURFACE_NO_OBLIGATIONS,
            basis_surface="no_obligations_read_surface",
            basis_quiet="no_obligations_read_within_refire_window",
            today=today,
            refire_days=refire_days,
            raw_inputs_for_digest=raw_inputs_for_digest,
            extra={
                "cohort_size": pull.cohort_size,
                "deep_read": pull.deep_read,
                "unreadable": pull.unreadable,
            },
        )

    plans: list[ChasePlan] = []
    held_matters: set[str] = set()

    # (d) HELD — surface once per held matter, and fence its obligations out of
    # every chase group below.
    for matter_id in sorted({o.matter_id for o in pull.obligations}):
        hold_key = ledger.item_key(matter_id, HOLD_SOURCE_ID, _HOLD_LABEL, None)
        hold_state = states.get(hold_key)
        if not _hold_active(hold_state):
            continue
        held_matters.add(matter_id)
        if not hold_state.handed_off and ledger.should_fire(
            hold_state, today, refire_days=refire_days, ack_snooze_days=refire_days
        ):
            plans.append(
                ChasePlan(
                    item_key=hold_key,
                    action=ACTION_SURFACE_HOLD,
                    attempt=ledger.next_attempt(hold_state),
                    matter_id=matter_id,
                    matters=(matter_id,),
                )
            )

    # (a) CHASE — group the live obligations by provider and plan ONE outreach.
    groups: dict[str, list[Obligation]] = {}
    for obligation in pull.obligations:
        if obligation.matter_id in held_matters or not obligation.outstanding:
            continue
        if states.get(obligation_key(ledger, obligation)) is not None:
            state = states[obligation_key(ledger, obligation)]
            if state.resolved or state.handed_off:
                continue
        groups.setdefault(normalize_provider_name(obligation.provider_display), []).append(obligation)

    cadence_days = int(config.chase_cadence_days or 0)
    for group in sorted(groups):
        members = groups[group]
        key = provider_key(ledger, group)
        state = states.get(key)
        if state is not None and (state.resolved or state.handed_off):
            continue
        if not _chase_due(state, today, cadence_days=cadence_days):
            continue
        last = None
        if state is not None and state.last_raised_date is not None:
            last = state.last_raised_date.isoformat()
        plans.append(
            ChasePlan(
                item_key=key,
                action=ACTION_CHASE_PROVIDER,
                attempt=ledger.next_attempt(state),
                provider_display=members[0].provider_display,
                provider_group=group,
                matters=tuple(sorted({m.matter_id for m in members})),
                outstanding_total=round(sum(m.balance for m in members), 2),
                last_chased=last,
            )
        )

    # The periodic register. Its own sentinel and its own cadence, because "the
    # firm last saw the standing picture on X" must not be inferred from whether
    # a chase happened to fall due. Unauthored cadence degrades: no periodic
    # wake, and the register still rides along whenever the turn wakes.
    if config.register_days is not None:
        register_key = ledger.item_key("", REGISTER_SOURCE_ID, _REGISTER_LABEL, "")
        register_state = states.get(register_key)
        if _chase_due(register_state, today, cadence_days=config.register_days):
            plans.append(
                ChasePlan(
                    item_key=register_key,
                    action=ACTION_EMIT_REGISTER,
                    attempt=ledger.next_attempt(register_state),
                    last_chased=(
                        register_state.last_raised_date.isoformat()
                        if register_state is not None and register_state.last_raised_date is not None
                        else None
                    ),
                )
            )

    coverage = {
        "cohort_size": pull.cohort_size,
        "deep_read": pull.deep_read,
        "unreadable": pull.unreadable,
        "obligations": len(pull.obligations),
        "name_keyed_obligations": pull.name_keyed,
        "stall_days_authored": config.stall_days is not None,
    }
    if plans:
        # Count each action for itself. Deriving one count by subtracting another
        # from the total was wrong the moment a third action existed: a
        # register-only wake reported itself as a held matter, which is a metric
        # asserting something that did not happen, in a row the audit keeps.
        chases = [p for p in plans if p.action == ACTION_CHASE_PROVIDER]
        holds = [p for p in plans if p.action == ACTION_SURFACE_HOLD]
        registers = [p for p in plans if p.action == ACTION_EMIT_REGISTER]
        # The basis names what is actually due, most actionable first. A wake
        # that says "chase due" when only the periodic register came around
        # misdescribes the run to anyone reading the heartbeat afterwards.
        if chases:
            basis = "closeout_chase_due"
        elif holds:
            basis = "closeout_hold_surface_due"
        else:
            basis = "closeout_register_due"
        return WakeDecision(
            wake=True,
            decision_basis=basis,
            pre_run_inputs_digest=raw_inputs_for_digest,
            plans=tuple(plans),
            register=build_register(pull, config, today),
            extra_metadata={
                **coverage,
                "provider_chases_due": len(chases),
                "hold_surface_due": len(holds),
                "register_due": len(registers),
                "matters_in_chases": sorted({m for p in chases for m in p.matters}),
            },
        )
    return WakeDecision(
        wake=False,
        decision_basis="no_closeout_chase_due",
        pre_run_inputs_digest=raw_inputs_for_digest,
        extra_metadata=coverage,
    )


# ---------------------------------------------------------------------------
# Runtime entrypoint.
# ---------------------------------------------------------------------------


def _next_scheduled_at(now: datetime, schedule_hours: int = 168) -> str:
    return (now + timedelta(hours=schedule_hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _plan_to_dict(plan: ChasePlan) -> dict:
    return {
        "item_key": plan.item_key,
        "action": plan.action,
        "attempt": plan.attempt,
        "provider_display": plan.provider_display,
        "provider_group": plan.provider_group,
        "matter_id": plan.matter_id,
        "matters": list(plan.matters),
        "outstanding_total": plan.outstanding_total,
        "last_chased": plan.last_chased,
    }


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

_HANDOFF_SKILL = "lien-ledger-tracker"
_HANDOFF_STARTED_AT = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


_handoff_values = _H.handoff_values


_is_iso_day = _H.is_iso_day


def _write_pre_run_handoff(payload: dict) -> None:
    _H.write_pre_run_handoff(payload, _HANDOFF_SKILL, _HANDOFF_STARTED_AT)


def _emit_wake(decision: "WakeDecision | None" = None, *, basis: str | None = None) -> int:
    """Print the wake line WITH its plans (ss #2226): the plans are the woken
    turn's work list, and a chase plan carries the state its message copies.
    Fail-open callers pass ``basis`` so the turn knows it woke blind."""
    payload: dict = {"wakeAgent": True}
    resolved_basis = decision.decision_basis if decision is not None else basis
    if resolved_basis:
        payload["decision_basis"] = resolved_basis
    if decision is not None:
        if decision.plans:
            payload["plans"] = [_plan_to_dict(p) for p in decision.plans]
        if decision.extra_metadata:
            payload["coverage"] = decision.extra_metadata
        if decision.register is not None:
            payload["register"] = decision.register
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


def _obligation_to_dict(obligation: Obligation) -> dict:
    return {
        "matter_id": obligation.matter_id,
        "provider_key": obligation.provider_key,
        "provider_display": obligation.provider_display,
        "balance": obligation.balance,
        "plaintiff_index": obligation.plaintiff_index,
        "lien_asserted": obligation.lien_asserted,
        "id_source": obligation.id_source,
    }


async def run_once(
    sources: Sequence[ObligationSource],
    audit_writer_factory,
    *,
    today: date | None = None,
    now: datetime | None = None,
    config: CloseoutConfig | None = None,
    refire_days: int | None = None,
    ledger_module=None,
    ledger_events: Sequence[dict] | None = None,
) -> int:
    """Driver. Fail-open on ledger loss; a config-read failure is the unauthored
    path and is handled inside ``decide``."""
    now = now or datetime.now(timezone.utc)
    today = today or now.date()
    if config is None or refire_days is None:
        loaded_config, loaded_refire = load_closeout_config()
        config = config or loaded_config
        refire_days = refire_days if refire_days is not None else loaded_refire

    ledger = ledger_module if ledger_module is not None else _load_ledger_module()
    if ledger is None:
        sys.stderr.write("[pre_run] escalation ledger unavailable; waking\n")
        return _emit_wake(basis="ledger_unavailable_fail_open")
    if ledger_events is None:
        ledger_events = ledger.read_ledger()

    obligations: list[Obligation] = []
    cohort_rows: list[CohortRow] = []
    cohort = deep = unreadable = name_keyed = 0
    raw_input_blob: bytes = b""
    for source in sources:
        pulled = source.pull_obligations()
        obligations.extend(pulled.obligations)
        cohort_rows.extend(pulled.cohort)
        cohort += pulled.cohort_size
        deep += pulled.deep_read
        unreadable += pulled.unreadable
        name_keyed += pulled.name_keyed
        raw_input_blob += json.dumps([_obligation_to_dict(o) for o in pulled.obligations], sort_keys=True).encode(
            "utf-8"
        )

    decision = decide(
        ObligationPull(
            obligations=tuple(obligations),
            cohort_size=cohort,
            deep_read=deep,
            unreadable=unreadable,
            name_keyed=name_keyed,
            cohort=tuple(cohort_rows),
        ),
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
_PULL_TIMEOUT_SECONDS = 240  # sized to the deep-read fan-out, not the bulk list
_HEARTBEAT_TIMEOUT_SECONDS = 10
_PULL_PAGE_LIMIT = 500

# How many never-before-read matters one run may open. This is an integrity
# control on our own API budget, not a client entitlement, so a pack default is
# doctrine-consistent (ADR 0062). It bounds wall clock too: the deep pass is N
# sequential round trips inside one subprocess timeout.
DEEP_READS_PER_RUN = 12

_SETTLEMENT_DESIGN_MARKER = "SettlementDetailsItem"
_GUID_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")

# argv stays a module constant; the two run-varying inputs cross as environment
# so nothing request-shaped reaches the command line. Both are shape-checked
# before they are set.
_PULL_SNIPPET = """\
import json, os

from smokeball_connector.client import build_client_from_env

client = build_client_from_env()
status = os.environ.get("SMD_SCT_STATUS", "")
wanted = [m for m in os.environ.get("SMD_SCT_MATTERS", "").split(",") if m]
budget = int(os.environ.get("SMD_SCT_BUDGET", "0") or 0)

out = {"status": status}
try:
    out["cohort"] = client.get("/matters", Status=status, Limit=500)
except Exception as exc:
    out["cohortError"] = str(exc)[:300]
    print(json.dumps(out, default=str))
    raise SystemExit(0)


def listing(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "value", "results", "data"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


rows = listing(out["cohort"])
known = {str(r.get("id")) for r in rows if isinstance(r, dict)}
targets = [m for m in wanted if m in known]
seen = set(targets)
# Bootstrap and top-up with matters never read before, most recently opened
# first: that is where the structured detail actually lives, so the budget is
# not spent producing empty rows.
extra = sorted(
    (r for r in rows if isinstance(r, dict) and str(r.get("id")) not in seen),
    key=lambda r: str(r.get("openedDate") or ""),
    reverse=True,
)
for row in extra:
    if len(targets) >= len(wanted) + budget:
        break
    targets.append(str(row.get("id")))

layouts = {}
errors = {}
for matter_id in targets:
    try:
        layouts[matter_id] = client.get("/matters/" + matter_id + "/layouts")
    except Exception as exc:
        errors[matter_id] = str(exc)[:200]
out["layouts"] = layouts
out["layoutErrors"] = errors
print(json.dumps(out, default=str))
"""


def _listing(payload) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "value", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _names(value) -> str:
    """Join a person/entity collection into a display string, taking whatever
    name field the record actually carries. Never composed from parts we did not
    read; an unnamed entry contributes nothing rather than a placeholder."""
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list):
        return ""
    out = []
    for entry in value:
        if isinstance(entry, str) and entry.strip():
            out.append(entry.strip())
        elif isinstance(entry, dict):
            for key in ("displayName", "DisplayName", "name", "Name", "fullName"):
                candidate = entry.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    out.append(candidate.strip())
                    break
    return ", ".join(out)


def _as_float(value) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


_PROVIDER_INDEX_RE = re.compile(r"^Providers\[(\d+)\]/(.+)$")


def parse_settlement_item(item: dict) -> list[dict]:
    """Explode one per-plaintiff settlement item into provider dicts.

    Deliberately per item: flattening the items of a multi-plaintiff matter into
    one map is what collapsed provider detail in the one-off generator, because
    ``Providers[0]`` means a different provider under each plaintiff.
    """
    by_index: dict[int, dict] = {}
    for value in item.get("values") or []:
        if not isinstance(value, dict):
            continue
        match = _PROVIDER_INDEX_RE.match(str(value.get("key") or ""))
        if not match:
            continue
        by_index.setdefault(int(match.group(1)), {})[match.group(2)] = value.get("value")
    return [by_index[i] for i in sorted(by_index)]


def parse_pull(raw: dict) -> tuple[ObligationPull, str | None]:
    """Pure parse of the connector pull. Returns (pull, problem).

    A non-None problem means the view is unusable and the caller MUST wake. A
    partial deep read is NOT a problem: it is reported as coverage, because a
    partial view that says so is worth more than a raised run.
    """
    if raw.get("cohortError"):
        return ObligationPull((), 0, 0), f"pull error: cohortError={raw['cohortError']}"
    rows = _listing(raw.get("cohort"))
    if raw.get("cohort") is not None and not isinstance(raw.get("cohort"), (list, dict)):
        return ObligationPull((), 0, 0), "unrecognized cohort envelope"
    if len(rows) >= _PULL_PAGE_LIMIT:
        return (
            ObligationPull((), len(rows), 0),
            f"cohort returned a full page ({len(rows)} rows); the set may be truncated",
        )

    layouts = raw.get("layouts")
    if not isinstance(layouts, dict):
        return ObligationPull((), len(rows), 0), "unrecognized layouts envelope"
    errors = raw.get("layoutErrors") if isinstance(raw.get("layoutErrors"), dict) else {}

    cohort: list[CohortRow] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cohort.append(
            CohortRow(
                matter_id=str(row.get("id") or ""),
                number=str(row.get("number") or ""),
                title=str(row.get("title") or row.get("description") or ""),
                clients=_names(row.get("clients")),
                responsible=_names(row.get("personResponsible")),
                opened=str(row.get("openedDate") or "")[:10],
            )
        )

    obligations: list[Obligation] = []
    name_keyed = 0
    for matter_id, payload in layouts.items():
        for item in _listing(payload):
            if not isinstance(item, dict):
                continue
            if _SETTLEMENT_DESIGN_MARKER not in str(item.get("layoutDesignId") or ""):
                continue
            plaintiff_index = item.get("parentIndex")
            for provider in parse_settlement_item(item):
                display = str(provider.get("Provider/DisplayName") or "").strip()
                if not display:
                    continue
                entity_id = str(provider.get("Provider/MatterEntityId") or "").strip()
                if entity_id:
                    key, source = entity_id, "entity_id"
                else:
                    key, source = fallback_provider_key(display), "display_name"
                    name_keyed += 1
                obligations.append(
                    Obligation(
                        matter_id=str(matter_id),
                        provider_key=key,
                        provider_display=display,
                        balance=_as_float(provider.get("InvoiceBalance")),
                        plaintiff_index=(plaintiff_index if isinstance(plaintiff_index, int) else 0),
                        lien_asserted=str(provider.get("LienAsserted") or "").lower() == "true",
                        id_source=source,
                    )
                )
    return (
        ObligationPull(
            obligations=tuple(obligations),
            cohort_size=len(rows),
            deep_read=len(layouts),
            unreadable=len(errors),
            name_keyed=name_keyed,
            cohort=tuple(cohort),
        ),
        None,
    )


def matters_with_open_obligations(events: Sequence[dict]) -> list[str]:
    """Matter ids this skill still has unresolved obligations on.

    Read off the ledger's own ``matter_id`` field rather than recovered from the
    key, which is a hash and cannot be reversed. Resolved and handed-off items
    drop out so a cleared matter stops consuming deep-read budget.
    """
    open_matters: dict[str, bool] = {}
    for event in events:
        if not isinstance(event, dict) or event.get("skill") != SKILL_NAME:
            continue
        matter_id = event.get("matter_id")
        if not isinstance(matter_id, str) or not matter_id:
            continue
        if event.get("event") in ("resolved", "handed_off"):
            open_matters[matter_id] = False
        elif event.get("event") in ("fired", "chased"):
            open_matters.setdefault(matter_id, True)
            if open_matters[matter_id] is False:
                open_matters[matter_id] = True
    return sorted(m for m, still_open in open_matters.items() if still_open and _GUID_RE.match(m))


class SmokeballSubprocessSource:
    """ObligationSource over a connector-venv subprocess pull."""

    def __init__(self, status: str, deep_matters: Sequence[str], budget: int) -> None:
        self._status = status
        self._deep_matters = [m for m in deep_matters if _GUID_RE.match(m)]
        self._budget = max(0, int(budget))

    def pull_obligations(self) -> ObligationPull:
        connector_python = os.environ.get("SMD_CONNECTOR_VENV_PYTHON", _CONNECTOR_PYTHON_DEFAULT)
        env = {
            **os.environ,
            "SMD_SCT_STATUS": self._status,
            "SMD_SCT_MATTERS": ",".join(self._deep_matters),
            "SMD_SCT_BUDGET": str(self._budget),
        }
        result = subprocess.run(  # raises on timeout → caller wakes
            # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args — argv[0] is the module-constant connector-venv interpreter, overridable only via SMD_CONNECTOR_VENV_PYTHON from the Machine's own boot env (same trust domain; the test seam). The snippet is a module constant, and the two run-varying inputs cross as shape-checked environment values, never argv.
            [connector_python, "-c", _PULL_SNIPPET],
            capture_output=True,
            text=True,
            timeout=_PULL_TIMEOUT_SECONDS,
            env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(f"smokeball pull exit {result.returncode}: {(result.stderr or '').strip()[:500]}")
        raw = json.loads((result.stdout or "").strip().splitlines()[-1])
        pull, problem = parse_pull(raw)
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
    config, refire_days = load_closeout_config()
    today = datetime.now(timezone.utc).date()
    if not config.authored:
        # No status to query on. Run the pure path so the unauthored surface
        # still fires on its window rather than the run failing on an empty query.
        ledger = _load_ledger_module()
        events = ledger.read_ledger() if ledger is not None else []
        source_less = ObligationPull((), 0, 0)
        if ledger is None:
            return _emit_wake(basis="ledger_unavailable_fail_open")
        decision = decide(
            source_less,
            config,
            ledger,
            events,
            raw_inputs_for_digest=b"",
            today=today,
            refire_days=refire_days,
        )
        return _emit_wake(decision) if decision.wake else _emit_suppress()
    try:
        ledger = _load_ledger_module()
        events = ledger.read_ledger() if ledger is not None else []
        source = SmokeballSubprocessSource(
            config.trigger_status or "",
            matters_with_open_obligations(events),
            DEEP_READS_PER_RUN,
        )
        return asyncio.run(
            run_once(
                [source],
                _writer_factory,
                today=today,
                config=config,
                refire_days=refire_days,
                ledger_module=ledger,
                ledger_events=events,
            )
        )
    except Exception as exc:  # noqa: BLE001 — any wiring failure → wake
        sys.stderr.write(f"[pre_run] lien-ledger pre_run failed ({exc}); waking\n")
        return _emit_wake(basis="pre_run_crashed_fail_open")


if __name__ == "__main__":
    sys.exit(main())
