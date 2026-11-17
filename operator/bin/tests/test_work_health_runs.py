"""How many chronology runs needed nobody (``bin/lib/work_health_runs.py``):
the unaided definition, the 7-day same-matter rule, the stop split, and the
``--runs`` report of ``bin/work-health.py``."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bin.lib import work_health_runs as runs

T0 = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)
A, B, C = "01KTJ0BX0000000000000000AA", "01KTJ0BX0000000000000000BB", "01KTJ0BX0000000000000000CC"


def _row(job: str, state: str, minutes: float, run: dict | None = None, matter: str = "m-1", **meta) -> dict:
    md = {"job_id": job, "state": state.lower(), **meta}
    if run is not None:
        md["run"] = {"v": 1, **run}
    return {
        "id": f"{job}-{minutes}",
        "ts": (T0 + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "action_type": f"MEDCHRON_JOB_{state}",
        "matter_ref": matter,
        "metadata": json.dumps(md),
    }


START = {"event": "start", "by": "code", "release": "unknown"}
END = {"event": "end", "by": "code", "release": "unknown"}


def _clean(job: str = A, at: float = 0, matter: str = "m-1") -> list[dict]:
    return [
        _row(job, "SUBMITTED", at, matter=matter),
        _row(job, "RUNNING", at + 1, START, matter=matter),
        _row(job, "DELIVERED", at + 60, END, matter=matter),
    ]


def test_a_delivered_run_with_only_code_blocks_is_unaided():
    got = runs.summarize(_clean())
    assert (got["runs"], got["unaided"], got["aided"], got["rate"]) == (1, 1, 0, 1.0)


def test_any_step_by_smd_or_the_firm_makes_it_aided():
    for by in ("smd", "firm", "unknown"):
        rows = _clean()[:2] + [_row(A, "DELIVERED", 60, {"event": "hand_delivery", "by": by})]
        got = runs.summarize(rows)
        assert got["unaided"] == 0 and got["aided"] == 1, by


def test_a_restart_after_a_hold_is_aided_even_with_no_block_on_it():
    """The structural clause: a resume whose site forgot its block still counts."""
    rows = [
        _row(A, "SUBMITTED", 0),
        _row(A, "RUNNING", 1, START),
        _row(A, "HELD", 30, {"event": "stop", "by": "code", "stage": "audit", "kind": "check", "code": "audit"}),
        _row(A, "RUNNING", 90),
        _row(A, "DELIVERED", 120, END),
    ]
    run = runs.runs_from_rows(rows)[0]
    assert runs.grade(run, [run])["aided"] == ["restarted"]
    assert runs.summarize(rows)["unaided"] == 0


def test_a_replacement_is_aided_at_both_ends():
    rows = [
        _row(A, "SUBMITTED", 0),
        _row(A, "RUNNING", 1, START),
        _row(A, "FAILED", 20, {"event": "stop", "by": "code", "stage": "download", "kind": "plumbing", "code": "x"}),
        _row(B, "SUBMITTED", 60 * 24 * 9, {"event": "replaces", "by": "firm", "replaces": A}),
        *_clean(B, 60 * 24 * 9 + 1)[1:],
    ]
    rs = runs.runs_from_rows(rows)
    grades = {r.job_id: runs.grade(r, rs) for r in rs}
    assert "replaced" in grades[A]["aided"]
    assert "replaces" in grades[B]["aided"] and "by_firm" in grades[B]["aided"]


def test_the_7_day_rule_counts_an_unlinked_replacement():
    failed = [
        _row(A, "SUBMITTED", 0),
        _row(A, "RUNNING", 1, START),
        _row(A, "FAILED", 9, {"event": "stop", "by": "code"}),
    ]
    within = failed + _clean(B, 60 * 24 * 6)
    outside = failed + _clean(B, 60 * 24 * 8)
    other_matter = failed + _clean(B, 60 * 24 * 6, matter="m-2")
    assert runs.summarize(within)["unaided"] == 0
    assert runs.summarize(outside)["unaided"] == 1
    assert runs.summarize(other_matter)["unaided"] == 1
    # Two deliveries on one matter inside 7 days (a run, then its update) are not a replacement.
    assert runs.summarize(_clean(A) + _clean(B, 60 * 24 * 2))["unaided"] == 2


def test_rows_with_no_block_are_unrecorded_never_unaided():
    rows = [_row(A, "SUBMITTED", 0), _row(A, "RUNNING", 1), _row(A, "DELIVERED", 60)]
    got = runs.summarize(rows)
    assert (got["unrecorded"], got["unaided"], got["rate"]) == (1, 0, None)


def test_an_infrastructure_retry_is_counted_apart_and_aids_nothing():
    rows = _clean()[:2] + [
        _row(A, "RUNNING", 20, {"event": "infra_requeue", "by": "code", "stage": "map", "code": "model_api"}),
        _row(A, "DELIVERED", 60, END),
    ]
    got = runs.summarize(rows)
    assert (got["infra_requeues"], got["unaided"]) == (1, 1)


def test_stops_split_by_check_and_plumbing_and_uncoded_is_counted():
    rows = [
        _row(A, "SUBMITTED", 0),
        _row(A, "HELD", 5, {"event": "stop", "by": "code", "stage": "coverage_gate", "kind": "check", "code": "c"}),
        _row(B, "SUBMITTED", 1, matter="m-2"),
        _row(B, "FAILED", 5, {"event": "stop", "by": "code", "stage": "upload", "kind": "plumbing", "code": "uncoded"}),
        _row(C, "SUBMITTED", 2, matter="m-3"),
    ]
    got = runs.summarize(rows)
    assert (got["stops_check"], got["stops_plumbing"], got["uncoded"]) == (1, 1, 1)
    assert (got["held_now"], got["failed"], got["running"]) == (1, 1, 1)


def test_the_check_can_fail():
    """The definition is not vacuous: a planted aided run moves the rate."""
    clean = runs.summarize(_clean() + _clean(B, 60 * 24 * 10, matter="m-2"))
    planted = _clean() + _clean(B, 60 * 24 * 10, matter="m-2")
    planted[-1] = _row(B, "DELIVERED", 60 * 24 * 10 + 60, {"event": "hand_delivery", "by": "smd"}, matter="m-2")
    assert clean["rate"] == 1.0 and runs.summarize(planted)["rate"] == 0.5


def test_the_report_and_combine_say_counts_only():
    per = {"seat-a": runs.summarize(_clean()), "seat-b": runs.summarize(_clean(B, 0, matter="m-9"))}
    text = runs.report(per, 30)
    assert "seat-a:" in text and "all seats: 2 chronology run(s), 2 unaided" in text
    assert "m-1" not in text and A not in text


def _work_health():
    path = Path(__file__).resolve().parents[1] / "work-health.py"
    spec = importlib.util.spec_from_file_location("work_health_cli_runs", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_work_health_runs_prints_per_seat_and_sends_nothing(tmp_path, capsys, monkeypatch):
    wh_cli = _work_health()
    monkeypatch.setattr(wh_cli, "send", lambda *a, **k: (_ for _ in ()).throw(AssertionError("sent")))
    rows = tmp_path / "rows.json"
    rows.write_text(json.dumps(_clean()), encoding="utf-8")
    assert wh_cli.main(["--runs", "--rows", f"seat-a={rows}", "--runs-days", "14"]) == 0
    out = capsys.readouterr().out
    assert "Chronology runs, last 14 day(s):" in out and "seat-a: 1 chronology run(s), 1 unaided" in out
