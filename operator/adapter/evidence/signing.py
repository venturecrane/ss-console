"""Detached Ed25519 signing for the compliance evidence packet (ss-console #2122).

WHAT THE SIGNATURE IS FOR. The packet's per-artifact SHA-256 digests and the
manifest hash prove internal consistency: the files are the files the manifest
says they are. They prove nothing about *origin*. A firm handing a packet to its
malpractice carrier is otherwise asking to be taken at its word about its own
evidence. The detached signature closes that: the carrier fetches SMD's
published public key and confirms the manifest was produced by SMD and has not
been altered since export. It protects the FIRM from the accusation that it
doctored its own record, which is why it matters more to them than to us.

WHAT IT DOES NOT COVER. Nothing about whether the underlying audit log is
*correct*, only that the packet is unaltered since export. Tamper evidence
*within* the ledger is the hash chain's job (``workspace_broker/chain.py``,
walked by ``bin/verify-audit-chain.py``). Two different guarantees; the packet
states both separately and must never blur them.

WHY DETACHED, AND THE CIRCULARITY IT AVOIDS
-------------------------------------------

The signature cannot live inside the bytes it signs. ``manifest.json`` embeds a
signature block, so signing the serialized manifest and then writing the result
back into that block would produce a signature over a document that no longer
exists — and would invalidate ``manifest_sha256_hex``, which the PDF cites
verbatim and the ``COMPLIANCE_PACKET_EXPORTED`` audit row records.

So the signature ships as a SEPARATE packet entry, ``manifest.sig``, over the
exact canonical bytes of ``manifest.json``. The embedded block still declares
the algorithm, the key id, and where the detached signature lives — that
metadata is inside the signed bytes, which is correct and desirable. Only the
signature VALUE is outside.

``manifest.sig`` is deliberately absent from the manifest's ``file_hashes`` map,
for the same reason: it cannot hash itself. The trust order is
``manifest.sig`` -> ``manifest.json`` -> every other artifact.

FAIL LOUD, NEVER SILENTLY UNSIGNED
----------------------------------

An unsigned packet is an honest artifact; a packet that *believes* it is signed
and is not is a lie in a legal record. So there are exactly two outcomes:

* No key configured -> :data:`UNSIGNED`, and the packet discloses it. This is
  the pre-#2122 behaviour and stays legitimate for local and dev builds.
* A key IS configured -> it must load and sign, or the build RAISES. A
  malformed key, a missing ``cryptography`` install, or a signing failure is
  never degraded to "unsigned". Whoever set the key asked for a signed packet.

KEY MATERIAL. ``EVIDENCE_PACKET_SIGNING_KEY_B64`` holds base64 of a PKCS#8 PEM
Ed25519 private key, at Infisical ``/ss`` (the flat ``*_SIGNING_KEY`` naming the
path already uses for ``ASSESSMENT_SESSION_SIGNING_KEY`` /
``OAUTH_STATE_SIGNING_KEY``; the spec's older ``/captain/signing-key`` path
predates that convention). The private half never leaves the vault and the
signing host. The public half is published at
``public/keys/evidence-packet-signing-key.pem`` so verification needs no
credential and no contact with us.

KEY ID is the SHA-256 of the DER-encoded PUBLIC key, so it is derivable from the
published file alone and a reader can confirm the packet names the key they
hold. It is not a secret and appears in the manifest in the clear.
"""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from typing import Optional

#: Env var holding base64 of the PKCS#8 PEM Ed25519 private key.
SIGNING_KEY_ENV = "EVIDENCE_PACKET_SIGNING_KEY_B64"

#: Packet entry that carries the detached signature.
DETACHED_SIGNATURE_FILENAME = "manifest.sig"

#: Algorithm recorded in the manifest when a real signature is produced.
ALGORITHM_ED25519 = "ed25519"

#: Algorithm recorded when no key is configured. Distinct from the legacy
#: ``"stub-noop"`` only in that it is paired with an explicit disclosure.
ALGORITHM_UNSIGNED = "unsigned"

#: Manifest ``signature`` value when no key is configured. Retained verbatim
#: from the pre-#2122 builder so existing honesty gates keep matching.
UNSIGNED = "unsigned-stub"

#: Manifest ``signature`` value when a detached signature IS produced. A
#: pointer, never the signature bytes — see the circularity note above.
SIGNATURE_DETACHED_MARKER = f"detached:{DETACHED_SIGNATURE_FILENAME}"


class EvidenceSigningError(RuntimeError):
    """A signing key was configured but a signature could not be produced.

    Raised rather than degrading to unsigned. The caller is expected to halt
    the packet build: whoever configured the key asked for a signed packet, and
    quietly shipping an unsigned one would misrepresent the artifact.
    """


@dataclass(frozen=True)
class SigningResult:
    """Outcome of a signing attempt over the canonical manifest bytes.

    ``signature`` is the raw detached signature to be written to
    :data:`DETACHED_SIGNATURE_FILENAME`, or ``None`` when unsigned.
    """

    signed: bool
    algorithm: str
    key_id: str
    manifest_signature_value: str
    signature: Optional[bytes] = None

    @property
    def detached_filename(self) -> Optional[str]:
        return DETACHED_SIGNATURE_FILENAME if self.signed else None


def _unsigned() -> SigningResult:
    return SigningResult(
        signed=False,
        algorithm=ALGORITHM_UNSIGNED,
        key_id=os.environ.get("EVIDENCE_SIGNER_KEY_ID", "unconfigured"),
        manifest_signature_value=UNSIGNED,
        signature=None,
    )


def key_id_from_public_der(public_der: bytes) -> str:
    """SHA-256 hex of the DER public key. Derivable from the published PEM."""
    return hashlib.sha256(public_der).hexdigest()


def signing_configured(env: Optional[dict] = None) -> bool:
    """True when a signing key is present in the environment."""
    source = os.environ if env is None else env
    return bool((source.get(SIGNING_KEY_ENV) or "").strip())


@dataclass(frozen=True)
class Signer:
    """A loaded signing key, plus the metadata the manifest must carry.

    Exists because of an ordering constraint: ``algorithm`` and ``key_id`` are
    recorded INSIDE ``manifest.json``, so they must be known before the
    manifest is serialized — but the signature is taken OVER those serialized
    bytes. Resolving the key first yields the metadata; :meth:`sign` is then
    called on the finished bytes.
    """

    algorithm: str
    key_id: str
    _key: object

    def sign(self, payload: bytes) -> bytes:
        try:
            return self._key.sign(payload)  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover - defensive
            raise EvidenceSigningError(f"Ed25519 signing failed: {exc}") from exc


def load_signer(env: Optional[dict] = None) -> Optional[Signer]:
    """Load the configured signing key, or ``None`` when none is configured.

    Raises :class:`EvidenceSigningError` when a key IS configured but unusable.
    Never returns ``None`` to mean "configured but broken".
    """
    source = os.environ if env is None else env
    raw = (source.get(SIGNING_KEY_ENV) or "").strip()
    if not raw:
        return None
    key, public_der = _load_key(raw)
    return Signer(
        algorithm=ALGORITHM_ED25519,
        key_id=key_id_from_public_der(public_der),
        _key=key,
    )


def sign_manifest_bytes(payload: bytes, env: Optional[dict] = None) -> SigningResult:
    """Sign canonical manifest bytes, or report an honest unsigned result.

    Returns an unsigned :class:`SigningResult` when no key is configured.
    Raises :class:`EvidenceSigningError` when a key IS configured but the
    signature cannot be produced — never degrades to unsigned.
    """
    signer = load_signer(env)
    if signer is None:
        return _unsigned()
    return SigningResult(
        signed=True,
        algorithm=signer.algorithm,
        key_id=signer.key_id,
        manifest_signature_value=SIGNATURE_DETACHED_MARKER,
        signature=signer.sign(payload),
    )


def _load_key(raw: str):
    """Decode and validate the configured key. Returns (key, public_der)."""
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise EvidenceSigningError(
            f"{SIGNING_KEY_ENV} is set but the 'cryptography' package is not "
            "installed, so the packet cannot be signed. Install it or unset "
            "the key; shipping an unsigned packet that claims to be signed is "
            "not an option."
        ) from exc

    try:
        pem = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise EvidenceSigningError(
            f"{SIGNING_KEY_ENV} is not valid base64; expected base64 of a PKCS#8 PEM Ed25519 private key."
        ) from exc

    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except Exception as exc:
        raise EvidenceSigningError(f"{SIGNING_KEY_ENV} did not decode to a usable PEM private key.") from exc

    if not isinstance(key, Ed25519PrivateKey):
        raise EvidenceSigningError(
            f"{SIGNING_KEY_ENV} is not an Ed25519 key (got "
            f"{type(key).__name__}). The published verification key is "
            "Ed25519; a mismatched algorithm would produce a signature no "
            "recipient can check."
        )

    try:
        public_der = key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    except Exception as exc:  # pragma: no cover - defensive
        raise EvidenceSigningError(f"could not derive the public key for {SIGNING_KEY_ENV}: {exc}") from exc

    return key, public_der
