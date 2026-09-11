"""TOCTOU lockdown — assert the public adapter surface is namespaced-only.

Issue #861's wrapper PR (#1006) shipped the namespace assertion. The
adoption PR (#1012) added the factory helpers. This test file is the
hardening step: it locks the public adapter surface so any future
caller doing `from adapter import *` or `from adapter.audit_log import
*` cannot grab a raw client by accident.

Raw classes remain importable by *explicit* name — that is needed by:

* the writer path itself (`audit_log.writer_from_env` constructs an
  `HttpD1Executor` directly, per the audit-log immutability invariant
  documented in `hermes-smd-overlay/plugins/hermes-smd-audit/immutability.py`),
* (historically) the namespace-bridge adapters — removed with the
  ADR-0008 ingestion plane in #1355,
* and tests that build sqlite-backed harnesses.

The lockdown is enforced by removing the raw names from `__all__` in
each of: `adapter/__init__.py`, `adapter/audit_log.py`,
`adapter/audit_log.py`. This test
guards each removal so a future PR that re-adds a raw name to `__all__`
breaks the build.

Run from repo root:

    cd operator && python -m pytest adapter/tests/test_namespace_surface_lockdown.py -v
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))  # operator/ on sys.path


# ---------------------------------------------------------------------------
# `adapter` top-level public surface
# ---------------------------------------------------------------------------


def test_adapter_public_surface_is_namespaced_only():
    """`from adapter import *` produces exactly the namespaced surface."""
    adapter = importlib.import_module("adapter")
    expected = {
        "NamespaceAssertionError",
        "NamespacedD1Executor",
        "NamespacedR2Client",
        "NamespacedVectorizeClient",
        "namespaced_executor_from_env",
    }
    assert set(adapter.__all__) == expected, (
        f"adapter.__all__ drifted: {set(adapter.__all__) ^ expected}; "
        "TOCTOU hardening requires the public surface to be namespaced-only"
    )


def test_adapter_star_import_does_not_pull_raw_executors():
    """Star-importing `adapter` does not grab `HttpD1Executor`/`SqliteExecutor`.

    Verifies the lockdown at the namespace level — even if a future PR
    accidentally re-exports a raw constructor from the package, this
    test catches it.
    """
    namespace: dict = {}
    exec("from adapter import *", namespace)  # noqa: S102 — the test exercises star-import on purpose, into a throwaway namespace
    forbidden = {
        "HttpD1Executor",
        "SqliteExecutor",
        "StorageClient",
        "R2Client",
        "Executor",  # the bare Protocol from audit_log
    }
    leaked = forbidden & set(namespace)
    assert not leaked, (
        f"`from adapter import *` leaked raw constructors: {leaked}; "
        "TOCTOU hardening: route all raw access through "
        "adapter.d1_env.namespaced_executor_from_env or the factories"
    )


# ---------------------------------------------------------------------------
# `adapter.audit_log` submodule surface
# ---------------------------------------------------------------------------


def test_audit_log_all_excludes_raw_executors():
    audit_log = importlib.import_module("adapter.audit_log")
    raw_names = {"HttpD1Executor", "SqliteExecutor"}
    leaked = raw_names & set(audit_log.__all__)
    assert not leaked, (
        f"adapter.audit_log.__all__ exports raw executors: {leaked}; "
        "TOCTOU hardening removes them from the public surface "
        "(they remain importable by explicit name for the writer path "
        "and tests, per the audit-log immutability design)"
    )


def test_audit_log_raw_executors_still_importable_by_explicit_name():
    """Defense-in-depth: confirm the back-compat path still works.

    The writer path (`writer_from_env`) and 19+ in-tree tests rely on
    importing `HttpD1Executor` / `SqliteExecutor` by name. The lockdown
    removes them from `__all__` but does NOT remove the classes — that
    rename is deferred to a follow-on once consumers migrate.
    """
    from adapter.audit_log import HttpD1Executor, SqliteExecutor  # noqa: F401 - the import IS the assertion: the back-compat names must still resolve

    # If either import raises ImportError, the lockdown went too far.
    # The test passing means back-compat is preserved.


def test_audit_log_star_import_does_not_pull_raw_executors():
    namespace: dict = {}
    exec("from adapter.audit_log import *", namespace)  # noqa: S102 - the test exercises star-import on purpose, into a throwaway namespace
    forbidden = {"HttpD1Executor", "SqliteExecutor"}
    leaked = forbidden & set(namespace)
    assert not leaked, (
        f"`from adapter.audit_log import *` leaked raw executors: {leaked}"
    )
