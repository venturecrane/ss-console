"""Tests for adapter.evidence.manifest."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[3]))

from adapter.evidence.manifest import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    PACKET_VERSION,
    SIGNER_EMAIL,
    SIGNER_NAME,
    SIGNATURE_STUB,
    build_manifest,
    manifest_sha256_hex,
)


def _base_kwargs(**over):
    base = dict(
        customer_slug="acme",
        matter="all",
        period_start="2026-04-01T00:00:00Z",
        period_end="2026-05-01T00:00:00Z",
        file_hashes={"a.txt": "deadbeef", "b.txt": "cafef00d"},
        actor="captain@example.com",
        actor_role="captain",
        generated_at="2026-05-15T12:00:00.000Z",
        signer_name="SMDurgan, LLC",
        signer_email="team@smd.services",
        signer_key_id="key-abc",
    )
    base.update(over)
    return base


def test_manifest_to_dict_carries_required_fields():
    m = build_manifest(**_base_kwargs())
    body = m.to_dict()
    assert body["customer_slug"] == "acme"
    assert body["period_start"] == "2026-04-01T00:00:00Z"
    assert body["period_end"] == "2026-05-01T00:00:00Z"
    assert body["generated_at"] == "2026-05-15T12:00:00.000Z"
    assert body["packet_version"] == PACKET_VERSION
    assert body["signer"]["name"] == "SMDurgan, LLC"
    assert body["signer"]["email"] == "team@smd.services"
    assert body["signer"]["key_id"] == "key-abc"
    assert body["signer"]["signature"] == SIGNATURE_STUB
    assert body["signer"]["algorithm"] == "stub-noop"
    assert body["generated_by"] == {"actor": "captain@example.com", "actor_role": "captain"}


def test_manifest_file_hashes_sorted_for_determinism():
    m = build_manifest(
        **_base_kwargs(file_hashes={"z.txt": "z", "a.txt": "a", "m.txt": "m"})
    )
    body = m.to_dict()
    assert list(body["file_hashes"].keys()) == ["a.txt", "m.txt", "z.txt"]


def test_manifest_to_bytes_is_deterministic():
    m1 = build_manifest(**_base_kwargs())
    m2 = build_manifest(**_base_kwargs())
    assert m1.to_bytes() == m2.to_bytes()


def test_manifest_sha256_matches_canonical_bytes():
    m = build_manifest(**_base_kwargs())
    raw = m.to_bytes()
    expected = hashlib.sha256(raw).hexdigest()
    assert manifest_sha256_hex(m) == expected


def test_manifest_extra_is_present_when_provided():
    m = build_manifest(**_base_kwargs(extra={"counts": {"audit_events": 5}}))
    body = m.to_dict()
    assert body["extra"] == {"counts": {"audit_events": 5}}


def test_manifest_signature_defaults_to_stub():
    m = build_manifest(**_base_kwargs())
    assert m.signature == SIGNATURE_STUB
    body = json.loads(m.to_bytes())
    assert body["signer"]["signature"] == SIGNATURE_STUB


def test_signer_of_record_is_the_entity_not_a_person():
    """The packet signs as SMDurgan, LLC (Captain decision 2026-08-13, #2122).

    Pinned as its own test rather than folded into the field-coverage test
    because it is a decision, not a shape: a firm hands this packet to a
    carrier years later, and the party that must be identifiable is the one
    under contract with the firm. A named individual would decay with staffing
    and would misstate who bears the obligation.

    Asserted through the DEFAULT path (no signer_* kwargs) because that is the
    path production takes. Asserting it only where the test passes the name in
    would prove the plumbing and not the decision.
    """
    kwargs = _base_kwargs()
    for k in ("signer_name", "signer_email"):
        kwargs.pop(k)
    body = build_manifest(**kwargs).to_dict()
    assert body["signer"]["name"] == SIGNER_NAME == "SMDurgan, LLC"
    assert body["signer"]["email"] == SIGNER_EMAIL
    assert "@" in body["signer"]["email"]
    # The operator who ran the export is still named, in its own field.
    assert body["generated_by"]["actor"] == "captain@example.com"


def test_packet_version_marks_the_signer_rename():
    """1.0 packets carry `captain_signature`; 1.1 carries `signer`.

    A reader who cannot tell the two apart cannot tell whether the name in the
    block is the guarantor or the operator, which is the confusion the rename
    exists to end.
    """
    body = build_manifest(**_base_kwargs()).to_dict()
    assert body["packet_version"] == "1.1"
    assert "captain_signature" not in body
    assert "signer" in body
