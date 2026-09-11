"""Merge-gate: the evidence packet must not OVERCLAIM cryptographic signing.

The packet is digest-verified (per-artifact SHA-256 + a manifest hash recorded
in the append-only COMPLIANCE_PACKET_EXPORTED audit row) but is NOT yet
cryptographically signed — the manifest self-discloses signature="unsigned-stub".
Operator-facing surfaces previously claimed "signed tar.gz", contradicting the
packet's own disclosure. For a law-firm compliance product, being caught
overclaiming integrity taints every other claim in the packet. These tests pin
the honest wording so it cannot silently regress, and stand as the merge gate
until a real (Ed25519 detached) signer is wired.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[3]))  # operator/ on sys.path

from bin.lib import evidence  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)


def test_cli_description_does_not_claim_signed():
    """The argparse help must not say the output is 'signed' — it is digest-
    verified and unsigned. Catches the overclaim at the operator surface."""
    import contextlib
    import io

    # parse_args builds the parser internally; capture its --help output.
    buf = io.StringIO()
    with contextlib.suppress(SystemExit), contextlib.redirect_stdout(buf):
        evidence.parse_args(["--help"])
    help_text = buf.getvalue().lower()
    assert "signed tar.gz" not in help_text, (
        "evidence CLI help claims 'signed tar.gz' but the packet is unsigned "
        "(manifest signature='unsigned-stub'). Keep the wording honest."
    )
    # Positively assert the honest framing is present.
    assert "digest-verified" in help_text or "unsigned" in help_text


def test_evidence_package_docstrings_are_honest():
    """The evidence package + builder docstrings must not call the packet
    'signed'; they should describe the digest-verified / unsigned reality."""
    from adapter import evidence as evidence_pkg
    from adapter.evidence import packet as packet_mod

    pkg_doc = (evidence_pkg.__doc__ or "").lower()
    assert "single signed tar.gz" not in pkg_doc
    assert "digest-verified" in pkg_doc

    builder_doc = (packet_mod.EvidencePacketBuilder.__doc__ or "").lower()
    assert "signed evidence packet" not in builder_doc
    assert "digest-verified" in builder_doc or "unsigned-stub" in builder_doc
