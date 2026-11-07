"""The made library file, handed from the jobs runner to the root writer.

The child filed the made file in the firm's Document Library and left its bytes
in the job's data folder (``library_setup/accept.py``). This, in the root daemon:

1. hands the bytes and what the child recorded to the broker
   (``library_setup_accept_submit``, root only). The broker checks the job row
   and the bytes' sha256 and spools a ``library_accept`` run in the shape of
   ``establish_intake/library_accept.SUBMISSION_FIELDS``;
2. waits for the intake's answer (``library_setup_accept_status``): the root
   writer's own no-client-text check, the vault write, and the applier's
   manifest. ``installed`` and ``accepted_pending_install`` are told to the
   firm; anything else, and an answer that never comes, hold for SMD (the file
   is in the library but is not an accepted file, so no draft is made in it).

Nothing here sends anything; the reply is the broker's words, bound and sent
in code after the DELIVER wake.
"""

from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from typing import Any

from .library_setup.accept import MADE_NAME

logger = logging.getLogger("jobs.drafting")

#: The broker takes one request line of at most 1 MiB, and base64 grows bytes by a third.
MAX_BYTES = 700_000
#: The intake waits up to 90 s for the applier; this waits a little longer.
WAIT_SECONDS = 240.0
POLL_SECONDS = 5.0
SLEEP = time.sleep
#: The intake's answers that the firm is told, and the outcome each is.
TOLD = {"installed": "accepted", "accepted_pending_install": "accepted_pending"}


def _held(code: str, report: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    return "held", {"cents": 0, "reason": f"{code}: the library setup job stopped", "report": {**report, "hold": code}}


def _client(lane: Any) -> Any:
    return getattr(lane.broker, "client", lane.broker)


def _wait(lane: Any, job_id: str) -> dict[str, Any] | None:
    from .broker_client import BrokerError

    deadline = lane.clock() + WAIT_SECONDS
    while True:
        try:
            resp = _client(lane)._request({"action": "library_setup_accept_status", "job_id": job_id})
        except BrokerError as exc:
            logger.warning("library setup %s: the acceptance could not be read yet: %s", job_id, exc)
            resp = {}
        if resp.get("status") == "complete" and isinstance(resp.get("result"), dict):
            return resp["result"]
        if lane.clock() >= deadline:
            return None
        SLEEP(POLL_SECONDS)


def hand_over(lane: Any, job_id: str, job_dir: Path, verdict: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """(the state to record, its fields) for a child that filed the made file."""
    from .broker_client import BrokerError

    report = {k: verdict[k] for k in ("filed", "residue", "chart_source") if k in verdict}
    try:
        data = (Path(job_dir) / "data" / MADE_NAME).read_bytes()
    except OSError:
        return _held("made_file_missing", report)
    if len(data) > MAX_BYTES:
        return _held("made_file_too_large", report)
    request = {
        "action": "library_setup_accept_submit",
        "job_id": job_id,
        "data_b64": base64.b64encode(data).decode("ascii"),
        "filed": verdict.get("filed"),
        "fixed_strings": verdict.get("fixed_strings") or [],
        "replaces": verdict.get("replaces"),
        "chart_source": verdict.get("chart_source"),
    }
    try:
        resp = _client(lane)._request(request)
    except BrokerError as exc:
        logger.error("library setup %s: the acceptance was not spooled: %s", job_id, exc)
        return _held("accept_not_spooled", report)
    if not resp.get("run_id"):
        return _held("accept_not_spooled", report)
    result = _wait(lane, job_id)
    if result is None:
        return _held("accept_unconfirmed", report)
    outcome = TOLD.get(str(result.get("status") or ""))
    if outcome is None:
        return _held("accept_rejected", {**report, "accept_status": str(result.get("status") or "")})
    return "delivered", {"cents": 0, "report": {**report, "setup_outcome": outcome}}


__all__ = ["MAX_BYTES", "TOLD", "hand_over"]
