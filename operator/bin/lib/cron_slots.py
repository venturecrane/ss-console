"""Authored-cron slot expansion for the per-slot watchdog (reconcile-wakes.py).

The question this library answers: given a seat's authored
``personas[].cron[]`` table and a time window, at exactly which instants was a
wake row OWED? The watchdog's finding class is the slot that passed with
NEITHER an EMITTED_WAKE nor a SUPPRESSED_WAKE row -- the shape where a dead
cron daemon, materializer drift, a downed machine, or a pre_run crash with the
broker down wrote nothing, and fail-open meant fail-silent.

SCOPE (cross-workstream contract, item 3): only ``wake_policy:
pre_run_decides`` rows expand. An ``always``-wake row (connector-auth-check)
writes no wake rows by construction -- its evidence lives in the
connector-health ledger -- so expanding it would manufacture a permanent
finding out of authored behavior. Those rows render as ``n/a``.

THE EXPANDER IS OURS, ~60 lines, because croniter is not a dependency of the
bare CI env (pytest + pyyaml only) and adding a dependency to dodge five
well-understood cron fields is how a watchdog grows a supply chain. Semantics
match the daemon's five-field subset the authored seats actually use: numbers,
``*``, ranges ``a-b``, steps ``*/n`` and ``a-b/n``, comma lists, and the
standard dom/dow OR rule (when both are restricted, either matching suffices).
Anything the parser does not recognize is REFUSED loudly (``CronParseError``),
never guessed -- the config-gates-parse rule.

DST IS HANDLED BY CONSTRUCTION, not by arithmetic: expansion walks UTC minutes
and converts each to the seat's zone (``business_hours.timezone`` ->
HERMES_TIMEZONE semantics) for matching. A local wall time that does not exist
on spring-forward day simply never appears in the walk, and a repeated
fall-back hour appears twice exactly as a wall clock does -- both without any
offset bookkeeping that could be wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo


class CronParseError(ValueError):
    """An unrecognized schedule. Refused loudly; a guessed slot grid is worse
    than none, because it accuses the wrong minutes."""


@dataclass(frozen=True)
class CronRow:
    """One authored cron entry, as the watchdog needs it."""

    skill: str
    schedule: str
    wake_policy: str


@dataclass
class Slot:
    """One concrete owed fire time (UTC instant; ``local`` for the report)."""

    skill: str
    fires_at: datetime  # aware, UTC
    local: str  # the seat-local rendering, for humans


_FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))  # minute hour dom month dow


def _parse_field(field: str, low: int, high: int) -> frozenset[int]:
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            if not step_text.isdigit() or int(step_text) < 1:
                raise CronParseError(f"bad step {step_text!r}")
            step = int(step_text)
        if part == "*":
            start, stop = low, high
        elif "-" in part:
            a, b = part.split("-", 1)
            if not (a.isdigit() and b.isdigit()):
                raise CronParseError(f"bad range {part!r}")
            start, stop = int(a), int(b)
        elif part.isdigit():
            start = stop = int(part)
        else:
            raise CronParseError(f"unrecognized field part {part!r}")
        if start < low or stop > high or start > stop:
            raise CronParseError(f"field part {part!r} outside {low}-{high}")
        values.update(range(start, stop + 1, step))
    return frozenset(values)


@dataclass(frozen=True)
class CronSpec:
    minutes: frozenset[int]
    hours: frozenset[int]
    dom: frozenset[int]
    months: frozenset[int]
    dow: frozenset[int]
    dom_restricted: bool
    dow_restricted: bool

    def matches(self, local: datetime) -> bool:
        if local.minute not in self.minutes or local.hour not in self.hours:
            return False
        if local.month not in self.months:
            return False
        # cron dow: 0 and 7 are both Sunday; python weekday(): Mon=0..Sun=6.
        cron_dow = (local.weekday() + 1) % 7
        dom_ok = local.day in self.dom
        dow_ok = cron_dow in self.dow or (cron_dow == 0 and 7 in self.dow)
        if self.dom_restricted and self.dow_restricted:
            return dom_ok or dow_ok  # the standard OR rule
        if self.dom_restricted:
            return dom_ok
        if self.dow_restricted:
            return dow_ok
        return True


def parse_schedule(schedule: str) -> CronSpec:
    fields = schedule.split()
    if len(fields) != 5:
        raise CronParseError(f"not a 5-field cron expression: {schedule!r}")
    parsed = [_parse_field(field, low, high) for field, (low, high) in zip(fields, _FIELD_RANGES)]
    return CronSpec(
        minutes=parsed[0],
        hours=parsed[1],
        dom=parsed[2],
        months=parsed[3],
        dow=parsed[4],
        dom_restricted=fields[2] != "*",
        dow_restricted=fields[4] != "*",
    )


def authored_cron_rows(customer_yaml: dict) -> list[CronRow]:
    """Every ``personas[].cron[]`` entry of a parsed customer.yaml. Parsed
    structurally; a malformed entry contributes nothing here because the schema
    validator owns malformedness -- this reader must not become a second
    validator that can disagree with the first."""
    rows: list[CronRow] = []
    personas = customer_yaml.get("personas")
    for persona in personas if isinstance(personas, list) else []:
        if not isinstance(persona, dict):
            continue
        cron = persona.get("cron")
        for entry in cron if isinstance(cron, list) else []:
            if not isinstance(entry, dict):
                continue
            skill = entry.get("skill")
            schedule = entry.get("schedule")
            if isinstance(skill, str) and skill and isinstance(schedule, str) and schedule:
                rows.append(
                    CronRow(
                        skill=skill,
                        schedule=schedule,
                        wake_policy=str(entry.get("wake_policy") or ""),
                    )
                )
    return rows


def seat_timezone(customer_yaml: dict) -> ZoneInfo:
    """``business_hours.timezone`` -> the zone cron runs in (HERMES_TIMEZONE
    semantics). Absent -> UTC, which is exactly what the seat runs without the
    export."""
    hours = customer_yaml.get("business_hours")
    name = hours.get("timezone") if isinstance(hours, dict) else None
    if isinstance(name, str) and name:
        return ZoneInfo(name)
    return ZoneInfo("UTC")


def expand_slots(rows: list[CronRow], tz: ZoneInfo, since: datetime, until: datetime) -> list[Slot]:
    """Concrete fire times for every expandable row inside [since, until).

    Walks UTC minutes (see module docstring for why that is the DST-correct
    construction). Only ``pre_run_decides`` rows expand; the caller reports the
    others as ``n/a`` so the denominator stays visible.
    """
    specs = [(row, parse_schedule(row.schedule)) for row in rows if row.wake_policy == "pre_run_decides"]
    if not specs:
        return []
    slots: list[Slot] = []
    cursor = since.astimezone(timezone.utc).replace(second=0, microsecond=0)
    end = until.astimezone(timezone.utc)
    while cursor < end:
        local = cursor.astimezone(tz)
        for row, spec in specs:
            if spec.matches(local):
                slots.append(Slot(skill=row.skill, fires_at=cursor, local=local.isoformat()))
        cursor += timedelta(minutes=1)
    return sorted(slots, key=lambda slot: (slot.fires_at, slot.skill))


@dataclass
class SlotVerdict:
    slot: Slot
    covered_by: Optional[str] = None  # EMITTED_WAKE | SUPPRESSED_WAKE
    covered_row_id: Optional[str] = None
    suppressed_reason: Optional[str] = None  # boot window etc.
    outcome_disposition: Optional[str] = None  # closed | pending | silent (annotation)

    @property
    def is_missing(self) -> bool:
        return self.covered_by is None and self.suppressed_reason is None


def match_slots(slots: list[Slot], wake_rows: list[dict], *, tolerance_s: int = 1800) -> list[SlotVerdict]:
    """Per slot: an EMITTED_WAKE or SUPPRESSED_WAKE row for that skill inside
    [slot, slot + tolerance], CONSUMED one-to-one (the reconciler discipline:
    one row can never cover two slots)."""
    candidates = []
    for row in wake_rows:
        if row.get("action_type") not in ("EMITTED_WAKE", "SUPPRESSED_WAKE"):
            continue
        if not row.get("ts"):
            continue
        candidates.append(
            {
                "ts": datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00")).astimezone(timezone.utc),
                "skill": str(row.get("skill_name") or ""),
                "kind": str(row.get("action_type")),
                "row_id": row.get("id"),
                "claimed": False,
            }
        )
    candidates.sort(key=lambda c: c["ts"])
    verdicts: list[SlotVerdict] = []
    for slot in slots:
        claim = next(
            (
                c
                for c in candidates
                if not c["claimed"]
                and c["skill"] == slot.skill
                and slot.fires_at <= c["ts"] <= slot.fires_at + timedelta(seconds=tolerance_s)
            ),
            None,
        )
        if claim is None:
            verdicts.append(SlotVerdict(slot=slot))
            continue
        claim["claimed"] = True
        verdicts.append(SlotVerdict(slot=slot, covered_by=claim["kind"], covered_row_id=claim["row_id"]))
    return verdicts


def boot_window(last_heartbeat_ts: Optional[str], uptime_s: Optional[int]) -> Optional[tuple[datetime, datetime]]:
    """The reprovision/boot suppression window, from fleet_status.

    boot = last_heartbeat - uptime; the window is [boot - 45min, boot + 15min].
    The pre-boot margin absorbs the outage that PRECEDED the restart (a Fly
    host migration or OOM kill takes the machine down before it comes back),
    and the post-boot margin absorbs the daemon's catch-up. Missing inputs
    return None -- no window -- rather than a guessed one.
    """
    if not last_heartbeat_ts or not isinstance(uptime_s, int):
        return None
    heartbeat = datetime.fromisoformat(str(last_heartbeat_ts).replace("Z", "+00:00"))
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=timezone.utc)
    boot = heartbeat.astimezone(timezone.utc) - timedelta(seconds=uptime_s)
    return (boot - timedelta(minutes=45), boot + timedelta(minutes=15))


def apply_boot_suppression(verdicts: list[SlotVerdict], window: Optional[tuple[datetime, datetime]]) -> None:
    if window is None:
        return
    start, end = window
    for verdict in verdicts:
        if verdict.is_missing and start <= verdict.slot.fires_at <= end:
            verdict.suppressed_reason = "boot/reprovision window"


__all__ = [
    "CronParseError",
    "CronRow",
    "CronSpec",
    "Slot",
    "SlotVerdict",
    "apply_boot_suppression",
    "authored_cron_rows",
    "boot_window",
    "expand_slots",
    "match_slots",
    "parse_schedule",
    "seat_timezone",
]
