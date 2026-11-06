"""Words a requester adds while his job runs: they reach the job, or he hears
that they did not.

WHY (2026-11-05, A&P). An attorney asked for a discovery set and widened it in
the same thread two minutes later. The reply said the direction was noted, and
nothing carried it: ``request_text`` is fixed in the envelope at admission and
each process loads the job once. The first fix was by hand (stop, edit, resume).

THE TWO HALVES, split as ``resume.py`` splits them and for the same reason. The
broker cannot read a job dir (``jobs/`` is root:jobs 0710), so it cannot tell how
far a job has got. It drops ``queue/.add-<job>-<key>.json`` and waits for
``queue/.added-<job>-<key>.json``; the root daemon answers. The lane thread sits
in ``child.run_child`` for as long as a job runs, so ``take`` runs from that
wait, from the idle sleep and from every tick: all on the lane's own thread,
which is also the only thing that claims a queue file.

THE DOOR. A paid stage already done is never redone: a job reads its request only
from compose on, so the words are good until then, and no stop or resume is
needed. The child closes the door (:func:`close_door`) before the first stage
that reads the request, and only then reloads its job file. Both sides hold a
``flock`` across their one decision, so the daemon's "applied" can never be
told to a job that has already read the request: either the words were written
before the door closed, and the reload has them, or the door was closed and the
answer says so.

WHAT THE ENVELOPE HOLDS. ``request_text`` with the words appended under an
attributed line, and ``request_additions``: one ``{key, result, by, at}`` per
email, which is what makes a repeat the same answer. ``job.json`` is re-stamped
from the envelope on every attempt, so a later attempt reads the same request.
Words the door refused are kept by the broker on the job's row, for the delivery
reply (``drafting_additions.py``), not here.

A CORE MODULE: it names no lane. Only the drafting lane calls it today.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Iterator

from . import smd_stop
from .daemon import TERMINAL

logger = logging.getLogger("jobs.daemon")

MARKER_PREFIX = ".add-"
ANSWER_PREFIX = ".added-"
JOB_FILE = "job.json"
ENVELOPE = "envelope.json"
#: Exists once the child has read, or is about to read, the request.
DOOR = ".request-door-closed"
LOCK = ".request-door.lock"
#: The broker's own ceiling on a request (``drafting_ledger.MAX_REQUEST_TEXT``).
MAX_REQUEST_TEXT = 20_000

APPLIED = "applied"
COMPOSE_STARTED = "compose_started"
JOB_ENDED = "job_ended"
TOO_LONG = "too_long"
NOT_CONFIRMED = "not_confirmed"


def key_of(ref: str) -> str:
    """One email's key: its message id, hashed for a file name."""
    return hashlib.sha256(ref.strip().encode("utf-8")).hexdigest()[:32]


def marker_name(job_id: str, key: str) -> str:
    return f"{MARKER_PREFIX}{job_id}-{key}.json"


def answer_name(job_id: str, key: str) -> str:
    return f"{ANSWER_PREFIX}{job_id}-{key}.json"


def attributed(by: str, at: str, words: str) -> str:
    """The words as a request carries them: who, when, then the words as written."""
    return f"\n\nAdded by {by} at {at}, in the same email thread:\n{words.strip()}"


@contextlib.contextmanager
def _door_lock(job_dir: Path) -> Iterator[None]:
    """Held across one decision. Read-only is enough for ``flock``, which lets
    root and the job's uid share a lock file either of them created."""
    fd = os.open(job_dir / LOCK, os.O_RDONLY | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def close_door(job_dir: Path | str) -> None:
    """The child, before the first stage that reads the request. The caller
    reloads its job file after this: no write can follow it."""
    jd = Path(job_dir)
    with _door_lock(jd):
        (jd / DOOR).touch()


def _write_as_it_was(path: Path, doc: dict[str, Any]) -> None:
    """Atomically, keeping the file's owner and mode: the child reads ``job.json``
    in its own dir, and the broker's queue file keeps the broker's owner."""
    st = path.stat()
    tmp = path.with_name(f".{path.name}.add.tmp")
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    os.chmod(tmp, st.st_mode & 0o777)
    with contextlib.suppress(PermissionError):  # a test's uid; root on a seat
        os.chown(tmp, st.st_uid, st.st_gid)
    tmp.replace(path)


def _digest(doc: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode("utf-8")).hexdigest()


def _ended(d: Any, job_id: str, job_dir: Path) -> bool:
    st = d._daemon_state(job_id)
    state = st.get("state")
    if state in TERMINAL or (state == "held" and not st.get("held_paused")):
        return True
    return job_dir.is_dir() and smd_stop.read(job_dir) is not None


def _decide(d: Any, path: Path, req: dict[str, Any], *, door_closed: bool) -> dict[str, Any]:
    """One email against one envelope, written only when the answer is new."""
    env = json.loads(path.read_text(encoding="utf-8"))
    seen = [a for a in env.get("request_additions") or [] if isinstance(a, dict)]
    for prior in seen:
        if prior.get("key") == req["key"]:
            return {"result": prior.get("result"), "repeat": True, "envelope_digest": _digest(env)}
    item = {"key": req["key"], "by": req["by"], "at": req["at"]}
    result = COMPOSE_STARTED if door_closed else APPLIED
    if result == APPLIED:
        text = str(env["request_text"]) + attributed(str(req["by"]), str(req["at"]), str(req["words"]))
        if len(text) > MAX_REQUEST_TEXT:
            return {"result": TOO_LONG}
        env["request_text"] = text
    env["request_additions"] = [*seen, {**item, "result": result}]
    _write_as_it_was(path, env)
    job_file = path.with_name(JOB_FILE)
    if path.name == ENVELOPE and job_file.is_file():
        doc = json.loads(job_file.read_text(encoding="utf-8"))
        _write_as_it_was(
            job_file, {**doc, "request_text": env["request_text"], "request_additions": env["request_additions"]}
        )
    return {"result": result, "envelope_digest": _digest(env)}


def answer(d: Any, req: dict[str, Any]) -> dict[str, Any]:
    """The answer to one marker. Nothing here stops, resumes or restarts a job."""
    job_id = str(req["job_id"])
    jd = d.job_dir(job_id)
    env, queued = jd / ENVELOPE, d.queue / f"{job_id}.json"
    if _ended(d, job_id, jd):
        return {"result": JOB_ENDED}
    if env.is_file():
        with _door_lock(jd):
            return _decide(d, env, req, door_closed=(jd / DOOR).exists())
    if queued.is_file():  # not claimed yet; only this thread claims
        return _decide(d, queued, req, door_closed=False)
    return {"result": JOB_ENDED}


def _write_answer(d: Any, job_id: str, key: str, out: dict[str, Any]) -> None:
    path = d.queue / answer_name(job_id, key)
    tmp = path.with_name(f".tmp-{path.name}")
    tmp.write_text(json.dumps(out), encoding="utf-8")
    os.chmod(tmp, 0o644)  # no words in it; the queue dir is what is closed
    tmp.replace(path)


def take(d: Any) -> list[str]:
    """Answer every marker in the lane's queue. Never raises: this runs inside
    the wait for a running child, which must outlive any fault here."""
    done: list[str] = []
    try:
        markers = sorted(d.queue.glob(f"{MARKER_PREFIX}*.json")) if d.queue.is_dir() else []
    except OSError:
        logger.exception("the request additions could not be listed")
        return done
    for marker in markers:
        try:
            req = json.loads(marker.read_text(encoding="utf-8"))
            job_id, key = str(req["job_id"]), str(req["key"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.error("unreadable request addition %s: %s", marker.name, type(exc).__name__)
            marker.unlink(missing_ok=True)
            continue
        try:
            out = answer(d, req)
        except Exception:  # whatever failed, the requester is told it was not confirmed, never that it landed
            logger.exception("the request addition for job %s could not be applied", job_id)
            out = {"result": NOT_CONFIRMED}
        try:
            _write_answer(d, job_id, key, out)
        except OSError:
            logger.exception("the answer for job %s could not be written", job_id)
        marker.unlink(missing_ok=True)
        done.append(job_id)
    return done


__all__ = [
    "ANSWER_PREFIX",
    "APPLIED",
    "COMPOSE_STARTED",
    "DOOR",
    "JOB_ENDED",
    "MARKER_PREFIX",
    "MAX_REQUEST_TEXT",
    "NOT_CONFIRMED",
    "TOO_LONG",
    "answer",
    "answer_name",
    "attributed",
    "close_door",
    "key_of",
    "marker_name",
    "take",
]
