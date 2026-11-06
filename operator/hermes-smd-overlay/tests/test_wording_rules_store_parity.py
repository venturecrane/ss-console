"""The wording-rule reader exists twice, byte for byte (shared/wording_rules_store.py).

The install letter reads the installed ``wording.json`` here; the job lanes read
it through ``operator/runners/jobs/jobs/wording_rules_store.py``. If the two ever
differ, the letter can call a rule in effect that the lanes read differently.
"""

from __future__ import annotations

from pathlib import Path

_HERE = Path(__file__).resolve().parents[1] / "shared" / "wording_rules_store.py"
_TWIN = Path(__file__).resolve().parents[2] / "runners" / "jobs" / "jobs" / "wording_rules_store.py"


def test_the_two_readers_are_byte_identical():
    assert _TWIN.is_file(), f"the jobs-side twin is not at {_TWIN}"
    assert _HERE.read_bytes() == _TWIN.read_bytes(), (
        "edit operator/hermes-smd-overlay/shared/wording_rules_store.py and copy it over "
        "operator/runners/jobs/jobs/wording_rules_store.py byte for byte"
    )
