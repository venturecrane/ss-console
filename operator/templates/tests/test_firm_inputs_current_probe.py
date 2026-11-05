"""jobs-firm-inputs-current: the lane's symlink and the applier's state agree.

The probe runs against a fixture seat root (SMD_SEAT_ROOT). Each falsifier the
probe's docstring names is driven here: a re-pointed symlink, a missing
version, a deleted state file, and a versioned lane the state never recorded.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROBE = Path(__file__).resolve().parents[1] / "firm-inputs-current-probe.py"


def _seat(tmp_path: Path, state: dict | None) -> Path:
    cfg = tmp_path / "var/lib/smd-config"
    cfg.mkdir(parents=True)
    (cfg / "records.v1a2b3c4d").mkdir()
    (cfg / "records.v1a2b3c4d/records-firm.yaml").write_text("x")
    os.symlink("records.v1a2b3c4d", cfg / "records")
    (cfg / "medchron-firm.vabcdef01.yaml").write_text("x")
    os.symlink("medchron-firm.vabcdef01.yaml", cfg / "medchron-firm.yaml")
    if state is not None:
        d = tmp_path / "run/smd-firm-inputs"
        d.mkdir(parents=True)
        (d / "state.json").write_text(json.dumps(state))
    return tmp_path


def _run(root: Path) -> tuple[int, str]:
    r = subprocess.run(  # noqa: S603 - the repo's own probe, a fixed argv
        [sys.executable, "-I", str(PROBE)],
        env={"SMD_SEAT_ROOT": str(root), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    return r.returncode, r.stdout


GOOD = {
    "records": {"etag8": "1a2b3c4d", "verdict": "fetched", "applied_at": "2026-11-04T12:00:00Z"},
    "medchron": {"etag8": "abcdef01", "verdict": "fetched", "applied_at": "2026-11-04T12:00:00Z"},
}


def test_agreement_passes(tmp_path: Path) -> None:
    assert _run(_seat(tmp_path, GOOD)) == (0, "2 lane(s) on the version the state records\n")


def test_a_repointed_link_fails(tmp_path: Path) -> None:
    state = {**GOOD, "records": {**GOOD["records"], "etag8": "22222222"}}
    code, out = _run(_seat(tmp_path, state))
    assert code == 1 and "records:" in out and "records.v22222222" in out


def test_a_missing_version_fails(tmp_path: Path) -> None:
    root = _seat(tmp_path, GOOD)
    (root / "var/lib/smd-config/medchron-firm.vabcdef01.yaml").unlink()
    code, out = _run(root)
    assert code == 1 and "medchron" in out


def test_no_state_with_versioned_lanes_fails_and_a_pre_versioning_seat_passes(tmp_path: Path) -> None:
    code, out = _run(_seat(tmp_path / "a", None))
    assert code == 1 and "unreadable" in out
    bare = tmp_path / "b"
    (bare / "var/lib/smd-config").mkdir(parents=True)
    assert _run(bare)[0] == 0


def test_a_versioned_lane_missing_from_the_state_fails(tmp_path: Path) -> None:
    code, out = _run(_seat(tmp_path, {"records": GOOD["records"]}))
    assert code == 1 and "medchron: versioned on the seat but not in the state" in out


def test_a_rejected_lane_with_no_copy_passes_only_while_no_copy_exists(tmp_path: Path) -> None:
    state = {**GOOD, "demand": {"etag8": None, "verdict": "rejected", "rejected_etag8": "badbadba"}}
    assert _run(_seat(tmp_path, state))[0] == 0
    os.symlink("demand.v00000000", tmp_path / "var/lib/smd-config/demand")
    assert _run(tmp_path)[0] == 1
