"""Per-customer decommission sequence (issue #820; data plane fixed by #1355).

Full off-boarding pipeline for a single customer. Preserves the LIVE
Machine-local data (audit ledger + ADR-0016 memory) through the ADR-0043
runtime-read seam, then runs the substrate-deletion steps owned by ops
(R2 bucket, Vectorize indexes, AgentMail identity, Fly Machine), archives
the compliance packet, and tombstones the customer config directory.

Data-plane doctrine (#1355)
---------------------------

The live runtime writes Machine-local sqlite: the broker-owned audit ledger
(OP-P1-4), the ADR-0016 ``persona_observations`` mirror, and
``agent_skills_inventory``. ``fly apps destroy`` (step 06) IS the designed
destruction mechanism for all of it. Two consequences:

* **Preservation is pull-before-destroy.** Step 02 pulls the audit ledger
  and the memory tables off the Machine through the runtime-read seam
  (``bin.lib.seam_pull.SeamAuditLogPreserver``) BEFORE any destructive
  step. A preservation failure halts the pipeline with the Machine intact.
* **There is no control-plane sweep.** The earlier ADR-0008 memory + voice
  ``decommission_source`` hooks walked ``memory_ingested_items`` /
  ``voice_ingestion_items`` on a per-customer control-plane Cloudflare D1
  that was never provisioned (no ``AIE_D1_DATABASE_ID`` exists anywhere in
  provisioning) and that the live runtime never wrote. The sweep was a
  silent no-op against an empty store and was removed with the rest of the
  ADR-0008 plane. R2 (step 03) and Vectorize (step 04) remain real
  control-plane substrates and keep their deletion steps.

Design notes
------------

* **One method per step.** The :class:`DecommissionPipeline`
  exposes one method per step plus :meth:`run` that orchestrates them in
  order. Each step writes an audit row before and after via
  :class:`adapter.audit_log.AuditLogWriter`; on failure it writes a third
  ``failed`` row before raising :class:`DecommissionStepFailed`.

* **External services behind Protocols.** Every destructive service
  (R2, Vectorize, AgentMail, Fly, observability) is a ``Protocol`` with a
  real implementation in ``bin/lib/decommission_backends.py`` (#2735) and
  a :class:`NoOpStub` that the pipeline defaults to. The CLI wires each
  real backend from a staged credential (``backends_from_env``); Fly arms
  only from ``FLY_API_TOKEN``, never from a logged-in ``fly`` CLI. A
  backend whose credential is absent stays the stub, which logs
  "skipped (no client wired)" and returns ``skipped=True``, and the
  ``--live`` gate refuses (exit 5) rather than report a clean
  decommission over a skipped deletion.

* **Dry-run mode is non-destructive.** Each step exposes a ``plan(...)``
  method that returns the manifest of what *would* happen without
  executing. The CLI surfaces this as one line per step. Live mode runs
  the same step body but with ``dry_run=False``; the manifests have the
  same shape so dry-run vs live diffs cleanly.

* **Idempotency is a P0 invariant.** Every step is safe to re-run on a
  partially-decommissioned customer. Re-running on a fully-decommissioned
  customer is a no-op that exits 0. The :class:`NoOpStub`,
  :class:`FilesystemTombstoner`, and the audit-log writer all treat
  missing inputs as success, not failure.

* **No real destructive actions in CI.** Tests construct the pipeline
  with :class:`NoOpStub` implementations of every external service. The
  ``smd`` customer-zero fixture is a synthetic directory inside
  ``operator/bin/fixtures/smd/``, not a real customer.

The steps:

  1. Drain in-flight LLM calls (#805 handled separately at the script
     level via the Fly Machine pause; the pipeline records that the
     drain completed before mutating substrate).
  2. Preserve Machine-local data: pull the full audit ledger + ADR-0016
     memory tables through the runtime-read seam to the archive dir
     (CSV + sqlite snapshot; the snapshot doubles as the evidence
     generator's ``--read-db`` input).
  3. R2: delete the customer's object namespace (everything under the
     ``{slug}/`` prefix EXCEPT the decommission-archive subtree).
  4. Vectorize: delete the per-customer vault + corrections indexes.
  5. AgentMail: deprovision inbox / forwarding rules (stubbed).
  6. Fly Machine: stop and destroy ``hermes-{slug}`` (stubbed). This is
     also the data-destruction step for ALL Machine-local state.
  7. Compliance evidence packet: generate the final packet and archive
     it to per-customer cold storage.
  8. ``operator/customers/{slug}/`` tombstone: rename to
     ``{slug}.decommissioned.{iso-date}`` and write a marker file.
  9. Observability cleanup (healthchecks.io + fleet_status row).
"""

from __future__ import annotations

import asyncio
import csv
import enum
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional, Protocol

log = logging.getLogger("aie.bin.decommission")


# ---------------------------------------------------------------------------
# Public exceptions
# ---------------------------------------------------------------------------


class DecommissionStepFailed(RuntimeError):
    """Raised when any decommission step fails in live mode.

    Halts execution. The script wrapper writes the failure to stderr and
    exits non-zero. The audit row for the failed step is already written
    by the time this is raised (the step body writes ``failed`` before
    re-raising), so the trail is preserved.
    """

    def __init__(self, step_name: str, customer_slug: str, cause: BaseException) -> None:
        super().__init__(
            f"decommission step {step_name!r} failed for customer {customer_slug!r}: "
            f"{type(cause).__name__}: {cause}"
        )
        self.step_name = step_name
        self.customer_slug = customer_slug
        self.cause = cause


# ---------------------------------------------------------------------------
# Step manifests
#
# Every step returns the same shape so dry-run and live runs are diffable
# line-by-line. ``skipped`` is true when the step short-circuited because
# the input was already absent (the idempotency path) or because the
# implementation is a NoOpStub.
# ---------------------------------------------------------------------------


class StepStatus(str, enum.Enum):
    PLANNED = "planned"      # dry-run; nothing executed
    EXECUTED = "executed"    # live run; work performed
    SKIPPED = "skipped"      # input already absent or stub
    FAILED = "failed"        # live run; exception raised


@dataclass(frozen=True)
class StepResult:
    """One step's outcome, both for plan() and execute().

    The pipeline collects these in order so the audit-log + dashboard can
    render a step-by-step decommission report.
    """

    name: str
    status: StepStatus
    detail: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# External services (AgentMail, Fly): Protocols and their NoOpStubs
#
# The real implementations live in bin/lib/decommission_backends.py
# (AgentMailInboxDeprovisioner, FlyAppDestroyer; #2735) and are injected by
# the CLI when their credential is staged (AGENTMAIL_API_KEY; FLY_API_TOKEN,
# a logged-in fly CLI does not count). The NoOpStub is the default the
# pipeline falls back to, and unwired_destructive_backends() reports it so a
# --live run refuses. The stubs return manifests that look like real ones so
# the audit trail stays the same shape across stub vs live transitions.
# ---------------------------------------------------------------------------


class AgentMailProvisioner(Protocol):
    """Deprovisions an AgentMail identity and any forwarding rules."""

    async def deprovision(self, customer_slug: str) -> dict: ...


class FlyMachineManager(Protocol):
    """Stops and destroys ``hermes-{slug}`` Fly Machine."""

    async def destroy_machine(self, customer_slug: str) -> dict: ...


class ObservabilityCleanup(Protocol):
    """Tears down the per-customer observability surface (ADR 0023 Wave 1).

    Cancels the customer's healthchecks.io check (so grace expiration
    stops firing alerts post-decommission) and deletes the central-D1
    ``fleet_status`` row. Both operations are idempotent so re-running
    the pipeline on a partially-decommissioned customer is safe.
    """

    async def cleanup(self, customer_slug: str) -> dict: ...


class NoOpAgentMailStub:
    _SKIPPED_REASON = "external_client_not_wired"

    async def deprovision(self, customer_slug: str) -> dict:
        log.info("agentmail.deprovision skipped (no client wired) customer=%s", customer_slug)
        return {"skipped": True, "reason": self._SKIPPED_REASON, "identities_removed": 0}


class NoOpFlyStub:
    _SKIPPED_REASON = "external_client_not_wired"

    async def destroy_machine(self, customer_slug: str) -> dict:
        log.info("fly.destroy_machine skipped (no client wired) customer=%s", customer_slug)
        return {"skipped": True, "reason": self._SKIPPED_REASON, "app_destroyed": False}


class NoOpObservabilityCleanupStub:
    """No-op observability cleanup — returns ``skipped`` manifest.

    Used until the operator wires real healthchecks.io API + D1 HTTP API
    clients into the CLI (planned alongside customer #2 onboarding so
    Captain can validate the full lifecycle on a real second tenant
    before committing). The stub keeps the pipeline runnable end-to-end
    against the ``smd`` customer-zero fixture today.
    """

    _SKIPPED_REASON = "external_client_not_wired"

    async def cleanup(self, customer_slug: str) -> dict:
        log.info(
            "observability.cleanup skipped (no client wired) customer=%s", customer_slug
        )
        return {
            "skipped": True,
            "reason": self._SKIPPED_REASON,
            "healthchecks_check_cancelled": False,
            "fleet_status_row_deleted": False,
        }


# ---------------------------------------------------------------------------
# Substrate clients
#
# Memory + voice decommission are delegated to the canonical hooks in
# adapter/. The pipeline carries the wired stores + storage so the caller
# constructs them once (against either real D1/R2 bindings or fakes for
# tests).
# ---------------------------------------------------------------------------


class AuditLogPreserver(Protocol):
    """Export the customer's audit_log table to cold storage and report
    the preservation manifest.

    Per ``docs/specs/operator/audit-retention.md`` §"Decommission carve-out"
    the audit log is NOT deleted alongside memory + voice — it must survive
    until the per-vertical retention window elapses. This Protocol covers
    the export step that runs BEFORE step 2's canonical
    ``decommission_source`` hooks fire.

    Production wires this to a D1 → CSV exporter that streams the per-customer
    audit_log table out via ``wrangler d1 execute`` and copies it under
    ``{archive_root}/{slug}/``. The default :class:`InMemoryAuditLogPreserver`
    implementation writes a stub manifest sufficient for the script-level
    contract test.
    """

    async def preserve(
        self,
        customer_slug: str,
        archive_dir: Path,
        audit_log_days: int,
    ) -> dict: ...


class R2NamespaceDeleter(Protocol):
    """Deletes every R2 object under ``{customer-slug}/`` EXCEPT the
    decommission-archive subtree (which has already been moved to cold
    storage by step 8).

    Production calls ``wrangler r2 object delete`` in batches; tests
    track the would-be deletions in an in-memory dict.
    """

    async def delete_namespace(self, customer_slug: str) -> dict: ...


class VectorizeIndexDeleter(Protocol):
    """Deletes ``hermes-{slug}-vault`` and ``hermes-{slug}-corrections``."""

    async def delete_indexes(self, customer_slug: str) -> dict: ...


class ComplianceArchiver(Protocol):
    """Generates the compliance evidence packet and copies it to the
    per-customer cold-storage retention bucket per the spec.

    Returns the archive path written.
    """

    async def archive(self, customer_slug: str, archive_dir: Path) -> dict: ...


# ---------------------------------------------------------------------------
# Default in-process implementations
#
# These cover the substrate steps that have no external dependencies in
# this PR. The pipeline accepts a Protocol so tests pass fakes that
# record calls.
# ---------------------------------------------------------------------------


class FilesystemTombstoner:
    """Renames ``operator/customers/{slug}/`` to
    ``{slug}.decommissioned.{iso-date}`` and writes a tombstone marker.

    Idempotent: if the live directory is already absent, returns
    ``skipped=True`` with the existing tombstone path (if found). If both
    the live and tombstone paths are absent, returns ``skipped=True`` with
    ``reason="no_customer_dir"``. Never deletes the directory entirely;
    preserves audit history per the issue.
    """

    def __init__(self, customers_root: Path) -> None:
        self._root = customers_root

    def tombstone(
        self,
        customer_slug: str,
        *,
        now: Optional[datetime] = None,
        audit_log_preserve_until: Optional[str] = None,
    ) -> dict:
        when = now if now is not None else datetime.now(timezone.utc)
        date_part = when.strftime("%Y-%m-%d")
        live_dir = self._root / customer_slug
        tomb_dir = self._root / f"{customer_slug}.decommissioned.{date_part}"

        # Idempotency: if there is already a tombstone, do not move again.
        if tomb_dir.exists():
            return {
                "skipped": True,
                "reason": "already_tombstoned",
                "tombstone_path": str(tomb_dir),
            }

        if not live_dir.exists():
            # No live dir, no existing tombstone — treat as already removed
            # (matches the "partial decommission" idempotency contract).
            return {
                "skipped": True,
                "reason": "no_customer_dir",
                "tombstone_path": None,
            }

        # Move the directory and drop a marker file at its root.
        live_dir.rename(tomb_dir)
        marker = tomb_dir / "DECOMMISSIONED.md"
        preserve_line = (
            f"audit_log_preserve_until: {audit_log_preserve_until}\n"
            if audit_log_preserve_until
            else ""
        )
        marker.write_text(
            "# Decommissioned\n\n"
            f"This directory contained the customer config for `{customer_slug}` until "
            f"{when.isoformat()}.\n\n"
            "The customer was decommissioned by `operator/bin/decommission-customer.sh`.\n\n"
            "The directory is preserved as historical record per the audit-history requirement.\n"
            + (f"\n{preserve_line}" if preserve_line else ""),
            encoding="utf-8",
        )
        return {
            "skipped": False,
            "reason": None,
            "tombstone_path": str(tomb_dir),
            "marker_path": str(marker),
            "audit_log_preserve_until": audit_log_preserve_until,
        }

    def plan(self, customer_slug: str, *, now: Optional[datetime] = None) -> dict:
        when = now if now is not None else datetime.now(timezone.utc)
        date_part = when.strftime("%Y-%m-%d")
        live_dir = self._root / customer_slug
        tomb_dir = self._root / f"{customer_slug}.decommissioned.{date_part}"
        if tomb_dir.exists():
            return {"would_rename": False, "reason": "already_tombstoned"}
        if not live_dir.exists():
            return {"would_rename": False, "reason": "no_customer_dir"}
        return {
            "would_rename": True,
            "from": str(live_dir),
            "to": str(tomb_dir),
        }


class DefaultDrainCoordinator:
    """Default drain coordinator: records that drain ran.

    The real drain logic (60s grace window for in-flight LLM calls)
    lives in #805 and is invoked by the shell wrapper BEFORE this
    pipeline runs. The pipeline records the drain marker into the audit
    log so the decommission report shows the drain step completed.

    If a richer drain client is provided by the CLI in the future, swap
    this for an implementation that calls the Fly Machine pause +
    in-flight poll. The protocol is intentionally trivial so this can
    happen without a pipeline rewrite.
    """

    def __init__(self, drain_window_seconds: int = 60) -> None:
        self._drain_window_seconds = drain_window_seconds

    async def drain(self, customer_slug: str) -> dict:
        # In-process default: assume the wrapper already paused the
        # machine. Return the drain window as evidence.
        log.info(
            "drain.complete window_seconds=%d customer=%s",
            self._drain_window_seconds,
            customer_slug,
        )
        return {
            "drain_window_seconds": self._drain_window_seconds,
            "in_flight_remaining": 0,
        }


class InMemoryComplianceArchiver:
    """Writes a minimal compliance-packet stub to the archive dir.

    Production wires this to the ``compliance-audit-export`` skill so the
    real packet (per ``compliance-evidence-packet.md`` §packet-structure)
    is generated. For now this writes a manifest JSON that names the
    customer, the timestamp, and the expected packet contents — enough to
    prove the archive step ran and to compose with the real generator
    later without changing the pipeline.
    """

    async def archive(self, customer_slug: str, archive_dir: Path) -> dict:
        archive_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        manifest_path = archive_dir / f"compliance-packet-manifest-{ts}.json"
        manifest = {
            "customer_slug": customer_slug,
            "generated_at": ts,
            "packet_contents_expected": [
                "00-README.md",
                "01-summary.pdf",
                "02-architecture-controls.md",
                "03-audit-log.csv",
                "04-audit-log-human.md",
                "05-customer-yaml.redacted.yml",
                "06-memory-snapshot.json",
                "07-skill-catalog.json",
                "08-engagement-letter-clauses",
                "09-boot-checks.csv",
                "10-dpa.pdf",
                "11-baa.pdf",
                "12-decommission-confirmation.pdf",
                "manifest.json",
            ],
            "note": (
                "stub manifest from bin/lib/decommission.py InMemoryComplianceArchiver; "
                "replace with compliance-audit-export skill output when wired"
            ),
        }
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        return {
            "archive_path": str(manifest_path),
            "stub": True,
        }


# ---------------------------------------------------------------------------
# Audit-log retention policy (mirror of src/lib/operator/customer-yaml/types.ts)
#
# Kept in sync with VERTICAL_AUDIT_LOG_DAYS_DEFAULTS on the TypeScript side.
# Both tables encode the same policy from
# docs/specs/operator/audit-retention.md §"Per-vertical defaults" — when one
# changes the other MUST change in the same PR so the validator and the
# decommission runner never disagree on the resolved retention window.
# ---------------------------------------------------------------------------


VERTICAL_AUDIT_LOG_DAYS_DEFAULTS: dict[str, int] = {
    "law-firm": 2555,
    "marketing-agency": 1095,
    "real-estate": 2555,
    "manufacturing": 2555,
    "insurance": 2555,
    "mixed": 2555,
}

_AUDIT_LOG_DAYS_FALLBACK = 2555


def resolve_audit_log_days(customer_yaml: Optional[dict]) -> int:
    """Resolve `audit_log_days` from a parsed customer.yaml dict.

    Reads ``memory.retention.audit_log_days`` if present (overrides the
    per-vertical default); otherwise looks up the per-vertical default by
    ``vertical``; otherwise returns the conservative 2555-day fallback.
    Mirrors the override-up-only guarantee in the TypeScript validator —
    by the time customer.yaml reaches the decommission script it has
    already been validated, so the override is known to satisfy the
    vertical minimum.
    """
    if not isinstance(customer_yaml, dict):
        return _AUDIT_LOG_DAYS_FALLBACK
    memory = customer_yaml.get("memory")
    if isinstance(memory, dict):
        retention = memory.get("retention")
        if isinstance(retention, dict):
            override = retention.get("audit_log_days")
            if isinstance(override, int) and override > 0:
                return override
    vertical = customer_yaml.get("vertical")
    if isinstance(vertical, str) and vertical in VERTICAL_AUDIT_LOG_DAYS_DEFAULTS:
        return VERTICAL_AUDIT_LOG_DAYS_DEFAULTS[vertical]
    return _AUDIT_LOG_DAYS_FALLBACK


def _load_customer_yaml(customers_root: Path, slug: str) -> Optional[dict]:
    """Best-effort load of the customer.yaml under customers/<slug>/.

    Returns None if the file is missing or unparseable — the pipeline
    falls back to the per-vertical default (and then the conservative
    2555-day fallback). Decommission must not fail closed on a missing
    YAML at this step; the YAML may already be gone if a previous
    decommission attempt tombstoned the directory.
    """
    yaml_path = customers_root / slug / "customer.yaml"
    if not yaml_path.is_file():
        return None
    try:
        import yaml as _yaml  # type: ignore[import-untyped]

        parsed = _yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else None
    except Exception:  # noqa: BLE001
        log.warning("decommission: customer.yaml parse failed at %s", yaml_path)
        return None


class InMemoryAuditLogPreserver:
    """Default audit-log preserver — writes a stub manifest into archive_dir.

    Production wires a real D1 → CSV exporter that streams the per-customer
    ``audit_log`` table; this stub satisfies the script-level idempotency
    + manifest contract so the decommission flow is testable end-to-end
    without a live D1. The manifest names the customer, the resolved
    retention window, the deadline, and the (stubbed) export path so
    the audit row recorded by the pipeline carries the same shape it
    would under production wiring.

    Idempotent: re-running on the same UTC date returns a manifest with
    ``skipped: True`` because the dated manifest is already present.
    """

    async def preserve(
        self,
        customer_slug: str,
        archive_dir: Path,
        audit_log_days: int,
    ) -> dict:
        archive_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        date_part = now.strftime("%Y-%m-%d")
        manifest_path = archive_dir / f"audit-log-manifest-{date_part}.json"
        csv_path = archive_dir / f"audit-log-{date_part}.csv"
        preserve_until = (now + timedelta(days=audit_log_days)).isoformat()

        if manifest_path.exists():
            return {
                "skipped": True,
                "reason": "audit_log_already_preserved_today",
                "audit_log_days": audit_log_days,
                "preserve_until": preserve_until,
                "archive_path": str(manifest_path),
                "rows_preserved": 0,
                "stub": True,
            }

        # Stub CSV: header-only file. Production replaces this with a real
        # D1 export that streams every row of audit_log under the customer's
        # database binding.
        with csv_path.open("w", encoding="utf-8", newline="") as fp:
            writer = csv.writer(fp)
            writer.writerow(
                [
                    "id",
                    "ts",
                    "action_type",
                    "actor",
                    "actor_role",
                    "skill_name",
                    "matter_ref",
                    "input_digest",
                    "output_digest",
                    "diff_digest",
                    "trust_ceiling",
                    "metadata",
                ]
            )

        manifest = {
            "customer_slug": customer_slug,
            "exported_at": now.isoformat(),
            "preserve_until": preserve_until,
            "audit_log_days": audit_log_days,
            "csv_path": str(csv_path),
            "rows_preserved": 0,
            "stub": True,
            "note": (
                "stub manifest from bin/lib/decommission.py InMemoryAuditLogPreserver; "
                "replace with d1-to-r2 audit-log exporter when wired"
            ),
        }
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        return {
            "skipped": False,
            "audit_log_days": audit_log_days,
            "preserve_until": preserve_until,
            "archive_path": str(manifest_path),
            "csv_path": str(csv_path),
            "rows_preserved": 0,
            "stub": True,
        }


# ---------------------------------------------------------------------------
# Audit helpers
# ---------------------------------------------------------------------------


def _audit_metadata(step: str, customer_slug: str, *, detail: Optional[dict] = None) -> dict:
    """Compose the metadata dict for a decommission audit row."""
    meta: dict = {
        "step": step,
        "customer_slug": customer_slug,
    }
    if detail:
        meta["detail"] = detail
    return meta


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


@dataclass
class DecommissionPipeline:
    """Orchestrator for the 9-step decommission sequence.

    Construct with the per-customer stores + storage clients (real or
    fake) plus the audit-log writer. Call :meth:`plan` for a dry-run
    manifest or :meth:`run` to execute.
    """

    customer_slug: str
    customers_root: Path
    archive_root: Path
    audit_writer: object  # adapter.audit_log.AuditLogWriter, kept loose to avoid import cycle
    actor: str = "captain"

    drain: object = field(default_factory=DefaultDrainCoordinator)
    r2_deleter: Optional[R2NamespaceDeleter] = None
    vectorize_deleter: Optional[VectorizeIndexDeleter] = None
    agentmail: AgentMailProvisioner = field(default_factory=NoOpAgentMailStub)
    fly: FlyMachineManager = field(default_factory=NoOpFlyStub)
    observability: ObservabilityCleanup = field(default_factory=NoOpObservabilityCleanupStub)
    archiver: ComplianceArchiver = field(default_factory=InMemoryComplianceArchiver)
    audit_log_preserver: AuditLogPreserver = field(
        default_factory=InMemoryAuditLogPreserver
    )
    tombstoner: Optional[FilesystemTombstoner] = None
    # Parsed customer.yaml (or None when the file is missing/unparseable).
    # Drives `resolve_audit_log_days` for the step-2 carve-out. Tests inject
    # this directly; the CLI loads it from disk before constructing the
    # pipeline (see decommission_cli.py).
    customer_yaml: Optional[dict] = None

    def __post_init__(self) -> None:
        if not self.customer_slug:
            raise ValueError("customer_slug must be a non-empty string")
        if self.tombstoner is None:
            self.tombstoner = FilesystemTombstoner(self.customers_root)

    # --- live-readiness inspection -----------------------------------------

    def unwired_destructive_backends(self) -> list[str]:
        """Names of destructive backends that are NOT wired to a real
        implementation.

        An empty list means every destructive step will actually execute.
        A non-empty list means a ``--live`` run would *report* deletions
        it cannot perform — for a law-firm product that is a silent breach
        of the offboarding / data-deletion promise (issue #1123).

        The CLI calls this to fail closed before a ``--live`` run: better
        to refuse than to tombstone the customer dir and exit ``OK`` while
        D1 rows, R2 objects, Vectorize indexes, the Fly Machine, its
        secrets, and the inbox all remain. Pure inspection — no I/O, no
        side effects.
        """
        unwired: list[str] = []
        # Preservation is a PREREQUISITE to destruction (#1355): a --live run
        # whose preserver is the stub would archive a header-only CSV and then
        # destroy the only real copy of the audit ledger with the Machine.
        if isinstance(self.audit_log_preserver, InMemoryAuditLogPreserver):
            unwired.append("audit_log_preserver")
        if self.r2_deleter is None:
            unwired.append("r2_deleter")
        if self.vectorize_deleter is None:
            unwired.append("vectorize_deleter")
        if isinstance(self.agentmail, NoOpAgentMailStub):
            unwired.append("agentmail")
        if isinstance(self.fly, NoOpFlyStub):
            unwired.append("fly")
        if isinstance(self.observability, NoOpObservabilityCleanupStub):
            unwired.append("observability")
        return unwired

    # --- public entrypoints -------------------------------------------------

    async def plan(self) -> list[StepResult]:
        """Dry-run: returns the per-step manifest of what would happen.

        Performs no destructive operations and writes no audit rows. The
        CLI surfaces each result as one line so dry-run output diffs
        cleanly against live-run output.
        """
        return [
            StepResult(
                name="01_drain",
                status=StepStatus.PLANNED,
                detail={"action": "verify drain marker", "customer": self.customer_slug},
            ),
            StepResult(
                name="02_preserve_machine_data",
                status=StepStatus.PLANNED,
                detail={
                    "action": (
                        "pull audit ledger + ADR-0016 memory tables via the "
                        "runtime-read seam to the archive dir (pull-before-destroy)"
                    ),
                    "preserver_wired": not isinstance(
                        self.audit_log_preserver, InMemoryAuditLogPreserver
                    ),
                    "audit_log_days": resolve_audit_log_days(self.customer_yaml),
                    "audit_log_preserve_until": (
                        datetime.now(timezone.utc)
                        + timedelta(days=resolve_audit_log_days(self.customer_yaml))
                    ).isoformat(),
                },
            ),
            StepResult(
                name="03_r2_namespace",
                status=StepStatus.PLANNED,
                detail={"namespace": f"{self.customer_slug}/", "deleter_wired": self.r2_deleter is not None},
            ),
            StepResult(
                name="04_vectorize_indexes",
                status=StepStatus.PLANNED,
                detail={
                    "indexes": [
                        f"hermes-{self.customer_slug}-vault",
                        f"hermes-{self.customer_slug}-corrections",
                    ],
                    "deleter_wired": self.vectorize_deleter is not None,
                },
            ),
            StepResult(
                name="05_agentmail",
                status=StepStatus.PLANNED,
                detail={"action": "deprovision inbox + forwarding rules"},
            ),
            StepResult(
                name="06_fly_machine",
                status=StepStatus.PLANNED,
                detail={"app": f"hermes-{self.customer_slug}"},
            ),
            StepResult(
                name="07_compliance_archive",
                status=StepStatus.PLANNED,
                detail={
                    "archive_dir": str(self.archive_root / self.customer_slug),
                },
            ),
            StepResult(
                name="08_tombstone",
                status=StepStatus.PLANNED,
                detail=self.tombstoner.plan(self.customer_slug),
            ),
            StepResult(
                name="09_observability_cleanup",
                status=StepStatus.PLANNED,
                detail={
                    "action": "cancel healthchecks.io check + delete fleet_status row",
                    "client_wired": not isinstance(self.observability, NoOpObservabilityCleanupStub),
                },
            ),
        ]

    async def run(self) -> list[StepResult]:
        """Live mode: executes all steps in order. Halts on first failure."""
        results: list[StepResult] = []

        # Step 1 — DECOMMISSION_INITIATED + drain
        await self._write_audit_row(
            action_type="DECOMMISSION_INITIATED",
            metadata=_audit_metadata("01_drain", self.customer_slug),
        )
        try:
            drain_detail = await self.drain.drain(self.customer_slug)
        except Exception as exc:
            await self._write_failure("01_drain", exc)
            raise DecommissionStepFailed("01_drain", self.customer_slug, exc) from exc
        results.append(StepResult(name="01_drain", status=StepStatus.EXECUTED, detail=drain_detail))
        await self._write_audit_row(
            action_type="DECOMMISSION_DRAIN_COMPLETE",
            metadata=_audit_metadata("01_drain", self.customer_slug, detail=drain_detail),
        )

        # Step 2 — preserve Machine-local data (pull-before-destroy, #1355)
        results.append(await self._run_step(
            "02_preserve_machine_data",
            self._step_preserve_machine_data,
        ))

        # Step 3 — R2 namespace delete
        results.append(await self._run_step(
            "03_r2_namespace",
            self._step_r2_namespace,
        ))

        # Step 4 — Vectorize indexes delete
        results.append(await self._run_step(
            "04_vectorize_indexes",
            self._step_vectorize_indexes,
        ))

        # Step 5 — AgentMail
        results.append(await self._run_step(
            "05_agentmail",
            self._step_agentmail,
        ))

        # Step 6 — Fly Machine
        results.append(await self._run_step(
            "06_fly_machine",
            self._step_fly_machine,
        ))

        # Step 7 — Compliance archive
        results.append(await self._run_step(
            "07_compliance_archive",
            self._step_compliance_archive,
        ))

        # Step 8 — Tombstone
        results.append(await self._run_step(
            "08_tombstone",
            self._step_tombstone,
        ))

        # Step 9 — Observability cleanup (ADR 0023 Wave 1)
        # Runs at the tail of the pipeline because the work is
        # idempotent control-plane housekeeping, not part of the
        # Machine teardown proper. Healthchecks.io has a small window
        # between Machine destroy (step 6) and this step where a grace-
        # expiration alert could fire; the windowed noise is acceptable
        # vs. the structural cost of weaving observability into the
        # core teardown sequence.
        results.append(await self._run_step(
            "09_observability_cleanup",
            self._step_observability_cleanup,
        ))

        # Final marker: DECOMMISSION_FINAL records the end of the pipeline.
        await self._write_audit_row(
            action_type="DECOMMISSION_FINAL",
            metadata=_audit_metadata(
                "decommission_complete",
                self.customer_slug,
                detail={"steps": [r.name for r in results]},
            ),
        )
        return results

    # --- step implementations ----------------------------------------------

    async def _run_step(
        self,
        name: str,
        body: Callable[[], Awaitable[dict]],
    ) -> StepResult:
        """Run one step body wrapped with begin/end audit rows + halt-on-fail."""
        await self._write_audit_row(
            action_type="DECOMMISSION_STEP_BEGIN",
            metadata=_audit_metadata(name, self.customer_slug),
        )
        try:
            detail = await body()
        except Exception as exc:
            await self._write_failure(name, exc)
            raise DecommissionStepFailed(name, self.customer_slug, exc) from exc

        skipped = bool(detail.get("skipped"))
        status = StepStatus.SKIPPED if skipped else StepStatus.EXECUTED
        await self._write_audit_row(
            action_type="DECOMMISSION_STEP_COMPLETE",
            metadata=_audit_metadata(name, self.customer_slug, detail=detail),
        )
        return StepResult(name=name, status=status, detail=detail)

    async def _step_preserve_machine_data(self) -> dict:
        # PULL-BEFORE-DESTROY (#1355; audit-retention.md #893 carve-out).
        # The live audit ledger + ADR-0016 memory are Machine-local; step 06's
        # app destroy is their designed destruction mechanism. The preserver
        # (bin.lib.seam_pull.SeamAuditLogPreserver in production) pulls them
        # through the runtime-read seam to the archive dir FIRST. It must
        # succeed before any destructive step — a mid-step failure halts the
        # pipeline with the Machine (and therefore the data) intact, and the
        # rerun re-attempts preservation against still-present data.
        #
        # There is no control-plane memory/voice sweep here anymore: the
        # ADR-0008 hooks walked tables on a per-customer Cloudflare D1 that
        # was never provisioned and never written (see module docstring).
        audit_log_days = resolve_audit_log_days(self.customer_yaml)
        archive_dir = self.archive_root / self.customer_slug
        audit_log_manifest = await self.audit_log_preserver.preserve(
            self.customer_slug, archive_dir, audit_log_days
        )
        # Emit a discrete audit row so the decommission report names the
        # carve-out and its manifest explicitly.
        await self._write_audit_row(
            action_type="DECOMMISSION_STEP_COMPLETE",
            metadata=_audit_metadata(
                "02_preserve_machine_data/audit_log_preserved",
                self.customer_slug,
                detail={
                    "audit_log_days": audit_log_days,
                    "preserve_until": audit_log_manifest.get("preserve_until"),
                    "archive_path": audit_log_manifest.get("archive_path"),
                    "rows_preserved": audit_log_manifest.get("rows_preserved", 0),
                    "memory_rows_preserved": audit_log_manifest.get("memory_rows_preserved"),
                    "skipped": bool(audit_log_manifest.get("skipped")),
                },
            ),
        )
        return {
            "skipped": bool(audit_log_manifest.get("skipped")),
            "audit_log_preserved": audit_log_manifest,
        }

    async def _step_r2_namespace(self) -> dict:
        if self.r2_deleter is None:
            return {
                "skipped": True,
                "reason": "no r2_deleter wired",
                "namespace": f"{self.customer_slug}/",
            }
        manifest = await self.r2_deleter.delete_namespace(self.customer_slug)
        return {"namespace": f"{self.customer_slug}/", **manifest}

    async def _step_vectorize_indexes(self) -> dict:
        if self.vectorize_deleter is None:
            return {
                "skipped": True,
                "reason": "no vectorize_deleter wired",
                "indexes": [
                    f"hermes-{self.customer_slug}-vault",
                    f"hermes-{self.customer_slug}-corrections",
                ],
            }
        manifest = await self.vectorize_deleter.delete_indexes(self.customer_slug)
        return manifest

    async def _step_agentmail(self) -> dict:
        return await self.agentmail.deprovision(self.customer_slug)

    async def _step_fly_machine(self) -> dict:
        return await self.fly.destroy_machine(self.customer_slug)

    async def _step_compliance_archive(self) -> dict:
        archive_dir = self.archive_root / self.customer_slug
        manifest = await self.archiver.archive(self.customer_slug, archive_dir)
        return manifest

    async def _step_tombstone(self) -> dict:
        # Tombstoner is sync; wrap to keep the step interface uniform.
        # Pass the resolved preserve-until so the marker file names the
        # audit-log retention deadline alongside the tombstone date.
        audit_log_days = resolve_audit_log_days(self.customer_yaml)
        preserve_until = (
            datetime.now(timezone.utc) + timedelta(days=audit_log_days)
        ).isoformat()
        return self.tombstoner.tombstone(
            self.customer_slug, audit_log_preserve_until=preserve_until
        )

    async def _step_observability_cleanup(self) -> dict:
        # ADR 0023 Wave 1: cancel the healthchecks.io check and delete
        # the central-D1 fleet_status row. Delegated to the
        # ObservabilityCleanup protocol so tests inject a fake and the
        # CLI wires a real client in when the operator stages
        # HEALTHCHECKS_API_KEY + CF_D1_API_TOKEN. The NoOp stub keeps
        # smd-customer-zero dry runs end-to-end green.
        return await self.observability.cleanup(self.customer_slug)

    # --- audit-row helpers --------------------------------------------------

    async def _write_audit_row(self, *, action_type: str, metadata: dict) -> None:
        # Import here to avoid hard adapter import at module load time;
        # the lib is consumed by the script + tests, and tests inject a
        # fake writer that does not need adapter.audit_log on PYTHONPATH.
        from adapter.audit_log import AuditEvent, ActorRole

        event = AuditEvent(
            action_type=action_type,
            actor=self.actor,
            actor_role=ActorRole.CAPTAIN,
            metadata=metadata,
        )
        await self.audit_writer.write(event)

    async def _write_failure(self, step_name: str, exc: BaseException) -> None:
        try:
            await self._write_audit_row(
                action_type="DECOMMISSION_STEP_FAILED",
                metadata=_audit_metadata(
                    step_name,
                    self.customer_slug,
                    detail={"failed": True, "error": f"{type(exc).__name__}: {exc}"},
                ),
            )
        except Exception:  # noqa: BLE001
            # If audit write itself fails we cannot do better than log;
            # the calling script still raises the original step failure.
            log.exception("decommission audit-row write failed for %s/%s", self.customer_slug, step_name)


__all__ = [
    "AgentMailProvisioner",
    "AuditLogPreserver",
    "ComplianceArchiver",
    "DecommissionPipeline",
    "DecommissionStepFailed",
    "DefaultDrainCoordinator",
    "FilesystemTombstoner",
    "FlyMachineManager",
    "InMemoryAuditLogPreserver",
    "InMemoryComplianceArchiver",
    "NoOpAgentMailStub",
    "NoOpFlyStub",
    "NoOpObservabilityCleanupStub",
    "ObservabilityCleanup",
    "R2NamespaceDeleter",
    "StepResult",
    "StepStatus",
    "VectorizeIndexDeleter",
    "VERTICAL_AUDIT_LOG_DAYS_DEFAULTS",
    "resolve_audit_log_days",
]
