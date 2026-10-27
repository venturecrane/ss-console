"""shared.selftest_check: the in-image self-test's verdict on the heartbeat.

The file under test is written by operator/templates/selftest.sh (root, at boot
and hourly). These tests pin what the gate ships from it, and above all that
SILENCE IS NOT HEALTH: a stale file, a missing file on a Machine up longer than
the staleness bound, and an unreadable file each ship a synthetic failed id.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from shared import heartbeat as hb
from shared import selftest_check as sc
from tests.test_heartbeat import _emitter

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc).timestamp()


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _results(**over: object) -> dict:
    doc = {
        "schema_version": 1,
        "version": "1" * 40,
        "image_ref": "registry.fly.io/hermes-fleet:rel-11111111-202610230000",
        "hermes_sha": "3" * 40,
        "run_kind": "hourly",
        "started_at": _iso(NOW - 120),
        "finished_at": _iso(NOW - 60),
        "checks": [
            {"id": "customer-yaml-present", "ok": True, "warn": False, "detail": ""},
            {
                "id": "retired-skills-absent",
                "ok": False,
                "warn": False,
                "detail": "retired skill still on volume: x",
            },
            {"id": "medchron-daemon-idle", "ok": False, "warn": True, "detail": ""},
        ],
        "failed": 1,
        "warned": 1,
        "total": 3,
        "failed_streak": 2,
    }
    doc.update(over)
    return doc


def _read(path: Path, uptime: float | None = 60.0) -> dict | None:
    return sc.read_selftest(path, now_fn=lambda: NOW, uptime_fn=lambda: uptime)


def _put(tmp_path: Path, doc: object) -> Path:
    path = tmp_path / "results.json"
    path.write_text(json.dumps(doc))
    return path


def test_a_fresh_file_ships_ids_identity_and_counts(tmp_path: Path) -> None:
    got = _read(_put(tmp_path, _results()))
    assert got == {
        "v": 1,
        "run_kind": "hourly",
        "finished_at": _iso(NOW - 60),
        "version": "1" * 40,
        "image_ref": "registry.fly.io/hermes-fleet:rel-11111111-202610230000",
        "hermes_sha": "3" * 40,
        "total": 3,
        "failed_count": 1,
        "warned_count": 1,
        "failed_streak": 2,
        "failed": ["retired-skills-absent"],
        "warned": ["medchron-daemon-idle"],
    }
    # No detail travels: the ids are the verdict, the detail stays on the seat.
    assert "retired skill still" not in json.dumps(got)


def test_a_clean_run_reports_zero_and_a_zero_streak(tmp_path: Path) -> None:
    doc = _results(checks=[{"id": "a", "ok": True, "warn": False, "detail": ""}], failed_streak=0)
    got = _read(_put(tmp_path, doc))
    assert (
        got is not None
        and got["failed"] == []
        and got["failed_count"] == 0
        and got["failed_streak"] == 0
    )


def test_a_stale_file_is_a_failure_not_health(tmp_path: Path) -> None:
    # FALSIFIER: a loop that died leaves its last (green) file behind.
    green = _results(checks=[{"id": "a", "ok": True, "warn": False, "detail": ""}])
    green["finished_at"] = _iso(NOW - sc.STALE_SECONDS - 1)
    got = _read(_put(tmp_path, green))
    assert got is not None and got["failed"] == [sc.LOOP_DEAD] and got["run_kind"] == "stale"


@pytest.mark.parametrize(
    ("checks", "want"),
    [
        # The crash record an image before 2026-10-27 wrote: one WARN, failed 0.
        (
            [{"id": "selftest-crashlooping", "ok": False, "warn": True, "detail": "exited 1"}],
            ["selftest-crashlooping"],
        ),
        # The crash record as written now: a failure.
        (
            [{"id": "selftest-crashlooping", "ok": False, "warn": False, "detail": "exited 1"}],
            ["selftest-crashlooping"],
        ),
        # A check list that would not load, from an image that wrote nothing for it.
        ([], ["selftest-no-checks"]),
    ],
)
def test_a_run_in_which_no_seat_check_ran_is_a_failure(
    tmp_path: Path, checks: list, want: list
) -> None:
    # FALSIFIER: drop the hollow-run branch in summarize and a crashed loop's
    # record, rewritten fresh on every restart, ships as a clean run: the
    # loop-dead bound never fires and a release on that image passes.
    got = _read(_put(tmp_path, _results(checks=checks, failed=0, failed_streak=0)))
    assert got is not None
    assert got["failed"] == want and got["failed_count"] == len(want)
    assert got["run_kind"] == "stale" and got["failed_streak"] >= 1 and got["warned"] == []
    # Identity stays, so the release judge fails the seat instead of waiting.
    assert got["image_ref"] == "registry.fly.io/hermes-fleet:rel-11111111-202610230000"


def test_a_real_run_beside_a_runner_record_is_judged_on_its_checks(tmp_path: Path) -> None:
    checks = [
        {"id": "customer-yaml-present", "ok": True, "warn": False, "detail": ""},
        {"id": "selftest-crashlooping", "ok": False, "warn": True, "detail": ""},
    ]
    got = _read(_put(tmp_path, _results(checks=checks, failed_streak=0)))
    assert got is not None and got["run_kind"] == "hourly" and got["failed_count"] == 0


def test_no_file_holds_early_and_fails_once_the_machine_is_older_than_the_bound(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "absent.json"
    assert _read(missing, uptime=600) is None
    assert _read(missing, uptime=None) is None
    late = _read(missing, uptime=sc.STALE_SECONDS + 1)
    assert late is not None and late["failed"] == [sc.LOOP_DEAD]


@pytest.mark.parametrize("text", ["{not json", "[1, 2]"])
def test_an_unreadable_file_is_a_failure(tmp_path: Path, text: str) -> None:
    path = tmp_path / "results.json"
    path.write_text(text)
    got = _read(path)
    assert got is not None and got["failed"] == [sc.UNREADABLE]


def test_a_fifo_is_refused_not_read(tmp_path: Path) -> None:
    path = tmp_path / "results.json"
    os.mkfifo(path)
    got = _read(path)
    assert got is not None and got["failed"] == [sc.UNREADABLE]


def test_ids_are_capped_and_counts_stay_exact(tmp_path: Path) -> None:
    checks = [{"id": f"check-{i}", "ok": False, "warn": False, "detail": ""} for i in range(40)]
    got = _read(_put(tmp_path, _results(checks=checks)))
    assert got is not None and len(got["failed"]) == sc.MAX_IDS and got["failed_count"] == 40


def test_a_malformed_id_is_never_shipped(tmp_path: Path) -> None:
    checks = [{"id": "Bad Id; rm -rf", "ok": False, "warn": False, "detail": ""}]
    got = _read(_put(tmp_path, _results(checks=checks)))
    assert got is not None and got["failed"] == ["selftest-malformed-id"]


def test_build_payload_carries_selftest_only_when_present() -> None:
    assert "selftest" not in hb.build_payload(
        heartbeat_ts="t", last_audit_ts=None, last_skill_ts=None, uptime_seconds=None, version=None
    )
    obj = {"v": 1, "failed": []}
    payload = hb.build_payload(
        heartbeat_ts="t",
        last_audit_ts=None,
        last_skill_ts=None,
        uptime_seconds=None,
        version=None,
        selftest=obj,
    )
    assert payload["selftest"] == obj


def test_the_beat_ships_the_reader_result_and_a_crash_ships_unreadable() -> None:
    em, calls = _emitter(selftest_check_fn=lambda: {"v": 1, "failed": ["x"]})
    em._tick()
    assert json.loads(calls["posts"][0][2])["selftest"] == {"v": 1, "failed": ["x"]}

    def boom() -> dict:
        raise RuntimeError("reader broke")

    em, calls = _emitter(selftest_check_fn=boom)
    em._tick()
    shipped = json.loads(calls["posts"][0][2])["selftest"]
    assert shipped["failed"] == [sc.UNREADABLE]
