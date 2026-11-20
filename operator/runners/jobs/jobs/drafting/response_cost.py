"""A discovery-response job's price and finish time, projected before the large spend.

WHY (2026-11-20). The lane's estimate prices the whole file through a digest
(``demand/preflight.estimate``) and runs before the job knows its sets. A
response job reads only its scoped corpus (``response_scope.py``), so it is
priced from that, once the scope step has run and before the transcription:

* **Transcription.** The scanned pages still queued after scope, minus those
  already in the seat's page-text store (``matter/store``), counted by READING
  the store read-only, never by paying. Measured 2026-11-20: 0 of 1,043, 307 of
  314 and 0 of 240 scan pages were already stored on three matters. Priced at
  the firm's ``budget.usd_per_scanned_page``.
* **Each unit.** Its pack's size (projected: text-layer characters, plus each
  queued scan page at ``response_scope.SCAN_PAGE_CHARS``, under the unit's
  cap; an inventory for production), one compose, one audit and a repair
  allowance, at the firm's models on the lane's rate card.
* **The job.** The attorney notes and the independent review over every pack
  and draft.

The cap is the firm's own ``budget.per_job_cap_usd``; nothing here adds one.
``run.py`` checks ``usd`` against it with the lane's limit hold, writes the
finish time at that moment, and runs the units cheapest first
(``cheapest_first``). Every constant says whether it is measured or a guess
to be replaced by a measurement.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from . import response_scope as scope_mod
from .evidence_cost import CHARS_PER_TOKEN

FILE = "response-estimate.json"
#: Characters of drafted prose per output token. GUESS (prose tokenizes
#: lighter than medical text's measured 2.0; 3.0 errs toward a higher price).
OUT_CHARS_PER_TOKEN = 3.0
#: The house style, skeleton, one exemplar, the context and the served
#: requests around a pack. GUESS: the ledger path's frame (evidence_cost).
COMPOSE_FRAME_CHARS = 160_000
#: Drafted characters per served request. MEASURED on the hand drafts
#: (2026-11-20): special interrogatory responses 14.9K-17.8K characters for 22
#: requests; production 18.4K-20.6K for 11-19 requests; form 29K-44K for 39-62
#: numbers. The largest ratio of each is kept. Admissions: GUESS, special's.
OUT_CHARS_PER_REQUEST: dict[str, int] = {
    "special_interrogatories": 810,  # 17.8K / 22
    "requests_for_production": 1_875,  # 20.6K / 11
    "form_interrogatories": 1_130,  # 44K / 39
    "requests_for_admission": 810,
}
#: Requests assumed when the plan carries no count: MEASURED, the largest seen.
DEFAULT_REQUESTS: dict[str, int] = {
    "special_interrogatories": 22,
    "requests_for_production": 19,
    "form_interrogatories": 62,
    "requests_for_admission": 22,
}
#: A served set's repair rounds priced. GUESS until the replays count them (the
#: lane allows up to 8, ``finish.REPAIR_CEILING``; most converge in one or two).
REPAIR_ROUNDS = 2
#: Output tokens of an audit, the notes and the review. GUESS (evidence_cost's).
AUDIT_OUT, NOTES_OUT, REVIEW_OUT = 6_000, 8_000, 10_000
#: One inventory row's characters (name, folder, category, pages). GUESS.
INVENTORY_ROW_CHARS = 120
#: Throughput, for the finish time. GUESSES until a replay is timed:
#: scanned pages read a minute at the lane's concurrency of 4; output tokens a
#: minute from one streamed call; input tokens a minute read; the fixed tail
#: (render, format check, filing, read-back) in minutes.
SCAN_PAGES_PER_MINUTE = 30.0
OUT_TOKENS_PER_MINUTE = 2_400.0
IN_TOKENS_PER_MINUTE = 600_000.0
FIXED_MINUTES = 5.0


def _usd(pricing: Any, model: str, tin: float, tout: float) -> float:
    r = pricing.rate_for(model)
    return (tin * r.input_per_million_cents + tout * r.output_per_million_cents) / 1e8


def _minutes(tin: float, tout: float) -> float:
    return tin / IN_TOKENS_PER_MINUTE + tout / OUT_TOKENS_PER_MINUTE


def pack_chars(rows: list[dict[str, Any]], cats: dict[str, str], set_type: str | None, group: str | None = None) -> int:
    """A unit's pack size before any text is transcribed, under its cap."""
    if set_type in scope_mod.INVENTORY_ONLY:
        return (len(cats) + 4) * INVENTORY_ROW_CHARS
    want = scope_mod.text_needs(set_type, group)
    total = sum(
        int(r.get("chars") or 0) + len(scope_mod.scan_pages(r)) * scope_mod.SCAN_PAGE_CHARS + 80
        for r in rows
        if cats.get(str(r.get("id"))) in want
    )
    return min(total, scope_mod.cap_for(set_type, group))


def unit_pack_chars(rows: list[dict[str, Any]], cats: dict[str, str], set_type: str | None) -> int:
    """A form set reads one pack per section group; any other set one pack."""
    if set_type == scope_mod.FORM:
        return sum(pack_chars(rows, cats, set_type, g) for g in scope_mod.FORM_GROUPS)
    return pack_chars(rows, cats, set_type)


def project(
    rows: list[dict[str, Any]],
    cats: dict[str, str],
    plan: list[dict[str, Any]],
    firm_data: dict[str, Any],
    pricing: Any,
    *,
    stored_pages: int = 0,
    request_counts: dict[str, int] | None = None,
    chars_per_token: float = CHARS_PER_TOKEN,
) -> dict[str, Any]:
    """Dollars and minutes for a response job from its scoped corpus."""
    b, models = firm_data["budget"], firm_data["models"]
    review_model = models.get("review") or models["audit"]
    queued = sum(len(scope_mod.scan_pages(r)) for r in rows)
    paid_pages = max(0, queued - max(0, stored_pages))
    trans_usd = paid_pages * float(b["usd_per_scanned_page"])
    units: dict[str, dict[str, Any]] = {}
    all_in = all_out = 0.0
    for p in plan:
        st = str(p.get("variant"))
        n = (request_counts or {}).get(p["key"]) or DEFAULT_REQUESTS.get(st, 22)
        out_chars = n * OUT_CHARS_PER_REQUEST.get(st, 810)
        size = unit_pack_chars(rows, cats, st)
        tin, tout = (size + COMPOSE_FRAME_CHARS) / chars_per_token, out_chars / OUT_CHARS_PER_TOKEN
        audit_in = (size + out_chars) / chars_per_token
        repair_in = REPAIR_ROUNDS * (COMPOSE_FRAME_CHARS + out_chars) / chars_per_token
        usd = (
            _usd(pricing, models["compose"], tin, tout)
            + _usd(pricing, models["audit"], audit_in, AUDIT_OUT)
            + _usd(pricing, models["repair"], repair_in, REPAIR_ROUNDS * tout)
        )
        mins = _minutes(tin, tout) + _minutes(audit_in, AUDIT_OUT) + _minutes(repair_in, REPAIR_ROUNDS * tout)
        units[p["key"]] = {"set_type": st, "pack_chars": size, "requests": n, "usd": round(usd, 2),
                           "minutes": round(mins, 1)}  # fmt: skip
        all_in += size + out_chars
        all_out += tout
    job_in = all_in / chars_per_token
    tail_usd = _usd(pricing, models["audit"], job_in, NOTES_OUT) + _usd(pricing, review_model, job_in, REVIEW_OUT)
    tail_min = _minutes(job_in, NOTES_OUT) + _minutes(job_in, REVIEW_OUT) + FIXED_MINUTES
    trans_min = paid_pages / SCAN_PAGES_PER_MINUTE
    usd = trans_usd + sum(u["usd"] for u in units.values()) + tail_usd
    minutes = trans_min + sum(u["minutes"] for u in units.values()) + tail_min
    cap = float(b.get("per_job_cap_usd") or 0)
    return {
        "pages": sum(int(r.get("pages") or 0) for r in rows),
        "characters": sum(int(r.get("chars") or 0) for r in rows),
        "transcription_pages": paid_pages,
        "scan_pages_queued": queued,
        "scan_pages_stored": min(queued, max(0, stored_pages)),
        "usd": round(usd, 2),
        "parts": {
            "transcription": round(trans_usd, 2),
            "units": round(sum(u["usd"] for u in units.values()), 2),
            "notes_and_review": round(tail_usd, 2),
        },
        "minutes": {
            "transcription": round(trans_min, 1),
            "units": round(sum(u["minutes"] for u in units.values()), 1),
            "notes_review_filing": round(tail_min, 1),
            "total": round(minutes, 1),
        },
        "units": units,
        "per_job_cap_usd": cap,
        "over_cap": bool(cap) and usd > cap,
    }


def stored_pages(rows: list[dict[str, Any]], model: str, log: Callable[[str], None] = print) -> int:
    """Queued scan pages the seat's page-text store already holds, read the
    way the lane's transcription reads them (``demand/transcribe._Store``).
    Read-only: nothing is touched, claimed or paid. 0 when the store is off."""
    from ..matter import paid as matter_paid, policy as matter_policy, store as page_store_mod

    store = page_store_mod.from_env("drafting", read_only=True, log=log)
    if not store.on:
        return 0
    method = matter_paid.method(matter_policy.DRAFTING, model).version
    hits = 0
    for r in rows:
        pages = scope_mod.scan_pages(r)
        if not pages or not r.get("source_path"):
            continue
        try:
            sha = matter_paid.sha_file(Path(r["source_path"]))
        except OSError:
            continue
        hits += sum(1 for n in pages if store.get(sha, n, method) is not None)
    return hits


def request_counts(data: Path, plan: list[dict[str, Any]]) -> dict[str, int]:
    """Unit key -> its served set's request count, from the frozen served plan."""
    from .served import PLAN

    p = data / PLAN
    if not p.is_file():
        return {}
    sets = json.loads(p.read_text(encoding="utf-8"))["sets"]
    return {u["key"]: int(sets[u["served"]]["request_count"]) for u in plan if isinstance(u.get("served"), int)}


def expected_finish(minutes: float, now: float | None = None) -> dict[str, Any]:
    """When the job expects to finish, ISO-8601 UTC, written once at the estimate."""
    t = time.time() if now is None else now
    return {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t + minutes * 60)),
        "computed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)),
        "minutes": round(minutes, 1),
        "basis": "response_cost.project",
    }


def cheapest_first(plan: list[dict[str, Any]], est: dict[str, Any]) -> list[dict[str, Any]]:
    """The plan with its units in order of projected cost (stable on ties)."""
    cost = {k: float(u["usd"]) for k, u in (est.get("units") or {}).items()}
    return sorted(plan, key=lambda p: cost.get(p["key"], 0.0))


def scope_and_estimate(
    data: Path,
    firm_data: dict[str, Any],
    pricing: Any,
    plan: list[dict[str, Any]],
    facts: dict[str, Any],
    model: str,
    log: Callable[[str], None] = print,
    now: float | None = None,
) -> dict[str, Any]:
    """The scope step and the estimate over it, written to ``FILE``: what
    ``run.py``'s scope stage calls. Returns the estimate with its finish time."""
    providers = scope_mod.providers_of(facts)
    rec = scope_mod.scope(data, firm_data, plan, providers)
    rows = scope_mod.corpus_rows(data)
    est = project(
        rows,
        scope_mod.categories(data),
        plan,
        firm_data,
        pricing,
        stored_pages=stored_pages(rows, model, log),
        request_counts=request_counts(data, plan),
    )
    est["scope"] = {k: rec[k] for k in ("unread", "scan_pages_unread_files", "scan_pages_untranscribed")}
    est["expected_finish"] = expected_finish(est["minutes"]["total"], now)
    (data / FILE).write_text(json.dumps(est, indent=1), encoding="utf-8")
    log(
        f"scope: {rec['unread']} file(s) not read, {rec['scan_pages_untranscribed']} scanned page(s) left "
        f"untranscribed; estimate {est['usd']:.2f} USD ({est['parts']['transcription']:.2f} transcription, "
        f"{est['transcription_pages']} pages), finish by {est['expected_finish']['at']}"
    )
    return est
