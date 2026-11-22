"""A chronology update's cost check sees a real number before every paid
step, and a failed job's reason names what failed (2026-11-22).

Measured before: an update's review was projected at $22.02 where its first
round alone cost $41.00, and at $7.13 where the review cost about $23.8 (the
projection priced pages and left out the delivered entries every part
carries); a stage with no projection was checked as $0.00; any exception in
the review read as "the grader ... gave no verdict"; and a resumed job kept
its first attempt's expected finish. Each test below fails on the code
before the change. Every name, date and value is invented.
"""

from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

import test_decisions_driver as TD
import update_testkit as K
from jobs import limits as limits_mod
from jobs.medchron import config as config_mod, dag, eta, projection
from jobs.medchron.state import RunState
from jobs.medchron.update import review_parts as RP, review_repair as RR
from jobs.paid import load_pricing
from jobs.stages import update_review as UR, update_review_cost as UC

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
    ents = RR.anchor_entries((slug / "runs" / UNIT / "final-chronology.md").read_text(), {3}, UC.headings(cfg))
    base = UC.FN.delivered_of((ud / "base.md").read_text())
    parts = RP.plan(ents, {3}, lambda _x: PAGES, {})
    assert len(parts) == PAGES // RP.WINDOW_PAGES + 1, "every window and the duplicate check"
    image = lambda _x, _p: {"type": "image"}  # noqa: E731
    return sum(RP.request_tokens(RP.review_content(p, "CLIENT", image, ents, base, {}, None)) for p in parts)


def test_the_projection_covers_the_priced_parts_with_their_delivered_entries(
    tmp_path: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    """Fails on the old formula (pages once, doubled, 4,000 output tokens per
    part), which leaves out the entries every part carries."""
    _update(tmp_path)
    cfg, pricing = config_mod.load(str(firm_config_path)), load_pricing(pricing_path)
    rate = pricing.rate_for(UC.review_model(cfg))
    est = _round_one_tokens(tmp_path, cfg)
    priced_parts = est / UC.ESTIMATE_SHARE * rate.input_per_million_cents / 1e8  # round one's real input, low end
    got = UC.projection(tmp_path, UNIT, cfg, pricing, None)
    assert got is not None and got >= 0.9 * priced_parts, (got, priced_parts)
    parts = PAGES // RP.WINDOW_PAGES + 1
    old = (
        PAGES * RP.RV.PAGE_TOKENS * 2 * rate.input_per_million_cents + parts * 4_000 * rate.output_per_million_cents
    ) / 1e8
    assert old < 0.9 * priced_parts, "the instrument: the formula this replaced is under the priced parts"


def test_before_the_documents_exist_the_projection_still_prices_a_full_context_per_part(
    tmp_path: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    _update(tmp_path)
    (tmp_path / "runs" / UNIT / "final-chronology.md").unlink()
    cfg, pricing = config_mod.load(str(firm_config_path)), load_pricing(pricing_path)
    got = UC.projection(tmp_path, UNIT, cfg, pricing, None)
    rate = pricing.rate_for(UC.review_model(cfg))
    parts = PAGES // RP.WINDOW_PAGES + 1
    pages_only = PAGES * RP.RV.PAGE_TOKENS * 2 * rate.input_per_million_cents / 1e8
    context = parts * RP.CONTEXT_MAX * UC.ENTRY_TOKENS * 2 * rate.input_per_million_cents / 1e8
    assert got is not None and got >= (pages_only + context) / UC.ESTIMATE_SHARE * 0.999


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
    asked, notes = _asked(monkeypatch), []
    stage = _update_stage("update_review")
    assert d._projection(stage, ctx, ext) is None, "no new exhibits named yet"
    d._check_limits(stage, ctx, ext, notes)
    assert asked and asked[-1] > 0.0, "never checked as $0.00"
    assert asked[-1] == pytest.approx(UC.bound_usd(PAGES, d.cfg, d.pricing))
    assert any(n.startswith(f"update_review: {projection.UNPROJECTED} (checked at a bound of") for n in notes), notes
    assert any("unprojected" in ln for ln in lines)


def test_a_stage_with_no_bound_proceeds_marked_and_a_hold_on_it_says_so(
    job_dir: Path, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d, ctx, ext, _lines = _driver(job_dir, firm_config_path, pricing_path, data_root, pages=10)
    notes: list[str] = []
    stage = _update_stage("update_summary")
    d._check_limits(stage, ctx, ext, notes)  # proceeds: no job was ever held by a missing projection
    assert notes == [f"update_summary: {projection.UNPROJECTED} (no bound computable; checked against what is spent)"]
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
    assert notes == []


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
    assert reason.startswith("update_review: the stage stopped on an unexpected RuntimeError: request refused")
    assert "Alpha Example" not in reason, "only the first line of the message"
    assert "the grader" not in reason

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
