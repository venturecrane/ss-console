"""End-to-end: a built packet's detached signature verifies (ss-console #2122).

The unit tests in ``test_signing.py`` prove the signer. This file proves the
PACKET: build a real tar.gz, extract ``manifest.json`` and ``manifest.sig`` from
it, and verify one against the other exactly as a recipient would — with no
knowledge of our code beyond the published public key.

The failure this guards is subtle and would be invisible to a signer unit test:
the signature must cover the manifest bytes AS SHIPPED. If anything re-serializes
the manifest between signing and archiving (a re-ordered key, a re-rendered
float, a trailing newline), the packet ships a signature that does not verify,
and a carrier discovers it instead of us.
"""

from __future__ import annotations

import asyncio
import base64
import sys
import tarfile
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[3]))

from adapter.evidence import signing  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)

pytest.importorskip(
    "cryptography",
    reason="packet signing requires the cryptography package; CI installs it",
)

from cryptography.exceptions import InvalidSignature  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
from cryptography.hazmat.primitives import serialization  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    Ed25519PrivateKey,
)

from .test_packet import _build_pair, _request, _seed_audit_row, _write_customer_yaml  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)


def _key_env(monkeypatch) -> Ed25519PrivateKey:
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    monkeypatch.setenv(signing.SIGNING_KEY_ENV, base64.b64encode(pem).decode())
    return key


def _build(tmp_path) -> Path:
    builder, conn = _build_pair(tmp_path)
    _seed_audit_row(
        conn,
        id="01HZZ0000000000000000000S1",
        ts="2026-04-15T10:00:00.000Z",
        action_type="DRAFT_CREATED",
        actor="agent",
        skill_name="inbox-triage",
    )
    customer_yaml = _write_customer_yaml(tmp_path, {"customer_name": "Acme"})
    request = _request(tmp_path, customer_yaml)
    asyncio.run(builder.build(request))
    return request.output_path


def _extract(archive: Path, name: str) -> bytes | None:
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            if Path(member.name).name == name:
                handle = tar.extractfile(member)
                return handle.read() if handle else None
    return None


def test_shipped_signature_verifies_against_shipped_manifest(tmp_path, monkeypatch):
    """The signature covers the manifest bytes exactly as archived."""
    key = _key_env(monkeypatch)
    archive = _build(tmp_path)

    manifest_bytes = _extract(archive, "manifest.json")
    signature = _extract(archive, signing.DETACHED_SIGNATURE_FILENAME)
    assert manifest_bytes is not None, "packet has no manifest.json"
    assert signature is not None, "signing configured but packet has no manifest.sig"

    # Raises InvalidSignature on failure; returns None on success.
    key.public_key().verify(signature, manifest_bytes)


def test_a_tampered_manifest_fails_verification(tmp_path, monkeypatch):
    """Altering the shipped manifest breaks the signature. The whole point."""
    key = _key_env(monkeypatch)
    archive = _build(tmp_path)

    manifest_bytes = _extract(archive, "manifest.json")
    signature = _extract(archive, signing.DETACHED_SIGNATURE_FILENAME)

    with pytest.raises(InvalidSignature):
        key.public_key().verify(signature, manifest_bytes.replace(b"acme", b"acmf"))


def test_manifest_declares_the_algorithm_and_key_id_it_was_signed_with(
    tmp_path, monkeypatch
):
    """A reader can tell which key to fetch, from the packet alone."""
    import json

    key = _key_env(monkeypatch)
    archive = _build(tmp_path)
    body = json.loads(_extract(archive, "manifest.json"))

    block = body["signer"]
    assert block["algorithm"] == signing.ALGORITHM_ED25519
    assert block["signature"] == signing.SIGNATURE_DETACHED_MARKER

    published_der = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    assert block["key_id"] == signing.key_id_from_public_der(published_der)


def test_signature_is_absent_from_file_hashes(tmp_path, monkeypatch):
    """manifest.sig cannot hash itself; the trust order is sig -> manifest ->
    everything else."""
    import json

    _key_env(monkeypatch)
    archive = _build(tmp_path)
    body = json.loads(_extract(archive, "manifest.json"))

    assert signing.DETACHED_SIGNATURE_FILENAME not in body["file_hashes"]


def test_no_key_configured_ships_no_signature_file_and_says_so(tmp_path, monkeypatch):
    """An unsigned packet is honest. It must not carry an empty or bogus
    manifest.sig, and must disclose the unsigned state."""
    import json

    monkeypatch.delenv(signing.SIGNING_KEY_ENV, raising=False)
    archive = _build(tmp_path)

    assert _extract(archive, signing.DETACHED_SIGNATURE_FILENAME) is None
    body = json.loads(_extract(archive, "manifest.json"))
    assert body["signer"]["signature"] == signing.UNSIGNED


def test_a_configured_but_broken_key_halts_the_build(tmp_path, monkeypatch):
    """Never degrade to unsigned. Whoever set the key asked for a signature."""
    monkeypatch.setenv(signing.SIGNING_KEY_ENV, "not-valid-base64!!")

    with pytest.raises(signing.EvidenceSigningError):
        _build(tmp_path)
