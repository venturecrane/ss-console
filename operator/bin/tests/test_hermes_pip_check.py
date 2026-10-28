"""hermes_pip_check.py tolerates named unmet requirements and nothing else.

Two kinds of mismatch are deliberate in the Hermes venv, and a plain
``uv pip check`` would fail the build on both:

* our override (hermes-security.txt): since the v0.22.5 promotion
  (ss-console#3564) brotlicffi moves past hermes-agent's own exact pin
  (``brotlicffi==1.2.0.2``) to close PYSEC-2026-4323. Tolerated only for that
  requirer, only at the version hermes-security.txt installs;
* Hermes' own uv.lock overrides: browser-harness pins ``pillow==12.3.0`` and
  Hermes' lock overrides it to 13.0.0. Tolerated only at the version the lock
  resolved, the rule Hermes' installer applies (pm/pip_check.py at v0.22.5).

The output lines are uv's exact format (checked against uv 0.11.6, the image's
uv, and 0.12.19).

Run::

    cd operator && python3 -m pytest bin/tests/test_hermes_pip_check.py -q
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REQUIREMENTS = Path(__file__).resolve().parents[2] / "requirements"

_spec = importlib.util.spec_from_file_location("hermes_pip_check", REQUIREMENTS / "hermes_pip_check.py")
assert _spec and _spec.loader
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

PIN = check.pinned_version(REQUIREMENTS / "hermes-security.txt", "brotlicffi")
PINS = {"brotlicffi": PIN}

LOCK = """\
version = 1
requires-python = ">=3.11, <3.15"

[manifest]
overrides = [
    { name = "cryptography", specifier = "==50.0.1" },
    { name = "pillow", specifier = "==13.0.0" },
    { name = "spacy-curated-transformers", marker = "sys_platform == 'never'" },
]

[[package]]
name = "browser-harness"
version = "0.1.13"

[[package]]
name = "pillow"
version = "13.0.0"

[[package]]
name = "cryptography"
version = "50.0.1"
"""


@pytest.fixture
def lock_versions(tmp_path: Path) -> dict[str, set[str]]:
    lock = tmp_path / "uv.lock"
    lock.write_text(LOCK, encoding="utf-8")
    return check.lock_overridden_versions(lock)


def _uv(*lines: str) -> str:
    n = len(lines)
    head = f"Checked 121 packages in 3ms\nFound {n} incompatibilit{'y' if n == 1 else 'ies'}\n"
    return head + "\n".join(lines) + "\n"


BROTLI_OK = f"The package `hermes-agent` requires `brotlicffi==1.2.0.2`, but `{PIN}` is installed"
PILLOW_OK = "The package `browser-harness` requires `pillow==12.3.0`, but `13.0.0` is installed"


def test_the_override_pin_is_read_from_the_file() -> None:
    assert PIN and PIN != "1.2.0.2"


def test_the_tolerated_table_is_exactly_the_override_file() -> None:
    names = {
        check._norm(line.split("==", 1)[0])
        for line in (REQUIREMENTS / "hermes-security.txt").read_text(encoding="utf-8").splitlines()
        if line and line[0].isalnum()
    }
    assert set(check.TOLERATED) == names


def test_a_clean_venv_passes() -> None:
    assert check.judge("Checked 121 packages in 3ms\nAll installed packages are compatible\n", 0, PINS) == []


def test_hermes_agents_brotlicffi_pin_at_our_version_is_tolerated() -> None:
    assert check.judge(_uv(BROTLI_OK), 1, PINS) == []


def test_brotlicffi_at_any_other_version_fails() -> None:
    line = "The package `hermes-agent` requires `brotlicffi==1.2.0.2`, but `1.2.0.3` is installed"
    assert check.judge(_uv(line), 1, PINS) == [line]


def test_a_missing_brotlicffi_fails() -> None:
    line = "The package `hermes-agent` requires `brotlicffi==1.2.0.2`, but it's not installed"
    assert check.judge(_uv(line), 1, PINS) == [line]


def test_another_requirer_of_brotlicffi_fails() -> None:
    line = f"The package `aiohttp` requires `brotlicffi<1.2`, but `{PIN}` is installed"
    assert check.judge(_uv(line), 1, PINS) == [line]


def test_a_hermes_lock_override_at_the_locked_version_is_tolerated(lock_versions) -> None:
    assert check.judge(_uv(PILLOW_OK, BROTLI_OK), 1, PINS, lock_versions) == []


def test_a_hermes_lock_override_at_another_version_fails(lock_versions) -> None:
    line = "The package `browser-harness` requires `pillow==12.3.0`, but `13.1.0` is installed"
    assert check.judge(_uv(line), 1, PINS, lock_versions) == [line]


def test_without_the_lock_the_pillow_mismatch_fails() -> None:
    # FALSIFIER: v0.22.5's browser-harness/pillow mismatch is real; the image's
    # check passes it only because Hermes' uv.lock records the override.
    assert check.judge(_uv(PILLOW_OK), 1, PINS) == [PILLOW_OK]


def test_any_other_unmet_requirement_fails_even_beside_the_tolerated_ones(lock_versions) -> None:
    other = "The package `hermes-smd-overlay` requires `boto3>=1.40`, but it's not installed"
    assert check.judge(_uv(BROTLI_OK, PILLOW_OK, other), 1, PINS, lock_versions) == [other]


def test_hermes_agents_pyjwt_pin_is_not_tolerated(lock_versions) -> None:
    line = "The package `hermes-agent` requires `pyjwt[crypto]==2.13.0`, but `2.15.0` is installed"
    assert check.judge(_uv(line), 1, PINS, lock_versions) == [line]


def test_unreadable_output_fails() -> None:
    assert check.judge("error: something broke\n", 2, PINS) != []
    # A count that disagrees with the lines it found is unreadable too.
    assert check.judge("Found 3 incompatibilities\n" + BROTLI_OK + "\n", 1, PINS) != []


def test_lock_overrides_print_as_requirement_lines(tmp_path: Path) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text(LOCK, encoding="utf-8")
    assert check.lock_override_requirements(lock) == [
        "cryptography==50.0.1",
        "pillow==13.0.0",
        "spacy-curated-transformers ; sys_platform == 'never'",
    ]


def test_a_source_override_is_refused(tmp_path: Path) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text(
        '[manifest]\noverrides = [{ name = "x", url = "https://example.invalid/x.whl" }]\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="unsupported"):
        check.lock_override_requirements(lock)
