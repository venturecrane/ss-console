#!/usr/bin/env python3
"""Find authored cron slots that passed with NEITHER wake row (the dead-daemon shape).

WHY THIS EXISTS (outbound-quality track, 2026-08). Every ``pre_run_decides``
cron row is supposed to leave exactly one row per fire: EMITTED_WAKE (the gate
woke the agent) or SUPPRESSED_WAKE (the gate decided not to, with its basis).
reconcile-outcomes.py already grades what happens AFTER an EMITTED_WAKE --
"wake with no terminal outcome" is ITS finding (EMITTED_WAKE is a `scheduled`
trigger in terminal-states.yaml) -- but nothing anywhere read the slot where
NEITHER row exists: a dead cron daemon, materializer drift, a machine down
outside a boot window, a pre_run crash with the broker down. Fail-open wrote
nothing, and nothing was the one shape no reconciler could see.

WHAT IT READS. The authored slot grid comes from each seat's
``personas[].cron[]`` (parsed, never grepped; expansion in
``operator/bin/lib/cron_slots.py``, seat-local timezone from
``business_hours.timezone``). The wake rows come through the ADR 0043
runtime-read seam, same as the sibling reconcilers. The boot window comes from
the console's ``fleet_status`` (heartbeat - uptime): a slot missed while the
machine was rebooting or being reprovisioned is reported as suppressed, counted,
and never a finding -- that class also absorbs Fly host migrations and OOM
restarts, which are the same page-storm shape.

WHAT IT DOES NOT FILE. A ``silent`` wake (EMITTED_WAKE with no terminal
outcome) is ANNOTATED on the slot line and never double-filed:
reconcile-outcomes owns that finding, and two controls filing one condition is
the ss#2386 flood with extra steps. ``wake_policy: always`` rows
(connector-auth-check) write no wake rows by construction and render ``n/a``.

EXIT CONTRACT -- reconcile-outcomes' loud-hold vocabulary, on purpose (this
control's most likely failure is the same rotated-secret shape):
    0  clean    every expandable slot in the window is covered or suppressed
    1  finding  at least one slot passed with neither row
    3  HOLD     nothing could be evaluated (D1 unreachable, missing credential,
                or every provisioned seat failed to read)

Usage:
    infisical run --env=prod --path=/ss -- python3 operator/bin/reconcile-wakes.py
    ... --days 3 --json
    ... --rows extract.json --slug pilot-smokeball   # offline extract, no D1
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

import console_d1
import cron_slots
import seam_pull

EXIT_CLEAN = 0
EXIT_FINDING = 1
EXIT_HOLD = 3

#: Constant for this control, deliberately NOT derived from the findings --
#: the reconcile-outcomes lesson (its finding set grew on five of six
#: day-over-day transitions, so a findings-derived marker files duplicates).
#: Daily misses accrue here the same way.
SERIES_MARKER = "reconcile-series: cron-slot-watchdog"

#: How long after its authored instant a slot's wake row may land and still
#: cover it. Generous next to observed daemon skew (seconds), tight next to the
#: slot cadence (the closest authored pair is 10 minutes apart but they are
#: different skills, and matching is per-skill).
SLOT_TOLERANCE_S = 1800


def _bin_dir() -> Path:
    return Path(__file__).resolve().parent


def customers_dir() -> Path:
    return _bin_dir().parent / "customers"


def seat_slugs() -> list[str]:
    return sorted(
        d.name
        for d in customers_dir().iterdir()
        if d.is_dir()
        and not d.name.startswith("_")
        and not d.name.startswith(".")
        and (d / "customer.yaml").exists()
    )


def _load_outcomes_module():
    """reconcile-outcomes, spec-loaded (dashed filename): its analyze() is the
    single owner of obligation grading, imported rather than re-implemented so
    the two controls can never disagree about what `silent` means."""
    spec = importlib.util.spec_from_file_location(
        "reconcile_outcomes", _bin_dir() / "reconcile-outcomes.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["reconcile_outcomes"] = module
    spec.loader.exec_module(module)
    return module


@dataclass
class SeatWakeReport:
    slug: str
    verdicts: list = field(default_factory=list)  # cron_slots.SlotVerdict
    na_rows: list[str] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)
    pending: int = 0
    rows_read: int = 0
    empty_cron: bool = False
    held: Optional[str] = None
    skipped: Optional[str] = None

    @property
    def missing(self) -> list:
        return [v for v in self.verdicts if v.is_missing]

    @property
    def is_finding(self) -> bool:
        return self.held is None and bool(self.missing)


def load_customer_yaml(slug: str) -> Optional[dict]:
    path = customers_dir() / slug / "customer.yaml"
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return parsed if isinstance(parsed, dict) else None


def annotate_outcomes(report: SeatWakeReport, rows: list[dict], outcomes, now: datetime) -> None:
    """Per EMITTED slot, what became of the obligation it opened -- closed /
    pending / silent. An annotation, NEVER this control's finding (see module
    header). Any failure inside the outcomes module leaves slots unannotated
    rather than failing the watchdog: the annotation is a courtesy, the missing
    slot is the mission."""
    try:
        contract = outcomes.load_contract()
        obligations = outcomes.analyze(contract, report.slug, [r for r in rows if r.get("ts")], now=now)
    except Exception:  # noqa: BLE001 -- annotation is best-effort by design
        return
    by_row_id = {o.row_id: o for o in obligations if o.row_id}
    for verdict in report.verdicts:
        if verdict.covered_by != "EMITTED_WAKE" or not verdict.covered_row_id:
            continue
        obligation = by_row_id.get(verdict.covered_row_id)
        if obligation is None:
            continue
        if obligation.closed_by:
            verdict.outcome_disposition = "closed"
        elif obligation.pending:
            verdict.outcome_disposition = "pending"
        elif obligation.is_finding:
            verdict.outcome_disposition = "silent"


def reconcile_seat(
    slug: str,
    *,
    now: datetime,
    since: datetime,
    boot_info: Optional[dict],
    outcomes,
    rows: Optional[list[dict]] = None,
    client_factory=seam_pull.seam_client_from_env,
) -> SeatWakeReport:
    report = SeatWakeReport(slug=slug)
    config = load_customer_yaml(slug)
    if config is None:
        report.held = f"customer.yaml unreadable for {slug}"
        return report

    cron_rows = cron_slots.authored_cron_rows(config)
    if not cron_rows:
        report.empty_cron = True
        return report

    expandable: list[cron_slots.CronRow] = []
    for row in cron_rows:
        if row.wake_policy != "pre_run_decides":
            report.na_rows.append(f"{row.skill} (wake_policy: {row.wake_policy or 'unset'})")
            continue
        try:
            cron_slots.parse_schedule(row.schedule)
        except cron_slots.CronParseError as exc:
            # Refused loudly on the report; a guessed grid accuses the wrong
            # minutes. The schema validator owns schedule validity upstream.
            report.unparsed.append(f"{row.skill}: {exc}")
            continue
        expandable.append(row)

    if rows is None:
        client = client_factory(slug)
        if client is None:
            report.held = f"no runtime-read client for {slug} (seam env incomplete)"
            return report
        try:
            rows = client.read_all("audit_export")
        except Exception as exc:  # noqa: BLE001 -- any transport failure HOLDS
            report.held = f"audit_export read failed for {slug}: {exc}"
            return report
        if not rows:
            report.held = f"audit_export returned no rows for {slug}"
            return report
    report.rows_read = len(rows)

    tz = cron_slots.seat_timezone(config)
    slots = cron_slots.expand_slots(expandable, tz, since, now)
    # A slot whose tolerance window has not fully elapsed is pending, not
    # gradable: work may still be in flight, and a control that pages on
    # in-flight work is muted within a week.
    gradable = [s for s in slots if s.fires_at + timedelta(seconds=SLOT_TOLERANCE_S) <= now]
    report.pending = len(slots) - len(gradable)
    report.verdicts = cron_slots.match_slots(gradable, rows, tolerance_s=SLOT_TOLERANCE_S)
    if boot_info:
        window = cron_slots.boot_window(
            boot_info.get("last_heartbeat_ts"), boot_info.get("process_uptime_seconds")
        )
        cron_slots.apply_boot_suppression(report.verdicts, window)
    annotate_outcomes(report, rows, outcomes, now)
    return report


def finding_digest(reports: list[SeatWakeReport]) -> str:
    keys = sorted(
        f"{report.slug}|{verdict.slot.skill}|{verdict.slot.fires_at.isoformat()}"
        for report in reports
        for verdict in report.missing
        if report.held is None
    )
    if not keys:
        return ""
    return hashlib.sha256("\n".join(keys).encode()).hexdigest()[:16]


def render(reports: list[SeatWakeReport]) -> str:
    lines: list[str] = []
    for report in reports:
        if report.skipped:
            lines.append(f"SKIP  {report.slug}: {report.skipped}")
            continue
        if report.held:
            lines.append(f"HOLD  {report.slug}: {report.held}")
            continue
        if report.empty_cron:
            lines.append(f"n/a   {report.slug}: cron [] (no armed slots)")
            continue
        covered = sum(1 for v in report.verdicts if v.covered_by)
        suppressed = sum(1 for v in report.verdicts if v.suppressed_reason)
        silent = sum(1 for v in report.verdicts if v.outcome_disposition == "silent")
        verdict = "FIND" if report.is_finding else "ok  "
        lines.append(
            f"{verdict}  {report.slug} rows={report.rows_read} "
            f"slots={len(report.verdicts)} covered={covered} "
            f"boot-suppressed={suppressed} pending={report.pending} "
            f"missing={len(report.missing)} n/a-rows={len(report.na_rows)} "
            f"silent-annotated={silent}"
        )
        for verdict_row in report.missing:
            lines.append(
                f"        {verdict_row.slot.local} {verdict_row.slot.skill} MISSING "
                f"(neither EMITTED_WAKE nor SUPPRESSED_WAKE within {SLOT_TOLERANCE_S}s)"
            )
        for annotated in report.verdicts:
            if annotated.outcome_disposition == "silent":
                lines.append(
                    f"        {annotated.slot.local} {annotated.slot.skill} wake ok; "
                    "obligation silent (reconcile-outcomes owns that finding; annotation only)"
                )
        for entry in report.na_rows:
            lines.append(f"        n/a {entry} -- writes no wake rows by construction")
        for entry in report.unparsed:
            lines.append(f"        UNPARSED {entry} -- slot grid not expanded for this row")
    findings = sum(len(r.missing) for r in reports if r.held is None)
    held = [r for r in reports if r.held]
    lines.append("")
    lines.append(
        f"{findings} slot(s) passed with neither wake row, "
        f"{len(held)} seat(s) held, "
        f"{sum(1 for r in reports if r.skipped)} seat(s) skipped, "
        f"{sum(1 for r in reports if r.empty_cron)} seat(s) with no armed cron, "
        f"{len(reports)} seat(s) scanned"
    )
    # Column 0, render() only (the --json path omits it; a bare marker inside a
    # JSON payload breaks the parse). The workflow finds its rolling issue by
    # this constant.
    lines.append("")
    lines.append(SERIES_MARKER)
    digest = finding_digest(reports)
    if digest:
        lines.append(f"reconcile-findings: {digest}")
    return "\n".join(lines)


def as_json(reports: list[SeatWakeReport]) -> str:
    return json.dumps(
        [
            {
                "slug": r.slug,
                "held": r.held,
                "skipped": r.skipped,
                "empty_cron": r.empty_cron,
                "rows_read": r.rows_read,
                "pending": r.pending,
                "na_rows": r.na_rows,
                "unparsed": r.unparsed,
                "slots": [
                    {
                        "skill": v.slot.skill,
                        "fires_at": v.slot.fires_at.isoformat(),
                        "local": v.slot.local,
                        "covered_by": v.covered_by,
                        "suppressed_reason": v.suppressed_reason,
                        "outcome_disposition": v.outcome_disposition,
                        "missing": v.is_missing,
                    }
                    for v in r.verdicts
                ],
            }
            for r in reports
        ],
        indent=2,
    )


def parse_ts(value) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _load_rows(path: str) -> list[dict]:
    parsed = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(parsed, dict):
        parsed = parsed.get("rows") or []
    if not isinstance(parsed, list):
        raise ValueError(f"{path}: expected a JSON list or an object with a `rows` list")
    return parsed


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", action="append", help="limit to these seats")
    parser.add_argument("--days", type=int, default=3, help="window size (default 3)")
    parser.add_argument("--now", help="evaluate as of this ISO time (default: now)")
    parser.add_argument("--rows", help="offline audit extract (JSON; needs exactly one --slug)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    now = parse_ts(args.now) if args.now else datetime.now(timezone.utc)
    since = now - timedelta(days=args.days)
    outcomes = _load_outcomes_module()

    slugs = args.slug or seat_slugs()
    offline_rows: Optional[list[dict]] = None
    if args.rows:
        if len(slugs) != 1:
            print("HOLD: --rows needs exactly one --slug (an extract belongs to one seat)",
                  file=sys.stderr)
            return EXIT_HOLD
        try:
            offline_rows = _load_rows(args.rows)
        except (OSError, ValueError) as exc:
            print(f"HOLD: {exc}", file=sys.stderr)
            return EXIT_HOLD

    provisioned: Optional[set[str]] = None
    boot_rows: dict[str, dict] = {}
    if offline_rows is None:
        if not os.environ.get("OPERATOR_RUNTIME_READ_SECRET"):
            print("HOLD: OPERATOR_RUNTIME_READ_SECRET unset (run under infisical)", file=sys.stderr)
            return EXIT_HOLD
        # fleet_status is the authority for a seat EXISTING (the audit-chain-watch
        # partition posture): an authored-unprovisioned dir must SKIP, not hold
        # forever, and the boot window comes from the same read.
        try:
            d1 = console_d1.ConsoleD1(db=os.environ.get("D1_DATABASE", console_d1.DEFAULT_DB))
            boot_rows = d1.fleet_boot_rows()
            provisioned = set(boot_rows)
        except Exception as exc:  # noqa: BLE001 -- D1 unreachable => nothing evaluable
            print(f"HOLD: fleet_status read failed ({exc}); no seat can be partitioned "
                  "or boot-suppressed, so nothing was evaluated", file=sys.stderr)
            return EXIT_HOLD

    reports: list[SeatWakeReport] = []
    for slug in slugs:
        if provisioned is not None and slug not in provisioned:
            report = SeatWakeReport(slug=slug)
            config = load_customer_yaml(slug)
            if config is not None and not cron_slots.authored_cron_rows(config):
                report.empty_cron = True
            else:
                report.skipped = "authored but not provisioned (no fleet_status row)"
            reports.append(report)
            continue
        reports.append(
            reconcile_seat(
                slug,
                now=now,
                since=since,
                boot_info=boot_rows.get(slug),
                outcomes=outcomes,
                rows=offline_rows,
            )
        )

    print(as_json(reports) if args.json else render(reports))

    if any(r.is_finding for r in reports):
        return EXIT_FINDING
    # An empty-cron seat counts as evaluated: "zero slots owed, zero missing"
    # is a measurement, not an absence of one. Only held/skipped seats are
    # unmeasured.
    evaluated = [r for r in reports if r.held is None and r.skipped is None]
    if not evaluated:
        # Nothing was measured anywhere. Clean and unmeasured must not print
        # the same exit code.
        return EXIT_HOLD
    return EXIT_CLEAN


if __name__ == "__main__":
    raise SystemExit(main())
