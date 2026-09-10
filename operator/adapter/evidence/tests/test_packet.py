"""End-to-end tests for adapter.evidence.packet.

Coverage:

* customer.yaml redaction strips token_ref, oauth_scopes, recipient
  emails, and leaves everything else intact;
* secret-pattern validator aborts the build when a recognized API key
  shape survives redaction;
* role gate rejects actors outside {captain, compliance};
* end-to-end build emits a tar.gz with every expected file, a
  manifest.json whose file_hashes match the on-disk bytes, and a
  COMPLIANCE_PACKET_EXPORTED audit row;
* missing customer.yaml raises EvidencePacketError (no fabrication);
* empty audit table produces a packet whose summary reports 0 events
  (truthful zero, not a placeholder);
* deterministic mtimes: re-running with the same inputs produces a
  bit-identical tar.gz;
* audit coverage: a matter-scoped export that matches zero rows while
  unattributed rows exist in the period refuses to build, and every
  packet states its coverage boundary on its face (issue #2122).

The tests use the same SqliteExecutor + AuditLogWriter pattern as
adapter/tests/test_audit_log.py and bin/tests/test_decommission.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import sys
import tarfile
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[3]))

from adapter.audit_log import AuditLogWriter, SqliteExecutor  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
from adapter.evidence.packet import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    EvidencePacketBuilder,
    EvidencePacketError,
    PacketActor,
    PacketRequest,
    SqliteReadExecutor,
    redact_customer_yaml,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_FULL_SCHEMA = """
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
CREATE TABLE invariant_boot_checks (
  id             TEXT PRIMARY KEY,
  ts             TEXT NOT NULL,
  invariant_num  INTEGER NOT NULL,
  passed         INTEGER NOT NULL,
  failure_detail TEXT
);
CREATE TABLE memory_rules (
  id            TEXT PRIMARY KEY,
  rule_type     TEXT NOT NULL,
  category      TEXT,
  content       TEXT NOT NULL,
  source        TEXT NOT NULL,
  source_ref    TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  deleted_at    TEXT,
  version       INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE person_mappings (
  id              TEXT PRIMARY KEY,
  canonical_name  TEXT NOT NULL,
  role            TEXT NOT NULL,
  email_addresses TEXT,
  external_ids    TEXT,
  firm_internal   INTEGER NOT NULL DEFAULT 1,
  notes           TEXT,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  deleted_at      TEXT
);
CREATE TABLE skill_state (
  skill_name           TEXT PRIMARY KEY,
  trust_ceiling        TEXT NOT NULL,
  content_hash         TEXT NOT NULL,
  activated_at         TEXT NOT NULL,
  last_run_at          TEXT,
  run_count            INTEGER NOT NULL DEFAULT 0,
  operator_may_approve INTEGER NOT NULL DEFAULT 0,
  config               TEXT
);
CREATE TABLE voice_samples (
  id                 TEXT PRIMARY KEY,
  uploaded_at        TEXT NOT NULL,
  uploaded_by        TEXT NOT NULL,
  source             TEXT NOT NULL,
  recipient_cohort_id TEXT,
  r2_key             TEXT NOT NULL,
  sanitized          INTEGER NOT NULL DEFAULT 0,
  active             INTEGER NOT NULL DEFAULT 1,
  used_in_blind_test INTEGER NOT NULL DEFAULT 0,
  notes              TEXT
);
CREATE TABLE recipient_cohorts (
  id               TEXT PRIMARY KEY,
  name             TEXT NOT NULL UNIQUE,
  description      TEXT,
  tone_descriptors TEXT,
  match_rules      TEXT NOT NULL,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);
"""


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _make_db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "data.sqlite"))
    conn.executescript(_FULL_SCHEMA)
    return conn


def _seed_audit_row(conn: sqlite3.Connection, **fields):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO audit_log (id, ts, action_type, actor, actor_role, "
        "skill_name, matter_ref, input_digest, output_digest, diff_digest, "
        "trust_ceiling, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            fields["id"],
            fields["ts"],
            fields["action_type"],
            fields.get("actor", "agent"),
            fields.get("actor_role"),
            fields.get("skill_name"),
            fields.get("matter_ref"),
            fields.get("input_digest"),
            fields.get("output_digest"),
            fields.get("diff_digest"),
            fields.get("trust_ceiling"),
            fields.get("metadata"),
        ],
    )
    conn.commit()


def _write_customer_yaml(tmp_path: Path, body: dict) -> Path:
    path = tmp_path / "customer.yaml"
    path.write_text(json.dumps(body, indent=2), encoding="utf-8")
    return path


def _build_pair(tmp_path: Path):
    """Construct (builder, audit_conn) with a shared sqlite as both
    read backend and audit writer destination."""
    conn = _make_db(tmp_path)
    reader = SqliteReadExecutor(conn)
    audit = AuditLogWriter(SqliteExecutor(conn))
    builder = EvidencePacketBuilder(
        reader=reader, audit_writer=audit, yaml_loader=json.loads
    )
    return builder, conn


def _build_pair_without_audit_table(tmp_path: Path):
    """Builder whose READ source has no audit_log table.

    The CLI's --read-db and --audit-db are separate paths, so this is a
    real production shape: the export snapshot can be missing the table
    the chain-of-custody row still writes to.
    """
    read_conn = sqlite3.connect(str(tmp_path / "read.sqlite"))
    read_conn.executescript(
        "CREATE TABLE invariant_boot_checks ("
        "  id TEXT PRIMARY KEY, ts TEXT NOT NULL, invariant_num INTEGER NOT NULL,"
        "  passed INTEGER NOT NULL, failure_detail TEXT);"
    )
    audit_conn = sqlite3.connect(str(tmp_path / "audit.sqlite"))
    audit_conn.executescript(_FULL_SCHEMA)
    builder = EvidencePacketBuilder(
        reader=SqliteReadExecutor(read_conn),
        audit_writer=AuditLogWriter(SqliteExecutor(audit_conn)),
        yaml_loader=json.loads,
    )
    return builder, read_conn, audit_conn


def _member_bytes(archive: Path, name: str) -> bytes:
    with tarfile.open(archive, "r:gz") as tar:
        member = tar.extractfile(name)
        assert member is not None, f"{name} missing from packet"
        return member.read()


def _manifest_of(archive: Path) -> dict:
    return json.loads(_member_bytes(archive, "manifest.json"))


# ---------------------------------------------------------------------------
# Redaction unit tests
# ---------------------------------------------------------------------------


def test_redact_customer_yaml_replaces_token_ref():
    src = {
        "connectors": {
            "Gmail": {"token_ref": "vault://secret/abc", "enabled": True},
        }
    }
    out = redact_customer_yaml(src)
    assert out["connectors"]["Gmail"]["token_ref"] == "<redacted>"
    assert out["connectors"]["Gmail"]["enabled"] is True


def test_redact_customer_yaml_collapses_oauth_scopes_to_count():
    src = {"connectors": {"Gmail": {"oauth_scopes": ["a", "b", "c"]}}}
    out = redact_customer_yaml(src)
    assert out["connectors"]["Gmail"]["oauth_scopes"] == "<3 scopes redacted>"


def test_redact_customer_yaml_anonymizes_recipient_emails():
    src = {
        "escalation": {
            "failure_recipients": ["alice@firm.com", "bob@firm.com"],
            "red_flag_recipients": ["paralegal@firm.com"],
        }
    }
    out = redact_customer_yaml(src)
    assert out["escalation"]["failure_recipients"] == [
        "<redacted>@firm.com",
        "<redacted>@firm.com",
    ]
    assert out["escalation"]["red_flag_recipients"] == ["<redacted>@firm.com"]


def test_redact_customer_yaml_preserves_unrelated_fields():
    src = {
        "customer_name": "Acme",
        "vertical": "law-firm",
        "personas": [{"slug": "marcus", "name": "Marcus"}],
    }
    out = redact_customer_yaml(src)
    assert out == src
    # mutation-free
    assert src["personas"][0]["slug"] == "marcus"


# ---------------------------------------------------------------------------
# End-to-end builds
# ---------------------------------------------------------------------------


def _request(tmp_path: Path, customer_yaml: Path, **over) -> PacketRequest:
    base = dict(
        customer_slug="acme",
        matter="all",
        period_start="2026-04-01T00:00:00Z",
        period_end="2026-05-31T23:59:59Z",
        output_path=tmp_path / "out" / "evidence.tar.gz",
        customer_yaml_path=customer_yaml,
        actor="captain@example.com",
        actor_role=PacketActor.CAPTAIN,
    )
    base.update(over)
    return PacketRequest(**base)


def test_build_emits_targz_with_every_expected_file(tmp_path, monkeypatch):
    # The signing key is read from ambient os.environ (signing.py); a developer
    # shell with the real key staged would sign the packet and add manifest.sig,
    # turning the expected-file list red for a reason unrelated to the code
    # (code review 2026-09-10, Testing 4). The signed path has its own test.
    monkeypatch.delenv("EVIDENCE_PACKET_SIGNING_KEY_B64", raising=False)
    builder, conn = _build_pair(tmp_path)
    _seed_audit_row(
        conn,
        id="01HZZ0000000000000000000A1",
        ts="2026-04-15T10:00:00.000Z",
        action_type="DRAFT_CREATED",
        actor="agent",
        skill_name="inbox-triage",
        matter_ref="m-1",
    )
    _seed_audit_row(
        conn,
        id="01HZZ0000000000000000000A2",
        ts="2026-04-15T10:01:00.000Z",
        action_type="DRAFT_APPROVED",
        actor="paralegal@firm.com",
        skill_name="inbox-triage",
        matter_ref="m-1",
    )
    customer_yaml = _write_customer_yaml(
        tmp_path,
        {
            "customer_name": "Acme Co.",
            "connectors": {"Gmail": {"token_ref": "vault://xyz"}},
        },
    )

    result = _run(builder.build(_request(tmp_path, customer_yaml)))

    assert result.output_path.exists()
    with tarfile.open(result.output_path, "r:gz") as tar:
        names = sorted(tar.getnames())
    expected = [
        "00-README.md",
        "01-summary.pdf",
        "03-audit-log.csv",
        "05-customer-yaml.redacted.yml",
        "06-memory-snapshot.json",
        "07-skill-catalog.json",
        "09-boot-checks.csv",
        "manifest.json",
    ]
    assert names == expected
    assert result.file_count == len(expected)
    assert result.bytes_written > 0


def test_build_manifest_file_hashes_match_archive_contents(tmp_path):
    builder, _ = _build_pair(tmp_path)
    customer_yaml = _write_customer_yaml(tmp_path, {"customer_name": "Acme"})

    result = _run(builder.build(_request(tmp_path, customer_yaml)))

    with tarfile.open(result.output_path, "r:gz") as tar:
        manifest_member = tar.extractfile("manifest.json")
        assert manifest_member is not None
        manifest = json.load(manifest_member)
        for name, expected_hash in manifest["file_hashes"].items():
            member = tar.extractfile(name)
            assert member is not None
            blob = member.read()
            assert hashlib.sha256(blob).hexdigest() == expected_hash


def test_build_emits_compliance_packet_audit_row(tmp_path):
    builder, conn = _build_pair(tmp_path)
    customer_yaml = _write_customer_yaml(tmp_path, {"customer_name": "Acme"})

    result = _run(builder.build(_request(tmp_path, customer_yaml)))

    # The shared SqliteReadExecutor sets row_factory on the connection,
    # so reuse the dict shape here rather than fighting the cursor.
    cur = conn.cursor()
    cur.execute(
        "SELECT action_type, actor, actor_role, skill_name, metadata "
        "FROM audit_log WHERE action_type = 'COMPLIANCE_PACKET_EXPORTED'"
    )
    rows = cur.fetchall()
    assert len(rows) == 1
    row = rows[0]
    # Row is dict (set by SqliteReadExecutor) or tuple (default); handle both.
    if isinstance(row, dict):
        action_type = row["action_type"]
        actor = row["actor"]
        actor_role = row["actor_role"]
        skill_name = row["skill_name"]
        metadata_text = row["metadata"]
    else:
        action_type, actor, actor_role, skill_name, metadata_text = row
    assert action_type == "COMPLIANCE_PACKET_EXPORTED"
    assert actor == "captain@example.com"
    assert actor_role == "captain"
    # No skill originated this row, and the row must not name one: the export
    # is a Captain-run CLI, and a seat skill provably cannot write this row
    # (broker audit_append is gateway-PID-gated). ss-console #2122.
    assert skill_name is None
    meta = json.loads(metadata_text)
    assert meta["producer"] == "operator/bin/generate-evidence-packet.sh"
    assert meta["customer_slug"] == "acme"
    assert meta["matter"] == "all"
    assert meta["manifest_sha256"] == result.manifest_sha256


def test_build_rejects_non_captain_non_compliance_actor(tmp_path):
    builder, _ = _build_pair(tmp_path)
    customer_yaml = _write_customer_yaml(tmp_path, {"customer_name": "Acme"})
    with pytest.raises(EvidencePacketError):
        _run(
            builder.build(
                _request(
                    tmp_path,
                    customer_yaml,
                    actor_role="not-a-role",  # type: ignore[arg-type]
                )
            )
        )


def test_build_rejects_missing_customer_yaml(tmp_path):
    builder, _ = _build_pair(tmp_path)
    nonexistent = tmp_path / "nope.yaml"
    with pytest.raises(EvidencePacketError):
        _run(builder.build(_request(tmp_path, nonexistent)))


def test_build_rejects_period_end_before_start(tmp_path):
    builder, _ = _build_pair(tmp_path)
    customer_yaml = _write_customer_yaml(tmp_path, {"customer_name": "Acme"})
    with pytest.raises(EvidencePacketError):
        _run(
            builder.build(
                _request(
                    tmp_path,
                    customer_yaml,
                    period_start="2026-05-01T00:00:00Z",
                    period_end="2026-04-01T00:00:00Z",
                )
            )
        )


def test_build_aborts_on_secret_leak_after_redaction(tmp_path):
    builder, _ = _build_pair(tmp_path)
    # Embed a leaked secret in a field the redactor does NOT cover:
    # this proves the post-redaction validator catches misses.
    customer_yaml = _write_customer_yaml(
        tmp_path,
        {
            "customer_name": "Acme",
            "notes": "Slack webhook leaked: xoxb-1234567890-abcdefghij",
        },
    )
    with pytest.raises(EvidencePacketError) as exc:
        _run(builder.build(_request(tmp_path, customer_yaml)))
    assert "secret-shaped" in str(exc.value)


def test_build_with_empty_audit_table_reports_truthful_zeros(tmp_path):
    builder, _ = _build_pair(tmp_path)
    customer_yaml = _write_customer_yaml(tmp_path, {"customer_name": "Acme"})

    result = _run(builder.build(_request(tmp_path, customer_yaml)))

    assert result.counts["audit_events"] == 0
    assert result.counts["drafts_created"] == 0
    with tarfile.open(result.output_path, "r:gz") as tar:
        pdf_member = tar.extractfile("01-summary.pdf")
        assert pdf_member is not None
        pdf_bytes = pdf_member.read()
    assert b"Audit events recorded: 0" in pdf_bytes


def test_build_filters_audit_rows_by_period_and_matter(tmp_path):
    builder, conn = _build_pair(tmp_path)
    # Row inside the period + matter
    _seed_audit_row(
        conn,
        id="01HZZ0000000000000000000B1",
        ts="2026-04-15T10:00:00.000Z",
        action_type="DRAFT_CREATED",
        matter_ref="m-1",
    )
    # Row inside the period but different matter
    _seed_audit_row(
        conn,
        id="01HZZ0000000000000000000B2",
        ts="2026-04-15T10:00:00.000Z",
        action_type="DRAFT_CREATED",
        matter_ref="m-2",
    )
    # Row outside the period
    _seed_audit_row(
        conn,
        id="01HZZ0000000000000000000B3",
        ts="2026-01-01T00:00:00.000Z",
        action_type="DRAFT_CREATED",
        matter_ref="m-1",
    )
    customer_yaml = _write_customer_yaml(tmp_path, {"customer_name": "Acme"})

    result = _run(
        builder.build(
            _request(
                tmp_path,
                customer_yaml,
                matter="m-1",
                period_start="2026-04-01T00:00:00Z",
                period_end="2026-04-30T23:59:59Z",
            )
        )
    )
    # Only the m-1 row inside the period counts; the COMPLIANCE_PACKET_EXPORTED
    # row is for matter=m-1 so it does NOT appear in this row's count (the
    # builder reads BEFORE writing the chain-of-custody row).
    assert result.counts["audit_events"] == 1
    assert result.counts["drafts_created"] == 1


def test_build_is_byte_deterministic_for_same_inputs(tmp_path):
    out_a = tmp_path / "a.tar.gz"
    out_b = tmp_path / "b.tar.gz"

    dir_a = tmp_path / "first"
    dir_b = tmp_path / "second"
    dir_a.mkdir()
    dir_b.mkdir()

    builder_a, _ = _build_pair(dir_a)
    yaml_a = _write_customer_yaml(dir_a, {"customer_name": "Acme"})
    _run(
        builder_a.build(
            _request(dir_a, yaml_a, output_path=out_a)
        )
    )

    # Fresh shared connection so we don't double-count the
    # chain-of-custody row from the first build.
    builder_b, _ = _build_pair(dir_b)
    yaml_b = _write_customer_yaml(dir_b, {"customer_name": "Acme"})
    _run(
        builder_b.build(
            _request(dir_b, yaml_b, output_path=out_b)
        )
    )

    # The README, summary PDF, and manifest all quote the manifest
    # sha256 (and the manifest itself carries generated_at). The
    # underlying data files (CSV / JSON dumps + redacted yaml) are the
    # deterministic invariant; the rendered artifacts above intentionally
    # change because they cite the manifest.
    deterministic_members = {
        "03-audit-log.csv",
        "05-customer-yaml.redacted.yml",
        "06-memory-snapshot.json",
        "07-skill-catalog.json",
        "09-boot-checks.csv",
    }

    def _hashes(path: Path) -> dict:
        with tarfile.open(path, "r:gz") as tar:
            return {
                name: hashlib.sha256(tar.extractfile(name).read()).hexdigest()
                for name in tar.getnames()
                if name in deterministic_members
            }

    assert _hashes(out_a) == _hashes(out_b)


# ---------------------------------------------------------------------------
# Audit coverage (#2122): an empty section is itself a claim
#
# matter_ref was added to the audit schema after seats had begun writing
# rows; those rows carry NULL forever and cannot be backfilled. A packet
# whose audit section is empty for THAT reason must not be mistakable for
# one whose audit section is empty because nothing happened.
# ---------------------------------------------------------------------------


def _seed_unattributed(conn: sqlite3.Connection, *, id_: str, ts: str) -> None:
    """A pre-fix audit row: real activity, no matter attribution."""
    _seed_audit_row(
        conn,
        id=id_,
        ts=ts,
        action_type="DRAFT_CREATED",
        skill_name="inbox-triage",
        matter_ref=None,
    )


def test_matter_scoped_zero_matches_with_unattributed_rows_refuses_to_build(tmp_path):
    """The headline defect: --matter <id> matched nothing, but the period
    holds rows that carry no attribution and may belong to that matter.

    Shipping an empty audit section here asserts "nothing happened on
    this matter" to an auditor. The system cannot support that claim, so
    the build halts instead.
    """
    builder, conn = _build_pair(tmp_path)
    _seed_unattributed(conn, id_="01HZZ00000000000000000C1", ts="2026-04-10T09:00:00.000Z")
    _seed_unattributed(conn, id_="01HZZ00000000000000000C2", ts="2026-04-20T09:00:00.000Z")
    customer_yaml = _write_customer_yaml(tmp_path, {"customer_name": "Acme"})

    with pytest.raises(EvidencePacketError) as exc:
        _run(builder.build(_request(tmp_path, customer_yaml, matter="m-9")))

    message = str(exc.value)
    assert "matched 0 audit rows" in message
    assert "2 rows in this period carry no matter attribution" in message
    assert (
        "from 2026-04-10T09:00:00.000Z to 2026-04-20T09:00:00.000Z" in message
    )
    assert "--matter all" in message
    assert "--acknowledge-unattributed-gap" in message
    # No partial artifact left behind.
    assert not (tmp_path / "out" / "evidence.tar.gz").exists()


def test_matter_scoped_zero_matches_with_full_attribution_builds_a_complete_zero(
    tmp_path,
):
    """The other zero: every row in the period IS attributed, just not to
    this matter. That zero is complete and the packet says so."""
    builder, conn = _build_pair(tmp_path)
    _seed_audit_row(
        conn,
        id="01HZZ00000000000000000D1",
        ts="2026-04-10T09:00:00.000Z",
        action_type="DRAFT_CREATED",
        matter_ref="m-1",
    )
    customer_yaml = _write_customer_yaml(tmp_path, {"customer_name": "Acme"})

    result = _run(builder.build(_request(tmp_path, customer_yaml, matter="m-9")))

    assert result.counts["audit_events"] == 0
    assert result.coverage.zero_is_complete is True
    assert result.coverage.is_unanswerable_empty is False

    readme = _member_bytes(result.output_path, "00-README.md").decode("utf-8")
    assert "## What this package covers, and what it cannot" in readme
    assert "This zero is complete" in readme
    assert "nothing was recorded against this matter during this period" in readme

    manifest = _manifest_of(result.output_path)
    coverage = manifest["extra"]["coverage"]
    assert coverage["rows_matching_matter"] == 0
    assert coverage["rows_unattributed"] == 0
    assert coverage["zero_is_complete"] is True


def test_matter_scoped_zero_matches_builds_when_gap_is_acknowledged(tmp_path):
    """The acknowledgement lifts the refusal. It does NOT soften the
    packet: the gap is on the face of the README and the PDF, and the
    acknowledgement is itself recorded."""
    builder, conn = _build_pair(tmp_path)
    _seed_unattributed(conn, id_="01HZZ00000000000000000E1", ts="2026-04-10T09:00:00.000Z")
    customer_yaml = _write_customer_yaml(tmp_path, {"customer_name": "Acme"})

    result = _run(
        builder.build(
            _request(
                tmp_path,
                customer_yaml,
                matter="m-9",
                acknowledge_unattributed_gap=True,
            )
        )
    )

    assert result.coverage.is_unanswerable_empty is True
    assert result.coverage.gap_acknowledged is True

    readme = _member_bytes(result.output_path, "00-README.md").decode("utf-8")
    assert "its audit section is EMPTY" in readme
    assert 'Read that as "this system cannot answer the question"' in readme
    assert 'NOT as "nothing happened on this matter"' in readme
    assert "1 row in this period carries no matter attribution" in readme
    assert (
        "acknowledged this gap before it was written: captain@example.com"
        in readme
    )

    pdf = _member_bytes(result.output_path, "01-summary.pdf")
    assert b"What this package covers, and what it cannot" in pdf
    assert b"EMPTY" in pdf

    manifest = _manifest_of(result.output_path)
    coverage = manifest["extra"]["coverage"]
    assert coverage["unanswerable_empty"] is True
    assert coverage["gap_acknowledged"] is True
    assert coverage["acknowledged_by"] == "captain@example.com"


def test_matter_scoped_with_matches_discloses_the_unattributed_remainder(tmp_path):
    """Partial coverage: the matter has activity AND the period holds
    rows nobody can scope. The packet states the remainder rather than
    presenting its counts as a complete tally."""
    builder, conn = _build_pair(tmp_path)
    _seed_audit_row(
        conn,
        id="01HZZ00000000000000000F1",
        ts="2026-04-10T09:00:00.000Z",
        action_type="DRAFT_CREATED",
        matter_ref="m-1",
    )
    _seed_unattributed(conn, id_="01HZZ00000000000000000F2", ts="2026-04-11T09:00:00.000Z")
    _seed_unattributed(conn, id_="01HZZ00000000000000000F3", ts="2026-04-12T09:00:00.000Z")
    customer_yaml = _write_customer_yaml(tmp_path, {"customer_name": "Acme"})

    result = _run(builder.build(_request(tmp_path, customer_yaml, matter="m-1")))

    assert result.counts["audit_events"] == 1
    assert result.coverage.rows_unattributed == 2
    assert result.coverage.unattributed_first_ts == "2026-04-11T09:00:00.000Z"
    assert result.coverage.unattributed_last_ts == "2026-04-12T09:00:00.000Z"

    readme = _member_bytes(result.output_path, "00-README.md").decode("utf-8")
    assert "1 row in the period carries that attribution" in readme
    assert "A further 2 rows in this period carry no matter attribution" in readme
    assert "from 2026-04-11T09:00:00.000Z to 2026-04-12T09:00:00.000Z" in readme
    assert "floor for this matter" in readme

    # The unattributed rows' CONTENTS stay out: they may concern other
    # clients. Only their count and time span are disclosed.
    audit_csv = _member_bytes(result.output_path, "03-audit-log.csv").decode("utf-8")
    assert "01HZZ00000000000000000F1" in audit_csv
    assert "01HZZ00000000000000000F2" not in audit_csv
    assert "01HZZ00000000000000000F3" not in audit_csv

    pdf = _member_bytes(result.output_path, "01-summary.pdf")
    assert b"FLOOR" in pdf
    assert b"truthful zeros" not in pdf


def test_customer_wide_export_never_refuses_and_states_its_scope(tmp_path):
    """--matter all includes every row regardless of attribution, so it
    has no unanswerable case. It still reports how many rows cannot be
    scoped to a matter, because that is the per-matter capability
    boundary an auditor needs to know about."""
    builder, conn = _build_pair(tmp_path)
    _seed_audit_row(
        conn,
        id="01HZZ00000000000000000G1",
        ts="2026-04-10T09:00:00.000Z",
        action_type="DRAFT_CREATED",
        matter_ref="m-1",
    )
    _seed_unattributed(conn, id_="01HZZ00000000000000000G2", ts="2026-04-11T09:00:00.000Z")
    customer_yaml = _write_customer_yaml(tmp_path, {"customer_name": "Acme"})

    result = _run(builder.build(_request(tmp_path, customer_yaml, matter="all")))

    assert result.coverage.is_unanswerable_empty is False
    assert result.counts["audit_events"] == 2

    readme = _member_bytes(result.output_path, "00-README.md").decode("utf-8")
    assert "This export is customer wide" in readme
    assert "Of those, 1 carries no matter attribution" in readme

    audit_csv = _member_bytes(result.output_path, "03-audit-log.csv").decode("utf-8")
    assert "01HZZ00000000000000000G1" in audit_csv
    assert "01HZZ00000000000000000G2" in audit_csv


def test_customer_wide_export_with_no_rows_at_all_is_a_truthful_zero(tmp_path):
    """An empty period with an intact audit table keeps the original
    truthful-zero wording; the new coverage machinery must not turn every
    quiet packet into a warning."""
    builder, _ = _build_pair(tmp_path)
    customer_yaml = _write_customer_yaml(tmp_path, {"customer_name": "Acme"})

    result = _run(builder.build(_request(tmp_path, customer_yaml, matter="all")))

    assert result.coverage.has_unattributed_rows is False
    pdf = _member_bytes(result.output_path, "01-summary.pdf")
    assert b"truthful zeros" in pdf
    assert b"FLOOR" not in pdf


def test_missing_audit_table_is_not_reported_as_zero_activity(tmp_path):
    """"No such table" is not "nothing happened". A matter-scoped export
    against a source with no audit_log refuses outright."""
    builder, read_conn, audit_conn = _build_pair_without_audit_table(tmp_path)
    customer_yaml = _write_customer_yaml(tmp_path, {"customer_name": "Acme"})

    with pytest.raises(EvidencePacketError) as exc:
        _run(builder.build(_request(tmp_path, customer_yaml, matter="m-1")))
    assert "no audit_log table" in str(exc.value)

    read_conn.close()
    audit_conn.close()


def test_missing_audit_table_customer_wide_says_it_cannot_report(tmp_path):
    """The customer-wide export still builds (the other evidence files
    are real), but it must not let its empty audit section stand as
    evidence of quiet."""
    builder, read_conn, audit_conn = _build_pair_without_audit_table(tmp_path)
    customer_yaml = _write_customer_yaml(tmp_path, {"customer_name": "Acme"})

    result = _run(builder.build(_request(tmp_path, customer_yaml, matter="all")))

    assert result.coverage.table_present is False
    readme = _member_bytes(result.output_path, "00-README.md").decode("utf-8")
    assert "audit_log table was not present" in readme
    assert "evidence that nothing happened" in readme

    read_conn.close()
    audit_conn.close()


def test_unattributed_rows_outside_the_period_do_not_trip_the_refusal(tmp_path):
    """The coverage boundary is the requested period, not all of history.
    Narrowing --from/--to to a window after attribution began is one of
    the remedies the refusal offers, so it has to actually work."""
    builder, conn = _build_pair(tmp_path)
    _seed_unattributed(conn, id_="01HZZ00000000000000000H1", ts="2026-01-01T09:00:00.000Z")
    _seed_audit_row(
        conn,
        id="01HZZ00000000000000000H2",
        ts="2026-04-10T09:00:00.000Z",
        action_type="DRAFT_CREATED",
        matter_ref="m-1",
    )
    customer_yaml = _write_customer_yaml(tmp_path, {"customer_name": "Acme"})

    result = _run(
        builder.build(
            _request(
                tmp_path,
                customer_yaml,
                matter="m-9",
                period_start="2026-04-01T00:00:00Z",
                period_end="2026-04-30T23:59:59Z",
            )
        )
    )
    assert result.coverage.zero_is_complete is True


def test_prior_export_rows_do_not_count_against_coverage(tmp_path):
    """A COMPLIANCE_PACKET_EXPORTED row for --matter all writes
    matter_ref = NULL. Without excluding those, the first customer-wide
    export would make every later per-matter export look unanswerable."""
    builder, conn = _build_pair(tmp_path)
    _seed_audit_row(
        conn,
        id="01HZZ00000000000000000J1",
        ts="2026-04-10T09:00:00.000Z",
        action_type="DRAFT_CREATED",
        matter_ref="m-1",
    )
    customer_yaml = _write_customer_yaml(tmp_path, {"customer_name": "Acme"})

    # First: a customer-wide export, which leaves a NULL-matter row behind.
    _run(
        builder.build(
            _request(
                tmp_path,
                customer_yaml,
                matter="all",
                output_path=tmp_path / "out" / "wide.tar.gz",
            )
        )
    )
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) AS n FROM audit_log "
        "WHERE action_type = 'COMPLIANCE_PACKET_EXPORTED' AND matter_ref IS NULL"
    )
    row = cur.fetchone()
    assert (row["n"] if isinstance(row, dict) else row[0]) == 1

    # Then: a per-matter export for a quiet matter still reads as a
    # complete zero rather than tripping on the export's own row.
    result = _run(
        builder.build(
            _request(
                tmp_path,
                customer_yaml,
                matter="m-9",
                output_path=tmp_path / "out" / "narrow.tar.gz",
            )
        )
    )
    assert result.coverage.rows_unattributed == 0
    assert result.coverage.zero_is_complete is True


def test_coverage_is_recorded_on_the_chain_of_custody_row(tmp_path):
    """The export's own audit row carries the coverage block, so the
    boundary is reconstructible from the log even without the packet."""
    builder, conn = _build_pair(tmp_path)
    _seed_audit_row(
        conn,
        id="01HZZ00000000000000000K1",
        ts="2026-04-10T09:00:00.000Z",
        action_type="DRAFT_CREATED",
        matter_ref="m-1",
    )
    _seed_unattributed(conn, id_="01HZZ00000000000000000K2", ts="2026-04-11T09:00:00.000Z")
    customer_yaml = _write_customer_yaml(tmp_path, {"customer_name": "Acme"})

    _run(builder.build(_request(tmp_path, customer_yaml, matter="m-1")))

    cur = conn.cursor()
    cur.execute(
        "SELECT metadata FROM audit_log "
        "WHERE action_type = 'COMPLIANCE_PACKET_EXPORTED'"
    )
    row = cur.fetchone()
    metadata_text = row["metadata"] if isinstance(row, dict) else row[0]
    coverage = json.loads(metadata_text)["coverage"]
    assert coverage["rows_matching_matter"] == 1
    assert coverage["rows_unattributed"] == 1
    assert coverage["unattributed_first_ts"] == "2026-04-11T09:00:00.000Z"


def test_readme_and_pdf_state_the_same_coverage_wording(tmp_path):
    """Two surfaces, one wording. A compliance artifact that describes
    its limits differently in two places invites the question of which
    one is the real one."""
    builder, conn = _build_pair(tmp_path)
    _seed_audit_row(
        conn,
        id="01HZZ00000000000000000L1",
        ts="2026-04-10T09:00:00.000Z",
        action_type="DRAFT_CREATED",
        matter_ref="m-1",
    )
    _seed_unattributed(conn, id_="01HZZ00000000000000000L2", ts="2026-04-11T09:00:00.000Z")
    customer_yaml = _write_customer_yaml(tmp_path, {"customer_name": "Acme"})

    result = _run(builder.build(_request(tmp_path, customer_yaml, matter="m-1")))

    readme = _member_bytes(result.output_path, "00-README.md").decode("utf-8")
    pdf = _member_bytes(result.output_path, "01-summary.pdf").decode(
        "latin-1", "replace"
    )
    heading = "What this package covers, and what it cannot"
    assert heading in readme
    assert heading in pdf
    # A distinctive token from the shared narrative reaches both surfaces.
    assert "01HZZ" not in readme  # row ids are never disclosed in the prose
    for token in ("no matter attribution", "customer-wide export"):
        assert token in readme
        # The PDF wraps at 88 chars, so check the token's first word
        # survives rather than the whole phrase.
        assert token.split()[0] in pdf


# ---------------------------------------------------------------------------
# Pinned chain head (ss#2500)
#
# The packet could always say a mutated or deleted MIDDLE row would show. It
# could never say anything about rows cut off the END, because what survives
# such a cut is itself a valid chain. A head recorded off the Machine is the
# only input that closes that, and these tests hold the three outcomes apart:
# checked and present, checked and gone (halt), and not checked (disclosed).
# ---------------------------------------------------------------------------

_CHAIN_SCHEMA_EXTRA = (
    "ALTER TABLE audit_log ADD COLUMN prev_hash TEXT;"
    "ALTER TABLE audit_log ADD COLUMN row_hash TEXT;"
)

_PIN_A = "a" * 64
_PIN_B = "b" * 64


def _build_pair_with_chain_columns(tmp_path: Path):
    """A read source shaped like a real ledger: audit_log plus the link columns."""
    builder, conn = _build_pair(tmp_path)
    conn.executescript(_CHAIN_SCHEMA_EXTRA)
    conn.commit()
    return builder, conn


def _seed_chained_row(conn: sqlite3.Connection, *, id_: str, ts: str, row_hash: str) -> None:
    _seed_audit_row(conn, id=id_, ts=ts, action_type="DRAFT_CREATED", matter_ref="m-1")
    conn.execute("UPDATE audit_log SET row_hash = ? WHERE id = ?", [row_hash, id_])
    conn.commit()


def test_a_present_pinned_head_is_stated_in_the_readme(tmp_path):
    builder, conn = _build_pair_with_chain_columns(tmp_path)
    _seed_chained_row(
        conn, id_="01HZZ00000000000000000P1", ts="2026-04-10T09:00:00.000Z", row_hash=_PIN_A
    )
    customer_yaml = _write_customer_yaml(tmp_path, {"customer_name": "Acme"})

    result = _run(builder.build(_request(tmp_path, customer_yaml, pinned_head=_PIN_A)))

    assert result.chain_pin.present is True
    assert result.chain_pin.was_checked is True
    readme = _member_bytes(result.output_path, "00-README.md").decode("utf-8")
    assert "Whether the log itself is complete" in readme
    assert _PIN_A in readme
    # The pin's SOURCE is named, so a reader can go and ask for it.
    assert "audit_head_history" in readme
    # And the limit is stated in the same breath, not omitted.
    assert "Rows written" in readme


def test_a_missing_pinned_head_halts_the_build(tmp_path):
    """THE falsifier for the packet half.

    The ledger holds a different head; the one recorded off the Machine is gone.
    Before ss#2500 this built a clean packet asserting a complete record.
    """
    builder, conn = _build_pair_with_chain_columns(tmp_path)
    _seed_chained_row(
        conn, id_="01HZZ00000000000000000P2", ts="2026-04-10T09:00:00.000Z", row_hash=_PIN_B
    )
    customer_yaml = _write_customer_yaml(tmp_path, {"customer_name": "Acme"})

    with pytest.raises(EvidencePacketError) as exc:
        _run(builder.build(_request(tmp_path, customer_yaml, pinned_head=_PIN_A)))
    assert "is NOT present in this ledger" in str(exc.value)
    # No partial artifact: the halt happens before anything is rendered.
    assert not (tmp_path / "out" / "evidence.tar.gz").exists()


def test_the_same_ledger_builds_cleanly_when_the_pin_is_omitted(tmp_path):
    """The negative control for the test above.

    Same ledger, same rows. The only difference is whether a pin was supplied,
    which is what makes the halt attributable to the pin check and not to
    something else about the fixture.
    """
    builder, conn = _build_pair_with_chain_columns(tmp_path)
    _seed_chained_row(
        conn, id_="01HZZ00000000000000000P3", ts="2026-04-10T09:00:00.000Z", row_hash=_PIN_B
    )
    customer_yaml = _write_customer_yaml(tmp_path, {"customer_name": "Acme"})

    result = _run(builder.build(_request(tmp_path, customer_yaml)))

    assert result.chain_pin.was_checked is False
    readme = _member_bytes(result.output_path, "00-README.md").decode("utf-8")
    assert "checked for internal consistency only" in readme
    assert "removed from the END" in readme


def test_a_malformed_pin_is_refused_before_any_read(tmp_path):
    """A junk pin matches nothing, so carrying it forward would print
    "the record was truncated" on a packet about a healthy ledger."""
    builder, _ = _build_pair_with_chain_columns(tmp_path)
    customer_yaml = _write_customer_yaml(tmp_path, {"customer_name": "Acme"})
    with pytest.raises(EvidencePacketError) as exc:
        _run(builder.build(_request(tmp_path, customer_yaml, pinned_head="not-a-hash")))
    assert "sha256 hexdigest" in str(exc.value)


def test_a_source_without_chain_columns_halts_rather_than_reporting_a_break(tmp_path):
    """"Could not look" must never be reported as "looked and it is gone"."""
    builder, conn = _build_pair(tmp_path)  # no prev_hash / row_hash columns
    _seed_audit_row(
        conn,
        id="01HZZ00000000000000000P4",
        ts="2026-04-10T09:00:00.000Z",
        action_type="DRAFT_CREATED",
        matter_ref="m-1",
    )
    customer_yaml = _write_customer_yaml(tmp_path, {"customer_name": "Acme"})
    with pytest.raises(EvidencePacketError) as exc:
        _run(builder.build(_request(tmp_path, customer_yaml, pinned_head=_PIN_A)))
    message = str(exc.value)
    assert "carries no hash-chain columns" in message
    assert "NOT present" not in message


def test_the_pin_is_recorded_on_the_chain_of_custody_row(tmp_path):
    """The pin travels INSIDE the chain it attests to, so a later reader can
    take this row's own hash as the next pin without a new mechanism."""
    builder, conn = _build_pair_with_chain_columns(tmp_path)
    _seed_chained_row(
        conn, id_="01HZZ00000000000000000P5", ts="2026-04-10T09:00:00.000Z", row_hash=_PIN_A
    )
    customer_yaml = _write_customer_yaml(tmp_path, {"customer_name": "Acme"})

    _run(builder.build(_request(tmp_path, customer_yaml, pinned_head=_PIN_A)))

    row = conn.execute(
        "SELECT metadata FROM audit_log WHERE action_type = 'COMPLIANCE_PACKET_EXPORTED'"
    ).fetchone()
    metadata = json.loads(row["metadata"])
    assert metadata["chain_pin"]["pinned_head"] == _PIN_A
    assert metadata["chain_pin"]["present"] is True
    manifest = _manifest_of(tmp_path / "out" / "evidence.tar.gz")
    assert manifest["extra"]["chain_pin"]["checked"] is True
    assert manifest["extra"]["chain_pin"]["present"] is True


def test_readme_publishes_a_recipe_the_firm_can_run_without_us(tmp_path):
    """ss-console#2501. The packet must tell counsel how to check a message
    body against the copy their own mail system stored, in commands they can
    run, and must not offer one recipe where the two transports need two.

    The falsifier this guards against is a plausible-looking instruction that
    cannot work: byte equality on a Graph reply fails every time, because Graph
    composes the stored message. A README that published equality alone would
    read as thorough and send an auditor after a hash that can never match.
    """
    builder, conn = _build_pair(tmp_path)
    _seed_audit_row(
        conn,
        id="01HZZ00000000000000000R1",
        ts="2026-04-10T09:00:00.000Z",
        action_type="REPLY_SENT",
        matter_ref="m-1",
    )
    customer_yaml = _write_customer_yaml(tmp_path, {"customer_name": "Acme"})

    result = _run(builder.build(_request(tmp_path, customer_yaml, matter="m-1")))
    readme = _member_bytes(result.output_path, "00-README.md").decode("utf-8")

    assert "## Checking a message against your own copy" in readme
    # Both field names, so a reader can find them in the CSV.
    assert "body_digest_authored" in readme
    assert "body_digest_authored_html" in readme
    # Runnable commands, not a description of a check.
    assert "openssl dgst -sha256 stored.txt" in readme
    assert "grep -F -f candidate.txt stored.txt" in readme
    # The firm holds the STORED message, not our authored text, so the
    # instruction has to start from what they have. A recipe that says
    # "save the authored text" is a recipe nobody can run.
    assert "you take to be what the Operator wrote" in readme
    # And the outcome is described as a fact about their mail system rather
    # than promised. Whether Graph embeds our bytes unchanged inside its
    # wrapper is Microsoft's behavior, and it is not probed anywhere in
    # this repo; promising a match would be asserting an unverified vendor
    # shape to a client.
    assert "not a promise from us" in readme
    # Both recipes, each named, and the reply case explained rather than
    # asserted: an auditor who is told only "containment" will assume we are
    # hedging.
    assert "the test is EQUALITY" in readme
    assert "the test is CONTAINMENT" in readme
    assert "the quoted original appended" in readme
    # The claim must not outrun the fields. Only reply rows carry these today,
    # and a README that implied every sent message could be checked this way
    # would be a fabricated capability in a client-facing artifact.
    assert "sent as a REPLY carry these" in readme
    assert "on its own initiative records" in readme
    # And a reader who cannot tell which transport sent a given message is
    # given a check that is correct either way rather than a coin flip.
    assert "run the containment check: it holds in both" in readme
    # And the internal digest is fenced off rather than left to be attempted.
    assert "`body_digest` is internal" in readme
