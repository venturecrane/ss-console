"""Which file to read for each record, what each read may cost, and what each
(record, file) end state means. Every end state is decided HERE, in
:func:`_try` and :func:`locate`, never by accident of a later stage:

====  ==========================================================  ==============================================
 id   the (record, file) pair ends with                           the record's outcome
====  ==========================================================  ==============================================
 A    every page read, record found                               cut the ranges, attached
 B    every page read, record established absent                  "Not found"; the next candidate is tried
 C    pages declined for good, record found on the read pages     cut, attached; "N pages ... could not be read";
                                                                  that file's declined pages never read again
 D    pages declined for good, record not found on the read ones  "Not established" with the closest sections;
                                                                  the next candidate if one exists
 E    pages a no-progress pass could not reach (``notReached``)   found on the read pages: cut, "N pages ... were
                                                                  not read". Not found: the record holds
                                                                  ``match_unsettled: <file> N pages not read``.
                                                                  The SHARED index is never told they are
                                                                  unreadable: a page this job did not reach is
                                                                  this job's state, not the file's
 F    the file's first read would pass what is left of the cap    the record holds ``per_job_cap_usd: <file> N
                                                                  pages`` before any read; others go on
 G    the cap trips mid-file                                      pages already read stay in the index; the
                                                                  record holds as F; others go on
 H    the match call itself fails (``matchError``)                the next candidate; with none left, the record
                                                                  holds ``match_unsettled: <file> the match did
                                                                  not answer``. Never the whole job
 I    the same file is a candidate for two records                one index build; the second record only asks
                                                                  its own match
====  ==========================================================  ==============================================

A record no file on the matter is named for (``rank.py``) holds before
anything is read: ``scope_unresolved: no file on the matter is named for this
record``. The month's budget still holds the whole job.

THE READING LOOP (:func:`read_all`). A page the model declined is never asked
for again (``retry_refused=False``: the second dry run of 2026-11-03 paid 149
requests retrying 23 declined pages). The loop ends when every page is read or
declined, or when a pass reads nothing new, and never past ``ceil(pages / 100)
+ 1`` build calls; the match is then asked once against the index as it stands
(``page_finder.locate_index``), so a (record, file) pair costs at most
``ceil(pages / 100) + 2`` calls.

THE PRICE (``measure.py``). A file's first read is projected at its unread
pages times the higher of the firm's ``usd_per_page`` and this lane's own
measured cost per page attempt, times ``1 +`` the measured share of declined
pages (a declined page is billed twice: its batch, then alone). The pre-flight
logs the figure it used, and each candidate's unread pages by file id prefix,
never a name.

Item holds live in ``data/item_holds.json`` ``{n: reason}``; the order each
record tries its files in ``data/order.json``; their scores in
``data/scores.json``. A re-run reads the same files in the same order and pays
for nothing twice.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from smokeball_connector import page_finder as pf

from .. import limits as limits_mod, stop_request
from ..litigation.tools import dump
from . import assemble, measure
from .outcome import RecordsHold

ORDER = "order.json"
HOLDS = "item_holds.json"
SCORES = "scores.json"
#: Characters of a file id a hold or log line names it by.
ID_PREFIX = 8
NO_FILE = "scope_unresolved: no file on the matter is named for this record"


def unread(meta: dict[str, Any]) -> int:
    """Pages of the file in neither the index's pages nor its unreadable ones.
    The lane never asks for a declined page again (``retry_refused=False``),
    so a declined page costs nothing more and is not priced."""
    index = pf.load_index(meta["sha256"])
    gone = sum(1 for k in index["unreadable"] if k not in index["pages"])
    return max(0, int(meta["page_count"]) - len(index["pages"]) - gone)


def order(ids: list[str], files: dict[str, Any], scores: dict[str, int] | None = None) -> list[str]:
    """Best score first; then a complete index, then fewer pages; the id breaks a tie."""
    sc = scores or {}
    return sorted(
        ids, key=lambda fid: (-int(sc.get(fid, 0)), unread(files[fid]) > 0, int(files[fid]["page_count"]), fid)
    )


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
    scores = _read(run.data / SCORES, {}) or {}
    out = {n: order(list(ids), files, scores.get(n)) for n, ids in cand.items()}
    dump(run.data / ORDER, out)
    return out


def price(run: Any) -> measure.Price:
    return measure.price(run.state, float(run.firm.usd_per_page))


def price_per_page(run: Any) -> float:
    return price(run).per_page


def preflight(run: Any) -> None:
    """$0: the month's budget and the cap not already reached; the per-file log."""
    files, cand = run._json("files.json"), run._json("candidates.json")
    wanted = sorted({fid for ids in cand.values() for fid in ids})
    per = {fid: unread(files[fid]) for fid in wanted}
    detail = ", ".join(f"{fid[:ID_PREFIX]} {n}" for fid, n in sorted(per.items(), key=lambda kv: (kv[1], kv[0])))
    run.log(f"[preflight] {len(wanted)} files, {sum(per.values())} unread pages ({detail or 'none'})")
    run.log(f"[preflight] priced at {price(run).describe()}")
    run.limits.check_before_paid(projected_usd=0.0, spent_usd=run.budget.refresh(), stage="preflight")


def read_all(run: Any, source: Path, mid: str, hook: Any) -> int:
    """Read the file until every page is read or declined, or a pass reads
    nothing new; return the build calls made (at most ``ceil(pages / 100) +
    1``). Nothing is written to the index for a page the loop did not reach."""
    calls = 0
    while True:
        with tempfile.TemporaryDirectory(dir=run.data) as tmp:
            built = pf.build_index(source, Path(tmp), window=None, matter_id=mid, hook=hook, retry_refused=False)
        calls += 1
        index = built["index"]
        total = int(index.get("page_count") or 0)
        measure.note_pages(run.data, len(built["read_now"]), len(built["unreadable_now"]))
        left = total - len(index["pages"]) - len(index["unreadable"])
        if left <= 0:
            return calls
        if not (built["read_now"] or built["unreadable_now"]) or calls >= -(-total // pf.PAGES_PER_CALL) + 1:
            run.log(f"[locate] {left} pages not reached after {calls} calls; the file stays open for later jobs")
            return calls


def locate_path(run: Any, n: int, file_id: str) -> Path:
    return run.data / "locate" / str(n) / f"{file_id}.json"


def locate_one(run: Any, n: int, fid: str, what: str, mid: str, meta: dict[str, Any], hook: Any) -> dict[str, Any]:
    """The (record, file) finder result: read back when saved, else the loop
    and ONE match. Saved (never overwritten) unless the match failed, so a
    re-run reads it back and pays nothing, and a failed match can be asked again."""
    path = locate_path(run, n, fid)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        read_all(run, run.data / "files" / f"{fid}.pdf", mid, hook)
        out = pf.locate_index(meta["sha256"], what, hook=hook)
    except pf.FinderRefused as exc:
        raise RecordsHold(f"finder_failed: {exc}") from exc
    out["items"] = [{k: v for k, v in item.items() if not k.startswith("_")} for item in out.get("items") or []]
    out.update(matterId=mid, fileId=fid, name=meta.get("name") or "")
    if not out.get("matchError"):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # never overwritten
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(out, fh, sort_keys=True)
    return out


def _priced(run: Any, fid: str, meta: dict[str, Any]) -> str | None:
    """F: the record's hold reason when this file's first read would pass the
    job's cap, else None. The month's budget raises (it holds the whole job)."""
    pages = unread(meta)
    if not pages:
        return None
    try:
        run.limits.check_before_paid(
            projected_usd=pages * price_per_page(run), spent_usd=run.budget.refresh(), stage="locate"
        )
    except limits_mod.LimitHold as hold:
        if hold.setting != limits_mod.CAP_SETTING:
            raise
        return f"{limits_mod.CAP_SETTING}: {fid[:ID_PREFIX]} {pages} pages"
    return None


def _try(
    run: Any, n: int, fid: str, rec: dict[str, Any], meta: dict[str, Any], hook: Any, stop_cls: Any
) -> tuple[str, str]:
    """One (record, file) pair: ``(state, reason)``. ``found`` ends the record
    (A, C, E found); ``next`` tries the next file (B, D); ``unsettled`` tries
    the next file and, with none left, holds the record (E not found, H);
    ``held`` holds it now (F, G)."""
    if not locate_path(run, n, fid).is_file():
        why = _priced(run, fid, meta)
        if why:
            return "held", why
    try:
        out = locate_one(run, n, fid, rec["asked"], run._matter(), meta, hook)
    except stop_cls as stop:
        reason = str(getattr(stop, "hold").reason)
        if not reason.startswith(limits_mod.CAP_SETTING):
            raise
        return "held", f"{limits_mod.CAP_SETTING}: {fid[:ID_PREFIX]} {unread(meta)} pages"
    if out.get("matchError"):
        return "unsettled", f"match_unsettled: {fid[:ID_PREFIX]} the match did not answer"
    if assemble.found(out):
        return "found", ""
    left = int(out.get("remainingPages") or 0)
    if left:
        return "unsettled", f"match_unsettled: {fid[:ID_PREFIX]} {left} pages not reached"
    return "next", ""


def locate(run: Any, hook: Any, stop_cls: type[BaseException]) -> None:
    """Every record through its candidates, best first, until it is found."""
    files = run._json("files.json")
    holds = item_holds(run.data)
    for rec in run._records():
        n = int(rec["n"])
        if n in holds:
            continue
        pending = ""
        for fid in orders(run).get(str(n)) or []:
            stop_request.check("records")
            state, reason = _try(run, n, fid, rec, files[fid], hook, stop_cls)
            if state in ("found", "held"):
                pending = reason
                break
            pending = reason or pending
        if pending:
            holds[n] = pending
        dump(run.data / HOLDS, {str(k): v for k, v in holds.items()})


__all__ = [
    "HOLDS",
    "ID_PREFIX",
    "NO_FILE",
    "ORDER",
    "SCORES",
    "item_holds",
    "locate",
    "locate_one",
    "locate_path",
    "order",
    "orders",
    "preflight",
    "price",
    "price_per_page",
    "read_all",
    "unread",
]
