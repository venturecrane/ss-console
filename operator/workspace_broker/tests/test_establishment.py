"""establish_* verbs: uid-gated, rebuilt-never-forwarded, spool-marshalled
(ADR 0085, ss#2161/#2162).

The mediated path by which an admin-instructed voice/shape submission crosses
the agent -> broker trust boundary. These tests hold the four corrections.py
disciplines at the seam where each could quietly stop being true: one pinned
action_type per writing verb, rows/files rebuilt from a bounded field set,
hashes computed server-side, and refusal (never sanitization) of malformed
fields — plus the spool lifecycle: staging ceilings, TTL sweeps, hash-bound
manifests, atomic run materialization, and one-shot result reads whose
retained audit rows never carry corpus text.
"""

from __future__ import annotations

import json
import hashlib
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import establishment, establishment_store
from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.establishment import (
    ESTABLISHMENT_RESULT_ACTION_TYPE,
    ESTABLISHMENT_SUBMITTED_ACTION_TYPE,
    EstablishmentStore,
    EstablishmentValidationError,
    build_result_row,
    normalize_lf,
    safe_slug,
)
from workspace_broker.server import Broker

AGENT_UID = 1000
GATEWAY_PID = 42

CORPUS_TEXT = "Dear Ms. Chen,\r\nWe write regarding your matter.\nSincerely,\nThe Firm"


def _spool(tmp_path: Path) -> Path:
    root = tmp_path / "establish-spool"
    for child in ("staging", "runs", "results"):
        (root / child).mkdir(parents=True)
    return root


def _broker(tmp_path: Path) -> Broker:
    broker = Broker.__new__(Broker)
    broker.customer_slug = "smd"
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.ledger = LedgerWriter(str(tmp_path / "audit.db"))
    broker.establishment = EstablishmentStore(_spool(tmp_path), broker.ledger)
    return broker


def _stage_request(**overrides) -> dict:
    request = {
        "action": "establish_stage_document",
        "name": "Demand Letter - Chen.docx",
        "text": CORPUS_TEXT,
        "source": {
            "connector": "smokeball",
            "document_id": "doc-guid-1",
            "matter_id": "matter-guid-1",
        },
    }
    request.update(overrides)
    return request


def _stage(broker: Broker, **overrides) -> dict:
    return broker.handle(_stage_request(**overrides), peer_pid=9999, peer_uid=AGENT_UID)


def _install_request(staged: dict, **overrides) -> dict:
    request = {
        "action": "establish_submit",
        "staging_id": staged["staging_id"],
        "phase": "install",
        "output_class": "client_email",
        "property": "voice",
        "spec_body": "Warm but precise.\r\nNever open with an apology.",
        "assertions": {"rules": [{"kind": "forbid", "pattern": "per our conversation"}]},
        "corpus_manifest": [{"doc_id": staged["doc_id"], "sha256": staged["sha256"]}],
        "instructed_by": "admin@example-firm.com",
        "source_ref": "msg-01JXYZ",
    }
    request.update(overrides)
    return request


def _rows(tmp_path: Path) -> list[tuple[str, dict]]:
    conn = sqlite3.connect(str(tmp_path / "audit.db"))
    rows = conn.execute("SELECT action_type, metadata FROM audit_log ORDER BY rowid").fetchall()
    conn.close()
    return [(r[0], json.loads(r[1])) for r in rows]


# ---------------------------------------------------------------------------
# The gate — all three verbs share it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "request_body",
    [
        _stage_request(),
        {"action": "establish_submit", "staging_id": "a" * 16, "phase": "analyze"},
        {"action": "establish_status", "run_id": "a" * 16},
    ],
)
def test_rejected_from_foreign_uid(tmp_path: Path, request_body: dict) -> None:
    broker = _broker(tmp_path)
    with pytest.raises(PermissionError):
        broker.handle(request_body, peer_pid=9999, peer_uid=AGENT_UID + 1)
    assert broker.ledger.count() == 0


def test_rejected_when_agent_uid_unresolved(tmp_path: Path) -> None:
    """agent_uid=None (pre-verb image / __new__ default) is fail-closed."""
    broker = _broker(tmp_path)
    broker.agent_uid = None
    broker.gateway_pid = 999999999  # the /proc stat fallback must also fail
    with pytest.raises(PermissionError):
        broker.handle(_stage_request(), peer_pid=9999, peer_uid=AGENT_UID)


def test_rejected_when_peer_uid_missing(tmp_path: Path) -> None:
    """Two-arg handle() callers (legacy wire) cannot reach these verbs."""
    broker = _broker(tmp_path)
    with pytest.raises(PermissionError):
        broker.handle(_stage_request(), peer_pid=GATEWAY_PID)


def test_rejected_when_spool_unconfigured(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    broker.establishment = None
    with pytest.raises(ValueError):
        broker.handle(_stage_request(), peer_pid=9999, peer_uid=AGENT_UID)


def test_rejected_when_ledger_unconfigured(tmp_path: Path) -> None:
    """An establishment that cannot be audited must not run."""
    broker = _broker(tmp_path)
    broker.ledger = None
    with pytest.raises(ValueError):
        broker.handle(_stage_request(), peer_pid=9999, peer_uid=AGENT_UID)


# ---------------------------------------------------------------------------
# establish_stage_document
# ---------------------------------------------------------------------------


def test_stage_writes_doc_with_server_side_hash(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    resp = _stage(broker)
    assert resp["ok"] is True
    assert resp["doc_count"] == 1
    from hashlib import sha256

    assert resp["sha256"] == sha256(CORPUS_TEXT.encode()).hexdigest()
    store = broker.establishment
    doc_path = store.staging_dir / resp["staging_id"] / "docs" / f"{resp['doc_id']}.json"
    record = json.loads(doc_path.read_text())
    assert record["text"] == CORPUS_TEXT  # corpus stored verbatim, never normalized
    assert record["sha256"] == resp["sha256"]
    assert record["source"] == {
        "connector": "smokeball",
        "document_id": "doc-guid-1",
        "matter_id": "matter-guid-1",
    }


def test_stage_wire_supplied_sha256_is_never_read(tmp_path: Path) -> None:
    """Discipline 3: the hash is a server-side constant."""
    broker = _broker(tmp_path)
    resp = _stage(broker, sha256="deadbeef" * 8)
    assert resp["sha256"] != "deadbeef" * 8


def test_stage_name_is_a_server_side_derivation(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    resp = _stage(broker, name="../..//Étrange  Letter (FINAL).PDF")
    assert resp["name"] == "trange-letter-final-.pdf".strip("-")
    # No path separators, no uppercase, no raw bytes retained anywhere.
    doc_path = broker.establishment.staging_dir / resp["staging_id"] / "docs" / f"{resp['doc_id']}.json"
    assert "FINAL" not in doc_path.read_text()


def test_stage_unsluggable_name_is_refused_not_invented(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    with pytest.raises(EstablishmentValidationError):
        _stage(broker, name="///")


def test_stage_text_required_nonempty_bounded(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    with pytest.raises(EstablishmentValidationError):
        _stage(broker, text="   ")
    with pytest.raises(EstablishmentValidationError):
        _stage(broker, text=7)
    with pytest.raises(EstablishmentValidationError):
        _stage(broker, text="x" * (establishment.MAX_DOC_TEXT_BYTES + 1))


def test_stage_source_fields_are_rebuilt_and_required(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    with pytest.raises(EstablishmentValidationError):
        _stage(broker, source="smokeball")
    with pytest.raises(EstablishmentValidationError):
        _stage(broker, source={"connector": "smokeball"})  # document_id missing
    # An invented source field has nowhere to land.
    resp = _stage(
        broker,
        source={
            "connector": "smokeball",
            "document_id": "d1",
            "spec_path": "/opt/data/specs/x",
        },
    )
    doc_path = broker.establishment.staging_dir / resp["staging_id"] / "docs" / f"{resp['doc_id']}.json"
    assert "spec_path" not in json.loads(doc_path.read_text())["source"]


def test_stage_unknown_staging_id_is_refused(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    with pytest.raises(EstablishmentValidationError):
        _stage(broker, staging_id="nonexistent-set-01")
    with pytest.raises(EstablishmentValidationError):
        _stage(broker, staging_id="../escape")  # traversal-shaped id is a charset refusal


def test_stage_doc_count_ceiling(tmp_path: Path, monkeypatch) -> None:
    # Patch target is establishment_store, not establishment: the constant is
    # consumed there, and `import *` binds values at import time, so patching
    # the re-export surface would not reach the consumer (2026-08-24 split).
    monkeypatch.setattr(establishment_store, "MAX_DOCS_PER_SET", 2)
    broker = _broker(tmp_path)
    first = _stage(broker)
    _stage(broker, staging_id=first["staging_id"])
    with pytest.raises(EstablishmentValidationError):
        _stage(broker, staging_id=first["staging_id"])


def test_stage_total_bytes_ceiling(tmp_path: Path, monkeypatch) -> None:
    # Patch target is establishment_store, not establishment: the constant is
    # consumed there, and `import *` binds values at import time, so patching
    # the re-export surface would not reach the consumer (2026-08-24 split).
    monkeypatch.setattr(establishment_store, "MAX_SET_BYTES", 100)
    broker = _broker(tmp_path)
    first = _stage(broker, text="x" * 80)
    with pytest.raises(EstablishmentValidationError):
        _stage(broker, staging_id=first["staging_id"], text="y" * 30)


def test_stage_ttl_sweep_expires_the_set(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    first = _stage(broker)
    staging_path = broker.establishment.staging_dir / first["staging_id"]
    expired = time.time() - establishment.STAGING_TTL_SECONDS - 60
    (staging_path / "meta.json").write_text(json.dumps({"created_at": expired}))
    with pytest.raises(EstablishmentValidationError):
        _stage(broker, staging_id=first["staging_id"])
    assert not staging_path.exists()


# ---------------------------------------------------------------------------
# establish_submit — shared refusals
# ---------------------------------------------------------------------------


def test_submit_phase_is_closed(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    staged = _stage(broker)
    for bad in ["Install", "apply", "", None]:
        with pytest.raises(EstablishmentValidationError):
            broker.handle(
                {"action": "establish_submit", "staging_id": staged["staging_id"], "phase": bad},
                peer_pid=9999,
                peer_uid=AGENT_UID,
            )


def test_submit_empty_staging_set_is_refused(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    staged = _stage(broker)
    docs_dir = broker.establishment.staging_dir / staged["staging_id"] / "docs"
    for f in docs_dir.glob("*.json"):
        f.unlink()
    with pytest.raises(EstablishmentValidationError):
        broker.handle(
            {"action": "establish_submit", "staging_id": staged["staging_id"], "phase": "analyze"},
            peer_pid=9999,
            peer_uid=AGENT_UID,
        )


def test_submit_staged_doc_failing_rehash_is_refused(tmp_path: Path) -> None:
    """A staged file whose text no longer matches its recorded digest is an
    integrity refusal — the spec must bind to exactly the staged corpus."""
    broker = _broker(tmp_path)
    staged = _stage(broker)
    doc_path = broker.establishment.staging_dir / staged["staging_id"] / "docs" / f"{staged['doc_id']}.json"
    record = json.loads(doc_path.read_text())
    record["text"] = record["text"] + " tampered"
    doc_path.write_text(json.dumps(record))
    with pytest.raises(EstablishmentValidationError):
        broker.handle(_install_request(staged), peer_pid=9999, peer_uid=AGENT_UID)


# ---------------------------------------------------------------------------
# establish_submit — analyze phase
# ---------------------------------------------------------------------------


def test_analyze_copies_corpus_and_keeps_staging(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    staged = _stage(broker)
    resp = broker.handle(
        {"action": "establish_submit", "staging_id": staged["staging_id"], "phase": "analyze"},
        peer_pid=9999,
        peer_uid=AGENT_UID,
    )
    assert resp["ok"] is True and resp["status"] == "queued" and resp["phase"] == "analyze"
    store = broker.establishment
    run_dir = store.runs_dir / resp["run_id"]
    submission = json.loads((run_dir / "submission.json").read_text())
    # The intake's submission contract: run_id/staging_id/phase/created_at.
    assert submission["phase"] == "analyze"
    assert submission["staging_id"] == staged["staging_id"]
    assert "created_at" in submission and "submitted_at" not in submission
    copied = json.loads((run_dir / "docs" / f"{staged['doc_id']}.json").read_text())
    assert copied["sha256"] == staged["sha256"]
    # Copied, not moved: the staging set survives for the later install.
    assert (store.staging_dir / staged["staging_id"] / "docs" / f"{staged['doc_id']}.json").exists()
    # No half-written temp dir left behind.
    assert not list(store.runs_dir.glob(".tmp-*"))


def test_analyze_appends_submitted_row_without_corpus_text(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    staged = _stage(broker)
    broker.handle(
        {"action": "establish_submit", "staging_id": staged["staging_id"], "phase": "analyze"},
        peer_pid=9999,
        peer_uid=AGENT_UID,
    )
    rows = _rows(tmp_path)
    assert [r[0] for r in rows] == [ESTABLISHMENT_SUBMITTED_ACTION_TYPE]
    metadata = rows[0][1]
    assert metadata["phase"] == "analyze"
    assert metadata["docs"] == [{"name": "demand-letter-chen.docx", "sha256": staged["sha256"]}]
    assert "Dear Ms. Chen" not in json.dumps(metadata)


# ---------------------------------------------------------------------------
# establish_submit — install phase
# ---------------------------------------------------------------------------


def test_install_materializes_run_and_moves_docs(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    staged = _stage(broker)
    resp = broker.handle(_install_request(staged), peer_pid=9999, peer_uid=AGENT_UID)
    assert resp["ok"] is True and resp["status"] == "queued" and resp["phase"] == "install"
    store = broker.establishment
    run_dir = store.runs_dir / resp["run_id"]
    submission = json.loads((run_dir / "submission.json").read_text())
    assert submission["output_class"] == "client_email"
    assert submission["property"] == "voice"
    assert submission["instructed_by"] == "admin@example-firm.com"
    assert "created_at" in submission and "submitted_at" not in submission
    # The manifest the intake re-verifies 1:1 against the run's docs.
    assert submission["corpus_manifest"] == [{"doc_id": staged["doc_id"], "sha256": staged["sha256"]}]
    # The corpus MOVED into the run (docs gone from staging), but the staging
    # set itself is retained — the intake reads its analysis/ artifacts during
    # the run and purges the whole set afterwards, as root.
    assert (run_dir / "docs" / f"{staged['doc_id']}.json").exists()
    staging_path = store.staging_dir / staged["staging_id"]
    assert staging_path.is_dir()
    assert not (staging_path / "docs" / f"{staged['doc_id']}.json").exists()


def test_install_body_is_lf_normalized_before_hash_and_ceiling(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    staged = _stage(broker)
    resp = broker.handle(_install_request(staged), peer_pid=9999, peer_uid=AGENT_UID)
    submission = json.loads((broker.establishment.runs_dir / resp["run_id"] / "submission.json").read_text())
    assert "\r" not in submission["spec_body"]
    from hashlib import sha256

    assert submission["spec_sha256"] == sha256(submission["spec_body"].encode()).hexdigest()


def test_install_spec_body_ceiling_counts_normalized_bytes(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    staged = _stage(broker)
    over = "x" * establishment.MAX_SPEC_BODY_BYTES + "y"
    with pytest.raises(EstablishmentValidationError):
        broker.handle(_install_request(staged, spec_body=over), peer_pid=9999, peer_uid=AGENT_UID)
    # CRLF that fits once normalized is accepted: the ceiling reads the stored
    # bytes, not the wire bytes.
    line = "a" * 100 + "\r\n"
    body = line * (establishment.MAX_SPEC_BODY_BYTES // 101)
    assert len(body.encode()) > establishment.MAX_SPEC_BODY_BYTES
    resp = broker.handle(_install_request(staged, spec_body=body), peer_pid=9999, peer_uid=AGENT_UID)
    assert resp["ok"] is True


def test_install_refusals_by_field(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    staged = _stage(broker)
    cases = [
        {"output_class": "Client Email"},
        {"output_class": "../smd"},
        {"property": "tone"},
        {"property": "VOICE"},
        {"spec_body": "   \r\n  "},
        {"spec_body": 7},
        {"instructed_by": None},
        {"instructed_by": ""},
        {"source_ref": None},
        {"corpus_manifest": []},
        {"corpus_manifest": "all"},
        {"corpus_manifest": [{"doc_id": "doc-001"}]},  # sha256 missing
    ]
    for overrides in cases:
        with pytest.raises(EstablishmentValidationError):
            broker.handle(_install_request(staged, **overrides), peer_pid=9999, peer_uid=AGENT_UID)
    # Nothing materialized by any refusal.
    assert not list(broker.establishment.runs_dir.iterdir())


def test_install_manifest_hash_mismatch_is_refused(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    staged = _stage(broker)
    bad = [{"doc_id": staged["doc_id"], "sha256": "0" * 64}]
    with pytest.raises(EstablishmentValidationError):
        broker.handle(_install_request(staged, corpus_manifest=bad), peer_pid=9999, peer_uid=AGENT_UID)


def test_install_manifest_unknown_doc_and_duplicate_are_refused(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    staged = _stage(broker)
    with pytest.raises(EstablishmentValidationError):
        broker.handle(
            _install_request(staged, corpus_manifest=[{"doc_id": "doc-999", "sha256": staged["sha256"]}]),
            peer_pid=9999,
            peer_uid=AGENT_UID,
        )
    duplicate = [
        {"doc_id": staged["doc_id"], "sha256": staged["sha256"]},
        {"doc_id": staged["doc_id"], "sha256": staged["sha256"]},
    ]
    with pytest.raises(EstablishmentValidationError):
        broker.handle(
            _install_request(staged, corpus_manifest=duplicate),
            peer_pid=9999,
            peer_uid=AGENT_UID,
        )


def test_install_assertions_shape_and_ceilings(tmp_path: Path, monkeypatch) -> None:
    """Wire shape is an OBJECT with an optional rules list (the intake reads
    assertions.get('rules')); a bare list, a non-list rules, or a non-object
    rule entry is a refusal."""
    broker = _broker(tmp_path)
    staged = _stage(broker)
    for bad in (
        [{"kind": "forbid"}],  # bare list — the pre-O2 shape
        {"rules": "forbid x"},
        {"rules": ["forbid x"]},
        "forbid x",
    ):
        with pytest.raises(EstablishmentValidationError):
            broker.handle(
                _install_request(staged, assertions=bad),
                peer_pid=9999,
                peer_uid=AGENT_UID,
            )
    # Patch target is establishment_store, not establishment: the constant is
    # consumed there, and `import *` binds values at import time, so patching
    # the re-export surface would not reach the consumer (2026-08-24 split).
    monkeypatch.setattr(establishment_store, "_MAX_ASSERTIONS", 1)
    with pytest.raises(EstablishmentValidationError):
        broker.handle(
            _install_request(staged, assertions={"rules": [{"a": 1}, {"b": 2}]}),
            peer_pid=9999,
            peer_uid=AGENT_UID,
        )


def test_install_assertions_are_optional(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    staged = _stage(broker)
    resp = broker.handle(_install_request(staged, assertions=None), peer_pid=9999, peer_uid=AGENT_UID)
    submission = json.loads((broker.establishment.runs_dir / resp["run_id"] / "submission.json").read_text())
    assert submission["assertions"] is None


def test_install_audit_row_carries_no_corpus_or_spec_text(tmp_path: Path) -> None:
    """The retained record is names + hashes + counts — never prose."""
    broker = _broker(tmp_path)
    staged = _stage(broker)
    broker.handle(_install_request(staged), peer_pid=9999, peer_uid=AGENT_UID)
    rows = _rows(tmp_path)
    assert [r[0] for r in rows] == [ESTABLISHMENT_SUBMITTED_ACTION_TYPE]
    metadata = rows[0][1]
    serialized = json.dumps(metadata)
    assert "Dear Ms. Chen" not in serialized
    assert "Warm but precise" not in serialized
    assert "spec_body" not in metadata
    assert set(metadata) == {
        "phase",
        "run_id",
        "staging_id",
        "output_class",
        "property",
        "spec_sha256",
        "docs",
        "doc_count",
        "assertion_count",
        "instructed_by",
        "source_ref",
    }


def test_submit_action_type_cannot_be_forged(tmp_path: Path) -> None:
    """Discipline 1: a caller-supplied action_type is simply never read."""
    broker = _broker(tmp_path)
    staged = _stage(broker)
    broker.handle(_install_request(staged, action_type="REPLY_SENT"), peer_pid=9999, peer_uid=AGENT_UID)
    assert [r[0] for r in _rows(tmp_path)] == [ESTABLISHMENT_SUBMITTED_ACTION_TYPE]


def test_submitted_row_joins_the_hash_chain(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    broker.handle(
        {
            "action": "audit_append",
            "row": {"action_type": "TOOL_CALL_COMPLETED", "actor": "agent", "actor_role": "agent"},
        },
        peer_pid=GATEWAY_PID,
        peer_uid=AGENT_UID,
    )
    staged = _stage(broker)
    broker.handle(_install_request(staged), peer_pid=9999, peer_uid=AGENT_UID)
    conn = sqlite3.connect(str(tmp_path / "audit.db"))
    rows = conn.execute("SELECT action_type, prev_hash, row_hash FROM audit_log ORDER BY rowid").fetchall()
    conn.close()
    assert [r[0] for r in rows] == ["TOOL_CALL_COMPLETED", ESTABLISHMENT_SUBMITTED_ACTION_TYPE]
    assert rows[1][1] == rows[0][2]


# ---------------------------------------------------------------------------
# establish_status
# ---------------------------------------------------------------------------


def _complete_run(broker: Broker, staged: dict) -> str:
    """Submit an install run, then act as the root intake: purge the run dir
    and write the result file (design §3 step 8)."""
    resp = broker.handle(_install_request(staged), peer_pid=9999, peer_uid=AGENT_UID)
    run_id = resp["run_id"]
    store = broker.establishment
    import shutil

    shutil.rmtree(store.runs_dir / run_id)
    result = {
        "schema_version": 1,
        "status": "installed",
        "phase": "install",
        "output_class": "client_email",
        "property": "voice",
        "demotions": [
            {
                "rule_id": "no-exclamation-points",
                "documents": ["demand-letter-chen.docx"],
                "detail": "3 of 1 exemplary docs violate this rule",
            }
        ],
        "previous_key": "vaults/smd/output-classes.previous.json",
        "warnings": ["output class client_email is not declared yet (spec-before-declare)"],
    }
    (store.results_dir / f"{run_id}.json").write_text(json.dumps(result))
    return run_id


def test_status_pending_while_run_unprocessed(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    staged = _stage(broker)
    resp = broker.handle(_install_request(staged), peer_pid=9999, peer_uid=AGENT_UID)
    status = broker.handle(
        {"action": "establish_status", "run_id": resp["run_id"]},
        peer_pid=9999,
        peer_uid=AGENT_UID,
    )
    assert status == {"ok": True, "run_id": resp["run_id"], "status": "pending"}


def test_status_unknown_run_is_refused(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    with pytest.raises(EstablishmentValidationError):
        broker.handle(
            {"action": "establish_status", "run_id": "never-existed-run"},
            peer_pid=9999,
            peer_uid=AGENT_UID,
        )
    with pytest.raises(EstablishmentValidationError):
        broker.handle(
            {"action": "establish_status", "run_id": "../../etc/passwd"},
            peer_pid=9999,
            peer_uid=AGENT_UID,
        )


def test_status_returns_result_verbatim_and_deletes_it(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    staged = _stage(broker)
    run_id = _complete_run(broker, staged)
    resp = broker.handle({"action": "establish_status", "run_id": run_id}, peer_pid=9999, peer_uid=AGENT_UID)
    assert resp["status"] == "complete"
    assert resp["result"]["status"] == "installed"
    assert resp["result"]["demotions"][0]["rule_id"] == "no-exclamation-points"
    assert resp["result"]["warnings"]  # the one-shot payload IS verbatim
    # One-shot: the file is gone and a second read is an unknown-run refusal.
    assert not (broker.establishment.results_dir / f"{run_id}.json").exists()
    with pytest.raises(EstablishmentValidationError):
        broker.handle({"action": "establish_status", "run_id": run_id}, peer_pid=9999, peer_uid=AGENT_UID)


def test_status_appends_bounded_result_row(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    staged = _stage(broker)
    run_id = _complete_run(broker, staged)
    broker.handle({"action": "establish_status", "run_id": run_id}, peer_pid=9999, peer_uid=AGENT_UID)
    rows = _rows(tmp_path)
    assert [r[0] for r in rows] == [
        ESTABLISHMENT_SUBMITTED_ACTION_TYPE,
        ESTABLISHMENT_RESULT_ACTION_TYPE,
    ]
    metadata = rows[1][1]
    assert metadata["verdict"] == "installed"
    # detail (compiler prose that may quote) is NOT retained — rule ids + names.
    assert metadata["demotions"] == [{"rule_id": "no-exclamation-points", "documents": ["demand-letter-chen.docx"]}]
    assert metadata["previous_key"] == "vaults/smd/output-classes.previous.json"
    assert "Dear Ms. Chen" not in json.dumps(metadata)
    assert "detail" not in json.dumps(metadata)
    assert "warnings" not in metadata  # bounded field set, not a forwarded result


def test_result_row_builder_bounds_hostile_shapes() -> None:
    """A malformed result (intake bug) is bounded, never propagated."""
    row = build_result_row(
        "run-abc123456789",
        {
            "status": "x" * 5000,
            "demotions": [
                "not-a-dict",
                {"rule_id": 7, "documents": "all"},
                {"rule_id": "ok", "documents": ["d1", 2, "d3"], "detail": "quoted prose"},
            ],
            "previous_key": 42,
        },
    )
    metadata = json.loads(row["metadata"])
    assert len(metadata["verdict"]) == 200
    assert metadata["previous_key"] is None
    assert metadata["demotions"] == [
        {"rule_id": None, "documents": []},
        {"rule_id": "ok", "documents": ["d1", "d3"]},
    ]
    assert "quoted prose" not in row["metadata"]


def test_result_ttl_sweep_clears_unread_results(tmp_path: Path) -> None:
    import os

    broker = _broker(tmp_path)
    staged = _stage(broker)
    run_id = _complete_run(broker, staged)
    result_path = broker.establishment.results_dir / f"{run_id}.json"
    old = time.time() - establishment.RESULT_TTL_SECONDS - 60
    os.utime(result_path, (old, old))
    broker.establishment.sweep()
    assert not result_path.exists()


def test_unparseable_result_is_an_error_not_a_delete(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    staged = _stage(broker)
    run_id = _complete_run(broker, staged)
    result_path = broker.establishment.results_dir / f"{run_id}.json"
    result_path.write_text("{not json")
    with pytest.raises(ValueError):
        broker.handle({"action": "establish_status", "run_id": run_id}, peer_pid=9999, peer_uid=AGENT_UID)
    assert result_path.exists()  # left for the TTL sweep, not silently eaten


# ---------------------------------------------------------------------------
# Contract with the root intake (overlay establish_intake)
# ---------------------------------------------------------------------------


def test_minted_ids_match_the_intake_safe_segment(tmp_path: Path) -> None:
    """The intake refuses any id outside \\A[a-z0-9][a-z0-9_-]{0,63}\\Z — a
    broker-minted id that the intake would reject is a run that can never
    complete, so the charsets must agree at the mint."""
    import re

    safe = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,63}\Z")
    broker = _broker(tmp_path)
    staged = _stage(broker)
    resp = broker.handle(_install_request(staged), peer_pid=9999, peer_uid=AGENT_UID)
    assert safe.match(staged["staging_id"])
    assert safe.match(staged["doc_id"])
    assert safe.match(resp["run_id"])


def test_sweep_leaves_analysis_bearing_sets_to_the_root_backstop(tmp_path: Path) -> None:
    """The broker cannot remove the root-owned analysis/ subdir; a partial
    rmtree would leave a zombie set, so it must not try — but expiry is still
    enforced by refusal."""
    broker = _broker(tmp_path)
    staged = _stage(broker)
    staging_path = broker.establishment.staging_dir / staged["staging_id"]
    (staging_path / "analysis").mkdir()
    expired = time.time() - establishment.STAGING_TTL_SECONDS - 60
    (staging_path / "meta.json").write_text(json.dumps({"created_at": expired}))
    broker.establishment.sweep()
    assert staging_path.is_dir()  # not touched — root's job
    assert (staging_path / "docs" / f"{staged['doc_id']}.json").exists()
    # ...but the lingering dir grants nothing: every verb refuses it.
    with pytest.raises(EstablishmentValidationError):
        _stage(broker, staging_id=staged["staging_id"])
    with pytest.raises(EstablishmentValidationError):
        broker.handle(_install_request(staged), peer_pid=9999, peer_uid=AGENT_UID)


def test_status_survives_an_undeletable_result(tmp_path: Path) -> None:
    """Resilience only: in production the one-shot delete WORKS (results/ is
    0770 root:workspace-broker on both halves — entrypoint here, the intake's
    overlay#221 hardening there; test_status_returns_result_verbatim_and_deletes_it
    asserts the deletion). Against a mis-hardened dir, the read must still
    succeed (the agent is owed its result) and the intake's TTL sweep becomes
    the remover."""
    broker = _broker(tmp_path)
    staged = _stage(broker)
    run_id = _complete_run(broker, staged)
    results_dir = broker.establishment.results_dir
    results_dir.chmod(0o500)
    try:
        resp = broker.handle({"action": "establish_status", "run_id": run_id}, peer_pid=9999, peer_uid=AGENT_UID)
    finally:
        results_dir.chmod(0o700)
    assert resp["status"] == "complete"
    assert (results_dir / f"{run_id}.json").exists()  # delete failed, tolerated


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def test_normalize_lf() -> None:
    assert normalize_lf("a\r\nb\rc\nd") == "a\nb\nc\nd"


def test_safe_slug_examples() -> None:
    assert safe_slug("Demand Letter - Chen.docx") == "demand-letter-chen.docx"
    assert safe_slug("A" * 300 if False else "settlement_v2.PDF") == "settlement_v2.pdf"
    with pytest.raises(EstablishmentValidationError):
        safe_slug("   ")
    with pytest.raises(EstablishmentValidationError):
        safe_slug(7)
    with pytest.raises(EstablishmentValidationError):
        safe_slug("x" * 300)


# ---------------------------------------------------------------------------
# Person scope (ADR 0085 §6, ss#2067) — docs-less, speaker-only submissions
# ---------------------------------------------------------------------------


def _person_request(**overrides) -> dict:
    request = {
        "action": "establish_submit",
        "scope": "person",
        "staging_id": None,
        "person": "Sarah@Firm.COM",
        "spec_body": "Digest bullets, never prose.\r\nMax three lines.",
        "assertions": None,
        "instructed_by": "sarah@firm.com",
        "source_ref": "msg-02PERSON",
    }
    request.update(overrides)
    return request


def _person_submit(broker, **overrides) -> dict:
    return broker.handle(_person_request(**overrides), peer_pid=9999, peer_uid=AGENT_UID)


def test_person_submit_happy_path_materializes_a_docsless_run(tmp_path):
    broker = _broker(tmp_path)
    result = _person_submit(broker)
    assert result["ok"] is True and result["status"] == "queued"
    run_dir = tmp_path / "establish-spool" / "runs" / result["run_id"]
    sub = json.loads((run_dir / "submission.json").read_text())
    assert sub["scope"] == "person"
    assert sub["phase"] == "install"
    assert sub["person"] == "sarah@firm.com"  # normalized, never literal
    assert "\r" not in sub["spec_body"]  # LF-normalized
    assert sub["spec_sha256"] == hashlib.sha256(sub["spec_body"].encode()).hexdigest()
    assert not list((run_dir / "docs").glob("*")) or not (run_dir / "docs").exists()


def test_person_submit_refuses_a_bad_scope(tmp_path):
    broker = _broker(tmp_path)
    with pytest.raises(EstablishmentValidationError, match="scope"):
        _person_submit(broker, scope="team")


@pytest.mark.parametrize(
    "field,value",
    [
        ("staging_id", "some-set"),
        ("corpus_manifest", [{"doc_id": "x", "sha256": "y"}]),
        ("output_class", "client_email"),
        ("property", "voice"),
    ],
)
def test_person_submit_refuses_non_null_firm_fields(tmp_path, field, value):
    broker = _broker(tmp_path)
    with pytest.raises(EstablishmentValidationError, match=field):
        _person_submit(broker, **{field: value})


@pytest.mark.parametrize("person", [None, "", "not-an-address", "two@ats@x.com", "sarah@nodot", "@firm.com"])
def test_person_submit_refuses_a_malformed_person(tmp_path, person):
    broker = _broker(tmp_path)
    with pytest.raises(EstablishmentValidationError, match="person"):
        _person_submit(broker, person=person)


def test_person_submit_refuses_an_empty_body_and_missing_provenance(tmp_path):
    broker = _broker(tmp_path)
    with pytest.raises(EstablishmentValidationError, match="spec_body"):
        _person_submit(broker, spec_body="  \n ")
    with pytest.raises(EstablishmentValidationError, match="instructed_by"):
        _person_submit(broker, instructed_by=None)
    with pytest.raises(EstablishmentValidationError, match="source_ref"):
        _person_submit(broker, source_ref=None)


def test_person_submit_audit_row_is_bounded_and_body_free(tmp_path):
    broker = _broker(tmp_path)
    _person_submit(broker)
    rows = _rows(tmp_path)
    submitted = [m for (t, m) in rows if t == "ESTABLISHMENT_SUBMITTED"]
    assert len(submitted) == 1
    meta = submitted[0]
    assert meta["scope"] == "person" and meta["person"] == "sarah@firm.com"
    assert "spec_body" not in meta and "Digest bullets" not in json.dumps(meta)


def test_firm_submissions_now_stamp_their_scope(tmp_path):
    broker = _broker(tmp_path)
    staged = _stage(broker)
    result = broker.handle(_install_request(staged), peer_pid=9999, peer_uid=AGENT_UID)
    run_dir = tmp_path / "establish-spool" / "runs" / result["run_id"]
    sub = json.loads((run_dir / "submission.json").read_text())
    assert sub["scope"] == "firm"
