#!/usr/bin/env python3
"""List routine runs that ended in no observable terminal state (ss#2388).

WHY THIS EXISTS. Every triggered routine run is supposed to end in exactly one
observable terminal state: delivered, refused-with-the-reason-delivered, or
escalated. Nothing checked that, so silence was indistinguishable from success.

  ss#2367 -- the Operator drafted a demand letter, filed it correctly through the
  checked seam, had its reply HELD by the fabrication gate, and the turn ended 23
  seconds later. No redraft, no minimal note, no escalation. From the firm's
  side: asked for a letter, got silence, while a complete letter sat on the
  matter.

  ss#2136 -- `hermes cron run` printed "Ran now: succeeded." for two jobs and
  produced zero audit rows on a seat whose audit write path was demonstrably
  healthy that hour.

Neither incident is visible one row at a time. Each individual row is honest;
what is missing is the row nobody wrote. This reconciler reads absence.

WHAT IT READS. The contract lives in operator/contracts/terminal-states.yaml and
is LOADED here, not restated: which events open an obligation, which close it,
which are explicitly non-terminal, and which terminal states each routine class
accepts. Routine classes for scheduled runs are derived from
operator/contracts/output-classes.yaml `skill_bindings.<skill>.outbound`, the
list CI already keeps honest, rather than from a second list that would drift.

CONSOLE-SIDE, like its sibling. Audit rows come through the ADR 0043
runtime-read seam (operator/bin/lib/seam_pull.py), the same path
reconcile-sends.py uses. A seat cannot be the auditor of its own silence: the
failure mode being measured is the seat not writing a row, and a seat-side check
that depends on the seat writing rows cannot see it.

CLAIMED RUNS. The ss#2136 shape has no trigger row to find, because the trigger
was asserted by a surface outside the ledger. Those triggers are supplied with
`--claims`, a JSON list of {slug, routine, trigger_ts, claimed_by}. A claim with
zero audit rows in its window is the loudest finding this control produces.

THREE-VALUE EXIT, and it differs from reconcile-sends.py on purpose:
    0  clean      -- every evaluated run reached a terminal state
    1  finding    -- at least one run ended in silence; the workflow files it
    3  HOLD       -- nothing could be evaluated (no credentials, or every seat
                     failed to read). reconcile-sends.py exits 0 here, which
                     means a rotated secret reads as a green control forever.
                     A missing credential is a configuration defect a person
                     must fix, not a transient blip, so it is loud.
A PARTIAL hold (some seats read, some did not) is reported line by line and does
not by itself change the exit code: pinging on one seat's network blip is how
controls get muted.

Usage:
    infisical run --env=prod --path=/ss -- python3 operator/bin/reconcile-outcomes.py
    ... --slug pilot-smokeball --days 3 --json
    ... --rows extract.json --slug pilot-smokeball        # offline extract
    ... --claims cron-claims.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

import seam_pull
from sender_class import (
    CUSTOMERS_DIR,  # noqa: F401 - re-exported: test_reconcile_outcomes reads it as a module attribute
    SENDER_FIRM,  # noqa: F401 - re-exported: test_reconcile_outcomes reads it as a module attribute
    SENDER_PROBE,  # noqa: F401 - re-exported: test_reconcile_outcomes reads it as a module attribute
    SENDER_SMD,  # noqa: F401 - re-exported: test_reconcile_outcomes reads it as a module attribute
    SENDER_UNKNOWN,  # noqa: F401 - re-exported: test_reconcile_outcomes reads it as a module attribute
    SENDER_UNRECORDED,  # noqa: F401 - re-exported: test_reconcile_outcomes reads it as a module attribute
    SIBLING_KEY_WINDOW_SECONDS,  # noqa: F401 - re-exported: test_reconcile_outcomes reads it as a module attribute
    classify_senders,
    load_roster,
    sender_breakdown as _sender_breakdown,
    sender_label as _sender_label,
)

_CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"
TERMINAL_STATES_PATH = _CONTRACTS / "terminal-states.yaml"
OUTPUT_CLASSES_PATH = _CONTRACTS / "output-classes.yaml"

EXIT_CLEAN = 0
EXIT_FINDING = 1
EXIT_HOLD = 3

# Findings that name a specific incident shape, so a report reads as the thing
# that happened rather than as a generic gap.
SHAPE_HELD_WITHOUT_NOTICE = "held_without_notice"
SHAPE_NO_RUN_EVENTS = "no_run_events"
SHAPE_ENDED_WITHOUT_OUTCOME = "ended_without_outcome"
SHAPE_UNCLASSIFIED = "unclassified"


class ReconcileError(RuntimeError):
    """Transport, credential, or contract failure. Holds; never a finding."""


# ---------------------------------------------------------------------------
# contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Contract:
    """The loaded terminal-state contract plus the derived skill -> class map."""

    trigger_events: dict[str, str]  # action_type -> trigger kind
    terminal_events: dict[str, str]  # action_type -> terminal state name
    terminal_tool_calls: dict[str, dict]  # state -> {action_class, outcome}
    classes: dict[str, dict]  # routine class -> {accepts, window_seconds}
    skill_outbound: dict[str, str]  # skill -> derived | none

    def window_for(self, routine_class: str) -> int:
        entry = self.classes.get(routine_class) or {}
        return int(entry.get("window_seconds") or 1800)

    def accepts(self, routine_class: str, state: str) -> bool:
        entry = self.classes.get(routine_class) or {}
        return state in (entry.get("accepts") or [])


def load_contract(
    terminal_path: Path = TERMINAL_STATES_PATH,
    output_classes_path: Path = OUTPUT_CLASSES_PATH,
) -> Contract:
    spec = yaml.safe_load(terminal_path.read_text(encoding="utf-8"))
    bindings = yaml.safe_load(output_classes_path.read_text(encoding="utf-8"))

    trigger_events: dict[str, str] = {}
    for kind, entry in (spec.get("triggers") or {}).items():
        for event in entry.get("events") or []:
            trigger_events[event] = kind

    terminal_events: dict[str, str] = {}
    terminal_tool_calls: dict[str, dict] = {}
    for state, entry in (spec.get("terminal_states") or {}).items():
        for event in entry.get("events") or []:
            terminal_events[event] = state
        if entry.get("tool_call"):
            terminal_tool_calls[state] = entry["tool_call"]

    skill_outbound = {
        skill: str((entry or {}).get("outbound"))
        for skill, entry in (bindings.get("skill_bindings") or {}).items()
    }
    if not trigger_events or not terminal_events or not skill_outbound:
        raise ReconcileError("terminal-state contract loaded empty; refusing to evaluate")

    return Contract(
        trigger_events=trigger_events,
        terminal_events=terminal_events,
        terminal_tool_calls=terminal_tool_calls,
        classes=spec.get("routine_classes") or {},
        skill_outbound=skill_outbound,
    )


# ---------------------------------------------------------------------------
# rows
# ---------------------------------------------------------------------------


def parse_ts(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def metadata_of(row: dict) -> dict:
    raw = row.get("metadata")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


@dataclass
class Obligation:
    """One triggered run that owes an observable ending."""

    slug: str
    opened_at: datetime
    opened_by: str  # action_type, or the claiming surface for a claimed run
    trigger_kind: str  # inbound | scheduled | hold | claimed
    routine_class: str
    skill_name: Optional[str] = None
    matter_ref: Optional[str] = None
    row_id: Optional[str] = None
    closed_by: Optional[str] = None  # terminal state name
    closed_at: Optional[datetime] = None
    shape: Optional[str] = None  # finding shape when unclosed
    pending: bool = False
    rows_in_window: int = 0
    claimed_by: Optional[str] = None
    #: The latest hold inside this run. A hold RESTARTS the obligation: a reply
    #: delivered before the hold cannot answer for the output the hold stopped.
    last_hold_at: Optional[datetime] = None
    #: sha256 of the canonical address that triggered an inbound run (ss#2497),
    #: and where it came from: "row" (this trigger carried it) or "sibling"
    #: (adopted from the INBOUND_RECEIVED this WEBHOOK_ROUTED preceded).
    sender_key: Optional[str] = None
    sender_key_via: Optional[str] = None
    #: One of the SENDER_* classes for inbound runs; None for every other
    #: trigger kind, because a cron wake or a bare hold has no sender.
    sender_class: Optional[str] = None

    @property
    def effective_open(self) -> datetime:
        return max(self.opened_at, self.last_hold_at or self.opened_at)

    @property
    def is_finding(self) -> bool:
        return self.shape in (
            SHAPE_HELD_WITHOUT_NOTICE,
            SHAPE_NO_RUN_EVENTS,
            SHAPE_ENDED_WITHOUT_OUTCOME,
        )

    @property
    def is_hold(self) -> bool:
        return self.shape == SHAPE_UNCLASSIFIED


def _routine_class(contract: Contract, trigger_kind: str, skill_name: Optional[str]) -> str:
    """Reply lanes are a lane, not a skill: an inbound message or a held output
    owes a person an answer no matter which skill produced it. A scheduled run's
    class is DERIVED from the skill's declared `outbound` binding."""
    if trigger_kind in ("inbound", "hold"):
        return "reply_to_person"
    outbound = contract.skill_outbound.get(skill_name or "")
    if outbound == "derived":
        return "scheduled_outbound"
    if outbound == "none":
        return "scheduled_internal"
    return SHAPE_UNCLASSIFIED


def _terminal_state_of(contract: Contract, row: dict) -> Optional[str]:
    action = row.get("action_type")
    if action in contract.terminal_events:
        return contract.terminal_events[action]
    if action != "TOOL_CALL_COMPLETED":
        return None
    meta = metadata_of(row)
    for state, matcher in contract.terminal_tool_calls.items():
        if meta.get("action_class") in (matcher.get("action_class") or []) and meta.get(
            "outcome"
        ) in (matcher.get("outcome") or []):
            return state
    return None


def _obligation_from_row(contract: Contract, slug: str, row: dict, kind: str) -> Obligation:
    skill = row.get("skill_name")
    key = metadata_of(row).get("sender_key") if kind == "inbound" else None
    return Obligation(
        slug=slug,
        opened_at=parse_ts(row["ts"]),
        opened_by=str(row.get("action_type")),
        trigger_kind=kind,
        routine_class=_routine_class(contract, kind, skill),
        skill_name=skill,
        matter_ref=row.get("matter_ref"),
        row_id=row.get("id"),
        sender_key=key if isinstance(key, str) and key else None,
        sender_key_via="row" if isinstance(key, str) and key else None,
    )


def _open_obligations(contract: Contract, slug: str, rows: list[dict]) -> list[Obligation]:
    """One obligation per RUN, not per event.

    A hold inside a run that a trigger already opened does not open a second
    run: it MOVES that run's obligation forward to the moment of the hold. Two
    obligations for one run would make a correct recovery (inbound, hold,
    redraft, reply sent) look like a finding, and a control that fires on the
    authored happy path is muted within a week.
    """
    triggers: list[Obligation] = []
    holds: list[tuple[datetime, dict]] = []
    for row in rows:
        kind = contract.trigger_events.get(str(row.get("action_type")))
        if kind is None:
            continue
        if kind == "hold":
            holds.append((parse_ts(row["ts"]), row))
            continue
        triggers.append(_obligation_from_row(contract, slug, row, kind))

    for held_at, row in holds:
        enclosing = _enclosing_run(contract, triggers, held_at)
        if enclosing is None:
            # The ss#2367 shape as CAPTURED: the extract holds the hold and the
            # turn end, with no trigger row before it. The hold is the run.
            triggers.append(_obligation_from_row(contract, slug, row, "hold"))
            continue
        enclosing.last_hold_at = max(held_at, enclosing.last_hold_at or held_at)
        # A held output is owed to a person, whatever the routine's normal lane
        # is. An internal artifact cannot answer for the thing the gate stopped,
        # so the run is graded on the reply lane from the hold onward.
        enclosing.routine_class = "reply_to_person"
    return triggers


def _enclosing_run(
    contract: Contract, triggers: list[Obligation], held_at: datetime
) -> Optional[Obligation]:
    """The already-open run this hold belongs to, latest one wins."""
    candidates = [
        o
        for o in triggers
        if o.opened_at <= held_at
        and held_at <= o.opened_at + timedelta(seconds=contract.window_for(o.routine_class))
    ]
    return max(candidates, key=lambda o: o.opened_at) if candidates else None


def _claimed_obligations(contract: Contract, claims: Iterable[dict]) -> list[Obligation]:
    out: list[Obligation] = []
    for claim in claims:
        skill = claim.get("routine") or claim.get("skill_name")
        out.append(
            Obligation(
                slug=str(claim.get("slug") or ""),
                opened_at=parse_ts(claim["trigger_ts"]),
                opened_by=str(claim.get("claimed_by") or "external claim"),
                trigger_kind="claimed",
                routine_class=_routine_class(contract, "scheduled", skill),
                skill_name=skill,
                claimed_by=str(claim.get("claimed_by") or "external claim"),
            )
        )
    return out


def resolve(
    contract: Contract,
    obligations: list[Obligation],
    rows: list[dict],
    *,
    now: datetime,
) -> list[Obligation]:
    """Close every obligation that has a terminal row after it inside its window.

    One terminal row closes ONE obligation (contract `matching.consume_terminal
    _rows`), the same consumption discipline the unaudited-send reconciler uses:
    otherwise a single delivered reply launders every silent run around it.
    """
    ordered = sorted(rows, key=lambda r: str(r.get("ts") or ""))
    stamped = [(parse_ts(r["ts"]), r, _terminal_state_of(contract, r)) for r in ordered]
    consumed: set[int] = set()

    for obligation in sorted(obligations, key=lambda o: o.opened_at):
        if obligation.routine_class == SHAPE_UNCLASSIFIED:
            obligation.shape = SHAPE_UNCLASSIFIED
            continue
        window = timedelta(seconds=contract.window_for(obligation.routine_class))
        deadline = obligation.effective_open + window
        in_window = [
            (index, ts, row, state)
            for index, (ts, row, state) in enumerate(stamped)
            if obligation.effective_open < ts <= deadline
        ]
        # Counted from the TRIGGER, not from the hold: "did anything at all
        # happen after this run was triggered" is the ss#2136 question.
        obligation.rows_in_window = sum(
            1 for ts, _row, _state in stamped if obligation.opened_at < ts <= deadline
        )

        for index, ts, _row, state in in_window:
            if state is None or index in consumed:
                continue
            if not contract.accepts(obligation.routine_class, state):
                continue
            consumed.add(index)
            obligation.closed_by = state
            obligation.closed_at = ts
            break

        if obligation.closed_by:
            continue
        if now < deadline:
            obligation.pending = True
            continue
        obligation.shape = _finding_shape(obligation)
    return obligations


def _finding_shape(obligation: Obligation) -> str:
    if obligation.rows_in_window == 0 and obligation.trigger_kind == "claimed":
        return SHAPE_NO_RUN_EVENTS
    if obligation.trigger_kind == "hold" or obligation.last_hold_at is not None:
        return SHAPE_HELD_WITHOUT_NOTICE
    return SHAPE_ENDED_WITHOUT_OUTCOME


def analyze(
    contract: Contract,
    slug: str,
    rows: list[dict],
    *,
    now: datetime,
    claims: Optional[list[dict]] = None,
    roster: Optional[dict[str, str]] = None,
) -> list[Obligation]:
    obligations = _open_obligations(contract, slug, rows)
    obligations += _claimed_obligations(contract, claims or [])
    classify_senders(obligations, load_roster(slug) if roster is None else roster)
    return resolve(contract, obligations, rows, now=now)


# ---------------------------------------------------------------------------
# seat reports
# ---------------------------------------------------------------------------


@dataclass
class SeatReport:
    slug: str
    obligations: list[Obligation] = field(default_factory=list)
    rows_read: int = 0
    held: Optional[str] = None
    #: The seat's hostname does not resolve, so no machine exists to read. Named
    #: in the report (#2366) but NOT a hold: holding on it warns daily forever
    #: about a slug nobody provisioned.
    absent: Optional[str] = None  # could not evaluate

    @property
    def findings(self) -> list[Obligation]:
        return [o for o in self.obligations if o.is_finding]

    @property
    def unclassified(self) -> list[Obligation]:
        return [o for o in self.obligations if o.is_hold]

    @property
    def pending(self) -> list[Obligation]:
        return [o for o in self.obligations if o.pending]


def _name_does_not_resolve(exc: BaseException) -> bool:
    """True when the failure is DNS refusing to resolve the seat's hostname.

    That is the signature of a machine that was never stood up, or has been
    destroyed -- there is no host to talk to, as opposed to a host that will not
    answer. `seat_slugs()` enumerates AUTHORED directories, and pilot-law has
    been authored and unprovisioned since 2026-06-05
    (`audit-chain-watch.py:29`), so without this every run holds on it forever.

    Derived from the probe, deliberately, rather than read from the seat
    descriptor: that file refuses to carry a lifecycle field because "a state
    field is a claim an agent can write, and a claim an agent can write is one
    that rots". DNS is not a claim.

    Walks the cause chain because urllib wraps the gaierror in a URLError.
    """
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, socket.gaierror):
            return True
        reason = getattr(exc, "reason", None)
        exc = reason if isinstance(reason, BaseException) else (exc.__cause__ or exc.__context__)
    return False


def reconcile_seat(
    contract: Contract,
    slug: str,
    *,
    now: datetime,
    since: Optional[datetime] = None,
    claims: Optional[list[dict]] = None,
    rows: Optional[list[dict]] = None,
    client_factory=seam_pull.seam_client_from_env,
) -> SeatReport:
    report = SeatReport(slug=slug)
    if rows is None:
        client = client_factory(slug)
        if client is None:
            report.held = f"no runtime-read client for {slug} (seam env incomplete)"
            return report
        try:
            rows = client.read_all("audit_export")
        except Exception as exc:  # noqa: BLE001 -- any transport failure HOLDS
            if _name_does_not_resolve(exc):
                report.absent = f"no machine resolves for {slug}: {exc}"
            else:
                report.held = f"audit_export read failed for {slug}: {exc}"
            return report
        if not rows:
            # A seat with no rows at all is unmeasurable, not clean. Kept
            # distinct from "read fine, found nothing to worry about".
            report.held = f"audit_export returned no rows for {slug}"
            return report

    usable = [r for r in rows if r.get("ts")]
    if since:
        usable = [r for r in usable if parse_ts(r["ts"]) >= since]
    report.rows_read = len(usable)
    report.obligations = analyze(contract, slug, usable, now=now, claims=claims)
    return report


#: Constant for this control, deliberately NOT derived from the findings.
#
# It is how a run locates the issue it already opened. The tempting design is a
# hash of the finding set, and it is wrong: this control's finding set GREW on
# five of six day-over-day transitions (350 -> 361 -> 376 -> 380 -> 388 -> 389
# -> 389, ss#2581), so a findings-derived marker would fail to match yesterday's
# issue on almost every run and file a second one -- exactly the defect. The
# sibling reconciler can key on its findings because its backlog is static; this
# one cannot.
SERIES_MARKER = "reconcile-series: terminal-state"


def finding_key(slug: str, obligation: Obligation) -> str:
    """Stable identity of one silent run, as a `|`-joined key.

    Mirrors reconcile-sends.py:753 in shape and in what it refuses to include.

    `row_id` is the audit ledger's own primary key and is the right identity,
    but `_claimed_obligations` never sets one, so claimed findings fall back to
    a timestamp keyspace behind a `ts:` marker -- the same trick the sibling
    uses "so the two key spaces can never collide".

    `slug` comes from the caller, never from `obligation.slug`: the claimed
    constructor takes ``slug=str(claim.get("slug") or "")``, which can be the
    empty string, and two seats' claimed findings would then share a key.

    Deliberately absent: `rows_in_window` and `rows_read` (recomputed against
    whatever window this run pulled), `pending` (depends on `now`), and
    `routine_class` (mutated in place at line 287 when a hold folds into an
    enclosing run). Any of them turns a stable identity into a daily-changing
    one, which is how an escalation ledger ends up disjoint from reality.
    `sender_class` is absent for the same reason: it depends on which roster
    the run loaded, and a roster edit must never make yesterday's finding look
    new.
    """
    if obligation.row_id:
        return f"{slug}|row:{obligation.row_id}"
    return f"{slug}|ts:{obligation.opened_at.isoformat()}|{obligation.shape}"


def finding_digest(reports: list["SeatReport"]) -> str:
    """Fingerprint of the whole finding set, for "did the number move".

    Its ONLY job is to decide whether a run has anything new to say on the
    issue it already opened. It never decides whether to report: this control
    reports every run, because its findings accrue daily.

    Sorted before hashing so seat read order cannot change it. Empty on a clean
    run, so a run with nothing to say is distinguishable from one that has not
    been compared.
    """
    keys = sorted(
        finding_key(report.slug, obligation)
        for report in reports
        for obligation in report.findings
    )
    if not keys:
        return ""
    return hashlib.sha256("\n".join(keys).encode()).hexdigest()[:16]


def render(reports: list[SeatReport]) -> str:
    lines: list[str] = []
    for report in reports:
        if report.absent:
            # SKIP, not HOLD: the denominator stays visible without the warning.
            lines.append(f"SKIP  {report.slug}: {report.absent}")
            continue
        if report.held:
            lines.append(f"HOLD  {report.slug}: {report.held}")
            continue
        verdict = "FIND" if report.findings else "ok  "
        lines.append(
            f"{verdict}  {report.slug} rows={report.rows_read} "
            f"runs={len(report.obligations)} closed="
            f"{sum(1 for o in report.obligations if o.closed_by)} "
            f"pending={len(report.pending)} "
            f"unclassified={len(report.unclassified)} "
            f"silent={len(report.findings)}{_sender_breakdown(report.findings)}"
        )
        for obligation in sorted(report.findings, key=lambda o: o.opened_at):
            lines.append(
                f"        {obligation.opened_at.isoformat()} {obligation.opened_by} "
                f"[{obligation.routine_class}] {obligation.shape} "
                f"skill={obligation.skill_name or '-'} "
                f"matter={obligation.matter_ref or '-'} "
                f"sender={_sender_label(obligation)} "
                f"rows_in_window={obligation.rows_in_window}"
            )
        for obligation in sorted(report.unclassified, key=lambda o: o.opened_at):
            lines.append(
                f"        {obligation.opened_at.isoformat()} {obligation.opened_by} "
                f"UNCLASSIFIED skill={obligation.skill_name or '-'} "
                "(no skill_bindings entry; cannot evaluate)"
            )
    findings = sum(len(r.findings) for r in reports)
    held = [r for r in reports if r.held]
    lines.append("")
    lines.append(
        f"{findings} run(s) with no terminal state, "
        f"{sum(len(r.unclassified) for r in reports)} unclassified, "
        f"{len(held)} seat(s) held, "
        f"{sum(1 for r in reports if r.absent)} seat(s) absent, "
        f"{len(reports)} seat(s) scanned"
    )
    # Column 0, and in render() only. The workflow finds the issue it already
    # opened with `sed -n 's/^reconcile-series: //p'`; finding detail lines are
    # indented eight spaces, so nothing above can be mistaken for it. --json
    # omits both, because a bare marker line inside a JSON payload breaks the
    # parse -- the sibling reconciler omits its own for the same reason.
    lines.append("")
    lines.append(SERIES_MARKER)
    digest = finding_digest(reports)
    if digest:
        lines.append(f"reconcile-findings: {digest}")
    return "\n".join(lines)


def as_json(reports: list[SeatReport]) -> str:
    return json.dumps(
        [
            {
                "slug": r.slug,
                "held": r.held,
                "rows_read": r.rows_read,
                "runs": [
                    {
                        "opened_at": o.opened_at.isoformat(),
                        "opened_by": o.opened_by,
                        "trigger_kind": o.trigger_kind,
                        "routine_class": o.routine_class,
                        "skill_name": o.skill_name,
                        "matter_ref": o.matter_ref,
                        "closed_by": o.closed_by,
                        "shape": o.shape,
                        "pending": o.pending,
                        "rows_in_window": o.rows_in_window,
                        "sender_class": o.sender_class,
                        "sender_key_via": o.sender_key_via,
                    }
                    for o in r.obligations
                ],
            }
            for r in reports
        ],
        indent=2,
    )


def _load_list(path: str, key: str) -> list[dict]:
    """Read a bare JSON list, or the `key` member of an envelope object.

    The envelope form is what the captured incident fixtures use
    (operator/bin/tests/fixtures/*.json): they carry their provenance in
    `_source` / `_captured_block` alongside the rows, so the file a test reads
    and the file a person runs the reconciler against are the same bytes.
    """
    parsed = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(parsed, dict):
        parsed = parsed.get(key) or []
    if not isinstance(parsed, list):
        raise ReconcileError(f"{path}: expected a JSON list or an object with a `{key}` list")
    return parsed


def customers_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "customers"


def seat_slugs() -> list[str]:
    # Authored dirs, not provisioned seats. audit-chain-watch.py intersects this
    # same enumeration against `fleet_status` (`partition_seats` /
    # `ConsoleD1.provisioned_slugs`, ss#2500) because an authored-but-never-
    # provisioned slug made every run of it red; this reconciler has no D1
    # credentials to do that with, and an unreachable seat only sets that seat's
    # `held` here rather than failing the run, so it is left as-is deliberately.
    return sorted(
        d.name
        for d in customers_dir().iterdir()
        if d.is_dir() and not d.name.startswith("_") and not d.name.startswith(".")
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", action="append", help="limit to these seats")
    parser.add_argument("--days", type=int, help="only consider runs in the last N days")
    parser.add_argument("--since", help="ISO date; only consider runs at/after this")
    parser.add_argument("--rows", help="offline audit extract (JSON list of rows)")
    parser.add_argument("--claims", help="JSON list of externally-claimed triggers")
    parser.add_argument("--now", help="evaluate as of this ISO time (default: now)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    try:
        contract = load_contract()
    except (OSError, yaml.YAMLError, ReconcileError) as exc:
        print(f"HOLD: {exc}", file=sys.stderr)
        return EXIT_HOLD

    now = parse_ts(args.now) if args.now else datetime.now(timezone.utc)
    since = None
    if args.days:
        since = now - timedelta(days=args.days)
    elif args.since:
        since = parse_ts(args.since if "T" in args.since else args.since + "T00:00:00Z")

    try:
        claims = _load_list(args.claims, "claims") if args.claims else []
        offline_rows = _load_list(args.rows, "rows") if args.rows else None
    except (OSError, ValueError, ReconcileError) as exc:
        print(f"HOLD: {exc}", file=sys.stderr)
        return EXIT_HOLD

    slugs = args.slug or seat_slugs()
    if offline_rows is not None and len(slugs) != 1:
        print("HOLD: --rows needs exactly one --slug (an extract belongs to one seat)",
              file=sys.stderr)
        return EXIT_HOLD

    if offline_rows is None and not os.environ.get("OPERATOR_RUNTIME_READ_SECRET"):
        # LOUD, not green. reconcile-sends.py exits 0 on a missing credential,
        # which makes a rotated secret indistinguishable from a clean fleet for
        # as long as nobody looks. A control that cannot run has not passed.
        print("HOLD: OPERATOR_RUNTIME_READ_SECRET unset (run under infisical)", file=sys.stderr)
        return EXIT_HOLD

    reports = [
        reconcile_seat(
            contract,
            slug,
            now=now,
            since=since,
            claims=[c for c in claims if c.get("slug") == slug],
            rows=offline_rows,
        )
        for slug in slugs
    ]

    print(as_json(reports) if args.json else render(reports))

    if any(r.findings for r in reports):
        return EXIT_FINDING
    if all(r.held for r in reports):
        # Nothing was evaluated anywhere. Clean and unmeasured must not print
        # the same exit code.
        return EXIT_HOLD
    return EXIT_CLEAN


if __name__ == "__main__":
    raise SystemExit(main())
