"""The proof run: a held job's twin, run by its own lane, stopped before the
write stage (``proof_mode.py``), so SMD can prove a lane within hours.

HOW IT IS ASKED FOR. ``medchron lane-proof dry-run <job_id>`` (root, on the
seat) drops ``.prove-<job_id>.json`` into the lane's queue, the same
marker-in-the-queue shape as a resume (``resume.py``: the daemon is the only
process that runs a child in the lane's cgroup). A forged marker buys nothing:
a proof run writes nothing to the firm and proves nothing by itself; a lane is
proven only by ``accept``, which is root, on the seat, with SMD's verify id.

HOW IT RUNS. Inside the lane's own thread, from ``claim_next``, only when that
lane is idle, so it never competes with a live job for the lane's memory cap.
The twin is the held job's envelope in a separate tree
(``<run_dir>/lane-proofs/<lane>/<attempt>/<job_id>``) driven by a copy of the
lane object whose broker READS through (allowance, month spend) and whose
``record`` writes nothing: the firm's job row never moves, no wake is queued
where the deliver loop looks, and no notice reaches a broker. A lane with a
persistent cursor (litigation, negotiation) gets a throwaway COPY of the real
one (``seed_state``): a proof measures what the live job would do from the
state the live job reads. 2026-10-13: the A&P litigation proof ran on an
EMPTY state, read every matter fresh and passed, while the live job on the
same scope held at the gates on the stored seed. The copy is written by the
twin and thrown away with it; nothing is ever copied back.

WHAT COUNTS. ``proof.json`` beside the twin says ``passed`` only when every
verdict the runner wrote is ``dry_run``: the pipeline reached the write stage
with every gate and the pre-delivery review behind it. Anything else is
``failed``, with the runner's own reason. ``passed`` is necessary, not
sufficient: someone reads the artifacts before ``accept`` (an automated green
is not a review; the 2026-10-12 discovery set passed its own gates with a
false declaration in it).

A twin is kept 72 h, like a finished job, then removed: a chronology's data is
a gigabyte and the volume is ten.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from . import lane_proof, proof_mode, verdict as verdict_mod
from .seat_probes import sticky_level

logger = logging.getLogger("medchron.daemon")

MARKER_PREFIX = ".prove-"
PROOFS_DIR = "lane-proofs"
RESULT = "proof.json"
KEEP_HOURS = 72.0
#: Envelopes SMD keeps for a lane still being proven (``keep``), under the run
#: dir, root-only: a job dir is wiped 72 h after it ends, and on 2026-10-14 the
#: only composed discovery request on the seat was due to go while its lane was
#: still being fixed. The envelope is all a proof run reads (it pulls the
#: matter afresh), so that is all that is kept: the request, never the records.
KEPT_DIR = "proof-envelopes"
KEPT_DAYS = 14.0
_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
#: A lane result that leaves the marker for the next tick (the job did not run).
RETRY_RESULTS = frozenset({"deferred", "waiting", "interrupted"})


class ProofBroker:
    """Reads through to the lane's broker; every ``record`` is kept here and
    written nowhere."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.records: list[dict[str, Any]] = []

    def record(self, job_id: str, state: str, fields: dict[str, Any]) -> dict[str, Any]:
        self.records.append({"state": state, "fields": fields})
        return {"ok": True, "job": {"id": job_id, "state": state}}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def proofs_root(run_dir: Path, lane: str) -> Path:
    return Path(run_dir) / PROOFS_DIR / lane


def twin(d: Any, root: Path) -> Any:
    """A copy of lane ``d`` whose job dirs live under ``root``, whose broker
    writes nothing, whose child runs in proof mode, and whose heartbeat is
    silent (the real lane's heartbeat file is the real lane's)."""
    cls = type(d)
    sub = type(
        f"Proof{cls.__name__}",
        (cls,),
        {"jobs": property(lambda self: root), "heartbeat": lambda self, **kw: None},
    )
    p = copy.copy(d)
    p.__class__ = sub
    p.broker = ProofBroker(d.broker)
    p.child_env = {**d.child_env, proof_mode.PROOF_ENV: "1"}
    if hasattr(p, "state_dir"):
        p.state_dir = str(seed_state(d, root))
    return p


def seed_state(d: Any, root: Path) -> Path:
    """The twin's state dir: a copy of the lane's real one when it exists
    (made once per attempt, so a resumed proof keeps its own progress), an
    empty dir otherwise. Read from the real dir, written only under ``root``."""
    dest = Path(root) / "state"
    real = Path(d.state_dir) if getattr(d, "state_dir", None) else None
    if dest.exists():
        return dest
    if real is not None and real.is_dir() and real.resolve() != dest.resolve():
        shutil.copytree(real, dest, symlinks=False, ignore_dangling_symlinks=True)
        chown = getattr(d, "_chown_child", None)
        if callable(chown):
            chown(dest)  # the child owns the copy, as it owns its job dir
    return dest


def request(queue: Path, job_id: str) -> Path:
    """The marker the CLI writes. The attempt stamp names the twin's tree."""
    if not _ULID.match(job_id):
        raise ValueError(f"{job_id!r} is not a job id")
    marker = Path(queue) / f"{MARKER_PREFIX}{job_id}.json"
    marker.write_text(json.dumps({"job_id": job_id, "attempt": time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())}))
    return marker


def _items(raw: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for v in raw:
        if isinstance(v, list):
            out.extend(x for x in v if isinstance(x, dict))
        elif isinstance(v, dict):
            out.append(v)
    return out


def read_verdicts(job_dir: Path) -> list[Any]:
    """Every verdict the proof run's runner wrote, in either shape a lane's
    runner emits.

    WHY (2026-10-13, the A&P litigation proof). The chronology, demand and
    drafting runners write a JSON LIST to ``verdict.json``; the litigation and
    negotiation runners write ONE JSON OBJECT (``__main__._cmd_litigate`` and
    ``_cmd_negotiate``, read back by their own lanes' ``read_verdict``).
    ``verdict.read`` keeps only a list, so a litigation run that wrote
    ``{"verdict": "dry_run", ...}`` read back as ``[]`` and its proof was
    recorded ``failed``, "the runner wrote no dry_run verdict", stages [] and
    0 cents. A single object is one verdict, judged exactly as a one-item list."""
    listed = verdict_mod.read(job_dir, "")
    if listed:
        return listed
    try:
        parsed = json.loads((Path(job_dir) / verdict_mod.NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [parsed] if isinstance(parsed, dict) else []


def judge(raw: list[Any]) -> tuple[bool, list[dict[str, Any]]]:
    """``passed`` when the runner wrote at least one verdict and every one is
    ``dry_run`` (the chronology, demand and drafting runners say ``outcome``;
    litigation and negotiation say ``verdict``)."""
    items = _items(raw)
    passed = bool(items) and all(str(i.get("outcome") or i.get("verdict") or "") == proof_mode.OUTCOME for i in items)
    return passed, items


def _artifacts(jd: Path) -> list[dict[str, Any]]:
    """What the run PRODUCED (its staged ``out/`` tree, plus a negotiation dry
    run's report), by path under the twin and size. Never the matter's own
    pulled files: on 2026-10-14 a discovery-set proof listed 21 pulled .docx
    from data/raw beside its 3 sets, burying the deliverable. Stays on the seat;
    the laptop wrapper copies it out for reading."""
    out = []
    for p in sorted(jd.rglob("*")):
        if not p.is_file():
            continue
        staged = "out" in p.relative_to(jd).parts and "raw" not in p.relative_to(jd).parts
        if staged or p.name == "dry-run-report.txt":
            out.append({"path": str(p.relative_to(jd)), "bytes": p.stat().st_size})
    return out[:200]


def _spend_cents(items: list[dict[str, Any]]) -> int:
    cents = sum(int(round(float(i.get("dollars") or 0) * 100)) for i in items)
    return cents + sum(int(i.get("cents") or 0) for i in items)


def _wipe_old(d: Any, now: float) -> None:
    root = proofs_root(d.run_dir, d.LANE)
    if root.is_dir():
        for attempt in root.iterdir():
            if attempt.is_dir() and now - attempt.stat().st_mtime > KEEP_HOURS * 3600:
                shutil.rmtree(attempt, ignore_errors=True)
    kept = kept_dir(d.run_dir, d.LANE)
    if kept.is_dir():
        for env in kept.glob("*.json"):
            if now - env.stat().st_mtime > KEPT_DAYS * 86400:
                env.unlink(missing_ok=True)


def kept_dir(run_dir: Path, lane: str) -> Path:
    return Path(run_dir) / KEPT_DIR / lane


def keep(run_dir: Path, lane: str, job_dir: Path, job_id: str) -> Path:
    """Copy a finished job's envelope where a proof run still finds it after
    the job dir is wiped (``source_envelope``), for ``KEPT_DAYS``. Root-only,
    like the queue it stands in for. Refuses a job with no envelope."""
    if not _ULID.match(job_id):
        raise ValueError(f"{job_id!r} is not a job id")
    src = Path(job_dir) / job_id / "envelope.json"
    if not src.is_file():
        raise FileNotFoundError(f"no envelope in {src.parent}")
    dest_dir = kept_dir(run_dir, lane)
    dest_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(dest_dir.parent, 0o700)
    os.chmod(dest_dir, 0o700)
    dest = dest_dir / f"{job_id}.json"
    tmp = dest_dir / f".{job_id}.json.tmp"
    shutil.copyfile(src, tmp)
    os.chmod(tmp, 0o600)
    tmp.replace(dest)
    return dest


def run_pending(d: Any) -> bool:
    """Run the oldest proof marker in this lane's queue, if any. True when the
    tick was spent on it (the caller claims nothing this tick)."""
    _wipe_old(d, d.clock())
    if not d.queue.is_dir() or sticky_level(d.sticky_db) == "HARD_STOP":
        return False  # a paused seat runs nothing, a proof included
    markers = sorted(d.queue.glob(f"{MARKER_PREFIX}*.json"))
    if not markers:
        return False
    marker = markers[0]
    try:
        req = json.loads(marker.read_text(encoding="utf-8"))
        job_id, attempt = str(req["job_id"]), str(req["attempt"])
        if not _ULID.match(job_id) or not re.fullmatch(r"\d{8}T\d{6}Z", attempt):
            raise ValueError("bad marker fields")
    except (OSError, ValueError, KeyError) as exc:
        logger.error("unreadable proof marker %s: %s", marker.name, exc)
        marker.unlink(missing_ok=True)
        return True
    root = proofs_root(d.run_dir, d.LANE) / attempt
    env = source_envelope(d, job_id)
    jd = root / job_id
    if not (jd / "envelope.json").is_file():
        if env is None:
            _finish(root, job_id, d, {"status": "refused", "why": "no envelope for that job in this lane"})
            marker.unlink(missing_ok=True)
            return True
        jd.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(env, jd / "envelope.json")
        _traversable(d, root)
    return _run(d, marker, root, job_id)


def source_envelope(d: Any, job_id: str) -> Path | None:
    """The envelope a proof run copies: the held job's, still in the queue,
    or else a FINISHED job's of this lane, still in its job dir (wiped at 72 h).
    The second is how SMD proves a lane before the firm next asks for it: a
    real request's own envelope, run again without filing, no ledger row and
    no allowance used (2026-10-13, the lead's proactive-proof ask)."""
    kept = kept_dir(d.run_dir, d.LANE) / f"{job_id}.json"
    for candidate in (d.queue / f"{job_id}.json", d.job_dir(job_id) / "envelope.json", kept):
        if candidate.is_file():
            return candidate
    return None


def _traversable(d: Any, root: Path) -> None:
    """The tree down to the twin, root-owned and group child EXECUTE-ONLY, the
    modes ``jobs/`` has (entrypoint.sh): the child must reach its own job dir
    and must not list the others. Only where a child uid exists (the seat)."""
    if not d.child_uid:
        return
    import grp

    gid = grp.getgrnam(d.child_uid).gr_gid
    top = Path(d.run_dir) / PROOFS_DIR
    for p in (top, top / d.LANE, root):
        os.chown(p, 0, gid)
        # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions - root-owned, group execute-only: the tightest mode that lets the child traverse to its own dir (the jobs/ tree's mode).
        os.chmod(p, 0o710)


def _run(d: Any, marker: Path, root: Path, job_id: str) -> bool:
    p = twin(d, root)
    st = p._daemon_state(job_id)
    if not st.get("claimed"):
        p._write_state(job_id, claimed=True, state="submitted", claimed_at=d.clock(), attempts=0, proof=True)
    logger.info("%s proof run of %s starting in %s", d.LANE, job_id, root)
    try:
        result = p.run_job(job_id)
    except Exception as exc:  # a proof that cannot start is a failed proof, reported
        logger.exception("%s proof run of %s could not run", d.LANE, job_id)
        _finish(root, job_id, d, {"status": "failed", "why": f"{type(exc).__name__}: {str(exc)[:300]}"})
        marker.unlink(missing_ok=True)
        return True
    if result in RETRY_RESULTS:
        logger.info("%s proof run of %s: %s; the marker stays for the next tick", d.LANE, job_id, result)
        return True
    passed, items = judge(read_verdicts(root / job_id))
    reasons = [str(i.get("reason")) for i in items if i.get("reason")]
    _finish(
        root,
        job_id,
        d,
        {
            "status": "passed" if passed else "failed",
            "why": "; ".join(reasons)[:500] or ("" if passed else "the runner wrote no dry_run verdict"),
            "stages": sorted({str(i.get("stage") or "") for i in items}),
            "cents": _spend_cents(items),
            "artifacts": _artifacts(root / job_id),
        },
    )
    marker.unlink(missing_ok=True)
    return True


def _finish(root: Path, job_id: str, d: Any, body: dict[str, Any]) -> None:
    env: dict[str, Any] = {}
    try:
        env = json.loads((root / job_id / "envelope.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    root.mkdir(parents=True, exist_ok=True)
    doc = {
        "lane": d.LANE,
        "key": lane_proof.lane_key(d.LANE, env if isinstance(env, dict) else {}),
        "job_id": job_id,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(d.clock())),
        **body,
    }
    (root / RESULT).write_text(json.dumps(doc, indent=1), encoding="utf-8")
    logger.warning("%s proof run of %s: %s", d.LANE, job_id, body.get("status"))


def results(run_dir: Path) -> list[dict[str, Any]]:
    """Every proof result still on the volume, newest last, with its path."""
    out = []
    for p in sorted(Path(run_dir, PROOFS_DIR).glob(f"*/*/{RESULT}")):
        try:
            out.append({**json.loads(p.read_text(encoding="utf-8")), "path": str(p.parent)})
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda r: str(r.get("finished_at") or ""))


__all__ = [
    "MARKER_PREFIX",
    "ProofBroker",
    "judge",
    "proofs_root",
    "read_verdicts",
    "request",
    "results",
    "run_pending",
    "seed_state",
    "twin",
]
