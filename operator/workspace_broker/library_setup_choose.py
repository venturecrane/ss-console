"""The administrator's choice from a "set up our ..." list, and the hand-off of
the made file to the root writer.

``library_setup_choose`` (gateway or root). The tool sends only the verified
email: its sender, its message id and the reader's own words above the quoted
history (``inbound.InboundOrigin.reply_text``). CODE decides, ADR 0089's
exact-match posture, and anything short of every check makes nothing:

1. the seat's switch: off, nothing is queued and the turn answers as today; a
   switch that cannot be read queues a dark job that holds for SMD;
2. the list: the delivered candidates job whose request email is in the same
   mailbox conversation as this reply (the mailbox says so, never the model);
   none, or a thread the mailbox cannot read, refuses;
3. the person: the sender is a Named Administrator and the person who asked;
4. the list is the newest of its kind for that attorney (or the firm), and
   nothing was chosen from it yet;
5. the words: exactly one listed number or listed file id, and no number that
   is not on the list. A line shaped like the list's own item lines is skipped.

Then one ``accept`` job is queued (``mode: accept``, the chosen document's ids
and the bytes' sha256 the hunt read) for the drafting lane.

``library_setup_accept_submit`` and ``library_setup_accept_status`` (root only:
the runner daemon). The made file's bytes arrive base64; the job row must be an
``accept`` job that is running, and the bytes must hash to what the child
filed. The run is spooled in the shape of
``establish_intake/library_accept.SUBMISSION_FIELDS`` beside the establishment
runs (broker-written, so the intake's uid check passes), and its result is read
once, as the establishment status read reads every run's.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from . import library_setup_wording as wording
from .audit_ledger import _iso_utc, _ulid
from .library_setup_ledger import JOB_KIND, SetupLedger
from .logic_in_code import LIBRARY_SETUP, load_switch
from .medchron_ledger import admins_from_customer_yaml
from .recipient_policy import normalize_address


def _obj(value: Any) -> dict[str, Any]:
    """``value`` when it is an object, else an empty one."""
    return value if isinstance(value, dict) else {}


CONTRACT = "marked"
SCOPE = "library_accept"
_ITEM_LINE = re.compile(r"(?i)^\s*\d+\.\s+(matter|saved)\b")
#: A bare number: not part of a date, a decimal, an id or a longer number.
_NUMBER = re.compile(r"(?<![\w.,/:-])(\d{1,3})(?![\w/:-]|[.,]\d)")


def _refused(key: str, **values: Any) -> dict[str, Any]:
    return {"ok": True, "accepted": False, "reason": wording.say(key, **values), "reason_key": key}


def choice_of(text: str, candidates: list[dict[str, Any]]) -> tuple[int | None, str]:
    """(the one listed candidate's index, "") or (None, the refusal's key)."""
    ids = {str(c.get("file_id")): i for i, c in enumerate(candidates) if isinstance(c, dict) and c.get("file_id")}
    picked: set[int] = set()
    for line in (text or "").splitlines():
        if _ITEM_LINE.match(line):
            continue
        for fid, i in ids.items():
            if fid and fid in line:
                picked.add(i)
                line = line.replace(fid, " ")
        for m in _NUMBER.finditer(line):
            n = int(m.group(1))
            if not 1 <= n <= len(candidates):
                return None, "refused_not_listed"
            picked.add(n - 1)
    if len(picked) > 1:
        return None, "refused_two"
    if not picked:
        return None, "refused_no_choice"
    return picked.pop(), ""


def _rows(db: SetupLedger, sql: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
    conn = db._connect()
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def _report(row: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(row.get("report_json") or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _list_in_thread(verbs: Any, db: SetupLedger, choice_ref: str) -> dict[str, Any] | str:
    """The delivered list whose request is in this reply's conversation, or a refusal key."""
    thread_of = getattr(verbs, "thread_of", None)
    if thread_of is None:
        return "refused_no_list"
    try:
        conversation = thread_of(choice_ref)
        lists = _rows(db, "SELECT * FROM library_setup_jobs WHERE mode='list' AND state='delivered'", ())
        found = [r for r in lists if conversation and thread_of(str(r["request_ref"])) == conversation]
    except Exception:  # noqa: BLE001 - a thread the mailbox cannot read is "could not tell", never "no list"
        return "refused_unreadable"
    found = [r for r in found if _report(r).get("setup_outcome") == "candidates"]
    return found[-1] if len(found) == 1 else "refused_no_list"


def _newer_or_chosen(db: SetupLedger, row: dict[str, Any]) -> str:
    newer = _rows(
        db,
        "SELECT id FROM library_setup_jobs WHERE mode='list' AND document_class=? "
        "AND COALESCE(lower(attorney_named),'')=? AND created_at > ? AND id != ?",
        (row["document_class"], str(row.get("attorney_named") or "").lower(), row["created_at"], row["id"]),
    )
    if newer:
        return "refused_superseded"
    chosen = _rows(db, "SELECT id FROM library_setup_jobs WHERE parent_id=? AND state != 'failed'", (row["id"],))
    return "refused_chosen" if chosen else ""


def _insert_accept(
    db: SetupLedger, parent: dict[str, Any], choice: dict[str, Any], env: dict[str, Any], live: bool
) -> str:
    report = _report(parent)
    job_id, now = _ulid(), _iso_utc()
    digest = hashlib.sha256(json.dumps({**env, "choice": choice}, sort_keys=True).encode()).hexdigest()
    conn = db._connect()
    try:
        conn.execute(
            "INSERT INTO library_setup_jobs (id, created_at, updated_at, state, document_class, matter_number, "
            "requester, request_ref, envelope_digest, live, attorney_named, mode, parent_id, choice_json, "
            "attorney_name, attorney_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, now, now, "submitted", parent["document_class"], parent["matter_number"], env["requested_by"],
             env["request_ref"], digest, int(live), parent.get("attorney_named"), "accept", parent["id"],
             json.dumps(choice, sort_keys=True), report.get("attorney_name"), report.get("attorney_key")),
        )  # fmt: skip
        conn.commit()
    finally:
        conn.close()
    record = {
        "kind": JOB_KIND,
        "mode": "accept",
        "job_id": job_id,
        "parent_id": parent["id"],
        "setup_kind": parent["document_class"],
        "document_class": parent["document_class"],
        "attorney_named": parent.get("attorney_named"),
        "attorney_name": report.get("attorney_name"),
        "attorney_key": report.get("attorney_key"),
        "choice": choice,
        "requester": env["requested_by"],
        "message_ref": env["request_ref"],
        "library_matter_number": parent["matter_number"],
        "live": live,
    }
    db.queue_dir.mkdir(parents=True, exist_ok=True)
    tmp = db.queue_dir / f".{job_id}.json.tmp"
    tmp.write_text(json.dumps(record, indent=1, sort_keys=True), encoding="utf-8")
    tmp.chmod(0o640)
    tmp.replace(db.queue_dir / f"{job_id}.json")
    return job_id


def _checked(verbs: Any, db: SetupLedger, env: dict[str, Any]) -> tuple[dict[str, Any], int] | dict[str, Any]:
    """(the list, the chosen index) or the refusal answer."""
    found = _list_in_thread(verbs, db, env["request_ref"])
    if isinstance(found, str):
        return _refused(found)
    if env.get("kind") and env["kind"] != found["document_class"]:
        return _refused("refused_no_list")
    many = wording.names(found["document_class"])
    admins = admins_from_customer_yaml(str(verbs.customer_yaml)) or ()
    if env["requested_by"] != normalize_address(str(found["requester"])) or env["requested_by"] not in admins:
        return _refused("refused_not_requester", **many)
    blocked = _newer_or_chosen(db, found)
    if blocked:
        return _refused(blocked, **many)
    candidates = _report(found).get("candidates") or []
    index, why = choice_of(env["reply_text"], candidates)
    if index is None:
        return _refused(why)
    picked = candidates[index]
    if not all(picked.get(k) for k in ("file_id", "matter_id", "sha256", "saved_on")):
        return _refused("refused_stale", **many)
    return found, index


def choose(verbs: Any, db: SetupLedger, request: dict[str, Any], audit: Any) -> dict[str, Any]:
    raw = _obj(request.get("envelope"))
    env = {
        "requested_by": normalize_address(str(raw.get("requested_by") or "")),
        "request_ref": str(raw.get("request_ref") or "").strip(),
        "reply_text": str(raw.get("reply_text") or ""),
        "kind": str(raw.get("kind") or ""),
    }
    if not (env["requested_by"] and env["request_ref"]):
        return _refused("refused_no_list")
    sw = load_switch(LIBRARY_SETUP, str(verbs.customer_yaml))
    if not sw.enforcing and sw.error is None:
        return {"ok": True, "accepted": False, "live": False}
    checked = _checked(verbs, db, env)
    if isinstance(checked, dict):
        return checked
    parent, index = checked
    c = _report(parent)["candidates"][index]
    choice = {"n": index + 1, **{k: c.get(k) for k in ("file_id", "matter_id", "matter_number", "saved_on", "sha256")}}
    donors = _report(parent).get("chart_donors")
    if isinstance(donors, list) and donors:  # a memo's chart look, by rank (memo_charts): ids only
        choice["chart_donors"] = [
            {k: str(d.get(k) or "") for k in ("file_id", "matter_id", "sha256")} for d in donors if isinstance(d, dict)
        ]
    live = sw.error is None
    job_id = _insert_accept(db, parent, choice, env, live)
    if not live:
        return {"ok": True, "accepted": True, "job_id": job_id, "live": False}
    audit("LIBRARY_SETUP_CHOSEN", {"job_id": job_id, "parent_id": parent["id"], "n": index + 1})
    attorney = _report(parent).get("attorney_name") or None
    return {
        "ok": True,
        "accepted": True,
        "job_id": job_id,
        "live": True,
        "say": wording.chosen(parent["document_class"], index + 1, attorney),
    }


# ---- the made file, to the root writer ---------------------------------------------------


def spool(runs_dir: Path, run_id: str, submission: dict[str, Any], data: bytes) -> None:
    """The run, assembled in a dot-prefixed directory the intake ignores, then renamed."""
    tmp = Path(runs_dir) / f".tmp-{run_id}"
    try:
        tmp.mkdir(parents=True)
        (tmp / "file.docx").write_bytes(data)
        (tmp / "submission.json").write_text(json.dumps(submission, sort_keys=True), "utf-8")
        tmp.rename(Path(runs_dir) / run_id)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def submission(
    row: dict[str, Any], run_id: str, data: bytes, filed: dict[str, Any], request: dict[str, Any]
) -> dict[str, Any]:
    fixed = request.get("fixed_strings")
    replaces = request.get("replaces")
    chart = request.get("chart_source")
    return {
        "scope": SCOPE,
        "phase": "install",
        "run_id": run_id,
        "created_at": _iso_utc(),
        "acceptance": {
            "kind": row["document_class"],
            "attorney": row.get("attorney_key") or None,
            "file_id": str(filed["file_id"]),
            "version": str(filed["version"]),
            "contract": CONTRACT,
            "accepted_by": row["requester"],
            "accepted_at": row["created_at"],
            "sha256": hashlib.sha256(data).hexdigest(),
            "fixed_strings": [str(s) for s in fixed] if isinstance(fixed, list) else [],
            "replaces": replaces if isinstance(replaces, dict) else None,
            # A memo whose tables take another file's look (memo_charts): which, by id.
            "chart_source": chart if isinstance(chart, dict) else None,
        },
    }


def accept_submit(store: Any, db: SetupLedger, request: dict[str, Any]) -> dict[str, Any]:
    """Root only: spool the made file's acceptance for the root writer."""
    row = db.read(str(request.get("job_id") or ""))
    if row is None or row.get("mode") != "accept" or row.get("state") != "running":
        raise ValueError("library_setup_accept_submit names no running accept job")
    if store is None:
        raise ValueError("the establishment spool is not configured on this broker")
    filed = _obj(request.get("filed"))
    try:
        data = base64.b64decode(str(request.get("data_b64") or ""), validate=True)
    except ValueError as exc:
        raise ValueError("data_b64 is not base64") from exc
    if not (filed.get("file_id") and filed.get("version")) or hashlib.sha256(data).hexdigest() != filed.get("sha256"):
        raise ValueError("the made file's bytes are not the ones the job filed")
    run_id = _ulid().lower()
    spool(store.runs_dir, run_id, submission(row, run_id, data, filed, request), data)
    _set_run(db, row["id"], run_id)
    return {"ok": True, "run_id": run_id}


def _set_run(db: SetupLedger, job_id: str, run_id: str) -> None:
    conn = db._connect()
    try:
        conn.execute("UPDATE library_setup_jobs SET run_id=? WHERE id=?", (run_id, job_id))
        conn.commit()
    finally:
        conn.close()


def accept_status(store: Any, db: SetupLedger, request: dict[str, Any]) -> dict[str, Any]:
    """Root only: the intake's answer for the job's run (one-shot, as every run's)."""
    row = db.read(str(request.get("job_id") or ""))
    if row is None or not row.get("run_id") or store is None:
        raise ValueError("library_setup_accept_status names no spooled accept job")
    try:
        return store.status({"run_id": row["run_id"]})
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise ValueError(f"the run's result could not be read: {exc}") from exc


__all__ = ["accept_status", "accept_submit", "choice_of", "choose", "spool", "submission"]
