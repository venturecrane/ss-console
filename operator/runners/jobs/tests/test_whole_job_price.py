"""The whole job's price at the first paid check (2026-11-20).

WHY. Before this, the one check every unit asks before its first paid stage
(``driver._check_limits`` -> ``limits.check_before_first_paid``) compared the
firm's cost cap and the cycle's cost budget with ONE stage's projection. A
stopped update of 2,918 new pages passed it and then spent past what any
figure the firm set would allow. The check now prices the WHOLE job: the
pages it was asked about times the worst rate measured for its kind (a full
chronology 0.065 a page, an update 0.09, each its worst measured bill over the
pages the price counts; ``job_price``), against the same two settings.

Every value here is invented. No client text.

What each test pins:

* an update priced past the job cap holds before anything is spent;
* a run priced past the cycle line holds on the cycle line, with what other
  jobs already spent this cycle counted (the second of two jobs);
* a run priced under both lines runs exactly as before (500 pages, and a job
  priced about 2);
* SMD's dollar approval lifts the price check and the cycle line for that job
  and is its hard ceiling: the run stops when its spend reaches it;
* a resumed unit is priced on what remains of its own price, not twice;
* a joint matter's second unit is priced for its own chronology, once.

The sequence is stubbed to two paid stages (vision, slug scope; map, unit
scope), as in ``test_allowance_cumulative.py``, through the real Driver,
Limits, Budget and approval file.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest
import yaml

from jobs import limits as limits_mod
from jobs.medchron import allowance, dag, driver as driver_mod, job as job_mod, pages_record
from jobs.proof import holds
from jobs_testkit import FIRM_CONFIG, FakeSeat, job_yaml

FIGURE = re.compile(r"\$|USD|\d+\.\d\d")
#: 45,000,000 input tokens at the test price list's 200 cents a million: 90.00.
NINETY_DOLLARS = {"model": "claude-sonnet-5", "in": 45_000_000, "out": 0, "stage": "map"}


class _NoNetwork:
    def __getattr__(self, name):  # pragma: no cover - a stage that reached the model is the defect
        raise AssertionError(f"a test stage reached the model ({name})")


def _firm(tmp_path: Path, *, cap: float, budget: float) -> Path:
    doc = copy.deepcopy(FIRM_CONFIG)
    doc["budget"]["per_job_cap_usd"] = cap
    doc["budget"]["monthly_budget_usd"] = budget
    p = tmp_path / f"firm-{cap}-{budget}.yaml"
    p.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return p


def _extracted(data_root: Path, pages: int) -> None:
    (data_root / "example-matter" / "extracted.jsonl").write_text(
        json.dumps({"id": "f1", "name": "f1", "pages": pages, "chars": 0}) + "\n", encoding="utf-8"
    )


def _asked(data_root: Path, pages: int, unit: str = "alpha") -> None:
    """An update's page record with ``pages`` rows sent to be read."""
    run_dir = data_root / "example-matter" / "runs" / unit
    run_dir.mkdir(parents=True, exist_ok=True)
    rows = (
        pages_record.row("f1", "a" * 64, i + 1, pages_record.READ, "record", pages_record.NONE, "code")
        for i in range(pages)
    )
    pages_record.path(run_dir).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _job(
    tmp_path: Path, data_root: Path, *, name: str, month_cents: int = 0, joint: bool = False, update: bool = False
) -> Path:
    jd = tmp_path / name
    jd.mkdir()
    body = yaml.safe_load(job_yaml(data_root, joint=joint))
    body.update(allowance_remaining_pages=100_000, month_cents_used=month_cents, allowance_month="2026-11")
    if update:
        body["update"] = {"into": job_mod.UPDATE_INTO}
        body["selection"] = {"include_file_ids": ["f1"]}
    (jd / "job.yaml").write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    return jd


def _spend(data_root: Path, unit: str, row: dict) -> None:
    p = data_root / "example-matter" / "runs" / unit / job_mod.USAGE_LEDGER
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def _driver(jd: Path, firm: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch) -> driver_mod.Driver:
    seq = [dag.BY_NAME["vision"], dag.BY_NAME["map"]]
    monkeypatch.setattr(driver_mod.dag, "stages_from", lambda *_a, **_k: list(seq))
    job_mod.load(jd)
    d = driver_mod.Driver(
        jd, firm_config=str(firm), pricing=str(pricing_path), log=lambda *_: None, seat_factory=FakeSeat,
        client=_NoNetwork(),
    )  # fmt: skip
    d._runner_override = {"vision": lambda _sr: 0, "map": lambda _sr: 0}
    return d


# ---- 1. an update priced past the job cap holds, $0 spent ---------------------
def test_an_update_priced_past_the_cap_holds_before_anything_is_spent(tmp_path, data_root, pricing_path, monkeypatch):
    """2,900 new pages at 0.09 is 261.00 against a 100 cap. FALSIFIER (main):
    the check priced vision alone, under the cap, and the update ran."""
    _extracted(data_root, 2900)
    _asked(data_root, 2900)
    jd = _job(tmp_path, data_root, name="upd", update=True)
    d = _driver(jd, _firm(tmp_path, cap=100.0, budget=800.0), pricing_path, monkeypatch)
    ran: list[str] = []
    d._runner_override = {"vision": lambda _sr: ran.append("vision") or 0, "map": lambda _sr: ran.append("map") or 0}
    (out,) = d.run()
    assert out.outcome == "held", out.sentence()
    assert ran == [] and out.stage == "vision"  # nothing paid ran
    assert out.dollars == 0.0
    assert out.reason.startswith(f"{limits_mod.CAP_SETTING}: ")
    assert not FIGURE.search(out.reason)  # the hold sentence carries no dollar figure
    assert holds.classify("medchron", out.reason) == (holds.SMD_FINISHING, "limit_held")
    # The price rides to SMD's alert as integer cents, beside the reason.
    assert out.price_cents == 26100


# ---- 4 and 5. the cycle line, with what this cycle already spent -------------
def test_the_second_of_two_jobs_holds_on_the_cycle_line(tmp_path, data_root, pricing_path, monkeypatch):
    """The first job recorded 500.00 this cycle; this one prices 2,000 full
    pages at 0.065 = 130.00, under its own 150 cap, but 500 + 130 passes the
    560 cycle line (each alone is under it). FALSIFIER (main): vision's own
    projection fitted the line and the job ran."""
    _extracted(data_root, 2000)
    jd = _job(tmp_path, data_root, name="second", month_cents=50_000)
    (out,) = _driver(jd, _firm(tmp_path, cap=150.0, budget=560.0), pricing_path, monkeypatch).run()
    assert out.outcome == "held", out.sentence()
    assert out.reason.startswith(f"{limits_mod.BUDGET_SETTING}: ")
    assert not FIGURE.search(out.reason)
    assert out.dollars == 0.0 and out.price_cents == 13000


def test_work_smd_stopped_before_is_priced_like_any_other(tmp_path, data_root, pricing_path, monkeypatch):
    """A fresh request for the same matter after an SMD stop is a new job:
    priced, and held if above the figure. No twin refusal decides it."""
    _extracted(data_root, 2900)
    _asked(data_root, 2900)
    jd = _job(tmp_path, data_root, name="again", update=True)
    (out,) = _driver(jd, _firm(tmp_path, cap=100.0, budget=800.0), pricing_path, monkeypatch).run()
    assert out.outcome == "held" and out.reason.startswith(f"{limits_mod.CAP_SETTING}: ")


# ---- 6 and 7. under the figure, exactly as today -----------------------------
@pytest.mark.parametrize("pages", [30, 500])
def test_a_job_priced_under_both_lines_runs_as_before(tmp_path, data_root, pricing_path, monkeypatch, pages):
    """30 pages prices 1.95, 500 prices 32.50: neither holds, both deliver.
    Passes on main too, by design: what works today still works."""
    _extracted(data_root, pages)
    jd = _job(tmp_path, data_root, name=f"ok{pages}")
    outs = _driver(jd, _firm(tmp_path, cap=150.0, budget=800.0), pricing_path, monkeypatch).run()
    assert [o.outcome for o in outs] == ["delivered"], [o.sentence() for o in outs]


# ---- 2. SMD's dollar approval: runs, and stops at its ceiling ----------------
def test_an_approved_price_runs_past_the_cap_and_the_cycle_line(tmp_path, data_root, pricing_path, monkeypatch):
    """The held update (261.00 against a 100 cap, 700 of 800 spent this
    cycle) with SMD's approval of 200: the price check does not hold it again,
    and spend under the approval passes both the cap and the cycle line.
    FALSIFIER (main): ``write_approval`` takes no dollars."""
    _extracted(data_root, 2900)
    _asked(data_root, 2900)
    jd = _job(tmp_path, data_root, name="approved", update=True, month_cents=70_000)
    allowance.write_approval(jd, usd=200.0, by="smd", attempt=1)
    assert allowance.approved_usd(jd) == 200.0
    d = _driver(jd, _firm(tmp_path, cap=100.0, budget=800.0), pricing_path, monkeypatch)
    outs = d.run()
    assert [o.outcome for o in outs] == ["delivered"], [o.sentence() for o in outs]
    d.limits.check_each_call(199.99, "map")  # 700 + 199.99 is past 800: the approval widened the line


def test_an_approved_job_stops_when_its_spend_reaches_the_approval(tmp_path, data_root, pricing_path, monkeypatch):
    """The approval is the hard ceiling: when the job's spend reaches 200 the
    doorway's per-call check stops it there, HELD on the job cap (the existing
    mid-run behaviour, ``Driver._execute_in_process``), never failed."""
    _extracted(data_root, 2900)
    _asked(data_root, 2900)
    jd = _job(tmp_path, data_root, name="ceiling", update=True, month_cents=70_000)
    allowance.write_approval(jd, usd=200.0, by="smd", attempt=1)
    d = _driver(jd, _firm(tmp_path, cap=100.0, budget=800.0), pricing_path, monkeypatch)

    def map_spends_to_the_ceiling(_sr) -> int:
        for _ in range(2):  # 90.00 + 90.00, then 20.00 more
            _spend(data_root, "alpha", NINETY_DOLLARS)
        _spend(data_root, "alpha", {**NINETY_DOLLARS, "in": 10_000_000})
        d._before_request("map")
        return 0

    d._runner_override["map"] = map_spends_to_the_ceiling
    (out,) = d.run()
    assert out.outcome == "held" and out.stage == "map", out.sentence()
    assert out.reason.startswith(f"{limits_mod.CAP_SETTING}: the run's spend reached")
    assert round(out.dollars, 2) == 200.0


# ---- 8. a resumed unit is priced on what remains ------------------------------
def test_a_resumed_unit_is_priced_on_what_remains_of_its_price(tmp_path, data_root, pricing_path, monkeypatch):
    """Stopped after spending 90.00 of a 130.00 price (2,000 full pages), cap
    150: the resume adds what remains (40.00), not the whole price again
    (90 + 130 would hold). Passes on main; it fails an implementation that
    adds the whole price to the spend already made."""
    _extracted(data_root, 2000)
    _spend(data_root, "alpha", NINETY_DOLLARS)
    jd = _job(tmp_path, data_root, name="resumed")
    outs = _driver(jd, _firm(tmp_path, cap=150.0, budget=800.0), pricing_path, monkeypatch).run()
    assert [o.outcome for o in outs] == ["delivered"], [o.sentence() for o in outs]


# ---- 9. a joint matter's second unit, priced once for its own chronology ------
def test_a_joint_matters_second_unit_is_priced_for_its_own_chronology(tmp_path, data_root, pricing_path, monkeypatch):
    """1,000 shared pages, 65.00 a chronology, cap 120. The first unit spends
    90.00; the second unit's check adds its own 65.00 to the job's 90.00 and
    holds before its first paid stage. FALSIFIER (main): map has no measured
    projection, so the second unit's check saw 90 against 120 and ran."""
    _extracted(data_root, 1000)
    jd = _job(tmp_path, data_root, name="joint", joint=True)
    d = _driver(jd, _firm(tmp_path, cap=120.0, budget=800.0), pricing_path, monkeypatch)
    seen: list[int] = []

    def map_run(_sr) -> int:
        seen.append(1)
        if len(seen) == 1:
            _spend(data_root, "alpha", NINETY_DOLLARS)
        return 0

    d._runner_override["map"] = map_run
    outs = d.run()
    assert [o.outcome for o in outs] == ["delivered", "held"], [o.sentence() for o in outs]
    assert outs[1].stage == "map" and outs[1].reason.startswith(f"{limits_mod.CAP_SETTING}: ")
    assert len(seen) == 1  # the second unit's map never ran
    assert outs[1].price_cents == 6500  # one chronology's price, not the job's two


def test_a_joint_matter_under_the_cap_delivers_both(tmp_path, data_root, pricing_path, monkeypatch):
    """The same matter with cap 160: 90 + 65 fits, both deliver (passes on main)."""
    _extracted(data_root, 1000)
    jd = _job(tmp_path, data_root, name="joint-ok", joint=True)
    d = _driver(jd, _firm(tmp_path, cap=160.0, budget=800.0), pricing_path, monkeypatch)
    fired = {"n": 0}

    def map_run(_sr) -> int:
        fired["n"] += 1
        if fired["n"] == 1:
            _spend(data_root, "alpha", NINETY_DOLLARS)
        return 0

    d._runner_override["map"] = map_run
    assert [o.outcome for o in d.run()] == ["delivered", "delivered"]


# ---- the approval reaches every place the limits are built --------------------
def test_the_audit_verify_check_honours_the_approved_ceiling(tmp_path, data_root, pricing_path, monkeypatch):
    """The entry audit's verify phase builds its own limits
    (``stages/entry_audit_io.check_limits``). With SMD's 200 approval a 150
    projection passes the firm's 100 cap there too; without it, it holds.
    FALSIFIER: that check read the firm's cap alone and held an approved job."""
    from jobs.stages import entry_audit_io

    monkeypatch.setenv("JOBS_PRICING_JSON", str(pricing_path))
    _extracted(data_root, 30)
    firm = _firm(tmp_path, cap=100.0, budget=800.0)
    plain = _driver(_job(tmp_path, data_root, name="plain"), firm, pricing_path, monkeypatch)
    with pytest.raises(limits_mod.LimitHold, match=f"^{limits_mod.CAP_SETTING}: "):
        entry_audit_io.check_limits(plain._stage_run(plain.job.units[0], lambda _m: None), 150.0)
    jd = _job(tmp_path, data_root, name="approved-audit")
    allowance.write_approval(jd, usd=200.0, by="smd", attempt=1)
    d = _driver(jd, firm, pricing_path, monkeypatch)
    entry_audit_io.check_limits(d._stage_run(d.job.units[0], lambda _m: None), 150.0)


# ---- SMD approves exactly what the alert prints: no arithmetic ----------------
LONG_JOB, LONG_MATTER = "0" * 26, "2026-PI-1234567890-ABCDEFGHIJ"


def _printed_usd(outs) -> float:
    """The ``--usd`` figure SMD's alert prints for this job's outcomes."""
    fig = allowance.cost_figures([o.to_dict() for o in outs])
    detail = allowance.cost_alert_detail(LONG_JOB, LONG_MATTER, limits_mod.CAP_SETTING, fig)
    m = re.search(r"--usd (\d+) ", detail)
    assert m and len(detail) <= 300 and "$" not in detail, detail
    return float(m.group(1))


def _map_spends(data_root: Path, *units_rows: tuple[str, dict]):
    """A map stage that records one ledger row per call, in order."""
    queue = list(units_rows)

    def run(_sr) -> int:
        if queue:
            unit, row = queue.pop(0)
            _spend(data_root, unit, row)
        return 0

    return run


def test_a_joint_matter_held_on_both_units_approved_at_the_printed_figure_delivers_both(
    tmp_path, data_root, pricing_path, monkeypatch
):
    """The reviewer's case: 1,000 pages, cap 50, both units hold at 65.00 each.
    The alert prints the WHOLE job's figure (131), and approving exactly that
    delivers both. FALSIFIER: one unit's figure (65) delivers the first and
    stops the second on the cap."""
    _extracted(data_root, 1000)
    jd = _job(tmp_path, data_root, name="joint-both", joint=True)
    firm = _firm(tmp_path, cap=50.0, budget=800.0)
    outs = _driver(jd, firm, pricing_path, monkeypatch).run()
    assert [o.outcome for o in outs] == ["held", "held"] and [o.price_cents for o in outs] == [6500, 6500]
    usd = _printed_usd(outs)
    assert usd == 131.0
    allowance.write_approval(jd, usd=usd, by="smd", attempt=2)
    sixty = {**NINETY_DOLLARS, "in": 30_000_000}
    again = _driver(jd, firm, pricing_path, monkeypatch)
    again._runner_override["map"] = _map_spends(data_root, ("alpha", sixty), ("beta", sixty))
    assert [o.outcome for o in again.run()] == ["delivered", "delivered"]


def test_a_joint_matters_second_unit_approved_at_the_printed_figure_delivers(
    tmp_path, data_root, pricing_path, monkeypatch
):
    """The second unit holds with the job at 90.00 spent and a 65.00 price;
    the alert prints 156, and approving exactly that delivers it. FALSIFIER:
    an alert that printed the price alone (65) under the job's 90 spent."""
    _extracted(data_root, 1000)
    jd = _job(tmp_path, data_root, name="joint-approve", joint=True)
    firm = _firm(tmp_path, cap=120.0, budget=800.0)
    d = _driver(jd, firm, pricing_path, monkeypatch)
    d._runner_override["map"] = _map_spends(data_root, ("alpha", NINETY_DOLLARS))
    outs = d.run()
    assert [o.outcome for o in outs] == ["delivered", "held"]
    usd = _printed_usd(outs)
    assert usd == 156.0
    allowance.write_approval(jd, usd=usd, by="smd", attempt=2)
    assert [o.outcome for o in _driver(jd, firm, pricing_path, monkeypatch).run()] == ["delivered", "delivered"]


def test_a_resumed_job_approved_at_the_printed_figure_delivers(tmp_path, data_root, pricing_path, monkeypatch):
    """A resumed unit at 90.00 spent of a 130.00 price against an 80 cap holds
    on what remains (40.00); the alert prints ceil(spent + remaining) = 131,
    never spent + the whole price (221), and that delivers it."""
    _extracted(data_root, 2000)
    _spend(data_root, "alpha", NINETY_DOLLARS)
    jd = _job(tmp_path, data_root, name="resumed-approve")
    firm = _firm(tmp_path, cap=80.0, budget=800.0)
    outs = _driver(jd, firm, pricing_path, monkeypatch).run()
    assert outs[0].outcome == "held" and outs[0].price_cents == 4000
    usd = _printed_usd(outs)
    assert usd == 131.0 and usd != 221.0
    allowance.write_approval(jd, usd=usd, by="smd", attempt=2)
    assert [o.outcome for o in _driver(jd, firm, pricing_path, monkeypatch).run()] == ["delivered"]


def test_a_stage_projection_above_the_price_is_the_printed_figure(tmp_path, data_root, pricing_path, monkeypatch):
    """The reviewer's strand: vision projects 80.00 on 1,000 pages priced
    65.00. The hold carries 80 (what it compared), the alert prints 81, and
    approving 81 runs the job. FALSIFIER: printing the price (65), which the
    next run's stage check holds again."""
    _extracted(data_root, 1000)
    jd = _job(tmp_path, data_root, name="strand")
    firm = _firm(tmp_path, cap=50.0, budget=800.0)
    monkeypatch.setattr(driver_mod.projection, "read_usd", lambda *_a, **_k: 80.0)
    outs = _driver(jd, firm, pricing_path, monkeypatch).run()
    assert outs[0].outcome == "held" and outs[0].price_cents == 8000
    usd = _printed_usd(outs)
    assert usd == 81.0
    allowance.write_approval(jd, usd=usd, by="smd", attempt=2)
    assert [o.outcome for o in _driver(jd, firm, pricing_path, monkeypatch).run()] == ["delivered"]


def test_a_cap_stop_mid_run_approved_higher_resumes_and_delivers(tmp_path, data_root, pricing_path, monkeypatch):
    """Spend reaches the approved 200 during map: held on the cap, carrying
    what remains of the price. A higher approval (newest wins) resumes it to
    delivery. A cap is a brake SMD lifts, not a dead end."""
    _extracted(data_root, 2900)
    _asked(data_root, 2900)
    jd = _job(tmp_path, data_root, name="lift", update=True)
    firm = _firm(tmp_path, cap=100.0, budget=800.0)
    allowance.write_approval(jd, usd=200.0, by="smd", attempt=1)
    d = _driver(jd, firm, pricing_path, monkeypatch)

    def to_the_ceiling(_sr) -> int:
        for _ in range(2):
            _spend(data_root, "alpha", NINETY_DOLLARS)
        _spend(data_root, "alpha", {**NINETY_DOLLARS, "in": 10_000_000})
        d._before_request("map")
        return 0

    d._runner_override["map"] = to_the_ceiling
    outs = d.run()
    assert outs[0].outcome == "held" and outs[0].reason.startswith(f"{limits_mod.CAP_SETTING}: the run's spend")
    assert outs[0].price_cents == 6100  # 261.00 less the unit's 200.00
    usd = _printed_usd(outs)
    assert usd == 262.0
    allowance.write_approval(jd, usd=usd, by="smd", attempt=2)
    assert allowance.approved_usd(jd) == 262.0
    again = _driver(jd, firm, pricing_path, monkeypatch)
    again._runner_override["map"] = lambda _sr: again._before_request("map") or 0
    assert [o.outcome for o in again.run()] == ["delivered"]


def test_the_newest_approval_replaces_the_last_even_when_lower(tmp_path):
    jd = tmp_path / "j"
    jd.mkdir()
    allowance.write_approval(jd, pages=10, by="smd", attempt=1)
    assert allowance.approved_usd(jd) is None and allowance.approved_pages(jd) == 10  # pages-only, as before
    allowance.write_approval(jd, usd=300.0, by="smd", attempt=2)
    allowance.write_approval(jd, usd=250.0, by="smd", attempt=3)
    assert allowance.approved_usd(jd) == 250.0 and allowance.approved_pages(jd) == 10
    (jd / allowance.APPROVAL_FILE).write_text('{"approvals": [{"usd": "inf"}, {"usd": 1e999}, 7]}')
    assert allowance.approved_usd(jd) is None  # old or odd state is no approval, never a crash


def test_an_approved_job_is_not_stopped_by_the_cycle_line(tmp_path, data_root, pricing_path, monkeypatch):
    """The cycle is already past its line (900 of 800); the approved job's
    calls are checked against its own 200 ceiling alone."""
    _extracted(data_root, 30)
    jd = _job(tmp_path, data_root, name="cycle-off", month_cents=90_000)
    allowance.write_approval(jd, usd=200.0, by="smd", attempt=1)
    d = _driver(jd, _firm(tmp_path, cap=100.0, budget=800.0), pricing_path, monkeypatch)
    assert [o.outcome for o in d.run()] == ["delivered"]
    d.limits.check_each_call(199.0, "map")
    with pytest.raises(limits_mod.LimitHold, match=f"^{limits_mod.CAP_SETTING}: "):
        d.limits.check_each_call(200.0, "map")


def test_an_unreadable_page_record_holds_unpriced(tmp_path, data_root, pricing_path, monkeypatch):
    """No extract record: the price cannot be checked, so the job holds on the
    cap with a fixed reason and $0 spent, and the alert asks SMD for a figure.
    FALSIFIER (before): 0 pages priced 0.00 and the job ran unpriced."""
    jd = _job(tmp_path, data_root, name="unread")
    (out,) = _driver(jd, _firm(tmp_path, cap=150.0, budget=800.0), pricing_path, monkeypatch).run()
    assert out.outcome == "held" and "page record could not be read" in out.reason
    assert out.reason.startswith(f"{limits_mod.CAP_SETTING}: ") and out.dollars == 0.0
    detail = allowance.cost_alert_detail(
        LONG_JOB, LONG_MATTER, limits_mod.CAP_SETTING, allowance.cost_figures([out.to_dict()])
    )
    assert "--usd N |" in detail and "--usd 0" not in detail and "choose --usd above spent" in detail


def test_a_stage_projection_hold_prints_a_figure_from_that_projection(tmp_path, data_root, pricing_path, monkeypatch):
    """An update whose asked figure prices at most 4.17 held on vision's own
    projection (10.00 against a 2.00 cap): the hold carries the projection it
    compared and the figure comes from it, never 0."""
    _extracted(data_root, 30)
    _asked(data_root, 0)
    jd = _job(tmp_path, data_root, name="zero", update=True)
    monkeypatch.setattr(driver_mod.projection, "read_usd", lambda *_a, **_k: 10.0)
    outs = _driver(jd, _firm(tmp_path, cap=2.0, budget=800.0), pricing_path, monkeypatch).run()
    assert outs[0].outcome == "held" and outs[0].price_cents == 1000
    assert _printed_usd(outs) == 11.0
