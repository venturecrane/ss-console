"""Rebuild a held job's document from ``build_doc``, after a fix that changes it.

A resume names stages to redo (``resume.py``), but ``build_doc`` cannot simply
be named once ``strip_apply`` has run: strip rewrote the exhibits in place
(the original kept beside each as ``.orig``) and renumbered the document's
citations, and a second apply refuses on purpose. A rebuilt document carries
pre-strip page numbers, so the exhibits must be the pre-strip ones again.

WHY (2026-10-22). The Curtis chronology (matter 200530) held at
``decide_orphans`` after strip, on a password-protected file. The fix that
explains such a file also names it in the Records Reviewed and Limitations
section, which ``build_doc`` writes; delivering without a rebuild would ship a
document silent about a file it never opened.

This is SMD's act, as root on the seat, with the medchron venv's python::

    python -m medchron.redo_doc <job_id> --by <who> --source <why> [--dry-run]

For each unit it puts every ``Exhibit N - ....pdf.orig`` back over its
stripped file, removes the strip result, and asks the daemon to resume the job
redoing ``build_doc``, ``strip_falsify``, ``strip_dry`` and ``strip_apply``
(``build_doc`` itself reopens the audit and the gates after it), and then
``render``, ``manifest`` and ``review``: a rebuilt document is a new document,
so it is rendered and reviewed again rather than shipped behind the old
document's render or held on the old document's review (2026-10-23, Curtis:
the review held on 45 findings and the corrected document must be reviewed
afresh). The classifiers' page labels describe the exhibits, which are
unchanged; the audit re-checks only claims whose text changed; the review is
the one paid stage that runs in full again.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .resume import MARKER_PREFIX

REDO = ["build_doc", "strip_falsify", "strip_dry", "strip_apply", "render", "manifest", "review"]


class Refused(RuntimeError):
    """The rebuild cannot be requested; the message says why."""


def _restores(outdir: Path) -> list[tuple[str, str]]:
    """``(stripped exhibit, its original)`` for every stripped exhibit."""
    if not outdir.is_dir():
        return []
    return [(str(o)[: -len(".orig")], str(o)) for o in sorted(outdir.glob("Exhibit *.pdf.orig"))]


def plan(d: Any, job_id: str, *, by: str, source: str) -> dict[str, Any]:
    """Every check, and what would be done. Raises Refused."""
    if not by.strip() or not source.strip():
        raise Refused("--by (who asked) and --source (why) are both required")
    if d._daemon_state(job_id).get("state") != "held":
        raise Refused(f"the daemon does not hold job {job_id}; it may be running already. Nothing was changed.")
    if (d.queue / f"{job_id}.json").exists() or (d.queue / f"{MARKER_PREFIX}{job_id}.json").exists():
        raise Refused(f"job {job_id} is already queued to run; nothing was changed")
    from . import job as job_mod

    job = job_mod.load(d.job_dir(job_id))
    slug_dir = job.data_root / job.slug
    units = []
    for u in job.units:
        units.append(
            {
                "unit": u.unit,
                "restore": _restores(slug_dir / "out" / u.unit),
                "strip_result": str(_strip_result(slug_dir, u.unit, job.joint)),
                "prestrip": str(_prestrip(slug_dir, u.unit)),
            }
        )
    return {
        "job_id": job_id,
        "units": units,
        "marker": {
            "job_id": job_id,
            "reason": f"document rebuilt from build_doc by SMD ({by.strip()}, {source.strip()})"[:500],
            "redo": list(REDO),
        },
    }


def _strip_result(slug_dir: Path, unit: str, joint: bool) -> Path:
    return slug_dir / f"strip_result{f'-{unit}' if joint else ''}.json"


def _prestrip(slug_dir: Path, unit: str) -> Path:
    """An unfinished strip_apply's copy of the pre-strip chronology. It must go
    with the restore: a rebuilt document would otherwise be ignored by the next
    apply, which resumes from that copy while it exists."""
    from .stages.strip import prestrip_path

    return prestrip_path(slug_dir / "runs" / unit / "final-chronology.md")


def _restore(restore: list[tuple[str, str]], strip_result: Path, prestrip: Path | None = None) -> None:
    for stripped, orig in restore:
        # The original was hard-linked beside the exhibit before the strip
        # replaced it, so the rename restores the very bytes exhibits wrote.
        Path(orig).replace(stripped)
    strip_result.unlink(missing_ok=True)
    if prestrip is not None:
        prestrip.unlink(missing_ok=True)


def restore_unit(slug_dir: Path, unit: str, *, joint: bool) -> int:
    """The rebuild's file half for one unit, in-process: put the unstripped
    exhibits back and remove the strip result, so the strip stages can run
    again over a rebuilt document. Returns how many exhibits were restored."""
    restore = _restores(slug_dir / "out" / unit)
    _restore(restore, _strip_result(slug_dir, unit, joint), _prestrip(slug_dir, unit))
    return len(restore)


def apply(d: Any, p: dict[str, Any]) -> None:
    for u in p["units"]:
        _restore(u["restore"], Path(u["strip_result"]), Path(u["prestrip"]) if u.get("prestrip") else None)
    d.queue.mkdir(parents=True, exist_ok=True)
    (d.queue / f"{MARKER_PREFIX}{p['job_id']}.json").write_text(
        json.dumps(p["marker"], sort_keys=True), encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="medchron.redo_doc", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("job_id")
    ap.add_argument("--by", required=True, help="who asked for the rebuild (an email address)")
    ap.add_argument("--source", required=True, help="why (a PR, a vfy_ id)")
    ap.add_argument("--dry-run", action="store_true", help="print what would be done; change nothing")
    a = ap.parse_args(argv)
    from . import daemon as daemon_mod

    d = daemon_mod.build()
    try:
        p = plan(d, a.job_id, by=a.by, source=a.source)
    except (Refused, OSError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"dry_run": a.dry_run, **p}, indent=1, default=str))
    if a.dry_run:
        return 0
    apply(d, p)
    print(f"restored the exhibits and asked to rebuild {a.job_id}; the daemon re-queues it on its next tick")
    return 0


if __name__ == "__main__":
    sys.exit(main())
