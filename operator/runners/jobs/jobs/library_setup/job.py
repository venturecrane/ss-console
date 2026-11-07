"""One "set up our ..." job, from ``job.json`` to its verdict. Reads only.

THE ORDER, ALL IN CODE (no model call anywhere in this job):

1. The seat's ``logic_in_code.library_setup`` switch, read now through the
   connector's copy. A switch the seat cannot read HOLDS the job for SMD
   (``switch_unreadable``): an unreadable switch must neither send nor write.
   The job is live only when the switch enforces now AND it did when the
   request was queued (``live`` on the envelope), so a firm never hears twice.
2. An attorney named in the request is matched to the roster
   (``roster.attorneys`` / ``roster.pick``). An attorney the roster does not
   have is told to the firm; a roster that cannot be built holds for SMD.
3. The read-only hunt (``firmbase.hunt``) over the firm's open and pending
   matters, narrowed to that attorney's own files when one was named. A matter
   whose listing failed holds the job (``could_not_look``): "could not look" is
   never "none found".
4. At most ``TOP`` candidates whose length is in the kind's band, each with its
   file id, saved-at date and the matter's number.

A job whose ``mode`` is ``accept`` is the administrator's choice from such a
list (``accept.py``).

THE VERDICT carries ids, dates, numbers, counts and codes, never a file name or
any text of a document (the hunt's names carry client names). It is the one
thing the lane records. State:

* ``observed``: the switch is off. Counted and recorded; nothing is sent.
* ``delivered``: live; the firm is told the outcome (candidates, none found,
  an attorney not on the roster or named ambiguously), in the broker's words.
* ``held``: SMD's (an unreadable switch, a roster or a listing that could not
  be read); the firm is told nothing and SMD is paged.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..firmbase import hunt as hunt_mod
from . import KINDS, roster as roster_mod

#: How many candidates the firm is shown at most.
TOP = 3
#: Outcomes the firm is told.
CANDIDATES = "candidates"
NONE_FOUND = "none_found"
#: Hold codes, SMD's.
SWITCH_UNREADABLE = "switch_unreadable"
BAD_JOB = "bad_job"

OBSERVED, DELIVERED, HELD = "observed", "delivered", "held"


def _switch() -> Any:
    from smokeball_connector import logic_in_code

    return logic_in_code.load_switch(logic_in_code.LIBRARY_SETUP)


def _client() -> Any:
    from smokeball_connector.client import build_client_from_env

    return build_client_from_env()


def _inputs(inputs_dir: str | None) -> Any:
    from ..drafting import firm as drafting_firm

    try:
        return drafting_firm.load(inputs_dir)
    except (drafting_firm.DraftingConfigError, OSError, ValueError):
        return None


def _in_band(c: hunt_mod.Candidate) -> bool:
    return any(", in the band " in r for r in c.reasons)


def _matter_number(client: Any, matter_id: str) -> str:
    try:
        m = client.get(f"/matters/{matter_id}")
    except Exception:  # noqa: BLE001 - a number we cannot read is left out of the sentence, never guessed
        return ""
    number = m.get("number") if isinstance(m, dict) else None
    return str(number).strip() if isinstance(number, str) and number.strip() else ""


def _date(saved_at: str) -> str:
    try:
        return datetime.fromisoformat(saved_at.replace("Z", "+00:00")).astimezone(timezone.utc).date().isoformat()
    except ValueError:
        return ""


def _plan_fits(kind: str, c: hunt_mod.Candidate, client: Any, hunted: hunt_mod.Hunted) -> bool:
    """The kind's plan can adopt this candidate (``plan.fits``, no write); a
    refusal is counted by its code, and a file that will not download is
    counted as could not look, never listed."""
    from ..firmbase import Source
    from .plan import fits

    try:
        blob = client.download_file(c.matter_id, c.file_id)[1]
    except Exception:  # noqa: BLE001 - a file that cannot be read now is not listed, and is counted
        hunted.bump("plan_unreadable")
        return False
    why = fits(kind, blob, Source(c.matter_id, c.file_id, "", c.sha256))
    if why:
        hunted.bump("plan_refused")
        hunted.bump(f"plan_refused_{why}")
    return not why


def candidates(hunted: hunt_mod.Hunted, client: Any, kind: str = "") -> list[dict[str, Any]]:
    """The candidates the firm is shown: ids, dates and numbers only. With
    ``kind``, only those the kind's plan can make a library file from."""
    chosen = []
    for c in (x for x in hunted.candidates if _in_band(x)):
        if len(chosen) >= TOP:
            break
        if not kind or _plan_fits(kind, c, client, hunted):
            chosen.append(c)
    return [
        {
            "file_id": c.file_id,
            "matter_id": c.matter_id,
            "sha256": c.sha256,
            "matter_number": _matter_number(client, c.matter_id),
            "saved_on": _date(c.saved_at),
            "score": c.score,
        }
        for c in chosen
    ]


def _verdict(live: bool, **fields: Any) -> dict[str, Any]:
    """Delivered when live, observed when the switch is off: the same facts either way."""
    return {"outcome": DELIVERED if live else OBSERVED, "live": live, **fields}


def _held(code: str, live: bool, **more: Any) -> dict[str, Any]:
    return {"outcome": HELD, "live": live, "hold": code, "reason": f"{code}: the library setup job stopped", **more}


def _attorney(job: dict[str, Any], client: Any, inputs_dir: str | None) -> tuple[Any, str, str]:
    """(the roster attorney or None, the firm outcome, the hold code)."""
    named = str(job.get("attorney_named") or "").strip()
    if not named:
        return None, "", ""
    try:
        roster = roster_mod.attorneys(_inputs(inputs_dir), client)
    except roster_mod.RosterUnusable as exc:
        return None, "", exc.code
    found, outcome = roster_mod.pick(named, roster)
    return found, outcome, ""


def run(
    job: dict[str, Any],
    *,
    client: Any = None,
    inputs_dir: str | None = None,
    switch: Callable[[], Any] = _switch,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The verdict for one job (the module docstring)."""
    sw = switch()
    live = bool(job.get("live")) and sw.enforcing and sw.error is None
    if sw.error is not None:
        return _held(SWITCH_UNREADABLE, False)
    kind = str(job.get("setup_kind") or "")
    if kind not in KINDS:
        return _held(BAD_JOB, live)
    client = client if client is not None else _client()
    attorney, told, hold = _attorney(job, client, inputs_dir)
    if hold:
        return _held(hold, live)
    who = {"attorney_key": attorney.folded_name, "attorney_name": attorney.case_system_name} if attorney else {}
    if told:
        return _verdict(live, setup_outcome=told, counts={}, candidates=[])
    hunted = hunt_mod.hunt(kind, client, now=now, person=attorney.case_system_name if attorney else None)
    counts = dict(sorted(hunted.counts.items()))
    if counts.get("matters_unlisted"):
        return _held(roster_mod.COULD_NOT_LOOK, live, counts=counts)
    found = candidates(hunted, client, kind)
    counts = dict(sorted(hunted.counts.items()))
    outcome = CANDIDATES if found else NONE_FOUND
    return _verdict(live, setup_outcome=outcome, counts=counts, candidates=found, **who)


def run_dir(job_dir: Path, *, inputs_dir: str | None = None, **kw: Any) -> dict[str, Any]:
    """``run`` on a job dir's ``job.json``; a job file that will not read holds."""
    try:
        job = json.loads((Path(job_dir) / "job.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _held(BAD_JOB, False)
    if not isinstance(job, dict):
        return _held(BAD_JOB, False)
    if job.get("mode") == "accept":
        from . import accept

        return accept.run(job, Path(job_dir), inputs_dir=inputs_dir, **kw)
    return run(job, inputs_dir=inputs_dir, **kw)


__all__ = ["CANDIDATES", "NONE_FOUND", "SWITCH_UNREADABLE", "TOP", "candidates", "run", "run_dir"]
