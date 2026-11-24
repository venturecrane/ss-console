"""``drafting_job_add_to_request``: the broker's half of a requester adding to a
drafting job that is still running (2026-11-05, A&P; the runner's half and the
reasons for the split are in ``runners/jobs/jobs/request_additions.py``).

Everything the broker can check it checks here, before anything is written: the
job, the sender against the job's requester, the email's thread against the
job's request email, and the job's state. Then it asks the root daemon, the only
process that can see how far the job has got, and waits for the answer. The
result, not the model, decides what the requester is told.

THE WORDS. The overlay's tool reads them, and the email's thread, from the
seat's request index for the turn's verified email (the broker's uid cannot read
that file), so no model types them; this verb is gateway-or-root, as submit is.

WAITING. The tool's turn waits while the daemon answers, which takes about a
second while a job runs. When it does not answer in time the marker is taken
back, so nothing can land after the requester was told it did not; a marker the
daemon had already taken gets a short while longer.

ABOUT FILED DOCUMENTS (``about_filed``, 2026-11-24). An email in the thread of
documents already filed on the matter (a delivered job of the same class, or a
failed one SMD filed by hand) that comments on them reaches every draft of that
class still running on the matter too: the attorney's "the interrogatories need
follow-up questions" is about the kind of set, not only the five he was reading.
The overlay sends it after its submit (``underway_job_id``) or when this verb
answers ``filed_in_thread``. Then the job's own thread and requester are not
required; instead the email's thread must hold a filed job of the target's class
on the target's matter that the sender asked for (``filed_job``).

WORDS THE DOOR REFUSED are kept on the job's row (``additions_not_covered`` in
the delivery report, the same place the markers live), so the delivery reply
names them and a revision in the same thread can carry them. An audit row never
holds them.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

from .drafting_ledger import MAX_REQUEST_TEXT, DraftingLedger

#: What a request email can hold in one go (the index keeps about 4,000).
MAX_WORDS = 6_000
#: How long the tool's turn waits for the daemon, and past that for an answer
#: when the daemon had already picked the marker up.
WAIT_SECONDS = 20.0
LATE_SECONDS = 5.0
POLL_SECONDS = 0.1
AUDIT_TYPE = "DRAFTING_JOB_REQUEST_ADDED"
#: The file names ``request_additions.py`` reads and writes (the runner's
#: package is not on the broker's path; a test pins the two together).
MARKER_PREFIX = ".add-"
ANSWER_PREFIX = ".added-"
IN_FLIGHT = ("submitted", "running")


def _key(ref: str) -> str:
    return hashlib.sha256(ref.strip().encode("utf-8")).hexdigest()[:32]


def _bare(ref: str) -> str:
    return str(ref).strip().strip("<>").lower()


def ask_runner(queue: Path, job_id: str, key: str, marker: dict[str, Any], wait: float) -> dict[str, Any] | None:
    """Drop the marker and wait for the daemon's answer; None when it gave none."""
    queue.mkdir(parents=True, exist_ok=True)
    path = queue / f"{MARKER_PREFIX}{job_id}-{key}.json"
    reply = queue / f"{ANSWER_PREFIX}{job_id}-{key}.json"
    reply.unlink(missing_ok=True)
    tmp = queue / f".tmp-{path.name}"
    tmp.write_text(json.dumps(marker), encoding="utf-8")
    tmp.chmod(0o640)
    tmp.replace(path)

    def await_reply(seconds: float) -> dict[str, Any] | None:
        end = time.monotonic() + seconds
        while True:
            try:
                out = json.loads(reply.read_text(encoding="utf-8"))
            except (FileNotFoundError, ValueError):
                out = None
            if isinstance(out, dict):
                reply.unlink(missing_ok=True)
                return out
            if time.monotonic() >= end:
                return None
            time.sleep(POLL_SECONDS)

    got = await_reply(wait)
    if got is not None:
        return got
    try:
        path.unlink()
    except FileNotFoundError:  # the daemon has it: its answer is on the way
        return await_reply(LATE_SECONDS)
    return None


def note_addition(
    db: DraftingLedger, job_id: str, *, key: str, digest: str, at: str, by: str, words: str | None
) -> None:
    """On the job's row: the envelope digest the daemon reports after writing the
    words, and, when the door refused them, the words for the delivery reply.
    Once per email."""
    conn = db._connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute("SELECT delivery_json FROM drafting_jobs WHERE id=?", (job_id,)).fetchone()
        if cur is None:
            conn.rollback()
            return
        delivery = json.loads(cur["delivery_json"]) if cur["delivery_json"] else {}
        kept = list(delivery.get("additions_not_covered") or [])
        if words and all(a.get("key") != key for a in kept):
            delivery["additions_not_covered"] = [*kept, {"key": key, "at": at, "by": by, "words": words}]
        conn.execute(
            "UPDATE drafting_jobs SET envelope_digest=COALESCE(NULLIF(?, ''), envelope_digest), "
            "delivery_json=CASE WHEN ? THEN ? ELSE delivery_json END WHERE id=?",
            (digest, bool(delivery), json.dumps(delivery, sort_keys=True), job_id),
        )
        conn.commit()
    finally:
        conn.close()


#: A filed job of a class on a matter: delivered, or failed and filed by SMD's hand.
_FILED_SQL = (
    "SELECT id, request_ref, requester FROM drafting_jobs WHERE matter_id=? AND document_class=? AND id != ? "
    "AND state IN ('delivered','failed') ORDER BY created_at DESC LIMIT 50"
)


def filed_job(db: DraftingLedger, row: dict[str, Any], thread: set[str], by: str = "") -> str:
    """The id of a filed job (delivered, or failed: SMD's hand) of ``row``'s class
    on its matter whose request email is in ``thread``, asked for by ``by`` when
    given; "" when none."""
    conn = db._connect()
    try:
        found = conn.execute(_FILED_SQL, (row["matter_id"], row["document_class"], row["id"])).fetchall()
    finally:
        conn.close()
    for r in found:
        if _bare(r["request_ref"]) in thread and (not by or str(r["requester"]).strip().lower() == by):
            return str(r["id"])
    return ""


def _whose(db: DraftingLedger, row: dict[str, Any], ref: str, by: str, thread: set[str], about: bool) -> str:
    """The refusal of an email that may not reach this job, or "". ``about``: the
    email is about documents already filed, and reaches a draft of their kind on
    the matter when its thread holds one the sender asked for."""
    if not about and by != str(row["requester"]).strip().lower():
        return "not_requester"
    if _bare(ref) == _bare(row["request_ref"]):
        return "same_email"
    if not ref or _bare(ref) not in thread:
        return "other_thread"
    held = filed_job(db, row, thread, by) if about else _bare(row["request_ref"]) in thread
    return "" if held else "other_thread"


def add_to_request(
    db: DraftingLedger,
    audit: Callable[[str, dict[str, Any], str | None], None],
    request: dict[str, Any],
    *,
    wait: float = WAIT_SECONDS,
    now: Callable[[], str],
    owe: Callable[[dict[str, Any], str, str], bool] | None = None,
) -> dict[str, Any]:
    """The result decides what the requester is told. A job that had ended
    ``failed`` answers ``smd_told``: whether ``owe(row, request_ref, state)``
    recorded the addition as work SMD owes (``smd_owed.owe_addition``)."""
    job_id = str(request.get("job_id") or "").strip()
    ref = str(request.get("request_ref") or "").strip()
    by = str(request.get("requested_by") or "").strip().lower()
    words = str(request.get("words") or "").strip()
    thread = {_bare(r) for r in request.get("thread_refs") or [] if isinstance(r, str)}

    def out(result: str, **extra: Any) -> dict[str, Any]:
        return {"ok": True, "result": result, **extra}

    row = db.read(job_id) if job_id else None
    if row is None:
        return out("no_such_job")
    named = {"matter_number": row["matter_number"], "document_class": row["document_class"], "job_id": job_id}
    about = request.get("about_filed") is True
    refused = _whose(db, row, ref, by, thread, about)
    if refused:
        return out(refused, **named)
    if not about and not row.get("revise_of") and filed_job(db, row, thread):
        # The thread also holds filed documents of this class, and this draft
        # is not their revision: the overlay submits the email too, so the
        # filed ones are revised (or SMD's).
        named = {**named, "filed_in_thread": True, "matter_id": row["matter_id"]}
    if not words:
        return out("no_words", **named)
    if len(words) > MAX_WORDS:
        return out("too_long", **named)

    def ended(state: Any, **extra: Any) -> dict[str, Any]:
        if state == "failed" and not about:
            # The addition cannot reach a failed draft. It is work SMD owes,
            # recorded in this step, and the answer says whether it was.
            extra["smd_told"] = bool(owe is not None and owe(row, ref, "failed"))
        return out("job_ended", state=state, **extra, **named)

    if row["state"] not in IN_FLIGHT:
        return ended(row["state"])
    key, at = _key(ref), now()
    marker = {"job_id": job_id, "key": key, "by": by, "at": at, "words": words}
    answer = ask_runner(db.queue_dir, job_id, key, marker, wait) or {"result": "not_confirmed"}
    result = str(answer.get("result") or "not_confirmed")
    digest = str(answer.get("envelope_digest") or "")
    note_addition(
        db, job_id, key=key, digest=digest, at=at, by=by, words=words if result == "compose_started" else None
    )
    audit(
        AUDIT_TYPE,
        {"job_id": job_id, "result": result, "request_ref": ref, "envelope_digest": digest},
        row["matter_id"],
    )
    if result == "job_ended":
        return ended((db.read(job_id) or {}).get("state"), repeat=bool(answer.get("repeat")))
    return out(result, repeat=bool(answer.get("repeat")), **named)


def carried_words(row: dict[str, Any] | None) -> list[dict[str, Any]]:
    """What a job's requester added that its draft did not cover."""
    try:
        delivery = json.loads((row or {}).get("delivery_json") or "{}")
    except ValueError:
        return []
    found = delivery.get("additions_not_covered") if isinstance(delivery, dict) else None
    return [a for a in found if isinstance(a, dict) and a.get("words")] if isinstance(found, list) else []


def with_carried_words(envelope: dict[str, Any], prior: dict[str, Any] | None) -> dict[str, Any]:
    """A revision's request, with the words the draft it revises did not cover.
    The filed draft was told they were left out and he has now asked for changes;
    the revision's checklist quotes only what its request holds. Left off when the
    request could not hold them, which the delivery reply already told him about."""
    text = str(envelope["request_text"])
    for item in carried_words(prior):
        text += f"\n\nAdded by {item.get('by')} at {item.get('at')}, in the same email thread:\n{item['words']}"
    return {**envelope, "request_text": text} if len(text) <= MAX_REQUEST_TEXT else envelope


__all__ = [
    "AUDIT_TYPE",
    "MAX_WORDS",
    "WAIT_SECONDS",
    "add_to_request",
    "carried_words",
    "filed_job",
    "with_carried_words",
]
