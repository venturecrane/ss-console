"""Compliance evidence packet builder (issue #894).

Builds a tar.gz packet containing:

* ``00-README.md`` -- plain-language overview
* ``01-summary.pdf`` -- Susan-readable narrative (see :mod:`.pdf`)
* ``03-audit-log.csv`` -- structured audit_log dump for the period
* ``05-customer-yaml.redacted.yml`` -- customer config, secrets redacted
* ``06-memory-snapshot.json`` -- memory rules + person mappings + voice
  metadata (no sample content; spec keeps it on a separate signed path)
* ``07-skill-catalog.json`` -- skills active during the period
* ``09-boot-checks.csv`` -- invariant_boot_checks dump
* ``manifest.json`` -- file hashes + Captain signature stub

The full spec lists additional documents (engagement-letter clauses,
DPA, BAA, decommission confirmation). Those are static or per-customer
documents not under the per-customer D1, so they are out of scope for
the runtime in this PR -- the packet structure leaves room to drop them
in without manifest/PDF rewrites.

Layout
------

Three modules, one direction of dependency. :mod:`.packet_sources` is the
read side: the read-executor protocol, the coverage model
(:class:`AuditCoverage`, :class:`ChainPin`) and the ``_fetch_*`` reads.
:mod:`.packet_render` is the write side: the README and the three dump
serializers. This module holds the request and result types, the
customer.yaml redaction, and :class:`EvidencePacketBuilder`, which is the
only place the two sides meet. The public names below are re-exported
here so callers import one module.

Composition
-----------

The builder talks to D1 through a read executor protocol (mirrors the
write executor in :mod:`adapter.audit_log`). Tests pass a sqlite-backed
implementation; production wires the HTTP D1 client.

Every successful build emits one ``COMPLIANCE_PACKET_EXPORTED`` row to
the audit log writer the caller supplied. That row is the
chain-of-custody artifact for the export itself, with the manifest
sha256 in metadata so a reviewer can verify packet identity at any time.

Role gate
---------

The CLI accepts an ``--actor`` flag; the builder verifies the actor's
role is in :data:`REQUIRED_ACTOR_ROLES` (``captain`` or ``compliance``).
A missing or wrong role aborts the build before any D1 read and before
any audit row.

No-fabrication contract
-----------------------

When a table has no rows for the period, the JSON / CSV dump is the
empty case (``[]`` or header-only CSV). The PDF narrative reports the
literal count (``0``), not a soft phrase that implies the agent did
something it did not. ``customer.yaml`` missing on disk is an error,
not a placeholder: callers must point at a real file.

An empty section is itself a claim
----------------------------------

A zero has two meanings and an auditor cannot tell them apart from the
zero alone: "nothing happened" and "this system cannot answer that
question". ``matter_ref`` was added to the audit schema after seats had
already begun writing rows, and the emitter did not populate it at
first, so rows written before that fix carry ``matter_ref = NULL``
permanently. There is no key to backfill them from.

A matter-scoped export therefore has a coverage boundary, and
:class:`AuditCoverage` computes it on every build. Three outcomes:

* **Answerable zero.** No row matches the matter and no row in the
  period lacks attribution. The packet states that the zero is
  complete.
* **Unanswerable zero.** No row matches the matter and one or more rows
  in the period carry no attribution at all. The build REFUSES
  (:class:`EvidencePacketError`) rather than ship an empty audit
  section that reads as "nothing happened". This follows the
  empty-state discipline in ``docs/style/empty-state-pattern.md``,
  whose legal-document precedent is to block generation rather than
  render a plausible-looking gap. ``--acknowledge-unattributed-gap``
  overrides the refusal; it does NOT suppress the disclosure, and the
  acknowledgement is recorded in the manifest and the audit row.
* **Partial coverage.** Rows match the matter AND other rows in the
  period lack attribution. The packet builds and states, on its face,
  how many rows it could not scope either way.

Unattributed rows are never included in a matter-scoped packet: they
may concern other clients. The packet discloses their count and their
time span, not their contents.

The secret-redaction pass walks the parsed YAML and replaces every
``token_ref``, ``oauth_scopes``, and ``failure_recipients`` /
``red_flag_recipients`` value. A pre-export validator scans the
redacted output for residual secret patterns and aborts if any leak
through (``EvidencePacketError`` raised before the packet writes).
"""

from __future__ import annotations

import enum
import hashlib
import io
import json
import re
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from .manifest import EvidenceManifest, build_manifest, manifest_sha256_hex
from .signing import (
    DETACHED_SIGNATURE_FILENAME,
    SIGNATURE_DETACHED_MARKER,
    load_signer,
)
from .pdf import render_summary_pdf
from .packet_render import _readme_text, _render_yaml, _rows_to_csv, _rows_to_json
from .packet_sources import (
    _AUDIT_LOG_COLUMNS,
    _BOOT_CHECK_COLUMNS,
    _CHAIN_HEAD_RE,
    CHAIN_PIN_SOURCE,
    AuditCoverage,
    ChainPin,
    ReadExecutor,
    SqliteReadExecutor,
    _chain_pin_refusal_message,
    _coverage_refusal_message,
    _fetch_audit_coverage,
    _fetch_audit_log,
    _fetch_boot_checks,
    _fetch_chain_pin,
    _fetch_memory_snapshot,
    _fetch_skill_catalog,
)

REQUIRED_ACTOR_ROLES = frozenset({"captain", "compliance"})

# Phrases / shapes the redactor walks customer.yaml for.
_SECRET_KEY_PATTERN = re.compile(
    r"(?i)^(token_ref|api_key|secret|password|client_secret|signing_key|"
    r"refresh_token|access_token|private_key)$"
)
_OAUTH_KEY_PATTERN = re.compile(r"(?i)^oauth_scopes$")
_RECIPIENT_KEY_PATTERN = re.compile(r"(?i)^(failure_recipients|red_flag_recipients|notification_recipients)$")

# Pre-export validator: a redacted file must NOT contain these patterns.
_SECRET_VALUE_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),  # OpenAI / Anthropic style secret
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),  # Google API key
    re.compile(r"xox[abposr]-[A-Za-z0-9-]{10,}"),  # Slack token
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]


class EvidencePacketError(RuntimeError):
    """Raised on any unrecoverable packet-build failure.

    The caller (CLI or dashboard worker) should surface this verbatim to
    the operator. Partial outputs are NOT written: the builder writes
    to a temp path and only renames into place on success.
    """


class PacketActor(str, enum.Enum):
    """Subset of :class:`adapter.audit_log.ActorRole` that may invoke."""

    CAPTAIN = "captain"
    COMPLIANCE = "compliance"


@dataclass(frozen=True)
class PacketRequest:
    """Inputs that fully specify a packet build.

    ``matter`` may be a specific matter ID or the string ``"all"`` (the
    spec lets the caller scope the export by matter or by period).
    ``customer_yaml_path`` is the on-disk yaml for the customer; the
    builder reads it once and includes the redacted form in the packet.

    ``acknowledge_unattributed_gap`` overrides the refusal a matter-scoped
    export raises when it matches zero rows while unattributed rows exist
    in the period (see :class:`AuditCoverage`). It does not change what
    the packet says: the gap is disclosed either way, and the
    acknowledgement itself is recorded in the manifest and the
    ``COMPLIANCE_PACKET_EXPORTED`` audit row.

    ``pinned_head`` is a chain head recorded OFF the Machine before this export
    (ss#2500). Supplying one turns the packet's audit section from "these rows
    are internally consistent" into "these rows still contain a head recorded at
    a moment nobody on the Machine could reach". Without it, tail truncation is
    invisible -- and the packet says so on its face rather than implying a
    completeness it did not test.
    """

    customer_slug: str
    matter: str
    period_start: str
    period_end: str
    output_path: Path
    customer_yaml_path: Path
    actor: str
    actor_role: PacketActor
    acknowledge_unattributed_gap: bool = False
    pinned_head: Optional[str] = None

    def validate(self) -> None:
        if not self.customer_slug:
            raise EvidencePacketError("customer_slug must be non-empty")
        if not self.matter:
            raise EvidencePacketError("matter must be a specific id or 'all'; never empty")
        if not _is_iso8601(self.period_start) or not _is_iso8601(self.period_end):
            raise EvidencePacketError("period_start / period_end must be ISO 8601 strings")
        if self.period_end < self.period_start:
            raise EvidencePacketError("period_end must be >= period_start")
        if not isinstance(self.actor_role, PacketActor):
            raise EvidencePacketError("actor_role must be a PacketActor (captain | compliance)")
        if self.actor_role.value not in REQUIRED_ACTOR_ROLES:
            raise EvidencePacketError(f"actor_role {self.actor_role.value!r} not in {sorted(REQUIRED_ACTOR_ROLES)}")
        if self.pinned_head is not None and not _CHAIN_HEAD_RE.match(self.pinned_head):
            # Refused here rather than reported as a missing head later. A
            # malformed pin can never match any row, so carrying it forward
            # would print "the record was truncated" on a packet about a healthy
            # ledger -- a false accusation in a document written for a court.
            raise EvidencePacketError(
                "pinned_head must be a sha256 hexdigest (64 lowercase hex "
                "characters), the shape row_hash takes in the audit ledger"
            )


@dataclass
class EvidencePacketResult:
    """What the build returns to the caller."""

    output_path: Path
    manifest_sha256: str
    file_count: int
    bytes_written: int
    counts: Mapping[str, int]
    manifest: EvidenceManifest
    coverage: "AuditCoverage"
    # ss#2500. Present even when no pin was supplied, carrying checked=False, so
    # a caller reading the result cannot mistake "not asked" for "asked and fine".
    chain_pin: "ChainPin" = field(
        default_factory=lambda: ChainPin(pinned_head=None, present=False, chain_readable=True, source=CHAIN_PIN_SOURCE)
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_ISO_8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,3})?Z?$")


def _is_iso8601(value: str) -> bool:
    return bool(value) and bool(_ISO_8601_RE.match(value))


def _iso_utc(now: Optional[datetime] = None) -> str:
    dt = now if now is not None else datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# customer.yaml redaction
# ---------------------------------------------------------------------------


def _redact_email(email: str) -> str:
    """Replace local-part; keep domain so the structure is auditable."""
    if "@" in email:
        _, domain = email.split("@", 1)
        return f"<redacted>@{domain}"
    return "<redacted>"


def redact_customer_yaml(parsed: Any) -> Any:
    """Walk a parsed-yaml structure and redact sensitive fields.

    Returns a NEW structure; the input is not mutated. The function is
    pure-Python (no PyYAML import here) so the test suite can pass it
    dicts directly and the CLI parses via yaml at call time.
    """
    if isinstance(parsed, dict):
        out: dict = {}
        for key, value in parsed.items():
            if isinstance(key, str) and _SECRET_KEY_PATTERN.match(key):
                out[key] = "<redacted>"
                continue
            if isinstance(key, str) and _OAUTH_KEY_PATTERN.match(key):
                count = len(value) if isinstance(value, (list, tuple, set)) else 1 if value else 0
                out[key] = f"<{count} scopes redacted>"
                continue
            if isinstance(key, str) and _RECIPIENT_KEY_PATTERN.match(key):
                if isinstance(value, list):
                    out[key] = [_redact_email(str(v)) for v in value]
                elif isinstance(value, str):
                    out[key] = _redact_email(value)
                else:
                    out[key] = "<redacted>"
                continue
            out[key] = redact_customer_yaml(value)
        return out
    if isinstance(parsed, list):
        return [redact_customer_yaml(item) for item in parsed]
    return parsed


def _scan_for_secret_leak(rendered: str) -> Optional[str]:
    """Return the first regex match (as a label) found in the rendered
    yaml, or None when clean."""
    for pattern in _SECRET_VALUE_PATTERNS:
        if pattern.search(rendered):
            return pattern.pattern
    return None


# ---------------------------------------------------------------------------
# Counts (used by both the PDF and the manifest extra block)
# ---------------------------------------------------------------------------


def _compute_counts(
    *,
    audit_rows: Sequence[dict],
    boot_check_rows: Sequence[dict],
    skill_rows: Sequence[dict],
    memory_snapshot: Mapping[str, Any],
) -> dict:
    """Tally the headline numbers the summary PDF reports."""

    def _count(action_type: str) -> int:
        return sum(1 for r in audit_rows if r.get("action_type") == action_type)

    return {
        "audit_events": len(audit_rows),
        "drafts_created": _count("DRAFT_CREATED"),
        "drafts_approved": _count("DRAFT_APPROVED"),
        "drafts_rejected": _count("DRAFT_REJECTED"),
        "memory_rule_events": (
            _count("MEMORY_RULE_ADDED") + _count("MEMORY_RULE_EDITED") + _count("MEMORY_RULE_DELETED")
        ),
        "skills_enabled": sum(1 for r in skill_rows if (r.get("trust_ceiling") or "").lower() != "refused"),
        "boot_checks": len(boot_check_rows),
        "invariant_violations": (_count("INVARIANT_VIOLATION") + _count("INVARIANT_BOOT_CHECK_FAILED")),
        "escalations": _count("ESCALATION_FIRED"),
        "memory_rules_in_snapshot": len(memory_snapshot.get("memory_rules", [])),
        "person_mappings_in_snapshot": len(memory_snapshot.get("person_mappings", [])),
        "voice_samples_metadata_rows": len(memory_snapshot.get("voice_samples_metadata", [])),
    }


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


@dataclass
class EvidencePacketBuilder:
    """Compose a digest-verified evidence packet for one customer + period.

    The manifest is NOT yet cryptographically signed -- it self-discloses
    ``signature="unsigned-stub"``; integrity rests on per-artifact SHA-256
    digests plus the manifest hash recorded in the append-only audit log.
    Detached signing is a tracked follow-on.

    Construction wires the read executor + audit writer + (optional)
    yaml parser. ``yaml_loader`` defaults to a minimal JSON-ish parser
    that accepts either JSON or simple YAML; production CLI passes
    ``yaml.safe_load`` directly.

    Call :meth:`build` to produce a tar.gz at ``request.output_path``.
    The builder writes to ``<output>.tmp``, fsyncs, and renames into
    place so partial files never appear on disk.
    """

    reader: ReadExecutor
    audit_writer: object  # adapter.audit_log.AuditLogWriter; kept loose
    yaml_loader: Optional[object] = None  # callable parsing yaml bytes
    yaml_dumper: Optional[object] = None  # callable dumping back to yaml

    async def build(self, request: PacketRequest) -> EvidencePacketResult:
        request.validate()

        customer_yaml_text = self._load_customer_yaml(request.customer_yaml_path)
        parsed_yaml = self._parse_yaml(customer_yaml_text)
        redacted_yaml = redact_customer_yaml(parsed_yaml)
        rendered_redacted_yaml = self._render_yaml(redacted_yaml)
        leak = _scan_for_secret_leak(rendered_redacted_yaml.decode("utf-8", "replace"))
        if leak is not None:
            raise EvidencePacketError(
                "redacted customer.yaml still contains a secret-shaped "
                f"value (pattern {leak!r}); aborting export. Update the "
                "redaction rules and re-run; do NOT modify the source yaml "
                "to bypass this guardrail."
            )

        customer_name = self._extract_customer_name(parsed_yaml, request.customer_slug)

        coverage = await _fetch_audit_coverage(
            self.reader,
            period_start=request.period_start,
            period_end=request.period_end,
            matter=request.matter,
            gap_acknowledged=request.acknowledge_unattributed_gap,
            actor=request.actor,
        )
        if coverage.is_unanswerable_empty and not coverage.gap_acknowledged:
            raise EvidencePacketError(_coverage_refusal_message(coverage))

        # ss#2500. Checked BEFORE any file is rendered, so a packet over a
        # truncated ledger never reaches disk in the first place.
        chain_pin = await _fetch_chain_pin(self.reader, pinned_head=request.pinned_head)
        if chain_pin.was_checked and not chain_pin.present:
            raise EvidencePacketError(_chain_pin_refusal_message(chain_pin))

        audit_rows = await _fetch_audit_log(
            self.reader,
            period_start=request.period_start,
            period_end=request.period_end,
            matter=request.matter,
        )
        boot_check_rows = await _fetch_boot_checks(
            self.reader,
            period_start=request.period_start,
            period_end=request.period_end,
        )
        memory_snapshot = await _fetch_memory_snapshot(self.reader)
        skill_rows = await _fetch_skill_catalog(
            self.reader,
            period_start=request.period_start,
            period_end=request.period_end,
        )

        counts = _compute_counts(
            audit_rows=audit_rows,
            boot_check_rows=boot_check_rows,
            skill_rows=skill_rows,
            memory_snapshot=memory_snapshot,
        )

        audit_csv = _rows_to_csv(audit_rows, _AUDIT_LOG_COLUMNS)
        boot_csv = _rows_to_csv(boot_check_rows, _BOOT_CHECK_COLUMNS)
        memory_json = _rows_to_json(memory_snapshot)
        skill_json = _rows_to_json(skill_rows)

        # Build a placeholder manifest first so the PDF can quote a
        # stable manifest-sha; we rebuild the manifest with the final
        # file hashes (including the PDF) after rendering.
        placeholder_hashes = {
            "00-README.md": _sha256(b""),
            "01-summary.pdf": _sha256(b""),
            "03-audit-log.csv": _sha256(audit_csv),
            "05-customer-yaml.redacted.yml": _sha256(rendered_redacted_yaml),
            "06-memory-snapshot.json": _sha256(memory_json),
            "07-skill-catalog.json": _sha256(skill_json),
            "09-boot-checks.csv": _sha256(boot_csv),
        }
        provisional_manifest = build_manifest(
            customer_slug=request.customer_slug,
            matter=request.matter,
            period_start=request.period_start,
            period_end=request.period_end,
            file_hashes=placeholder_hashes,
            actor=request.actor,
            actor_role=request.actor_role.value,
            extra={
                "counts": counts,
                "coverage": coverage.to_dict(),
                "chain_pin": chain_pin.to_dict(),
                "stage": "provisional",
            },
        )
        provisional_sha = manifest_sha256_hex(provisional_manifest)

        pdf_bytes = render_summary_pdf(
            customer_slug=request.customer_slug,
            customer_name=customer_name,
            period_start=request.period_start,
            period_end=request.period_end,
            matter=request.matter,
            signer_key_id=provisional_manifest.signer_key_id,
            manifest_sha256=provisional_sha,
            counts=counts,
            coverage_lines=coverage.narrative_lines(),
            counts_are_partial=coverage.has_unattributed_rows,
        )

        # Resolve the signing key BEFORE anything is rendered. Three artifacts
        # depend on knowing whether this packet will be signed: the README's
        # verification section, and the algorithm + key id recorded inside
        # manifest.json. The signature itself is taken later, over the
        # serialized manifest, and shipped detached (adapter/evidence/signing.py
        # explains why it cannot be embedded). load_signer raises rather than
        # degrading when a key is configured but unusable: an unsigned packet is
        # an honest artifact, a falsely-signed one is a lie in a legal record.
        signer = load_signer()

        readme_bytes = _readme_text(
            customer_slug=request.customer_slug,
            customer_name=customer_name,
            period_start=request.period_start,
            period_end=request.period_end,
            matter=request.matter,
            signer_name=provisional_manifest.signer_name,
            signer_email=provisional_manifest.signer_email,
            actor=request.actor,
            actor_role=request.actor_role.value,
            manifest_sha256=provisional_sha,
            coverage=coverage,
            chain_pin=chain_pin,
            signed=signer is not None,
            key_id=signer.key_id if signer else "",
        )

        # Final manifest with real file hashes (PDF + README included).
        file_hashes = {
            "00-README.md": _sha256(readme_bytes),
            "01-summary.pdf": _sha256(pdf_bytes),
            "03-audit-log.csv": _sha256(audit_csv),
            "05-customer-yaml.redacted.yml": _sha256(rendered_redacted_yaml),
            "06-memory-snapshot.json": _sha256(memory_json),
            "07-skill-catalog.json": _sha256(skill_json),
            "09-boot-checks.csv": _sha256(boot_csv),
        }
        manifest = build_manifest(
            customer_slug=request.customer_slug,
            matter=request.matter,
            period_start=request.period_start,
            period_end=request.period_end,
            file_hashes=file_hashes,
            actor=request.actor,
            actor_role=request.actor_role.value,
            signer_key_id=signer.key_id if signer else None,
            signature=SIGNATURE_DETACHED_MARKER if signer else None,
            signature_algorithm=signer.algorithm if signer else None,
            extra={
                "counts": counts,
                "coverage": coverage.to_dict(),
                "chain_pin": chain_pin.to_dict(),
                "provisional_manifest_sha256": provisional_sha,
            },
        )
        manifest_bytes = manifest.to_bytes()
        manifest_sha = manifest_sha256_hex(manifest)

        entries: List[Tuple[str, bytes]] = [
            ("00-README.md", readme_bytes),
            ("01-summary.pdf", pdf_bytes),
            ("03-audit-log.csv", audit_csv),
            ("05-customer-yaml.redacted.yml", rendered_redacted_yaml),
            ("06-memory-snapshot.json", memory_json),
            ("07-skill-catalog.json", skill_json),
            ("09-boot-checks.csv", boot_csv),
            ("manifest.json", manifest_bytes),
        ]

        # The detached signature covers manifest.json, which covers every other
        # artifact. It is deliberately NOT in file_hashes: it cannot hash
        # itself. Trust order: manifest.sig -> manifest.json -> everything else.
        if signer is not None:
            entries.append((DETACHED_SIGNATURE_FILENAME, signer.sign(manifest_bytes)))

        bytes_written = self._write_targz(request.output_path, entries)

        await self._emit_audit_row(
            request=request,
            manifest_sha=manifest_sha,
            counts=counts,
            file_count=len(entries),
            bytes_written=bytes_written,
            coverage=coverage,
            chain_pin=chain_pin,
        )

        return EvidencePacketResult(
            output_path=request.output_path,
            manifest_sha256=manifest_sha,
            file_count=len(entries),
            bytes_written=bytes_written,
            counts=counts,
            manifest=manifest,
            coverage=coverage,
            chain_pin=chain_pin,
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _load_customer_yaml(self, path: Path) -> bytes:
        if not path.exists():
            raise EvidencePacketError(
                f"customer.yaml not found at {path}; refusing to fabricate a placeholder. Provide a real customer.yaml."
            )
        return path.read_bytes()

    def _parse_yaml(self, text: bytes) -> Any:
        if self.yaml_loader is not None:
            return self.yaml_loader(text.decode("utf-8"))  # type: ignore[misc]
        # Fall back to a tiny built-in parser: accept JSON, otherwise
        # treat as opaque single-key text. This branch is exercised only
        # in tests that pass yaml_loader=None for simplicity.
        text_str = text.decode("utf-8")
        try:
            return json.loads(text_str)
        except json.JSONDecodeError:
            return {"raw": text_str}

    def _render_yaml(self, data: Any) -> bytes:
        if self.yaml_dumper is not None:
            rendered = self.yaml_dumper(data)  # type: ignore[misc]
            if isinstance(rendered, bytes):
                return rendered
            return rendered.encode("utf-8")
        return _render_yaml(data)

    def _extract_customer_name(self, parsed: Any, fallback_slug: str) -> str:
        if isinstance(parsed, dict):
            for key in ("customer_name", "name", "firm_name"):
                value = parsed.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return fallback_slug

    def _write_targz(self, output_path: Path, entries: Sequence[Tuple[str, bytes]]) -> int:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
        with tarfile.open(tmp_path, "w:gz", format=tarfile.PAX_FORMAT) as tar:
            for name, blob in entries:
                info = tarfile.TarInfo(name=name)
                info.size = len(blob)
                info.mtime = 0  # deterministic mtime for stable archives
                info.mode = 0o644
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                tar.addfile(info, io.BytesIO(blob))
        bytes_written = tmp_path.stat().st_size
        tmp_path.replace(output_path)
        return bytes_written

    async def _emit_audit_row(
        self,
        *,
        request: PacketRequest,
        manifest_sha: str,
        counts: Mapping[str, int],
        file_count: int,
        bytes_written: int,
        coverage: AuditCoverage,
        chain_pin: ChainPin,
    ) -> None:
        # Import locally to mirror the bin/lib/decommission.py pattern
        # (avoids hard adapter import at module load time).
        from adapter.audit_log import ActorRole, AuditEvent  # type: ignore

        role_map = {
            PacketActor.CAPTAIN: ActorRole.CAPTAIN,
            PacketActor.COMPLIANCE: ActorRole.COMPLIANCE,
        }
        event = AuditEvent(
            action_type="COMPLIANCE_PACKET_EXPORTED",
            actor=request.actor,
            actor_role=role_map[request.actor_role],
            # NOT a skill, and the row must not claim one (ss-console #2122).
            #
            # This field read `"compliance-audit-export"` for a year, naming a
            # skill that exists in neither repo and in no customer.yaml. The
            # instinct on finding that is to go build the skill. It cannot be
            # built: a packet's chain-of-custody row is written through the
            # broker's `audit_append`, which is gateway-PID-gated and rejects
            # the execute_code / terminal children that all skill work runs in
            # (`workspace_broker/server.py`, the `peer_pid != self.gateway_pid`
            # guard; vfy_01KZXYQNK316TK9JGHS9KBEFJC). That refusal is not an
            # obstacle to route around. It IS the control this packet attests
            # to: service agreement §4.5's claim that the agent cannot rewrite
            # its own record holds precisely because the agent cannot append to
            # it. A skill that could stamp its own export would falsify the
            # sentence the packet is built to prove.
            #
            # So the producer is named for what it is, in metadata, and
            # skill_name is NULL because no skill originated this row.
            skill_name=None,
            matter_ref=None if request.matter == "all" else request.matter,
            metadata={
                # The real producer. A reader asking "what made this packet"
                # gets the answer here instead of a skill name that resolves to
                # nothing.
                "producer": "operator/bin/generate-evidence-packet.sh",
                "customer_slug": request.customer_slug,
                "matter": request.matter,
                "period_start": request.period_start,
                "period_end": request.period_end,
                "manifest_sha256": manifest_sha,
                "file_count": file_count,
                "bytes_written": bytes_written,
                "output_path": str(request.output_path),
                "counts": dict(counts),
                "coverage": coverage.to_dict(),
                # The pin this packet was checked against, recorded INSIDE the
                # chain it attests to. A later reader can take this row's own
                # row_hash as the next pin, which is how the chain of custody
                # keeps going without a new mechanism.
                "chain_pin": chain_pin.to_dict(),
            },
        )
        try:
            await self.audit_writer.write(event)  # type: ignore[attr-defined]
        except Exception as exc:
            # If chain-of-custody fails, the packet that exists on disk
            # is unprovable. Surface the failure rather than swallow it.
            raise EvidencePacketError(
                "evidence packet wrote successfully but the "
                "COMPLIANCE_PACKET_EXPORTED audit row could not be "
                "persisted; manifest SHA-256 "
                f"{manifest_sha} is on disk but lacks chain-of-custody. "
                "Re-run after audit log recovery, or quarantine the "
                "packet pending audit reconciliation."
            ) from exc


__all__ = [
    "AuditCoverage",
    "CHAIN_PIN_SOURCE",
    "ChainPin",
    "EvidencePacketBuilder",
    "EvidencePacketError",
    "EvidencePacketResult",
    "PacketActor",
    "PacketRequest",
    "REQUIRED_ACTOR_ROLES",
    "ReadExecutor",
    "SqliteReadExecutor",
    "redact_customer_yaml",
]
