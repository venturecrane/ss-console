"""The conflict check: is the person writing in, or a party they name, on the
other side of one of the firm's open matters?

WHY A TOOL. The matter inbox router's conflict step was prose: "a read-only
get_contacts + list_matters name/entity check". The model chose which names to
search, read the matters, decided what counted as adverse, and wrote the
clearance task's subject and note itself. A conflict it missed went on to a
reply, a handoff or a write, and nothing in code knew a check had been owed.
Now code does the search and the decision under one written rule table, and
the overlay's trust gate (``hermes-smd-trust``, ``conflict_fence.py``) refuses
every write and send in a turn from a sender off the firm's roster until this
tool has returned ``clear`` for that sender, and everything after a ``hit``.

THE RULE TABLE (the design's, 2026-11-04; nothing here adds to it):

    hit            the sender, or a party the message names, is a Smokeball
                   contact in an ADVERSE role on an OPEN matter, and that matter
                   is not the one the router already resolved the message to
                   (``resolved_matter_id`` empty or different): a new
                   relationship with someone the firm is against.
    clear          otherwise.
    search_failed  any fault reading the tenant, or a request this tool cannot
                   search completely. Never ``clear``.

An ADVERSE role is one of three, read from the matter's own records:

    other_side        the contact is one of the matter's other-side parties;
    opposing_counsel  a relationship on an other-side role whose name says
                      counsel (attorney, counsel, lawyer, solicitor, law firm);
    their_carrier     a relationship on an other-side role whose name says
                      insurance (insurer, insurance, carrier, adjuster).

FORMAL SERVICE IS THE CALLER'S CARVE-OUT. Opposing counsel serving a captioned
litigation document is the adversarial system working; the router classifies
that as served-document intake and handles it by its own rule. This tool does
not know what a message is, only who is in it. The carve-out holds where the
trust gate does not fence: a colleague on the roster forwarding the papers. An
outside sender's email is fenced whatever it is, so the router checks it, and
a hit leaves the firm a clearance task rather than a capture nobody saw.

ON A HIT THE TOOL WRITES THE ONE CLEARANCE TASK ITSELF (routing-rubric.md,
"the clearance surface is a task"), so the halt is never silent and its words
are never the model's:

    subject  Conflict clearance: inbound from <sender> re <subject line> (held, no reply sent)
    due      the next business day on the firm's clock (weekends skipped)
    matter   the matter the router resolved, else firm-general
    owner    that matter's responsible staff member, else the responsible
             staff member of the first matter the hit names
    note     one line per conflict: the matter number, the contact's name AS
             SMOKEBALL RECORDS IT, and the role. Never a word of the message
             body: a named party is a search term here, never task text.

Plain ASCII throughout (the internal-write content filter refuses an em dash,
and a task that fights the filter on every halt is a retry loop; seen live
2026-07-05). A second call for the same sender and subject in this process
returns the task already created, never a second one; a task on a matter is
also found again on the matter's open tasks after a restart.

Classified ``read``: the task is the connector's own write, recorded and
stamped like every other (``write_record``, ``PROVENANCE_MARK``), and the
model's only way to it is a hit this code decided.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
import unicodedata
from collections import OrderedDict
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from .form_letter_facts import contact_name, seat_today
from .library import _listing as listing
from .matter_resolution import SearchFailed, contacts_by_name
from .readback_records import prove_task
from .task_update import PROVENANCE_MARK

VERDICT_HIT = "hit"
VERDICT_CLEAR = "clear"
VERDICT_SEARCH_FAILED = "search_failed"

ROLE_OTHER_SIDE = "other_side"
ROLE_OPPOSING_COUNSEL = "opposing_counsel"
ROLE_THEIR_CARRIER = "their_carrier"
_ROLE_LABEL = {
    ROLE_OTHER_SIDE: "the other side",
    ROLE_OPPOSING_COUNSEL: "opposing counsel",
    ROLE_THEIR_CARRIER: "the other side's insurer",
}

_COUNSEL = re.compile(r"attorney|counsel|lawyer|solicitor|law firm", re.IGNORECASE)
_CARRIER = re.compile(r"insur|carrier|adjuster", re.IGNORECASE)

#: A conflict check is only as good as its coverage, so a request this tool
#: cannot search to the end is refused rather than half searched.
MAX_PARTIES = 10
_PAGE = 50
_MAX_PAGES = 10
_SENDER_CHARS = 120
_SUBJECT_CHARS = 150
_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

#: The firm's calendar day; module-level so a test pins it.
TODAY: Callable[[], date] = seat_today

_ASCII_MAP = str.maketrans(
    {
        chr(0x2014): "-",  # em dash
        chr(0x2013): "-",  # en dash
        chr(0x2018): "'",
        chr(0x2019): "'",
        chr(0x201C): '"',
        chr(0x201D): '"',
        chr(0x2026): "...",
    }
)

#: Clearance tasks this process created, by sender and subject (see the module
#: docstring). Bounded, and old entries expire.
_TASK_TTL_SECONDS = 12 * 3600
_MAX_TASKS = 256
_LOCK = threading.Lock()
_TASKS: OrderedDict[str, tuple[float, str]] = OrderedDict()


def _reset_for_tests() -> None:
    with _LOCK:
        _TASKS.clear()


def plain_ascii(value: Any, limit: int) -> str:
    """One line of plain ASCII: typographic punctuation mapped, accents folded,
    control characters and runs of space collapsed, then capped."""
    text = value if isinstance(value, str) else ""
    text = unicodedata.normalize("NFKD", text.translate(_ASCII_MAP)).encode("ascii", "ignore").decode()
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _ids(values: Any) -> set[str]:
    return {v.casefold() for v in values or [] if isinstance(v, str) and v}


def _contact_of(record: dict[str, Any]) -> str:
    cid = record.get("contactId")
    if not cid and isinstance(record.get("contact"), dict):
        cid = record["contact"].get("id")
    return cid.casefold() if isinstance(cid, str) else ""


def _roles(client: Any, matter_id: str) -> list[dict[str, Any]]:
    try:
        resp = client.get(f"/matters/{matter_id}/roles")
    except Exception as exc:
        raise SearchFailed(f"the matter's roles could not be read ({exc.__class__.__name__})") from exc
    if isinstance(resp, dict) and isinstance(resp.get("roles"), list):
        return [r for r in resp["roles"] if isinstance(r, dict)]
    return listing(resp)


def adverse_role(client: Any, contact_id: str, matter: dict[str, Any], roles_cache: dict[str, Any]) -> str | None:
    """The contact's adverse role on this matter, or None (a client, or a role
    the table does not name)."""
    cid = contact_id.casefold()
    if cid in _ids(matter.get("otherSideIds")):
        return ROLE_OTHER_SIDE
    if cid in _ids(matter.get("clientIds")):
        return None
    mid = str(matter.get("id") or "")
    if mid not in roles_cache:
        roles_cache[mid] = _roles(client, mid)
    for role in roles_cache[mid]:
        if role.get("isOtherSide") is not True:
            continue
        if _contact_of(role) == cid:
            return ROLE_OTHER_SIDE
        for rel in role.get("relationships") or []:
            if not isinstance(rel, dict) or _contact_of(rel) != cid:
                continue
            name = str(rel.get("name") or "")
            if _COUNSEL.search(name):
                return ROLE_OPPOSING_COUNSEL
            if _CARRIER.search(name):
                return ROLE_THEIR_CARRIER
    return None


def _open_matters(client: Any, contact_id: str) -> list[dict[str, Any]]:
    """Every OPEN matter the contact is on, to the last page, or SearchFailed."""
    out: list[dict[str, Any]] = []
    for page in range(_MAX_PAGES):
        try:
            rows = listing(
                client.get("/matters", ContactId=contact_id, Status="Open", Limit=_PAGE, Offset=page * _PAGE)
            )
        except Exception as exc:
            raise SearchFailed(f"the contact's matters could not be read ({exc.__class__.__name__})") from exc
        out.extend(m for m in rows if isinstance(m.get("id"), str) and str(m.get("status") or "Open") == "Open")
        if len(rows) < _PAGE:
            return out
    raise SearchFailed("a contact is on more open matters than this check reads; it cannot answer clear")


def _conflicts_for(client: Any, name: str, matched: str, cache: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for contact in contacts_by_name(client, name):
        cid = str(contact.get("id") or "")
        for matter in _open_matters(client, cid):
            role = adverse_role(client, cid, matter, cache)
            if role:
                found.append(
                    {
                        "matter_id": str(matter["id"]),
                        "matter_number": str(matter.get("number") or ""),
                        "role": role,
                        "matched": matched,
                        "contact_name": plain_ascii(contact_name(contact) or "", 80),
                        "staff_id": str(matter.get("personResponsibleStaffId") or ""),
                    }
                )
    return found


def _sender_label(sender_name: str, sender_email: str) -> str:
    name = plain_ascii(sender_name, _SENDER_CHARS)
    email = plain_ascii(sender_email, _SENDER_CHARS).lower()
    if name and email:
        return f"{name} <{email}>"[:_SENDER_CHARS]
    return name or email or "an unknown sender"


def clearance_subject(sender_name: str, sender_email: str, subject_line: str) -> str:
    """The rubric's fixed subject, from the sender identity and subject line only."""
    subject = plain_ascii(subject_line, _SUBJECT_CHARS) or "(no subject)"
    return (
        f"Conflict clearance: inbound from {_sender_label(sender_name, sender_email)} "
        f"re {subject} (held, no reply sent)"
    )


def clearance_note(conflicts: list[dict[str, Any]]) -> str:
    """One line per conflict, from Smokeball's own records only."""
    lines = [
        "Held for conflict clearance. The Operator did not reply to, route or draft for this message.",
        "Possible conflicts on the firm's open matters:",
    ]
    for c in conflicts:
        who = c["contact_name"] or "a contact"
        lines.append(f"- Matter {c['matter_number'] or c['matter_id']}: {who} is {_ROLE_LABEL[c['role']]}.")
    lines.append("Clear or decline the conflict before anyone responds.")
    return "\n".join(lines)


def next_business_day(today: date) -> date:
    day = today + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _dedup_key(sender_name: str, sender_email: str, subject_line: str) -> str:
    raw = "|".join(
        (plain_ascii(sender_email, 200).lower(), plain_ascii(sender_name, 200).lower(), plain_ascii(subject_line, 200))
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _remembered(key: str) -> str | None:
    now = time.monotonic()
    with _LOCK:
        held = _TASKS.get(key)
        if held is None or now - held[0] > _TASK_TTL_SECONDS:
            _TASKS.pop(key, None)
            return None
        return held[1]


def _remember(key: str, task_id: str) -> None:
    with _LOCK:
        _TASKS[key] = (time.monotonic(), task_id)
        _TASKS.move_to_end(key)
        while len(_TASKS) > _MAX_TASKS:
            _TASKS.popitem(last=False)


def _existing_on_matter(client: Any, matter_id: str, subject: str) -> str | None:
    """An open task already on the matter with this exact subject (a restart
    must not leave the firm two clearance tasks for one message)."""
    try:
        rows = listing(client.get("/tasks", MatterId=matter_id, IsCompleted=False, Limit=500))
    except Exception:  # noqa: BLE001 - an unreadable list just means a new task is created
        return None
    for row in rows:
        if str(row.get("subject") or "") == subject and isinstance(row.get("id"), str):
            return row["id"]
    return None


def _owner(client: Any, matter_id: str | None, conflicts: list[dict[str, Any]]) -> str:
    if matter_id:
        matter = client.get(f"/matters/{matter_id}")
        staff = matter.get("personResponsibleStaffId") if isinstance(matter, dict) else None
        return staff if isinstance(staff, str) else ""
    for c in sorted(conflicts, key=lambda c: c["matter_number"]):
        if c["staff_id"]:
            return c["staff_id"]
        matter = client.get(f"/matters/{c['matter_id']}")
        staff = matter.get("personResponsibleStaffId") if isinstance(matter, dict) else None
        if isinstance(staff, str) and staff:
            return staff
    return ""


def create_clearance_task(
    client: Any,
    conflicts: list[dict[str, Any]],
    *,
    sender_name: str,
    sender_email: str,
    subject_line: str,
    resolved_matter_id: str | None,
) -> tuple[str | None, str]:
    """``(task id, "")`` for the one clearance task, or ``(None, why not)``."""
    key = _dedup_key(sender_name, sender_email, subject_line)
    remembered = _remembered(key)
    if remembered:
        return remembered, ""
    subject = f"{PROVENANCE_MARK} {clearance_subject(sender_name, sender_email, subject_line)}"
    matter_id = resolved_matter_id if resolved_matter_id and _GUID.match(resolved_matter_id) else None
    try:
        if matter_id:
            existing = _existing_on_matter(client, matter_id, subject)
            if existing:
                _remember(key, existing)
                return existing, ""
        staff = _owner(client, matter_id, conflicts)
        if not staff:
            return None, "no responsible staff member could be read to own the clearance task"
        body: dict[str, Any] = {
            "staffId": staff,
            "subject": subject,
            "note": clearance_note(conflicts),
            "dueDateOnly": next_business_day(TODAY()).isoformat(),
        }
        if matter_id:
            body["matterId"] = matter_id
        result = client.request("POST", "/tasks", json=body)
    except Exception as exc:  # noqa: BLE001 - a task that did not land is reported, never assumed
        return None, f"the clearance task could not be created ({exc.__class__.__name__})"
    task_id = str(result.get("id") or "") if isinstance(result, dict) else ""
    if not task_id:
        return None, "the clearance task write returned no id"
    prove_task(client, body, result)
    _remember(key, task_id)
    return task_id, ""


def _searchable(sender_name: str, named_parties: Any) -> tuple[list[tuple[str, str]], str]:
    """``(names to search with what each is, "")`` or ``([], why the request
    cannot be searched completely)``."""
    if named_parties is not None and not isinstance(named_parties, list):
        return [], "named_parties must be a list of names"
    parties = [p for p in named_parties or [] if isinstance(p, str) and p.strip()]
    if len(parties) > MAX_PARTIES:
        return [], f"at most {MAX_PARTIES} named parties can be checked in one call"
    if not isinstance(sender_name, str) or not sender_name.strip():
        return [], "the sender's name is required (as the email's From line or signature gives it)"
    return [(sender_name.strip(), "sender")] + [(p.strip(), "named_party") for p in parties], ""


def conflict_check(
    client: Any,
    *,
    sender_name: Any,
    sender_email: Any,
    subject_line: Any,
    named_parties: Any = None,
    resolved_matter_id: Any = None,
) -> dict[str, Any]:
    """The verdict for one inbound message. See the module docstring."""
    names, why = _searchable(sender_name, named_parties)
    if not names:
        return {
            "verdict": VERDICT_SEARCH_FAILED,
            "matters": [],
            "clearance_task_id": None,
            "searched": [],
            "reason": why,
        }
    resolved = resolved_matter_id.strip().casefold() if isinstance(resolved_matter_id, str) else ""
    cache: dict[str, Any] = {}
    conflicts: list[dict[str, Any]] = []
    try:
        for name, matched in names:
            conflicts.extend(_conflicts_for(client, name, matched, cache))
    except SearchFailed as exc:
        return {
            "verdict": VERDICT_SEARCH_FAILED,
            "matters": [],
            "clearance_task_id": None,
            "searched": [n for n, _ in names],
            "reason": str(exc),
        }
    hits = [c for c in conflicts if c["matter_id"].casefold() != resolved]
    out: dict[str, Any] = {
        "verdict": VERDICT_HIT if hits else VERDICT_CLEAR,
        "matters": [{k: c[k] for k in ("matter_id", "matter_number", "role", "matched")} for c in _unique(hits)],
        "clearance_task_id": None,
        "searched": [n for n, _ in names],
    }
    if hits:
        task_id, error = create_clearance_task(
            client,
            _unique(hits),
            sender_name=str(sender_name),
            sender_email=sender_email if isinstance(sender_email, str) else "",
            subject_line=subject_line if isinstance(subject_line, str) else "",
            resolved_matter_id=resolved_matter_id if isinstance(resolved_matter_id, str) else None,
        )
        out["clearance_task_id"] = task_id
        if error:
            out["clearance_task_error"] = error
    return out


def _unique(conflicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for c in sorted(conflicts, key=lambda c: (c["matter_number"], c["role"])):
        key = (c["matter_id"], c["role"])
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _client() -> Any:
    from . import server

    return server._get_client()


def conflict_check_tool(
    sender_name: str,
    sender_email: str,
    subject_line: str,
    named_parties: list[str] | None = None,
    resolved_matter_id: str | None = None,
) -> Any:
    """Check one inbound message for a conflict under the firm's rule table. Call
    this only when a skill's written step names ``conflict_check``, or when a
    refusal this turn tells you to run it; otherwise follow the conflict step your
    skill already gives you.

    Pass the sender's name and address and the subject line exactly as the
    email's own headers give them, every party the message names (people and
    companies, at most 10), and ``resolved_matter_id`` when you already
    resolved the message to one matter from Smokeball (empty otherwise).

    ``verdict`` is ``clear``, ``hit`` (someone in the message is on the other side
    of an open matter; a clearance task now exists and ``clearance_task_id`` is its
    id), or ``search_failed`` (the check could not be completed, which is not
    clear)."""
    # WHY THE DESCRIPTION SAYS ONLY THIS. The tool reaches a seat before the skills
    # that call it, and on a hit it writes a task. What a verdict means for the rest
    # of the turn is the skill's text and, when enforcing, the trust gate's refusal
    # (hermes-smd-trust conflict_fence.py); a description that said "nothing is
    # written until this returns clear" would change what the model does on a seat
    # still running its old conflict step with the fence only recording.
    return conflict_check(
        _client(),
        sender_name=sender_name,
        sender_email=sender_email,
        subject_line=subject_line,
        named_parties=named_parties,
        resolved_matter_id=resolved_matter_id,
    )


def register(server: Any) -> None:
    """Register the read onto the connector's server."""
    server.tool(name="conflict_check")(conflict_check_tool)


__all__ = [
    "ROLE_OPPOSING_COUNSEL",
    "ROLE_OTHER_SIDE",
    "ROLE_THEIR_CARRIER",
    "VERDICT_CLEAR",
    "VERDICT_HIT",
    "VERDICT_SEARCH_FAILED",
    "adverse_role",
    "clearance_note",
    "clearance_subject",
    "conflict_check",
    "conflict_check_tool",
    "next_business_day",
    "register",
]
