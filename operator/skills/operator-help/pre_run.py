#!/usr/bin/env python3
"""operator-help pre-run: wake the daily help review, and on Mondays send SMD the weekly note.

ss-console#3423, increment 3. One cron row (``13 14 * * *``) drives both halves:

* EVERY DAY it wakes the agent for the help review (stdout carries
  ``"mode": "review"``). The woken turn follows the skill's Review mode: it
  reads ``operator_help_signals`` with ``scope: "review"`` and may promote,
  demote or reorder SMD-authored tips within the limits the tool enforces.
* ON MONDAYS (seat-local), and whenever ``SMD_OPERATOR_HELP_FORCE_NOTE=1`` is
  set for an on-demand proof, it ALSO renders the weekly note and writes the
  consume-once dispatch envelope the overlay's ``prerendered_dispatch`` sends out
  of turn, through the full gate, to the one authored recipient
  (``settings.recipient``, SMD's own address). No model composes the note.

WHAT THE NOTE SAYS, AND FROM WHAT. It is read from the seat's help state file
(``/opt/data/smd/help_state.db``, written by the initiation plugin's
``help_review``), read-only: the emphasis changes of the last seven days with
the counts that justified each, the last review's counts, and the actionable
kinds no tip covers (proposals; SMD adds tips by PR). Tip ids, kinds and counts
only: never a name, an address, a matter, a date or a message's words. No em
dash anywhere. The note ends by telling SMD that a reply of undo restores the
emphasis from before the most recent change (the router's help-guide undo
class, ``operator_help_guide_undo``).

FAILURE. A help state that cannot be read is said in the note ("could not be
read"), never shown as "no changes". An unauthored recipient or an envelope that
cannot be written costs the note, and the review still wakes; the stderr line
and the heartbeat row name it.

WHAT STDOUT CARRIES: the verdict, the mode and counts. Never content.

DRY RUN: ``SMD_OPERATOR_HELP_DRY=1`` prints the verdict and counts and writes
nothing (no envelope, no heartbeat row).
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

SKILL = "operator-help"
_SKILL_DIRNAME = "operator-help"
_BROKER_TIMEOUT_SECONDS = 10
#: Monday, in the seat's own zone.
NOTE_WEEKDAY = 0
WINDOW_DAYS = 7
ROUTING_LEG = "central"
DEFAULT_STATE_PATH = "/opt/data/smd/help_state.db"
_ACTIONS = ("promote", "demote", "reorder")


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


_H = _load_skill_helpers()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def note_recipient(cfg: dict) -> str | None:
    """``settings.recipient`` on this skill: one address, or None (no imposed default)."""
    value = _H.find_skill_settings(cfg, SKILL).get("recipient")
    if isinstance(value, str) and "@" in value.strip() and " " not in value.strip():
        return value.strip()
    return None


def help_zone(cfg: dict) -> str:
    """``HERMES_TIMEZONE`` (what the cron runs in), else ``business_hours.timezone``, else UTC."""
    configured = os.environ.get("HERMES_TIMEZONE", "").strip()
    if configured:
        return configured
    hours = cfg.get("business_hours") if isinstance(cfg, dict) else None
    zone = hours.get("timezone") if isinstance(hours, dict) else None
    return zone.strip() if isinstance(zone, str) and zone.strip() else "UTC"


def help_local_day(now: datetime, zone: str) -> date:
    try:
        from zoneinfo import ZoneInfo

        return now.astimezone(ZoneInfo(zone)).date()
    except Exception:  # noqa: BLE001 - an unknown zone reads as the UTC day
        return now.astimezone(timezone.utc).date()


def state_path() -> Path:
    return Path(os.environ.get("SMD_HELP_STATE_DB_PATH") or DEFAULT_STATE_PATH)


# ---------------------------------------------------------------------------
# Reading the help state (read-only)
# ---------------------------------------------------------------------------


class StateUnreadable(Exception):
    """The help state file exists and could not be read."""


def read_week(path: Path, now: float) -> dict:
    """The week's changes and the last review. ``{}`` parts when nothing was ever
    recorded; raises :class:`StateUnreadable` when the file cannot be read."""
    out: dict = {"changes": [], "last_review": None}
    if not path.exists():
        return out
    try:
        conn = sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True, timeout=5)
    except Exception as exc:
        raise StateUnreadable(type(exc).__name__) from exc
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "help_snapshots" in tables:
            rows = conn.execute(
                "SELECT action, tip_id, reason_json, undone_at FROM help_snapshots WHERE ts >= ? ORDER BY id",
                (now - WINDOW_DAYS * 86400.0,),
            ).fetchall()
            for action, tip_id, reason_json, undone_at in rows:
                if action not in _ACTIONS:
                    continue
                try:
                    reason = json.loads(reason_json) if reason_json else {}
                except ValueError:
                    reason = {}
                out["changes"].append(
                    {"action": action, "tip_id": str(tip_id), "reason": reason, "undone": undone_at is not None}
                )
        if "help_meta" in tables:
            row = conn.execute("SELECT value FROM help_meta WHERE key = 'last_review'").fetchone()
            if row is not None:
                review = json.loads(row[0])
                if isinstance(review, dict) and isinstance(review.get("at"), (int, float)):
                    if review["at"] >= now - WINDOW_DAYS * 86400.0:
                        out["last_review"] = review
        return out
    except Exception as exc:
        raise StateUnreadable(type(exc).__name__) from exc
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

SUBJECT = "Operator help: this week's note on how people are asking"
UNDO_LINE = (
    "To put the tips back the way they were before the most recent change, reply to this email with the word undo."
)


def _people(n: int) -> str:
    return "1 person" if n == 1 else str(n) + " people"


def _times(n: int) -> str:
    return "once" if n == 1 else str(n) + " times"


def _change_line(change: dict) -> str:
    tip = change["tip_id"]
    action = change["action"]
    if action == "promote":
        head = "Put the tip " + tip + " first in the help answer"
    elif action == "demote":
        head = "Stopped leading the help answer with the tip " + tip
    else:
        head = "Moved the tip " + tip + " within the help answer's lead"
    reason = change.get("reason") or {}
    if reason.get("kind"):
        head += (
            ", because "
            + str(reason["kind"])
            + " came up "
            + _times(int(reason.get("occurrences") or 0))
            + " from "
            + _people(int(reason.get("senders") or 0))
        )
    if change.get("undone"):
        head += " (since undone)"
    return "- " + head + "."


def _counts_lines(review: dict) -> list[str]:
    lines: list[str] = []
    req = review.get("requests") or {}
    lines.append(
        "- Requests: "
        + str(int(req.get("total") or 0))
        + " from "
        + _people(int(req.get("senders") or 0))
        + "; threads where one person wrote four or more times: "
        + str(int(req.get("repeat_ask_threads") or 0))
        + "."
    )
    jobs = review.get("jobs") or {}
    for label, key in (("Jobs held", "held"), ("Jobs failed", "failed")):
        by_lane = jobs.get(key) or {}
        if by_lane:
            parts = [str(lane) + " " + str(int(n)) for lane, n in sorted(by_lane.items())]
            lines.append("- " + label + ": " + ", ".join(parts) + ".")
        else:
            lines.append("- " + label + ": none.")
    kinds = review.get("kinds") or {}
    for kind, entry in sorted(kinds.items()):
        lines.append(
            "- "
            + str(kind)
            + ": "
            + _times(int(entry.get("occurrences") or 0))
            + " from "
            + _people(int(entry.get("senders") or 0))
            + (", actionable" if entry.get("actionable") else "")
            + "."
        )
    return lines


def render_note(week: dict | None) -> str:
    """The full body. ``week`` None means the help state could not be read."""
    lines = [
        "Hello,",
        "",
        "This is the weekly note on how people at the firm are asking me for things, for the last seven days.",
        "",
    ]
    if week is None:
        lines += [
            "I could not read my help records this week, so I cannot say what changed or what I saw. Nothing here means nothing happened.",
            "",
            UNDO_LINE,
        ]
        return "\n".join(lines)
    changes = week.get("changes") or []
    lines.append("What changed")
    lines += [_change_line(c) for c in changes] if changes else ["- Nothing changed this week."]
    lines.append("")
    review = week.get("last_review")
    lines.append("What I saw")
    if review is None:
        lines.append("- No review ran in the last seven days.")
        proposals: list = []
    else:
        lines += _counts_lines(review)
        proposals = [str(k) for k in review.get("proposals") or []]
    lines.append("")
    lines.append("Proposed new tips")
    if proposals:
        kinds = (review or {}).get("kinds") or {}
        for kind in proposals:
            entry = kinds.get(kind) or {}
            lines.append(
                "- "
                + kind
                + " came up "
                + _times(int(entry.get("occurrences") or 0))
                + " from "
                + _people(int(entry.get("senders") or 0))
                + " and no tip covers it. A tip for it would be added by SMD."
            )
    else:
        lines.append("- None this week.")
    lines += ["", UNDO_LINE]
    return "\n".join(lines)


SKELETON = "Hello,\n\nThis week's help note could not be sent in full.\n\n" + UNDO_LINE


def build_envelope(recipient: str, body: str, started_at: str) -> dict:
    """One dispatch to the one authored recipient; the skeleton as the turn's only body."""
    dispatch = {
        "recipients": [recipient],
        "cc": [],
        "routing_leg": ROUTING_LEG,
        "subject": SUBJECT,
        "full_body": body,
        "skeleton_body": SKELETON,
        "body_sha256_full": _H.canonical_body_sha256(body),
        "body_sha256_skeleton": _H.canonical_body_sha256(SKELETON),
        "appends": [],
    }
    return {
        "skill": SKILL,
        "render_mode": "templated",
        "started_at": started_at,
        "dispatches": [dispatch],
        "unroutable": [],
        "memo_matters": [],
        "in_turn": [{"name": "help_note_skeleton", "template": SKELETON, "slots": {}}],
        "in_turn_enforce": True,
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _wake_row(verb: str, action_type: str, basis: str, extra: dict) -> bool:
    """One wake row through the broker's agent-uid verb. Counts only. True on ack."""
    socket_path = os.environ.get("SMD_AUDIT_BROKER_SOCKET") or os.environ.get("SMD_WORKSPACE_BROKER_SOCKET")
    if not socket_path:
        return False
    metadata = {"decision_basis": basis, "platform": "cron-pre-run", "customer": os.environ.get("CUSTOMER_SLUG", "")}
    metadata.update(extra)
    row = {"action_type": action_type, "actor": "agent", "actor_role": "agent", "skill_name": SKILL}
    row["metadata"] = json.dumps(metadata, sort_keys=True, separators=(",", ":"))
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(_BROKER_TIMEOUT_SECONDS)
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
        _H.warn_observability_failure(exc, "operator-help heartbeat")
        return False


def _say(payload: dict) -> int:
    print(json.dumps(payload, sort_keys=True))
    return 0


def run(cfg: dict, *, clock=lambda: datetime.now(timezone.utc), force: bool = False, dry: bool = False) -> int:
    """Decide the note, write it, and wake the review. Never raises past ``main``."""
    now = clock()
    today = help_local_day(now, help_zone(cfg))
    note_due = force or today.weekday() == NOTE_WEEKDAY
    if not note_due:
        return _say({"wakeAgent": True, "mode": "review", "status": "review_due", "note": False})
    recipient = note_recipient(cfg)
    try:
        week: dict | None = read_week(state_path(), now.timestamp())
    except StateUnreadable:
        sys.stderr.write("[pre_run] operator-help: help state unreadable; the note says so\n")
        week = None
    counts = {
        "changes": None if week is None else len(week["changes"]),
        "review_seen": None if week is None else week["last_review"] is not None,
        "forced": force,
    }
    if dry:
        return _say({"wakeAgent": False, "mode": "review", "status": "dry_run", "dry_run": True, **counts})
    if recipient is None:
        sys.stderr.write("[pre_run] operator-help: no recipient authored; no weekly note\n")
        _wake_row("suppressed_wake_append", "SUPPRESSED_WAKE", "note_recipient_unauthored", counts)
        return _say({"wakeAgent": True, "mode": "review", "status": "note_recipient_unauthored", "note": False})
    stamp = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    envelope = build_envelope(recipient, render_note(week), stamp)
    if not _H.write_dispatch_envelope(SKILL, envelope):
        _wake_row("suppressed_wake_append", "SUPPRESSED_WAKE", "note_envelope_write_failed", counts)
        return _say({"wakeAgent": True, "mode": "review", "status": "note_envelope_write_failed", "note": False})
    stamps = {
        "render_mode": "templated",
        "dispatch_count": 1,
        "body_sha256": [
            {
                "body_sha256_full": envelope["dispatches"][0]["body_sha256_full"],
                "body_sha256_skeleton": envelope["dispatches"][0]["body_sha256_skeleton"],
            }
        ],
    }
    _wake_row("emitted_wake_append", "EMITTED_WAKE", "note_ready", {**counts, **stamps})
    return _say(
        {"wakeAgent": True, "dispatch_expected": True, "mode": "review", "status": "note_ready", "note": True, **counts}
    )


def main() -> int:
    dry = os.environ.get("SMD_OPERATOR_HELP_DRY", "").strip() == "1"
    force = os.environ.get("SMD_OPERATOR_HELP_FORCE_NOTE", "").strip() == "1"
    try:
        return run(_H.load_customer_yaml(None), force=force, dry=dry)
    except Exception as exc:  # noqa: BLE001 - a crashed note still wakes the review
        sys.stderr.write("[pre_run] operator-help failed: " + type(exc).__name__ + "\n")
        return _say({"wakeAgent": True, "mode": "review", "status": "pre_run_crashed", "note": False})


if __name__ == "__main__":
    sys.exit(main())
