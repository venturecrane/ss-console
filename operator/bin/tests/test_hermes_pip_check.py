"""hermes_pip_check.py tolerates exactly one unmet requirement (DP-1, 2026-10-27).

The image moves certifi past hermes-agent's own exact pin (certifi==2026.5.20)
to close PYSEC-2026-4320, so a plain ``uv pip check`` would fail the build.
The check the image runs instead must tolerate that one requirement, only at
the version hermes-security.txt installs, and fail on everything else.

The output lines are uv's exact format (checked against uv 0.11.6, the image's
uv, and 0.12.19).

Run::

    cd operator && python3 -m pytest bin/tests/test_hermes_pip_check.py -q
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parents[2] / "requirements"

_spec = importlib.util.spec_from_file_location("hermes_pip_check", REQUIREMENTS / "hermes_pip_check.py")
assert _spec and _spec.loader
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

PIN = check.pinned_version(REQUIREMENTS / "hermes-security.txt", "certifi")


def _uv(*lines: str) -> str:
    n = len(lines)
    head = f"Checked 121 packages in 3ms\nFound {n} incompatibilit{'y' if n == 1 else 'ies'}\n"
    return head + "\n".join(lines) + "\n"


CERTIFI_OK = f"The package `hermes-agent` requires `certifi==2026.5.20`, but `{PIN}` is installed"


def test_the_override_pin_is_read_from_the_file() -> None:
    assert PIN and PIN != "2026.5.20"


def test_a_clean_venv_passes() -> None:
    assert check.judge("Checked 121 packages in 3ms\nAll installed packages are compatible\n", 0, PIN) == []


def test_hermes_agents_certifi_pin_at_our_version_is_tolerated() -> None:
    assert check.judge(_uv(CERTIFI_OK), 1, PIN) == []


def test_certifi_at_any_other_version_fails() -> None:
    line = "The package `hermes-agent` requires `certifi==2026.5.20`, but `2026.9.1` is installed"
    assert check.judge(_uv(line), 1, PIN) == [line]


def test_a_missing_certifi_fails() -> None:
    line = "The package `hermes-agent` requires `certifi==2026.5.20`, but it's not installed"
    assert check.judge(_uv(line), 1, PIN) == [line]


def test_another_requirer_of_certifi_fails() -> None:
    line = f"The package `requests` requires `certifi<2026`, but `{PIN}` is installed"
    assert check.judge(_uv(line), 1, PIN) == [line]


def test_any_other_unmet_requirement_fails_even_beside_the_tolerated_one() -> None:
    other = "The package `hermes-smd-overlay` requires `boto3>=1.40`, but it's not installed"
    assert check.judge(_uv(CERTIFI_OK, other), 1, PIN) == [other]


def test_hermes_agents_pyjwt_pin_is_not_tolerated() -> None:
    line = "The package `hermes-agent` requires `pyjwt[crypto]==2.13.0`, but `2.15.0` is installed"
    assert check.judge(_uv(line), 1, PIN) == [line]


def test_unreadable_output_fails() -> None:
    assert check.judge("error: something broke\n", 2, PIN) != []
    # A count that disagrees with the lines it found is unreadable too.
    assert check.judge("Found 3 incompatibilities\n" + CERTIFI_OK + "\n", 1, PIN) != []
