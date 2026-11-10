"""seat-checks.sh composes byte-identical check commands across a refactor.

The seat check list names seat paths through the generated seat layout
(operator/templates/_seat-layout.generated.sh, from
operator/contracts/seat-layout.yaml). Moving a path spelling from a literal to
``${SMD_LAYOUT_*}`` must not change one byte of any command a seat runs, so this
test sources the real list with every verb stubbed to print its arguments,
SEAT_ROOT empty (as on a seat) and every lane predicate true (so every branch
composes), and compares the lines with a fixture captured from the list BEFORE
the migration (fixtures/seat-checks-golden.txt).

A deliberate change to a check updates the fixture in the same PR:

    SMD_UPDATE_SEAT_CHECKS_GOLDEN=1 python -m pytest templates/tests/test_seat_checks_golden.py

Run::

    cd operator && python -m pytest templates/tests/test_seat_checks_golden.py -v
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1]
SEAT_CHECKS = TEMPLATES / "seat-checks.sh"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "seat-checks-golden.txt"

# Every verb a transport binds prints its name and each argument between bars,
# so a changed argument boundary changes the line too.
DRIVER = r"""
set -euo pipefail
_p() { printf '%s' "$1"; shift; for a in "$@"; do printf ' |%s|' "$a"; done; printf '\n'; }
check() { _p check "$@"; }
check_script() { _p check_script "$@"; }
check_idle() { _p check_idle "$@"; }
check_vendor() { _p check_vendor "$@"; }
check_note() { _p check_note "$@"; }
check_broken() { _p check_broken "$@"; }
check_file() { _p check_file "$@"; }
check_report() { _p check_report "$@"; }
check_lever() { _p check_lever "$@"; }
lane_authored() { return 0; }
lane_runs() { return 0; }
any_lane_runs() { return 0; }
SEAT_ROOT=""
EXPECTED_HERMES_SHA="0000000000000000000000000000000000000000"
APP_NAME="hermes-golden"
source "$1"
seat_checks
"""


def compose(script: Path) -> str:
    done = subprocess.run(  # noqa: S603 - runs the repo's own check list with stub verbs
        ["bash", "-c", DRIVER, "golden", str(script)],
        capture_output=True,
        text=True,
        check=False,
        cwd="/",
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"},
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_seat_checks_compose_the_captured_commands() -> None:
    now = compose(SEAT_CHECKS)
    if os.environ.get("SMD_UPDATE_SEAT_CHECKS_GOLDEN") == "1":
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(now, encoding="utf-8")
    golden = FIXTURE.read_text(encoding="utf-8")
    assert now.count("\n") > 100, "the list composed almost nothing; the driver is not exercising it"
    assert now == golden, "seat-checks.sh composes different commands than the captured fixture"


def _copy_with_layout(tmp_path: Path, layout: str) -> Path:
    copy = tmp_path / "seat-checks.sh"
    copy.write_text(SEAT_CHECKS.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "_seat-layout.generated.sh").write_text(layout, encoding="utf-8")
    return copy


def test_the_list_reads_the_generated_layout_beside_it(tmp_path: Path) -> None:
    # FALSIFIER: the same list beside a layout whose jobs run dir moved (the run
    # dir and every path derived from it, as the generator would write them)
    # composes the moved commands, so the golden above measures the layout's values.
    generated = (TEMPLATES / "_seat-layout.generated.sh").read_text(encoding="utf-8")
    assert "SMD_LAYOUT_JOBS_RUN='/run/smd-jobs'\n" in generated
    moved = generated.replace("='/run/smd-jobs", "='/run/smd-jobz")
    golden = FIXTURE.read_text(encoding="utf-8")
    assert compose(_copy_with_layout(tmp_path, generated)) == golden
    assert compose(_copy_with_layout(tmp_path, moved)) == golden.replace("/run/smd-jobs", "/run/smd-jobz")
    assert "/run/smd-jobz/tick" in golden.replace("/run/smd-jobs", "/run/smd-jobz")


def test_a_list_without_its_layout_does_not_load(tmp_path: Path) -> None:
    copy = tmp_path / "seat-checks.sh"
    copy.write_text(SEAT_CHECKS.read_text(encoding="utf-8"), encoding="utf-8")
    done = subprocess.run(  # noqa: S603 - sources a copy of the repo's own list
        ["bash", "-c", 'source "$1" && echo loaded || echo refused', "nolayout", str(copy)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.stdout.strip() == "refused"
