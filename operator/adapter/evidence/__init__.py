"""Compliance evidence packet generation (issue #894).

Composes the per-customer audit_log, memory tables, voice tables,
customer.yaml, skill_state, and invariant_boot_checks dumps into a
single digest-verified tar.gz packet (per-artifact SHA-256 + a manifest
SHA-256 recorded in the append-only audit log) per spec
``docs/specs/operator/compliance-evidence-packet.md``. The manifest
is not yet cryptographically signed (``signature="unsigned-stub"``);
detached Ed25519 signing is a tracked follow-on gated on provisioning
the Captain signing key.

The public entrypoint is :class:`EvidencePacketBuilder.build`. The
CLI wrapper at ``operator/bin/generate-evidence-packet.sh`` calls
into :mod:`bin.lib.evidence`, which constructs the builder against a
real D1 binding or a sqlite executor (for tests).
"""

from __future__ import annotations

from .manifest import EvidenceManifest, build_manifest, manifest_sha256_hex
from .packet import (
    AuditCoverage,
    CHAIN_PIN_SOURCE,
    ChainPin,
    EvidencePacketBuilder,
    EvidencePacketError,
    EvidencePacketResult,
    PacketActor,
    PacketRequest,
    REQUIRED_ACTOR_ROLES,
    redact_customer_yaml,
)
from .pdf import render_summary_pdf

__all__ = [
    "AuditCoverage",
    "CHAIN_PIN_SOURCE",
    "ChainPin",
    "EvidenceManifest",
    "EvidencePacketBuilder",
    "EvidencePacketError",
    "EvidencePacketResult",
    "PacketActor",
    "PacketRequest",
    "REQUIRED_ACTOR_ROLES",
    "build_manifest",
    "manifest_sha256_hex",
    "redact_customer_yaml",
    "render_summary_pdf",
]
