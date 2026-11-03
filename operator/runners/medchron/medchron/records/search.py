"""Which file to read for each record, and what each read may cost.

WHY (2026-11-03, the first A&P dry run). The job priced the unread pages of
EVERY candidate of every record before reading anything: nine files, 4,775
unread pages, about $43 against a $5 cap, so it held at pre-flight having
found nothing, though most records sat in files whose index was already
complete. Now:

* Each record tries its candidates CHEAPEST FIRST: files whose index already
  covers every page (no page to buy), then by page count, smallest first; and
  it stops at the first file where the record is found. A file that would
  never have been needed is never priced.
* Pre-flight is PER FILE, just before its first read: its unread pages times
  ``usd_per_page`` against what is left of the job's cap. A file that would
  pass the cap HOLDS THAT RECORD ALONE, with ``per_job_cap_usd: <file id
  prefix> <unread> pages``; every other record still completes. The month's
  budget still holds the whole job, as before.
* The job-wide ``preflight`` stage spends nothing and checks only that the
  month's budget and the cap are not already reached; it logs each candidate's
  unread pages by file id prefix (never a name), so SMD can see what priced a
  job.

Item holds are saved in ``data/item_holds.json`` ``{n: reason}`` and the
order each record tried in ``data/order.json``, so a re-run reads the same
files in the same order and pays for nothing twice.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from smokeball_connector import page_finder as pf

from .. import limits as limits_mod, stop_request
from ..litigation.tools import dump
from . import assemble
from .outcome import RecordsHold

ORDER = "order.json"
HOLDS = "item_holds.json"
#: Characters of a file id a hold or log line names it by.
ID_PREFIX = 8


def unread(meta: dict[str, Any]) -> int:
    """Pages of the file not in its index (a page declined for good counts as read)."""
    index = pf.load_index(meta["sha256"])
    settled = sum(1 for k in index["unreadable"] if pf._settled(index, k) and k not in index["pages"])
    return max(0, int(meta["page_count"]) - len(index["pages"]) - settled)


def order(ids: list[str], files: dict[str, Any]) -> list[str]:
    """Complete indexes first, then smallest first; the id breaks a tie."""
    return sorted(ids, key=lambda fid: (unread(files[fid]) > 0, int(files[fid]["page_count"]), fid))


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def item_holds(data: Path) -> dict[int, str]:
    return {int(k): str(v) for k, v in (_read(data / HOLDS, {}) or {}).items()}


def orders(run: Any) -> dict[str, list[str]]:
    """Each record's candidates in the order it tries them, fixed on first use."""
    saved = _read(run.data / ORDER, None)
    if isinstance(saved, dict):
        return saved
    files, cand = run._json("files.json"), run._json("candidates.json")
    out = {n: order(list(ids), files) for n, ids in cand.items()}
    dump(run.data / ORDER, out)
    return out


def preflight(run: Any) -> None:
    """$0: the month's budget and the cap not already reached; the per-file log."""
    files, cand = run._json("files.json"), run._json("candidates.json")
    wanted = sorted({fid for ids in cand.values() for fid in ids})
    per = {fid: unread(files[fid]) for fid in wanted}
    detail = ", ".join(f"{fid[:ID_PREFIX]} {n}" for fid, n in sorted(per.items(), key=lambda kv: (kv[1], kv[0])))
    run.log(f"[preflight] {len(wanted)} files, {sum(per.values())} unread pages ({detail or 'none'})")
    run.limits.check_before_paid(projected_usd=0.0, spent_usd=run.budget.refresh(), stage="preflight")


def _priced(run: Any, fid: str, meta: dict[str, Any]) -> str | None:
    """The record's hold reason when this file's unread pages would pass the
    job's cap, else None. The month's budget raises (it holds the whole job)."""
    pages = unread(meta)
    if not pages:
        return None
    try:
        run.limits.check_before_paid(
            projected_usd=pages * run.firm.usd_per_page, spent_usd=run.budget.refresh(), stage="locate"
        )
    except limits_mod.LimitHold as hold:
        if hold.setting != limits_mod.CAP_SETTING:
            raise
        return f"{limits_mod.CAP_SETTING}: {fid[:ID_PREFIX]} {pages} pages"
    return None


def locate(run: Any, hook: Any, stop_cls: type[BaseException]) -> None:
    """Every record through its candidates, cheapest first, until it is found.
    A cap that one file would pass, or that a read reached, holds that record."""
    mid, files = run._matter(), run._json("files.json")
    holds = item_holds(run.data)
    for rec in run._records():
        n = int(rec["n"])
        if n in holds:
            continue
        for fid in orders(run).get(str(n)) or []:
            stop_request.check("records")
            if not run.locate_path(n, fid).is_file():
                why = _priced(run, fid, files[fid])
                if why:
                    holds[n] = why
                    break
            try:
                out = run._locate_one(n, fid, rec["asked"], mid, files[fid], hook)
            except stop_cls as stop:  # a read reached the cap: this record waits for SMD
                reason = str(getattr(stop, "hold").reason)
                if not reason.startswith(limits_mod.CAP_SETTING):
                    raise
                holds[n] = f"{limits_mod.CAP_SETTING}: {fid[:ID_PREFIX]} {unread(files[fid])} pages"
                break
            if out.get("matchError"):
                raise RecordsHold("finder_failed: the match step did not answer")
            if assemble.found(out):
                break
        dump(run.data / HOLDS, {str(k): v for k, v in holds.items()})
    for n, _fid, out in run.located():
        if n not in holds and int(out.get("remainingPages") or 0) > 0:
            raise RecordsHold(f"match_unsettled: record {n}'s file still has pages not read")


__all__ = ["HOLDS", "ID_PREFIX", "ORDER", "item_holds", "locate", "order", "orders", "preflight", "unread"]
