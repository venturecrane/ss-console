"""date-prep-brief: the cross-record reads, run in the CONNECTOR venv.

Loaded by path inside the connector interpreter (``pre_run._PULL_SNIPPET``)
and called with a real Smokeball client, and imported in-process by the tests
with a stub client: one implementation, so the tested behaviour is the shipped
behaviour (the ``pre_run_gate.py`` facts pattern).

Two reads, both returning FACTS, never prose:

* :func:`pull_dates` - the calendar events in the window, per open matter,
  through ``/events?MatterId=<id>`` with deleted events excluded (Smokeball
  deletion is soft and the vendor default lists tombstones). The unfiltered ``/events`` list reports
  every event as unlinked (probed 2026-09-24 on a client tenant), so the
  per-matter filter is the only proof an event belongs to a matter.
* :func:`pull_matter_status` - one matter's file status: file names and days,
  the DAY of each ``[Operator]`` memo that carries a prep routine's own marker,
  and its records-request roster tasks, each received provider with the day of
  its newest record on file (matched on the file NAME, never its content). Memo bodies are read HERE and never
  leave: only the marker kind and its day cross the process boundary, so no
  memo prose reaches the scheduled session (the matter-mixing fence refuses a
  second matter's content in one session; code reading across matters and
  handing the model facts is the structure that keeps it from asking).

File names DO cross: a name is record metadata the brief may show ("Witness
List (draft)"), and the turn reads the named document before it writes a word
about what is in it.
"""

from __future__ import annotations

import re
import sys

#: Matters whose calendars one tick reads. Past it the view says truncated.
MATTER_CAP = 40
#: One page, matching the connector's own default. A full page is a partial view.
PAGE_LIMIT = 500
#: Files carried per matter. The brief cites a handful; the rest is noise.
FILE_CAP = 60

_ENVELOPE_KEYS = ("value", "items", "results", "data", "matters", "tasks", "memos", "events")
_ISO_DAY = re.compile(r"\A\d{4}-\d{2}-\d{2}")
_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")

PROBE_MARK = "[SMD-PROBE"
PROVENANCE_MARK = "[Operator]"

#: The prep routines' own memo markers (each routine's references/output-format.md).
#: Only [Operator] memos count: the marker is the routine saying it ran.
MEMO_MARKERS: dict[str, tuple[str, ...]] = {
    "binder_assembled": ("trial binder index assembled", "# trial binder -"),
    "motion_calendar": ("motion calendar assembled", "# motion calendar"),
    "discovery_status": ("# response deadline", "# deadline confirmed", "# opposing discovery"),
}

#: The records-request roster convention medical-records-chaser keys on.
ROSTER_MARKER = "request roster"
_ROSTER_PROVIDER = re.compile(r"-\s*(?P<provider>[^()]+?)\s*\(request roster\)", re.IGNORECASE)


def _listed(payload) -> list | None:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in _ENVELOPE_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return None


def _first(record: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _day(record: dict, keys: tuple[str, ...]) -> str | None:
    value = _first(record, keys)
    return value[:10] if value and _ISO_DAY.match(value) else None


def _id(record: dict, keys: tuple[str, ...] = ("id", "Id")) -> str | None:
    value = _first(record, keys)
    return value if value and _ID.match(value) else None


def _subject(record: dict) -> str:
    return _first(record, ("subject", "Subject", "title", "Title", "name", "Name")) or ""


def _is_probe(subject: str) -> bool:
    text = subject.lstrip()
    if text.startswith(PROVENANCE_MARK):
        text = text[len(PROVENANCE_MARK) :].lstrip()
    return text.startswith(PROBE_MARK)


def _warn(what: str, exc: Exception) -> None:
    # The exception TYPE only: a message can quote the payload it failed on.
    sys.stderr.write("[date-prep] " + what + " failed: " + type(exc).__name__ + "\n")


def matter_events(client, matter: dict, frm: str, to: str) -> list[dict] | None:
    """One matter's events in ``[frm, to]``, or None when the read failed."""
    matter_id = _id(matter)
    if matter_id is None:
        return None
    try:
        listed = _listed(
            client.get("/events", MatterId=matter_id, From=frm, To=to, ExcludeDeletedEvents=True, Limit=PAGE_LIMIT)
        )
    except Exception as exc:  # noqa: BLE001 - one matter failing degrades that matter, never the run
        _warn("event read", exc)
        return None
    if listed is None:
        return None
    number = _first(matter, ("number", "Number"))
    rows = []
    for event in listed:
        if not isinstance(event, dict):
            continue
        event_id, day, subject = (
            _id(event),
            _day(event, ("startTime", "StartTime", "startDate", "start")),
            _subject(event),
        )
        if event_id is None or day is None or _is_probe(subject):
            continue
        rows.append(
            {
                "event_id": event_id,
                "date": day,
                "subject": subject[:120],
                "matter_id": matter_id,
                "matter_number": number,
            }
        )
    return rows


def pull_dates(client, frm: str, to: str) -> dict:
    """Every open matter's calendar events in the window, per matter."""
    matters = _listed(client.get("/matters", Status="Open", Limit=MATTER_CAP + 1))
    if matters is None:
        return {"envelopeUnknown": True}
    out: dict = {"events": [], "unreadableMatters": 0, "mattersTruncated": len(matters) > MATTER_CAP}
    for matter in matters[:MATTER_CAP]:
        rows = matter_events(client, matter, frm, to) if isinstance(matter, dict) else None
        if rows is None:
            out["unreadableMatters"] += 1
        else:
            out["events"].extend(rows)
    return out


def _files(client, matter_id: str) -> tuple[list[dict], bool]:
    listed = _listed(client.get("/matters/" + matter_id + "/documents/files", Limit=PAGE_LIMIT))
    if listed is None:
        raise ValueError("unrecognised file envelope")
    files = []
    for record in listed:
        if not isinstance(record, dict):
            continue
        file_id, name = _id(record), _first(record, ("name", "fileName", "Name"))
        if file_id is None or name is None:
            continue
        ext = _first(record, ("fileExtension",)) or ""
        day = _day(record, ("dateModified", "dateCreated", "createdDate"))
        files.append({"file_id": file_id, "name": (name + ext)[:160], "date": day})
    files.sort(key=lambda f: f["date"] or "", reverse=True)
    return files, len(listed) >= PAGE_LIMIT


#: Words too generic to tell one provider from another in a file name.
_GENERIC = frozenset(
    {"dr", "md", "do", "the", "of", "and", "center", "centre", "medical", "group", "clinic", "hospital", "inc", "llc"}
)
_WORD = re.compile(r"[a-z0-9]+")


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def newest_record(provider: str, files: list[dict]) -> str | None:
    """The newest dated file whose name carries every distinctive word of the
    provider's name ("Dr. Reyes" -> reyes; "Valley Imaging Center" -> valley,
    imaging). A name match on metadata only, the records chaser's own receipt
    rule; no match or no date is None, which means unknown, never stale."""
    wanted = {w for w in _words(provider) if w not in _GENERIC and len(w) > 1}
    if not wanted:
        return None
    days = [f["date"] for f in files if f.get("date") and wanted <= _words(f.get("name") or "")]
    return max(days) if days else None


def memo_markers(memos: list) -> dict[str, str]:
    """``{marker kind: latest day}`` over ``[Operator]`` memos. The body is read
    and dropped; only the kind and the day are returned."""
    latest: dict[str, str] = {}
    for memo in memos:
        if not isinstance(memo, dict):
            continue
        body = _first(memo, ("plainText", "PlainText", "text", "Text")) or ""
        day = _day(memo, ("createdDate", "CreatedDate", "dateCreated", "created"))
        if day is None or not body.lstrip().startswith(PROVENANCE_MARK):
            continue
        lowered = body.lower()
        for kind, phrases in MEMO_MARKERS.items():
            if any(phrase in lowered for phrase in phrases) and day > latest.get(kind, ""):
                latest[kind] = day
    return latest


def roster_tasks(tasks: list, *, completed: bool) -> list[dict]:
    """The matter's records-request roster tasks: id, provider, open or received."""
    rows = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        subject = _subject(task)
        task_id = _id(task)
        if task_id is None or ROSTER_MARKER not in subject.lower() or _is_probe(subject):
            continue
        match = _ROSTER_PROVIDER.search(subject)
        provider = match.group("provider").strip() if match else subject
        rows.append(
            {"roster_task_id": task_id, "provider": provider[:120], "state": "received" if completed else "outstanding"}
        )
    return rows


def pull_matter_status(client, matter_id: str) -> dict:
    """One matter's file status as facts. Each part degrades on its own, and
    says so: an unread part is ``unknown``, never an empty result."""
    status: dict = {"matter_id": matter_id, "unread": []}
    every_file: list[dict] = []
    try:
        every_file, status["files_truncated"] = _files(client, matter_id)
    except Exception as exc:  # noqa: BLE001 - an unread part is reported, not guessed
        _warn("file read", exc)
        status["unread"] = status["unread"] + ["files"]
    status["files"] = every_file[:FILE_CAP]
    try:
        memos = _listed(client.get("/matters/" + matter_id + "/memos", Limit=PAGE_LIMIT))
        if memos is None:
            raise ValueError("unrecognised memo envelope")
        status["memo_markers"] = memo_markers(memos)
    except Exception as exc:  # noqa: BLE001 - an unread memo page is reported as unread, never as no markers
        _warn("memo read", exc)
        status["memo_markers"], status["unread"] = {}, status["unread"] + ["memos"]
    records: list[dict] = []
    for completed in (False, True):
        try:
            listed = _listed(client.get("/tasks", MatterId=matter_id, IsCompleted=completed, Limit=PAGE_LIMIT))
            records.extend(roster_tasks(listed or [], completed=completed))
        except Exception as exc:  # noqa: BLE001 - an unread task page is reported as unread, never as no roster
            _warn("task read", exc)
            if "tasks" not in status["unread"]:
                status["unread"] = status["unread"] + ["tasks"]
    for record in records:
        if record["state"] == "received" and "files" not in status["unread"]:
            record["newest_record"] = newest_record(record["provider"], every_file)
    status["records"] = records
    return status
