"""SMD has the Operator tell a firm colleague something, in her own thread.

    python -m medchron.note_by_smd --recent [--match TEXT]
    python -m medchron.note_by_smd <graph_id> --text-file F --approved-by WHO --dry-run
    python -m medchron.note_by_smd <graph_id> --text-file F --approved-by WHO --confirm DIGEST [--wait-min 20]

Root only, on the seat, with the medchron venv's python and the runner daemon's
environment (``SMD_WORKSPACE_BROKER_SOCKET``, ``SMD_MEDCHRON_RUN_DIR``). Runbook:
``docs/runbooks/operator/smd-note.md``.

WHY (2026-10-15). The Operator filed a document on SMD's request and could not
tell the colleague who asked: a turn an inbound email opened may answer only
its sender, and the only way for SMD to have the Operator speak was a job's
completion wake. This is that wake for words SMD approved:

1. ``--recent`` lists the colleague emails this seat received lately (the
   overlay's request index, read only), so SMD can pick the request to answer.
2. ``--dry-run`` asks the broker's root-only ``note_preview``: the email's
   subject, sender and time, the exact CC list the reply will carry, the words,
   and a digest over all of it. It writes nothing. The broker refuses an email
   that is not a recent received request from someone the seat may answer,
   words with an em dash, and any identifier the email does not itself carry.
3. The live run repeats the digest (``--confirm``), so what is recorded is what
   SMD read. ``note_record`` writes the note (once per email and words); this
   command then wakes the smd-note skill's DELIVER mode with a code-only task
   (``note_lane.wake_task``), arms the daemon's reply watch (which pages SMD if
   the reply never reaches Sent Items, after this command has exited), and
   polls the note's reply up to ``--wait-min`` minutes.

THE WAKE SECRET (``WEBHOOK_SECRET_MCP``) is read in-process from this process's
environment, the runner daemon's, or the gateway's ``/proc/<pid>/environ``. It
is never printed, logged or written. When it cannot be found, or the gate cannot
be reached before sending, the wake is left PENDING in the note's job dir: the
note lane holds the secret and dispatches it on its next tick (paging
``wake_lost`` if it never can).

Exit codes: 0 the reply was sent and found in Sent Items (or a dry run / list
succeeded); 1 any other ending (printed); 2 refused (nothing was recorded).
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from . import note_lane
from .broker_client import BrokerClient, BrokerError
from .daemon import DEFAULT_GATE_URL, DEFAULT_RUN_DIR, GATE_URL_ENV, RUN_DIR_ENV, SOCKET_ENV, WAKE_SECRET_ENV
from .proof import watch as proof_watch

REQUEST_INDEX_ENV = "SMD_REQUEST_INDEX_DB_PATH"
DEFAULT_REQUEST_INDEX = "/opt/data/requests.db"
AUDIT_DB_ENV = "SMD_AUDIT_DB_PATH"
DEFAULT_AUDIT_DB = "/run/smd-audit/audit.db"
RECENT_DAYS = 14
POLL_SECONDS = 15.0


class Refused(RuntimeError):
    """The broker refused the note; nothing was recorded."""


# -- the secret ------------------------------------------------------------------


def _environ_of(path: Path) -> dict[str, str]:
    try:
        raw = path.read_bytes()
    except OSError:
        return {}
    out: dict[str, str] = {}
    for item in raw.split(b"\0"):
        key, sep, value = item.partition(b"=")
        if sep:
            out[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return out


def wake_secret(env: dict[str, str] | None = None, proc: Path = Path("/proc")) -> str:
    """The handoff bearer, from (in order) this process, the runner daemon, the
    gateway. "" when none holds it. NEVER print or log the value."""
    env = dict(os.environ) if env is None else env
    if env.get(WAKE_SECRET_ENV):
        return env[WAKE_SECRET_ENV]
    gateway = env.get("SMD_GATEWAY_PID", "")
    try:
        pids = sorted(p for p in proc.iterdir() if p.name.isdigit())
    except OSError:
        pids = []
    for pid in pids:
        try:
            cmdline = (pid / "cmdline").read_bytes()
        except OSError:
            continue
        if b"medchron.daemon" in cmdline:
            daemon_env = _environ_of(pid / "environ")
            gateway = gateway or daemon_env.get("SMD_GATEWAY_PID", "")
            if daemon_env.get(WAKE_SECRET_ENV):
                return daemon_env[WAKE_SECRET_ENV]
    for pid in [gateway, "1"]:
        if pid.isdigit():
            value = _environ_of(proc / pid / "environ").get(WAKE_SECRET_ENV, "")
            if value:
                return value
    return ""


# -- the request index --------------------------------------------------------------


def recent(db_path: str, *, match: str = "", now: float | None = None) -> list[dict[str, Any]]:
    """The seat's received requests of the last ``RECENT_DAYS`` days, newest first."""
    since = (now if now is not None else time.time()) - RECENT_DAYS * 86400.0
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
    try:
        rows = conn.execute(
            "SELECT vendor_message_id, received_at, sender, subject FROM requests "
            "WHERE received_at >= ? AND vendor_message_id IS NOT NULL ORDER BY received_at DESC",
            (since,),
        ).fetchall()
    finally:
        conn.close()
    want = match.lower()
    out = []
    for gid, at, sender, subject in rows:
        if want and want not in f"{sender or ''} {subject or ''}".lower():
            continue
        received = datetime.fromtimestamp(float(at), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out.append({"graph_message_id": gid, "received_at": received, "sender": sender, "subject": subject})
    return out


# -- the wake -----------------------------------------------------------------------


def post_wake(gate_url: str, secret: str, handoff_id: str, task: str) -> str:
    """The daemon's own POST (``Daemon._dispatch_wakes``): ``delivered``,
    ``timeout after send``, ``paused``, ``status N`` or ``unreachable (...)``."""
    url = urlparse(gate_url)
    body = json.dumps({"handoff_id": handoff_id, "task": task})
    sent = False
    try:
        conn = http.client.HTTPConnection(url.hostname or "127.0.0.1", url.port or 8643, timeout=15)
        try:
            conn.connect()
            sent = True
            conn.request(
                "POST",
                "/webhooks/handoff",
                body=body,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {secret}"},
            )
            resp = conn.getresponse()
            raw = resp.read()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - each shape maps to a wake decision, as in the daemon
        return "timeout after send" if sent else f"unreachable ({type(exc).__name__})"
    if 200 <= resp.status < 300:
        return "delivered"
    if resp.status == 503 and b"paused" in raw:
        return "paused"
    return f"status {resp.status}"


def _write_job(lane: Any, note_id: str, requester: str, ref: str, task: str) -> None:
    jd = lane.job_dir(note_id)
    jd.mkdir(parents=True, exist_ok=True)
    (jd / "envelope.json").write_text(json.dumps({"requested_by": requester, "request_ref": ref}), encoding="utf-8")
    lane._write_state(
        note_id,
        state="delivered",
        delivered_by="smd",
        finished_at=lane.clock(),
        wake={"pending": True, "attempts": 0, "task": task},
    )


def wake(lane: Any, note_id: str, secret: str, post: Callable[..., str] = post_wake) -> str:
    """Send the note's wake once; consume it and arm the reply watch exactly as
    the daemon does. A wake that never left stays pending for the note lane."""
    task = str((lane._daemon_state(note_id).get("wake") or {}).get("task") or "")
    if not secret:
        return "left pending: no wake secret readable here; the note lane sends it on its next tick"
    outcome = post(lane.gate_url, secret, f"{lane.LANE}-{note_id}", task)
    if outcome == "delivered" or outcome.startswith("timeout"):
        st = lane._daemon_state(note_id)
        w = dict(st.get("wake") or {})
        w.update(pending=False, sent_at=lane.clock(), outcome=outcome)
        lane._write_state(note_id, wake=w)
        proof_watch.arm_reply_watch(lane, note_id)
        return outcome
    return f"left pending ({outcome}); the note lane retries it"


# -- the outcome ---------------------------------------------------------------------


def reply_outcome(rows: list[dict[str, Any]]) -> str | None:
    """``sent+located`` / ``sent-not-located`` / ``unknown``, or None while
    nothing is settled (the watch's own reading, ``proof/watch.py``)."""
    if any(r.get("outcome") == "sent" and r.get("located") is not False for r in rows):
        return "sent+located"
    if any(r.get("outcome") == "unknown" for r in rows):
        return "unknown"
    if any(r.get("outcome") == "sent" and r.get("located") is False for r in rows):
        return "sent-not-located"
    return None


def bind_verdicts(audit_db: str, note_id: str) -> list[str]:
    """Every REPLY_BINDING verdict on this note, read only: outcome and reason."""
    try:
        conn = sqlite3.connect(f"file:{audit_db}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error:
        return ["(the audit log could not be opened)"]
    try:
        rows = conn.execute(
            "SELECT metadata FROM audit_log WHERE action_type='REPLY_BINDING' "
            "AND json_extract(metadata,'$.job_id') = ? ORDER BY ts",
            (note_id,),
        ).fetchall()
    except sqlite3.Error:
        return ["(the audit log could not be read)"]
    finally:
        conn.close()
    out = []
    for (meta,) in rows:
        m = json.loads(meta or "{}")
        out.append(f"{m.get('outcome')}" + (f": {m['reason']}" if m.get("reason") else ""))
    return out


def poll(
    client: Any, note_id: str, *, wait_min: float, sleep: Callable[[float], None], clock: Callable[[], float]
) -> str:
    deadline = clock() + wait_min * 60.0
    while True:
        resp = client._request({"action": "delivery_reply_status", "kind": note_lane.REPLY_KIND, "job_id": note_id})
        expected = str(resp.get("expected_key") or "")
        rows = [r for r in resp.get("replies") or [] if not expected or r.get("key") == expected]
        found = reply_outcome(rows)
        if found:
            return found
        if clock() >= deadline:
            return "not sent"
        sleep(POLL_SECONDS)


# -- the command ---------------------------------------------------------------------


def _text(a: argparse.Namespace) -> str:
    if a.text_file:
        return Path(a.text_file).read_text(encoding="utf-8")
    return a.text or ""


def _lane(client: BrokerClient) -> note_lane.NoteLane:
    return note_lane.NoteLane(
        run_dir=Path(os.environ.get(RUN_DIR_ENV) or DEFAULT_RUN_DIR),
        broker=note_lane.NoteBroker(client),
        runner_cmd=[],
        customer_slug=os.environ.get("CUSTOMER_SLUG", ""),
        gate_url=os.environ.get(GATE_URL_ENV) or DEFAULT_GATE_URL,
        wake_secret="",
    )


def _request(client: Any, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        resp = client._request(payload)
    except BrokerError as exc:
        raise Refused(str(exc)) from exc
    if resp.get("accepted") is not True:
        raise Refused(str(resp.get("reason") or "the broker did not accept the note"))
    return resp


def run(
    a: argparse.Namespace,
    *,
    client: Any,
    lane: Any,
    secret: Callable[[], str] = wake_secret,
    post: Callable[..., str] = post_wake,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
    audit_db: str | None = None,
) -> int:
    text = _text(a)
    base = {"graph_message_id": a.graph_id, "text": text, "approved_by": a.approved_by or ""}
    if a.dry_run:
        p = _request(client, {"action": "note_preview", **base})
        print(f"To: {p['sender']}")
        print(f"Cc: {', '.join(p['cc']) or '(nobody)'}")
        print(f"Answering: {p['subject']!r}, received {p['received_at']}")
        print("Words, sent exactly as below and then 'Thanks,':")
        print("-----")
        print(p["text"])
        print("-----")
        print(f"digest: {p['digest']}")
        print("Nothing was recorded. To send, rerun without --dry-run and with --confirm <digest>.")
        return 0
    if not a.confirm:
        raise Refused("a live note needs --confirm <digest> from its dry run")
    rec = _request(client, {"action": "note_record", **base, "confirm": a.confirm, "recorded_by": a.recorded_by})
    note_id = str(rec["job_id"])
    print(f"recorded note job {note_id}")
    _write_job(lane, note_id, str(rec.get("requester") or ""), a.graph_id, note_lane.wake_task(note_id))
    print(f"wake: {wake(lane, note_id, secret(), post)}")
    outcome = poll(client, note_id, wait_min=a.wait_min, sleep=sleep, clock=clock)
    print(f"reply: {outcome}")
    for verdict in bind_verdicts(audit_db or os.environ.get(AUDIT_DB_ENV) or DEFAULT_AUDIT_DB, note_id):
        print(f"bind: {verdict}")
    if outcome != "sent+located":
        print("The daemon's reply watch stays armed and pages SMD if the reply is not proven.")
    return 0 if outcome == "sent+located" else 1


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="medchron.note_by_smd", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("graph_id", nargs="?", help="the colleague's request email (Graph id, from --recent)")
    ap.add_argument("--recent", action="store_true", help="list this seat's recent requests and exit")
    ap.add_argument("--match", default="", help="with --recent: only rows whose sender or subject contains this")
    ap.add_argument("--text", help="the note's exact words")
    ap.add_argument("--text-file", help="a file holding the note's exact words")
    ap.add_argument("--approved-by", help="who approved these exact words")
    ap.add_argument("--recorded-by", default="root", help="who is running this (recorded on the note)")
    ap.add_argument("--dry-run", action="store_true", help="show what would be sent; record nothing")
    ap.add_argument("--confirm", help="the digest the dry run printed")
    ap.add_argument("--wait-min", type=float, default=20.0, help="minutes to wait for the reply (default 20)")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    if a.recent:
        path = os.environ.get(REQUEST_INDEX_ENV) or DEFAULT_REQUEST_INDEX
        try:
            rows = recent(path, match=a.match)
        except sqlite3.Error as exc:
            print(f"REFUSED: the request index at {path} could not be read ({exc})", file=sys.stderr)
            return 2
        for r in rows:
            print(f"{r['received_at']}  {r['graph_message_id']}  {r['sender']}  {r['subject']}")
        return 0
    if not a.graph_id:
        print("REFUSED: name the request email's Graph id (see --recent)", file=sys.stderr)
        return 2
    socket_path = os.environ.get(SOCKET_ENV, "").strip()
    if not socket_path:
        print(f"REFUSED: {SOCKET_ENV} is not set; run in the runner daemon's environment", file=sys.stderr)
        return 2
    client = BrokerClient(socket_path)
    try:
        return run(a, client=client, lane=_lane(client))
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
