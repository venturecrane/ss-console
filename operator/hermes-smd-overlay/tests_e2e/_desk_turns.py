"""The rep desk's turns in the guide rehearsal (ss-console #3946, slice 3).

Called from ``test_guide_rehearsal_run.run`` after the foundation's turns, on
the same seat and the same two people. Everything goes through the seat's own
path: each email through the webhook router and Hermes' own agent loop with the
scripted model; the hourly digest through the REAL ``rep-desk`` pre_run, run as
the scheduler runs it, then a real cron turn on the managed job translate
materialized, whose ``pre_llm_call`` dispatches the envelope out of turn
through the trust plugin's full gate to the stub broker (``_cron_turn.py``,
shared with the training slice).

The seat config gains, by :func:`author_desk`, what the lead authors on the
real guide seat: the ``rep-desk`` skill with its reminder store, digest hour,
zone and ``recipients: roster``, and its hourly managed cron row. The
``rep-reminders`` store (walled per person, written only by code) is in the
rehearsal's ``STORES``. The zone is UTC and the digest hour 0 so a reminder
kept "today" is due in the same run whatever the wall clock says.

People and markers are the foundation rehearsal's: what A keeps carries
``A_MARK`` and what B keeps ``B_MARK``, so the canary inventory finds where each
person's desk words land.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

#: This repository's ``operator/`` tree (the broker's install on a seat).
OPERATOR = Path(__file__).resolve().parents[2]
SKILL = "rep-desk"
REMINDERS = "rep-reminders"

#: The skill block the rehearsal seat authors (the lead's config PR mirrors it).
DESK_SKILL: dict[str, Any] = {
    "name": SKILL,
    "version": "pending",
    "initiation": {"manual": True, "scheduled": True, "webhook": True},
    "enabled": True,
    "settings": {
        "reminder_store": REMINDERS,
        "digest_hour": 0,
        "timezone": "UTC",
        "recipients": "roster",
    },
}
DESK_CRON: dict[str, Any] = {
    "skill": SKILL,
    "schedule": "7 * * * *",
    "pre_run": "pre_run.py",
    "wake_policy": "pre_run_decides",
}


def author_desk(doc: dict[str, Any]) -> None:
    """The rehearsal seat authors the desk (mutates ``doc``)."""
    persona = doc["personas"][0]
    persona["skills"].append(json.loads(json.dumps(DESK_SKILL)))
    persona.setdefault("cron", []).append(dict(DESK_CRON))


def _lead(company: str, mark: str) -> str:
    return (
        f"Company: {company}\n"
        "Contact: the CFO\n"
        "Stage: meeting set\n"
        "Next step: send the pilot outline\n"
        "Amount: 12,000 dollars a year\n"
        f"Notes: wants fewer handoffs. {mark}\n"
    )


AHEAD_DAYS = 3


def receipt_ahead(what: str, kind: str = "Reminder") -> str:
    """The receipt line reminder_set returns for ``when: in 3 days`` on this
    seat (zone UTC), computed the way the seat's code renders it. Not today:
    the seat's own date is never an unverified date, so a receipt for today
    would pass the reply checks with or without the seeding under test."""
    from shared.reminders import day_words

    day = datetime.now(timezone.utc).date() + timedelta(days=AHEAD_DAYS)
    return f"{kind} kept for {day_words(day)}: {what}"


def run_desk(r: Any, a: str, b: str, a_mark: str, b_mark: str) -> dict[str, Any]:
    """The desk turns. Returns the digest's pre_run verdict and its turn label.

    The first turn's reply carries a receipt line, date included, through the
    real reply relay and its checks.

    Every desk label starts ``d-``, and each desk email starts on a fresh day
    of the per-sender daily limit (the training slice's recorded departure,
    ``_training_turns._fresh_day``): the foundation's turns spend A's three and
    prove the limit, and that test leaves ``d-`` turns out of its count."""
    from tests_e2e._training_turns import _fresh_day

    def turn(*args: Any, **kwargs: Any) -> dict[str, Any]:
        _fresh_day()
        return r.turn(*args, **kwargs)

    turn(
        "d-a-desk-lead-and-reminder",
        [
            (
                "record_store_write",
                {"store": "rep-leads", "name": "globex.md", "content": _lead("Globex", a_mark)},
            ),
            (
                "reminder_set",
                {"when": "today", "what": f"Call the Globex CFO {a_mark}", "kind": "meeting"},
            ),
            (
                "reminder_set",
                {
                    "when": f"in {AHEAD_DAYS} days",
                    "what": f"Send Globex the pilot outline {a_mark}",
                },
            ),
            ("record_store_read", {"store": "rep-leads", "name": "globex.md"}),
        ],
        sender=a,
        body=f"New lead Globex, meeting today with the CFO, outline due in three days. {a_mark}",
        final_text=receipt_ahead(f"Send Globex the pilot outline {a_mark}")
        + "\nLead kept: Globex, meeting set.",
    )
    turn(
        "d-b-desk-reminder-and-look-across",
        [
            ("reminder_set", {"when": "today", "what": f"Send Initech the deck {b_mark}"}),
            ("record_store_list", {"store": REMINDERS}),
            ("record_store_list", {"store": "rep-leads"}),
            ("record_store_read", {"store": "rep-leads", "name": "globex.md"}),
            (
                "record_store_write",
                {"store": REMINDERS, "name": "reminder-x.json", "content": "{}"},
            ),
        ],
        sender=b,
        body=f"Remind me today to send Initech the deck. {b_mark}",
    )
    turn(
        "d-a-brief-from-own-records",
        [
            ("record_store_list", {"store": "rep-leads"}),
            ("record_store_read", {"store": "rep-leads", "name": "globex.md"}),
            ("record_store_list", {"store": "rep-notes"}),
            ("record_store_read", {"store": "rep-notes", "name": "acme.md"}),
            ("record_store_list", {"store": REMINDERS}),
        ],
        sender=a,
        body="Brief me on Globex before my call.",
        final_text=(
            "Who: Globex, the CFO.\n"
            "Where it stands: meeting set; next step, send the pilot outline.\n"
            "Amount: 12,000 dollars a year.\n"
            "Open questions: who else signs off.\n"
        ),
    )
    turn(
        "d-a-follow-up-draft",
        [("record_store_read", {"store": "rep-leads", "name": "globex.md"})],
        sender=a,
        body="Draft a follow-up to Globex.",
        final_text=(
            "Draft for you to send:\n\n"
            "Thank you for the time today. As promised, here is an outline of how a pilot "
            "would start, built around the handoffs you described. Would a short call "
            "work to go through it?\n"
        ),
    )
    return digest(r)


def digest(r: Any) -> dict[str, Any]:
    """The hourly job: the real pre_run, then the cron turn that sends.

    The job counts walled directories nobody listed owns with the broker
    tree's ``workspace_broker.person_orphans``, which a seat's agent reads from
    the broker's install (``seat_layout.BROKER_ROOT``). Here that tree is this
    repository's ``operator/``, put on the child's path: a recorded departure."""
    import os

    from tests_e2e._cron_turn import cron_turn, run_pre_run

    tree = str(OPERATOR)
    path = os.environ.get("PYTHONPATH", "")
    if tree not in path.split(os.pathsep):
        os.environ["PYTHONPATH"] = os.pathsep.join(p for p in (path, tree) if p)
    ran = run_pre_run(r.profile, SKILL, volume=r.volume)
    (r.root / "digest-pre-run.out").write_text(ran["stdout"] + ran["stderr"], encoding="utf-8")
    cron_turn(r, "desk-digest-cron-turn", skill=SKILL, script_output=json.dumps(ran["verdict"]))
    return {"verdict": ran["verdict"], "code": ran["code"], "turn": "desk-digest-cron-turn"}


__all__ = [
    "DESK_CRON",
    "DESK_SKILL",
    "REMINDERS",
    "author_desk",
    "digest",
    "receipt_ahead",
    "run_desk",
]
