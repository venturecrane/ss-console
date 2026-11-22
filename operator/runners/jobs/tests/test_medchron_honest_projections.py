"""A chronology update's cost check sees a real number before every paid
step, and a failed job's reason names what failed (2026-11-22).

Measured before: an update's review was projected at $22.02 where its first
round alone cost $41.00, and at $7.13 where the review cost about $23.8 (the
projection priced pages and left out the delivered entries every part
carries); a stage with no projection was checked as $0.00; any exception in
the review read as "the grader ... gave no verdict"; and a resumed job kept
its first attempt's expected finish. Every name, date and value is invented.

RUN AGAINST THE CODE BEFORE THE CHANGE (12672486097), all 15 fail, not all for
the same kind of reason. Ten fail on what that code DOES: the priced parts
($9.30 projected, under 0.9 of $11.75), the pages with a full context, the
resumed round, the cache rates, the review's bound (asked about 0.0), the
unbounded stage's mark, the logged components, the exception's name, a crash
in a stage whose exit 1 refuses, and the resumed finish. Five fail there only
on a name or argument it does not have, so they prove nothing about it:
a projected stage carries no mark, the dry run, the unreadable document, the
page-allowance hold and the requeue. The last three of those pin behavior
this change could have broken, not a defect it found there.
"""

from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

import test_decisions_driver as TD
import update_testkit as K
from jobs import limits as limits_mod, requeue, run_record
from jobs.medchron import config as config_mod, dag, eta, projection, unit_proof
from jobs.medchron.state import RunState
from jobs.medchron.update import finisher as FN, review_parts as RP, review_repair as RR
from jobs.paid import load_pricing
from jobs.stages import update_review as UR, update_review_cost as UC
from jobs.stages.update_base import headings

UNIT = "alpha"
PAGES = 400
DELIVERED = 600
ADDED = 40
START = date(2025, 1, 1)


# ---- D1: the review is priced from the requests it will send ----------------------
def _pdf(n: int) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    for _ in range(n):
        doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


def _entry(day: date, ex: int, pages: tuple[int, ...], words: int) -> str:
    cites = " ".join(f"(Exhibit {ex} - p. {p})" for p in pages)
    body = " ".join(["Range of motion remained reduced at the follow up visit and exercises continued."] * words)
    return f"{day:%m/%d/%Y}\nSample Clinic | Medical Diagnoses\n\nMedical Diagnoses\n{body} {cites}"


def _update(slug: Path) -> None:
    """An update adding Exhibit 3 (PAGES pages) to a delivered chronology of
    DELIVERED entries, with ADDED entries across its pages, as
    ``update_collect`` leaves it."""
    out, ud = slug / "out" / UNIT, slug / "runs" / UNIT / "update"
    out.mkdir(parents=True, exist_ok=True)
    ud.mkdir(parents=True, exist_ok=True)
    (out / "Exhibit 3 - Sample Clinic - 08-27-2026 (Medical Records).pdf").write_bytes(_pdf(PAGES))
    (ud / "new_exhibits.final.json").write_text(json.dumps([{"exhibit": 3, "id": "n3"}]))
    delivered = tuple(_entry(START + timedelta(days=i), 1, (i + 1,), 30) for i in range(DELIVERED))
    (ud / "base.md").write_text(K.base_md(entries=delivered))
    step = PAGES // ADDED
    added = [_entry(START + timedelta(days=15 * i), 3, (step * i + 1, step * i + 5), 10) for i in range(ADDED)]
    doc = "## Treatment Timeline\n\n## Diagnostic Highlights\n\n## Medical Chronology\n\n" + "\n\n".join(added)
    (slug / "runs" / UNIT / "final-chronology.md").write_text(
        f"Alpha Example - Medical Chronology\n\n{doc}\n\n## Exhibit List\n"
    )


def _round_one_tokens(slug: Path, cfg: config_mod.FirmConfig) -> int:
    """Round one's request estimate, built here from the stage's own planner,
    independently of the projection."""
    ud = slug / "runs" / UNIT / "update"
    ents = RR.anchor_entries((slug / "runs" / UNIT / "final-chronology.md").read_text(), {3}, headings(cfg))
    base = FN.delivered_of((ud / "base.md").read_text())
    parts = RP.plan(ents, {3}, lambda _x: PAGES, {})
    assert len(parts) == PAGES // RP.WINDOW_PAGES + 1, "every window and the duplicate check"
    image = lambda _x, _p: {"type": "image"}  # noqa: E731
    return sum(RP.request_tokens(RP.review_content(p, "CLIENT", image, ents, base, {}, None)) for p in parts)


def test_the_projection_covers_the_priced_parts_with_their_delivered_entries(
    tmp_path: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    """Through the entry point the old code has too (``UC.projection``, same
    arguments), so on origin/main it fails on the DOLLARS, not on a name: the
    old formula prices pages and leaves out the entries every part carries.
    The fixture's delivered entries are long visits (about 2,400 characters);
    with short ones the old formula's doubled pages would cover the parts."""
    _update(tmp_path)
    cfg, pricing = config_mod.load(str(firm_config_path)), load_pricing(pricing_path)
    rate = pricing.rate_for(UC.review_model(cfg))
    real_tokens = _round_one_tokens(tmp_path, cfg) / 0.46  # the measured low end, written out
    priced_parts = real_tokens * rate.input_per_million_cents / 1e8
    got = UC.projection(tmp_path, UNIT, cfg, pricing, None)
    assert got is not None and got >= 0.9 * priced_parts, (got, priced_parts)
    b = UC.projection_of(tmp_path, UNIT, cfg, pricing, None)
    assert b is not None and b.round1.basis == UC.BY_REQUESTS
    assert b.round1_usd >= 0.9 * priced_parts, "round one alone, without the later share's help"
    assert b.usd == pytest.approx(b.round1_usd * (1 + UC.LATER_SHARE))


def test_before_the_documents_exist_the_projection_still_prices_a_full_context_per_part(
    tmp_path: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    _update(tmp_path)
    (tmp_path / "runs" / UNIT / "final-chronology.md").unlink()
    cfg, pricing = config_mod.load(str(firm_config_path)), load_pricing(pricing_path)
    got = UC.projection(tmp_path, UNIT, cfg, pricing, None)
    rate = pricing.rate_for(UC.review_model(cfg))
    parts = PAGES // RP.WINDOW_PAGES + 1
    tokens = PAGES * RP.RV.PAGE_TOKENS + parts * RP.CONTEXT_MAX * 600
    round_one = tokens / 0.46 * rate.input_per_million_cents / 1e8
    assert got is not None and got >= round_one, (got, round_one)
    assert got >= round_one * (1 + UC.LATER_SHARE) * 0.999


def _checkpoint(slug: Path, done: int, rec: dict) -> None:
    from jobs.stages import update_review_io as IO

    ud = slug / "runs" / UNIT / "update"
    row = {"compose_sha": IO.compose_sha(ud), "done": done, "md": "the checkpoint document", "rec": rec}
    (ud / IO.CHECKPOINT).write_text(json.dumps(row))


def test_a_resumed_review_is_not_projected_for_the_round_it_already_paid(
    tmp_path: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    """Round one is in the job's spend once paid: projecting it again held a
    resumed update at the cap for money already spent."""
    _update(tmp_path)
    cfg, pricing = config_mod.load(str(firm_config_path)), load_pricing(pricing_path)
    before = UC.projection(tmp_path, UNIT, cfg, pricing, None)
    _checkpoint(tmp_path, 1, {})
    after = UC.projection(tmp_path, UNIT, cfg, pricing, None)
    assert before is not None and after is not None and after < before, "a paid round one is not projected again"
    (tmp_path / "runs" / UNIT / "update" / "review_checkpoint.json").unlink()
    fresh = UC.projection_of(tmp_path, UNIT, cfg, pricing, None)
    assert fresh is not None and fresh.round1_left
    _checkpoint(tmp_path, 0, {"pending": {"round": 1, "md_sha": UC._sha("the checkpoint document")}})
    paid_review = UC.projection_of(tmp_path, UNIT, cfg, pricing, None)
    assert paid_review is not None and not paid_review.round1_left
    assert paid_review.usd == pytest.approx(fresh.round1_usd * UC.LATER_SHARE)
    _checkpoint(tmp_path, 1, {})
    after_one = UC.projection_of(tmp_path, UNIT, cfg, pricing, None)
    assert after_one is not None and after_one.usd == pytest.approx(fresh.round1_usd * UC.RECHECK_SHARE)
    assert "already paid, not projected" in after_one.line()
    _checkpoint(tmp_path, 0, {"pending": {"round": 1, "md_sha": "another document"}})
    assert UC.projection(tmp_path, UNIT, cfg, pricing, None) == pytest.approx(fresh.usd)
    (tmp_path / "runs" / UNIT / "update" / "base.md").write_text(K.base_md())  # a new collect: a new review
    assert UC.still_to_run(tmp_path / "runs" / UNIT / "update") == (True, UC.LATER_SHARE)


def _one_long_entry(slug: Path) -> None:
    """One added entry citing the first and last page of Exhibit 3: every
    window between carries it (its owner), so every part repeats one
    nearby-entries block, which the stage marks for the cache."""
    _update(slug)
    doc = "## Treatment Timeline\n\n## Diagnostic Highlights\n\n## Medical Chronology\n\n"
    doc += _entry(START + timedelta(days=30), 3, (1, PAGES), 10)
    (slug / "runs" / UNIT / "final-chronology.md").write_text(
        f"Alpha Example - Medical Chronology\n\n{doc}\n\n## Exhibit List\n"
    )


def test_a_repeated_nearby_entries_block_is_priced_at_the_cache_rates(
    tmp_path: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _one_long_entry(tmp_path)
    cfg, pricing = config_mod.load(str(firm_config_path)), load_pricing(pricing_path)
    marked = UC.projection(tmp_path, UNIT, cfg, pricing, None)
    got = getattr(UC, "projection_of", lambda *_a: None)(tmp_path, UNIT, cfg, pricing, None)
    monkeypatch.setattr(RP, "cache_blocks", lambda _c, _r: ("system",))
    plain = UC.projection(tmp_path, UNIT, cfg, pricing, None)
    assert marked is not None and plain is not None and marked < plain, "reads are priced below plain input"
    assert got is not None and got.round1.read > got.round1.written > 0
    assert "read from the cache" in got.line() and f"{got.round1.requests} request(s)" in got.line()
    uncached = UC.projection_of(tmp_path, UNIT, cfg, pricing, None)
    assert uncached is not None and uncached.round1.read == uncached.round1.written == 0


# ---- D2: a paid stage with no projection is marked, never a silent $0.00 ----------
def _driver(job_dir: Path, firm: Path, pricing: Path, data_root: Path, pages: int = 0):
    if pages:
        TD._seed_extracted(data_root, pages=pages)
    d = TD._driver(job_dir, firm, pricing)
    lines: list[str] = []
    d.log = lines.append
    ctx = dag.Ctx(job=d.job, unit=d.job.units[0], date_stamp=d.date_stamp)
    return d, ctx, data_root / "example-matter" / "extracted.jsonl", lines


def _update_stage(name: str) -> dag.Stage:
    return next(s for s in dag.stages(update=True) if s.name == name)


def _asked(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    asked: list[float] = []
    for meth in ("check_before_paid", "check_before_first_paid"):
        real = getattr(limits_mod.Limits, meth)

        def spy(self, *a, _real=real, **kw):
            asked.append(kw["projected_usd"])
            return _real(self, *a, **kw)

        monkeypatch.setattr(limits_mod.Limits, meth, spy)
    return asked


def test_the_review_before_collect_is_checked_at_a_bound_and_marked_unprojected(
    job_dir: Path, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d, ctx, ext, lines = _driver(job_dir, firm_config_path, pricing_path, data_root, pages=PAGES)
    asked = _asked(monkeypatch)
    stage = _update_stage("update_review")
    assert d._projection(stage, ctx, ext) is None, "no new exhibits named yet"
    d._check_limits(stage, ctx, ext)
    assert asked and asked[-1] > 0.0, "not checked as $0.00: a bound can be computed"
    assert asked[-1] == pytest.approx(UC.bound_usd(PAGES, d.cfg, d.pricing))
    assert any(f"update_review: {projection.UNPROJECTED} (checked at a bound of" in ln for ln in lines), lines
    assert _marks(d) == {"unprojected_update_review": 1}


def _marks(d) -> dict[str, int]:
    """The ``checks`` counts the lane puts on the audit row that ends the
    attempt (``run_record.counts`` reads ``<job dir>/data``)."""
    return run_record.counts(Path(d.job.data_root).parent).get("checks", {})


def test_a_stage_with_no_bound_proceeds_marked_and_a_hold_on_it_says_so(
    job_dir: Path, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d, ctx, ext, _lines = _driver(job_dir, firm_config_path, pricing_path, data_root, pages=10)
    asked = _asked(monkeypatch)
    stage = _update_stage("update_summary")
    d._check_limits(stage, ctx, ext)  # proceeds: no job was ever held by a missing projection
    assert set(asked) == {0.0}, "no bound: only what is already spent can hold it, and the mark says so"
    assert _marks(d) == {"unprojected_update_summary": 1}
    monkeypatch.setattr(d.budget, "refresh", lambda: d.limits.cap_usd + 1.0)
    with pytest.raises(limits_mod.LimitHold) as held:
        d._check_limits(stage, ctx, ext, [])
    assert held.value.reason.startswith("per_job_cap_usd: ")
    assert held.value.reason.endswith(f"(update_summary had no projection: {projection.UNPROJECTED})")
    assert "$" not in held.value.reason


def test_a_projected_stage_carries_no_mark(
    job_dir: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    d, ctx, ext, _lines = _driver(job_dir, firm_config_path, pricing_path, data_root, pages=10)
    notes: list[str] = []
    d._check_limits(_update_stage("vision"), ctx, ext, notes)
    assert notes == [] and _marks(d) == {}


def test_a_dry_run_marks_its_notes_and_writes_nothing(
    job_dir: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    d, ctx, ext, _lines = _driver(job_dir, firm_config_path, pricing_path, data_root, pages=10)
    d.dry_run = True
    notes: list[str] = []
    d._check_limits(_update_stage("update_summary"), ctx, ext, notes)
    assert notes == [f"update_summary: {projection.UNPROJECTED} (no bound computable; checked against what is spent)"]
    assert _marks(d) == {}


def test_the_reviews_projection_is_logged_with_its_components(
    job_dir: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    d, ctx, ext, lines = _driver(job_dir, firm_config_path, pricing_path, data_root, pages=PAGES)
    _update(d.slug_dir)
    d._check_limits(_update_stage("update_review"), ctx, ext)
    (line,) = [ln for ln in lines if "update_review projection" in ln]
    assert "round one" in line and "read from the cache" in line and "later rounds 0.65 of round one" in line


def test_a_document_the_planner_cannot_read_leaves_the_review_unprojected_not_the_driver_dead(
    job_dir: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    d, ctx, ext, lines = _driver(job_dir, firm_config_path, pricing_path, data_root, pages=PAGES)
    _update(d.slug_dir)
    (d.slug_dir / "runs" / UNIT / "final-chronology.md").write_text("Alpha Example\n\nno sections here\n")
    notes: list[str] = []
    d._check_limits(_update_stage("update_review"), ctx, ext, notes)  # never raises
    assert any("the projection could not be computed (ValueError)" in ln for ln in lines), lines
    assert notes and notes[0].startswith(f"update_review: {projection.UNPROJECTED} (checked at a bound of")


def test_a_page_allowance_hold_carries_no_projection_suffix(
    tmp_path: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    TD._seed_extracted(data_root, pages=10)
    jd = TD._job(
        tmp_path, data_root, "job-allow", allowance_remaining_pages=5, month_cents_used=0, allowance_month="2026-09"
    )
    d = TD._driver(jd, firm_config_path, pricing_path)
    d.log = lambda _m: None
    ctx = dag.Ctx(job=d.job, unit=d.job.units[0], date_stamp=d.date_stamp)
    with pytest.raises(limits_mod.LimitHold) as held:
        d._check_limits(_update_stage("update_summary"), ctx, data_root / "example-matter" / "extracted.jsonl", [])
    assert held.value.setting not in (limits_mod.CAP_SETTING, limits_mod.BUDGET_SETTING)
    assert "had no projection" not in held.value.reason


# ---- D3: a failed row's reason names what failed ----------------------------------
def _run_review(d, ctx, ext, runner) -> tuple[str, str]:
    st = RunState.load_or_new(d.slug_dir / "runs" / UNIT / "state.json", slug=d.job.slug, unit=UNIT)
    d._runner_override = {"update_review": runner}
    out = d._execute_in_process(_update_stage("update_review"), ctx, st, ext, [])
    assert out is not None
    return out.outcome, out.reason or ""


def test_an_unexpected_exception_is_named_and_the_grader_sentence_kept_for_its_own_exit(
    job_dir: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    d, ctx, ext, _lines = _driver(job_dir, firm_config_path, pricing_path, data_root)

    def refused(_sr) -> int:
        raise RuntimeError("request refused: prompt is too long: 1,234,567 tokens > 1,000,000\nAlpha Example p. 9 text")

    outcome, reason = _run_review(d, ctx, ext, refused)
    assert outcome == "failed"
    assert reason == (
        "update_review: the stage stopped on an unexpected RuntimeError; the trace is in log-update_review.txt"
    ), "the class only: no word of the message, which can quote record text"

    def no_verdict(sr) -> int:  # what ``update_review._loop`` returns on GradeFailed
        sr.log(f"update review: {UR.GradeFailed('no verdict')}; nothing is reverted or kept, a rerun grades again")
        return 1

    outcome, reason = _run_review(d, ctx, ext, no_verdict)
    assert outcome == "failed"
    assert reason.startswith(
        "update_review: the grader of the finisher's sentences gave no verdict; a rerun grades again"
    )


def test_a_crash_in_a_stage_whose_exit_one_is_a_refusal_is_a_failure(
    job_dir: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    d, ctx, ext, _lines = _driver(job_dir, firm_config_path, pricing_path, data_root)
    st = RunState.load_or_new(d.slug_dir / "runs" / UNIT / "state.json", slug=d.job.slug, unit=UNIT)

    def broken(_sr) -> int:
        raise KeyError("n7")

    d._runner_override = {"update_summary": broken}
    out = d._execute_in_process(_update_stage("update_summary"), ctx, st, ext, [])
    assert out is not None and out.outcome == "failed" and "unexpected KeyError" in (out.reason or "")


def _api_error(status: int, kind: str) -> Exception:
    import anthropic
    import httpx

    body = {"type": "error", "error": {"type": kind, "message": "invented"}}
    resp = httpx.Response(status, request=httpx.Request("POST", "https://example.invalid/v1/messages"))
    return anthropic.Anthropic(api_key="invented")._make_status_error(f"Error: {body}", body=body, response=resp)


def _requeue_cause(exc: Exception) -> str | None:
    o = {"outcome": "failed", "reason": unit_proof.crash_reason("update_review", exc)}
    return requeue.cause([o], "failed", o, "update_review")


def test_the_requeue_still_reads_a_transient_class_off_the_reason() -> None:
    """The fallback for a crash the driver did not mark ``transient``. The one
    shape class names cannot carry: an overload sent mid-stream (HTTP 200) is
    a plain ``APIStatusError`` whose message alone said ``overloaded_error``.
    That job now ends failed instead of requeued by this fallback."""
    for status, kind in ((529, "overloaded_error"), (500, "api_error"), (429, "rate_limit_error")):
        assert _requeue_cause(_api_error(status, kind)) == "model_api", (status, kind)
    assert _requeue_cause(_api_error(400, "invalid_request_error")) is None
    assert _requeue_cause(_api_error(200, "overloaded_error")) is None, "known loss: only its message says so"


# ---- D4: a resumed job's expected finish is its own -------------------------------
def test_a_resume_restamps_the_expected_finish(
    job_dir: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    d, _ctx, ext, _lines = _driver(job_dir, firm_config_path, pricing_path, data_root, pages=500)
    st = RunState.load_or_new(d.slug_dir / "runs" / UNIT / "state.json", slug=d.job.slug, unit=UNIT)
    then = time.time() - 26 * 3600
    stale = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(then + 3600)),
        "computed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(then)),
        "basis": eta.BASIS,
        "minutes": {"total": 60.0},
    }
    st.expected_finish = dict(stale)
    st.save()
    projection.expected_finish(d, st, ext)
    fresh = st.expected_finish
    assert fresh is not None and fresh["computed_at"] != stale["computed_at"], "re-stamped on the resume"
    projection.expected_finish(d, st, ext)
    assert st.expected_finish == fresh, "once per attempt"
    reloaded = RunState.load_or_new(d.slug_dir / "runs" / UNIT / "state.json", slug=d.job.slug, unit=UNIT)
    assert reloaded.expected_finish == fresh
