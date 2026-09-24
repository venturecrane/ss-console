"""`dos_check`: did every billed date of service reach the chronology? $0.

The logic lives in `medchron.dos_check` (pure, no writes, so it also runs
against a delivered run, and `rehearse` below runs it for `medchron rehearse`);
this stage writes `runs/<unit>/dos_report.json` and logs the counts on every
run. It HOLDS only when the firm authored `levers.dos_check: hold` and a
billed date has a record page but no entry (a missed visit). An absent lever
is `report`: the check starts by measuring, and the firm's hold is switched on
from those numbers, not before them.

A held date is released by recording why (`medchron explain-date`), then
resuming the job. Exit 2 is a different thing: there was no entries file to
check at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import dos_check
from .base import StageRun
from .billing_chart import Matcher

ENTRY_SOURCES = ("entries_scoped_final.md", "entries_scoped.md", "entries_final.md")


def _report(sr: StageRun) -> tuple[Path, dict[str, Any]] | None:
    rd = sr.slug_dir / "runs" / sr.unit.unit
    src = next((rd / n for n in ENTRY_SOURCES if (rd / n).is_file()), None)
    if src is None:
        return None
    patient = sr.unit.client_name.lower() if sr.job.joint else None
    rep = dos_check.check(
        sr.slug_dir,
        rd,
        sr.job.incident_date,
        patient,
        src.read_text(encoding="utf-8"),
        Matcher(sr.cfg, sr.slug_dir).match,
    )
    return src, rep


def _lines(src: Path, rep: dict[str, Any]) -> list[str]:
    q = rep["quality"]
    out = []
    if q["billing_extract_missing"] or not q["bill_chunks"]:
        # Zero billed visits because nothing was billed looks exactly like zero
        # because the billing stage produced nothing; say which.
        why = "no billing extraction on disk" if q["billing_extract_missing"] else "no bill or ledger in it"
        out.append(f"dos_check: NOT MEASURED, {why}; a pass here proves nothing")
    out.append(
        f"dos_check: {rep['billed_dates']} billed visit(s) against {src.name}: "
        + ", ".join(f"{k} {v}" for k, v in rep["classes"].items())
    )
    out.append("dos_check data quality: " + ", ".join(f"{k} {v}" for k, v in q.items()))
    for m in rep["missed_visits"]:
        out.append(
            f"  MISSED VISIT {m['date']}: record page {m['record_file']} p.{m['record_page']}, "
            f"nearest entry {m['nearest_entry_days']} day(s) away"
        )
    for u in rep["provider_unmatched"]:
        out.append(
            f"  PROVIDER UNMATCHED {u['date']}: billed by {u['billing_provider']}; "
            f"entries that day name {', '.join(u['entry_providers'])}"
        )
    return out


def rehearse(sr: StageRun) -> list[str]:
    """The $0 half, for `medchron rehearse`: the same report, written nowhere."""
    got = _report(sr)
    return ["no entries file yet (composition has not run)"] if got is None else _lines(*got)


def run(sr: StageRun) -> int:
    got = _report(sr)
    if got is None:
        sr.log("dos_check: no entries file to check")
        return 2
    src, rep = got
    dos_check.write(src.parent, rep)
    for line in _lines(src, rep):
        sr.log(line)
    missed = rep["classes"]["missed_visit"]
    mode = str(sr.cfg.get("levers", "dos_check") or "report")
    if missed and mode == "hold":
        sr.log(
            f"HELD: {missed} billed date(s) of service have a record page and no chronology "
            "entry; record why with `medchron explain-date` or rebuild the entries, then resume"
        )
        return 1
    return 0
