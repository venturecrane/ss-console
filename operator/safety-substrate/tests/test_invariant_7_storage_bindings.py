"""Boot-runner entry for invariant #7 — cross-Machine query prohibition.

This file is the pytest-free boot entry required by ``run_invariants.py``
and guarded by ``test_guard_pytest_skip_boot_inert.py``.

WHY A SEPARATE FILE? ``test_invariant_7.py`` is the rich pytest suite for
invariant #7 and imports pytest at module top (needed for ``pytest.raises``,
``@pytest.mark.parametrize``, etc.). The boot runner running in the customer
Machine venv (no pytest) cannot import that file — ``ModuleNotFoundError``
for pytest aborts the import and the runner classifies the file as
``PYTEST_ONLY_SKIP``. Invariant #7, the load-bearing per-customer storage
isolation check (cross-Machine query prohibition, ADR 0007/0009), was
therefore never firing at Machine boot despite passing in CI.

This file contains ONLY a top-level ``run()`` that delegates to
``invariant_7.run()`` — the module's own pytest-free self-check fixtures.
No pytest import here, so this file imports cleanly in the runtime venv and
the boot runner executes the invariant. Detailed coverage lives in
``test_invariant_7.py``; this file is intentionally thin.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # safety-substrate/

from invariants.invariant_7 import run as _invariant_run


def run() -> tuple[bool, str]:
    """Delegate to invariant_7's pytest-free smoke fixtures."""
    return _invariant_run()


if __name__ == "__main__":
    ok, msg = run()
    print(msg)
    sys.exit(0 if ok else 1)
