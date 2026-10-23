"""Sync gate for the vendored escalation_ledger.py copies (WP-A / WP-B).

CANONICAL SOURCE is ``operator/workspace_broker/escalation_ledger.py``. Skills
carry a byte-identical copy in their own dir so a stdlib-only ``pre_run.py`` and
the agent's ``execute_code`` turn can import it without a package install. Edit
the canonical, restamp the copies — never edit a copy.

The overlay's copy, ``operator/hermes-smd-overlay/shared/escalation_ledger.py``,
is what the agent's escalation tools import on a live seat. Since the 2026-10-22
fold (ADR 0095) it is in this tree, so it is byte-compared like every other copy
(``test_overlay_copy_is_byte_identical``); ``operator/contracts/overlay-pairs.json``
still records both hashes so an edit to either side is a conscious act.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from vendored_sync import assert_byte_identical, discover_copies

_OPERATOR_ROOT = Path(__file__).resolve().parents[1]
_CANONICAL = _OPERATOR_ROOT / "workspace_broker" / "escalation_ledger.py"
_PAIRS_MANIFEST = _OPERATOR_ROOT / "contracts" / "overlay-pairs.json"

_CANONICAL_REL = "operator/workspace_broker/escalation_ledger.py"
_OVERLAY_REL = "shared/escalation_ledger.py"

# Every skill that imports the shared ledger carries a vendored copy. The
# copies are discovered by glob (skills/*/escalation_ledger.py), so a sixth
# skill that vendors the module is under the gate the moment its copy exists;
# until 2026-09-11 this was a hand-maintained tuple (code review 2026-09-10,
# Architecture 5). Five copies as of 2026-09-11: deadline-miss-escalator,
# daily-needs-you-digest, client-verification-tracker, medical-records-chaser,
# lien-ledger-tracker; six since 2026-09-25 with task-list-keeper (it reads the
# records chase's resolutions), seven with date-prep-brief (it reads the records
# chaser's chase state). A skill dropping its copy lowers the floor on purpose.
_FLOOR = 7


def _escalation_pair() -> dict:
    manifest = json.loads(_PAIRS_MANIFEST.read_text(encoding="utf-8"))
    for pair in manifest["pairs"]:
        if pair.get("adapterPath") == _CANONICAL_REL:
            return pair
    raise AssertionError(
        f"overlay-pairs.json has no entry for {_CANONICAL_REL}; the overlay's "
        f"{_OVERLAY_REL} is then pinned by nothing at all (ss #2289 fix 4)."
    )


def test_canonical_exists() -> None:
    assert _CANONICAL.is_file(), f"canonical escalation_ledger.py missing at {_CANONICAL}"


def test_vendored_copies_are_byte_identical() -> None:
    assert_byte_identical(
        _CANONICAL,
        discover_copies("escalation_ledger.py"),
        floor=_FLOOR,
        restamp_hint="Edit workspace_broker/escalation_ledger.py and restamp, never the copy.",
    )


# ---------------------------------------------------------------------------
# The overlay's copy — ss #2289 fix 4, in-tree since the 2026-10-22 fold
# ---------------------------------------------------------------------------


def test_manifest_records_the_canonical_hash() -> None:
    """The pair's ``sha256`` must be the canonical file's REAL hash. Without
    this the manifest can name a file that no longer exists in that form, and
    every downstream byte-identity claim is anchored to a stale number."""
    pair = _escalation_pair()
    actual = hashlib.sha256(_CANONICAL.read_bytes()).hexdigest()
    assert pair["sha256"] == actual, (
        f"overlay-pairs.json sha256 for {_CANONICAL_REL} is stale.\n  recorded {pair['sha256']}\n  actual   {actual}"
    )


def test_overlay_copy_is_byte_identical() -> None:
    """The overlay's ``shared/escalation_ledger.py`` is a byte-identical twin of
    the canonical: the agent must compute the item_key the broker validates and
    pre_run joins against, or the join forks exactly as it did before ss #2151.

    Before the fold the two files lived in different repositories and the pair
    manifest recorded their hashes INDEPENDENTLY, so a one-sided edit could ship
    green behind a declared ``pendingOverlayPR`` window. There is no window any
    more: one repository, one commit, and this compares the bytes.
    """
    overlay_copy = _OPERATOR_ROOT / "hermes-smd-overlay" / "shared" / "escalation_ledger.py"
    assert overlay_copy.is_file(), f"overlay copy missing at {overlay_copy}"
    assert overlay_copy.read_bytes() == _CANONICAL.read_bytes(), (
        f"{_OVERLAY_REL} (under operator/hermes-smd-overlay) differs from {_CANONICAL_REL}. "
        "Edit the canonical and restamp the overlay copy in the same change; the "
        "item_key the agent computes must be the one the broker validates."
    )
    pair = _escalation_pair()
    assert pair["overlaySha256"] == hashlib.sha256(overlay_copy.read_bytes()).hexdigest(), (
        f"overlay-pairs.json overlaySha256 for {_OVERLAY_REL} is stale"
    )
    assert "pendingOverlayPR" not in pair, "the paired-PR window no longer exists; drop the field"
