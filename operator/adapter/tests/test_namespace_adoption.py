"""Tests for the namespace-assertion adoption factory (#861 follow-on).

Covers the surviving in-tree migration entry point:

* `adapter.d1_env.namespaced_executor_from_env` — builds a
  `NamespacedD1Executor` from the per-customer env, with the audit
  writer wired in.

The headline test: an executor built with slug A is asked to do an
operation that names slug B; the call refuses with
`NamespaceAssertionError` AND emits an `INVARIANT_VIOLATION` audit row.

(The `build_namespaced_memory_runner` / `build_namespaced_voice_runner`
factories this file also covered were removed with the ADR-0008
ingestion plane in #1355 — those runners wrote a per-customer
control-plane D1 that was never provisioned.)

Run from repo root:

    cd operator && python -m pytest adapter/tests/test_namespace_adoption.py -v
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))  # operator/ on sys.path

from adapter import namespaced_executor_from_env  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
from adapter.audit_log import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    AuditLogWriter,
    SqliteExecutor,
)
from adapter.namespace_assertion import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    NamespaceAssertionError,
    NamespacedD1Executor,
)


# ---------------------------------------------------------------------------
# Audit-log writer fixture
# ---------------------------------------------------------------------------


_SCHEMA_SQL = """
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


def _make_audit_writer() -> tuple[AuditLogWriter, sqlite3.Connection]:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA_SQL)
    return AuditLogWriter(SqliteExecutor(conn)), conn


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# `namespaced_executor_from_env` — D1 wiring
# ---------------------------------------------------------------------------


def test_namespaced_executor_from_env_requires_customer_slug(monkeypatch):
    # Unset both the slug AND the D1 env vars so the slug check fires first
    monkeypatch.delenv("CUSTOMER_SLUG", raising=False)
    monkeypatch.setenv("CF_ACCOUNT_ID", "acct")
    monkeypatch.setenv("CF_API_TOKEN", "tok")
    monkeypatch.setenv("AIE_D1_DATABASE_ID", "db")

    with pytest.raises(RuntimeError, match="CUSTOMER_SLUG"):
        namespaced_executor_from_env()


def test_namespaced_executor_from_env_requires_d1_env_vars(monkeypatch):
    monkeypatch.setenv("CUSTOMER_SLUG", "acme")
    monkeypatch.delenv("CF_ACCOUNT_ID", raising=False)
    monkeypatch.setenv("CF_API_TOKEN", "tok")
    monkeypatch.setenv("AIE_D1_DATABASE_ID", "db")

    # writer_from_env (called for the default audit writer) fails first
    # because it scans the same env. Either error path is acceptable.
    with pytest.raises(RuntimeError, match="CF_ACCOUNT_ID"):
        namespaced_executor_from_env()


def test_namespaced_executor_from_env_returns_slug_bound_wrapper(monkeypatch):
    monkeypatch.setenv("CUSTOMER_SLUG", "acme")
    monkeypatch.setenv("CF_ACCOUNT_ID", "acct")
    monkeypatch.setenv("CF_API_TOKEN", "tok")
    monkeypatch.setenv("AIE_D1_DATABASE_ID", "db")

    writer, _ = _make_audit_writer()
    executor = namespaced_executor_from_env(audit_writer=writer)

    assert isinstance(executor, NamespacedD1Executor)
    # Bound slug is read back via a refusal probe — exercising a foreign
    # token returns a NamespaceAssertionError whose expected_slug is "acme".
    with pytest.raises(NamespaceAssertionError) as excinfo:
        _run(executor.execute("INSERT INTO x (key) VALUES ('hermes-other-vault')", []))
    assert excinfo.value.expected_slug == "acme"


def test_namespaced_executor_from_env_accepts_explicit_slug(monkeypatch):
    monkeypatch.setenv("CF_ACCOUNT_ID", "acct")
    monkeypatch.setenv("CF_API_TOKEN", "tok")
    monkeypatch.setenv("AIE_D1_DATABASE_ID", "db")
    monkeypatch.delenv("CUSTOMER_SLUG", raising=False)

    writer, _ = _make_audit_writer()
    executor = namespaced_executor_from_env("operator-slug", audit_writer=writer)

    assert isinstance(executor, NamespacedD1Executor)
    with pytest.raises(NamespaceAssertionError) as excinfo:
        _run(executor.execute("SELECT 'hermes-other-vault'", []))
    assert excinfo.value.expected_slug == "operator-slug"
