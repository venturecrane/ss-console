"""Every shell script that names the seat layout defines it before the first use.

The boot scripts read seat paths as ``${SMD_LAYOUT_<ID>}`` from
operator/templates/_seat-layout.generated.sh (operator/contracts/seat-layout.yaml).
Under ``set -u`` a name the generated file does not assign, or a use before the
file is sourced, kills a seat's boot; without ``set -u`` it silently expands to
nothing and a check or a mount points at the wrong place. So, for every script:

1. each SMD_LAYOUT_<ID> it names is assigned by the generated file, and
2. it sources the generated file itself, on a line before its first use.

Run::

    cd operator && python -m pytest templates/tests/test_seat_layout_shell_names.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
GENERATED = REPO / "operator/templates/_seat-layout.generated.sh"
ASSIGNED = set(re.findall(r"^SMD_LAYOUT_([A-Z0-9_]+)=", GENERATED.read_text(encoding="utf-8"), re.M))
USE = re.compile(r"\$\{?SMD_LAYOUT_([A-Z0-9_]+)")
SOURCE = re.compile(r"^\s*\.\s+.*_seat-layout\.generated\.sh\b")

SCRIPTS = sorted(
    p
    for p in [*REPO.glob("operator/**/*.sh"), REPO / "scripts/ci-publish-bundle.sh"]
    if p != GENERATED and "node_modules" not in p.parts and USE.search(p.read_text(encoding="utf-8"))
)


def _code_lines(path: Path) -> list[tuple[int, str]]:
    return [
        (n, line)
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if not line.lstrip().startswith("#")
    ]


def test_the_scripts_that_name_the_layout_are_found() -> None:
    names = {p.relative_to(REPO).as_posix() for p in SCRIPTS}
    for expected in (
        "operator/templates/entrypoint.sh",
        "operator/templates/seat-checks.sh",
        "operator/templates/bootstrap.sh",
        "operator/templates/selftest.sh",
        "operator/bin/lane-proof.sh",
        "scripts/ci-publish-bundle.sh",
    ):
        assert expected in names


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.relative_to(REPO).as_posix())
def test_every_layout_name_is_assigned(path: Path) -> None:
    used = {m for _, line in _code_lines(path) for m in USE.findall(line)}
    assert used, "the script names no layout id in code"
    assert sorted(used - ASSIGNED) == [], f"not assigned in {GENERATED.name}"


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.relative_to(REPO).as_posix())
def test_the_script_sources_the_layout_before_its_first_use(path: Path) -> None:
    lines = _code_lines(path)
    sourced = [n for n, line in lines if SOURCE.match(line)]
    first_use = min(n for n, line in lines if USE.search(line) and not SOURCE.match(line))
    assert sourced, "names SMD_LAYOUT_* but never sources the generated layout"
    assert sourced[0] < first_use, f"first use on line {first_use}, sourced on line {sourced[0]}"


def test_the_checks_fail_on_an_unassigned_name_and_a_late_source(tmp_path: Path) -> None:
    # FALSIFIER for the two tests above.
    bad = tmp_path / "bad.sh"
    bad.write_text('echo "${SMD_LAYOUT_NOT_DECLARED}"\n. ./_seat-layout.generated.sh\n', encoding="utf-8")
    lines = _code_lines(bad)
    assert {m for _, line in lines for m in USE.findall(line)} - ASSIGNED == {"NOT_DECLARED"}
    sourced = [n for n, line in lines if SOURCE.match(line)]
    first_use = min(n for n, line in lines if USE.search(line) and not SOURCE.match(line))
    assert sourced[0] > first_use
