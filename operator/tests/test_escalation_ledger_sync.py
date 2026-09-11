"""Sync gate for the vendored escalation_ledger.py copies (WP-A / WP-B).

CANONICAL SOURCE is ``operator/workspace_broker/escalation_ledger.py``. Skills
carry a byte-identical copy in their own dir so a stdlib-only ``pre_run.py`` and
the agent's ``execute_code`` turn can import it without a package install. Edit
the canonical, restamp the copies — never edit a copy.

The module has a FIFTH copy: ``shared/escalation_ledger.py`` in
venturecrane/hermes-smd-overlay, which is what the agent's escalation tools
import on a live seat. It is cross-repo, so the byte-comparison above cannot
reach it; ``operator/contracts/overlay-pairs.json`` pins it instead. This file
also gates that pin — see ``test_overlay_copy_is_pinned_byte_identical``.
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
# lien-ledger-tracker. A skill dropping its copy lowers the floor on purpose.
_FLOOR = 5


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
# The fifth copy (cross-repo) — ss #2289 fix 4
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


def test_overlay_copy_is_pinned_byte_identical() -> None:
    """The overlay's ``shared/escalation_ledger.py`` is declared a byte-identical
    twin of the canonical — the agent must compute the item_key the broker
    validates and pre_run joins against, or the join forks exactly as it did
    before ss #2151. Nothing enforced that: the manifest records the two hashes
    INDEPENDENTLY, so an overlay-side edit could be shipped green by recording
    its own new ``overlaySha256`` while the two repos silently diverged.

    This asserts they are the same number. The one legitimate exception is the
    paired-PR window: the console half merges first (that is the established
    order — ss #2151 shipped d2e0f7cb before overlay#239), and until the pin
    moves past the overlay half, ``overlaySha256`` MUST keep naming the file at
    the currently pinned ``overlayRef`` or the SEC-32 network gate fails. During
    that window the split must be DECLARED, not silent: the pair carries
    ``pendingOverlayPR`` naming the open overlay PR, and closing the window is
    the same act that removes the field.

    What this catches: an overlay copy that diverges from the canonical with no
    declared pairing. What it does NOT catch: a ``pendingOverlayPR`` left parked
    open forever, and drift in the overlay between pin bumps — that one belongs
    to ``operator/bin/verify-overlay-pairs.py``, which hashes the real overlay
    file at the pinned ref in the ``operator-substrate`` workflow.
    """
    pair = _escalation_pair()
    pending = pair.get("pendingOverlayPR")
    if pair["sha256"] == pair["overlaySha256"]:
        assert not pending, (
            "pendingOverlayPR is still set but the hashes already agree — the "
            "paired-PR window is closed; drop the field so the next split is "
            "visible as a split."
        )
        return
    assert pending, (
        f"{_CANONICAL_REL} and {_OVERLAY_REL} are pinned to DIFFERENT content and "
        f"nothing says why.\n"
        f"  sha256        {pair['sha256']}\n"
        f"  overlaySha256 {pair['overlaySha256']}\n"
        f"These two are byte-identical twins: the overlay computes the item_key "
        f"the broker receives. If this is the paired-PR window, add "
        f"`pendingOverlayPR` naming the overlay PR. If it is not, restamp the "
        f"overlay copy from the canonical."
    )
    assert isinstance(pending, str) and "hermes-smd-overlay" in pending, (
        f"pendingOverlayPR must name the overlay PR that closes the split "
        f"(a venturecrane/hermes-smd-overlay URL); got {pending!r}"
    )
