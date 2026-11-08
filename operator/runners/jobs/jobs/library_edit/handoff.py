"""A firm's edit, handed from the job to the root writer, in the root daemon,
after the child (design 2026-11-06, piece 4).

The child made its draft in the firm's cleaned save and left the acceptance in
its data folder (``library_edit.leave``), or left the code it refused the save
for. This, after the child, for the drafting and demand lanes:

* a REFUSED save pages SMD once (``library_edit_refused``, the code, the
  version; never a word of the file). The draft was made in the accepted copy.
  The firm is told nothing in this piece;
* a USED save is handed to the broker (``library_edit_submit``, root only),
  which spools a ``library_accept`` run in the shape of
  ``establish_intake/library_accept.SUBMISSION_FIELDS``, exactly as 3b's choice
  does; the root writer runs its own no-client-text check, writes the copy and
  the new ledger to the firm's vault, and waits for the seat to install it.
  This waits for the intake's answer (``library_edit_status``):

  - ``installed`` / ``accepted_pending_install``: the slot's entry is now the
    firm's version, the case system's version id with the sha256 of the bytes
    kept, ``replaces`` the entry before it; nothing to say to anyone;
  - refused because the slot no longer holds the entry this one replaces:
    another job found the same save first and its acceptance is the slot's
    now (the intake's ``replaces`` check is the race's only arbiter; nothing
    is written twice). Logged, not paged;
  - anything else, or no answer: SMD is paged. The draft already filed was
    made in the cleaned save, which passed the job's own check; the slot keeps
    its entry, so the next draft considers the save again.

Nothing here writes to the case system or sends a message to the firm.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path
from typing import Any

from . import EDIT_DOCX, EDIT_JSON

logger = logging.getLogger("jobs.daemon")

CODE = "library_edit_refused"
WAIT_SECONDS = 240.0
POLL_SECONDS = 5.0
SLEEP = time.sleep
#: The broker takes one request line of at most 1 MiB, and base64 grows bytes by a third.
MAX_BYTES = 700_000
#: The intake's words when the slot moved on under this acceptance (``library_accept._slot_problems``).
RACE_LOST = "not the one this acceptance says it replaces"
INSTALLED = ("installed", "accepted_pending_install")
#: What the handled hand-off is renamed to, so it is handed once.
HANDED = f"{EDIT_JSON}.handed"


def read(data_dir: Path, name: str = EDIT_JSON) -> dict[str, Any] | None:
    """What the child left, or None when it considered no save."""
    try:
        doc = json.loads((Path(data_dir) / name).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and isinstance(doc.get("note"), dict) else None


def delivery_note(data_dir: Path) -> dict[str, Any] | None:
    """The one fact the broker's reply may read (``library_setup_wording.edit_not_used``):
    whether the firm's save was used, and the refusal's code. None when none was considered."""
    doc = read(data_dir)
    if doc is None:
        return None
    note = doc["note"]
    return {"used": note.get("status") == "used", "code": str(note.get("code") or "")}


def _page(d: Any, job_id: str, detail: str, code: str = CODE) -> None:
    from ..proof import watch

    watch.alert(d, job_id, code, detail[:300])


def _wait(client: Any, run_id: str, clock: Any) -> dict[str, Any] | None:
    from ..broker_client import BrokerError

    deadline = clock() + WAIT_SECONDS
    while True:
        try:
            resp = client._request({"action": "library_edit_status", "run_id": run_id})
        except BrokerError as exc:
            logger.warning("library edit %s: the acceptance could not be read yet: %s", run_id, exc)
            resp = {}
        if resp.get("status") == "complete" and isinstance(resp.get("result"), dict):
            return resp["result"]
        if clock() >= deadline:
            return None
        SLEEP(POLL_SECONDS)


def _submit(d: Any, job_id: str, acc: dict[str, Any], data: bytes) -> str:
    from ..broker_client import BrokerError
    from ..proof import watch

    client = watch._client(d)
    request = {
        "action": "library_edit_submit",
        "lane": getattr(d, "LANE", ""),
        "job_id": job_id,
        "acceptance": acc,
        "data_b64": base64.b64encode(data).decode("ascii"),
    }
    try:
        run_id = str(client._request(request).get("run_id") or "")
    except BrokerError as exc:
        logger.error("library edit for %s was not spooled: %s", job_id, exc)
        run_id = ""
    if not run_id:
        return "not_spooled"
    result = _wait(client, run_id, d.clock)
    if result is None:
        return "unconfirmed"
    status = str(result.get("status") or "")
    if status in INSTALLED:
        return status
    reasons = " ".join(str(r) for r in result.get("reasons") or [])
    return "already_replaced" if RACE_LOST in reasons else f"rejected_by_root ({status})"


def _done(data_dir: Path, name: str = EDIT_JSON) -> None:
    """Handled once: a resumed job's next child leaves its own, or none."""
    try:
        (Path(data_dir) / name).replace(Path(data_dir) / f"{name}.handed")
    except OSError as exc:
        logger.error("library edit: could not mark %s handled: %s", data_dir, exc)


def hand_over(
    d: Any,
    job_id: str,
    *,
    names: tuple[str, str] = (EDIT_JSON, EDIT_DOCX),
    code: str = CODE,
    what: str = "the firm's saved version",
) -> str:
    """After the child: page a refusal, hand a used save to the root writer.
    Returns what happened, for the log; never raises: a lane must not lose its
    job to this. A lane proof run's twin hands nothing over and pages nothing.
    ``names``, ``code``, ``what``: the same hand-over for a version code wrote
    with the firm's layout rules in it (``drafting/layout_into_library``)."""
    from ..proof import watch

    data_dir = d.job_dir(job_id) / "data"
    doc = read(data_dir, names[0])
    if doc is None:
        return ""
    _done(data_dir, names[0])
    if watch.in_proof_run(d):
        return "proof_run"
    try:
        return _hand_over(d, job_id, data_dir, doc, (names[1], code, what))
    except Exception:  # logged with its trace; the job's own record stands
        logger.exception("library edit for %s could not be handed over", job_id)
        return "error"


def _hand_over(d: Any, job_id: str, data_dir: Path, doc: dict[str, Any], how: tuple[str, str, str]) -> str:
    docx_name, code, what = how
    note, acc = doc["note"], doc.get("acceptance")
    version = str(note.get("version") or "")
    if note.get("status") != "used" or not isinstance(acc, dict):
        _page(
            d,
            job_id,
            f"{what} {version} was not used: {note.get('code')}; the draft was made in the accepted copy",
            code,
        )
        return "refused"
    try:
        data = (data_dir / docx_name).read_bytes()
    except OSError:
        data = b""
    if not data or len(data) > MAX_BYTES:
        outcome = "made_file_missing" if not data else "made_file_too_large"
    else:
        outcome = _submit(d, job_id, acc, data)
    if outcome in INSTALLED or outcome == "already_replaced":
        logger.info("library edit for %s: %s (version %s)", job_id, outcome, version)
    else:
        _page(d, job_id, f"{what} {version} was used in the draft and not accepted: {outcome}", code)
    return outcome


__all__ = ["CODE", "delivery_note", "hand_over", "read"]
