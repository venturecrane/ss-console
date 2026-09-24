"""`dos_check`: did every billed date of service reach the chronology? $0.

The logic lives in `medchron.dos_check` (pure, no writes, so it also runs
against a delivered run); this stage writes `runs/<unit>/dos_report.json` and
logs the counts on every run. It HOLDS only when the firm authored
`levers.dos_check: hold` and a billed date has a record page but no entry
(a missed visit). An absent lever is `report`: the check starts by measuring,
and the firm's hold is switched on from those numbers, not before them.

A held date is released by recording why (`medchron explain-date`), then
resuming the job.
"""

from __future__ import annotations

from .. import dos_check
from .base import StageRun

ENTRY_SOURCES = ("entries_scoped_final.md", "entries_scoped.md", "entries_final.md")


def run(sr: StageRun) -> int:
    rd = sr.slug_dir / "runs" / sr.unit.unit
    src = next((rd / n for n in ENTRY_SOURCES if (rd / n).is_file()), None)
    if src is None:
        sr.log("dos_check: no entries file to check")
        return 1
    patient = sr.unit.client_name.lower() if sr.job.joint else None
    rep = dos_check.check(sr.slug_dir, rd, sr.job.incident_date, patient, src.read_text(encoding="utf-8"))
    dos_check.write(rd, rep)
    cls, q = rep["classes"], rep["quality"]
    sr.log(f"dos_check: {rep['billed_dates']} billed date(s) of service against {src.name}: "
           + ", ".join(f"{k} {v}" for k, v in cls.items()))
    sr.log("dos_check data quality: " + ", ".join(f"{k} {v}" for k, v in q.items()))
    for m in rep["missed_visits"]:
        sr.log(f"  MISSED VISIT {m['date']}: record page {m['record_file']} p.{m['record_page']}, "
               f"nearest entry {m['nearest_entry_days']} day(s) away")
    mode = str(sr.cfg.get("levers", "dos_check") or "report")
    if cls["missed_visit"] and mode == "hold":
        sr.log(f"HELD: {cls['missed_visit']} billed date(s) of service have a record page and no chronology "
               "entry; record why with `medchron explain-date` or rebuild the entries, then resume")
        return 1
    return 0
