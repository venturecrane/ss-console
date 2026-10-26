"""Validator parity contract (ADR 0044): the Python and TS validators agree.

The on-box Python validator (``bootstrap.validate``) and the console TS validator
(``ss-console/src/lib/operator/customer-yaml``) gate the SAME file at two points
in the apply path: the console blesses an edit at authoring time, the broker
re-validates the pulled file on-box before writing it to the volume. If they
disagree, a config the console accepted could be rejected on apply, or a danger
the console would catch could land on the Machine if its path is bypassed.

This test pins the agreement. Since the 2026-10-22 fold both validators live in
one repo and both tests read the ONE manifest,
``src/lib/operator/customer-yaml/__contract__/validator_parity_fixtures.json``.
Each test asserts ITS validator classifies every fixture as the manifest's
``expect``, and pins a canonical-content hash of the ``fixtures`` array
(formatting-independent) that must equal the constant in the console's
``tests/customer-yaml-parity-contract.test.ts``. A change to the fixture data
trips both hashes, so neither validator can be re-pinned alone.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bootstrap.validate import validate_customer_yaml

_MANIFEST = (
    Path(__file__).resolve().parents[4]
    / "src/lib/operator/customer-yaml/__contract__/validator_parity_fixtures.json"
)

# Canonical-content hash of the `fixtures` array (sorted keys, compact separators)
# — independent of file formatting, so prettier cannot break it. MUST equal
# PINNED_CONTENT_SHA256 in the console contract test
# (src/lib/operator/customer-yaml/__contract__/validator-parity.test.ts); both
# read the one manifest above since the 2026-10-22 fold. Update both pins
# whenever the fixture data changes.
_PINNED_CONTENT_SHA256 = "59c8618dc932a627e82b6782ada55254aeea4cfd3a336774316eab904dc5ba68"


def _load() -> dict:
    return json.loads(_MANIFEST.read_text())


def _content_hash(manifest: dict) -> str:
    canon = json.dumps(
        manifest["fixtures"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canon.encode()).hexdigest()


def test_manifest_content_hash_is_pinned():
    """The cross-repo drift guard: the canonical content of the fixtures must
    match the digest shared with the console test. A one-sided edit trips this."""
    actual = _content_hash(_load())
    assert actual == _PINNED_CONTENT_SHA256, (
        "fixture data changed without updating _PINNED_CONTENT_SHA256 here AND in "
        "the console contract test — the two repos have drifted."
    )


def _fixture_ids():
    return [f["name"] for f in _load()["fixtures"]]


@pytest.mark.parametrize("fixture", _load()["fixtures"], ids=_fixture_ids())
def test_python_validator_matches_contract(fixture, tmp_path):
    """The on-box validator classifies each fixture as the manifest's expect."""
    path = tmp_path / "customer.yaml"
    path.write_text(fixture["yaml"])
    errors = validate_customer_yaml(path)
    accepted = errors == []
    if fixture["expect"] == "accept":
        assert accepted, f"{fixture['name']}: expected accept, got errors: {errors}"
    else:
        assert not accepted, f"{fixture['name']}: expected reject ({fixture['note']}), but accepted"
