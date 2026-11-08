"""A firm's own save of its accepted library file, handed to the root writer
(design 2026-11-06, piece 4; the job side is ``runners/jobs/jobs/library_edit``).

TWO VERBS, ROOT ONLY (the runner daemon, after a drafting or demand job's child):

* ``library_edit_submit``: the acceptance the job made in code and the cleaned
  bytes it kept. Checked here, then spooled as a ``library_accept`` run in the
  shape of ``establish_intake/library_accept.SUBMISSION_FIELDS``, through the
  same spool 3b's choice uses (``library_setup_choose.spool``). The intake's
  root writer runs its own no-client-text check, refuses an acceptance whose
  ``replaces`` is not the slot's entry (the race between two jobs that found
  the same save), writes the copy and the ledger to the firm's vault and waits
  for the seat to install them.
* ``library_edit_status``: the intake's one-shot answer for that run.

WHAT IS CHECKED HERE, each a refusal with nothing spooled: the lane is the
drafting or demand lane; the acceptance carries exactly the writer's keys;
``accepted_by`` is ``firm_made`` (no person said yes; the code did), or
``layout_rule:<ids>`` for a version code wrote with the firm's layout rules in
it (design piece 5, the same hand-over); the
contract is one the writer takes (``styles`` or ``marked``); ``replaces`` names
the entry the save is a version of (same file); the bytes hash to its sha256.

No firm-facing words here; nothing is sent.
"""

from __future__ import annotations

import base64
import hashlib
import re
import sqlite3
from typing import Any

from .audit_ledger import _iso_utc, _ulid
from .broker_context import BrokerContext
from .library_setup_choose import SCOPE, spool

VERBS = ("library_edit_submit", "library_edit_status")
LANES = frozenset({"drafting", "demand"})
FIRM_MADE = "firm_made"
#: A version code wrote with a firm's layout rules in it (``runners/jobs/jobs/
#: drafting/layout_into_library``), behind ``logic_in_code.layout_into_library``.
LAYOUT_RULE = re.compile(r"^layout_rule:[0-9A-Za-z]{1,40}(?:,[0-9A-Za-z]{1,40}){0,19}$")
CONTRACTS = frozenset({"styles", "marked"})
_KEYS = frozenset(
    {
        "kind",
        "attorney",
        "file_id",
        "version",
        "contract",
        "accepted_by",
        "accepted_at",
        "sha256",
        "fixed_strings",
        "replaces",
        "chart_source",
    }
)
_TEXT = ("kind", "file_id", "version", "contract", "accepted_by", "accepted_at", "sha256")


def _problems(acc: Any, data: bytes) -> list[str]:
    if not isinstance(acc, dict) or set(acc) != _KEYS:
        return [f"acceptance must carry exactly {sorted(_KEYS)}"]
    out = [f"acceptance.{k} must be a non-empty string" for k in _TEXT if not isinstance(acc[k], str) or not acc[k]]
    if out:
        return out
    if acc["accepted_by"] != FIRM_MADE and not LAYOUT_RULE.match(acc["accepted_by"]):
        out.append(f"a firm's own save is accepted by {FIRM_MADE!r}, and a layout rule's version by layout_rule:<ids>")
    if acc["contract"] not in CONTRACTS:
        out.append(f"contract {acc['contract']!r} is not one the writer takes for a firm's save")
    if acc["attorney"] is not None and (not isinstance(acc["attorney"], str) or not acc["attorney"]):
        out.append("acceptance.attorney must be an attorney key or null")
    fixed = acc["fixed_strings"]
    if not isinstance(fixed, list) or not all(isinstance(s, str) for s in fixed):
        out.append("acceptance.fixed_strings must be a list of strings")
    rep = acc["replaces"]
    if (
        not isinstance(rep, dict)
        or set(rep) != {"file_id", "version", "sha256"}
        or rep.get("file_id") != acc["file_id"]
    ):
        out.append("a firm's save replaces the accepted entry of the same file")
    elif rep.get("version") == acc["version"]:
        out.append("a firm's save is a newer version than the one it replaces")
    if acc["chart_source"] is not None and not isinstance(acc["chart_source"], dict):
        out.append("acceptance.chart_source must be null or where a memo's tables' look came from")
    if hashlib.sha256(data).hexdigest() != acc["sha256"]:
        out.append("the bytes do not hash to the acceptance's sha256")
    return out


def submission(acc: dict[str, Any], run_id: str) -> dict[str, Any]:
    """The run's ``submission.json``, in the root writer's shape."""
    return {"scope": SCOPE, "phase": "install", "run_id": run_id, "created_at": _iso_utc(), "acceptance": dict(acc)}


def submit(store: Any, request: dict[str, Any]) -> dict[str, Any]:
    if store is None:
        raise ValueError("the establishment spool is not configured on this broker")
    if request.get("lane") not in LANES or not str(request.get("job_id") or ""):
        raise ValueError("library_edit_submit names a drafting or demand job")
    try:
        data = base64.b64decode(str(request.get("data_b64") or ""), validate=True)
    except ValueError as exc:
        raise ValueError("data_b64 is not base64") from exc
    acc = request.get("acceptance")
    problems = _problems(acc, data)
    if problems:
        raise ValueError("; ".join(problems)[:500])
    run_id = _ulid().lower()
    spool(store.runs_dir, run_id, submission(acc, run_id), data)  # type: ignore[arg-type]
    return {"ok": True, "run_id": run_id}


def status(store: Any, request: dict[str, Any]) -> dict[str, Any]:
    run_id = str(request.get("run_id") or "")
    if store is None or not run_id:
        raise ValueError("library_edit_status names no spooled run")
    try:
        return store.status({"run_id": run_id})
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise ValueError(f"the run's result could not be read: {exc}") from exc


def dispatch(
    broker: BrokerContext, action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    """Root only (the verb table's gate)."""
    store = getattr(broker, "establishment", None)
    if action == "library_edit_submit":
        return submit(store, request)
    if action == "library_edit_status":
        return status(store, request)
    raise ValueError(f"unsupported library edit action: {action}")


__all__ = ["VERBS", "dispatch", "status", "submission", "submit"]
