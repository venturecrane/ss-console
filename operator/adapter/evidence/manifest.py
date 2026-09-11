"""Evidence-packet manifest.json builder + the signature block.

The manifest is the deterministic index of every file in the packet,
their sha256 digests, and the signature block. Per spec
(``compliance-evidence-packet.md`` §manifest.json), the manifest carries:

* ``customer_slug``
* ``period_start`` / ``period_end`` (ISO 8601)
* ``generated_at`` (ISO 8601)
* ``signer`` — name, email, key_id, algorithm, and the signature value
* ``file_hashes`` — every packet entry's sha256
* ``packet_version``

THE PACKET SIGNS AS THE ENTITY, NOT AS A PERSON (Captain decision,
2026-08-13; ss-console #2122). The signer of record is **SMDurgan, LLC**.
The reason is not cosmetic. A firm hands this packet to its malpractice
carrier or to opposing counsel years after the engagement ends, and what
that recipient needs to confirm is that the company under contract with
the firm produced it. A named individual's signature would decay the
moment staffing changed, and it would misstate who bears the obligation:
the agreement is with the entity. The person who RAN the export is still
recorded, separately and correctly, in ``generated_by`` — that is an
operator-attribution fact, not a statement of provenance.

That split is why ``packet_version`` moved to 1.1 and the block is named
``signer`` rather than the old ``captain_signature``: a reader must not
have to guess whether the name inside is the author, the operator, or the
guarantor.

Signing itself is optional and self-disclosing. With no key configured,
``signature`` carries the literal ``"unsigned-stub"`` and the packet says
plainly, on its face, that it is not cryptographically verifiable. With a
key configured, ``adapter/evidence/signing.py`` produces a detached
Ed25519 signature and the block names the key id. The public half is
published at https://smd.services/trust so verification needs no
credential and no contact with us.

The manifest sha256 is the canonical handle: PDF references it, the
packet generation audit row carries it in metadata, the receipt the
caller emails to outside counsel quotes it. Compute via
:func:`manifest_sha256_hex`.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping, Optional

#: 1.1 renamed ``captain_signature`` to ``signer`` and moved the signer of
#: record from a named person to SMDurgan, LLC. A reader who sees 1.0 is
#: holding a packet whose signature block names an individual.
PACKET_VERSION = "1.1"
SIGNATURE_STUB = "unsigned-stub"

#: The entity that signs every evidence packet. Not configurable per run: the
#: signer is the party under contract with the firm, and a packet that could
#: name someone else would be describing an obligation nobody holds. The env
#: override below exists for test fixtures and for a future entity rename, not
#: for per-packet attribution (that is ``generated_by``).
SIGNER_NAME = "SMDurgan, LLC"
SIGNER_EMAIL = "team@smd.services"

#: Recorded in ``signer.algorithm`` when no key is configured. The
#: historical literal was ``"stub-noop"``; it is kept for byte-compatibility
#: with packets generated before ss-console #2122 wired real signing.
SIGNATURE_ALGORITHM_UNSIGNED = "stub-noop"


def _iso_utc(now: Optional[datetime] = None) -> str:
    dt = now if now is not None else datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


@dataclass(frozen=True)
class EvidenceManifest:
    """The structured manifest body. Serialize via :meth:`to_bytes`.

    The dataclass is frozen because the manifest must be byte-stable:
    the PDF cites :func:`manifest_sha256_hex` of these exact bytes, and
    any mutation invalidates the citation. Re-build a new manifest
    rather than mutating.
    """

    customer_slug: str
    period_start: str
    period_end: str
    generated_at: str
    signer_name: str
    signer_email: str
    signer_key_id: str
    file_hashes: Mapping[str, str]
    actor: str
    actor_role: str
    matter: str
    packet_version: str = PACKET_VERSION
    signature: str = SIGNATURE_STUB
    signature_algorithm: str = SIGNATURE_ALGORITHM_UNSIGNED
    extra: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        body: dict = {
            "customer_slug": self.customer_slug,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "matter": self.matter,
            "generated_at": self.generated_at,
            "generated_by": {
                "actor": self.actor,
                "actor_role": self.actor_role,
            },
            "signer": {
                "name": self.signer_name,
                "email": self.signer_email,
                "key_id": self.signer_key_id,
                "signature": self.signature,
                "algorithm": self.signature_algorithm,
            },
            "file_hashes": {k: self.file_hashes[k] for k in sorted(self.file_hashes)},
            "packet_version": self.packet_version,
        }
        if self.extra:
            body["extra"] = dict(self.extra)
        return body

    def to_bytes(self) -> bytes:
        """Canonical JSON bytes: sorted keys, no whitespace artifacts.

        The sort + separator choice mirrors :mod:`adapter.audit_log` so
        any future signature implementation sees the same bytes the
        sha256-cite path saw.
        """
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")


def manifest_sha256_hex(manifest: EvidenceManifest) -> str:
    """Return the sha256 of the canonical manifest bytes (hex)."""
    return hashlib.sha256(manifest.to_bytes()).hexdigest()


def build_manifest(
    *,
    customer_slug: str,
    matter: str,
    period_start: str,
    period_end: str,
    file_hashes: Mapping[str, str],
    actor: str,
    actor_role: str,
    signer_name: Optional[str] = None,
    signer_email: Optional[str] = None,
    signer_key_id: Optional[str] = None,
    generated_at: Optional[str] = None,
    extra: Optional[Mapping[str, object]] = None,
    signature: Optional[str] = None,
    signature_algorithm: Optional[str] = None,
) -> EvidenceManifest:
    """Construct an :class:`EvidenceManifest` from packet inputs.

    The signer defaults to the entity (:data:`SIGNER_NAME`), never to a
    person. ``EVIDENCE_SIGNER_NAME`` / ``EVIDENCE_SIGNER_EMAIL`` exist for
    fixtures and for a future entity rename; they are not a per-packet
    attribution knob, because attribution is ``generated_by``.

    ``signer_key_id`` is supplied by the caller once a signing key is
    resolved. With no key it falls back to ``"unconfigured"``, matching
    ``signing.py``'s unsigned result, so the manifest never implies a key
    that does not exist.
    """
    return EvidenceManifest(
        customer_slug=customer_slug,
        matter=matter,
        period_start=period_start,
        period_end=period_end,
        generated_at=generated_at or _iso_utc(),
        signer_name=signer_name or os.environ.get("EVIDENCE_SIGNER_NAME", SIGNER_NAME),
        signer_email=signer_email or os.environ.get("EVIDENCE_SIGNER_EMAIL", SIGNER_EMAIL),
        signer_key_id=signer_key_id or os.environ.get("EVIDENCE_SIGNER_KEY_ID", "unconfigured"),
        file_hashes=dict(file_hashes),
        actor=actor,
        actor_role=actor_role,
        signature=signature or SIGNATURE_STUB,
        signature_algorithm=signature_algorithm or SIGNATURE_ALGORITHM_UNSIGNED,
        extra=dict(extra or {}),
    )


__all__ = [
    "EvidenceManifest",
    "PACKET_VERSION",
    "SIGNATURE_STUB",
    "SIGNER_NAME",
    "SIGNER_EMAIL",
    "build_manifest",
    "manifest_sha256_hex",
]
