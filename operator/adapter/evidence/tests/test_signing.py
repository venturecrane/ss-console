"""Detached Ed25519 signing for the evidence packet (ss-console #2122).

The load-bearing property is the one a recipient exercises: a signature
produced here verifies against the PUBLISHED public key, using a plain
``openssl``-equivalent path and no knowledge of our code. Everything else in
this file guards the failure modes that would put a false integrity claim into
a legal record.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[3]))  # operator/ on sys.path

from adapter.evidence import signing  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)

cryptography = pytest.importorskip(
    "cryptography",
    reason="signing requires the cryptography package; CI installs it",
)

from cryptography.hazmat.primitives import serialization  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    Ed25519PrivateKey,
)

PAYLOAD = b'{"customer_slug":"acme","packet_version":"1.0"}'


def _fresh_key_b64() -> tuple[str, Ed25519PrivateKey]:
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.b64encode(pem).decode(), key


# ---------------------------------------------------------------------------
# The property a carrier actually exercises
# ---------------------------------------------------------------------------


def test_signature_verifies_against_the_public_key():
    """A recipient holding only the public key can verify the payload."""
    key_b64, key = _fresh_key_b64()
    result = signing.sign_manifest_bytes(PAYLOAD, env={signing.SIGNING_KEY_ENV: key_b64})

    assert result.signed is True
    assert result.algorithm == signing.ALGORITHM_ED25519
    # Verification raises on failure and returns None on success.
    key.public_key().verify(result.signature, PAYLOAD)


def test_signature_rejects_a_tampered_payload():
    """One altered byte breaks verification. This is the whole point."""
    from cryptography.exceptions import InvalidSignature

    key_b64, key = _fresh_key_b64()
    result = signing.sign_manifest_bytes(PAYLOAD, env={signing.SIGNING_KEY_ENV: key_b64})

    with pytest.raises(InvalidSignature):
        key.public_key().verify(result.signature, PAYLOAD + b" ")


def test_key_id_is_derivable_from_the_published_public_key():
    """A reader can confirm the packet names the key they hold, from the
    published PEM alone, without contacting us."""
    key_b64, key = _fresh_key_b64()
    result = signing.sign_manifest_bytes(PAYLOAD, env={signing.SIGNING_KEY_ENV: key_b64})

    published_der = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    assert result.key_id == signing.key_id_from_public_der(published_der)


# ---------------------------------------------------------------------------
# Never silently unsigned
# ---------------------------------------------------------------------------


def test_no_key_configured_is_an_honest_unsigned_result():
    result = signing.sign_manifest_bytes(PAYLOAD, env={})

    assert result.signed is False
    assert result.signature is None
    assert result.manifest_signature_value == signing.UNSIGNED
    assert result.algorithm == signing.ALGORITHM_UNSIGNED
    assert result.detached_filename is None


@pytest.mark.parametrize(
    "bad_value",
    [
        "not-base64!!",
        base64.b64encode(b"-----BEGIN PUBLIC KEY-----\nnope\n").decode(),
        base64.b64encode(b"").decode() or "AA==",
    ],
)
def test_a_configured_but_unusable_key_raises_rather_than_degrading(bad_value):
    """Whoever set the key asked for a signed packet. A malformed key must
    halt the build, never quietly produce an unsigned one."""
    with pytest.raises(signing.EvidenceSigningError):
        signing.sign_manifest_bytes(PAYLOAD, env={signing.SIGNING_KEY_ENV: bad_value})


def test_a_non_ed25519_key_raises():
    """An RSA key would produce a signature no recipient can check against the
    published Ed25519 verification key."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = rsa_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    with pytest.raises(signing.EvidenceSigningError, match="Ed25519"):
        signing.sign_manifest_bytes(PAYLOAD, env={signing.SIGNING_KEY_ENV: base64.b64encode(pem).decode()})


def test_signing_configured_reports_presence():
    assert signing.signing_configured(env={}) is False
    assert signing.signing_configured(env={signing.SIGNING_KEY_ENV: "   "}) is False
    assert signing.signing_configured(env={signing.SIGNING_KEY_ENV: "x"}) is True


# ---------------------------------------------------------------------------
# The circularity guard
# ---------------------------------------------------------------------------


def test_manifest_value_is_a_pointer_never_the_signature():
    """The signature value must not be embedded in the bytes it signs.

    ``manifest.json`` carries a signature block, so the manifest records a
    POINTER to the detached file. Embedding the signature would sign a
    document that no longer exists and would invalidate the manifest sha256
    that the PDF cites.
    """
    key_b64, _ = _fresh_key_b64()
    result = signing.sign_manifest_bytes(PAYLOAD, env={signing.SIGNING_KEY_ENV: key_b64})

    assert result.manifest_signature_value == "detached:manifest.sig"
    assert result.detached_filename == signing.DETACHED_SIGNATURE_FILENAME
    encoded = base64.b64encode(result.signature).decode()
    assert encoded not in result.manifest_signature_value
