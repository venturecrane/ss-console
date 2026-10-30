"""Resolve a cron session id to the routine that fired it (ss-console #2122).

95.7% of pilot rows and 100% of ashton-price rows carry ``skill_name = NULL``
because the audit hooks never learn which routine a turn belongs to: Hermes has
no runtime skill identity, and the cron store's job ids rotate on every
materialization (``bootstrap/cron_materialize.py`` removes and re-creates all
managed jobs), so a job id recorded raw resolves to nothing days later.

Two Hermes-native facts close the gap without new state:

* The scheduler tells the hooks which job a session belongs to, in one of two
  forms:

  - Hermes v0.22.5+ (ss-console #3581, 2026-10-28) runs each agent cron fire as
    an OWNER EXECUTION whose session id is ``local-<sha256>``
    (``hermes_state_keys.local_identity``): a hash nothing can read a job out
    of. The authoritative fact is the scheduler's own binding, held in two
    ContextVars for the life of the fire: ``gateway.session_cron.current_execution()``
    = ``(authority, session_id, job_id, execution_id)`` and
    ``cron.execution_identity.current_cron_execution()`` (the plugin API
    ``ctx.current_cron_execution()``) with the job id, execution id and
    ``started_at``. Model output cannot reach either (tool subprocesses do not
    share the interpreter), another firing job or an inbound turn sees its own
    context, and a delegated child reads ``None``. A session is a cron job's
    only when BOTH bindings exist, agree on job and execution, and the owner
    binding names THIS session id (:func:`_owner_cron_execution`). That is the
    guarantee: a non-cron turn holds no binding, so it can never claim a cron
    job's pre-rendered email or pre-run handoff.
  - Older Hermes, and ``run_job`` without an owner: the id is
    ``cron_{job_id}_{YYYYMMDD}_{HHMMSS}`` (``cron/scheduler.py``), parsed by
    :data:`_CRON_SESSION_RE`. Kept so a seat on an older image keeps working.
* The cron store (``$HERMES_HOME/profiles/<persona>/cron/jobs.json``, plus the
  root ``$HERMES_HOME/cron/jobs.json`` for the default profile) maps the live
  ``job_id`` to a STABLE managed name ``op-managed:<persona>:<skill>`` and a
  ``skills`` list (``bootstrap/cron_materialize.py::managed_name``).

So attribution is resolved AT EMISSION TIME — while the id → name mapping is
alive — and the resolved name is persisted in the row. Rotation stops
mattering: a later re-materialization can mint new ids freely because every
row already carries the durable identity.

Contract:

* ``resolve_routine(session_id)`` returns a :class:`RoutineIdentity` or
  ``None``. It NEVER raises — attribution is an enrichment of the audit row;
  the row itself is the obligation (same posture as ``shared.trust_decision``).
* Non-cron sessions (interactive, inbound, MCP) return ``None`` and the row's
  ``skill_name`` stays NULL — honest, since no routine fired them.
* An unresolvable job id (store unreadable, id already rotated away) returns
  ``None``, never a guess.

The jobs.json files are tiny (a dozen jobs); reads are cached per path and
invalidated by mtime, so per-tool-call overhead is a stat(), not a parse.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_HERMES_HOME = "/opt/data"

# ``cron_{job_id}_{YYYYMMDD}_{HHMMSS}`` — job ids are 12-hex today, but the id
# is matched non-greedily against the trailing timestamp rather than by shape,
# so an id containing an underscore would still parse.
#
# The timestamp halves are CAPTURED (ss-console#2547) so the pre-run handoff can
# bind a handoff file to the one session it was produced for. One regex, two
# readers: a second copy of this shape living beside the window check is exactly
# how a parser and its subject drift, and the drift would be invisible here — a
# handoff that never binds seeds nothing and the gate goes on refusing in
# silence, which is the failure class the handoff exists to end.
_CRON_SESSION_RE = re.compile(r"^cron_(?P<job_id>.+)_(?P<date>\d{8})_(?P<time>\d{6})$")

# ``op-managed:<persona>:<skill>`` per bootstrap/cron_materialize.py, plus an
# optional ``@<space>`` uniqueness suffix on space-mode seats
# (``shared/cron_space.py``). The skill stays bare so every reader of
# ``RoutineIdentity.skill`` is unchanged; the run's space is NOT read from here
# (``shared.space`` derives it from the delivery target).
_MANAGED_NAME_RE = re.compile(r"^op-managed:(?P<persona>[^:]+):(?P<skill>[^@]+)(?:@.+)?$")


@dataclass(frozen=True)
class RoutineIdentity:
    """The durable identity of the routine behind one cron session."""

    job_id: str
    job_name: str  # the stable managed name, or the raw name for unmanaged jobs
    persona: str | None  # parsed from the managed name; None for unmanaged jobs
    skill: str | None  # the skill the job runs; None when the job names none
    deliver: str | None = None  # the job's Hermes ``deliver`` as stored; None when absent


# path -> (mtime_ns, {job_id: RoutineIdentity})
_CACHE: dict[str, tuple[int, dict[str, RoutineIdentity]]] = {}


def _owner_cron_execution(session_id: str) -> tuple[str, datetime | None] | None:
    """``(job_id, started_at)`` when THIS session is a Hermes owner-execution cron fire.

    Hermes v0.22.5 names a cron fire's session ``local-<sha256>``, so the id says
    nothing; the scheduler's ContextVar bindings say everything (module
    docstring). Both must be present and agree, and the owner binding must name
    ``session_id`` itself, so a turn in the same context under another session id
    does not inherit the claim. ``started_at`` is the execution ledger's start
    instant, returned NAIVE in the scheduler's own clock (the same digits the
    legacy id carried, so every caller's clock reasoning is unchanged), or
    ``None`` when the row has none. ``None`` outside a cron fire or on any
    doubt. Never raises.
    """
    try:
        from cron.execution_identity import current_cron_execution
        from gateway.session_cron import current_execution
    except Exception:  # noqa: BLE001 — not inside Hermes (tests, tools)
        return None
    try:
        identity = current_cron_execution()
        owner = current_execution()
        if identity is None or not isinstance(owner, tuple) or len(owner) < 4:
            return None
        owner_session, owner_job, owner_execution = owner[1], owner[2], owner[3]
        job_id = getattr(identity, "job_id", None)
        if not isinstance(job_id, str) or not job_id:
            return None
        if not session_id or owner_session != session_id or owner_job != job_id:
            return None
        if str(owner_execution) != str(getattr(identity, "execution_id", None)):
            return None
        started_at: datetime | None = None
        raw = getattr(identity, "started_at", None)
        if isinstance(raw, str) and raw:
            try:
                started_at = datetime.fromisoformat(raw).replace(tzinfo=None)
            except ValueError:
                started_at = None
        return job_id, started_at
    except Exception as exc:  # noqa: BLE001 — enrichment, never the obligation
        logger.debug("cron_attribution: owner execution read failed for %r: %s", session_id, exc)
        return None


def parse_cron_session(session_id: str) -> str | None:
    """Return the cron job id this session belongs to, else ``None``.

    The legacy ``cron_<job>_<stamp>`` id first, then the v0.22.5 owner-execution
    binding (:func:`_owner_cron_execution`)."""
    if not isinstance(session_id, str) or not session_id:
        return None
    m = _CRON_SESSION_RE.match(session_id)
    if m:
        return m.group("job_id")
    owned = _owner_cron_execution(session_id)
    return owned[0] if owned else None


def parse_cron_session_started_at(session_id: str) -> datetime | None:
    """The wall-clock instant embedded in a cron session id, as a NAIVE datetime.

    ``cron_{job_id}_{YYYYMMDD}_{HHMMSS}`` — the scheduler stamps the id when it
    fires the turn, so this is the only fact about a cron turn's start time the
    hooks can read without asking anything.

    NAIVE ON PURPOSE, and the caller decides the clock. Nothing in the id says
    whether the scheduler formatted local time or UTC, and this module cannot
    find out from inside the agent process. Attaching a tzinfo here would be
    inventing that answer and hiding it behind a type; handing back the digits as
    they were written leaves the question with the one caller that has to answer
    it — ``shared.pre_run_handoff.take_handoff``, which tries both readings and
    explains there why that is safe.

    Returns ``None`` for a non-cron id or a stamp that is not a real instant
    (``..._20260231_120000``). Never raises: this is enrichment, like every other
    entry point in this module.
    """
    try:
        if not isinstance(session_id, str):
            return None
        m = _CRON_SESSION_RE.match(session_id)
        if m is None:
            owned = _owner_cron_execution(session_id)
            return owned[1] if owned else None
        return datetime.strptime(f"{m.group('date')}{m.group('time')}", "%Y%m%d%H%M%S")
    except Exception as exc:  # noqa: BLE001 — enrichment, never the obligation
        logger.debug("cron_attribution: session start parse failed for %r: %s", session_id, exc)
        return None


def _identity_from_job(job: dict[str, Any]) -> RoutineIdentity | None:
    job_id = job.get("id")
    if not isinstance(job_id, str) or not job_id:
        return None
    name = job.get("name") if isinstance(job.get("name"), str) else ""

    persona: str | None = None
    skill: str | None = None
    managed = _MANAGED_NAME_RE.match(name or "")
    if managed:
        persona = managed.group("persona")
        skill = managed.group("skill")
    else:
        # Unmanaged job (agent- or operator-created): take the declared skill.
        skills = job.get("skills")
        if isinstance(skills, list) and skills and isinstance(skills[0], str):
            skill = skills[0]
        elif isinstance(job.get("skill"), str) and job["skill"]:
            skill = job["skill"]

    deliver = job.get("deliver") if isinstance(job.get("deliver"), str) else None
    return RoutineIdentity(
        job_id=job_id, job_name=name or job_id, persona=persona, skill=skill, deliver=deliver
    )


def _load_store(path: Path) -> dict[str, RoutineIdentity]:
    """Load one jobs.json into an id-keyed index. mtime-cached, never raises."""
    key = str(path)
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        _CACHE.pop(key, None)
        return {}

    cached = _CACHE.get(key)
    if cached is not None and cached[0] == mtime_ns:
        return cached[1]

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Torn write mid-read or malformed store: serve stale cache if any —
        # better a slightly-old mapping than none — else empty.
        return cached[1] if cached is not None else {}

    jobs = raw.get("jobs") if isinstance(raw, dict) else raw
    index: dict[str, RoutineIdentity] = {}
    if isinstance(jobs, list):
        for job in jobs:
            if isinstance(job, dict):
                identity = _identity_from_job(job)
                if identity is not None:
                    index[identity.job_id] = identity
    _CACHE[key] = (mtime_ns, index)
    return index


def _store_paths(hermes_home: str) -> list[Path]:
    home = Path(hermes_home)
    paths = [home / "cron" / "jobs.json"]
    profiles = home / "profiles"
    try:
        if profiles.is_dir():
            paths.extend(sorted(profiles.glob("*/cron/jobs.json")))
    except OSError:
        pass
    return paths


def resolve_routine(session_id: str, *, hermes_home: str | None = None) -> RoutineIdentity | None:
    """Resolve a session id to its routine identity. Never raises."""
    try:
        job_id = parse_cron_session(session_id)
        if job_id is None:
            return None
        home = hermes_home or os.environ.get("HERMES_HOME") or _DEFAULT_HERMES_HOME
        for path in _store_paths(home):
            identity = _load_store(path).get(job_id)
            if identity is not None:
                return identity
        return None
    except Exception as exc:  # noqa: BLE001 — enrichment, never the obligation
        logger.warning("cron_attribution: resolve failed for session %r: %s", session_id, exc)
        return None


__all__ = [
    "RoutineIdentity",
    "parse_cron_session",
    "parse_cron_session_started_at",
    "resolve_routine",
]
