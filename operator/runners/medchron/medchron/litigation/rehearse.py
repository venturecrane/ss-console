"""`medchron litigate --rehearse <report.json>`: the real job on the real seat,
minus every effect.

What a rehearsal guarantees, each pinned by a test:

* **No write to the firm's system.** The seat is wrapped read-only: a
  ``create_folder`` or ``add_file`` raises before any request is made, and the
  walk stops before ``file`` (and ``report``) in any case.
* **No broker call.** The runner never speaks to the broker; a rehearsal job
  dir that has no ``job.json`` gets a synthetic one (scope all, nothing to
  file to, this month's spend 0), so the lane is not involved at all.
* **No touch of the lane's state.** The state dir is ``--state-dir`` or, when
  none is given, ``<job_dir>/rehearsal-state``; never the volume's. It is
  seeded ONCE from ``<inputs>/baseline/`` (never overwritten), and nothing is
  committed to it: ``report`` does not run.
* **Bounded spend.** ``--read-limit N`` reads N matters (the named ones first,
  then those with the most candidate files); inventory, manifest and diff
  still cover every matter, so the counts and the projection are whole.

Gates, parity and the workbook's leak scan run and are RECORDED, not obeyed:
a rehearsal exists to see what they would say. The verdict is always
``held`` with a ``rehearsal: ...`` reason, and the report JSON carries the
measurements (``REPORT_KEYS``).
"""

from __future__ import annotations

import datetime as dt
import json
import resource
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from ..seat import SeatError
from . import fetch as fetch_mod, inventory, manifest, passes
from .outcome import LitigationFailed, LitigationHold, Verdict
from .run import STAGES, LitigationRun
from .tools import dump

REHEARSAL_STAGES = STAGES[: STAGES.index("file")]
WEEKDAYS_PER_MONTH = 21.7
PROJECTION_DAYS = 5
SYNTHETIC_JOB_ID = "01REHEARSA0000000000000000"
NOWHERE = "00000000-0000-4000-8000-000000000000"
REPORT_KEYS = (
    "rehearsal",
    "stages",
    "peak_rss",
    "cents",
    "counts",
    "gates",
    "parity",
    "book",
    "projection",
    "matters",
)


class RehearsalError(RuntimeError):
    pass


class OfflineSeat:
    """No seat at all (a laptop replay of a copied job dir): every listing,
    mint and write raises, so a replay reads only what the job dir holds."""

    writes_refused = 0

    def __getattr__(self, name: str) -> Any:
        def refuse(*_a: Any, **_k: Any) -> Any:
            raise SeatError("offline: no seat in this run")

        return refuse

    def mint(self, matter_id: str, file_ids: list[str]) -> list[dict[str, Any]]:
        return [{"id": f, "error": "offline: no seat in this run"} for f in file_ids]


class ReadOnlySeat:
    """Every read passes through; every write raises before it is made."""

    def __init__(self, seat: Any) -> None:
        self._seat = seat
        self.writes_refused = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._seat, name)

    def create_folder(self, matter_id: str, name: str) -> dict[str, Any]:
        self.writes_refused += 1
        raise SeatError("rehearsal: the seat is read-only")

    def add_file(self, matter_id: str, folder_id: str, name: str, data: bytes) -> dict[str, Any]:
        self.writes_refused += 1
        raise SeatError("rehearsal: the seat is read-only")


def ensure_job(job_dir: Path, slug: str, staff_ids: list[str]) -> None:
    """A synthetic job.json when the rehearsal dir has none (never replaced)."""
    path = job_dir / "job.json"
    if path.is_file():
        return
    job_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        "kind": "litigation",
        "job_id": SYNTHETIC_JOB_ID,
        "trigger": "request",
        "requester": "rehearsal@rehearsal.invalid",
        "message_ref": "rehearsal",
        "request_text": "rehearsal",
        "scope": {"attorney_staff_ids": staff_ids} if staff_ids else {"all": True},
        "file_to_matter_id": NOWHERE,
        "file_to_matter_number": "REHEARSAL",
        "folder_name": "Rehearsal",
        "slug": slug,
        "month_cents_used": 0,
    }
    path.write_text(json.dumps(doc, indent=1), encoding="utf-8")


def seed_state(state: Path, inputs_dir: Path) -> bool:
    """Copy ``<inputs>/baseline/`` into the state dir once. True if seeded now."""
    src = inputs_dir / "baseline"
    if (state / "baseline.json").is_file() or not src.is_dir():
        state.mkdir(parents=True, exist_ok=True)
        return False
    shutil.copytree(src, state, dirs_exist_ok=True)
    return True


def _reads(p: dict[str, Any]) -> bool:
    return bool(p["read_groups"]) or p["audit"] == "all"


def pick(plan: dict[str, Any], limit: int, named: list[str]) -> list[str]:
    """The matters a limited rehearsal reads: named first, then the most candidates."""
    want = [mid for mid, p in plan.items() if _reads(p)]
    first = [m for m in named if m in plan]
    rest = sorted((m for m in want if m not in first), key=lambda m: (-len(plan[m]["candidates"]), m))
    return (first + rest)[:limit]


class RehearsalRun(LitigationRun):
    def __init__(
        self,
        job_dir: Path,
        *,
        report_path: Path,
        read_limit: int | None = None,
        read_matters: list[str] | None = None,
        seat_factory: Callable[[], Any] | None = None,
        offline: bool = False,
        **kw: Any,
    ) -> None:
        self.offline = offline

        def read_only() -> Any:
            if offline:
                return OfflineSeat()
            if seat_factory is not None:
                return ReadOnlySeat(seat_factory())
            from ..seat import open_seat

            return ReadOnlySeat(open_seat(self.job.slug))

        super().__init__(job_dir, seat_factory=read_only, **kw)
        if self.state.resolve() == manifest.state_dir().resolve():
            raise RehearsalError("rehearsal: refused; the state dir is the lane's own (pass --state-dir)")
        self.report_path = Path(report_path)
        self.read_limit = read_limit
        self.read_matters = list(read_matters or [])
        self.seeded = seed_state(self.state, self.firm.root)

    def _stage(self, name: str, fn: Callable[[], Any]) -> None:
        before = self.budget.refresh()
        try:
            super()._stage(name, fn)
        except (LitigationHold, LitigationFailed) as exc:
            if name not in ("gates", "parity", "book") and not (self.offline and name == "fetch"):
                raise
            self._put(f"{name}_refusal", str(exc))
            self._put(name, {"status": "done", "refused": True})
        st = self._state().get(name) or {}
        if "cents" not in st:
            self._put(name, {**st, "cents": round((self.budget.refresh() - before) * 100, 2)})

    def _diff(self) -> None:
        super()._diff()
        if self.read_limit is None:
            return
        plan = self._plan()
        keep = set(pick(plan, self.read_limit, self.read_matters))
        for mid, p in plan.items():
            if mid in keep:
                continue
            p["limited_out"] = {"read_groups": p["read_groups"], "audit": p["audit"]}
            p["read_groups"], p["audit"] = [], "none"
        dump(self.data / "plan.json", plan)

    def run(self) -> Verdict:
        """The report is written on EVERY exit path, a failed run included:
        a rehearsal that dies without one has measured nothing."""
        v = super().run()
        try:
            write_report(self, verdict=v)
        except Exception as exc:  # noqa: BLE001 - the verdict still goes out; the report says why it is thin
            self.report_path.write_text(
                json.dumps({"rehearsal": {"verdict": json.loads(v.to_json()), "report_error": repr(exc)[:300]}}),
                encoding="utf-8",
            )
        return v

    def _walk(self) -> Verdict:
        for name in REHEARSAL_STAGES:
            self._stage(name, getattr(self, f"_{name}"))
            if name == "inventory" and not self._matters():
                raise LitigationHold("scope_empty: no open litigation matters are in this job's scope")
        report = build_report(self)
        c, g, par = report["counts"], report["gates"], report["parity"]
        return Verdict(
            "held",
            stage="book",
            reason=(
                f"rehearsal: {c.get('matters_inventoried')} matters inventoried, {c.get('matters_read')} read, "
                f"{len(c.get('matters_unread') or [])} unread, "
                f"{sum((g.get('by_rule') or {}).values())} gate findings, "
                f"{par.get('unexplained')} unexplained changes; nothing filed"
            ),
            flags_new=0,
        )


# ---- the report -------------------------------------------------------------------------
def _rss() -> dict[str, Any]:
    unit = "bytes" if sys.platform == "darwin" else "kilobytes"
    me = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    kids = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return {"self": me, "children": kids, "unit": unit}


def _cents_by(r: RehearsalRun) -> dict[str, Any]:
    from ..ledger import read_rows

    by_call: dict[str, float] = {}
    by_model: dict[str, float] = {}
    for row in read_rows(r.data / "usage-ledger.jsonl"):
        c = r.pricing.price_row(row) * 100
        by_call[str(row.get("stage"))] = by_call.get(str(row.get("stage")), 0.0) + c
        by_model[str(row.get("model"))] = by_model.get(str(row.get("model")), 0.0) + c
    st = r._state()
    by_stage = {s: (st.get(s) or {}).get("cents", 0.0) for s in REHEARSAL_STAGES}

    def rnd(d: Any) -> dict[str, float]:
        return {k: round(v, 2) for k, v in d.items()}

    return {
        "total": round(r.budget.refresh() * 100, 2),
        "by_stage": rnd(by_stage),
        "by_call": rnd(by_call),
        "by_model": rnd(by_model),
    }


def _counts(r: RehearsalRun) -> dict[str, Any]:
    plan = r._plan()
    fails: Counter[str] = Counter()
    for mid in plan:
        for rec in fetch_mod.pulled(r.data, mid).values():
            if not rec.get("ok"):
                fails["fetch_not_ok"] += 1
        for rec in (r._fetch_report().get(mid) or {}).get("integrity") or []:
            fails[str(rec.get("problem"))] += 1
        from .extract import failures

        for rec in failures(fetch_mod.matter_dir(r.data, mid)):
            fails[str(rec.get("problem") or "").split(":")[0]] += 1
    read = [m for m, p in plan.items() if _reads(p)]
    inv = r._json("inventory.json")
    return {
        "matters_listed_in_scope": inv.get("listed"),
        "matters_inventoried": len(plan),
        "files_listed": sum(len(inventory.files_of(r.data, m)) for m in plan),
        "candidate_files": sum(len(p["candidates"]) for p in plan.values()),
        "plan_reasons": dict(Counter(p["reason"] for p in plan.values())),
        "changed_vs_seed": sum(1 for p in plan.values() if p["reason"] == "changed"),
        "new_vs_seed": sum(1 for p in plan.values() if p["reason"] == "new"),
        "would_read_without_limit": sum(1 for p in plan.values() if _reads(p.get("limited_out") or p)),
        "matters_read": len(read),
        "matters_read_ids": read,
        "matters_unread": [u["number"] for u in passes.unread_matters(r)],
        "extraction_failures_by_type": dict(fails),
        "state_seeded_from_inputs": r.seeded,
        "seat_writes_refused": getattr(r._seat, "writes_refused", 0),
    }


def _gates(r: RehearsalRun) -> dict[str, Any]:
    p = r.data / "gates.json"
    g = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {"issues": {}}
    plan = r._plan()
    read_nums = {str(m["number"]) for m in r._matters() if _reads(plan[m["id"]])}

    def on_read(item: Any) -> bool:
        first = item[0] if isinstance(item, list) and item else item
        return str(first) in read_nums

    return {
        "by_rule": {k: len(v) for k, v in g.get("issues", {}).items()},
        "by_rule_read_matters": {
            k: n for k, v in g.get("issues", {}).items() if (n := sum(1 for i in v if on_read(i)))
        },
        "examples": {k: v[:5] for k, v in g.get("issues", {}).items()},
        "refusal": r._state().get("gates_refusal"),
    }


def _parity(r: RehearsalRun) -> dict[str, Any]:
    p = r.data / "changes.json"
    changes = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    flat = [c for cs in changes.values() for c in cs]
    finals = r._json("final.json") if (r.data / "final.json").is_file() else []
    overturns = [o for m in finals for o in m.get("overturns") or []]
    return {
        "changes": len(flat),
        "explained": sum(1 for c in flat if c["explained"]),
        "unexplained": sum(1 for c in flat if not c["explained"]),
        "unexplained_paths": [c["path"] for c in flat if not c["explained"]][:40],
        "overturns": len(overturns),
        "overturns_by_pass": dict(Counter(str(o.get("pass")) for o in overturns)),
        "refusal": r._state().get("parity_refusal"),
    }


def _weekdays_before(today: dt.date, n: int) -> list[dt.date]:
    out, d = [], today
    while len(out) < n:
        d -= dt.timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return out


def projection(r: RehearsalRun) -> dict[str, Any]:
    plan = r._plan()
    days = _weekdays_before(r.today, PROJECTION_DAYS)
    per_day = {}
    for d in days:
        n = 0
        for mid in plan:
            files = inventory.files_of(r.data, mid)
            cands = set(manifest.candidates(files, r.firm, d))
            if any(manifest.file_date(f) == d.isoformat() and str(f["id"]) in cands for f in files):
                n += 1
        per_day[d.isoformat()] = n
    st = r._state()
    read_cents = sum((st.get(s) or {}).get("cents", 0.0) for s in ("fetch", "extract", "read1", "read2", "read3"))
    read_n = sum(1 for p in plan.values() if _reads(p))
    per_matter = read_cents / read_n if read_n else None
    mean = sum(per_day.values()) / len(days)
    monthly = None if per_matter is None else round(mean * per_matter * WEEKDAYS_PER_MONTH)
    return {
        "method": (
            f"For each of the last {PROJECTION_DAYS} weekdays before {r.today.isoformat()}, count the matters "
            "with at least one file whose Smokeball dateModified falls on that day AND which passes the "
            "candidate filter (court/discovery/process-server name, a recent case-pointing email, or one of the "
            "newest emails). Mean those counts, multiply by this run's measured cents per read matter (the cents "
            "of fetch+extract+read1+read2+read3 divided by the matters read), and by "
            f"{WEEKDAYS_PER_MONTH} weekdays a month. An approximation: newest-email membership is judged on "
            "today's listing, a dateModified moves on any edit, and matters a person reads by hand are not removed."
        ),
        "weekdays": per_day,
        "mean_rereads_per_weekday": round(mean, 2),
        "measured_cents_per_read_matter": None if per_matter is None else round(per_matter, 2),
        "projected_monthly_cents": monthly,
    }


def _safe(fn: Callable[[RehearsalRun], Any], r: RehearsalRun) -> Any:
    try:
        return fn(r)
    except Exception as exc:  # noqa: BLE001 - a section that cannot be measured says so; the report is still written
        return {"unavailable": f"{type(exc).__name__}: {str(exc)[:200]}"}


def write_report(r: RehearsalRun, verdict: Verdict | None = None) -> dict[str, Any]:
    report = build_report(r)
    if verdict is not None:
        report["rehearsal"]["verdict"] = json.loads(verdict.to_json())
    r.report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = r.report_path.with_name(f".{r.report_path.name}.tmp")
    tmp.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    tmp.replace(r.report_path)
    return report


def build_report(r: RehearsalRun) -> dict[str, Any]:
    st = r._state()
    book_p = r.data / "book.json"
    return {
        "rehearsal": {
            "job_dir": str(r.job.job_dir),
            "state_dir": str(r.state),
            "today": r.today.isoformat(),
            "read_limit": r.read_limit,
            "read_matters": r.read_matters,
            "filed": False,
        },
        "stages": {s: (st.get(s) or {}).get("seconds") for s in REHEARSAL_STAGES},
        "peak_rss": _safe(lambda _r: _rss(), r),
        "cents": _safe(_cents_by, r),
        "counts": _safe(_counts, r),
        "gates": _safe(_gates, r),
        "parity": _safe(_parity, r),
        "book": {
            **(json.loads(book_p.read_text(encoding="utf-8")) if book_p.is_file() else {}),
            "path": str(r._out()) if r._out().is_file() else None,
            "refusal": st.get("book_refusal"),
        },
        "projection": _safe(projection, r),
        "matters": _safe(per_matter, r),
    }


def per_matter(r: RehearsalRun) -> dict[str, Any]:
    """For each matter this run read: what was fetched and why, what the
    reads cost, and what they found (statuses only, never a name)."""
    from ..ledger import read_rows

    plan = r._plan()
    rows = read_rows(r.data / "usage-ledger.jsonl")
    out: dict[str, Any] = {}
    finals = {m["matter_id"]: m for m in (r._json("final.json") if (r.data / "final.json").is_file() else [])}
    for m in r._matters():
        mid, p = m["id"], plan[m["id"]]
        if not _reads(p):
            continue
        mine = [x for x in rows if str(x.get("custom_id") or "").endswith(mid) or mid in str(x.get("custom_id") or "")]
        calls: dict[str, int] = {}
        for x in mine:
            calls[str(x.get("stage"))] = calls.get(str(x.get("stage")), 0) + 1
        f = finals.get(mid) or {}
        out[str(m["number"])] = {
            "files_listed": m.get("files"),
            "selected": len(p["candidates"]),
            "selected_by_class": p.get("candidate_classes"),
            "fetched_ok": sum(1 for x in fetch_mod.pulled(r.data, mid).values() if x.get("ok")),
            "read_calls": calls,
            "read_cents": round(sum(r.pricing.price_row(x) * 100 for x in mine), 2),
            "unread": (r._mjson(mid, "unread.json") or {}).get("reason"),
            "case_status": (f.get("case_status") or {}).get("value"),
            "defendant_statuses": dict(Counter(str(d.get("status")) for d in f.get("defendants") or [])),
        }
    ocr = [x for x in rows if x.get("stage") == "litigation_ocr"]
    out["_ocr"] = {"pages": len(ocr), "models": dict(Counter(str(x.get("model")) for x in ocr))}
    return out
