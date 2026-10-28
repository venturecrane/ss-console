"""A `find | wc -l` count in a seat script's assignment must not be able to kill the boot.

bootstrap.sh and entrypoint.sh run under `set -euo pipefail`. In an assignment
`n="$(find DIR ... | wc -l)"`, a missing DIR makes find exit 1, pipefail fails
the substitution, and set -e ends the script with no log line. The boot harness
found exactly that on 2026-10-28: a fresh volume whose first voice-vault fetch
failed exited bootstrap silently, so the seat crash-loops with nothing to read.

The guard: every such assignment wraps find as `{ find ... || true; }`.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_TEMPLATES = Path(__file__).resolve().parents[1]
_SCRIPTS = sorted(_TEMPLATES.glob("*.sh"))
_ASSIGNED_FIND_COUNT = re.compile(r'^\s*\w+="\$\(\s*(\{\s*)?find\b[^\n]*\|\s*wc -l')


def _offenders() -> list[str]:
    bad = []
    for script in _SCRIPTS:
        for no, line in enumerate(script.read_text().splitlines(), 1):
            m = _ASSIGNED_FIND_COUNT.match(line)
            if m and not (m.group(1) and "|| true" in line):
                bad.append(f"{script.name}:{no}: {line.strip()}")
    return bad


def test_seat_scripts_exist() -> None:
    assert any(s.name == "bootstrap.sh" for s in _SCRIPTS), _SCRIPTS


def test_no_find_count_assignment_can_end_the_boot() -> None:
    assert _offenders() == []


@pytest.mark.parametrize(
    ("snippet", "survives"),
    [
        ('n="$(find "$D/cohort" -name "*.json" 2>/dev/null | wc -l | tr -d " ")"', False),
        ('n="$( { find "$D/cohort" -name "*.json" 2>/dev/null || true; } | wc -l | tr -d " ")"', True),
    ],
)
def test_the_guarded_form_survives_a_missing_dir(tmp_path: Path, snippet: str, survives: bool) -> None:
    script = f'set -euo pipefail\nD="{tmp_path}/absent"\n{snippet}\necho "reached n=$n"\n'
    out = subprocess.run(  # noqa: S603 - fixed argv; the script is this test's own literal
        ["bash", "-c", script], capture_output=True, text=True, check=False
    )
    assert (out.returncode == 0 and "reached n=0" in out.stdout) is survives, out
