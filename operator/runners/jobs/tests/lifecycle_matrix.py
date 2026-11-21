"""The chronology lifecycle matrix: every job state x every event that can
reach it, plus one cell per exit class every stage of a run and an update
declares, each driven through the REAL chronology lane (built by its own
``build_lane``) against the REAL broker over its socket (the host matrix's
seat, ``host_matrix_kit.py``), and graded on where the job ends.

WHY (2026-11-17). No real chronology run of 29 had ever finished unaided, and
43% of 103 run-defect fixes were plumbing: the queue, the upload, the reply,
the resume. Each was found by a client. This instrument asks, for every way a
job can end, four questions a client would otherwise answer for us:

(a) who owns the ending (``firm``, ``smd`` or ``code``), and is the owner told
    (an ending SMD owns carries a page);
(b) what way out it has: a verb that accepts the row now;
(c) how many messages the firm gets: the declared count (one for a delivery,
    none for a hold or a failure), never two;
(d) how many filed sets: at most one;
(e) whether the row that put the job in its state carries a run record.

And three asked of particular events: (f) a bound's ending leaves the job dir
wiped on schedule; (g) the firm's message never waits for the broker; (h) one
broker error the lane's retry rides out is no page and leaves the run unaided.

THE CELLS ARE GENERATED FROM CODE. ``STATES`` is the ledger's own
``medchron_ledger.STATES`` plus the two resting states it does not name
(``parked``: held by a seat pause; ``requeued_waiting``: an infrastructure
retry waiting out its delay). ``EVENTS`` is a fixed set plus one event per
declared exit class, read from every ``Stage.exit_map`` in ``dag.stages()``
and ``dag.stages(update=True)``. A stage added with an exit code and no cell
fails the completeness test (``test_lifecycle_matrix.py``).

HOW A CELL RUNS. The job is brought to its state by ticks of the real lane;
only the child is inline (the lane's ``run_child`` returns the cell's verdict,
written by the real ``verdict.write`` in the driver's own ``Outcome`` shape,
the way ``host_matrix_child.py`` swaps only the run class). For ``running``
the event happens while the child runs. Then the event, then ticks until the
lane is quiet (each advancing the clock past a retry delay), and the ending is
read back from the broker's row, the daemon's state, the queue, the audit log
and what the gateway received.

Regenerate the golden (``goldens/lifecycle/medchron.json``) with one command,
from ``operator/`` in the runner's venv (four workers; about two minutes):

    python runners/jobs/tests/lifecycle_matrix.py --write
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from host_matrix_cells import Run
from host_matrix_kit import broker_down, broker_up, build_broker, freeze_broker_clock, seed_random, serve
from host_matrix_lanes import remove

GOLDEN = Path(__file__).resolve().parent / "goldens" / "lifecycle" / "medchron.json"
PROOF = {
    "readback": "confirmed",
    "files": [{"name": "x.docx", "file_id": "f1", "size": 10, "sha256": "a" * 64}],
    "review": "passed",
    "review_cents": 0,
}
DELIVERY_FILES = [{"name": "A.docx", "sha256": "ab", "bytes": 10, "confirmed": True}]
SETTLE_TICKS = 8
SETTLE_STEP = 1000.0
ALLOWANCE = 100_000


def states() -> tuple[str, ...]:
    from workspace_broker.medchron_ledger import STATES

    return (*STATES, "parked", "requeued_waiting")


#: The events every state is tried against (the exit classes are added from the dag).
FIXED_EVENTS = (
    "claim",
    "cap_hold",
    "deploy_stop",
    "machine_stop",
    "broker_down",
    "upload_readback_mismatch",
    "reply_send_failure",
    "resume",
    "redo",
    "release",
    "audit_decide",
    "coverage_decide",
    "identity_decide",
    "update_decide",
    "redo_doc",
    "hand_delivery",
    "expiry",
    "resubmit_while_running",
    "queue_behind_same_lane",
    # 2026-11-19 (``workspace_broker/medchron_progress.py``): the same person
    # asked again in other words and spelling, and another client on the matter.
    "resubmit_same_person",
    "second_client_same_matter",
    # The broker answers the ending's record internal_error once, then takes it.
    "broker_errors_once",
    # The bounds (2026-11-17, the reviews of #4110): each ends with the job dir wiped.
    "broker_errors_ending",
    "broker_away_past_bound",
    "paused_past_wipe_window",
    "no_wake_secret",
    # 2026-11-20, the whole job's price at the first paid check: the job holds
    # on the cycle line before anything is spent (the second of two jobs), and a
    # job held on its price is approved with a dollar ceiling (runs to delivery,
    # or stops held at the ceiling) or declined (failed) by SMD's own commands
    # (``jobs.medchron.allowance approve --usd`` / ``decline``).
    "cycle_hold",
    "price_approve",
    "price_approve_ceiling",
    "price_decline",
)
#: The whole-job price events: the job is first held on its price, then SMD acts.
PRICE_EVENTS = ("price_approve", "price_approve_ceiling", "price_decline")
#: The price hold's sentence (``limits.check_before_paid``; no dollar figure) and
#: the price SMD's page carries, in cents. Invented.
PRICE_HOLD = (
    "per_job_cap_usd: this run's projected cost would exceed the job's cost cap before vision; "
    "the package was not started"
)
PRICE_CENTS = 40310
#: The approved ceiling, in dollars, and the per-call stop when spend reaches it.
APPROVED_USD = 200.0
CEILING_STOP = "per_job_cap_usd: the run's spend reached the job's cost cap during map; the run stopped"
#: The events whose ending must also leave the job dir wiped on schedule.
WIPE_EVENTS = ("broker_errors_ending", "broker_away_past_bound", "paused_past_wipe_window", "no_wake_secret")


def exit_classes(stages: Callable[..., Any] | None = None) -> list[tuple[str, str, int, str, str]]:
    """``(flavor, stage, code, outcome, reason)`` for every declared exit."""
    if stages is None:
        from jobs.medchron import dag

        stages = dag.stages
    out = []
    for flavor, update in (("run", False), ("update", True)):
        for s in stages(update=update):
            for code, (outcome, reason) in sorted(s.exit_map.items()):
                out.append((flavor, s.name, int(code), str(outcome), str(reason)))
    return out


def exit_event(flavor: str, stage: str, code: int) -> str:
    return f"exit.{flavor}.{stage}.{code}"


def cells(stages: Callable[..., Any] | None = None) -> list[str]:
    fixed = [f"{st}/{ev}" for st in states() for ev in FIXED_EVENTS]
    exits = [f"running/{exit_event(f, s, c)}" for f, s, c, _o, _r in exit_classes(stages)]
    return fixed + exits


#: Cells the code cannot reach, each with the reason read from the code.
NOT_APPLICABLE: dict[str, str] = {
    **{
        f"{st}/{ev}": "a submitted job has run nothing, so nothing of a run can stop, mismatch or be decided "
        "(daemon.claim_next moves it to its job dir; only run_job starts a child)"
        for st in ("submitted",)
        for ev in ("upload_readback_mismatch",)
    },
    **{
        f"delivered/{ev}": "a delivered row never moves again: _ALLOWED_NEXT['delivered'] is empty "
        "(workspace_broker/medchron_ledger.py) and nothing re-queues it, so no child runs to stop or mismatch"
        for ev in ("cap_hold", "deploy_stop", "machine_stop", "upload_readback_mismatch")
    },
    "running/exit.run.audit.9": "the entry audit's answers-pending exit is reached only under its $0 replay switch "
    "(ENTRY_AUDIT_REPLAY=1, read only by stages/entry_audit_io.replaying), which no seat run sets",
}


# ---------------------------------------------------------------------------
# The seat and the inline child
# ---------------------------------------------------------------------------


@dataclass
class Life:
    """One seat, one chronology lane, and the cell's record."""

    run: Run
    children: list[Callable[[Path], tuple[int, str]]] = field(default_factory=list)
    during: list[Callable[[], Any]] = field(default_factory=list)
    acts: list[tuple[str, str]] = field(default_factory=list)
    #: Delivered messages the firm had before the broker came back (``broker_away_past_bound``).
    told_while_away: int = 0
    #: Whether the job's ending came while the broker was away.
    ended_while_away: bool = False
    #: Indexes of the gateway's posts it answered with an error (never delivered).
    refused_posts: set[int] = field(default_factory=set)
    #: Lifts the broker's injected error (``_error_ending``): SMD's repair.
    heal: Callable[[], Any] | None = None

    @property
    def lane(self) -> Any:
        return self.run.lane

    @property
    def seat(self) -> Any:
        return self.run.seat

    def client(self) -> Any:
        from jobs.broker_client import BrokerClient

        return BrokerClient(str(self.seat.socket_path))

    def child(self, lane: Any, job_id: str, argv: list[str], **_kw: Any) -> tuple[int, str]:
        jd = lane.job_dir(job_id)
        self.run.launches.rows.append({"job_id": job_id, "argv": list(argv)})
        while self.during:
            self.during.pop(0)()
        make = self.children.pop(0) if self.children else delivered()
        return make(jd)

    def submit(self, number: str = "2026-PI-102", matter: str = "m-1") -> str:
        env = _envelope(number, matter)
        return self.run.broker.medchron._db.submit(env, remaining=40)

    def tick(self) -> Any:
        return self.lane.tick()

    def settle(self) -> None:
        for _ in range(SETTLE_TICKS):
            self.seat.now["t"] += SETTLE_STEP
            if self.tick() is None and not _wakes_pending(self):
                return


def _envelope(number: str, matter: str) -> dict[str, Any]:
    unit = {"unit": "alpha", "client_name": "Alpha Example", "name_token": "Example", "surname": "Example"}
    return {
        "matter": {"id": matter, "number": number, "title": "Example v. Example"},
        "units": [{**unit, "dob": "01/02/1980"}],
        "incident": {"date": "2026-01-15", "source": "administrator_request"},
        "cap_usd": 25.0,
        "requested_by": "admin@firm.example",
        "request_ref": f"<req-{matter}@firm.example>",
    }


def _wakes_pending(life: Life) -> bool:
    for p in life.seat.run_dir.glob("jobs/*/daemon.json"):
        if (json.loads(p.read_text(encoding="utf-8")).get("wake") or {}).get("pending"):
            return True
    return False


def _verdict(jd: Path, outcome: str, *, stage: str | None, reason: str | None, **kw: Any) -> None:
    from jobs import verdict as verdict_mod
    from jobs.medchron.driver import Outcome

    o = Outcome(unit="alpha", outcome=outcome, reason=reason, stage=stage, dollars=1.0, pages=30, documents=4)
    for k, v in kw.items():
        setattr(o, k, v)
    verdict_mod.write(jd, json.dumps([o.to_dict()]))


def delivered(proof: dict[str, Any] | None = None) -> Callable[[Path], tuple[int, str]]:
    def make(jd: Path) -> tuple[int, str]:
        _verdict(jd, "delivered", stage=None, reason=None, folder_id="folder-9", files=list(DELIVERY_FILES),
                 proof=dict(proof or PROOF))  # fmt: skip
        return 0, ""

    return make


def ended(outcome: str, stage: str, reason: str, exit_code: int | None = None) -> Callable[[Path], tuple[int, str]]:
    def make(jd: Path) -> tuple[int, str]:
        _verdict(jd, outcome, stage=stage, reason=reason, **({"exit_code": exit_code} if exit_code else {}))
        return {"held": 3, "refused": 4}.get(outcome, 1), ""

    return make


def stopped(life: Life, code: int) -> Callable[[Path], tuple[int, str]]:
    """A child cut short by the Machine's stop: no verdict, the stop flag set."""

    def make(jd: Path) -> tuple[int, str]:
        life.lane.stopping = lambda: True
        return code, ""

    return make


def build(tmp_path: Path, mp: Any) -> tuple[Life, Any]:
    """The seat, the broker on its socket (a context to enter) and the lane."""
    r = Run(tmp_path, mp, "medchron", "delivered")
    r.setup()
    r.broker = build_broker(r.seat)
    freeze_broker_clock(mp, r.seat)
    seed_random(mp)
    _author_allowance(r.seat.root / "customer.yaml")
    life = Life(run=r)
    from jobs import child as child_mod
    from jobs.medchron import lane as medchron_lane

    mp.setattr(child_mod, "run_child", life.child)
    if hasattr(medchron_lane, "run_child"):
        mp.setattr(medchron_lane, "run_child", life.child)
    return life, serve(r.seat, r.broker)


def _author_allowance(path: Path) -> None:
    import yaml

    from workspace_broker.medchron_ledger import ALLOWANCE_KEY, SKILL_NAME

    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    for persona in doc["personas"]:
        for skill in persona["skills"]:
            if skill["name"] == SKILL_NAME:
                skill["settings"] = {ALLOWANCE_KEY: ALLOWANCE}
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def teardown(life: Life) -> None:
    logging.getLogger("jobs").removeHandler(life.run.handler)
    remove(life.run.sock_dir)


# ---------------------------------------------------------------------------
# Reaching a state
# ---------------------------------------------------------------------------

#: The full chronology's audit holds when an entry could not be read against
#: its cited pages, or the corrections passed the edit tripwire
#: (``stages/entry_audit``, 2026-11-18; it was the per-claim coverage gate).
AUDIT_HOLD = "audit coverage: entry N2 was not read against its pages"


def reach(life: Life, state: str) -> str:
    """Submit one job and tick it into ``state``; returns its id. ``running``
    is reached by the event's own tick (the event runs during the child)."""
    jid = life.submit()
    if state in ("submitted", "running"):
        return jid
    if state == "parked":
        _sticky(life, "HARD_STOP")
        life.tick()
        return jid
    make = {
        "held": ended("held", "audit", AUDIT_HOLD),
        "failed": ended("failed", "extract", "extract: a stage failed"),
        "delivered": delivered(),
        "requeued_waiting": ended("failed", "map", "map: model API error (infrastructure)", exit_code=75),
    }[state]
    life.children.append(make)
    life.tick()
    if state == "delivered":
        life.tick()  # its DELIVER wake
    return jid


def _sticky(life: Life, level: str) -> None:
    db = life.seat.root / "sticky.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE IF NOT EXISTS sticky_stop_state (scope TEXT, level TEXT)")
    conn.execute("DELETE FROM sticky_stop_state")
    conn.execute("INSERT INTO sticky_stop_state VALUES ('seat', ?)", (level,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# The events (each the real verb, tool write or tick the seat would see)
# ---------------------------------------------------------------------------


def _resume(life: Life, jid: str, **extra: Any) -> None:
    from jobs.broker_client import BrokerError

    try:
        life.client()._request({"action": "medchron_job_resume", "job_id": jid, "reason": "the matrix fixed it",
                                "redo": [], **extra})  # fmt: skip
        life.acts.append(("medchron_job_resume", "accepted"))
    except BrokerError:
        life.acts.append(("medchron_job_resume", "refused"))


def _marker(life: Life, jid: str, intervention: str, redo: list[str]) -> None:
    """The marker each ``*_decide`` tool and ``redo_doc`` writes in ``apply``,
    in its own shape (``medchron/<tool>.py``), at the daemon's own path."""
    from jobs.resume import MARKER_PREFIX

    life.lane.queue.mkdir(parents=True, exist_ok=True)
    body = {"job_id": jid, "intervention": intervention, "reason": f"{intervention} recorded by SMD", "redo": redo}
    (life.lane.queue / f"{MARKER_PREFIX}{jid}.json").write_text(json.dumps(body), encoding="utf-8")
    life.acts.append((intervention, "written"))


def _hand(life: Life, jid: str) -> None:
    from jobs.medchron import deliver_by_smd

    rec = {"documents": 4, "pages": 30, "cents": 100, "folder_id": "folder-9",
           "files": [{"name": "A.docx", "file_id": "f1", "bytes": 10, "sha256": "a" * 64}],
           "covered": {"covered": [], "uncovered": [], "pulled": 0}}  # fmt: skip
    try:
        p = deliver_by_smd.plan(life.lane, jid, rec, update=False)
    except deliver_by_smd.Refused:
        life.acts.append(("deliver_by_smd", "refused"))
        return
    deliver_by_smd.apply(life.lane, p)
    life.acts.append(("deliver_by_smd", "accepted"))


def _resubmit(life: Life, jid: str) -> None:
    reply = life.client()._request({"action": "medchron_job_submit", "envelope": _envelope("2026-PI-102", "m-1")})
    life.acts.append(("medchron_job_submit", "accepted" if reply.get("accepted") else "refused"))


def _reply_failure(life: Life) -> None:
    """The gateway refuses every post for six ticks (past the wake's attempts)."""
    first = len(life.seat.posts)
    life.seat.gate_status = 500
    for _ in range(6):
        life.seat.now["t"] += 60
        life.tick()
    life.seat.gate_status = 200
    life.refused_posts.update(range(first, len(life.seat.posts)))


def _broker_down(life: Life) -> None:
    hidden = broker_down(life.seat)
    life.tick()
    broker_up(life.seat, hidden)


def _child_event(event: str, life: Life) -> Callable[[Path], tuple[int, str]] | None:
    from jobs import resume as resume_mod

    return {
        "cap_hold": ended("held", "map", "per_job_cap_usd: the run's projected spend passed the job's cap"),
        # The driver's first paid check holds it on its whole price, which
        # rides beside the reason as it does on the seat (``Outcome.price_cents``).
        "cycle_hold": _priced_hold(
            "monthly_budget_usd: this run's projected cost added to the month's spend to date would exceed "
            "the monthly cost budget; the package was not started before vision"
        ),
        "deploy_stop": stopped(life, -15),
        "machine_stop": stopped(life, resume_mod.CHECKPOINT_EXIT),
        "upload_readback_mismatch": delivered({**PROOF, "readback": "mismatch"}),
    }.get(event)


#: In ``running`` the event happens while the child runs, except these, which
#: act on the lane itself and so wait until the child has returned (a lane is
#: one thread: nothing ticks it while its child runs).
AFTER_THE_CHILD = ("claim", "reply_send_failure", "expiry")


def apply_event(life: Life, state: str, jid: str, event: str) -> None:
    """The event, then ticks until the lane is quiet."""
    if event.startswith("exit."):
        _, flavor, stage, code = event.split(".")
        outcome, reason = next((o, r) for f, s, c, o, r in exit_classes() if (f, s, c) == (flavor, stage, int(code)))
        if outcome == "current":
            life.children.append(delivered({**PROOF, "files": PROOF["files"][:1]}))
        else:
            life.children.append(ended(outcome, stage, reason, exit_code=int(code)))
        life.tick()
        life.settle()
        return
    if event in WIPE_EVENTS:
        _bound_event(life, state, jid, event)
        return
    if event in PRICE_EVENTS:
        _price_event(life, state, jid, event)
        return
    if event == "broker_errors_once":
        _error_ending(life, jid, times=1)
    act = _act(life, jid, event)
    child = _child_event(event, life)
    if child is not None:
        life.children.append(child)
    if state == "running":
        hidden: list[Path] = []
        if event == "broker_down":
            life.during.append(lambda: hidden.append(broker_down(life.seat)))
        elif act is not None and event not in AFTER_THE_CHILD:
            life.during.append(act)
        life.tick()
        for h in hidden:
            broker_up(life.seat, h)
        if act is not None and event in AFTER_THE_CHILD:
            act()
    else:
        if act is not None:
            act()
        if child is not None:
            life.tick()
    life.lane.stopping = lambda: False
    if state == "parked" and event != "expiry":
        _sticky(life, "OK")
    life.settle()


def _priced_hold(reason: str = PRICE_HOLD) -> Callable[[Path], tuple[int, str]]:
    """The child held before its first paid stage on its whole-job price, $0
    spent, the price riding beside the reason (``Outcome.price_cents``)."""

    def make(jd: Path) -> tuple[int, str]:
        _verdict(jd, "held", stage="vision", reason=reason, dollars=0.0, price_cents=PRICE_CENTS)
        return 3, ""

    return make


def _price_event(life: Life, state: str, jid: str, event: str) -> None:
    """The job runs into its price hold (if it runs at all from ``state``),
    then SMD decides it with the real commands, run as root on the seat would
    (``jobs.medchron.allowance``, the lane as its daemon object). A refusal is
    an act SMD hears at once."""
    from jobs.medchron import allowance

    life.children.append(_priced_hold())
    if state == "parked":
        _sticky(life, "OK")
    life.tick()
    life.lane.stopping = lambda: False
    life.settle()
    try:
        if event == "price_decline":
            allowance.apply_decline(life.lane, allowance.plan_decline(life.lane, jid, by="SMD Example"))
            life.acts.append(("allowance decline", "accepted"))
        else:
            # What runs after the approval: to delivery, or to the ceiling.
            after = delivered() if event == "price_approve" else ended("held", "map", CEILING_STOP)
            p = allowance.plan_approve(life.lane, jid, by="SMD Example", pages=None, usd=APPROVED_USD)
            life.children.insert(0, after)
            allowance.apply_approve(life.lane, p)
            life.acts.append(("allowance approve", "accepted"))
    except allowance.Refused:
        life.acts.append(("allowance decline" if event == "price_decline" else "allowance approve", "refused"))
    life.settle()


def _error_ending(life: Life, jid: str, *, times: int) -> None:
    """The broker answers the job's ENDING record ``internal_error`` (its
    ledger raised, as a locked database does) ``times`` times, then takes it.
    Every other record is taken, the dead-row sweep's close among them."""
    import sqlite3 as _sqlite

    verbs = life.run.broker.medchron
    real = verbs._record
    left = {"n": times}

    def record(request: dict[str, Any]) -> dict[str, Any]:
        run = (request.get("fields") or {}).get("run") or {}
        ending = run.get("event") in ("end", "stop") and run.get("code") != "ending_unrecorded"
        if request.get("job_id") == jid and ending and left["n"] > 0:
            left["n"] -= 1
            raise _sqlite.OperationalError("database is locked")
        return real(request)

    verbs._record = record
    life.heal = lambda: left.update(n=0)


def _told(life: Life, jid: str) -> int:
    bodies = {
        json.dumps(p["body"], sort_keys=True)
        for i, p in enumerate(life.seat.posts)
        if jid in json.dumps(p["body"]) and i not in life.refused_posts
    }
    return sum("Outcome: delivered." in b for b in bodies)


def _bound_event(life: Life, state: str, jid: str, event: str) -> None:
    """One bound, held through the job's run (or its next run) and past its
    wipe window: the job must still end owned, its firm told at most once, and
    its workdir wiped on schedule."""
    from jobs import retention
    from jobs.medchron import unrecorded

    hidden: list[Path] = []
    first = len(life.seat.posts)
    if event == "broker_errors_ending":
        _error_ending(life, jid, times=10**6)
    elif event == "paused_past_wipe_window":
        life.seat.gate_status = 503
    elif event == "no_wake_secret":
        life.lane.wake_secret = None
    if event == "broker_away_past_bound":
        if state == "running":
            life.during.append(lambda: hidden.append(broker_down(life.seat)))
        else:
            hidden.append(broker_down(life.seat))
    life.tick()
    life.lane.stopping = lambda: False
    if state == "parked":
        _sticky(life, "OK")
    for _ in range(unrecorded.MAX_ATTEMPTS + 1 if hidden else 0):
        life.seat.now["t"] += unrecorded.RETRY_SECONDS + 1
        life.tick()
    life.told_while_away = _told(life, jid)
    held = _state(life, jid)
    life.ended_while_away = bool(hidden) and bool(held.get("record_pending"))
    for h in hidden:
        broker_up(life.seat, h)
    life.settle()
    life.seat.now["t"] += (life.lane.held_wipe_hours + retention.WAKE_WIPE_GRACE_HOURS + 1) * 3600
    life.tick()
    life.tick()
    if event == "paused_past_wipe_window":  # every post of the paused window was answered 503
        life.refused_posts.update(range(first, len(life.seat.posts)))
    life.seat.gate_status = 200


def _act(life: Life, jid: str, event: str) -> Callable[[], Any] | None:
    acts: dict[str, Callable[[], Any]] = {
        "claim": lambda: life.tick(),
        "broker_down": lambda: _broker_down(life),
        "reply_send_failure": lambda: _reply_failure(life),
        "resume": lambda: _resume(life, jid),
        "redo": lambda: _resume(life, jid, redo=["map"]),
        "release": lambda: _resume(life, jid, release_review=True),
        "audit_decide": lambda: _marker(life, jid, "decide_audit", []),
        "coverage_decide": lambda: _marker(life, jid, "decide_coverage", []),
        "identity_decide": lambda: _marker(life, jid, "decide_identity", ["build_units"]),
        "update_decide": lambda: _marker(life, jid, "decide_update", []),
        "redo_doc": lambda: _marker(life, jid, "redo_doc", ["build_doc"]),
        "hand_delivery": lambda: _hand(life, jid),
        "expiry": lambda: _expire(life),
        "resubmit_while_running": lambda: _resubmit(life, jid),
        "queue_behind_same_lane": lambda: life.acts.append(("second_job", life.submit("2026-PI-205", "m-2"))),
        "resubmit_same_person": lambda: _resubmit_person(life, jid),
        "second_client_same_matter": lambda: _second_client(life, jid),
    }
    return acts.get(event)


def _kept_units(life: Life, jid: str) -> None:
    """The units the submit verb keeps (``medchron_identity.remember``):
    ``Life.submit`` writes the row straight to the ledger, which skips it."""
    from workspace_broker import medchron_identity

    medchron_identity.remember(life.run.broker.medchron._db, jid, _envelope("2026-PI-102", "m-1"))


def _resubmit_person(life: Life, jid: str) -> None:
    """The same person, spelled another way and asked with other words, through
    the real submit verb: refused naming the live job while it is active."""
    _kept_units(life, jid)
    env = _envelope("2026-PI-102", "m-1")
    env["units"] = [{**env["units"][0], "client_name": "Alpha  B. Example", "surname": "EXAMPLE"}]
    env["injuries"] = "neck and back, as asked again"
    reply = life.client()._request({"action": "medchron_job_submit", "envelope": env})
    if not reply.get("accepted") and reply.get("job_id") not in (None, jid):
        raise AssertionError(f"the refusal named job {reply.get('job_id')}, not the live job {jid}")
    life.acts.append(("medchron_job_submit", "accepted" if reply.get("accepted") else "refused"))


def _second_client(life: Life, jid: str) -> None:
    """Another client on the same matter, through the real submit verb: its
    own job, whatever state the first client's is in."""
    _kept_units(life, jid)
    env = _envelope("2026-PI-102", "m-1")
    unit = {"unit": "beta", "client_name": "Beta Sample", "name_token": "Sample", "surname": "Sample"}
    env["units"] = [{**unit, "dob": "05/06/1990"}]
    reply = life.client()._request({"action": "medchron_job_submit", "envelope": env})
    if reply.get("accepted"):
        life.acts.append(("second_job", str(reply["job_id"])))
    else:
        life.acts.append(("medchron_job_submit", "refused"))


def _expire(life: Life) -> None:
    life.seat.now["t"] += (life.lane.held_wipe_hours + 1) * 3600
    life.tick()


# ---------------------------------------------------------------------------
# Reading the ending back
# ---------------------------------------------------------------------------


def _audit(life: Life) -> list[dict[str, Any]]:
    conn = sqlite3.connect(life.seat.audit_db)
    try:
        rows = conn.execute("SELECT action_type, metadata FROM audit_log ORDER BY rowid").fetchall()
    finally:
        conn.close()
    return [{"action_type": a, "metadata": json.loads(m or "{}")} for a, m in rows]


def _state(life: Life, jid: str) -> dict[str, Any]:
    p = life.lane.job_dir(jid) / "daemon.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def _way_out(life: Life, jid: str, row: dict[str, Any], told: str, messages: int, pages: list[str]) -> list[str]:
    """Every verb that would accept this row now, asked of the real code
    (a resume is asked and its marker withdrawn; a fresh submission is asked
    of the ledger's own duplicate rule, which writes nothing)."""
    from jobs.proof import holds

    state = row.get("state")
    out: list[str] = []
    st = _state(life, jid)
    if state == "running" and isinstance(st.get("record_pending"), dict):
        # An ending the broker has not taken yet, its workdir still there: the
        # way out is the broker accepting the retried TRUE ending, asked of the
        # real code after SMD's repair (last: it writes the row). Meanwhile a
        # submission of the same work is refused as already running as this
        # job, which is true to the ledger (the page says so).
        if life.run.broker.medchron._db.active_duplicate(_envelope("2026-PI-102", "m-1")) != (jid, "running"):
            return []
        if life.heal is not None:
            life.heal()
        life.lane.record_retry_at = 0.0
        life.lane.poll_requests()
        moved = (life.client().status(jid) or {}).get("state") != "running"
        return ["the broker accepts the retried true ending"] if moved else []
    true = _wiped_ending(life, jid)
    if state == "running" and true and not life.lane.job_dir(jid).exists():
        # The known limit (``unrecorded.at_wipe``): wiped with its ending still
        # unrecorded, the row stays running and nothing retries it. The page
        # names the true ending; SMD corrects the row by hand with the root
        # record verb (asked here, last, because it writes the row).
        if life.run.broker.medchron._db.active_duplicate(_envelope("2026-PI-102", "m-1")) != (jid, "running"):
            return []
        if life.heal is not None:
            life.heal()
        try:
            life.client().record(jid, true, {})
        except Exception:  # noqa: BLE001 - a refusal is the answer
            return []
        return ["SMD corrects the ledger row by hand from the page"]
    if state == "delivered":
        if messages:
            return ["done"]
        if "wake_lost" in pages and not life.lane.job_dir(jid).exists():
            # Wiped on schedule with its message never sent: the page names the
            # job and says the firm was not told; SMD tells them.
            return ["SMD tells the firm (paged wake_lost)"]
        from jobs import rewake

        return ["jobs rewake"] if rewake.rewake(life.seat.run_dir, "medchron", jid, "smd", "matrix", False) == 0 else []
    queued = (life.lane.queue / f"{jid}.json").exists()
    claimed = bool(st.get("claimed")) and st.get("state") not in ("delivered", "failed")
    if state in ("submitted", "running") and (queued or claimed):
        return ["tick"]
    reason = str(row.get("reason") or "")
    if state == "held" and reason.startswith("seat paused"):
        out.append("sticky_release")
    try:
        life.client()._request({"action": "medchron_job_resume", "job_id": jid, "reason": "probe", "redo": []})
        from jobs.resume import MARKER_PREFIX

        (life.lane.queue / f"{MARKER_PREFIX}{jid}.json").unlink(missing_ok=True)
        # The broker queues a marker; the daemon acts on it only while the job's
        # envelope is still on the seat (resume.take_requests), so only then is
        # the resume a way out.
        if (life.lane.job_dir(jid) / "envelope.json").is_file():
            out.append("medchron_job_resume")
    except Exception:  # noqa: BLE001 - a refusal is the answer
        pass
    db = life.run.broker.medchron._db
    if db.active_duplicate(_envelope("2026-PI-102", "m-1")) is None:
        out.append("medchron_job_submit")
    if state == "held" and holds.classify("medchron", reason)[0] == holds.REROUTED_TO_UPDATE:
        # The DELIVER turn resubmits the refused RUN as the UPDATE, a different
        # piece of work, so the duplicate rule must let it through.
        if db.active_duplicate({**_envelope("2026-PI-102", "m-1"), "update": {"mode": "append"}}) is None:
            out.append("medchron_job_submit (update)")
    if state == "held":
        code = holds.classify("medchron", reason)[1] or ""
        out += _limit_way_out(life, jid) if code == "limit_held" else DECIDE_FOR.get(code, ())
    return sorted(set(out))


def _limit_way_out(life: Life, jid: str) -> list[str]:
    """A limit hold's decision, asked of the real command (a plan writes
    nothing): ``approve --usd`` at the alert's figure (or one cent above the
    job's spend where it prints none) on a cost hold, ``approve`` on a page
    allowance hold. Every cost hold is approvable: a cap is a brake SMD lifts."""
    from jobs import verdict as verdict_mod
    from jobs.medchron import allowance

    fig = allowance.cost_figures(verdict_mod.read(life.lane.job_dir(jid), ""))
    usd = fig["approve_usd"] or (int(fig["spent_cents"] or 0) + 1) / 100
    try:
        allowance.plan_approve(life.lane, jid, by="SMD Example", pages=None, usd=usd)
        return ["allowance approve --usd"]
    except allowance.Refused:
        pass
    try:
        allowance.plan_approve(life.lane, jid, by="SMD Example", pages=None)
    except allowance.Refused:
        return []
    return ["allowance approve"]


def _wiped_ending(life: Life, jid: str) -> str | None:
    """The true ending the wipe's ``ending_unrecorded`` page names, if any."""
    for a in _audit(life):
        md = a["metadata"]
        detail = str(md.get("detail") or "")
        if md.get("job_id") == jid and md.get("code") == "ending_unrecorded" and detail.startswith("true ending "):
            return detail.removeprefix("true ending ").split(";", 1)[0]
    return None


#: Who performs each act the matrix drives, so a refused act is owned by the
#: one who asked (the verb answers them in the same call).
ACTOR = {
    "medchron_job_resume": "smd",
    "medchron_job_submit": "firm",
    "deliver_by_smd": "smd",
    "allowance approve": "smd",
    "allowance decline": "smd",
}


#: The ``*_decide`` tool that answers each hold's page code (``proof/holds.py``),
#: read from each tool's own docstring and refusals.
DECIDE_FOR: dict[str, tuple[str, ...]] = {
    "gate_held": ("audit_decide", "coverage_decide"),
    "identity_held": ("identity_decide",),
    "update_held": ("update_decide",),
    "upload_held": ("deliver_by_smd",),
    "stage_refused": ("redo_doc",),
}


def observe(life: Life, jid: str) -> dict[str, Any]:
    """The ending, graded."""
    from jobs.proof import holds

    row = life.client().status(jid) or {}
    st = _state(life, jid)
    audit = _audit(life)
    mine = [a for a in audit if a["metadata"].get("job_id") == jid]
    posted = {
        json.dumps(p["body"], sort_keys=True)
        for i, p in enumerate(life.seat.posts)
        if jid in json.dumps(p["body"]) and i not in life.refused_posts
    }
    messages = sum(1 for p in posted if "Outcome: delivered." in p)
    filed = sum(1 for a in mine if a["action_type"] == "DELIVERY_PROVEN")
    pages = sorted({a["metadata"].get("code") for a in mine if a["action_type"] == "DELIVERY_PROOF_FAILED"})
    state = str(row.get("state") or "gone")
    reason = str(row.get("reason") or "")
    refused = [verb for verb, how in life.acts if how == "refused"]
    if refused:
        ending, code = "refused", refused[0]
    elif state in ("submitted", "running"):
        ending, code = "running", None
    else:
        from jobs import run_record

        recorded = (_entered([a for a in _audit(life) if a["metadata"].get("job_id") == jid], state) or {}).get("run")
        ending, code = state, None
        if state != "delivered":
            code = (recorded or {}).get("code") or run_record.code_of(reason)
    unrecorded = "ending_unrecorded" in pages
    told = (st.get("state") or ("delivered" if messages else state)) if unrecorded else state
    way_out = _way_out(life, jid, row, told, messages, pages)
    if refused:
        owner = ACTOR[refused[0]]  # the one who asked hears the refusal at once
    elif unrecorded:
        owner = "smd"  # paged ending_unrecorded: the broker did not take the ending in its bound
    elif state == "delivered":
        owner = "firm" if messages else "smd"
    elif state in ("submitted", "running"):
        owner = "code"
    elif state == "held" and holds.classify("medchron", reason)[0] == holds.REROUTED_TO_UPDATE:
        owner = "code"  # the DELIVER turn resubmits it; proof/watch.check_reroutes pages when it does not
    elif state == "held" and not reason.startswith("seat paused"):
        owner = "firm" if holds.classify("medchron", reason)[0] in holds.FIRM_QUESTION else "smd"
    else:
        owner = "smd"
    entered = _entered(mine, state)
    return {
        "ending": ending,
        "code": code,
        "state": state,
        "owner": owner,
        "paged": pages,
        "way_out": way_out,
        "firm_messages": messages,
        "filed_sets": filed,
        "run": (entered or {}).get("run"),
        "second": _second(life),
        "firm_owed": 1 if told == "delivered" else 0,
        "wiped": not life.lane.job_dir(jid).exists(),
        "told_while_away": life.told_while_away,
        "ended_while_away": life.ended_while_away,
        "record_late": any((a["metadata"].get("run") or {}).get("event") == "record_late" for a in mine),
    }


def _entered(rows: list[dict[str, Any]], state: str) -> dict[str, Any] | None:
    """The metadata of the audit row that put the job in ``state`` (the first
    row of the last run of rows in that state)."""
    want = f"MEDCHRON_JOB_{state.upper()}"
    found = None
    prev = None
    for a in rows:
        kind = a["action_type"]
        if not kind.startswith("MEDCHRON_JOB_"):
            continue
        if kind == want and prev != want:
            found = a["metadata"]
        prev = kind
    return found


def _second(life: Life) -> dict[str, Any] | None:
    """The second job's ending, for ``queue_behind_same_lane``."""
    second = next((how for verb, how in life.acts if verb == "second_job"), None)
    if second is None:
        return None
    row = life.client().status(second) or {}
    posted = [p for p in life.seat.posts if second in json.dumps(p["body"])]
    return {
        "state": row.get("state"),
        "firm_messages": sum("Outcome: delivered." in json.dumps(p["body"]) for p in posted),
    }


def run_cell(tmp_path: Path, mp: Any, cell: str) -> dict[str, Any]:
    state, event = cell.split("/", 1)
    life, server = build(tmp_path, mp)
    try:
        with server as port:
            life.run.build(port)
            jid = reach(life, state)
            apply_event(life, state, jid, event)
            return observe(life, jid)
    finally:
        teardown(life)


# ---------------------------------------------------------------------------
# The assertions (a)-(e), one function so the planted-defect tests reuse them
# ---------------------------------------------------------------------------

ENDINGS = frozenset({"delivered", "held", "failed", "running", "refused"})


def problems(cell: str, got: dict[str, Any]) -> list[str]:
    """What is wrong with one ending (empty: nothing)."""
    out: list[str] = []
    if got["ending"] not in ENDINGS:
        out.append(f"ending {got['ending']!r} is not one of {sorted(ENDINGS)}")
    if got["ending"] in ("held", "failed", "refused") and not got["code"]:
        out.append("a held, failed or refused ending names no code")
    # (a) an owner, and an SMD-owned stop is told to SMD
    if got["owner"] not in ("firm", "smd", "code"):
        out.append(f"(a) no owner: {got['owner']!r}")
    smd_stop = got["owner"] == "smd" and (
        got["state"] in ("held", "failed") or got["firm_owed"] != got["firm_messages"]
    )
    # A seat pause is SMD's own act on the whole seat, paged where it was set.
    if smd_stop and not got["paged"] and got.get("code") != "seat_paused":
        out.append("(a) SMD owns this stop and was never paged")
    # (b) a way out
    if not got["way_out"]:
        out.append("(b) no verb accepts this row: the ending has no way out")
    # (c) the firm hears the declared count, never two
    declared = got["firm_owed"]
    if got["firm_messages"] > 1:
        out.append(f"(c) the firm got {got['firm_messages']} messages for one job")
    elif got["firm_messages"] != declared and not (declared and got["paged"]):
        out.append(f"(c) the firm got {got['firm_messages']} message(s); the declared count is {declared}")
    second = got.get("second")
    if second is not None and second.get("firm_messages", 0) > 1:
        out.append("(c) the job queued behind got more than one message")
    # (d) one filed set at most
    if got["filed_sets"] > 1:
        out.append(f"(d) {got['filed_sets']} filed sets for one job")
    # (f) a bound's ending leaves the job dir wiped on schedule
    if cell.rpartition("/")[2] in WIPE_EVENTS and not got["wiped"]:
        out.append("(f) the job dir outlived its wipe window")
    # (g) an outage past its bound never keeps the firm waiting for the broker
    if got.get("ended_while_away") and got["firm_owed"] and not got["told_while_away"]:
        out.append("(g) the firm was told only after the broker came back, past the bound")
    # (h) a broker error the lane's retry rides out inside its bound is no page
    # and leaves the run unaided (``jobs/medchron/unrecorded.py``)
    if cell.rpartition("/")[2] == "broker_errors_once" and ("ending_unrecorded" in got["paged"] or got["record_late"]):
        out.append("(h) one broker error inside the bound paged SMD or graded the run aided")
    # (i) the same person asked again while a job is active is refused by
    # that job (the act raises when the refusal names another); another
    # client on the matter is its own job, accepted
    event = cell.rpartition("/")[2]
    if event == "resubmit_same_person" and got["state"] in ("submitted", "running", "held"):
        if got["ending"] != "refused":
            out.append("(i) the same person was queued twice while a job was active")
    if event == "second_client_same_matter" and got.get("second") is None:
        out.append("(i) another client on the matter was refused")
    # (j) the whole job's price (2026-11-20). From a state that runs the job,
    # a price hold or a cycle hold ends held on that setting with nothing sent;
    # SMD's dollar approval runs it to its one delivery, or stops it held at the
    # ceiling; SMD's decline ends it failed. The firm hears nothing but a delivery.
    runs = cell.partition("/")[0] in ("submitted", "running")
    want = {
        "cycle_hold": ("held", "monthly_budget_usd", 0),
        "price_approve": ("delivered", None, 1),
        "price_approve_ceiling": ("held", "per_job_cap_usd", 0),
        "price_decline": ("failed", None, 0),
    }.get(event)
    if runs and want is not None:
        ending, code, told = want
        if got["ending"] != ending or (code is not None and got["code"] != code) or got["firm_messages"] != told:
            out.append(
                f"(j) {event} ended {got['ending']} ({got['code']}, {got['firm_messages']} message(s)); "
                f"want {ending} ({code or 'any code'}, {told})"
            )
    # (e) the row that put the job in its state carries a run block
    if got["state"] not in ("submitted", "gone") and not isinstance(got["run"], dict):
        out.append("(e) the row that ended the job carries no run block")
    return out


__all__ = [
    "FIXED_EVENTS",
    "GOLDEN",
    "NOT_APPLICABLE",
    "cells",
    "exit_classes",
    "problems",
    "run_cell",
    "states",
]


def _write() -> int:
    """Every cell under ``JOBS_WRITE_LIFECYCLE_GOLDENS`` (parallel), then the combine."""
    import os
    import subprocess
    import sys

    base = [
        sys.executable,
        "-m",
        "pytest",
        "-c",
        "runners/jobs/pyproject.toml",
        "runners/jobs/tests/test_lifecycle_matrix.py",
        "-q",
    ]
    env = {**os.environ, "JOBS_WRITE_LIFECYCLE_GOLDENS": "1"}
    cells_run = subprocess.run([*base, "-n", "4", "-k", "test_a_cell"], env=env, check=False)  # noqa: S603 - this interpreter, literal args
    if cells_run.returncode:
        return cells_run.returncode
    return subprocess.run([*base, "-k", "test_write_the_golden"], env=env, check=False).returncode  # noqa: S603 - this interpreter and literal pytest args, as above


if __name__ == "__main__":
    import sys as _sys

    if "--write" not in _sys.argv:
        raise SystemExit("usage: python runners/jobs/tests/lifecycle_matrix.py --write   (from operator/)")
    raise SystemExit(_write())
