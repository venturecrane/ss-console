"""The task-list-keeper's read of the firm's record, and its parse.

The read runs in the Smokeball connector's own venv (the connector package is
not importable from the Hermes venv the pre_run runs in), the same subprocess
seam the escalator and the verification tracker use. It reads, per matter that
holds an overdue open task: the matter (status, responsible and assisting
staff, the client contacts' surnames), its document listing, and its calendar
inside the court window. For a document that could be a task's evidence (its
name is of a kind an overdue task on the matter names) it also reads the
document's OWN text, text layer or cached transcription only (never a billed
vision read), and reports which client surnames the text contains: a name
alone never proves a task done. Reads are capped per run
(``SMD_CASEWORK_READ_BUDGET``); a document past the cap is reported unread.
Nothing it reads is written anywhere but this process.

FAILURE DIRECTION. A failed task pull is a problem the caller reports and the
run ends without a message. A failed read of ONE matter degrades that matter
toward caution, never toward action: an unread calendar makes every task on
the matter ``at_stake`` (never proposed for closure), an unread document list
yields no "looks done" evidence, an unread document or an unresolved client
list yields none either, and an unread matter has no staff to route to.

``parse_pull`` is pure and unit-tested over captured shapes.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import date

_CONNECTOR_PYTHON_DEFAULT = "/opt/connectors/smokeball/.venv/bin/python"
_PULL_TIMEOUT_SECONDS = 120
DEFAULT_MATTER_BUDGET = 60

# Runs inside the connector venv. argv carries only the two date strings this
# process computed; the matter budget rides the env. Every per-matter read is
# reported, never allowed to kill the pull.
_PULL_SNIPPET = """\
import json
import os
import re
import sys

from smokeball_connector.client import build_client_from_env
from smokeball_connector.extract import METHOD_NONE_SCANNED, extract_text_ex
from smokeball_connector.local_time import enrich_event
from smokeball_connector.matter_ref import attach_matter_numbers
from smokeball_connector.parties import _party_surname

today, to = sys.argv[1], sys.argv[2]
budget = int(os.environ.get("SMD_CASEWORK_MATTER_BUDGET", "60"))
read_budget = [int(os.environ.get("SMD_CASEWORK_READ_BUDGET", "40"))]
# The same topic pairs as classify._TOPICS (a test pins them equal): a task's
# subject names the topic, a document's name is of that kind.
TOPICS = (
    (re.compile(r"\\bserv(?:e|ed|ice)\\b", re.I), re.compile(r"proof[\\s_-]+of[\\s_-]+service", re.I)),
    (re.compile(r"\\bverif(?:y|ied|ication)\\b", re.I), re.compile(r"\\bverification\\b", re.I)),
    (re.compile(r"\\brecords?\\b", re.I), re.compile(r"\\brecords?\\b", re.I)),
)
client = build_client_from_env()
out = {"tasks": [], "matters": {}}


def rows_of(envelope):
    if isinstance(envelope, dict):
        envelope = envelope.get("value")
    return envelope if isinstance(envelope, list) else None


def due_of(task):
    for key in ("dueDate", "DueDate", "dueDateOnly", "due_date"):
        value = task.get(key)
        if isinstance(value, str) and len(value) >= 10:
            return value[:10]
    return None


def matter_of(task):
    link = task.get("matter")
    if isinstance(link, dict) and isinstance(link.get("id"), str):
        return link["id"]
    value = task.get("matterId") or task.get("MatterId")
    return value if isinstance(value, str) else None


try:
    tasks = []
    for page in range(4):
        batch = rows_of(client.get("/tasks", IsCompleted=False, Limit=500, Offset=page * 500))
        if batch is None:
            raise ValueError("unrecognized /tasks envelope")
        tasks.extend(batch)
        if len(batch) < 500:
            break
    else:
        out["tasksTruncated"] = True
except Exception as exc:
    out["tasksError"] = str(exc)[:300]
    print(json.dumps(out, default=str))
    sys.exit(0)

overdue = [t for t in tasks if isinstance(t, dict) and (due_of(t) or "9999") < today]
out["openTaskCount"] = len(tasks)
try:
    attach_matter_numbers(client, overdue, budget=budget)
except Exception as exc:
    out["matterRefError"] = str(exc)[:300]
out["tasks"] = overdue

matter_ids = []
for task in overdue:
    mid = matter_of(task)
    if mid and mid not in matter_ids:
        matter_ids.append(mid)
out["matterCount"] = len(matter_ids)
staff_cache = {}


def staff(sid):
    if sid not in staff_cache:
        try:
            rec = client.get("/staff/" + sid)
            rec = rec if isinstance(rec, dict) else {}
            staff_cache[sid] = {"staff_id": sid, "email": rec.get("email"), "enabled": rec.get("enabled"), "former": rec.get("former")}
        except Exception as exc:
            staff_cache[sid] = {"staff_id": sid, "error": str(exc)[:200]}
    return staff_cache[sid]


def subject_of(task):
    for key in ("subject", "Subject", "name", "title"):
        value = task.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def read_names(mid, f, surnames):
    # (read, names_found) for one document: its own text, never a billed read.
    if read_budget[0] <= 0:
        return None, []
    read_budget[0] -= 1
    info, blob = client.download_file(mid, f["id"])
    result = extract_text_ex(
        blob, file_name=str(info.get("name") or ""), file_extension=str(info.get("fileExtension") or ""), allow_vision=False
    )
    text = (result.text or "").lower()
    if result.method == METHOD_NONE_SCANNED or len(text.strip()) < 100:
        return False, []
    found = [s for s in surnames if re.search("(?<![a-z])" + re.escape(s) + "(?![a-z])", text)]
    return True, found


for mid in matter_ids[:budget]:
    entry = {}
    surnames = []
    try:
        matter = client.get("/matters/" + mid)
        matter = matter if isinstance(matter, dict) else {}
        entry["status"] = matter.get("status")
        rid = matter.get("personResponsibleStaffId")
        entry["responsible"] = staff(rid) if isinstance(rid, str) and rid else None
        assisting = []
        for key in ("personAssistingStaffIds", "personAssistingStaffId", "personAssistingStaffs"):
            raw = matter.get(key)
            for sid in (raw if isinstance(raw, list) else [raw]):
                if isinstance(sid, str) and sid and sid not in assisting:
                    assisting.append(sid)
        entry["assisting"] = [staff(sid) for sid in assisting]
        client_ids = [cid for cid in (matter.get("clientIds") or []) if isinstance(cid, str) and cid]
        for cid in client_ids:
            try:
                label = _party_surname(client.get("/contacts/" + cid))
            except Exception:
                label = None
            if label:
                surnames.append(label.lower())
        entry["clientSurnames"] = surnames
        entry["clientsComplete"] = bool(client_ids) and len(surnames) == len(client_ids)
    except Exception as exc:
        entry["matterError"] = str(exc)[:200]
    try:
        files = rows_of(client.get("/matters/" + mid + "/documents/files", Limit=500)) or []
        subjects = [subject_of(t) for t in overdue if matter_of(t) == mid]
        kinds = [file_re for subject_re, file_re in TOPICS if any(subject_re.search(s) for s in subjects)]
        entry["files"] = []
        for f in files:
            if not isinstance(f, dict):
                continue
            name = f.get("name") or f.get("fileName")
            row = {"name": name, "date": f.get("dateCreated") or f.get("createdDate")}
            if name and f.get("id") and surnames and any(k.search(name) for k in kinds):
                try:
                    row["read"], row["namesFound"] = read_names(mid, f, surnames)
                except Exception as exc:
                    row["read"], row["readError"] = False, str(exc)[:120]
            entry["files"].append(row)
    except Exception as exc:
        entry["filesError"] = str(exc)[:200]
    try:
        events = rows_of(client.get("/events", MatterId=mid, From=today, To=to, ExcludeDeletedEvents=True, Limit=500))
        if events is None:
            raise ValueError("unrecognized /events envelope")
        # The court day is the connector's localDate: startTime is already the
        # firm's local clock (never UTC), read as written with no zone shift.
        entry["events"] = [
            {
                "id": e.get("id"),
                "start": e.get("localDate") or e.get("startTime") or e.get("startDate") or e.get("start"),
                "time": e.get("localTime"),
            }
            for e in (enrich_event(x) for x in events)
            if isinstance(e, dict)
        ]
    except Exception as exc:
        entry["eventsError"] = str(exc)[:200]
    out["matters"][mid] = entry

# The authored fallback recipients' own staff records, for matters with no
# responsible staff: their "yes" closes a task the way the responsible
# attorney's would. One paged /staff list (read-only, at most two pages),
# read only when a matter lacks a responsible attorney; matched by email.
try:
    wanted = {
        e.strip().lower()
        for e in json.loads(os.environ.get("SMD_CASEWORK_FALLBACK_EMAILS") or "[]")
        if isinstance(e, str) and e.strip()
    }
except ValueError:
    wanted = set()
if wanted and any(not m.get("responsible") and "matterError" not in m for m in out["matters"].values()):
    try:
        found = []
        for page in range(2):
            batch = rows_of(client.get("/staff", Limit=500, Offset=page * 500))
            if batch is None:
                raise ValueError("unrecognized /staff envelope")
            for rec in batch:
                email = rec.get("email") if isinstance(rec, dict) else None
                if isinstance(email, str) and email.strip().lower() in wanted and isinstance(rec.get("id"), str):
                    found.append(
                        {"staff_id": rec["id"], "email": email, "enabled": rec.get("enabled"), "former": rec.get("former")}
                    )
            if len(batch) < 500:
                break
        out["fallbackStaff"] = found
    except Exception as exc:
        out["fallbackStaffError"] = str(exc)[:200]
print(json.dumps(out, default=str))
"""


@dataclass(frozen=True)
class StaffRecord:
    staff_id: str
    email: str | None
    enabled: object = None
    former: object = None

    def as_routing_record(self) -> dict:
        """The shape ``routing.resolve_case_alert_routing`` reads."""
        return {"email": self.email, "enabled": self.enabled, "former": self.former}


@dataclass(frozen=True)
class MatterPull:
    matter_id: str
    status: str | None
    responsible: StaffRecord | None
    assisting: tuple[StaffRecord, ...]
    #: (name, day, read, client surnames found in the document's own text)
    files: tuple[tuple[str, date, bool | None, tuple[str, ...]], ...]
    court_days: tuple[date, ...]
    court_event_ids: tuple[str, ...]
    calendar_read: bool
    client_surnames: tuple[str, ...] = ()
    clients_complete: bool = False


@dataclass(frozen=True)
class TaskPull:
    task_id: str
    matter_id: str
    subject: str
    due: date
    created: date | None
    event_id: str | None
    matter_number: str | None
    matter_number_absent: str | None


@dataclass(frozen=True)
class Snapshot:
    tasks: tuple[TaskPull, ...]
    matters: dict
    open_task_count: int
    matters_skipped: int
    probe_excluded: int
    #: The authored fallback recipients' staff records, by lowercased email.
    #: Empty when none was read or none matched: a fallback recipient with no
    #: staff record can be told about a task but cannot have it closed for them.
    fallback_staff: dict = field(default_factory=dict)


def _day(value) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _str(value) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _staff(raw) -> StaffRecord | None:
    if not isinstance(raw, dict) or not _str(raw.get("staff_id")) or raw.get("error"):
        return None
    return StaffRecord(raw["staff_id"], _str(raw.get("email")), raw.get("enabled"), raw.get("former"))


def _subject(task: dict) -> str:
    for key in ("subject", "Subject", "name", "title"):
        value = task.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _matter_id(task: dict) -> str | None:
    link = task.get("matter")
    if isinstance(link, dict) and _str(link.get("id")):
        return link["id"]
    return _str(task.get("matterId")) or _str(task.get("MatterId"))


def _is_probe(subject: str) -> bool:
    text = subject.lstrip()
    if text.lower().startswith("[operator]"):
        text = text[len("[operator]") :].lstrip()
    return text.upper().startswith("[SMD-PROBE")


def _parse_matter(matter_id: str, raw: dict) -> MatterPull:
    files: list[tuple[str, date, bool | None, tuple[str, ...]]] = []
    for f in raw.get("files") or []:
        name, day = (_str(f.get("name")), _day(f.get("date"))) if isinstance(f, dict) else (None, None)
        if name and day:
            # Only a literal True is a read; anything else is no evidence.
            read = True if f.get("read") is True else (False if f.get("read") is False else None)
            found = tuple(s.lower() for s in f.get("namesFound") or [] if isinstance(s, str) and s)
            files.append((name, day, read, found))
    surnames = tuple(s.lower() for s in raw.get("clientSurnames") or [] if isinstance(s, str) and s)
    days: list[date] = []
    ids: list[str] = []
    for e in raw.get("events") or []:
        day = _day(e.get("start")) if isinstance(e, dict) else None
        if day:
            days.append(day)
            if _str(e.get("id")):
                ids.append(e["id"])
    return MatterPull(
        matter_id=matter_id,
        status=_str(raw.get("status")),
        responsible=_staff(raw.get("responsible")),
        assisting=tuple(s for s in (_staff(r) for r in raw.get("assisting") or []) if s is not None),
        files=tuple(files),
        court_days=tuple(days),
        court_event_ids=tuple(ids),
        calendar_read="eventsError" not in raw and isinstance(raw.get("events"), list),
        client_surnames=surnames,
        clients_complete=raw.get("clientsComplete") is True and bool(surnames),
    )


def parse_pull(raw: dict) -> tuple[Snapshot | None, str | None]:
    """``(snapshot, problem)``: a non-None problem means no snapshot."""
    if not isinstance(raw, dict):
        return None, "unrecognized pull output"
    if raw.get("tasksError"):
        return None, "task pull failed: " + str(raw["tasksError"])[:200]
    if not isinstance(raw.get("tasks"), list) or not isinstance(raw.get("matters"), dict):
        return None, "unrecognized pull output"
    tasks: list[TaskPull] = []
    probes = 0
    for t in raw["tasks"]:
        if not isinstance(t, dict):
            continue
        subject = _subject(t)
        if _is_probe(subject):
            probes += 1
            continue
        task_id, matter_id = _str(t.get("id")), _matter_id(t)
        due = _day(t.get("dueDate")) or _day(t.get("dueDateOnly")) or _day(t.get("DueDate"))
        if not (task_id and matter_id and due):
            continue
        raw_event = t.get("event")
        event: dict = raw_event if isinstance(raw_event, dict) else {}
        tasks.append(
            TaskPull(
                task_id=task_id,
                matter_id=matter_id,
                subject=subject,
                due=due,
                created=_day(t.get("dateCreated")) or _day(t.get("createdDate")),
                event_id=_str(t.get("eventId")) or _str(event.get("id")),
                matter_number=_str(t.get("matterNumber")),
                matter_number_absent=_str(t.get("matterNumberAbsent")),
            )
        )
    matters = {mid: _parse_matter(mid, m) for mid, m in raw["matters"].items() if isinstance(m, dict)}
    count = raw.get("matterCount")
    skipped = max(0, count - len(matters)) if isinstance(count, int) else 0
    opened = raw.get("openTaskCount")
    fallback = {}
    for record in raw.get("fallbackStaff") or []:
        staff = _staff(record)
        if staff is not None and staff.email:
            fallback[staff.email.strip().lower()] = staff
    return (
        Snapshot(tuple(tasks), matters, opened if isinstance(opened, int) else len(tasks), skipped, probes, fallback),
        None,
    )


def fallback_emails(customer_yaml: dict) -> list[str]:
    """The authored ``escalation.case_alert_routing.fallback_recipients``."""
    esc = customer_yaml.get("escalation") if isinstance(customer_yaml, dict) else None
    routing = esc.get("case_alert_routing") if isinstance(esc, dict) else None
    raw = routing.get("fallback_recipients") if isinstance(routing, dict) else None
    return [e.strip() for e in raw if isinstance(e, str) and e.strip()] if isinstance(raw, list) else []


def run_pull(
    today: date, window_days: int, budget: int = DEFAULT_MATTER_BUDGET, fallback: list[str] | None = None
) -> dict:
    """Run the connector-venv read and return its raw JSON. Raises on a failed
    subprocess; the caller turns that into a reported problem. ``fallback`` is
    the authored fallback recipients, whose staff records the read resolves."""
    from datetime import timedelta

    connector_python = os.environ.get("SMD_CONNECTOR_VENV_PYTHON", _CONNECTOR_PYTHON_DEFAULT)
    env = dict(os.environ)
    env["SMD_CASEWORK_MATTER_BUDGET"] = str(max(0, budget))
    env["SMD_CASEWORK_FALLBACK_EMAILS"] = json.dumps(fallback or [])
    result = subprocess.run(  # noqa: S603 - connector-venv interpreter, a module-constant snippet, two isoformat dates; no shell
        # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args — argv[0] is the module-constant connector-venv interpreter, overridable only via SMD_CONNECTOR_VENV_PYTHON from the Machine's own boot env (same trust domain; the test seam). The snippet is a module constant; the two dates are date.isoformat() strings computed here, never external input.
        [connector_python, "-c", _PULL_SNIPPET, today.isoformat(), (today + timedelta(days=window_days)).isoformat()],
        capture_output=True,
        text=True,
        timeout=_PULL_TIMEOUT_SECONDS,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"smokeball pull exit {result.returncode}: {(result.stderr or '').strip()[:300]}")
    return json.loads((result.stdout or "").strip().splitlines()[-1])
