"""Tests for operator/adapter/namespace_assertion.py (issue #861).

Covers the three namespaced wrappers (D1, R2, Vectorize), the refusal
contract (structured exception + INVARIANT_VIOLATION audit row), and
the slug-validation guard at construction.

The headline integration test (`test_cross_customer_attempt_refused_and_audited`)
exercises the full AC for issue #861: a wrapper bound to one customer
slug is asked to touch a foreign customer's namespace; the call must
refuse AND the per-customer audit log must carry the violation row.

Run from repo root:

    cd operator && python -m pytest adapter/tests/test_namespace_assertion.py -v
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))  # operator/ on sys.path

from adapter.audit_log import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    AuditLogWriter,
    AuditWriteError,
    SqliteExecutor,
)
from adapter.namespace_assertion import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    NamespaceAssertionError,
    NamespacedD1Executor,
    NamespacedR2Client,
    NamespacedVectorizeClient,
)


# ---------------------------------------------------------------------------
# Shared fixtures
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
# In-memory fakes for the three underlying clients
# ---------------------------------------------------------------------------


class _RecordingExecutor:
    """Records every (sql, params) it sees so tests can assert pass-through."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list]] = []

    async def execute(self, sql: str, params: list) -> None:
        self.calls.append((sql, list(params)))


class _RecordingR2:
    def __init__(self) -> None:
        self.put_calls: list[tuple[str, bytes, str]] = []
        self.get_calls: list[str] = []
        self.delete_calls: list[str] = []
        self.objects: dict[str, bytes] = {}

    async def put_object(self, key: str, body: bytes, *, content_type: str) -> None:
        self.put_calls.append((key, body, content_type))
        self.objects[key] = body

    async def get_object(self, key: str) -> bytes:
        self.get_calls.append(key)
        return self.objects.get(key, b"")

    async def delete_object(self, key: str) -> None:
        self.delete_calls.append(key)
        self.objects.pop(key, None)


class _RecordingVectorize:
    def __init__(self) -> None:
        self.upserts: list[tuple[str, list[dict]]] = []
        self.queries: list[tuple[str, list[float], int]] = []
        self.deletes: list[tuple[str, list[str]]] = []

    async def upsert_vectors(self, index_name: str, vectors: list[dict]) -> None:
        self.upserts.append((index_name, vectors))

    async def query_vectors(self, index_name: str, vector: list[float], *, top_k: int):
        self.queries.append((index_name, vector, top_k))
        return []

    async def delete_vectors(self, index_name: str, ids: list[str]) -> None:
        self.deletes.append((index_name, ids))


# ---------------------------------------------------------------------------
# Slug validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_slug",
    [
        "",
        "A",
        "ABC",
        "-leading-dash",
        "trailing-dash-",
        "has space",
        "has_underscore",
        "way-too-long-" + ("x" * 50),
        "x",  # one-char too short
    ],
)
def test_slug_validation_rejects_invalid_slugs(bad_slug):
    with pytest.raises(ValueError, match="namespace slug"):
        NamespacedR2Client(expected_slug=bad_slug, inner=_RecordingR2())


@pytest.mark.parametrize(
    "good_slug",
    ["ab", "smd", "acme", "client-1", "client-1-prod", "a0", "0a", "a-b-c"],
)
def test_slug_validation_accepts_valid_slugs(good_slug):
    # No raise
    NamespacedR2Client(expected_slug=good_slug, inner=_RecordingR2())
    NamespacedD1Executor(expected_slug=good_slug, inner=_RecordingExecutor())
    NamespacedVectorizeClient(expected_slug=good_slug, inner=_RecordingVectorize())


# ---------------------------------------------------------------------------
# D1 — passthrough + refusal
# ---------------------------------------------------------------------------


def test_d1_passes_through_queries_with_no_slug_token():
    inner = _RecordingExecutor()
    wrapped = NamespacedD1Executor(expected_slug="acme", inner=inner)
    _run(
        wrapped.execute(
            "SELECT * FROM audit_log WHERE id = ?",
            ["01HZZZ"],
        )
    )
    assert len(inner.calls) == 1
    assert inner.calls[0][0].startswith("SELECT")


def test_d1_passes_through_queries_mentioning_bound_slug():
    inner = _RecordingExecutor()
    wrapped = NamespacedD1Executor(expected_slug="acme", inner=inner)
    _run(
        wrapped.execute(
            "INSERT INTO memory_index (key) VALUES ('hermes-acme-vault')",
            [],
        )
    )
    assert len(inner.calls) == 1


def test_d1_refuses_query_mentioning_foreign_vault_index():
    inner = _RecordingExecutor()
    wrapped = NamespacedD1Executor(expected_slug="acme", inner=inner)
    with pytest.raises(NamespaceAssertionError) as excinfo:
        _run(
            wrapped.execute(
                "INSERT INTO memory_index (key) VALUES ('hermes-other-vault')",
                [],
            )
        )
    assert excinfo.value.violation_kind == "d1_sql"
    assert excinfo.value.expected_slug == "acme"
    assert excinfo.value.attempted_target == "hermes-other-vault"
    assert inner.calls == []


def test_d1_refuses_query_mentioning_foreign_corrections_index():
    inner = _RecordingExecutor()
    wrapped = NamespacedD1Executor(expected_slug="acme", inner=inner)
    with pytest.raises(NamespaceAssertionError) as excinfo:
        _run(
            wrapped.execute(
                "SELECT * FROM corrections_index WHERE name = 'hermes-other-corrections'",
                [],
            )
        )
    assert excinfo.value.attempted_target == "hermes-other-corrections"
    assert inner.calls == []


def test_d1_refuses_query_embedding_foreign_vault_path():
    inner = _RecordingExecutor()
    wrapped = NamespacedD1Executor(expected_slug="acme", inner=inner)
    with pytest.raises(NamespaceAssertionError) as excinfo:
        _run(
            wrapped.execute(
                "UPDATE memory_state SET r2_key = 'vaults/other/foo.json' WHERE id = ?",
                ["k1"],
            )
        )
    assert excinfo.value.violation_kind == "d1_sql"
    assert excinfo.value.attempted_target == "vaults/other/"
    assert inner.calls == []


def test_d1_passes_through_query_embedding_own_vault_path():
    inner = _RecordingExecutor()
    wrapped = NamespacedD1Executor(expected_slug="acme", inner=inner)
    _run(
        wrapped.execute(
            "UPDATE memory_state SET r2_key = 'vaults/acme/foo.json' WHERE id = ?",
            ["k1"],
        )
    )
    assert len(inner.calls) == 1


# ---------------------------------------------------------------------------
# R2 — passthrough + refusal across both prefix conventions
# ---------------------------------------------------------------------------


def test_r2_accepts_vaults_slug_prefix():
    inner = _RecordingR2()
    wrapped = NamespacedR2Client(expected_slug="acme", inner=inner)
    _run(wrapped.put_object("vaults/acme/foo.json", b"x", content_type="application/json"))
    _run(wrapped.put_object("vaults/acme/no_pm/matters/m-1/doc.pdf", b"y", content_type="application/pdf"))
    assert len(inner.put_calls) == 2


def test_r2_accepts_slug_vault_prefix():
    inner = _RecordingR2()
    wrapped = NamespacedR2Client(expected_slug="acme", inner=inner)
    _run(
        wrapped.put_object(
            "acme/vault/narrative/pm-filevine-matter-m-1.json",
            b"x",
            content_type="application/json",
        )
    )
    assert len(inner.put_calls) == 1


def test_r2_refuses_foreign_slug_under_vaults_prefix():
    inner = _RecordingR2()
    wrapped = NamespacedR2Client(expected_slug="acme", inner=inner)
    with pytest.raises(NamespaceAssertionError) as excinfo:
        _run(wrapped.put_object("vaults/other/foo.json", b"x", content_type="application/json"))
    assert excinfo.value.violation_kind == "r2_key"
    assert excinfo.value.attempted_target == "vaults/other/foo.json"
    assert inner.put_calls == []


def test_r2_refuses_foreign_slug_under_slug_vault_prefix():
    inner = _RecordingR2()
    wrapped = NamespacedR2Client(expected_slug="acme", inner=inner)
    with pytest.raises(NamespaceAssertionError) as excinfo:
        _run(
            wrapped.put_object(
                "other/vault/narrative/foo.json",
                b"x",
                content_type="application/json",
            )
        )
    assert excinfo.value.attempted_target == "other/vault/narrative/foo.json"
    assert inner.put_calls == []


def test_r2_refuses_key_that_matches_neither_convention():
    inner = _RecordingR2()
    wrapped = NamespacedR2Client(expected_slug="acme", inner=inner)
    # First segment is neither `vaults` nor a valid slug shape — uppercase
    # is disallowed by the slug regex, so the wrapper falls through to the
    # "neither convention" refusal.
    with pytest.raises(NamespaceAssertionError) as excinfo:
        _run(wrapped.put_object("BAD/foo.json", b"x", content_type="application/json"))
    assert "either prefix convention" in excinfo.value.detail


def test_r2_refuses_misc_first_segment_as_foreign_slug():
    # A first segment that is slug-shaped but not the expected slug is
    # treated as a foreign-customer key — `misc` matches the slug regex,
    # so it gets the foreign-slug refusal path (not the "neither
    # convention" one).
    inner = _RecordingR2()
    wrapped = NamespacedR2Client(expected_slug="acme", inner=inner)
    with pytest.raises(NamespaceAssertionError) as excinfo:
        _run(wrapped.put_object("misc/foo.json", b"x", content_type="application/json"))
    assert excinfo.value.violation_kind == "r2_key"
    assert "foreign customer slug" in excinfo.value.detail


def test_r2_refuses_empty_key():
    wrapped = NamespacedR2Client(expected_slug="acme", inner=_RecordingR2())
    with pytest.raises(NamespaceAssertionError, match="empty key"):
        _run(wrapped.put_object("", b"x", content_type="application/json"))


def test_r2_refuses_path_traversal():
    wrapped = NamespacedR2Client(expected_slug="acme", inner=_RecordingR2())
    with pytest.raises(NamespaceAssertionError, match="traversal"):
        _run(wrapped.put_object("vaults/acme/../other/foo.json", b"x", content_type="application/json"))


def test_r2_refuses_get_and_delete_with_foreign_key():
    inner = _RecordingR2()
    wrapped = NamespacedR2Client(expected_slug="acme", inner=inner)
    with pytest.raises(NamespaceAssertionError):
        _run(wrapped.get_object("vaults/other/foo.json"))
    with pytest.raises(NamespaceAssertionError):
        _run(wrapped.delete_object("vaults/other/foo.json"))
    assert inner.get_calls == []
    assert inner.delete_calls == []


def test_r2_normalizes_single_leading_slash():
    inner = _RecordingR2()
    wrapped = NamespacedR2Client(expected_slug="acme", inner=inner)
    _run(wrapped.put_object("/vaults/acme/foo.json", b"x", content_type="application/json"))
    assert len(inner.put_calls) == 1


def test_r2_refuses_multiple_leading_slashes():
    wrapped = NamespacedR2Client(expected_slug="acme", inner=_RecordingR2())
    with pytest.raises(NamespaceAssertionError, match="multiple leading slashes"):
        _run(wrapped.put_object("//vaults/acme/foo.json", b"x", content_type="application/json"))


# ---------------------------------------------------------------------------
# Vectorize — passthrough + refusal
# ---------------------------------------------------------------------------


def test_vectorize_accepts_vault_index():
    inner = _RecordingVectorize()
    wrapped = NamespacedVectorizeClient(expected_slug="acme", inner=inner)
    _run(wrapped.upsert_vectors("hermes-acme-vault", [{"id": "v-1", "values": [0.1]}]))
    assert len(inner.upserts) == 1


def test_vectorize_accepts_corrections_index():
    inner = _RecordingVectorize()
    wrapped = NamespacedVectorizeClient(expected_slug="acme", inner=inner)
    _run(wrapped.upsert_vectors("hermes-acme-corrections", []))
    assert len(inner.upserts) == 1


def test_vectorize_refuses_foreign_slug_vault_index():
    inner = _RecordingVectorize()
    wrapped = NamespacedVectorizeClient(expected_slug="acme", inner=inner)
    with pytest.raises(NamespaceAssertionError) as excinfo:
        _run(wrapped.upsert_vectors("hermes-other-vault", []))
    assert excinfo.value.violation_kind == "vectorize_index"
    assert excinfo.value.attempted_target == "hermes-other-vault"
    assert inner.upserts == []


def test_vectorize_refuses_unknown_suffix():
    wrapped = NamespacedVectorizeClient(expected_slug="acme", inner=_RecordingVectorize())
    with pytest.raises(NamespaceAssertionError, match="allowed set"):
        _run(wrapped.upsert_vectors("hermes-acme-other", []))


def test_vectorize_refuses_malformed_index_name():
    wrapped = NamespacedVectorizeClient(expected_slug="acme", inner=_RecordingVectorize())
    with pytest.raises(NamespaceAssertionError, match="does not match"):
        _run(wrapped.upsert_vectors("totally-unrelated-name", []))


def test_vectorize_refuses_empty_index_name():
    wrapped = NamespacedVectorizeClient(expected_slug="acme", inner=_RecordingVectorize())
    with pytest.raises(NamespaceAssertionError, match="empty index name"):
        _run(wrapped.upsert_vectors("", []))


def test_vectorize_query_and_delete_also_assert():
    inner = _RecordingVectorize()
    wrapped = NamespacedVectorizeClient(expected_slug="acme", inner=inner)
    with pytest.raises(NamespaceAssertionError):
        _run(wrapped.query_vectors("hermes-other-vault", [0.1], top_k=5))
    with pytest.raises(NamespaceAssertionError):
        _run(wrapped.delete_vectors("hermes-other-vault", ["v-1"]))
    assert inner.queries == []
    assert inner.deletes == []


# ---------------------------------------------------------------------------
# Headline AC: cross-customer attempt is refused AND audited
# ---------------------------------------------------------------------------


def test_cross_customer_attempt_refused_and_audited():
    """Issue #861 AC: integration test attempts cross-customer query;
    verify refusal AND audit log entry.

    Exercises all three wrappers against a writer-shared SqliteExecutor
    so one audit_log table records every refusal.
    """
    writer, audit_conn = _make_audit_writer()

    d1 = NamespacedD1Executor(
        expected_slug="acme",
        inner=_RecordingExecutor(),
        audit_writer=writer,
    )
    r2 = NamespacedR2Client(
        expected_slug="acme",
        inner=_RecordingR2(),
        audit_writer=writer,
    )
    vec = NamespacedVectorizeClient(
        expected_slug="acme",
        inner=_RecordingVectorize(),
        audit_writer=writer,
    )

    with pytest.raises(NamespaceAssertionError):
        _run(
            d1.execute(
                "INSERT INTO memory_index (key) VALUES ('hermes-other-vault')",
                [],
            )
        )
    with pytest.raises(NamespaceAssertionError):
        _run(r2.put_object("vaults/other/leak.json", b"x", content_type="application/json"))
    with pytest.raises(NamespaceAssertionError):
        _run(vec.upsert_vectors("hermes-other-vault", []))

    rows = audit_conn.execute("SELECT action_type, actor, metadata FROM audit_log ORDER BY id").fetchall()
    assert len(rows) == 3
    for action_type, actor, metadata_json in rows:
        assert action_type == "INVARIANT_VIOLATION"
        assert actor == "agent"
        meta = json.loads(metadata_json)
        assert meta["invariant"] == "namespace_isolation"
        assert meta["expected_slug"] == "acme"
        assert meta["source"].endswith("namespace_assertion.py")

    violation_kinds = sorted(json.loads(metadata_json)["violation_kind"] for _, _, metadata_json in rows)
    assert violation_kinds == ["d1_sql", "r2_key", "vectorize_index"]


# ---------------------------------------------------------------------------
# Audit-channel failure does not mask the namespace refusal
# ---------------------------------------------------------------------------


class _AlwaysFailsAuditWriter:
    """Synthetic AuditLogWriter that always raises on write.

    Mirrors the interface (`write(AuditEvent) -> str`) duck-typed enough
    for the wrapper's `audit_writer.write(...)` call to dispatch.
    """

    async def write(self, event):
        raise AuditWriteError("synthetic transport failure")


def test_namespace_refusal_still_raises_when_audit_fails():
    bad_writer = _AlwaysFailsAuditWriter()
    wrapped = NamespacedR2Client(
        expected_slug="acme",
        inner=_RecordingR2(),
        audit_writer=bad_writer,  # type: ignore[arg-type]
    )
    with pytest.raises(NamespaceAssertionError):
        _run(wrapped.put_object("vaults/other/foo.json", b"x", content_type="application/json"))


# ---------------------------------------------------------------------------
# Wrapper works without an audit writer (best-effort logging only)
# ---------------------------------------------------------------------------


def test_wrappers_function_without_audit_writer():
    # No raise on construction
    d1 = NamespacedD1Executor(expected_slug="acme", inner=_RecordingExecutor())
    r2 = NamespacedR2Client(expected_slug="acme", inner=_RecordingR2())
    vec = NamespacedVectorizeClient(expected_slug="acme", inner=_RecordingVectorize())

    # Refusal still raises NamespaceAssertionError; the absence of an
    # audit writer is logged but does not silence the failure.
    with pytest.raises(NamespaceAssertionError):
        _run(d1.execute("SELECT 'hermes-other-vault'", []))
    with pytest.raises(NamespaceAssertionError):
        _run(r2.put_object("vaults/other/foo.json", b"x", content_type="application/json"))
    with pytest.raises(NamespaceAssertionError):
        _run(vec.upsert_vectors("hermes-other-vault", []))
