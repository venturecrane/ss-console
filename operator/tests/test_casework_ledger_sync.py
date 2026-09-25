"""Sync gate for the vendored casework_ledger.py copies.

CANONICAL SOURCE is ``operator/workspace_broker/casework_ledger.py``: the broker
validates every casework row with it. Skills that read the ledger (the task
review, the date prep, the escalator's exclusions) carry a byte-identical copy
beside their ``pre_run.py``, and the overlay carries one at
``shared/casework_ledger.py`` for the reply and finish tools. All of them must
compute the same ``item_key`` and fold the same state, or a reply resolves to a
row the broker never wrote. Edit the canonical, restamp the copies.

The overlay copy is cross-repo, so ``operator/contracts/overlay-pairs.json``
pins it, and this file checks that pin the same way
``test_escalation_ledger_sync.py`` checks its own.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from vendored_sync import assert_byte_identical, discover_copies

_OPERATOR_ROOT = Path(__file__).resolve().parents[1]
_CANONICAL = _OPERATOR_ROOT / "workspace_broker" / "casework_ledger.py"
_PAIRS_MANIFEST = _OPERATOR_ROOT / "contracts" / "overlay-pairs.json"
_CANONICAL_REL = "operator/workspace_broker/casework_ledger.py"

# Copies are discovered by glob (skills/*/casework_ledger.py). The floor starts
# at zero because the broker half lands first; the PR that vendors the first
# skill copy raises it, and a skill dropping its copy lowers it on purpose.
# Two copies as of 2026-09-25: task-list-keeper, deadline-miss-escalator.
_FLOOR = 2


def _pair() -> dict:
    manifest = json.loads(_PAIRS_MANIFEST.read_text(encoding="utf-8"))
    for pair in manifest["pairs"]:
        if pair.get("adapterPath") == _CANONICAL_REL:
            return pair
    raise AssertionError(f"overlay-pairs.json has no entry for {_CANONICAL_REL}")


def test_canonical_exists() -> None:
    assert _CANONICAL.is_file(), f"canonical casework_ledger.py missing at {_CANONICAL}"


def test_vendored_copies_are_byte_identical() -> None:
    assert_byte_identical(
        _CANONICAL,
        discover_copies("casework_ledger.py"),
        floor=_FLOOR,
        restamp_hint="Edit workspace_broker/casework_ledger.py and restamp, never the copy.",
    )


def test_manifest_records_the_canonical_hash() -> None:
    actual = hashlib.sha256(_CANONICAL.read_bytes()).hexdigest()
    assert _pair()["sha256"] == actual, f"overlay-pairs.json sha256 for {_CANONICAL_REL} is stale; actual {actual}"


def test_overlay_copy_is_pinned_byte_identical() -> None:
    """The overlay's copy must be the same bytes. A split is allowed only while
    declared: ``pendingOverlayPR`` names the overlay PR that closes it."""
    pair = _pair()
    if pair["sha256"] == pair["overlaySha256"]:
        assert not pair.get("pendingOverlayPR"), "hashes agree; drop pendingOverlayPR"
        return
    pending = pair.get("pendingOverlayPR")
    assert isinstance(pending, str) and "hermes-smd-overlay" in pending, (
        f"{_CANONICAL_REL} and shared/casework_ledger.py are pinned to different content and "
        "nothing says why; add pendingOverlayPR naming the overlay PR, or restamp the overlay copy."
    )
