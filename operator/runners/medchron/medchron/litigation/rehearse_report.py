"""The rehearsal's report: what a ``RehearsalRun`` measured, as JSON.

Split from ``rehearse.py`` (the run and its guarantees) to keep each module
under the size ceiling. Every section is built by ``_safe``: one that cannot
be measured says why, and the report is still written.
"""

from __future__ import annotations

import datetime as dt
import json
import resource
import sys
from collections import Counter
from typing import Any, Callable

from ..stages.base import read_jsonl
from . import fetch as fetch_mod, inventory, manifest, passes, vocab
from .outcome import Verdict
from .run import STAGES

REHEARSAL_STAGES = STAGES[: STAGES.index("file")]
WEEKDAYS_PER_MONTH = 21.7
PROJECTION_DAYS = 5
OCR_LOG = "ocr-needed.jsonl"
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


def _reads(p: dict[str, Any]) -> bool:
    return bool(p["read_groups"]) or p["audit"] == "all"


# ---- the report -------------------------------------------------------------------------
def _rss() -> dict[str, Any]:
    unit = "bytes" if sys.platform == "darwin" else "kilobytes"
    me = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    kids = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return {"self": me, "children": kids, "unit": unit}


def _cents_by(r: Any) -> dict[str, Any]:
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


def _counts(r: Any) -> dict[str, Any]:
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
    ocr = read_jsonl(r.data / OCR_LOG)
    return {
        "matters_listed_in_scope": inv.get("listed"),
        "people_scope": inv.get("people_scope"),
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
        "ocr_pages_needed": len(ocr),
        "ocr_pages_by_model": dict(Counter(str(x.get("model")) for x in ocr)),
        "files_from_text_cache": sum(int(v.get("cached") or 0) for v in r._fetch_report().values()),
        "seat_writes_refused": getattr(r._seat, "writes_refused", 0),
        "fetch_refusal": r._state().get("fetch_refusal"),
    }


def _gates(r: Any) -> dict[str, Any]:
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


def _parity(r: Any) -> dict[str, Any]:
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


def _day_groups(r: Any, day: dt.date) -> tuple[int, int]:
    """(matters a refresh on ``day`` re-reads, field groups it re-reads)."""
    matters = groups = 0
    for mid in r._plan():
        files = inventory.files_of(r.data, mid)
        classes = dict(manifest.select(files, r.firm, day))
        trig = [str(f["id"]) for f in files if manifest.file_date(f) == day.isoformat() and str(f["id"]) in classes]
        if trig:
            matters += 1
            groups += len(manifest.groups_for(trig, classes))
    return matters, groups


def projection(r: Any) -> dict[str, Any]:
    plan = r._plan()
    days = _weekdays_before(r.today, PROJECTION_DAYS)
    per_day = {d.isoformat(): _day_groups(r, d) for d in days}
    st = r._state()
    read_cents = sum((st.get(s) or {}).get("cents", 0.0) for s in ("fetch", "extract", "read1", "read2", "read3"))
    read = [p for p in plan.values() if _reads(p)]
    read_groups = sum(len(p["read_groups"]) or len(vocab.GROUPS) for p in read)
    per_group = read_cents / read_groups if read_groups else None
    mean_groups = sum(g for _m, g in per_day.values()) / len(days)
    monthly = None if per_group is None else round(mean_groups * per_group * WEEKDAYS_PER_MONTH)
    return {
        "method": (
            f"Incremental refresh. For each of the last {PROJECTION_DAYS} weekdays before {r.today.isoformat()}, "
            "find every matter with a file whose Smokeball dateModified falls on that day and which the selection "
            "would hand a read; map each such file to the field groups its class can change (court paper: case "
            "and defendants; service record: defendants; discovery paper: discovery; named email: all three; an "
            "unnamed newest email: case status only) and count the groups a refresh would re-read. Mean the "
            "daily group count, multiply by this run's measured cents per field group read (the cents of "
            "fetch+extract+read1+read2+read3 divided by the field groups read, a full matter counting as all "
            f"three), and by {WEEKDAYS_PER_MONTH} weekdays a month. Approximations: a re-read of one group "
            "still opens the matter's papers, so a one-group read costs more than a third of a full read; "
            "newest-email membership is judged on that day's listing as it reads today. Vision OCR is not in "
            "the measured cents (a rehearsal never pays for it): this run's OCR is priced apart, as "
            "counts.ocr_pages_needed (text-cache misses the counting stub recorded) times the transcription model's "
            f"price for one page ({OCR_PAGE_INPUT_TOKENS} input and {OCR_PAGE_OUTPUT_TOKENS} output tokens, "
            "an estimate), in ocr_cents_needed."
        ),
        **ocr_cents(r),
        "weekdays": {d: {"matters": m, "groups": g} for d, (m, g) in per_day.items()},
        "mean_groups_per_weekday": round(mean_groups, 2),
        "measured_cents_per_group": None if per_group is None else round(per_group, 2),
        "measured_cents_per_full_matter": None if per_group is None else round(per_group * 3, 2),
        "projected_monthly_cents": monthly,
    }


#: One OCR page's tokens, an estimate: a page image rendered at
#: ``extract.RENDER_LONG_SIDE`` (about 1600 x 1240 px, ~2,650 image tokens)
#: plus the prompt, and a typed page of transcription out.
OCR_PAGE_INPUT_TOKENS = 2700
OCR_PAGE_OUTPUT_TOKENS = 900


def ocr_cents(r: Any) -> dict[str, Any]:
    """The vision pages this run would buy (the counting stub's log, cache
    misses only), priced at the transcription model's rate for one page."""
    pages = len(read_jsonl(r.data / OCR_LOG))
    rate = r.pricing.rate_for(r.firm.model("transcription"))
    per = (
        OCR_PAGE_INPUT_TOKENS * rate.input_per_million_cents + OCR_PAGE_OUTPUT_TOKENS * rate.output_per_million_cents
    ) / 1e6
    return {"ocr_cents_per_page": round(per, 3), "ocr_cents_needed": round(pages * per, 2)}


def _safe(fn: Callable[[Any], Any], r: Any) -> Any:
    try:
        return fn(r)
    except Exception as exc:  # noqa: BLE001 - a section that cannot be measured says so; the report is still written
        return {"unavailable": f"{type(exc).__name__}: {str(exc)[:200]}"}


def write_report(r: Any, verdict: Verdict | None = None) -> dict[str, Any]:
    report = build_report(r)
    if verdict is not None:
        report["rehearsal"]["verdict"] = json.loads(verdict.to_json())
    r.report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = r.report_path.with_name(f".{r.report_path.name}.tmp")
    tmp.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    tmp.replace(r.report_path)
    return report


def build_report(r: Any) -> dict[str, Any]:
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


def per_matter(r: Any) -> dict[str, Any]:
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
    return out
