"""A file the other-person RULE withheld on an earlier run is decided again,
before any page picture of this run's candidates is computed. $0, no model.

WHY (2026-11-17). An update withheld a 34-page file as another person's
because the text layer over two page images misread the client's date of
birth; 24 of its pages were picture for picture the client's own delivered
exhibit. #4105 stops that misread on the run that sees it, but a withhold the
rule made on an EARLIER run stood forever: the file was never a candidate
again, so nothing could look at it.

THE RULE. Only a withhold the rule made (``by: "rule"``, the other-person
ground) is re-decided; a person's decision never is. For each such file:

* the test(s) its withhold recorded (``foreign_identity.rule_tests``; every
  test when none was recorded) must be runnable on this job
  (``foreign_identity.runnable``: the date-of-birth test needs a date of
  birth, the name test a surname), and the file must have a text layer to
  test before the vision pass; otherwise it is KEPT, ``kept: cannot re-test``;
* the other-person test is run on its pages with the delivered-page
  exemption of #4105 (a page whose picture is EXACTLY a page of a delivered
  exhibit is the client's own): a finding keeps it (``kept``); none makes it
  ``cleared: pending selection``.

A file pending its clear is a candidate (``update_select._candidates`` reads
this record), and every later step treats it exactly as it treats any file
on main; a kept file is not a candidate, exactly as on main. The clear is
written to ``identity_release.json`` only once the file survives
``update_select._leave_out_foreign`` (``finalize``): a file that step
withholds again in the same run (it reads the run's own picture record,
where an exact extract of a delivered exhibit has no page rows, so the
exemption above may not reach it there) is recorded as ``kept: re-withheld
this run``, with both tests, and no decision row is added, so the outcome is
the FINAL one and the decisions never grow run after run. Each outcome is
recorded with its test codes in ``update/identity_redecided.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..medchron.gates import foreign_identity as FI
from ..medchron.update import pageid as PI, select as S
from .base import StageRun, read_jsonl

CLEARED, KEPT = "cleared", "kept"
KEPT_CANNOT = f"kept: {FI.KEPT_CANNOT_RETEST}"
#: Passed its test in ``update_pages``; cleared only if it survives selection (``finalize``).
PENDING = "cleared: pending selection"
KEPT_REWITHHELD = "kept: re-withheld this run"
KEPT_NOT_READ = "kept: not a candidate this run"


def _exempt(sigs: list[PI.Sig] | None, delivered: list[tuple[int, list[PI.Sig]]]) -> set[int]:
    """The pages whose picture is EXACTLY a page of a delivered exhibit."""
    if not sigs:
        return set()
    out: set[int] = set()
    for _n, theirs in delivered:
        out |= {p for p, (_q, exact) in PI.pair_pages(sigs, theirs).items() if exact}
    return out


def redecide(
    sr: StageRun, delivered: list[tuple[int, list[PI.Sig]]], sigs_of: Any, name: str
) -> dict[str, dict[str, Any]]:
    """{id: {"outcome", "tests"}} for every file the rule withheld before, each
    decided and the record written to ``update/<name>``. `sigs_of(path)`: the
    file's page pictures, or None when it cannot be drawn (then no page is
    exempt: the test runs stricter, never looser)."""
    from .update_base import update_dir
    from .update_select import _page_findings, _texts

    surnames, dobs = FI.identities(sr.job.units)
    can_run = FI.runnable(surnames, dobs)
    out: dict[str, dict[str, Any]] = {}
    for r in read_jsonl(sr.slug_dir / "raw_manifest.jsonl"):
        rid = str(r.get("id") or "")
        if not r.get("ok") or not r.get("path") or not FI.by_rule(sr.slug_dir, rid, FI.RULE_ANOTHER_PERSON):
            continue
        need = FI.rule_tests(sr.slug_dir, rid) or set(FI.ALL_TESTS)
        tests = sorted(need)
        has_text = any((t or "").strip() and t.strip() not in S.FAILED_MARKS for t in _texts(sr, rid).values())
        if not need <= can_run or not has_text:
            out[rid] = {"outcome": KEPT_CANNOT, "tests": tests}
            continue
        found = _page_findings(sr, rid, surnames, dobs, _exempt(sigs_of(Path(str(r["path"]))), delivered))
        if found:
            out[rid] = {"outcome": KEPT, "tests": tests}
            continue
        out[rid] = {"outcome": PENDING, "tests": tests}  # written to identity_release.json by ``finalize``
    ud = update_dir(sr)
    ud.mkdir(parents=True, exist_ok=True)
    (ud / name).write_text(json.dumps(out, indent=1, sort_keys=True), encoding="utf-8")
    if out:
        n = sum(v["outcome"] == PENDING for v in out.values())
        sr.log(
            f"update_pages: {len(out)} file(s) the other-person rule withheld before re-decided: {n} pass their test"
        )
    return out


def pending(path: Path) -> set[str]:
    """The files whose clear waits on selection (read tolerantly: a missing or
    damaged record means none, so every withhold stands)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return set()
    return (
        {str(k) for k, v in raw.items() if isinstance(v, dict) and v.get("outcome") == PENDING}
        if isinstance(raw, dict)
        else set()
    )


def finalize(sr: StageRun, path: Path, candidates: set[str], held: dict[str, list[str]]) -> None:
    """After ``_leave_out_foreign``: each file pending its clear is cleared
    (written once, now) when it was a candidate and selection did not withhold
    it; otherwise it stays withheld (``held``: {id: this run's test codes}),
    no decision row added, and the record says so. The record then holds the
    FINAL outcome."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return
    if not isinstance(raw, dict):
        return
    can_run = FI.runnable(*FI.identities(sr.job.units))
    for rid, v in raw.items():
        if not isinstance(v, dict) or v.get("outcome") != PENDING:
            continue
        if rid in held:
            v["outcome"], v["tests"] = KEPT_REWITHHELD, sorted(set(v.get("tests") or []) | set(held[rid]))
        elif rid in candidates:
            FI.clear_by_rule(sr.slug_dir, [rid], ground=FI.RULE_ANOTHER_PERSON, can_run=can_run)
            v["outcome"] = CLEARED
        else:
            v["outcome"] = KEPT_NOT_READ
    path.write_text(json.dumps(raw, indent=1, sort_keys=True), encoding="utf-8")
