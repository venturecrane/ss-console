#!/usr/bin/env python3
"""date-prep-brief pre-run gate: pick ONE date to prepare for, derive its file
status and the closed decision catalog, and hand the woken turn both.

Case-manager spec Job 2 (``docs/specs/operator/case-manager-deadline-work.md``
§4): a court date or deadline entering its window starts the prep work, not an
email. This gate does every part of that code can do:

1. Reads the firm's authored ``case_manager.date_prep`` block. Absent, or a
   level the gate does not know: the job is off, and the tick is a
   ``SUPPRESSED_WAKE`` heartbeat (ADR 0035, no imposed default).
2. Pulls the calendar events in ``[today, today + window_days]`` per open
   matter (``file_status.pull_dates``, connector venv).
3. Drops every date the casework ledger already holds a ``briefed`` row for,
   and every date this seat already woke for today. A date is briefed once.
4. Takes the nearest remaining date per matter and, for up to
   :data:`MAX_MATTERS_TRIED` matters nearest first, resolves the matter's
   staff (responsible attorney to, assisting staff cc; ``matter_staff``
   routing and roster grants only), reads the file status, and builds the
   catalog. The first matter that routes AND has a step to offer wins.
5. Writes the brief envelope ``casework_brief`` reads, the provenance
   handoff (the dates the turn may render), and a woken-today marker, then
   wakes the turn with the facts on the gate line.

ONE MATTER PER TICK. The overlay's matter-mixing fence refuses a second
matter's content in one session, so the brief's matter is chosen here and the
turn reads only that one. The cron fires hourly across the morning, so a seat
with four dates entering the window gets four briefs, one per tick.

FAIL DIRECTION. This routine adds preparation; it is not the deadline alarm.
The deadline-miss-escalator keeps its backstop for any court date whose
decisions go unanswered, so every failure here (config unreadable, a pull that
fails, a ledger that cannot be read, a staff pull that returns nothing)
SUPPRESSES with a heartbeat row naming the reason, rather than waking a turn
that has nothing it may do. The row is the trace; the escalator is the alarm.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SKILL = "date-prep-brief"
MAX_MATTERS_TRIED = 3
LEVELS = ("surfaces", "prepares", "handles")
_CONNECTOR_PYTHON_DEFAULT = "/opt/connectors/smokeball/.venv/bin/python"
_PULL_TIMEOUT_SECONDS = 90
_HEARTBEAT_TIMEOUT_SECONDS = 10
_STARTED_AT = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

# Loads file_status.py in the connector venv and runs ONE of its two reads.
# The query rides stdin, never argv (argv[1] is the module path only).
_PULL_SNIPPET = """\
import importlib.util
import json
import sys

spec = importlib.util.spec_from_file_location("date_prep_file_status", sys.argv[1])
fs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fs)

from smokeball_connector.client import build_client_from_env

req = json.load(sys.stdin)
client = build_client_from_env()
if req.get("op") == "dates":
    out = fs.pull_dates(client, req["from"], req["to"])
else:
    out = {"status": fs.pull_matter_status(client, req["matter_id"])}
print(json.dumps(out, default=str))
"""


def _sibling_path(filename: str) -> Path | None:
    """A file shipped in this skill's directory, wherever the seat staged it."""
    for base in (Path(__file__).resolve().parent, Path("/opt/data/skills") / SKILL, Path("/app/skills") / SKILL):
        if (base / filename).is_file():
            return base / filename
    return None


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


_SKILL_DIRNAME = "date-prep-brief"
_H = _load_skill_helpers()
_CATALOG = _H.load_sibling(SKILL, __file__, "catalog.py", "date_prep_catalog")
_CASEWORK = _H.load_sibling(SKILL, __file__, "casework_ledger.py", "date_prep_casework_ledger")
_ESCALATION = _H.load_sibling(SKILL, __file__, "escalation_ledger.py", "date_prep_escalation_ledger")
_ROUTING = _H.load_sibling(SKILL, __file__, "routing.py", "date_prep_routing")
_BRIEF = _H.load_sibling(SKILL, __file__, "brief_envelope.py", "date_prep_brief_envelope")
_SINCE = _H.load_sibling(SKILL, __file__, "done_since.py", "date_prep_done_since")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def date_prep_config(cfg: dict) -> dict | None:
    """The authored ``case_manager.date_prep`` block, or None when the job is off."""
    block = cfg.get("case_manager") if isinstance(cfg, dict) else None
    prep = block.get("date_prep") if isinstance(block, dict) else None
    if not isinstance(prep, dict) or prep.get("level") not in LEVELS:
        return None
    window = prep.get("window_days")
    if isinstance(window, bool) or not isinstance(window, int) or window < 1:
        return None
    steps = prep.get("steps") if isinstance(prep.get("steps"), dict) else {}
    stale = _H.pos_int_or_none(prep.get("records_stale_days"))
    return {"level": prep["level"], "window_days": window, "records_stale_days": stale, "steps": steps}


# ---------------------------------------------------------------------------
# The pure decision: which dates are candidates, nearest first
# ---------------------------------------------------------------------------


def date_item_key(event: dict) -> str | None:
    try:
        return _CASEWORK.item_key(matter_id=event.get("matter_id"), kind="date", source_id=event.get("event_id"))
    except ValueError:
        return None


def is_briefed(state) -> bool:
    return bool(state) and any(d.raise_event == "briefed" for d in state.decisions.values())


def candidates(events: list[dict], states: dict, woken: dict, today: date, window_days: int) -> list[dict]:
    """The nearest unbriefed, not-yet-woken date per matter, nearest first."""
    last = today + timedelta(days=window_days)
    best: dict[str, dict] = {}
    for event in events:
        key = date_item_key(event)
        day = _H.parse_iso_date(event.get("date"))
        if key is None or day is None or not today <= day <= last:
            continue
        if is_briefed(states.get(key)) or woken.get(key) == today.isoformat():
            continue
        row = {**event, "item_key": key, "days_out": (day - today).days}
        current = best.get(event["matter_id"])
        if current is None or (row["date"], row["event_id"]) < (current["date"], current["event_id"]):
            best[event["matter_id"]] = row
    return sorted(best.values(), key=lambda r: (r["date"], r.get("matter_number") or "", r["event_id"]))


def chase_states(records: list[dict], matter_id: str, events: list[dict]) -> dict:
    """The records chaser's ledger state per roster task: attempts and last chase day."""
    states = _ESCALATION.derive_state(events)
    out = {}
    for record in records:
        key = _ESCALATION.item_key(matter_id, record["roster_task_id"], None, None)
        state = states.get(key)
        last = state.last_raised_date.isoformat() if state and state.last_raised_date else None
        out[record["roster_task_id"]] = {"attempts": state.attempts if state else 0, "last_chased": last}
    return out


def chase_cadence(cfg: dict) -> int | None:
    return _H.pos_int_or_none(_H.find_skill_settings(cfg, "medical-records-chaser").get("chase_cadence_days"))


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def connector_pull(query: dict) -> dict | None:
    """One connector-venv read. None on any failure (the caller suppresses)."""
    module = _sibling_path("file_status.py")
    python = os.environ.get("SMD_CONNECTOR_VENV_PYTHON", _CONNECTOR_PYTHON_DEFAULT)
    if module is None or not Path(python).exists():
        return None
    try:
        result = subprocess.run(  # noqa: S603 - connector-venv interpreter, a module-constant snippet and this skill's own file path; the query rides stdin
            # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args — argv[0] is the module-constant connector-venv interpreter, overridable only via SMD_CONNECTOR_VENV_PYTHON from the Machine's own boot env (the test seam every pre_run pull carries). argv[2] is a module constant and argv[3] this skill's own file path. Pulled data rides stdin, never argv, and there is no shell.
            [python, "-c", _PULL_SNIPPET, str(module)],
            input=json.dumps(query),
            capture_output=True,
            text=True,
            timeout=_PULL_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            sys.stderr.write("[pre_run] date-prep pull exit " + str(result.returncode) + "\n")
            return None
        out = json.loads((result.stdout or "").strip().splitlines()[-1])
        return out if isinstance(out, dict) else None
    except Exception as exc:  # noqa: BLE001 - any pull failure suppresses with a named heartbeat
        sys.stderr.write("[pre_run] date-prep pull failed: " + type(exc).__name__ + "\n")
        return None


def heartbeat(verb: str, action_type: str, basis: str) -> bool:
    """One wake row through the broker's uid-gated verb. True only on ack."""
    socket_path = os.environ.get("SMD_AUDIT_BROKER_SOCKET") or os.environ.get("SMD_WORKSPACE_BROKER_SOCKET")
    if not socket_path:
        return False
    metadata = {"decision_basis": basis, "platform": "cron-pre-run", "customer": os.environ.get("CUSTOMER_SLUG", "")}
    row = {"action_type": action_type, "actor": "agent", "actor_role": "agent", "skill_name": SKILL}
    row["metadata"] = json.dumps(metadata, sort_keys=True, separators=(",", ":"))
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(_HEARTBEAT_TIMEOUT_SECONDS)
            sock.connect(socket_path)
            sock.sendall(json.dumps({"action": verb, "row": row}).encode("utf-8") + b"\n")
            raw = b""
            while not raw.endswith(b"\n"):
                chunk = sock.recv(4096)
                if not chunk:
                    break
                raw += chunk
        return json.loads(raw.decode("utf-8")).get("ok") is True
    except Exception as exc:  # noqa: BLE001 - a heartbeat failure is reported, never raised
        sys.stderr.write("[pre_run] heartbeat failed: " + type(exc).__name__ + "\n")
        return False


def suppress(basis: str) -> int:
    """Suppress with a named heartbeat. A failed heartbeat still suppresses:
    waking a turn with no brief to send is not a trace, it is a cost, and the
    escalator backstop covers the date either way."""
    heartbeat("suppressed_wake_append", "SUPPRESSED_WAKE", basis)
    return _H.emit_suppress()


def wake(facts: dict) -> int:
    heartbeat("emitted_wake_append", "EMITTED_WAKE", "date_in_window:brief_prepared")
    print(json.dumps({"wakeAgent": True, "date_prep": facts}))
    return 0


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def prepare(candidate: dict, cfg: dict, prep: dict, staff: dict, ledger_events: list, today: date) -> dict | None:
    """One candidate date to a brief plan, or None (unroutable, unread, or nothing to offer)."""
    recipients = _BRIEF.recipients(_ROUTING, cfg, staff.get(candidate["matter_id"]))
    if recipients is None:
        return None
    pulled = connector_pull({"op": "status", "matter_id": candidate["matter_id"]})
    status = pulled.get("status") if isinstance(pulled, dict) else None
    if not isinstance(status, dict):
        return None
    chases = chase_states(status.get("records") or [], candidate["matter_id"], ledger_events)
    catalog = _CATALOG.build_catalog(
        status,
        prep["steps"],
        chases,
        cadence_days=chase_cadence(cfg),
        today=today,
        stale_days=prep["records_stale_days"],
    )
    if not catalog:
        return None
    return {
        "candidate": candidate,
        "status": status,
        "chases": chases,
        "catalog": catalog,
        "recipients": recipients,
        "today": today.isoformat(),
    }


def quiet_authored(cfg: dict) -> bool:
    """``case_manager.quiet`` is authored at a known level (Job 3 is on)."""
    block = cfg.get("case_manager") if isinstance(cfg, dict) else None
    quiet = block.get("quiet") if isinstance(block, dict) else None
    return isinstance(quiet, dict) and quiet.get("level") in LEVELS


def add_done(plan: dict, cfg: dict, states: dict) -> None:
    """What the brief tells as already done, from records only (``done_since.py``).

    * Each ``handles`` catalog entry carries ``done_line``: the closed phrase
      for that step. When the turn runs the step and records it
      (``casework_step_done``), the brief lists it under Done in these words
      and marks it told; a step never recorded is never listed.
    * With ``quiet`` authored, ``done_since`` holds this matter's untold work
      from earlier runs (a close on the record's evidence, a step the Operator
      ran with no brief to carry it). Only this matter's: a brief is one
      matter's, and the fence refuses another's content in the session.

    Every day those lines render is added to the handoff (``seed_days``)."""
    catalog = _CATALOG.envelope_catalog(plan["catalog"])
    days: list = []
    rows: list = []
    if _SINCE is not None:
        for entry in catalog:
            phrase = _SINCE.step_phrase(entry) if entry.get("level") == "handles" else None
            if phrase:
                entry["done_line"] = phrase
                days += _SINCE.step_days(entry)
        if quiet_authored(cfg):
            candidate = plan["candidate"]
            rows = _SINCE.rows(_CASEWORK, states, {candidate["matter_id"]: candidate.get("matter_number")})
            days += [d for row in rows for d in row["_days"]]
    plan["envelope_catalog"] = catalog
    plan["done_since"] = [_SINCE.public(row) for row in rows] if _SINCE is not None else []
    plan["seed_days"] = [d.isoformat() for d in days]


def run(cfg: dict, today: date) -> int:
    prep = date_prep_config(cfg)
    if prep is None:
        return suppress("case_manager_unauthored:date_prep_off")
    if not any(level in ("prepares", "handles") for level in prep["steps"].values()):
        return suppress("case_manager:no_step_offered")
    if _CASEWORK is None or _ESCALATION is None or _ROUTING is None or _CATALOG is None or _BRIEF is None:
        return suppress("degraded:sibling_missing")
    to = today + timedelta(days=prep["window_days"])
    dates = connector_pull({"op": "dates", "from": today.isoformat(), "to": to.isoformat()})
    if not isinstance(dates, dict) or not isinstance(dates.get("events"), list):
        return suppress("degraded:date_pull_failed")
    states = _CASEWORK.derive_state(_CASEWORK.read_ledger())
    todo = candidates(dates["events"], states, _BRIEF.read_woken(), today, prep["window_days"])
    if not todo:
        return suppress("no_unbriefed_date_in_window")
    tried = todo[:MAX_MATTERS_TRIED]
    staff = _ROUTING.pull_matter_staff([c["matter_id"] for c in tried], _ROUTING.staff_lookup_budget(cfg))
    ledger_events = _ESCALATION.read_ledger()
    for candidate in tried:
        plan = prepare(candidate, cfg, prep, staff, ledger_events, today)
        if plan is not None:
            add_done(plan, cfg, states)
            if not _BRIEF.write_all(plan, SKILL, _STARTED_AT, plan["envelope_catalog"]):
                return suppress("degraded:envelope_write_failed")
            return wake(_BRIEF.facts(plan))
    return suppress("no_briefable_date:unroutable_or_nothing_to_offer")


def main(argv: list[str] | None = None) -> int:
    del argv
    today = datetime.now(timezone.utc).date()
    return run(_H.load_customer_yaml(None), today)


if __name__ == "__main__":
    sys.exit(main())
