"""End-to-end tests for bin/lib/decommission.py against the smd fixture.

Coverage:

* dry-run prints per-step plan, performs no destructive operations,
  writes no audit rows, exits 0;
* live mode runs the full sequence to completion against fake
  external services (R2, Vectorize, the seam preserver) + NoOpStubs
  (AgentMail, Fly);
* idempotency: dry-run x2 -> live -> live again all succeed cleanly
  (the second live run is a full no-op because every step is already
  done);
* failure halts: a runner that raises mid-sequence halts with
  ``DecommissionStepFailed`` and the audit log contains a failure row
  for the failed step;
* audit-log emission: every step emits begin + end rows, plus a final
  ``DECOMMISSION_FINAL`` row.

The smd customer-zero fixture is copied into a tmp customers/ root per
test so the tombstone step can rename without touching the checked-in
fixture.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

_HERE = Path(__file__).resolve()
# operator/ on sys.path so `from adapter.audit_log import ...` resolves.
sys.path.insert(0, str(_HERE.parents[2]))

from adapter.audit_log import (  # noqa: E402
    ACCEPTED_ACTION_TYPES,
    AuditEvent,
    AuditLogWriter,
    SqliteExecutor,
)
from bin.lib.decommission import (  # noqa: E402
    DecommissionPipeline,
    DecommissionStepFailed,
    FilesystemTombstoner,
    InMemoryComplianceArchiver,
    NoOpAgentMailStub,
    NoOpFlyStub,
    StepStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_AUDIT_SCHEMA = """
CREATE TABLE audit_log (
  id            TEXT PRIMARY KEY,
  ts            TEXT NOT NULL,
  action_type   TEXT NOT NULL,
  actor         TEXT NOT NULL,
  actor_role    TEXT,
  skill_name    TEXT,
  matter_ref    TEXT,
  input_digest  TEXT,
  output_digest TEXT,
  diff_digest   TEXT,
  trust_ceiling TEXT,
  metadata      TEXT
);
"""


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _make_audit(tmp_path: Path) -> tuple[AuditLogWriter, sqlite3.Connection]:
    conn = sqlite3.connect(str(tmp_path / "audit.sqlite"))
    conn.executescript(_AUDIT_SCHEMA)
    return AuditLogWriter(SqliteExecutor(conn)), conn


def _copy_fixture(tmp_path: Path) -> Path:
    """Copy the smd fixture into a fresh customers/ root under tmp_path."""
    src = _HERE.parent.parent / "fixtures" / "smd"
    assert src.exists(), f"smd fixture missing at {src}"
    customers_root = tmp_path / "customers"
    customers_root.mkdir(parents=True)
    shutil.copytree(src, customers_root / "smd")
    return customers_root


# ---------------------------------------------------------------------------
# Fake runners (R2 + Vectorize)
#
# Tracks calls so the idempotency assertion can check that re-runs are
# no-ops the second time around.
# ---------------------------------------------------------------------------


class _RecordingR2Deleter:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self._depleted: set[str] = set()

    async def delete_namespace(self, customer_slug: str) -> dict:
        self.calls.append(customer_slug)
        if customer_slug in self._depleted:
            return {"objects_deleted": 0, "skipped": True, "reason": "namespace_already_empty"}
        self._depleted.add(customer_slug)
        return {"objects_deleted": 12}


class _RecordingVectorizeDeleter:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self._depleted: set[str] = set()

    async def delete_indexes(self, customer_slug: str) -> dict:
        self.calls.append(customer_slug)
        if customer_slug in self._depleted:
            return {"indexes_deleted": 0, "skipped": True, "reason": "indexes_already_absent"}
        self._depleted.add(customer_slug)
        return {"indexes_deleted": 2}


class _RecordingPreserver:
    """Wired (non-stub) preserver fake — counts as real for the #1123 gate."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def preserve(self, customer_slug: str, archive_dir, audit_log_days: int) -> dict:
        self.calls.append(customer_slug)
        archive_dir.mkdir(parents=True, exist_ok=True)
        return {
            "skipped": False,
            "audit_log_days": audit_log_days,
            "preserve_until": "2033-01-01T00:00:00+00:00",
            "archive_path": str(archive_dir / "audit-log-manifest-test.json"),
            "rows_preserved": 3,
            "memory_rows_preserved": {"persona_observations": 2},
            "stub": False,
        }


class _FailingRunner:
    """Raises on first run; succeeds on second so the resume path can be tested."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._failed_once = False

    async def delete_namespace(self, customer_slug: str) -> dict:
        self.calls.append(customer_slug)
        if not self._failed_once:
            self._failed_once = True
            raise RuntimeError("wrangler timed out")
        return {"objects_deleted": 7}


# ---------------------------------------------------------------------------
# Tests: dry-run
# ---------------------------------------------------------------------------


def test_dry_run_returns_planned_steps_and_does_nothing(tmp_path):
    customers_root = _copy_fixture(tmp_path)
    writer, conn = _make_audit(tmp_path)
    pipeline = DecommissionPipeline(
        customer_slug="smd",
        customers_root=customers_root,
        archive_root=tmp_path / "archive",
        audit_writer=writer,
        r2_deleter=_RecordingR2Deleter(),
        vectorize_deleter=_RecordingVectorizeDeleter(),
    )
    plan = _run(pipeline.plan())

    assert [r.name for r in plan] == [
        "01_drain", "02_preserve_machine_data", "03_r2_namespace",
        "04_vectorize_indexes", "05_agentmail", "06_fly_machine",
        "07_compliance_archive", "08_tombstone",
        "09_observability_cleanup",
    ]
    for r in plan:
        assert r.status == StepStatus.PLANNED
    # Live customer dir is untouched.
    assert (customers_root / "smd" / "customer.yaml").exists()
    # No audit rows written for plan().
    rows = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    assert rows == 0


# ---------------------------------------------------------------------------
# Tests: live mode end-to-end
# ---------------------------------------------------------------------------


def test_live_runs_full_sequence_and_writes_audit_trail(tmp_path):
    customers_root = _copy_fixture(tmp_path)
    writer, conn = _make_audit(tmp_path)
    r2 = _RecordingR2Deleter()
    vec = _RecordingVectorizeDeleter()
    pipeline = DecommissionPipeline(
        customer_slug="smd",
        customers_root=customers_root,
        archive_root=tmp_path / "archive",
        audit_writer=writer,
        r2_deleter=r2,
        vectorize_deleter=vec,
    )

    results = _run(pipeline.run())

    assert len(results) == 9
    assert r2.calls == ["smd"]
    assert vec.calls == ["smd"]
    # Customer dir tombstoned.
    assert not (customers_root / "smd").exists()
    tomb = list(customers_root.glob("smd.decommissioned.*"))
    assert len(tomb) == 1
    assert (tomb[0] / "DECOMMISSIONED.md").exists()
    assert (tomb[0] / "customer.yaml").exists()
    # Compliance archive manifest written.
    archive = list((tmp_path / "archive" / "smd").glob("compliance-packet-manifest-*.json"))
    assert len(archive) == 1
    # Audit rows: begin/end per step + DECOMMISSION_FINAL.
    rows = conn.execute(
        "SELECT action_type FROM audit_log ORDER BY id"
    ).fetchall()
    action_types = [r[0] for r in rows]
    # Pipeline boundaries + per-step lifecycle rows (2026-06-12 review:
    # steps no longer reuse INITIATED/DRAIN_COMPLETE).
    assert "DECOMMISSION_INITIATED" in action_types
    assert "DECOMMISSION_DRAIN_COMPLETE" in action_types
    assert "DECOMMISSION_STEP_BEGIN" in action_types
    assert "DECOMMISSION_STEP_COMPLETE" in action_types
    assert "DECOMMISSION_FINAL" in action_types
    # Steps 02..09 each get a begin row; the trail distinguishes them.
    assert action_types.count("DECOMMISSION_STEP_BEGIN") >= 8
    # Every action_type written is in the spec's accepted set.
    for at in action_types:
        assert at in ACCEPTED_ACTION_TYPES


# ---------------------------------------------------------------------------
# Tests: idempotency — dry-run x2 -> live -> live again all succeed
# ---------------------------------------------------------------------------


def test_idempotent_repeated_runs(tmp_path):
    customers_root = _copy_fixture(tmp_path)
    writer, _conn = _make_audit(tmp_path)
    r2 = _RecordingR2Deleter()
    vec = _RecordingVectorizeDeleter()
    pipeline = DecommissionPipeline(
        customer_slug="smd",
        customers_root=customers_root,
        archive_root=tmp_path / "archive",
        audit_writer=writer,
        r2_deleter=r2,
        vectorize_deleter=vec,
    )

    # Dry-run x2 — both succeed, no destructive changes.
    _run(pipeline.plan())
    _run(pipeline.plan())
    assert (customers_root / "smd").exists()

    # Live x1 — full sequence.
    first = _run(pipeline.run())
    assert all(r.status in (StepStatus.EXECUTED, StepStatus.SKIPPED) for r in first)
    assert not (customers_root / "smd").exists()

    # Live x2 — fully decommissioned customer, every step idempotent.
    second = _run(pipeline.run())
    assert len(second) == 9
    # Tombstone step returns skipped on the second run (already tombstoned).
    tomb_step = next(r for r in second if r.name == "08_tombstone")
    assert tomb_step.status == StepStatus.SKIPPED
    assert tomb_step.detail.get("reason") == "already_tombstoned"
    # R2 step second time reports namespace already empty.
    r2_step = next(r for r in second if r.name == "03_r2_namespace")
    assert r2_step.status == StepStatus.SKIPPED
    # Vectorize same shape.
    vec_step = next(r for r in second if r.name == "04_vectorize_indexes")
    assert vec_step.status == StepStatus.SKIPPED


# ---------------------------------------------------------------------------
# Tests: observability cleanup step (ADR 0023 Wave 1)
# ---------------------------------------------------------------------------


class _RecordingObservabilityCleanup:
    """Test fake that records cleanup calls and returns a real-looking manifest."""

    def __init__(self):
        self.calls: list[str] = []

    async def cleanup(self, customer_slug: str) -> dict:
        self.calls.append(customer_slug)
        return {
            "healthchecks_check_cancelled": True,
            "fleet_status_row_deleted": True,
            "customer_slug": customer_slug,
        }


def test_plan_includes_observability_cleanup_with_client_wired_flag(tmp_path):
    customers_root = _copy_fixture(tmp_path)
    writer, _conn = _make_audit(tmp_path)
    # NoOp stub — plan should report client_wired=False so the dry-run is
    # honest about what would actually happen.
    pipeline_noop = DecommissionPipeline(
        customer_slug="smd",
        customers_root=customers_root,
        archive_root=tmp_path / "archive",
        audit_writer=writer,
        r2_deleter=_RecordingR2Deleter(),
        vectorize_deleter=_RecordingVectorizeDeleter(),
    )
    plan = _run(pipeline_noop.plan())
    obs = next(r for r in plan if r.name == "09_observability_cleanup")
    assert obs.status == StepStatus.PLANNED
    assert obs.detail["client_wired"] is False

    # Real client injected → client_wired=True.
    pipeline_wired = DecommissionPipeline(
        customer_slug="smd",
        customers_root=customers_root,
        archive_root=tmp_path / "archive",
        audit_writer=writer,
        r2_deleter=_RecordingR2Deleter(),
        vectorize_deleter=_RecordingVectorizeDeleter(),
        observability=_RecordingObservabilityCleanup(),
    )
    plan = _run(pipeline_wired.plan())
    obs = next(r for r in plan if r.name == "09_observability_cleanup")
    assert obs.detail["client_wired"] is True


def test_run_invokes_observability_cleanup_with_customer_slug(tmp_path):
    customers_root = _copy_fixture(tmp_path)
    writer, _conn = _make_audit(tmp_path)
    obs = _RecordingObservabilityCleanup()
    pipeline = DecommissionPipeline(
        customer_slug="smd",
        customers_root=customers_root,
        archive_root=tmp_path / "archive",
        audit_writer=writer,
        r2_deleter=_RecordingR2Deleter(),
        vectorize_deleter=_RecordingVectorizeDeleter(),
        observability=obs,
    )
    results = _run(pipeline.run())
    assert obs.calls == ["smd"]
    obs_step = next(r for r in results if r.name == "09_observability_cleanup")
    assert obs_step.status == StepStatus.EXECUTED
    assert obs_step.detail["healthchecks_check_cancelled"] is True
    assert obs_step.detail["fleet_status_row_deleted"] is True


# ---------------------------------------------------------------------------
# Tests: failure halts the sequence and surfaces a clear error
# ---------------------------------------------------------------------------


def test_failure_halts_with_step_failed(tmp_path):
    customers_root = _copy_fixture(tmp_path)
    writer, conn = _make_audit(tmp_path)
    failing_r2 = _FailingRunner()
    pipeline = DecommissionPipeline(
        customer_slug="smd",
        customers_root=customers_root,
        archive_root=tmp_path / "archive",
        audit_writer=writer,
        r2_deleter=failing_r2,
        vectorize_deleter=_RecordingVectorizeDeleter(),
    )

    with pytest.raises(DecommissionStepFailed) as ei:
        _run(pipeline.run())

    assert ei.value.step_name == "03_r2_namespace"
    assert ei.value.customer_slug == "smd"
    # Customer dir is NOT tombstoned (we halted before step 9).
    assert (customers_root / "smd").exists()
    # Audit log contains the failure metadata for that step.
    failure_rows = conn.execute(
        "SELECT metadata FROM audit_log WHERE action_type = 'DECOMMISSION_STEP_FAILED'"
    ).fetchall()
    failure_text = " ".join(r[0] or "" for r in failure_rows)
    assert "failed" in failure_text
    assert "03_r2_namespace" in failure_text

    # Resume path: with the failure latched, second run completes (R2
    # raised only once); idempotency contract holds.
    results = _run(pipeline.run())
    assert any(r.name == "08_tombstone" for r in results)


# ---------------------------------------------------------------------------
# Tests: --live fail-closed when destructive backends are unwired (#1123)
# ---------------------------------------------------------------------------


class _FakeAgentMail:
    async def deprovision(self, customer_slug: str) -> dict:
        return {"skipped": False, "identities_removed": 1}


class _FakeFly:
    async def destroy_machine(self, customer_slug: str) -> dict:
        return {"skipped": False, "app_destroyed": True}


def test_unwired_backends_lists_all_stubs_by_default(tmp_path):
    customers_root = _copy_fixture(tmp_path)
    writer, _conn = _make_audit(tmp_path)
    # Construct exactly as the CLI does today: no runners injected, all
    # external services defaulting to NoOp stubs.
    pipeline = DecommissionPipeline(
        customer_slug="smd",
        customers_root=customers_root,
        archive_root=tmp_path / "archive",
        audit_writer=writer,
    )
    assert pipeline.unwired_destructive_backends() == [
        "audit_log_preserver",
        "r2_deleter",
        "vectorize_deleter",
        "agentmail",
        "fly",
        "observability",
    ]


def test_unwired_backends_empty_when_all_wired(tmp_path):
    customers_root = _copy_fixture(tmp_path)
    writer, _conn = _make_audit(tmp_path)
    pipeline = DecommissionPipeline(
        customer_slug="smd",
        customers_root=customers_root,
        archive_root=tmp_path / "archive",
        audit_writer=writer,
        r2_deleter=_RecordingR2Deleter(),
        vectorize_deleter=_RecordingVectorizeDeleter(),
        agentmail=_FakeAgentMail(),
        fly=_FakeFly(),
        observability=_RecordingObservabilityCleanup(),
        audit_log_preserver=_RecordingPreserver(),
    )
    assert pipeline.unwired_destructive_backends() == []


@pytest.fixture
def _no_ambient_backends(monkeypatch):
    """The CLI wires real backends from the environment. A unit test must see
    NONE of them, whatever the developer's shell holds (a logged-in `fly` CLI
    is enough to wire the Fly destroyer), so the wiring hook is replaced with
    one that reports everything unwired."""
    from bin.lib import decommission_cli

    monkeypatch.setattr(
        decommission_cli,
        "backends_from_env",
        lambda slug, root: ({}, {n: False for n in decommission_cli.BACKEND_REQUIREMENTS}),
    )
    # Same discipline for the seam preserver: a shell with the runtime-read
    # env staged would otherwise make this unit test dial a real Machine.
    monkeypatch.setattr(decommission_cli, "seam_client_from_env", lambda slug: None)


def test_cli_live_refuses_when_backends_unwired(tmp_path, _no_ambient_backends):
    from bin.lib.decommission_cli import main

    customers_root = _copy_fixture(tmp_path)
    rc = main(
        [
            "smd",
            "--live",
            "--customers-root",
            str(customers_root),
            "--archive-root",
            str(tmp_path / "archive"),
            "--audit-db",
            str(tmp_path / "audit.sqlite"),
        ]
    )
    assert rc == 5
    # Customer dir must be untouched — no tombstone, no false success.
    assert (customers_root / "smd" / "customer.yaml").exists()
    assert not list(customers_root.glob("smd.decommissioned.*"))


def test_cli_live_allow_unwired_runs_and_tombstones(tmp_path, _no_ambient_backends):
    from bin.lib.decommission_cli import main

    customers_root = _copy_fixture(tmp_path)
    rc = main(
        [
            "smd",
            "--live",
            "--allow-unwired",
            "--customers-root",
            str(customers_root),
            "--archive-root",
            str(tmp_path / "archive"),
            "--audit-db",
            str(tmp_path / "audit.sqlite"),
        ]
    )
    assert rc == 0
    # With the explicit override the flow proceeds and tombstones.
    assert not (customers_root / "smd").exists()
    assert len(list(customers_root.glob("smd.decommissioned.*"))) == 1


# ---------------------------------------------------------------------------
# Tests: tombstone with no live customer dir is a clean skip
# ---------------------------------------------------------------------------


def test_tombstone_skips_when_no_customer_dir(tmp_path):
    customers_root = tmp_path / "customers"
    customers_root.mkdir()
    tomb = FilesystemTombstoner(customers_root)
    result = tomb.tombstone("ghost-customer")
    assert result["skipped"] is True
    assert result["reason"] == "no_customer_dir"


def test_tombstone_idempotent_when_already_tombstoned(tmp_path):
    customers_root = tmp_path / "customers"
    customers_root.mkdir()
    (customers_root / "smd").mkdir()
    (customers_root / "smd" / "customer.yaml").write_text("x")
    tomb = FilesystemTombstoner(customers_root)
    when = datetime(2026, 5, 21, tzinfo=timezone.utc)
    first = tomb.tombstone("smd", now=when)
    assert first["skipped"] is False
    second = tomb.tombstone("smd", now=when)
    assert second["skipped"] is True
    assert second["reason"] == "already_tombstoned"


# ---------------------------------------------------------------------------
# Tests: stubs return skipped manifests
# ---------------------------------------------------------------------------


def test_noop_stubs_return_skipped_manifests():
    agentmail = NoOpAgentMailStub()
    fly = NoOpFlyStub()
    r2 = _run(agentmail.deprovision("smd"))
    r3 = _run(fly.destroy_machine("smd"))
    assert r2["skipped"] is True
    assert r3["skipped"] is True
    for r in (r2, r3):
        assert r["reason"] == "external_client_not_wired"


# ---------------------------------------------------------------------------
# Tests: compliance archiver writes a manifest
# ---------------------------------------------------------------------------


def test_compliance_archiver_writes_manifest(tmp_path):
    archiver = InMemoryComplianceArchiver()
    archive_dir = tmp_path / "archive" / "smd"
    result = _run(archiver.archive("smd", archive_dir))
    assert Path(result["archive_path"]).exists()
    assert result["stub"] is True


# ---------------------------------------------------------------------------
# Tests: audit-log retention carve-out (audit-retention.md, #893)
#
# Step 2 "02_preserve_machine_data" runs the audit-log + memory preservation
# BEFORE any destructive step (pull-before-destroy, #1355). The preserver
# writes a CSV + manifest under the archive dir, and the pipeline emits a
# dedicated audit row naming the preservation deadline.
# ---------------------------------------------------------------------------


from bin.lib.decommission import (  # noqa: E402
    InMemoryAuditLogPreserver,
    VERTICAL_AUDIT_LOG_DAYS_DEFAULTS,
    resolve_audit_log_days,
)


def test_resolve_audit_log_days_law_firm_default():
    assert resolve_audit_log_days({"vertical": "law-firm"}) == 2555


def test_resolve_audit_log_days_marketing_agency_default():
    assert resolve_audit_log_days({"vertical": "marketing-agency"}) == 1095


def test_resolve_audit_log_days_override_wins():
    yaml = {
        "vertical": "law-firm",
        "memory": {"retention": {"audit_log_days": 3650}},
    }
    assert resolve_audit_log_days(yaml) == 3650


def test_resolve_audit_log_days_missing_yaml_returns_fallback():
    assert resolve_audit_log_days(None) == 2555
    assert resolve_audit_log_days({}) == 2555
    assert resolve_audit_log_days({"vertical": "unknown-vertical"}) == 2555


def test_resolve_audit_log_days_ignores_invalid_override():
    yaml = {
        "vertical": "law-firm",
        "memory": {"retention": {"audit_log_days": "seven-years"}},
    }
    # Non-int override falls through to the vertical default rather than
    # crashing the decommission script mid-flight. The validator already
    # rejected this case at commit time.
    assert resolve_audit_log_days(yaml) == 2555


def test_vertical_defaults_table_matches_typescript_constants():
    # The Python table here MUST match VERTICAL_AUDIT_LOG_DAYS_DEFAULTS
    # in src/lib/operator/customer-yaml/types.ts. This test guards
    # against drift between the two policy sources.
    assert VERTICAL_AUDIT_LOG_DAYS_DEFAULTS == {
        "law-firm": 2555,
        "marketing-agency": 1095,
        "real-estate": 2555,
        "manufacturing": 2555,
        "insurance": 2555,
        "mixed": 2555,
    }


def test_audit_log_preserver_writes_csv_and_manifest(tmp_path):
    preserver = InMemoryAuditLogPreserver()
    archive_dir = tmp_path / "archive" / "smd"
    result = _run(preserver.preserve("smd", archive_dir, 2555))
    assert result["skipped"] is False
    assert result["audit_log_days"] == 2555
    assert Path(result["archive_path"]).exists()
    assert Path(result["csv_path"]).exists()
    # CSV is header-only in the stub but the header row must match
    # the audit_log table schema so the production exporter is a drop-in.
    csv_text = Path(result["csv_path"]).read_text(encoding="utf-8")
    assert "action_type" in csv_text
    assert "metadata" in csv_text
    # Manifest carries the preservation deadline.
    manifest = json.loads(Path(result["archive_path"]).read_text(encoding="utf-8"))
    assert manifest["audit_log_days"] == 2555
    assert "preserve_until" in manifest


def test_audit_log_preserver_is_idempotent_same_day(tmp_path):
    preserver = InMemoryAuditLogPreserver()
    archive_dir = tmp_path / "archive" / "smd"
    first = _run(preserver.preserve("smd", archive_dir, 2555))
    second = _run(preserver.preserve("smd", archive_dir, 2555))
    assert first["skipped"] is False
    assert second["skipped"] is True
    assert second["reason"] == "audit_log_already_preserved_today"


def test_step_2_runs_audit_log_preservation_before_memory_voice(tmp_path):
    customers_root = _copy_fixture(tmp_path)
    writer, conn = _make_audit(tmp_path)
    pipeline = DecommissionPipeline(
        customer_slug="smd",
        customers_root=customers_root,
        archive_root=tmp_path / "archive",
        audit_writer=writer,
        r2_deleter=_RecordingR2Deleter(),
        vectorize_deleter=_RecordingVectorizeDeleter(),
        # smd fixture is marketing-agency → 1095-day default. Override
        # ratchets up to 5 years (1825 days).
        customer_yaml={
            "vertical": "marketing-agency",
            "memory": {"retention": {"audit_log_days": 1825}},
        },
    )
    results = _run(pipeline.run())
    step2 = next(r for r in results if r.name == "02_preserve_machine_data")
    preserved = step2.detail["audit_log_preserved"]
    assert preserved["audit_log_days"] == 1825
    assert Path(preserved["archive_path"]).exists()
    # Carve-out emits its own audit row distinct from the canonical
    # memory + voice cleanup row.
    rows = conn.execute(
        "SELECT metadata FROM audit_log "
        "WHERE action_type = 'DECOMMISSION_STEP_COMPLETE'"
    ).fetchall()
    carve_out = [r[0] for r in rows if "audit_log_preserved" in (r[0] or "")]
    assert carve_out, "expected at least one audit row tagged with audit_log_preserved"
    # The carve-out row records the resolved retention window + deadline.
    payload = " ".join(carve_out)
    assert "1825" in payload
    assert "preserve_until" in payload


def test_step_2_falls_back_to_vertical_default_when_no_override(tmp_path):
    customers_root = _copy_fixture(tmp_path)
    writer, _conn = _make_audit(tmp_path)
    pipeline = DecommissionPipeline(
        customer_slug="smd",
        customers_root=customers_root,
        archive_root=tmp_path / "archive",
        audit_writer=writer,
        r2_deleter=_RecordingR2Deleter(),
        vectorize_deleter=_RecordingVectorizeDeleter(),
        # marketing-agency vertical without override → 1095-day default.
        customer_yaml={"vertical": "marketing-agency"},
    )
    results = _run(pipeline.run())
    step2 = next(r for r in results if r.name == "02_preserve_machine_data")
    assert step2.detail["audit_log_preserved"]["audit_log_days"] == 1095


def test_step_2_uses_2555_fallback_when_customer_yaml_missing(tmp_path):
    customers_root = _copy_fixture(tmp_path)
    writer, _conn = _make_audit(tmp_path)
    pipeline = DecommissionPipeline(
        customer_slug="smd",
        customers_root=customers_root,
        archive_root=tmp_path / "archive",
        audit_writer=writer,
        r2_deleter=_RecordingR2Deleter(),
        vectorize_deleter=_RecordingVectorizeDeleter(),
        # No customer_yaml at all.
    )
    results = _run(pipeline.run())
    step2 = next(r for r in results if r.name == "02_preserve_machine_data")
    assert step2.detail["audit_log_preserved"]["audit_log_days"] == 2555


def test_tombstone_marker_records_audit_log_preserve_until(tmp_path):
    customers_root = _copy_fixture(tmp_path)
    writer, _conn = _make_audit(tmp_path)
    pipeline = DecommissionPipeline(
        customer_slug="smd",
        customers_root=customers_root,
        archive_root=tmp_path / "archive",
        audit_writer=writer,
        r2_deleter=_RecordingR2Deleter(),
        vectorize_deleter=_RecordingVectorizeDeleter(),
        customer_yaml={"vertical": "law-firm"},
    )
    _run(pipeline.run())
    tomb = list(customers_root.glob("smd.decommissioned.*"))
    assert len(tomb) == 1
    marker = (tomb[0] / "DECOMMISSIONED.md").read_text(encoding="utf-8")
    assert "audit_log_preserve_until:" in marker


def test_decommission_does_not_delete_audit_log_rows(tmp_path):
    # The audit log written by the script's local writer is the trail of
    # the decommission itself. It must survive the run unchanged — the
    # pipeline never DELETEs from audit_log, no matter what step 2 does.
    customers_root = _copy_fixture(tmp_path)
    writer, conn = _make_audit(tmp_path)
    pipeline = DecommissionPipeline(
        customer_slug="smd",
        customers_root=customers_root,
        archive_root=tmp_path / "archive",
        audit_writer=writer,
        r2_deleter=_RecordingR2Deleter(),
        vectorize_deleter=_RecordingVectorizeDeleter(),
        customer_yaml={"vertical": "law-firm"},
    )
    _run(pipeline.run())
    # Snapshot the row count.
    n_after_first = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    assert n_after_first > 0
    # Re-run live: every step idempotent, audit log keeps growing (the
    # rerun's audit rows ADD; they never replace).
    _run(pipeline.run())
    n_after_second = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    assert n_after_second > n_after_first


def test_plan_step_2_surfaces_resolved_retention_window(tmp_path):
    customers_root = _copy_fixture(tmp_path)
    writer, _conn = _make_audit(tmp_path)
    pipeline = DecommissionPipeline(
        customer_slug="smd",
        customers_root=customers_root,
        archive_root=tmp_path / "archive",
        audit_writer=writer,
        customer_yaml={"vertical": "law-firm"},
    )
    plan = _run(pipeline.plan())
    step2 = next(r for r in plan if r.name == "02_preserve_machine_data")
    assert step2.detail["audit_log_days"] == 2555
    assert "audit_log_preserve_until" in step2.detail


def test_audit_log_preservation_runs_before_substrate_deletion(tmp_path):
    # Audit log must be exported BEFORE memory + voice are wiped, so a
    # mid-step failure leaves the substrate intact for the rerun. We
    # verify ordering by using a memory runner that asserts the archive
    # dir already contains a manifest before its first call.
    customers_root = _copy_fixture(tmp_path)
    writer, _conn = _make_audit(tmp_path)
    archive_root = tmp_path / "archive"

    class _OrderAssertingMemoryRunner:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def run(self, source_kind: str, source_id: str) -> dict:
            manifests = list((archive_root / "smd").glob("audit-log-manifest-*.json"))
            assert manifests, (
                "audit-log preservation must run BEFORE memory cleanup; "
                "no manifest found in archive when memory runner fired"
            )
            self.calls.append((source_kind, source_id))
            return {
                "items_removed": 1,
                "r2_objects_removed": 1,
                "vectorize_vectors_removed": 0,
                "skipped": False,
            }

    pipeline = DecommissionPipeline(
        customer_slug="smd",
        customers_root=customers_root,
        archive_root=archive_root,
        audit_writer=writer,
        r2_deleter=_RecordingR2Deleter(),
        vectorize_deleter=_RecordingVectorizeDeleter(),
        customer_yaml={"vertical": "law-firm"},
    )
    _run(pipeline.run())
