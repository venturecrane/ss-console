"""A guard that cannot run fails, and says how to make it run.

The incident (2026-11-06): the function-size ratchet carried
`pytest.mark.skipif(ruff is None)`. A builder ran the suite without ruff on
PATH, the ratchet skipped, the run was reported green twice, and CI then failed
on that ratchet. A skip is reported as a non-failure, so a guard that skips has
certified something it never looked at.

So a guard or ratchet test asks for what it needs through `require()`. When the
need is missing the test FAILS with one line: what is missing and the exact
command that provides it. The only way to skip instead is the explicit switch
below, and the skip reason names it. No workflow under .github/workflows/ sets
it (bin/tests/test_guard_needs.py asserts that), so CI can never skip a guard.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

SWITCH = "SMD_ALLOW_GUARD_SKIPS"

# The pinned install line the operator-substrate workflow runs. Kept here, next
# to the guards that need it; test_guard_needs.py asserts the workflow still
# installs this exact pin, so the hint cannot drift from CI.
RUFF_INSTALL = "python3 -m pip install ruff==0.15.17"

REPO = Path(__file__).resolve().parents[3]


def skips_allowed() -> bool:
    return os.environ.get(SWITCH) == "1"


def require(present: object, missing: str, provide: str) -> None:
    """Fail (or, under the switch, skip) unless `present` is truthy.

    `missing` names the need ("ruff on PATH"); `provide` is the exact command
    that supplies it.
    """
    if present:
        return
    if skips_allowed():
        pytest.skip(f"{missing}: skipped because {SWITCH}=1 is set")
    pytest.fail(f"{missing}; provide it with `{provide}` (or set {SWITCH}=1 to skip on purpose)", pytrace=False)


def require_path(path: Path) -> None:
    """A committed file or directory the test reads. Absent means the checkout
    is incomplete, not that the check may be waived, so restoring it is the fix."""
    rel = path.resolve().relative_to(REPO).as_posix()
    require(path.exists(), f"{rel} is absent from this checkout", f"git checkout origin/main -- {rel}")
