"""Tests for invariant #7 - cross-Machine query prohibition (boot-time).

Real Phase-1 storage model (see invariant_7.py module docstring):

* Per-customer R2 skill-bodies bucket: ``ss-operator-{slug}-skills``.
* Shared R2 config bucket: ``smd-customer-config`` (slug isolation is in
  the key path, not the bucket name).
* SQLite paths under the per-Machine volume ``/opt/data`` (the volume is
  the customer boundary per ADR 0007).
* Slug agreement between ``CUSTOMER_SLUG`` (runtime) and
  ``SMD_CUSTOMER_SLUG`` (overlay).

Coverage:

* PASSES when every binding resolves to the customer's namespace.
* FIRES on the explicit cross-Machine failure modes:
    - skill-bodies bucket naming ANOTHER customer's slug;
    - SQLite path embedding another customer's slug;
    - SQLite path escaping the volume root;
    - config bucket pointed at a per-customer skill bucket;
    - overlay/runtime slug disagreement.
* FIRES on empty / unbound bindings.
* FIRES on a malformed customer slug.
* Reports the correct failure-mode reason text per mismatch.
* Generates a stable ``refusal_message`` for stdout.
* Generates audit metadata with the documented shape.
* The boolean ``__bool__`` flips on violation.
* ``collect_snapshot_from_env`` reads the real env var names.
* ``verify_at_boot`` returns 0 on pass and 3 on violation, emits the
  audit row through an injected broker socket, and imports without pytest.

Run from repo root:

    cd operator && uv run --with pytest python -m pytest \
        safety-substrate/tests/test_invariant_7.py -v
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))  # safety-substrate/ on path

from invariants.invariant_7 import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    BindingKind,
    BindingSnapshot,
    collect_snapshot_from_env,
    run as run_module_self_check,
    verify_at_boot,
    verify_storage_bindings,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok_snapshot(slug: str = "acme") -> BindingSnapshot:
    return BindingSnapshot(
        customer_slug=slug,
        overlay_slug=slug,
        skill_bodies_bucket=f"ss-operator-{slug}-skills",
        config_bucket="smd-customer-config",
        audit_db_path="/opt/data/audit/audit.db",
        agent_state_db_path="/opt/data/agent-state.db",
    )


def _ok_env(slug: str = "acme") -> dict[str, str]:
    return {
        "CUSTOMER_SLUG": slug,
        "SMD_CUSTOMER_SLUG": slug,
        "R2_SKILL_BODIES_BUCKET": f"ss-operator-{slug}-skills",
        "R2_BUCKET_CONFIG": "smd-customer-config",
        "SMD_D1_AUDIT_BINDING": "/opt/data/audit/audit.db",
        "SMD_D1_AGENT_STATE_BINDING": "/opt/data/agent-state.db",
    }


# ---------------------------------------------------------------------------
# BindingSnapshot contract
# ---------------------------------------------------------------------------


def test_snapshot_rejects_non_string_field():
    with pytest.raises(TypeError):
        BindingSnapshot(
            customer_slug="x",
            overlay_slug="x",
            skill_bodies_bucket=42,  # type: ignore[arg-type]
            config_bucket="smd-customer-config",
            audit_db_path="/opt/data/audit/audit.db",
            agent_state_db_path="/opt/data/agent-state.db",
        )


def test_snapshot_to_kind_map_covers_every_kind():
    m = _ok_snapshot().to_kind_map()
    assert set(m.keys()) == set(BindingKind)


# ---------------------------------------------------------------------------
# Passing case
# ---------------------------------------------------------------------------


def test_passes_when_every_binding_matches_slug():
    result = verify_storage_bindings(_ok_snapshot())
    assert result.passed
    assert result.mismatches == ()
    assert result.refusal_message() == ""
    assert not bool(result)


def test_passes_for_a_different_valid_slug():
    result = verify_storage_bindings(_ok_snapshot("ashton-price"))
    assert result.passed


# ---------------------------------------------------------------------------
# Cross-Machine failure mode: foreign skill-bodies bucket (the named edge)
# ---------------------------------------------------------------------------


def test_fires_on_foreign_skill_bodies_bucket():
    """The named PRD §7.5 invariant - the per-customer skill-bodies bucket
    points at ANOTHER customer's bucket. This is the existential
    isolation-leak failure mode.
    """
    snap = BindingSnapshot(
        customer_slug="acme",
        overlay_slug="acme",
        skill_bodies_bucket="ss-operator-other-skills",  # foreign tenant
        config_bucket="smd-customer-config",
        audit_db_path="/opt/data/audit/audit.db",
        agent_state_db_path="/opt/data/agent-state.db",
    )
    result = verify_storage_bindings(snap)
    assert not result.passed
    assert bool(result)
    assert len(result.mismatches) == 1
    m = result.mismatches[0]
    assert m.kind is BindingKind.SKILL_BODIES_BUCKET
    assert m.expected == "ss-operator-acme-skills"
    assert m.observed == "ss-operator-other-skills"
    assert "cross-Machine isolation failure mode" in m.reason
    assert "other" in m.reason


def test_fires_on_wrong_shaped_skill_bucket():
    # Right prefix is absent entirely -> not a foreign per-customer bucket,
    # just a wrong/malformed bucket. Still a violation, different reason.
    snap = BindingSnapshot(
        customer_slug="acme",
        overlay_slug="acme",
        skill_bodies_bucket="some-random-bucket",
        config_bucket="smd-customer-config",
        audit_db_path="/opt/data/audit/audit.db",
        agent_state_db_path="/opt/data/agent-state.db",
    )
    result = verify_storage_bindings(snap)
    assert not result.passed
    m = result.mismatches[0]
    assert m.kind is BindingKind.SKILL_BODIES_BUCKET
    assert "does not match the per-customer name" in m.reason
    assert "cross-Machine" not in m.reason


# ---------------------------------------------------------------------------
# Cross-Machine failure mode: SQLite paths
# ---------------------------------------------------------------------------


def test_fires_on_audit_path_with_foreign_slug():
    snap = BindingSnapshot(
        customer_slug="acme",
        overlay_slug="acme",
        skill_bodies_bucket="ss-operator-acme-skills",
        config_bucket="smd-customer-config",
        audit_db_path="/opt/data/other/audit.db",  # foreign slug segment
        agent_state_db_path="/opt/data/agent-state.db",
    )
    result = verify_storage_bindings(snap)
    assert not result.passed
    assert len(result.mismatches) == 1
    m = result.mismatches[0]
    assert m.kind is BindingKind.AUDIT_DB
    assert "embeds another customer's slug" in m.reason
    assert "cross-Machine isolation failure mode" in m.reason


def test_fires_on_path_escaping_the_volume():
    snap = BindingSnapshot(
        customer_slug="acme",
        overlay_slug="acme",
        skill_bodies_bucket="ss-operator-acme-skills",
        config_bucket="smd-customer-config",
        audit_db_path="/opt/data/audit/audit.db",
        agent_state_db_path="/etc/passwd",  # escapes the volume
    )
    result = verify_storage_bindings(snap)
    assert not result.passed
    m = result.mismatches[0]
    assert m.kind is BindingKind.AGENT_STATE_DB
    assert "outside the per-Machine volume root" in m.reason


def test_fires_on_dotdot_traversal_out_of_volume():
    snap = BindingSnapshot(
        customer_slug="acme",
        overlay_slug="acme",
        skill_bodies_bucket="ss-operator-acme-skills",
        config_bucket="smd-customer-config",
        audit_db_path="/opt/data/../secrets/audit.db",  # ../ escapes
        agent_state_db_path="/opt/data/agent-state.db",
    )
    result = verify_storage_bindings(snap)
    assert not result.passed
    m = result.mismatches[0]
    assert m.kind is BindingKind.AUDIT_DB
    assert "outside the per-Machine volume root" in m.reason


def test_fires_on_relative_path():
    snap = BindingSnapshot(
        customer_slug="acme",
        overlay_slug="acme",
        skill_bodies_bucket="ss-operator-acme-skills",
        config_bucket="smd-customer-config",
        audit_db_path="opt/data/audit/audit.db",  # not absolute
        agent_state_db_path="/opt/data/agent-state.db",
    )
    result = verify_storage_bindings(snap)
    assert not result.passed
    assert result.mismatches[0].kind is BindingKind.AUDIT_DB


# ---------------------------------------------------------------------------
# Config-bucket failure mode
# ---------------------------------------------------------------------------


def test_config_bucket_shared_default_passes():
    # The shared config bucket need NOT embed the slug.
    assert verify_storage_bindings(_ok_snapshot()).passed


def test_fires_when_config_bucket_points_at_a_per_customer_bucket():
    snap = BindingSnapshot(
        customer_slug="acme",
        overlay_slug="acme",
        skill_bodies_bucket="ss-operator-acme-skills",
        config_bucket="ss-operator-other-skills",  # foreign per-customer ns
        audit_db_path="/opt/data/audit/audit.db",
        agent_state_db_path="/opt/data/agent-state.db",
    )
    result = verify_storage_bindings(snap)
    assert not result.passed
    m = next(x for x in result.mismatches if x.kind is BindingKind.CONFIG_BUCKET)
    assert "cross-Machine isolation failure mode" in m.reason


# ---------------------------------------------------------------------------
# Slug-agreement failure mode
# ---------------------------------------------------------------------------


def test_fires_on_overlay_runtime_slug_disagreement():
    snap = BindingSnapshot(
        customer_slug="acme",
        overlay_slug="other",  # overlay namespaces a different customer
        skill_bodies_bucket="ss-operator-acme-skills",
        config_bucket="smd-customer-config",
        audit_db_path="/opt/data/audit/audit.db",
        agent_state_db_path="/opt/data/agent-state.db",
    )
    result = verify_storage_bindings(snap)
    assert not result.passed
    assert any(
        "disagrees with" in m.reason and "cross-Machine isolation failure mode" in m.reason for m in result.mismatches
    )


# ---------------------------------------------------------------------------
# Empty / unbound bindings
# ---------------------------------------------------------------------------


def test_fires_on_empty_skill_bucket():
    snap = BindingSnapshot(
        customer_slug="acme",
        overlay_slug="acme",
        skill_bodies_bucket="",  # unbound
        config_bucket="smd-customer-config",
        audit_db_path="/opt/data/audit/audit.db",
        agent_state_db_path="/opt/data/agent-state.db",
    )
    result = verify_storage_bindings(snap)
    assert not result.passed
    m = result.mismatches[0]
    assert m.kind is BindingKind.SKILL_BODIES_BUCKET
    assert "unbound" in m.reason


def test_fires_on_empty_audit_path():
    snap = BindingSnapshot(
        customer_slug="acme",
        overlay_slug="acme",
        skill_bodies_bucket="ss-operator-acme-skills",
        config_bucket="smd-customer-config",
        audit_db_path="",  # unbound
        agent_state_db_path="/opt/data/agent-state.db",
    )
    result = verify_storage_bindings(snap)
    assert not result.passed
    m = next(x for x in result.mismatches if x.kind is BindingKind.AUDIT_DB)
    assert "unbound" in m.reason


# ---------------------------------------------------------------------------
# Malformed slug failure mode
# ---------------------------------------------------------------------------


def test_fires_on_uppercase_slug():
    snap = BindingSnapshot(
        customer_slug="Acme",
        overlay_slug="Acme",
        skill_bodies_bucket="ss-operator-Acme-skills",
        config_bucket="smd-customer-config",
        audit_db_path="/opt/data/audit/audit.db",
        agent_state_db_path="/opt/data/agent-state.db",
    )
    result = verify_storage_bindings(snap)
    assert not result.passed
    assert "is not a valid slug" in result.mismatches[0].reason


def test_fires_on_empty_slug():
    snap = BindingSnapshot(
        customer_slug="",
        overlay_slug="",
        skill_bodies_bucket="ss-operator--skills",
        config_bucket="smd-customer-config",
        audit_db_path="/opt/data/audit/audit.db",
        agent_state_db_path="/opt/data/agent-state.db",
    )
    result = verify_storage_bindings(snap)
    assert not result.passed
    assert "is not a valid slug" in result.mismatches[0].reason


def test_fires_on_slug_with_leading_hyphen():
    snap = BindingSnapshot(
        customer_slug="-acme",
        overlay_slug="-acme",
        skill_bodies_bucket="ss-operator--acme-skills",
        config_bucket="smd-customer-config",
        audit_db_path="/opt/data/audit/audit.db",
        agent_state_db_path="/opt/data/agent-state.db",
    )
    result = verify_storage_bindings(snap)
    assert not result.passed


# ---------------------------------------------------------------------------
# Multiple simultaneous mismatches
# ---------------------------------------------------------------------------


def test_reports_multiple_mismatches():
    snap = BindingSnapshot(
        customer_slug="acme",
        overlay_slug="acme",
        skill_bodies_bucket="ss-operator-other-skills",  # foreign
        config_bucket="",  # empty
        audit_db_path="/etc/passwd",  # escapes
        agent_state_db_path="/opt/data/agent-state.db",
    )
    result = verify_storage_bindings(snap)
    assert not result.passed
    kinds = {m.kind for m in result.mismatches}
    assert BindingKind.SKILL_BODIES_BUCKET in kinds
    assert BindingKind.CONFIG_BUCKET in kinds
    assert BindingKind.AUDIT_DB in kinds


# ---------------------------------------------------------------------------
# Output shape contracts
# ---------------------------------------------------------------------------


def test_refusal_message_names_every_mismatch():
    snap = BindingSnapshot(
        customer_slug="acme",
        overlay_slug="acme",
        skill_bodies_bucket="ss-operator-other-skills",
        config_bucket="smd-customer-config",
        audit_db_path="/etc/passwd",
        agent_state_db_path="/opt/data/agent-state.db",
    )
    result = verify_storage_bindings(snap)
    msg = result.refusal_message()
    assert msg.startswith("INVARIANT_7_VIOLATION:")
    assert "customer_slug='acme'" in msg
    assert "r2_skill_bodies_bucket=" in msg
    assert "smd_d1_audit_binding=" in msg


def test_to_audit_metadata_shape():
    snap = BindingSnapshot(
        customer_slug="acme",
        overlay_slug="acme",
        skill_bodies_bucket="ss-operator-other-skills",
        config_bucket="smd-customer-config",
        audit_db_path="/opt/data/audit/audit.db",
        agent_state_db_path="/opt/data/agent-state.db",
    )
    result = verify_storage_bindings(snap)
    meta = result.to_audit_metadata()
    assert meta["invariant"] == 7
    assert meta["customer_slug"] == "acme"
    assert isinstance(meta["mismatches"], list)
    assert len(meta["mismatches"]) == 1
    m = meta["mismatches"][0]
    assert m["kind"] == "r2_skill_bodies_bucket"
    assert m["expected"] == "ss-operator-acme-skills"
    assert m["observed"] == "ss-operator-other-skills"
    assert "reason" in m


def test_violation_bool_is_truthy_iff_violation():
    assert not bool(verify_storage_bindings(_ok_snapshot()))
    bad = BindingSnapshot(
        customer_slug="acme",
        overlay_slug="acme",
        skill_bodies_bucket="ss-operator-other-skills",
        config_bucket="smd-customer-config",
        audit_db_path="/opt/data/audit/audit.db",
        agent_state_db_path="/opt/data/agent-state.db",
    )
    assert bool(verify_storage_bindings(bad))


# ---------------------------------------------------------------------------
# Env collection
# ---------------------------------------------------------------------------


def test_collect_snapshot_reads_real_env_var_names():
    snap = collect_snapshot_from_env(_ok_env("zeta"))
    assert snap.customer_slug == "zeta"
    assert snap.overlay_slug == "zeta"
    assert snap.skill_bodies_bucket == "ss-operator-zeta-skills"
    assert snap.config_bucket == "smd-customer-config"
    assert snap.audit_db_path == "/opt/data/audit/audit.db"
    assert snap.agent_state_db_path == "/opt/data/agent-state.db"
    assert verify_storage_bindings(snap).passed


def test_collect_snapshot_overlay_slug_falls_back_to_runtime_slug():
    env = _ok_env("zeta")
    del env["SMD_CUSTOMER_SLUG"]
    snap = collect_snapshot_from_env(env)
    # Fallback keeps overlay == runtime so a deployment that only set
    # CUSTOMER_SLUG does not spuriously fail the slug-agreement check.
    assert snap.overlay_slug == "zeta"
    assert verify_storage_bindings(snap).passed


def test_collect_snapshot_missing_vars_become_empty():
    snap = collect_snapshot_from_env({})
    assert snap.customer_slug == ""
    assert snap.skill_bodies_bucket == ""
    assert not verify_storage_bindings(snap).passed


# ---------------------------------------------------------------------------
# Boot entry: verify_at_boot return code + audit emission
# ---------------------------------------------------------------------------


def test_verify_at_boot_returns_zero_on_pass():
    assert verify_at_boot(_ok_env()) == 0


def test_verify_at_boot_returns_three_on_violation():
    bad = _ok_env()
    bad["R2_SKILL_BODIES_BUCKET"] = "ss-operator-other-skills"
    # No broker socket: emission is best-effort and must not change the code.
    assert verify_at_boot(bad) == 3


class _OneShotAuditBroker:
    """Minimal Unix-socket stand-in for the Workspace broker's
    ``audit_append`` verb. Captures the first request's row and replies
    ``{"ok": true, "id": "..."}`` like the real broker.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self.captured: dict | None = None
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(path)
        self._sock.listen(1)
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _serve(self) -> None:
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        with conn:
            raw = conn.makefile("rb").readline()
            try:
                self.captured = json.loads(raw)
            except ValueError:
                self.captured = None
            conn.sendall(json.dumps({"ok": True, "id": "01ABCDEF"}).encode() + b"\n")

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass
        self._thread.join(timeout=2)


def test_verify_at_boot_emits_audit_row_through_broker_socket():
    # AF_UNIX paths are length-capped (~104 chars on macOS), so bind in a
    # short-named tempdir rather than pytest's deep tmp_path.
    import shutil
    import tempfile

    sock_dir = tempfile.mkdtemp(prefix="i7")
    sock_path = os.path.join(sock_dir, "b.sock")
    broker = _OneShotAuditBroker(sock_path)
    broker.start()
    try:
        bad = _ok_env()
        bad["R2_SKILL_BODIES_BUCKET"] = "ss-operator-other-skills"
        bad["SMD_AUDIT_BROKER_SOCKET"] = sock_path
        rc = verify_at_boot(bad)
    finally:
        broker.close()
        shutil.rmtree(sock_dir, ignore_errors=True)

    assert rc == 3
    assert broker.captured is not None
    assert broker.captured["action"] == "audit_append"
    row = broker.captured["row"]
    assert row["action_type"] == "INVARIANT_BOOT_CHECK_FAILED"
    assert row["actor"] == "agent"
    # metadata is a JSON string per the ledger schema (metadata TEXT).
    meta = json.loads(row["metadata"])
    assert meta["invariant"] == 7
    assert meta["customer_slug"] == "acme"
    assert meta["mismatches"][0]["kind"] == "r2_skill_bodies_bucket"


def test_verify_at_boot_refuses_even_when_broker_socket_missing(tmp_path):
    # A dangling socket path: connect() fails. The refusal (rc=3) must
    # still hold — audit-emit must never weaken the boot refusal.
    bad = _ok_env()
    bad["R2_SKILL_BODIES_BUCKET"] = "ss-operator-other-skills"
    bad["SMD_AUDIT_BROKER_SOCKET"] = str(tmp_path / "does-not-exist.sock")
    assert verify_at_boot(bad) == 3


def test_verify_at_boot_imports_without_pytest():
    """The boot path must be importable in the customer Machine venv,
    which has no pytest. Re-import the module with pytest masked from
    sys.modules and confirm verify_at_boot still resolves and runs.
    """
    import importlib

    saved = {k: v for k, v in sys.modules.items() if k == "pytest" or k.startswith("pytest.")}
    for k in list(saved):
        del sys.modules[k]
    sys.modules["pytest"] = None  # poison: any `import pytest` now raises
    try:
        mod = importlib.reload(importlib.import_module("invariants.invariant_7"))
        assert mod.verify_at_boot(_ok_env()) == 0
    finally:
        sys.modules.pop("pytest", None)
        sys.modules.update(saved)
        importlib.reload(importlib.import_module("invariants.invariant_7"))


# ---------------------------------------------------------------------------
# Substrate-runner entrypoint smoke check
# ---------------------------------------------------------------------------


def test_module_run_callable_returns_pass():
    ok, msg = run_module_self_check()
    assert ok, f"module-level run() should pass on a clean import; got: {msg}"
    assert "invariant 7" in msg.lower()


# ---------------------------------------------------------------------------
# Substrate-runner shape (run_invariants.py compatibility)
# ---------------------------------------------------------------------------


def run() -> tuple[bool, str]:
    """Aggregated harness entrypoint for run_invariants.py."""
    return run_module_self_check()


if __name__ == "__main__":
    ok, msg = run()
    print(msg)
    sys.exit(0 if ok else 1)
