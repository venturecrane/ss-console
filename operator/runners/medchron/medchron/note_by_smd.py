"""SMD has the Operator tell a firm colleague something: in her own thread,
or (when she has no thread in this mailbox) in one new email.

    python -m medchron.note_by_smd --recent [--match TEXT]
    python -m medchron.note_by_smd <graph_id> --text-file F --approved-by WHO --dry-run
    python -m medchron.note_by_smd <graph_id> --text-file F --approved-by WHO --confirm DIGEST [--wait-min 20]
    python -m medchron.note_by_smd --to ADDR --subject S --text-file F --approved-by WHO
        [--plain-number N ...] --dry-run | --confirm DIGEST

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
   and a digest over all of it (a new email: see THE NEW-MESSAGE FORM). It writes nothing. The broker refuses an email
   that is not a recent received request from someone the seat may answer,
   words with an em dash, and any identifier the email does not itself carry.
3. The live run repeats the digest (``--confirm``), so what is recorded is what
   SMD read. ``note_record`` writes the note (once per email and words, or per
   recipient, subject and words); this
   command then writes the note's job dir with a PENDING code-only wake
   (``note_lane.wake_task``) and does nothing else to it. The daemon's note
   lane, which holds the wake secret, sends the wake on its next tick and arms
   the reply watch then (which pages SMD if the note never reaches Sent Items,
   whether or not this command is still running). The command only polls the
   note's reply, up to ``--wait-min`` minutes from the job's creation.

WHY THE COMMAND NEVER SENDS THE WAKE (review of #3240). The lane scans the same
job dirs every tick; a command that also POSTed would race it to a second
DELIVER turn and to an unlocked read-modify-write of ``daemon.json``. This is
``deliver_by_smd``'s shape: one writer of the wake, the daemon. The command
writes ``daemon.json`` once, atomically, before the lane can see the job, and
only reads it after.

THE NEW-MESSAGE FORM (2026-10-16). Most of what SMD tells a firm has no
thread of hers here: she asked Scott, or nobody asked. ``--to`` names a Named
Administrator the smd-note skill authors (``settings.scheduled_recipients``),
and ``--subject`` the email's subject. With no request email to check the words
against, every three-to-ten-digit number in them is looked up as a Smokeball
matter number (``smokeball_connector.library.lookup_matter``) unless SMD
declares it plain (``--plain-number``: a count, a year, a dollar figure). Found
prints the matter's title; not found, or Smokeball unreachable, refuses. The
Smokeball client is built only when a number needs looking up, so a note with
no numbers works on a seat without Smokeball. The live run looks them up again.

Exit codes: 0 the reply was sent and the reply watch would count it proven
(or a dry run / list succeeded); 1 any other ending (printed); 2 refused
(nothing was recorded).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import note_lane
from .broker_client import BrokerClient, BrokerError
from .daemon import DEFAULT_RUN_DIR, RUN_DIR_ENV, SOCKET_ENV

REQUEST_INDEX_ENV = "SMD_REQUEST_INDEX_DB_PATH"
DEFAULT_REQUEST_INDEX = "/opt/data/requests.db"
AUDIT_DB_ENV = "SMD_AUDIT_DB_PATH"
DEFAULT_AUDIT_DB = "/run/smd-audit/audit.db"
RECENT_DAYS = 14
POLL_SECONDS = 15.0


class Refused(RuntimeError):
    """The broker refused the note; nothing was recorded."""


# -- the matter numbers (the new-message form) ---------------------------------------


def numbers_in(client: Any, text: str, approved_by: str) -> list[str]:
    """The note's bare digit runs, as the broker scans them: asked of the
    broker (``note_preview`` with ``scan_numbers``), whose scan is the one it
    refuses on. This venv cannot import the broker's package."""
    resp = _request(client, {"action": "note_preview", "scan_numbers": True, "text": text, "approved_by": approved_by})
    return [str(n) for n in resp.get("numbers") or []]


def _default_matter_client() -> Any:
    from .seat import ClientSeat

    return ClientSeat().client


def _matter_title(client: Any, matter_id: str) -> str:
    try:
        r = client.get(f"/matters/{matter_id}")
    except Exception:  # noqa: BLE001 - a missing title is shown as such, never invented
        return ""
    if isinstance(r, dict) and isinstance(r.get("value"), list) and r["value"]:
        r = r["value"][0]
    if not isinstance(r, dict):
        return ""
    return str(r.get("title") or r.get("description") or "").strip()[:200]


def resolve_matters(
    numbers: list[str], *, client_factory: Callable[[], Any] = _default_matter_client
) -> list[dict[str, str]]:
    """Each number as a Smokeball matter (number + title), or ``Refused``. The
    client is built only when there is something to look up."""
    if not numbers:
        return []
    try:
        from smokeball_connector.library import lookup_matter

        client = client_factory()
    except Exception as exc:  # "could not look" is a refusal, never "none found"
        raise Refused(
            f"could not look up matter numbers {', '.join(numbers)}: Smokeball is not reachable here "
            f"({exc.__class__.__name__}); nothing was recorded"
        ) from exc
    out = []
    for number in numbers:
        try:
            found = lookup_matter(client, number=number)
        except Exception as exc:
            raise Refused(
                f"could not look up matter {number} ({exc.__class__.__name__}); nothing was recorded"
            ) from exc
        if found.failed:
            raise Refused(f"could not look up matter {number} ({found.reason}); nothing was recorded")
        if not found.found or not found.matter_id:
            raise Refused(
                f"{number} is not a matter number in Smokeball. If it is not meant as one (a count, a year, "
                f"an amount), pass --plain-number {number}; otherwise correct it"
            )
        out.append({"number": number, "name": _matter_title(client, found.matter_id)})
    return out


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


def _write_job(lane: Any, note_id: str, requester: str, ref: str, task: str, created: float) -> None:
    """The note's job dir, for the note lane: the envelope first, then
    ``daemon.json`` by an atomic rename, so the lane never reads it half-written
    and never sees a job without its requester. Written once; never again."""
    jd = lane.job_dir(note_id)
    jd.mkdir(parents=True, exist_ok=True)
    (jd / "envelope.json").write_text(json.dumps({"requested_by": requester, "request_ref": ref}), encoding="utf-8")
    state = {
        "state": "delivered",
        "delivered_by": "smd",
        "finished_at": created,
        "wake": {"pending": True, "attempts": 0, "task": task},
    }
    tmp = jd / ".daemon.json.tmp"
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    tmp.replace(jd / "daemon.json")


def wake_state(lane: Any, note_id: str) -> str:
    """Where the note's wake is, read from the lane's state: ``pending``,
    ``sent``, or ``failed: <why>``."""
    w = lane._daemon_state(note_id).get("wake") or {}
    if w.get("pending"):
        return "pending"
    outcome = str(w.get("outcome") or "")
    return outcome if outcome.startswith("failed") else "sent"


# -- the outcome ---------------------------------------------------------------------


#: The endings the reply watch counts proven (``proof/watch.check_reply_watches``
#: closes a watch on ``outcome == "sent" and located is not False``).
PROVEN = frozenset({"sent+located", "sent (no Sent Items lookup reported)"})


def reply_outcome(rows: list[dict[str, Any]]) -> str | None:
    """The watch's own reading, mirrored exactly (``proof/watch.py``), with the
    one case it folds together named apart: a ``sent`` row whose ``located`` is
    None (the send reported no Sent Items lookup) is proven to the watch, so it
    is proven here, but it is not called "located". None while nothing settled."""
    sent = [r for r in rows if r.get("outcome") == "sent"]
    if any(r.get("located") is True for r in sent):
        return "sent+located"
    if any(r.get("located") is None for r in sent):
        return "sent (no Sent Items lookup reported)"
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
    client: Any,
    note_id: str,
    *,
    created: float,
    wait_min: float,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> str:
    """Poll the note's reply until it settles or ``wait_min`` after the job was
    created. "not sent" when the window closes with nothing settled."""
    deadline = created + wait_min * 60.0
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
    """The lane's paths only: this process never dispatches a wake, so it holds
    no wake secret."""
    return note_lane.NoteLane(
        run_dir=Path(os.environ.get(RUN_DIR_ENV) or DEFAULT_RUN_DIR),
        broker=note_lane.NoteBroker(client),
        runner_cmd=[],
        customer_slug=os.environ.get("CUSTOMER_SLUG", ""),
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


def _base(a: argparse.Namespace, text: str, client: Any, matter_client: Callable[[], Any]) -> dict[str, Any]:
    if not a.to:
        return {"graph_message_id": a.graph_id, "text": text, "approved_by": a.approved_by or ""}
    plain = sorted({str(n).strip() for n in (a.plain_number or [])})
    lookup = [n for n in numbers_in(client, text, a.approved_by or "") if n not in plain]
    return {
        "to": a.to,
        "subject": a.subject or "",
        "text": text,
        "approved_by": a.approved_by or "",
        "matters": resolve_matters(lookup, client_factory=matter_client),
        "plain_numbers": plain,
    }


def _print_preview(p: dict[str, Any]) -> None:
    print(f"To: {p['sender']}")
    if p.get("mode") == "new_message":
        print("Cc: (nobody)")
        print(f"Subject: {p['subject']!r} (a new email; she has no thread to answer)")
        for m in p.get("matters") or []:
            print(f"Matter {m['number']}: {m['name'] or '(Smokeball gave no title)'}")
        if p.get("plain_numbers"):
            print(f"Plain numbers (not matters): {', '.join(p['plain_numbers'])}")
        for ident in p.get("identifiers") or []:
            print(f"Also states {ident} (nothing on the seat can check it: read it)")
    else:
        print(f"Cc: {', '.join(p['cc']) or '(nobody)'}")
        print(f"Answering: {p['subject']!r}, received {p['received_at']}")


def run(
    a: argparse.Namespace,
    *,
    client: Any,
    lane: Any,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
    audit_db: str | None = None,
    matter_client: Callable[[], Any] = _default_matter_client,
) -> int:
    text = _text(a)
    base = _base(a, text, client, matter_client)
    if a.dry_run:
        p = _request(client, {"action": "note_preview", **base})
        _print_preview(p)
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
    created = clock()
    new_message = rec.get("mode") == "new_message"
    ref = "" if new_message else a.graph_id
    task = note_lane.wake_task(note_id, new_message=new_message)
    _write_job(lane, note_id, str(rec.get("requester") or ""), ref, task, created)
    print("wake: queued; the note lane sends it on its next tick and arms the reply watch then")
    outcome = poll(client, note_id, created=created, wait_min=a.wait_min, sleep=sleep, clock=clock)
    if outcome == "not sent":
        where = wake_state(lane, note_id)
        if where == "pending":
            outcome = "wake still pending: the note lane has not sent it yet; it pages wake_lost if it never can"
        elif where.startswith("failed"):
            outcome = f"wake {where}; SMD is paged wake_lost"
        else:
            outcome = "not sent: the wake went and no reply settled in the window; the reply watch pages SMD"
    print(f"reply: {outcome}")
    for verdict in bind_verdicts(audit_db or os.environ.get(AUDIT_DB_ENV) or DEFAULT_AUDIT_DB, note_id):
        print(f"bind: {verdict}")
    return 0 if outcome in PROVEN else 1


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="medchron.note_by_smd", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("graph_id", nargs="?", help="the colleague's request email (Graph id, from --recent)")
    ap.add_argument("--to", help="a new email instead of a reply: the authored administrator it goes to")
    ap.add_argument("--subject", help="with --to: the new email's subject")
    ap.add_argument(
        "--plain-number",
        action="append",
        default=[],
        help="with --to: a number in the words that is not a matter number (repeatable)",
    )
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
    if a.to and a.graph_id:
        print("REFUSED: a note answers an email (graph id) or is a new email (--to), not both", file=sys.stderr)
        return 2
    if not a.graph_id and not a.to:
        print("REFUSED: name the request email's Graph id (see --recent), or --to for a new email", file=sys.stderr)
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
