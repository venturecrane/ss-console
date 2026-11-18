"""The one builder of a chronology run's record block (``run`` on an audit row).

WHY (2026-11-17). Whether a run needed anybody was not recorded anywhere a
program could read: the stop's stage was computed and dropped before the
ledger record, a resume or a ``*_decide`` left only a marker the daemon
deletes, and the job dir is wiped at 72 hours. Every site that ends, stops,
restarts or hands over a chronology job now builds its block here and passes
it in the record's fields; the broker checks it (``workspace_broker/run_meta.py``,
which drops anything it does not know) and writes it on the transition's audit
row; ``bin/work-health.py --runs`` folds the rows into runs and computes how
many finished with nobody stepping in.

Codes and counts only: an event word, a dag stage name, the leading token of
a reason (never its text), who took the step, the seat's release id, a job id
the run replaces, and named counts.

THE ``extra`` HOOK. A stage that wants a count on its run's record (page-class
counts, a record-only check's fire count) calls :func:`note_counts` with its
job's data root; it imports nothing from the broker or the lane. The lane's
end-of-job block reads the file back (:func:`counts`), so the counts ride the
record that ends the job. Writing it is best effort: a failure to note a count
never fails a stage.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger("jobs.daemon")

#: The image's commit (the Dockerfile bakes it) and the boot's jobs bundle
#: version (``entrypoint-bundles.sh`` writes ``<version>`` or ``image``).
IMAGE_REF_FILE = Path("/app/OVERLAY_REF")
BUNDLE_STATE_FILE = Path("/run/smd-bundles/jobs")
#: ``<job data root>/<this>``: the counts stages noted for the job's record.
COUNTS_NAME = "run-counts.json"
COUNT_BLOCKS = ("pages", "checks")

_SHA = re.compile(r"\A[0-9a-f]{7,40}\Z")
_BUNDLE = re.compile(r"\A[0-9]{12}-[0-9a-f]{8}\Z")
_HEAD = re.compile(r"\A[a-z][a-z0-9_.-]{0,47}")
_NOT_CODES = frozenset({"a", "an", "the"})
#: A stage whose name says it is a check: a gate, a proof, a review, an audit,
#: an identity or classification gate, the strip's falsifier, the date of
#: service check. A stop anywhere else is plumbing (the pull, the reads, the
#: render, the upload). Decision stages (``Stage.decision``) are checks too.
_CHECK = re.compile(r"(gate|check|review|audit|proof|identity|classify|strip|decide_|coverage)")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def release() -> str:
    """The seat's release id: ``img:<commit>``, ``+jobs:<bundle>`` when a
    published jobs bundle is mounted over the image's own copy, or
    ``unknown`` when neither can be read (a laptop, a test)."""
    sha = _read(IMAGE_REF_FILE)[:12]
    bundle = _read(BUNDLE_STATE_FILE)
    parts = []
    if _SHA.match(sha):
        parts.append(f"img:{sha}")
    if _BUNDLE.match(bundle):
        parts.append(f"jobs:{bundle}")
    return "+".join(parts) or "unknown"


def code_of(reason: Any) -> str:
    """The leading token of a reason, in work-health's ``_REASON_HEAD`` shape
    (``decision: two files ...`` is ``decision``, ``map incomplete: ...`` is
    ``map``), never the text after it. ``uncoded`` when there is none, or when
    the reason opens with an article (``the runner exited ...``, ``a truncated
    chunk ...``): that word is no code anybody wrote."""
    if not isinstance(reason, str):
        return "uncoded"
    text = reason.strip().lower()
    for prefix in ("refused: ", "proof run: "):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    found = _HEAD.match(text)
    code = found.group(0).rstrip(".-") if found else ""
    return code if code and code not in _NOT_CODES else "uncoded"


def stage_kind(stage: str | None, stages: Mapping[str, Any] | None = None) -> str | None:
    """``check`` or ``plumbing`` for a stop at ``stage`` (None when unnamed).
    ``stages`` is the lane's stage table (name to stage); a decision stage is a check."""
    if not stage:
        return None
    st = (stages or {}).get(stage)
    if st is not None and getattr(st, "decision", None):
        return "check"
    return "check" if _CHECK.search(stage) else "plumbing"


def known_stage(stage: str | None, stages: Mapping[str, Any] | None = None) -> str | None:
    """``stage`` when the lane's stage table names it (any shaped name when
    the lane gives no table), else None."""
    if not stage:
        return None
    if stages is None:
        return stage if _HEAD.fullmatch(stage) else None
    return stage if stage in stages else None


def stages_of(d: Any, job_id: str) -> Mapping[str, Any] | None:
    """The lane's stage table for this job (``run_stages``), or None."""
    fn = getattr(d, "run_stages", None)
    table = fn(job_id) if callable(fn) else None
    return table if isinstance(table, Mapping) else None


def note_counts(data_root: str | Path, block: str, counts: dict[str, int]) -> None:
    """The ``extra`` hook: add named counts to this job's run record (summed
    with what earlier calls noted). ``block`` is ``pages`` or ``checks``."""
    if block not in COUNT_BLOCKS:
        raise ValueError(f"block must be one of {COUNT_BLOCKS}")
    path = Path(data_root) / COUNTS_NAME
    try:
        have = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        mine = have.setdefault(block, {})
        for k, n in counts.items():
            if isinstance(n, int) and not isinstance(n, bool) and n >= 0:
                mine[str(k)] = int(mine.get(str(k)) or 0) + n
        path.write_text(json.dumps(have, sort_keys=True), encoding="utf-8")
    except (OSError, ValueError) as exc:
        logger.warning("could not note run counts in %s: %s", path, exc)


def counts(job_dir: str | Path) -> dict[str, dict[str, int]]:
    """What stages noted for the job (``note_counts``), or {}."""
    path = Path(job_dir) / "data" / COUNTS_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in raw.items() if k in COUNT_BLOCKS and isinstance(v, dict)} if isinstance(raw, dict) else {}


def block(
    event: str,
    *,
    by: str = "code",
    stage: str | None = None,
    reason: Any = None,
    code: str | None = None,
    replaces: str | None = None,
    with_release: bool = False,
    stages: Mapping[str, Any] | None = None,
    extra: dict[str, dict[str, int]] | None = None,
) -> dict[str, Any]:
    """One run block. ``code`` wins over ``reason`` (whose leading token is
    used); ``extra`` is ``{"pages": {...}, "checks": {...}}``."""
    out: dict[str, Any] = {"v": 1, "event": event, "by": by}
    named = known_stage(stage, stages)
    if named:
        out["stage"] = named
        kind = stage_kind(named, stages)
        if kind and event == "stop":
            out["kind"] = kind
    if code is not None or reason is not None:
        out["code"] = code if code is not None else code_of(reason)
    if replaces:
        out["replaces"] = replaces
    if with_release:
        out["release"] = release()
    for k, v in (extra or {}).items():
        if k in COUNT_BLOCKS and v:
            out[k] = dict(v)
    return out


def on(d: Any) -> bool:
    """Whether this lane records run blocks (the chronology lane, today)."""
    return bool(getattr(d, "RUN_RECORD", False))


def fields_for(d: Any, event: str, **kw: Any) -> dict[str, Any]:
    """``{"run": block(...)}`` on a lane that records runs, else {}: a site
    shared by every lane spreads this into its record's fields."""
    return {"run": block(event, **kw)} if on(d) else {}


#: A resume marker's request, as the event it records: SMD's release of a
#: review hold, a redo of named stages, or a plain resume. A ``*_decide`` tool
#: and ``redo_doc`` name their own event in the marker (``intervention``).
def marker_event(req: dict[str, Any]) -> str:
    named = req.get("intervention")
    if isinstance(named, str) and named:
        return named
    if req.get("release_review") or req.get("release_held"):
        return "release"
    if req.get("redo"):
        return "redo"
    return "resume"


__all__ = [
    "COUNTS_NAME",
    "block",
    "code_of",
    "counts",
    "fields_for",
    "known_stage",
    "marker_event",
    "note_counts",
    "on",
    "release",
    "stage_kind",
    "stages_of",
]
