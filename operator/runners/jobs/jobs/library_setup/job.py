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
   never "none found". ``setup_kind: library`` ("set up the document library")
   hunts every kind in ``KINDS`` over one listing (``hunt.hunt_all``); a job
   that names a matter or a document's name (``point_matter``, ``point_name``,
   the administrator pointing at a document) hunts only that (``hunt.pointed``).
4. At most ``TOP`` candidates whose length is in the kind's band AND that the
   library file can be made from (``plan.contract_for``: the marked plan where
   one exists and fits, else a styles base; the whole make runs, with the
   firm's identity, and writes nothing), each with its file id, saved-at date,
   the matter's number and the contract. The best by score comes first, then
   the rest newest first.
5. For each kind, the file the seat uses now (the accepted-file ledger's entry,
   ``accept.replaces``): a ledger that cannot be read holds (``ledger_unreadable``).

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
from . import KINDS, LIBRARY, roster as roster_mod

#: How many candidates the firm is shown at most.
TOP = 3
#: Outcomes the firm is told.
CANDIDATES = "candidates"
NONE_FOUND = "none_found"
#: Hold codes, SMD's.
SWITCH_UNREADABLE = "switch_unreadable"
BAD_JOB = "bad_job"
LEDGER_UNREADABLE = "ledger_unreadable"
#: The administrator pointed at a matter the case system does not have: told.
MATTER_NOT_FOUND = "matter_not_found"
#: ``setup_outcome`` of a "set up the document library" job: one list per kind.
LIBRARY_LISTS = "library"

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


def _plan_fits(kind: str, c: hunt_mod.Candidate, client: Any, hunted: hunt_mod.Hunted, pieces: list[str]) -> str:
    """The contract the library file can be made with from this candidate
    (``plan.contract_for``, no write), or "". A refusal is counted by its code
    (``plan_refused`` and one ``<contract>_refused_<code>`` per contract tried),
    and a file that will not download is counted as could not look, never listed."""
    from ..firmbase import Source
    from .plan import contract_for

    try:
        blob = client.download_file(c.matter_id, c.file_id)[1]
    except Exception:  # noqa: BLE001 - a file that cannot be read now is not listed, and is counted
        hunted.note("plan_unreadable", c.file_id)
        return ""
    contract, refused = contract_for(kind, blob, Source(c.matter_id, c.file_id, "", c.sha256), pieces)
    for code in refused:
        hunted.note(code, c.file_id)
    if not contract:
        hunted.bump("plan_refused")
    return contract


def _shown_order(chosen: list[tuple[hunt_mod.Candidate, str]]) -> list[tuple[hunt_mod.Candidate, str]]:
    """The best by score first (what we recommend), then the rest newest first."""
    if len(chosen) < 2:
        return chosen
    return [chosen[0], *sorted(chosen[1:], key=lambda x: x[0].saved_at, reverse=True)]


def candidates(
    hunted: hunt_mod.Hunted, client: Any, kind: str = "", pieces: list[str] | None = None
) -> list[dict[str, Any]]:
    """The candidates the firm is shown: ids, dates, numbers and the contract.
    With ``kind``, only those the library file can be made from."""
    chosen: list[tuple[hunt_mod.Candidate, str]] = []
    for c in (x for x in hunted.candidates if _in_band(x)):
        if len(chosen) >= TOP:
            break
        contract = _plan_fits(kind, c, client, hunted, list(pieces or [])) if kind else ""
        if not kind or contract:
            chosen.append((c, contract))
    return [
        {
            "file_id": c.file_id,
            "matter_id": c.matter_id,
            "sha256": c.sha256,
            "matter_number": _matter_number(client, c.matter_id),
            "saved_on": _date(c.saved_at),
            "score": c.score,
            **({"contract": contract} if contract else {}),
        }
        for c, contract in _shown_order(chosen)
    ]


def found_count(hunted: hunt_mod.Hunted) -> int:
    """How many of the firm's own documents of the kind the hunt found: those
    in the kind's length band (what "found" means to the firm)."""
    return sum(1 for c in hunted.candidates if _in_band(c))


#: How many of the firm's own chart memos the hunt names, best first, and how many
#: in-band files it reads at most to find them (memo only; ``drafting/memo_charts``).
DONORS = 3
DONOR_READS = 30


def _donor(kind: str, c: hunt_mod.Candidate, client: Any) -> bool:
    from ..drafting.memo_charts import donor_charts
    from ..firmbase import Source
    from .plan import fits

    try:
        blob = client.download_file(c.matter_id, c.file_id)[1]
        return bool(donor_charts(blob)) and not fits(kind, blob, Source(c.matter_id, c.file_id, "", c.sha256))
    except Exception:  # noqa: BLE001 - a file that will not read or parse is no donor; the next one is tried
        return False


def chart_donors(hunted: hunt_mod.Hunted, client: Any, kind: str) -> list[dict[str, str]]:
    """The firm's own highest-ranked memos whose charts the memo plan reads, best
    first, by id: where a chosen memo with no chart takes its tables' look (the
    coordinator's decision on #3876). Empty for any other kind."""
    if kind != "memo":
        return []
    out: list[dict[str, str]] = []
    for reads, c in enumerate(x for x in hunted.candidates if _in_band(x)):
        if len(out) >= DONORS or reads >= DONOR_READS:
            break
        if _donor(kind, c, client):
            out.append({"file_id": c.file_id, "matter_id": c.matter_id, "sha256": c.sha256})
    return out


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


def _current(kind: str, attorney: str | None, spec_dir: Any, inputs_dir: str | None) -> Any:
    """The file the seat uses now for the slot: the accepted file
    (``accept.replaces``: its ids; "" when the ledger cannot be read), else
    ``{"pinned": True}`` when the firm's drafting inputs pin a firm file for the
    kind, else None (the standard format)."""
    from .accept import replaces

    found = replaces(kind, attorney, spec_dir)
    if found is not None:
        return found
    inputs = _inputs(inputs_dir)
    try:
        pinned = inputs is not None and (inputs.firm_base(kind) is not None or inputs.styles_base(kind) is not None)
    except Exception:  # noqa: BLE001 - a pin we cannot read is not claimed; the standard format is said
        pinned = False
    return {"pinned": True} if pinned else None


def _pieces(inputs_dir: str | None, pieces: list[str] | None) -> list[str]:
    from .accept import identity

    return list(pieces) if pieces is not None else identity(inputs_dir)


def _one_kind(
    kind: str,
    hunted: hunt_mod.Hunted,
    client: Any,
    pieces: list[str],
    current: Any,
) -> dict[str, Any]:
    found = candidates(hunted, client, kind, pieces)
    out: dict[str, Any] = {
        "kind": kind,
        "setup_outcome": CANDIDATES if found else NONE_FOUND,
        "found": found_count(hunted),
        "candidates": found,
        "counts": dict(sorted(hunted.counts.items())),
        "dropped_files": {k: list(v) for k, v in sorted(hunted.files.items())},
        "current": current,
    }
    if found and kind == "memo":
        out["chart_donors"] = chart_donors(hunted, client, kind)
    return out


def _pointed_matter(client: Any, number: str) -> tuple[str, str]:
    """(the matter id, "") or ("", the outcome or hold code)."""
    from smokeball_connector.library import lookup_matter

    found = lookup_matter(client, number=number)
    if found.failed:
        return "", roster_mod.COULD_NOT_LOOK
    return (found.matter_id or "", "") if found.found else ("", MATTER_NOT_FOUND)


def _hunt_one(job: dict[str, Any], kind: str, client: Any, now: Any, person: str | None) -> hunt_mod.Hunted | str:
    """One kind's hunt: the whole firm, or what the administrator pointed at."""
    number = str(job.get("point_matter") or "").strip()
    name_words = str(job.get("point_name") or "").strip()
    if number:
        matter_id, code = _pointed_matter(client, number)
        if code:
            return code
        return hunt_mod.pointed(kind, client, matter_id=matter_id, now=now, person=person)
    if name_words:
        return hunt_mod.pointed(kind, client, name_words=name_words, now=now, person=person)
    return hunt_mod.hunt(kind, client, now=now, person=person)


def run(
    job: dict[str, Any],
    *,
    client: Any = None,
    inputs_dir: str | None = None,
    switch: Callable[[], Any] = _switch,
    now: datetime | None = None,
    pieces: list[str] | None = None,
    spec_dir: Any = None,
) -> dict[str, Any]:
    """The verdict for one job (the module docstring)."""
    sw = switch()
    live = bool(job.get("live")) and sw.enforcing and sw.error is None
    if sw.error is not None:
        return _held(SWITCH_UNREADABLE, False)
    kind = str(job.get("setup_kind") or "")
    if kind not in KINDS and kind != LIBRARY:
        return _held(BAD_JOB, live)
    client = client if client is not None else _client()
    attorney, told, hold = _attorney(job, client, inputs_dir)
    if hold:
        return _held(hold, live)
    who = {"attorney_key": attorney.folded_name, "attorney_name": attorney.case_system_name} if attorney else {}
    if told:
        return _verdict(live, setup_outcome=told, counts={}, candidates=[])
    try:
        allow = _pieces(inputs_dir, pieces)
    except roster_mod.RosterUnusable as exc:
        return _held(exc.code, live)
    kinds = KINDS if kind == LIBRARY else (kind,)
    current: dict[str, Any] = {}
    for k in kinds:
        current[k] = _current(k, attorney.folded_name if attorney else None, spec_dir, inputs_dir)
        if current[k] == "":
            return _held(LEDGER_UNREADABLE, live)
    person = attorney.case_system_name if attorney else None
    if kind == LIBRARY:
        hunts = hunt_mod.hunt_all(kinds, client, now=now, person=person)
    else:
        hunted = _hunt_one(job, kind, client, now, person)
        if hunted == MATTER_NOT_FOUND:
            return _verdict(live, setup_outcome=MATTER_NOT_FOUND, counts={}, candidates=[], **who)
        if isinstance(hunted, str):
            return _held(hunted, live)
        hunts = {kind: hunted}
    for h in hunts.values():
        if h.counts.get("matters_unlisted"):
            return _held(roster_mod.COULD_NOT_LOOK, live, counts=dict(sorted(h.counts.items())))
    lists = [_one_kind(k, hunts[k], client, allow, current[k]) for k in kinds]
    if kind == LIBRARY:
        return _verdict(live, setup_outcome=LIBRARY_LISTS, kinds=lists, **who)
    one = lists[0]
    extra = {"chart_donors": one["chart_donors"]} if "chart_donors" in one else {}
    if job.get("point_matter") or job.get("point_name"):
        extra["pointed"] = True
    return _verdict(
        live,
        setup_outcome=one["setup_outcome"],
        counts=one["counts"],
        candidates=one["candidates"],
        dropped_files=one["dropped_files"],
        found=one["found"],
        current=one["current"],
        **extra,
        **who,
    )


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


__all__ = [
    "CANDIDATES",
    "LIBRARY_LISTS",
    "MATTER_NOT_FOUND",
    "NONE_FOUND",
    "SWITCH_UNREADABLE",
    "TOP",
    "candidates",
    "found_count",
    "run",
    "run_dir",
]
