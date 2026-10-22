"""The decommission backends' transport, environment wiring and archiver.

The per-layer deleters (R2, Vectorize, AgentMail, Fly, the console) are
exercised against stateful fake vendors in ``test_decommission_layers.py``,
where every removal is followed by a re-read that asserts absence. This file
keeps what is not a layer: the https-only transport, the environment wiring
(no credentials wires nothing, so the fail-closed refusal in decommission_cli
stays armed; full credentials wire all six), and the compliance archiver.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))

from bin.lib.decommission import DecommissionPipeline  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
from bin.lib.decommission_backends import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    CloudflareR2NamespaceDeleter,
    FlyAppDestroyer,
    HealthchecksAndFleetStatusCleanup,
    backends_from_env,
    http_request,
)
from adapter.evidence import EvidencePacketError  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
from adapter.evidence.signing import SIGNING_KEY_ENV, EvidenceSigningError  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
from bin.lib.decommission_archiver import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    EvidencePacketArchiver,
    ledger_period,
)
from bin.lib.seam_pull import _write_audit_snapshot  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _FakeHttp:
    """Routes (method, url-prefix) to canned responses and records every call."""

    def __init__(self, routes: dict) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url))
        for (m, prefix), resp in self.routes.items():
            if m == method and url.startswith(prefix):
                return resp(url) if callable(resp) else resp
        raise AssertionError(f"unexpected request {method} {url}")


class _FakeRunner:
    def __init__(self, table: dict) -> None:
        self.table = table
        self.calls: list[list[str]] = []

    def __call__(self, cmd):
        self.calls.append(list(cmd))
        key = " ".join(cmd[:4])
        for prefix, (rc, out, err) in self.table.items():
            if key.startswith(prefix):
                return subprocess.CompletedProcess(list(cmd), rc, out, err)
        raise AssertionError(f"unexpected command {cmd}")


# ---------------------------------------------------------------------------
# Transport: https only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.test/x", "http://api.cloudflare.com/x"])
def test_http_request_refuses_non_https(url):
    with pytest.raises(ValueError, match="https:// only"):
        http_request("GET", url, {}, None)


# ---------------------------------------------------------------------------
# Environment wiring: nothing staged wires nothing; everything staged wires all
# ---------------------------------------------------------------------------


def test_backends_from_env_wires_nothing_without_credentials(tmp_path):
    runner = _FakeRunner({"fly auth whoami": (1, "", "not logged in")})
    kwargs, wired = backends_from_env("acme", tmp_path, {}, runner=runner, http=_FakeHttp({}))
    assert kwargs == {}
    assert wired == {name: False for name in wired}
    # And the pipeline built from them still refuses a --live run.
    pipeline = DecommissionPipeline(
        customer_slug="acme", customers_root=tmp_path, archive_root=tmp_path / "a", audit_writer=object()
    )
    assert set(pipeline.unwired_destructive_backends()) >= {
        "r2_deleter",
        "vectorize_deleter",
        "agentmail",
        "fly",
        "observability",
    }


def test_backends_from_env_wires_all_six_with_credentials(tmp_path):
    env = {
        "CLOUDFLARE_API_TOKEN": "t",
        "CLOUDFLARE_ACCOUNT_ID": "a",
        "AGENTMAIL_API_KEY": "m",
        "FLY_API_TOKEN": "f",
        "HEALTHCHECKS_API_KEY": "h",
        SIGNING_KEY_ENV: "k",
    }
    kwargs, wired = backends_from_env(
        "acme", tmp_path, env, runner=_FakeRunner({}), http=_FakeHttp({}), archive_root=tmp_path / "archive"
    )
    assert set(kwargs) == {"r2_deleter", "vectorize_deleter", "agentmail", "fly", "observability", "archiver"}
    assert all(wired.values())
    assert isinstance(kwargs["r2_deleter"], CloudflareR2NamespaceDeleter)
    assert isinstance(kwargs["fly"], FlyAppDestroyer)
    # Step 09 exports the obligations its customer_configs delete cascades
    # into the same archive the packet lands in.
    assert isinstance(kwargs["observability"], HealthchecksAndFleetStatusCleanup)
    assert kwargs["observability"].archive_root == tmp_path / "archive"
    assert isinstance(kwargs["archiver"], EvidencePacketArchiver)
    # The archiver signs with the key it was handed, not whatever the process
    # environment holds.
    assert kwargs["archiver"].signing_env == {SIGNING_KEY_ENV: "k"}


def test_backends_from_env_accepts_the_provisioning_scripts_cf_aliases(tmp_path):
    env = {"CF_API_TOKEN": "t", "CF_ACCOUNT_ID": "a"}
    runner = _FakeRunner({"fly auth whoami": (1, "", "")})
    kwargs, wired = backends_from_env("acme", tmp_path, env, runner=runner, http=_FakeHttp({}))
    assert wired["r2_deleter"] is True
    # wrangler only reads CLOUDFLARE_API_TOKEN, so the wrangler-backed ones stay unwired.
    assert wired["vectorize_deleter"] is False
    assert wired["observability"] is False


def test_backends_from_env_does_not_arm_fly_from_a_logged_in_cli(tmp_path):
    """The Captain's shell is logged into fly most of the time. That must not
    arm `fly apps destroy`; only a staged FLY_API_TOKEN does, and the wiring
    never even asks the CLI."""
    runner = _FakeRunner({"fly auth whoami": (0, "someone@smd.services", "")})
    kwargs, wired = backends_from_env("acme", tmp_path, {}, runner=runner, http=_FakeHttp({}))
    assert wired["fly"] is False and "fly" not in kwargs
    assert runner.calls == []


def test_backends_from_env_arms_fly_only_from_a_staged_token(tmp_path):
    kwargs, wired = backends_from_env(
        "acme", tmp_path, {"FLY_API_TOKEN": "f"}, runner=_FakeRunner({}), http=_FakeHttp({})
    )
    assert wired["fly"] is True and isinstance(kwargs["fly"], FlyAppDestroyer)


def test_backends_from_env_arms_the_archiver_only_with_key_and_wrangler_token(tmp_path):
    only_key, wired = backends_from_env(
        "acme", tmp_path, {SIGNING_KEY_ENV: "k"}, runner=_FakeRunner({}), http=_FakeHttp({})
    )
    assert wired["compliance_archiver"] is False and "archiver" not in only_key
    blank_key, wired = backends_from_env(
        "acme",
        tmp_path,
        {SIGNING_KEY_ENV: "  ", "CLOUDFLARE_API_TOKEN": "t"},
        runner=_FakeRunner({}),
        http=_FakeHttp({}),
    )
    assert wired["compliance_archiver"] is False and "archiver" not in blank_key


# ---------------------------------------------------------------------------
# Compliance archiver (step 07): the real packet, read back from disk
# ---------------------------------------------------------------------------


def _signing_key_b64() -> str:
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    pem = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return base64.b64encode(pem).decode()


_ROW_HASH = "a" * 64


def _archiver_world(tmp_path, *, snapshot: bool = True):
    """A customers root with the smd fixture, an archive dir holding the
    snapshot step 02 writes, and the decommission trail's audit writer."""
    import shutil
    import sqlite3

    from bin.lib.decommission_cli import _build_local_audit_writer

    customers_root = tmp_path / "customers"
    shutil.copytree(_HERE.parent.parent / "fixtures" / "smd", customers_root / "smd")
    archive_dir = tmp_path / "archive" / "smd"
    archive_dir.mkdir(parents=True)
    if snapshot:
        conn = sqlite3.connect(str(archive_dir / "machine-snapshot-2026-09-25.sqlite"))
        _write_audit_snapshot(
            conn,
            [
                {
                    "id": "r1",
                    "ts": "2026-09-01T10:00:00.123456+00:00",
                    "action_type": "DRAFT_CREATED",
                    "actor": "operator",
                    "row_hash": "0" * 64,
                },
                {
                    "id": "r2",
                    "ts": "2026-09-20T08:30:00Z",
                    "action_type": "DRAFT_SENT",
                    "actor": "operator",
                    "row_hash": _ROW_HASH,
                },
            ],
        )
        conn.close()
    writer, trail = _build_local_audit_writer(tmp_path / "trail.sqlite")
    return customers_root, archive_dir, writer, trail


def _archiver(customers_root, writer, *, key=None, pin=None):
    from datetime import datetime, timezone

    return EvidencePacketArchiver(
        customers_root=customers_root,
        signing_env={SIGNING_KEY_ENV: key or _signing_key_b64()},
        audit_writer=writer,
        pin_source=(lambda slug: pin),
        clock=lambda: datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc),
    )


def test_archiver_builds_the_signed_packet_and_reports_what_is_on_disk(tmp_path):
    import hashlib
    import tarfile

    customers_root, archive_dir, writer, trail = _archiver_world(tmp_path)
    snapshot = archive_dir / "machine-snapshot-2026-09-25.sqlite"
    before = hashlib.sha256(snapshot.read_bytes()).hexdigest()

    result = _run(_archiver(customers_root, writer, pin=_ROW_HASH).archive("smd", archive_dir))

    packet = archive_dir / "compliance-packet-2026-09-25.tar.gz"
    assert result["skipped"] is False
    assert result["archive_path"] == str(packet)
    with tarfile.open(packet, "r:gz") as tar:
        names = [m.name for m in tar.getmembers() if m.isfile()]
        manifest = tar.extractfile("manifest.json").read()  # type: ignore[union-attr]
        audit_csv = tar.extractfile("03-audit-log.csv").read().decode()  # type: ignore[union-attr]
    # Every number in the manifest is read back from the file, not echoed.
    assert result["file_count"] == len(names)
    assert "manifest.sig" in names
    assert result["signed"] is True and result["signature_verified"] is True
    assert result["manifest_sha256"] == hashlib.sha256(manifest).hexdigest()
    assert result["bytes_on_disk"] == packet.stat().st_size
    assert result["chain_pin_checked"] is True
    # The period holds every ledger row, including the fractional-second one
    # that sorts below a ...Z bound of the same second.
    assert result["period_start"] == "2026-08-31T00:00:00Z"
    assert "r1" in audit_csv and "r2" in audit_csv
    # The preserved snapshot is evidence: opened read-only, left byte-identical.
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == before
    # The chain-of-custody row lands in the decommission trail.
    exported = trail.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action_type = 'COMPLIANCE_PACKET_EXPORTED'"
    ).fetchone()
    assert exported[0] == 1


def test_archiver_rerun_reads_the_existing_packet_back_as_a_skip(tmp_path):
    customers_root, archive_dir, writer, _trail = _archiver_world(tmp_path)
    key = _signing_key_b64()
    first = _run(_archiver(customers_root, writer, key=key).archive("smd", archive_dir))
    second = _run(_archiver(customers_root, writer, key=key).archive("smd", archive_dir))
    assert second["skipped"] is True and second["reason"] == "packet_already_archived"
    assert second["file_count"] == first["file_count"]
    assert second["signature_verified"] is True


def test_archiver_without_a_snapshot_fails_rather_than_build_an_empty_packet(tmp_path):
    customers_root, archive_dir, writer, _trail = _archiver_world(tmp_path, snapshot=False)
    with pytest.raises(RuntimeError, match="machine-snapshot"):
        _run(_archiver(customers_root, writer).archive("smd", archive_dir))
    assert not list(archive_dir.glob("compliance-packet*"))


def test_archiver_halts_when_the_console_pin_is_missing_from_the_ledger(tmp_path):
    customers_root, archive_dir, writer, _trail = _archiver_world(tmp_path)
    with pytest.raises(EvidencePacketError):
        _run(_archiver(customers_root, writer, pin="b" * 64).archive("smd", archive_dir))
    assert not list(archive_dir.glob("compliance-packet*.tar.gz"))


def test_archiver_with_an_unusable_key_raises_never_ships_unsigned(tmp_path):
    import base64

    customers_root, archive_dir, writer, _trail = _archiver_world(tmp_path)
    not_a_pem = base64.b64encode(b"this is not a PEM private key").decode()
    with pytest.raises(EvidenceSigningError):
        _run(_archiver(customers_root, writer, key=not_a_pem).archive("smd", archive_dir))
    assert not list(archive_dir.glob("compliance-packet*.tar.gz"))


def test_archiver_refuses_a_packet_it_cannot_verify(tmp_path):
    """A packet on disk signed by some other key is not reported as archived."""
    customers_root, archive_dir, writer, _trail = _archiver_world(tmp_path)
    _run(_archiver(customers_root, writer).archive("smd", archive_dir))
    with pytest.raises(RuntimeError, match="not signed by the staged key"):
        _run(_archiver(customers_root, writer).archive("smd", archive_dir))


def test_archiver_failure_halts_the_pipeline_at_step_07(tmp_path):
    from bin.lib.decommission import DecommissionStepFailed

    customers_root, archive_dir, writer, _trail = _archiver_world(tmp_path, snapshot=False)

    class _Wired:
        async def preserve(self, slug, archive_dir, days):
            return {"skipped": False, "rows_preserved": 0}

    pipeline = DecommissionPipeline(
        customer_slug="smd",
        customers_root=customers_root,
        archive_root=tmp_path / "archive",
        audit_writer=writer,
        audit_log_preserver=_Wired(),
        archiver=_archiver(customers_root, writer),
    )
    with pytest.raises(DecommissionStepFailed) as exc:
        _run(pipeline.run())
    assert exc.value.step_name == "07_compliance_archive"
    # Halted before the tombstone: a resumed run still finds the customer dir.
    assert (customers_root / "smd" / "customer.yaml").exists()


def test_ledger_period_brackets_fractional_and_offset_timestamps():
    import sqlite3
    from datetime import datetime, timezone

    conn = sqlite3.connect(":memory:")
    _write_audit_snapshot(
        conn,
        [
            {"id": "a", "ts": "2026-09-01T00:00:00.5+00:00", "action_type": "X", "actor": "o"},
            {"id": "b", "ts": "2026-09-30T23:59:59.9", "action_type": "X", "actor": "o"},
        ],
    )
    start, end = ledger_period(conn, datetime(2026, 9, 25, tzinfo=timezone.utc))
    rows = conn.execute("SELECT COUNT(*) FROM audit_log WHERE ts >= ? AND ts <= ?", (start, end)).fetchone()[0]
    assert rows == 2
    empty = sqlite3.connect(":memory:")
    assert ledger_period(empty, datetime(2026, 9, 25, 12, tzinfo=timezone.utc)) == (
        "2026-09-24T00:00:00Z",
        "2026-09-25T12:00:01Z",
    )


# ---- the signing preflight (review 2026-10-06 N14) --------------------------


def test_signing_preflight_passes_a_key_that_loads_and_an_absent_one():
    from bin.lib.decommission_backends import signing_preflight

    assert signing_preflight({SIGNING_KEY_ENV: _signing_key_b64()}) is None
    assert signing_preflight({}) is None, "no key leaves the archiver unwired; the #1123 gate handles that"


def test_signing_preflight_names_a_key_that_does_not_parse():
    import base64

    from bin.lib.decommission_backends import signing_preflight

    assert signing_preflight({SIGNING_KEY_ENV: base64.b64encode(b"this is not a PEM private key").decode()})


def test_signing_preflight_names_a_missing_cryptography_install(monkeypatch):
    """The decommission ran under a uv environment holding only pyyaml: the key
    was staged, the archiver wired, and the import failed at step 07, after
    the Fly app was gone. The preflight sees that same failure first."""
    from bin.lib.decommission_backends import signing_preflight

    key = _signing_key_b64()
    monkeypatch.setitem(sys.modules, "cryptography.hazmat.primitives", None)
    problem = signing_preflight({SIGNING_KEY_ENV: key})
    assert problem and "cryptography" in problem


def test_the_decommission_and_packet_scripts_run_with_cryptography():
    bin_dir = Path(__file__).resolve().parents[1]
    for script in ("decommission-customer.sh", "generate-evidence-packet.sh"):
        assert "--with cryptography" in (bin_dir / script).read_text(), script
