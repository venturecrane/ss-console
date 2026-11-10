"""The hourly per-person digest of what is due (ss-console #3946, the rep desk).

ONE MANAGED CRON JOB, HOURLY (``op-managed:<persona>:rep-desk``, ``wake_policy:
pre_run_decides``, ``settings.recipients: roster``). Its ``pre_run.py`` calls
:func:`run`, and CODE does all of it:

* reads the seat's roster (authored exact addresses, and on a guide seat the
  enrolled list) and, for each person, ONLY that person's own partition of the
  reminder store (``record_store.code_list`` / ``code_read``, one named person
  at a time);
* decides who is due a digest this hour: a person with at least one reminder due
  today or still open from the last :data:`LOOKBACK_DAYS` days, who has not had
  a digest today, once the seat's local hour reaches ``settings.digest_hour``;
* renders ONE email per person, addressed to that person only, from that
  person's records only (:func:`render`), so no body can carry another person's
  words: the renderer is handed one person's reminders and nothing else;
* writes at most :data:`CHUNK` dispatches into the consume-once envelope
  ``shared.prerendered_dispatch`` sends out of turn through the full gate. An
  envelope over ten dispatches is DROPPED WHOLE by that module, so a chunk is
  ten, never more.

THE CURSOR. Each person's partition carries one mark, ``digest-sent.json``,
holding the local day their last digest was rendered for. The candidates are
taken in a fixed order (by the person's directory name), skipping anyone marked
today; the first ten are this hour's chunk and the rest wait for the next hour.
So the marks ARE the cursor, and they live behind each person's own wall: the
purge deletes a person's cursor with their reminders (``person_purge``'s
partition layer), and no file outside a partition names anyone.

A mark is written for a chunk BEFORE its envelope (a mark that could not be
written drops that person from the chunk), and every mark of the chunk is
restored if the envelope cannot be written. A refused or undelivered send is
not retried within the day, by design: a second copy is worse than a late one,
the reminders stay listed as still open in the next day's digest, and the
refusal is a work-health item (the triage reads the run's rows).

NOTHING DUE FOR ANYONE (or before the digest hour, or no store authored):
``wakeAgent: false``, so no model call and no send. An envelope that cannot be
written wakes the turn with nothing to send (``envelope_write_failed``), so the
run's wake row is an obligation nothing closes and the triage reports it.

PROVENANCE. The handoff (``shared.pre_run_handoff``) carries every date the
rendered bodies show, read off the bodies by the identifier gate's own extractor
and nothing else, so the out-of-turn gate can verify each date the code wrote.

WHAT STDOUT AND THE ROWS CARRY: the verdict and counts. Never an address, a
name, a reminder or a date.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from shared import pre_run_handoff, record_store, reminders

logger = logging.getLogger(__name__)

SKILL = reminders.SKILL
#: ``prerendered_dispatch._MAX_DISPATCHES``; pinned equal by test.
CHUNK = 10
#: How many days back an open reminder is still listed.
LOOKBACK_DAYS = 7
#: Lines per digest before the rest is summarized.
MAX_ITEMS = 12
DIGEST_HOUR_SETTING = "digest_hour"
MARK_NAME = "digest-sent.json"
ROUTING_LEG = "person"

SKELETON = (
    "Hello,\n\n"
    "You have reminders due. This message could not carry them in full; reply in this "
    "thread and ask for your reminders to see them."
)
FOOTER = "To keep another reminder, reply in this thread with what it is and when."


# ---------------------------------------------------------------------------
# Who
# ---------------------------------------------------------------------------


def people(cfg: Any) -> list[str]:
    """Every exact address on the seat's roster (authored, and enrolled on a
    guide seat), in a fixed order: by the person's partition directory name."""
    from shared.person_prefs import normalize_person_address, person_slug

    out: dict[str, str] = {}
    for entry in cfg.inbound_roster:
        norm = normalize_person_address(entry)
        if norm is not None and not entry.startswith("@"):
            out[person_slug(norm)] = norm
    return [out[slug] for slug in sorted(out)]


def digest_hour(cfg: Any) -> int | None:
    """``settings.digest_hour`` on rep-desk, 0 to 23, or None (unauthored: no
    digest, never an imposed default)."""
    value = (reminders.desk_settings(cfg) or {}).get(DIGEST_HOUR_SETTING)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 23:
        return None
    return value


# ---------------------------------------------------------------------------
# What is due, and the words
# ---------------------------------------------------------------------------


def due_for(items: list[reminders.Reminder], today: date) -> tuple[list, list]:
    """``(due today, still open from the last LOOKBACK_DAYS days)``."""
    floor = today - timedelta(days=LOOKBACK_DAYS)
    today_items = [r for r in items if r.due == today]
    earlier = [r for r in items if floor <= r.due < today]
    return today_items, earlier


def _line(n: int, r: reminders.Reminder, *, with_day: bool) -> str:
    parts = []
    if with_day:
        parts.append(reminders.day_words(r.due))
    if r.at is not None:
        parts.append(reminders.clock_words(r.at))
    if r.kind == reminders.KIND_MEETING:
        parts.append("meeting")
    head = ", ".join(parts)
    return f"{n}. {head}: {r.what}" if head else f"{n}. {r.what}"


def subject(today: date) -> str:
    return f"Due today, {reminders.day_words(today)}"


def render(today_items: list, earlier: list, today: date) -> str:
    """One person's digest body, from that person's reminders only."""
    lines = [
        "Hello,",
        "",
        "Here is what is due from the reminders you kept with the guide.",
        "",
    ]
    n = 0
    shown = 0
    if today_items:
        lines.append(f"Today, {reminders.day_words(today)}")
        for r in today_items:
            if shown >= MAX_ITEMS:
                break
            n += 1
            shown += 1
            lines.append(_line(n, r, with_day=False))
        lines.append("")
    if earlier and shown < MAX_ITEMS:
        lines.append("Still open from earlier")
        for r in earlier:
            if shown >= MAX_ITEMS:
                break
            n += 1
            shown += 1
            lines.append(_line(n, r, with_day=True))
        lines.append("")
    hidden = len(today_items) + len(earlier) - shown
    if hidden > 0:
        lines.append(f"And {hidden} more; reply and ask for your reminders to see them all.")
        lines.append("")
    lines.append(FOOTER)
    return "\n".join(lines)


def body_dates(body: str) -> list[str]:
    """Every date the identifier gate reads in ``body``, as written, and
    nothing that is not a date (the handoff's own read-side check repeats it)."""
    from shared.identifier_filter import IdKind, ProvenanceRegister, unverified_identifiers

    return [
        hit.raw
        for hit in unverified_identifiers(body, ProvenanceRegister())
        if hit.kind is IdKind.DATE
    ]


# ---------------------------------------------------------------------------
# The cursor: one mark per person, in their own partition
# ---------------------------------------------------------------------------


def read_mark(store: str, person: str, cfg: Any) -> str | None:
    try:
        raw = json.loads(record_store.code_read(store, person, MARK_NAME, config=cfg))
    except (record_store.RecordStoreError, ValueError):
        return None
    day = raw.get("day") if isinstance(raw, dict) else None
    return day if isinstance(day, str) else None


def write_mark(store: str, person: str, day: str | None, cfg: Any) -> bool:
    """Set the person's mark to ``day``; ``None`` restores "never". True on success."""
    try:
        if day is None:
            policy = record_store.authored_policies(cfg).get(store)
            if policy is not None:
                (record_store.partition_dir(policy, person) / MARK_NAME).unlink(missing_ok=True)
            return True
        record_store.code_write(
            store, person, MARK_NAME, json.dumps({"day": day}), overwrite=True, config=cfg
        )
        return True
    except (record_store.RecordStoreError, OSError):
        return False


# ---------------------------------------------------------------------------
# The plan and the run
# ---------------------------------------------------------------------------


@dataclass
class Dispatch:
    person: str
    subject: str
    body: str
    previous_mark: str | None


@dataclass
class Plan:
    status: str
    chunk: list[Dispatch] = field(default_factory=list)
    people: int = 0
    due_people: int = 0
    deferred: int = 0
    unreadable: int = 0

    def counts(self) -> dict[str, int]:
        return {
            "people": self.people,
            "due": self.due_people,
            "chunk": len(self.chunk),
            "deferred": self.deferred,
            "unreadable": self.unreadable,
        }


def plan(cfg: Any, now: datetime) -> Plan:
    """Who gets a digest this hour, and its words. Reads; writes nothing."""
    store = reminders.reminder_store(cfg)
    if store is None:
        return Plan(status="store_unauthored")
    hour = digest_hour(cfg)
    if hour is None:
        return Plan(status="digest_hour_unauthored")
    local = reminders.local_now(reminders.seat_zone(cfg), now)
    if local.hour < hour:
        return Plan(status="before_digest_hour")
    today = local.date()
    everyone = people(cfg)
    out = Plan(status="nothing_due", people=len(everyone))
    for person in everyone:
        try:
            previous = read_mark(store, person, cfg)
            if previous == today.isoformat():
                continue
            today_items, earlier = due_for(
                reminders.person_reminders(store, person, config=cfg), today
            )
        except record_store.RecordStoreError:
            out.unreadable += 1
            continue
        if not today_items and not earlier:
            continue
        out.due_people += 1
        if len(out.chunk) >= CHUNK:
            out.deferred += 1
            continue
        out.chunk.append(
            Dispatch(person, subject(today), render(today_items, earlier, today), previous)
        )
    if out.chunk:
        out.status = "digest_ready"
    return out


def envelope(chunk: list[Dispatch], started_at: str) -> dict[str, Any]:
    """The consume-once envelope: one dispatch per person, to that person only."""
    from shared.prerendered_dispatch import canonical_body_sha256

    return {
        "skill": SKILL,
        "render_mode": "templated",
        "started_at": started_at,
        "dispatches": [
            {
                "recipients": [d.person],
                "cc": [],
                "routing_leg": ROUTING_LEG,
                "subject": d.subject,
                "full_body": d.body,
                "skeleton_body": SKELETON,
                "body_sha256_full": canonical_body_sha256(d.body),
                "body_sha256_skeleton": canonical_body_sha256(SKELETON),
                "appends": [],
            }
            for d in chunk
        ],
        "unroutable": [],
        "memo_matters": [],
        "in_turn": [{"name": "rep_desk_digest_skeleton", "template": SKELETON, "slots": {}}],
        "in_turn_enforce": True,
    }


def write_envelope(payload: dict[str, Any], hermes_home: str | None = None) -> bool:
    """Atomic 0600 write beside the handoff (``shared.prerendered_dispatch.envelope_path``
    under the scheduler's ``HERMES_HOME``). False on any failure."""
    try:
        directory = pre_run_handoff.handoff_dir(hermes_home)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = directory / f"{SKILL}.dispatch.json"
        fd, tmp = tempfile.mkstemp(dir=str(directory), prefix=".digest-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return True
    except Exception as exc:  # noqa: BLE001 - the caller reports it; never raised
        logger.warning("person_digest: envelope write failed (%s)", type(exc).__name__)
        return False


def run(cfg: Any, now: datetime | None = None, *, dry: bool = False) -> dict[str, Any]:
    """Plan, mark, write the envelope and the handoff. Returns the verdict:
    ``{"wakeAgent": bool, "status": str, **counts}``, content-free."""
    moment = now if now is not None else datetime.now(timezone.utc)
    p = plan(cfg, moment)
    verdict: dict[str, Any] = {"status": p.status, **p.counts()}
    if p.status != "digest_ready" or dry:
        return {"wakeAgent": False, **verdict, **({"dry_run": True} if dry else {})}
    store = reminders.reminder_store(cfg) or ""
    today = reminders.local_now(reminders.seat_zone(cfg), moment).date().isoformat()
    marked = [d for d in p.chunk if write_mark(store, d.person, today, cfg)]
    stamp = moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if not marked or not write_envelope(envelope(marked, stamp)):
        for d in marked:
            write_mark(store, d.person, d.previous_mark, cfg)
        # Wake the turn with nothing to send: the run's EMITTED_WAKE row then
        # opens an obligation nothing closes, which the triage reports.
        return {"wakeAgent": True, **verdict, "status": "envelope_write_failed", "chunk": 0}
    dates = [d for item in marked for d in body_dates(item.body + "\n" + item.subject)]
    pre_run_handoff.write_handoff(SKILL, moment, dates)
    return {
        "wakeAgent": True,
        "dispatch_expected": True,
        **verdict,
        "chunk": len(marked),
        "mark_failed": len(p.chunk) - len(marked),
    }


__all__ = [
    "CHUNK",
    "LOOKBACK_DAYS",
    "MARK_NAME",
    "Plan",
    "SKELETON",
    "body_dates",
    "digest_hour",
    "due_for",
    "envelope",
    "people",
    "plan",
    "render",
    "run",
    "subject",
    "write_envelope",
]
