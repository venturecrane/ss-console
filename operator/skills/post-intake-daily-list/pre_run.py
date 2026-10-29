#!/usr/bin/env python3
"""post-intake-daily-list pre-run: one email a day naming only the post that needs a person.

Class N (ADR 0050): no model composes anything. Combined post intake records
what it did with every scanned bundle in the seat's post journal (the
Smokeball connector's ``post_journal.py``, ``$SMD_POST_JOURNAL_DIR``, default
``/opt/data/post-intake-journal``). Once a day this script reads every bundle
recorded since the last list, keeps only the lines that need a person (a
letter it could not place, a filing whose keying did not land or may be
wrong, a whole bundle nothing was filed from, and a court paper to calendar),
renders the whole email, writes the numbered list where the intake's
``get_post_list`` finds it when the recipient replies, and writes the
consume-once dispatch envelope the overlay's ``prerendered_dispatch`` sends out
of turn, through the full gate, to the ONE recipient the firm authored
(``settings.recipient``). The woken turn composes nothing.

WHAT IS NOT LISTED. A letter that filed and whose keying landed. A figure read
from a scan is not listed for that alone: the Medicals row or expense already
says so to whoever relies on it.

QUIET BY DESIGN. Nothing to list sends nothing: ``wakeAgent: false`` and a
SUPPRESSED_WAKE row (``nothing_to_list``). An unauthored recipient, an
unreadable journal or an envelope that cannot be written sends nothing either,
each with its own status on the row. A failed run never sends a partial list.

THE WINDOW. Every bundle recorded after the previous list's cursor
(``state.json``), so a bundle that arrives after the day's list, or on a
weekend, is on the next one. The cursor moves only once the envelope is
written. With no cursor yet, the window opens at the start of the seat's day.

WHAT STDOUT CARRIES: the verdict, a status and counts. Never a sender, a page
range or a matter number: those travel only in the 0600 envelope.

DRY RUN: ``SMD_POST_LIST_DRY=1`` prints the verdict and counts and writes
nothing. A run by hand must set ``HERMES_HOME=/opt/data/profiles/operator``,
the scheduler's value, or the cron turn never finds the envelope.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

SKILL = "post-intake-daily-list"
_SKILL_DIRNAME = "post-intake-daily-list"
_BROKER_TIMEOUT_SECONDS = 10
ROUTING_LEG = "central"
JOURNAL_DIR_ENV = "SMD_POST_JOURNAL_DIR"
DEFAULT_JOURNAL_DIR = "/opt/data/post-intake-journal"
RETAIN_DAYS = 45
#: The journal's closed vocabulary (connector post_journal.py, pinned equal by a test).
NOT_PLACED = ("no_match", "one_candidate", "ambiguous", "search_failed", "filing_refused")
PLACED_CHECK = ("tab_not_written", "figure_not_saved", "possible_duplicate", "offer_not_written", "expense_not_staged")
BUNDLE_ISSUES = ("unreadable", "partition_failed", "not_processed")
KIND_WORDS = {
    "letter": "letter",
    "court_paper": "court paper",
    "medical_bill": "medical bill",
    "vendor_bill": "bill",
    "settlement_offer": "settlement offer",
}


def _load_skill_helpers():
    """The shared helpers vendored beside this file (canonical: operator/templates/skill_helpers.py)."""
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
# Configuration and the clock
# ---------------------------------------------------------------------------


def list_recipient(cfg: dict) -> str | None:
    """``settings.recipient`` on this skill: one address, or None (no imposed default)."""
    value = _H.find_skill_settings(cfg, SKILL).get("recipient")
    if isinstance(value, str) and "@" in value.strip() and " " not in value.strip() and "," not in value:
        return value.strip()
    return None


def seat_zone(cfg: dict) -> str:
    configured = os.environ.get("HERMES_TIMEZONE", "").strip()
    if configured:
        return configured
    hours = cfg.get("business_hours") if isinstance(cfg, dict) else None
    zone = hours.get("timezone") if isinstance(hours, dict) else None
    return zone.strip() if isinstance(zone, str) and zone.strip() else "UTC"


def _local(moment: datetime, zone: str) -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return moment.astimezone(ZoneInfo(zone))
    except Exception:  # noqa: BLE001 - an unknown zone reads as UTC
        return moment.astimezone(timezone.utc)


def _parse(stamp: Any) -> datetime | None:
    if not isinstance(stamp, str) or not stamp.strip():
        return None
    try:
        moment = datetime.fromisoformat(stamp.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def journal_dir() -> Path:
    return Path(os.environ.get(JOURNAL_DIR_ENV) or DEFAULT_JOURNAL_DIR)


# ---------------------------------------------------------------------------
# The journal
# ---------------------------------------------------------------------------


class JournalUnreadable(RuntimeError):
    """A batch record exists that cannot be read: no list rather than a short one."""


def read_cursor(root: Path) -> datetime | None:
    try:
        doc = json.loads((root / "state.json").read_text("utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:
        raise JournalUnreadable("state") from exc
    return _parse(doc.get("listed_through") if isinstance(doc, dict) else None)


def read_batches(root: Path, since: datetime, until: datetime) -> list[dict]:
    """Every bundle recorded in (since, until], oldest first."""
    out = []
    directory = root / "batches"
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            record = json.loads(path.read_text("utf-8"))
        except Exception as exc:
            raise JournalUnreadable(path.name) from exc
        at = _parse(record.get("recorded_at") if isinstance(record, dict) else None)
        if at is None:
            raise JournalUnreadable(path.name)
        if since < at <= until:
            out.append(record)
    return sorted(out, key=lambda r: (str(r.get("received_at") or r.get("recorded_at")), str(r.get("message_id"))))


def _needs_person(entry: dict) -> bool:
    return bool(entry.get("issue")) or entry.get("kind") == "court_paper"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _clock(moment: datetime | None) -> str:
    if moment is None:
        return "an unknown time"
    hour = moment.hour % 12 or 12
    return f"{hour}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"


def _scan(record: dict, zone: str) -> str:
    when = _parse(record.get("received_at")) or _parse(record.get("recorded_at"))
    local = _local(when, zone) if when else None
    return f'Scan of {_clock(local)} ("{record.get("file_name")}")'


def _ask(entry: dict) -> str:
    """What one listed letter needs, in plain words, ending in the question."""
    issue, detail = entry.get("issue") or "", entry.get("detail") or ""
    where = entry.get("placed_on") or ""
    cands = entry.get("candidates") or []
    because = f" ({detail})" if detail else ""
    if issue == "one_candidate":
        return f"the only matter I found is {cands[0]}. Is it that one?"
    if issue == "ambiguous":
        return f"it could be matter {' or '.join(cands)}. Which is it?"
    if issue == "no_match":
        return "no matter matched it. Which matter is it?"
    if issue == "search_failed":
        return "the matter search could not run, so it was not filed. Which matter is it?"
    if issue == "filing_refused":
        return f"it could not be filed{because}. Which matter is it?"
    filed = f"filed on matter {where}"
    if issue == "tab_not_written":
        return f"{filed}, but it is not on the Medicals tab{because}."
    if issue == "figure_not_saved":
        return f"{filed}, but a figure did not save as written{because}; please check it."
    if issue == "possible_duplicate":
        return f"{filed}; it looks like one already entered{because}, so it was not entered again. Should it be?"
    if issue == "offer_not_written":
        return f"{filed}, but the offer is not on Negotiation Details{because}."
    if issue == "expense_not_staged":
        return f"{filed}, but it was not staged as an expense{because}."
    return f"{filed}."


def _bundle_line(record: dict) -> str:
    issue, detail = record.get("bundle_issue"), record.get("bundle_detail") or ""
    pages = record.get("page_count") or 0
    count = f" ({pages} pages)" if pages else ""
    because = f": {detail}" if detail else ""
    if issue == "not_processed":
        return f"a second PDF on the same email{count} was not read. Please send it on its own."
    if issue == "partition_failed":
        return f"the whole scan{count}: the letters could not be told apart, so nothing in it was filed."
    return f"the whole scan{count} could not be read{because}, so nothing in it was filed."


def build(batches: list[dict], zone: str) -> dict:
    """Every line the list carries, numbered, plus the day's counts."""
    asks: list[dict] = []
    calendar: list[dict] = []
    letters = filed = 0
    for record in batches:
        scan = _scan(record, zone)
        base = {
            "message_id": record.get("message_id"),
            "file_name": record.get("file_name"),
            "sha256": record.get("sha256"),
            "received_at": record.get("received_at") or record.get("recorded_at"),
        }
        if record.get("bundle_issue") in BUNDLE_ISSUES:
            asks.append({**base, "issue": record["bundle_issue"], "text": f"{scan}, {_bundle_line(record)}"})
            continue
        for entry in record.get("letters") or []:
            letters += 1
            filed += 0 if entry.get("issue") in NOT_PLACED else 1
            if not _needs_person(entry):
                continue
            kind = KIND_WORDS.get(entry.get("kind"), "letter")
            head = f"{scan}, pages {entry['first_page']}-{entry['last_page']}, {kind} from {entry.get('sender')}"
            item = {
                **base,
                "first_page": entry["first_page"],
                "last_page": entry["last_page"],
                "sender": entry.get("sender"),
                "kind": entry.get("kind"),
                "issue": entry.get("issue") or "",
                "placed_on": entry.get("placed_on") or "",
                "candidates": list(entry.get("candidates") or []),
            }
            if not entry.get("issue"):
                calendar.append({**item, "text": f"{head}, filed on matter {entry.get('placed_on')}."})
            else:
                asks.append({**item, "text": f"{head}: {_ask(entry)}"})
    for n, item in enumerate(asks, start=1):
        item["n"] = n
    return {"asks": asks, "calendar": calendar, "scans": len(batches), "letters": letters, "filed": filed}


def render(listing: dict) -> str:
    scans, letters, filed = listing["scans"], listing["letters"], listing["filed"]
    lines = [
        "Hello,",
        "",
        f"Post since the last list: {scans} scan{'s' if scans != 1 else ''}, "
        f"{letters} letter{'s' if letters != 1 else ''}, {filed} filed on their matters.",
    ]
    if listing["asks"]:
        lines += ["", "Needs a word from you:"]
        lines += [f"{item['n']}. {item['text']}" for item in listing["asks"]]
    if listing["calendar"]:
        lines += ["", "Filed, needs calendaring:"]
        lines += [f"- {item['text']}" for item in listing["calendar"]]
    if listing["asks"]:
        lines += [
            "",
            "Reply to this email with each item's number and its matter number, and I will file them. "
            "One reply can answer every item.",
        ]
    return "\n".join(lines)


def subject(ref: str, listing: dict) -> str:
    parts = []
    if listing["asks"]:
        parts.append(f"{len(listing['asks'])} need a word from you")
    if listing["calendar"]:
        parts.append(f"{len(listing['calendar'])} to calendar")
    return f"Post list {ref}: " + ", ".join(parts)


def skeleton(listing: dict) -> str:
    total = len(listing["asks"]) + len(listing["calendar"])
    return (
        "Hello,\n\n"
        f"{total} item{'s' if total != 1 else ''} from the post need a word from you. "
        "The list itself could not be included in this email."
    )


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def _write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    handle = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, sort_keys=True)
    os.replace(tmp, path)


def list_ref(root: Path, day: date) -> str:
    """Today's date, or ``<date>-2`` and on when a list was already sent today."""
    ref, n = day.isoformat(), 1
    while (root / "lists" / f"{ref}.json").exists():
        n += 1
        ref = f"{day.isoformat()}-{n}"
    return ref


def write_handoff(listing: dict, started_at: str) -> None:
    """The matter numbers the body prints, each paired with the day its scan
    arrived, so the send gate verifies them as READ (the statute-watch shape).
    Best-effort: without it the gate refuses the body and the skeleton goes."""
    try:
        grouped: dict[str, list[str]] = {}
        for item in listing["asks"] + listing["calendar"]:
            day = str(item.get("received_at") or "")[:10]
            for number in [item.get("placed_on"), *item.get("candidates", [])]:
                if number and len(day) == 10:
                    dates = grouped.setdefault(number, [])
                    if day not in dates:
                        dates.append(day)
        days = sorted({d for dates in grouped.values() for d in dates})
        record = {
            "skill": SKILL,
            "started_at": started_at,
            "dates": days,
            "matter_ids": [],
            "records": [{"matterNumber": k, "dates": v} for k, v in grouped.items()],
        }
        _write_json(Path(os.environ.get("HERMES_HOME") or "/opt/data") / ".smd" / "pre_run" / (SKILL + ".json"), record)
    except Exception as exc:  # noqa: BLE001 - never change stdout or the wake
        sys.stderr.write("[pre_run] post-intake-daily-list: handoff write failed (" + type(exc).__name__ + ")\n")


def build_envelope(recipient: str, subject_line: str, body: str, skeleton_body: str, started_at: str) -> dict:
    dispatch = {
        "recipients": [recipient],
        "cc": [],
        "routing_leg": ROUTING_LEG,
        "subject": subject_line,
        "full_body": body,
        "skeleton_body": skeleton_body,
        "body_sha256_full": _H.canonical_body_sha256(body),
        "body_sha256_skeleton": _H.canonical_body_sha256(skeleton_body),
        "appends": [],
    }
    return {
        "skill": SKILL,
        "render_mode": "templated",
        "started_at": started_at,
        "dispatches": [dispatch],
        "unroutable": [],
        "memo_matters": [],
        "in_turn": [{"name": "post_list_skeleton", "template": skeleton_body, "slots": {}}],
        "in_turn_enforce": True,
    }


def _prune(directory: Path) -> None:
    import time

    cutoff = time.time() - RETAIN_DAYS * 86400
    for old in directory.glob("*.json") if directory.is_dir() else []:
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            continue


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
        _H.warn_observability_failure(exc, "post-intake-daily-list heartbeat")
        return False


def _say(payload: dict) -> int:
    print(json.dumps(payload, sort_keys=True))
    return 0


def _quiet(status: str, counts: dict, *, dry: bool) -> int:
    if not dry and not _wake_row("suppressed_wake_append", "SUPPRESSED_WAKE", status, counts):
        sys.stderr.write("[pre_run] post-intake-daily-list: no list this run (" + status + "); row not recorded\n")
    return _say({"wakeAgent": False, "status": status, "dry_run": dry, **counts})


def run(cfg: dict, *, clock=lambda: datetime.now(timezone.utc), dry: bool = False) -> int:
    now = clock()
    zone = seat_zone(cfg)
    recipient = list_recipient(cfg)
    if recipient is None and not dry:
        return _quiet("recipient_unauthored", {}, dry=dry)
    root = journal_dir()
    try:
        cursor = read_cursor(root)
        start_of_day = _local(now, zone).replace(hour=0, minute=0, second=0, microsecond=0)
        since = cursor if cursor is not None else start_of_day
        batches = read_batches(root, since, now)
    except JournalUnreadable:
        return _quiet("journal_unreadable", {}, dry=dry)
    listing = build(batches, zone)
    counts = {
        "scans": listing["scans"],
        "letters": listing["letters"],
        "asks": len(listing["asks"]),
        "calendar": len(listing["calendar"]),
    }
    if dry:
        return _say({"wakeAgent": False, "status": "dry_run", "dry_run": True, **counts})
    if not listing["asks"] and not listing["calendar"]:
        try:
            _write_json(root / "state.json", {"listed_through": _iso(now)})
        except OSError:
            sys.stderr.write("[pre_run] post-intake-daily-list: cursor not written\n")
        return _quiet("nothing_to_list", counts, dry=dry)
    ref = list_ref(root, _local(now, zone).date())
    body, skeleton_body = render(listing), skeleton(listing)
    stamp = _iso(clock())
    try:
        items = [{k: v for k, v in item.items() if k != "text"} for item in listing["asks"] + listing["calendar"]]
        _write_json(root / "lists" / f"{ref}.json", {"list_ref": ref, "sent_at": stamp, "items": items})
        _prune(root / "lists")
    except OSError:
        return _quiet("list_write_failed", counts, dry=dry)
    write_handoff(listing, stamp)
    envelope = build_envelope(recipient or "", subject(ref, listing), body, skeleton_body, stamp)
    if not _H.write_dispatch_envelope(SKILL, envelope):
        return _quiet("envelope_write_failed", counts, dry=dry)
    try:
        _write_json(root / "state.json", {"listed_through": _iso(now)})
    except OSError:
        sys.stderr.write("[pre_run] post-intake-daily-list: cursor not written; the next list repeats these lines\n")
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
    _wake_row("emitted_wake_append", "EMITTED_WAKE", "list_ready", {**counts, **stamps})
    return _say({"wakeAgent": True, "dispatch_expected": True, "status": "list_ready", **counts})


def main() -> int:
    dry = os.environ.get("SMD_POST_LIST_DRY", "").strip() == "1"
    try:
        return run(_H.load_customer_yaml(None), dry=dry)
    except Exception as exc:  # noqa: BLE001 - a crashed list sends nothing, recorded
        sys.stderr.write("[pre_run] post-intake-daily-list failed: " + type(exc).__name__ + "\n")
        return _quiet("pre_run_crashed", {}, dry=dry)


if __name__ == "__main__":
    sys.exit(main())
