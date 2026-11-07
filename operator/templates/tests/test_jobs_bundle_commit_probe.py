"""jobs-bundle-built-for-image: the mounted jobs bundle is not older than the image.

The probe runs against a fixture seat root (SMD_SEAT_ROOT). The two cases the
check exists for are both driven: a bundle built before the image's bundle floor
fails naming both commits, and a bundle that lags the image because no bundle
path changed since its commit (it IS the floor, or comes after it) passes.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROBE = Path(__file__).resolve().parents[1] / "jobs-bundle-commit-probe.py"
IMAGE = "c" * 40
FLOOR = "b" * 40
OLDER = "a" * 40
NEWER = "d" * 40
VERSION = "202611070833-aaaaaaaa"


def seat(
    root: Path, *, state: str = VERSION, floor: str | None = f"{FLOOR} 2680", manifest: dict | None = None
) -> Path:
    """A fixture root: what boot wrote for the bundle, the image's floor and commit, the installed manifest."""
    (root / "run/smd-bundles").mkdir(parents=True)
    (root / "run/smd-bundles/jobs").write_text(state + "\n")
    (root / "app").mkdir(parents=True)
    (root / "app/OVERLAY_REF").write_text(IMAGE + "\n")
    if floor is not None:
        (root / "app/bundle-floor").write_text(floor + "\n")
    if manifest is not None:
        d = root / "opt/data/.smd-root/bundles/jobs" / state
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps(manifest))
    return root


def run(root: Path) -> tuple[int, str]:
    r = subprocess.run(  # noqa: S603 - the repo's own probe, a fixed argv
        [sys.executable, "-I", str(PROBE)],
        env={"SMD_SEAT_ROOT": str(root), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    return r.returncode, r.stdout


def test_a_bundle_built_before_the_image_floor_fails_naming_both_commits(tmp_path: Path) -> None:
    """The 2026-11-07 window: the new image booted with the previous bundle.

    FALSIFIER: compare with >= floor_seq - 1, or pass every bundle the image did
    not build, and this exits 0.
    """
    rc, out = run(seat(tmp_path, manifest={"ss_console_sha": OLDER, "ss_console_seq": 2675}))
    assert rc == 1, out
    assert OLDER[:8] in out and FLOOR[:8] in out and IMAGE[:8] in out


def test_a_bundle_that_lags_because_no_bundle_path_changed_passes(tmp_path: Path) -> None:
    """The image's commit changed no bundle path, so its floor is an earlier commit,
    and the bundle built there is current. FALSIFIER: compare the bundle with the
    image's own commit instead of its floor and this fails."""
    rc, out = run(seat(tmp_path, manifest={"ss_console_sha": FLOOR, "ss_console_seq": 2680}))
    assert rc == 0, out
    assert "bundle floor" in out


def test_a_bundle_newer_than_the_image_passes(tmp_path: Path) -> None:
    """A runner-only merge builds a bundle and no image. FALSIFIER: require equality."""
    rc, out = run(seat(tmp_path, manifest={"ss_console_sha": NEWER, "ss_console_seq": 2690}))
    assert rc == 0, out


def test_a_manifest_without_a_commit_number_fails_unless_it_is_the_floor(tmp_path: Path) -> None:
    """It predates the number, so it predates every image that bakes a floor."""
    rc, _out = run(seat(tmp_path / "a", manifest={"ss_console_sha": OLDER}))
    assert rc == 1
    rc, _out = run(seat(tmp_path / "b", manifest={"ss_console_sha": FLOOR}))
    assert rc == 0


@pytest.mark.parametrize(
    "kw",
    [
        {"state": "image"},
        {"floor": None, "manifest": {"ss_console_sha": OLDER, "ss_console_seq": 1}},
    ],
)
def test_nothing_to_compare_is_a_pass_that_says_so(tmp_path: Path, kw: dict) -> None:
    rc, out = run(seat(tmp_path, **kw))
    assert rc == 0 and out.strip(), out


@pytest.mark.parametrize(
    "kw",
    [
        {"manifest": None},
        {"floor": "not a floor", "manifest": {"ss_console_sha": FLOOR, "ss_console_seq": 2680}},
        {"state": "garbage", "manifest": None},
        {"manifest": {"ss_console_seq": 2690}},
    ],
)
def test_cannot_evaluate_fails(tmp_path: Path, kw: dict) -> None:
    rc, out = run(seat(tmp_path, **kw))
    assert rc == 1, out


def test_an_unreadable_state_fails(tmp_path: Path) -> None:
    root = seat(tmp_path, manifest={"ss_console_sha": FLOOR, "ss_console_seq": 2680})
    (root / "run/smd-bundles/jobs").unlink()
    assert run(root)[0] == 1
