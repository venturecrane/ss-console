"""The real decommission backends, driven with fake transports.

Every backend is exercised on three paths: the thing exists and is removed,
the thing is already gone and the step reports a clean skip, and the vendor
answers with an error that must surface rather than be read as success.
The environment wiring is checked both ways: no credentials wires nothing
(the fail-closed refusal in decommission_cli stays armed), full credentials
wire all five.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))

from bin.lib.decommission import DecommissionPipeline  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
from bin.lib.decommission_backends import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    AGENTMAIL_API,
    ARCHIVE_SEGMENT,
    CLOUDFLARE_API,
    HEALTHCHECKS_API,
    AgentMailInboxDeprovisioner,
    CloudflareR2NamespaceDeleter,
    FlyAppDestroyer,
    HealthchecksAndFleetStatusCleanup,
    HttpResponse,
    WranglerVectorizeIndexDeleter,
    backends_from_env,
    customer_r2_prefixes,
    http_request,
    seat_inbox_address,
)
from bin.lib.console_d1 import ConsoleD1  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
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
# R2
# ---------------------------------------------------------------------------


def _r2_list_response(url: str) -> HttpResponse:
    # Two pages for the vaults/ prefix, one archive object that must survive.
    if "prefix=vaults%2Facme%2F" in url and "cursor=" not in url:
        return HttpResponse(
            200,
            {
                "result": [{"key": "vaults/acme/customer.yaml"}, {"key": "vaults/acme/output-classes.json"}],
                "result_info": {"cursor": "c2"},
            },
        )
    if "prefix=vaults%2Facme%2F" in url and "cursor=c2" in url:
        return HttpResponse(
            200,
            {
                "result": [{"key": f"vaults/acme/{ARCHIVE_SEGMENT}final.zip"}],
                "result_info": {},
            },
        )
    return HttpResponse(200, {"result": [], "result_info": {}})


def test_r2_deletes_every_object_under_the_seat_prefixes_but_never_the_archive():
    http = _FakeHttp(
        {
            ("GET", f"{CLOUDFLARE_API}/accounts/acct/r2/buckets/"): _r2_list_response,
            ("DELETE", f"{CLOUDFLARE_API}/accounts/acct/r2/buckets/"): HttpResponse(204, None),
        }
    )
    deleter = CloudflareR2NamespaceDeleter("acct", "tok", http=http)
    manifest = _run(deleter.delete_namespace("acme"))
    assert manifest["objects_deleted"] == 2
    assert manifest["archive_objects_retained"] == 1
    assert manifest.get("skipped") is None
    deleted = [u for m, u in http.calls if m == "DELETE"]
    assert len(deleted) == 2
    assert all("decommission-archive" not in u for u in deleted)
    # Keys are URL-encoded as a single path segment.
    assert deleted[0].endswith("/objects/vaults%2Facme%2Fcustomer.yaml")
    # All three (bucket, prefix) pairs were listed, the archive bucket never.
    listed = [u for m, u in http.calls if m == "GET"]
    assert all("smd-audit-archive" not in u for u in listed)
    assert len(customer_r2_prefixes("acme")) == 3


def test_r2_empty_namespace_is_a_clean_skip():
    http = _FakeHttp({("GET", f"{CLOUDFLARE_API}/accounts/"): HttpResponse(200, {"result": [], "result_info": {}})})
    manifest = _run(CloudflareR2NamespaceDeleter("acct", "tok", http=http).delete_namespace("acme"))
    assert manifest["skipped"] is True
    assert manifest["reason"] == "namespace_already_empty"


def test_r2_list_error_surfaces():
    http = _FakeHttp({("GET", f"{CLOUDFLARE_API}/accounts/"): HttpResponse(403, {"errors": []})})
    with pytest.raises(RuntimeError, match="r2 list"):
        _run(CloudflareR2NamespaceDeleter("acct", "tok", http=http).delete_namespace("acme"))


# ---------------------------------------------------------------------------
# Vectorize
# ---------------------------------------------------------------------------


def test_vectorize_deletes_only_indexes_that_exist():
    runner = _FakeRunner(
        {
            "npx wrangler vectorize list": (0, json.dumps([{"name": "hermes-acme-vault"}, {"name": "other"}]), ""),
            "npx wrangler vectorize delete": (0, "", ""),
        }
    )
    manifest = _run(WranglerVectorizeIndexDeleter(runner=runner).delete_indexes("acme"))
    assert manifest["indexes_deleted"] == 1
    assert manifest["indexes"] == ["hermes-acme-vault"]
    assert ["npx", "wrangler", "vectorize", "delete", "hermes-acme-vault"] in runner.calls


def test_vectorize_absent_indexes_are_a_skip_not_an_error():
    runner = _FakeRunner({"npx wrangler vectorize list": (0, "[]", "")})
    manifest = _run(WranglerVectorizeIndexDeleter(runner=runner).delete_indexes("acme"))
    assert manifest["skipped"] is True
    assert manifest["reason"] == "indexes_already_absent"
    assert manifest["checked"] == ["hermes-acme-vault", "hermes-acme-corrections"]
    assert not any(c[2:4] == ["vectorize", "delete"] for c in runner.calls)


def test_vectorize_delete_failure_surfaces():
    runner = _FakeRunner(
        {
            "npx wrangler vectorize list": (0, json.dumps([{"name": "hermes-acme-vault"}]), ""),
            "npx wrangler vectorize delete": (1, "", "boom"),
        }
    )
    with pytest.raises(RuntimeError, match="vectorize delete"):
        _run(WranglerVectorizeIndexDeleter(runner=runner).delete_indexes("acme"))


# ---------------------------------------------------------------------------
# AgentMail
# ---------------------------------------------------------------------------


def test_seat_inbox_address_prefers_authored_then_convention(tmp_path):
    y = tmp_path / "customer.yaml"
    y.write_text("connectors:\n  Email:\n    inbox_address: Ops@Example.Com\n")
    assert seat_inbox_address(y, "acme") == "ops@example.com"
    assert seat_inbox_address(tmp_path / "missing.yaml", "Acme ") == "acme@agentmail.to"


def test_agentmail_deletes_the_seat_inbox_by_listing_then_id(tmp_path):
    http = _FakeHttp(
        {
            ("GET", f"{AGENTMAIL_API}/inboxes"): HttpResponse(
                200, {"inboxes": [{"inbox_id": "other@agentmail.to"}, {"inbox_id": "Acme@agentmail.to"}]}
            ),
            ("DELETE", f"{AGENTMAIL_API}/inboxes/"): HttpResponse(204, None),
        }
    )
    manifest = _run(AgentMailInboxDeprovisioner("key", tmp_path, http=http).deprovision("acme"))
    assert manifest == {"skipped": False, "identities_removed": 1, "inbox_id": "Acme@agentmail.to"}
    assert ("DELETE", f"{AGENTMAIL_API}/inboxes/Acme%40agentmail.to") in http.calls


def test_agentmail_absent_inbox_is_a_skip(tmp_path):
    http = _FakeHttp({("GET", f"{AGENTMAIL_API}/inboxes"): HttpResponse(200, {"inboxes": []})})
    manifest = _run(AgentMailInboxDeprovisioner("key", tmp_path, http=http).deprovision("acme"))
    assert manifest["skipped"] is True
    assert manifest["reason"] == "inbox_already_absent"
    assert manifest["inbox"] == "acme@agentmail.to"


def test_agentmail_list_failure_surfaces(tmp_path):
    http = _FakeHttp({("GET", f"{AGENTMAIL_API}/inboxes"): HttpResponse(401, {"error": "nope"})})
    with pytest.raises(RuntimeError, match="agentmail list"):
        _run(AgentMailInboxDeprovisioner("key", tmp_path, http=http).deprovision("acme"))


# ---------------------------------------------------------------------------
# Fly
# ---------------------------------------------------------------------------


def test_fly_destroys_the_app_with_yes_when_it_exists():
    runner = _FakeRunner(
        {
            "fly apps list --json": (0, json.dumps([{"Name": "hermes-acme"}, {"Name": "hermes-other"}]), ""),
            "fly apps destroy hermes-acme": (0, "Destroyed app hermes-acme", ""),
        }
    )
    manifest = _run(FlyAppDestroyer(runner=runner).destroy_machine("acme"))
    assert manifest == {"skipped": False, "app_destroyed": True, "app": "hermes-acme"}
    assert ["fly", "apps", "destroy", "hermes-acme", "--yes"] in runner.calls


def test_fly_absent_app_is_a_skip():
    runner = _FakeRunner({"fly apps list --json": (0, "[]", "")})
    manifest = _run(FlyAppDestroyer(runner=runner).destroy_machine("acme"))
    assert manifest["skipped"] is True
    assert manifest["reason"] == "app_already_absent"
    assert not any(c[:3] == ["fly", "apps", "destroy"] for c in runner.calls)


def test_fly_destroy_failure_surfaces():
    runner = _FakeRunner(
        {
            "fly apps list --json": (0, json.dumps([{"Name": "hermes-acme"}]), ""),
            "fly apps destroy hermes-acme": (1, "", "permission denied"),
        }
    )
    with pytest.raises(RuntimeError, match="fly apps destroy"):
        _run(FlyAppDestroyer(runner=runner).destroy_machine("acme"))


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


class _CountingD1Runner:
    """A wrangler stand-in with a per-table row count for the slug.

    ``SELECT COUNT(*)`` answers from the table; ``DELETE`` empties it unless
    the table is in ``inert`` (a delete that matches nothing, the case the
    old boolean manifest could not distinguish); a table in ``missing`` fails
    the way wrangler reports an absent table. Every statement is recorded.
    """

    def __init__(self, counts: dict, *, inert: set | None = None, missing: set | None = None) -> None:
        self.counts = dict(counts)
        self.inert = inert or set()
        self.missing = missing or set()
        self.calls: list[list[str]] = []

    @staticmethod
    def _table(sql: str) -> str:
        for token in ("FROM ",):
            if token in sql:
                return sql.split(token, 1)[1].split()[0]
        raise AssertionError(f"no table in {sql}")

    def __call__(self, cmd):
        self.calls.append(list(cmd))
        sql = cmd[-1]
        table = self._table(sql)
        if table in self.missing:
            return subprocess.CompletedProcess(list(cmd), 1, "", f"D1_ERROR: no such table: {table}")
        if sql.startswith("SELECT COUNT(*)"):
            body = json.dumps([{"results": [{"n": self.counts.get(table, 0)}]}])
            return subprocess.CompletedProcess(list(cmd), 0, body, "")
        if sql.startswith("DELETE FROM"):
            if table not in self.inert:
                self.counts[table] = 0
            return subprocess.CompletedProcess(list(cmd), 0, json.dumps([{"results": []}]), "")
        raise AssertionError(f"unexpected statement {sql}")


def _d1_runner(**kwargs):
    counts = {"operator_runtime_summary": 1, "fleet_status": 1, "machine_credentials": 1}
    return _CountingD1Runner(counts, **kwargs)


def test_observability_deletes_the_check_by_uuid_and_both_d1_rows():
    http = _FakeHttp(
        {
            ("GET", f"{HEALTHCHECKS_API}/checks/?tag=operator&tag=acme"): HttpResponse(
                200, {"checks": [{"name": "hermes-other", "uuid": "u0"}, {"name": "hermes-acme", "uuid": "u1"}]}
            ),
            ("DELETE", f"{HEALTHCHECKS_API}/checks/u1"): HttpResponse(200, {}),
        }
    )
    runner = _d1_runner()
    cleanup = HealthchecksAndFleetStatusCleanup("hc", ConsoleD1(runner=runner), http=http)
    manifest = _run(cleanup.cleanup("acme"))
    assert manifest["healthchecks_check_cancelled"] is True
    assert manifest["healthchecks_check_uuid"] == "u1"
    assert manifest["fleet_status_row_deleted"] is True
    assert manifest["runtime_summary_row_deleted"] is True
    assert manifest["machine_credentials_row_deleted"] is True
    sqls = [c[-1] for c in runner.calls]
    assert any(s.startswith("DELETE FROM fleet_status WHERE customer_slug = CAST(x'") for s in sqls)
    assert any(s.startswith("DELETE FROM operator_runtime_summary WHERE") for s in sqls)
    assert any(s.startswith("DELETE FROM machine_credentials WHERE") for s in sqls)
    # The slug travels as a hex CAST, never as a quoted literal.
    assert all("'acme'" not in s for s in sqls)


def test_observability_reports_observed_row_counts():
    """The manifest is a probe: each table is counted before and after, and
    the numbers reported are what was read back, not what was attempted."""
    runner = _d1_runner()
    manifest = _run(
        HealthchecksAndFleetStatusCleanup(None, ConsoleD1(runner=runner), http=_FakeHttp({})).cleanup("acme")
    )
    for key in ("fleet_status", "runtime_summary", "machine_credentials"):
        assert manifest[f"{key}_rows_deleted"] == 1
        assert manifest[f"{key}_rows_remaining"] == 0
        assert manifest[f"{key}_row_deleted"] is True
    assert manifest["machine_credentials_table_present"] is True
    sqls = [c[-1] for c in runner.calls]
    # count, delete, count -- per table, in that order.
    for table in ("operator_runtime_summary", "fleet_status", "machine_credentials"):
        mine = [s for s in sqls if f" {table} " in s or s.endswith(table)]
        kinds = [s.split()[0] for s in mine]
        assert kinds == ["SELECT", "DELETE", "SELECT"], (table, kinds)


def test_observability_reports_a_delete_that_matched_nothing():
    """A DELETE that removed no row must not be reported as a deletion; the
    read-back count is what the flag is computed from."""
    runner = _d1_runner(inert={"fleet_status"})
    manifest = _run(
        HealthchecksAndFleetStatusCleanup(None, ConsoleD1(runner=runner), http=_FakeHttp({})).cleanup("acme")
    )
    assert manifest["fleet_status_rows_deleted"] == 0
    assert manifest["fleet_status_rows_remaining"] == 1
    assert manifest["fleet_status_row_deleted"] is False
    assert manifest["runtime_summary_row_deleted"] is True


def test_observability_tolerates_a_d1_without_machine_credentials():
    """Before migration 0114 lands the table does not exist; that is an
    observation (nothing to revoke), reported as such rather than raised."""
    runner = _d1_runner(missing={"machine_credentials"})
    manifest = _run(
        HealthchecksAndFleetStatusCleanup(None, ConsoleD1(runner=runner), http=_FakeHttp({})).cleanup("acme")
    )
    assert manifest["machine_credentials_table_present"] is False
    assert manifest["machine_credentials_row_deleted"] is False
    assert manifest["machine_credentials_rows_deleted"] == 0
    assert manifest["fleet_status_row_deleted"] is True


def test_observability_raises_when_a_seat_table_is_unreadable():
    """An unreachable D1 must not read as "nothing left"."""
    runner = _d1_runner(missing={"fleet_status"})
    with pytest.raises(RuntimeError, match="no such table: fleet_status"):
        _run(HealthchecksAndFleetStatusCleanup(None, ConsoleD1(runner=runner), http=_FakeHttp({})).cleanup("acme"))


def test_observability_without_healthchecks_key_still_clears_d1():
    runner = _d1_runner()
    http = _FakeHttp({})
    manifest = _run(HealthchecksAndFleetStatusCleanup(None, ConsoleD1(runner=runner), http=http).cleanup("acme"))
    assert manifest["healthchecks_check_cancelled"] is False
    assert manifest["healthchecks_configured"] is False
    assert manifest["fleet_status_row_deleted"] is True
    assert http.calls == []
    # Three tables, each counted, deleted, counted.
    assert len(runner.calls) == 9


def test_observability_absent_check_is_not_an_error():
    http = _FakeHttp({("GET", f"{HEALTHCHECKS_API}/checks/"): HttpResponse(200, {"checks": []})})
    manifest = _run(HealthchecksAndFleetStatusCleanup("hc", ConsoleD1(runner=_d1_runner()), http=http).cleanup("acme"))
    assert manifest["healthchecks_check_cancelled"] is False
    assert manifest["healthchecks_check_uuid"] is None


def test_count_where_slug_refuses_a_non_identifier_table():
    with pytest.raises(ValueError):
        ConsoleD1(runner=_d1_runner()).count_where_slug("fleet_status; DROP", "acme")


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
    kwargs, wired = backends_from_env("acme", tmp_path, env, runner=_FakeRunner({}), http=_FakeHttp({}))
    assert set(kwargs) == {"r2_deleter", "vectorize_deleter", "agentmail", "fly", "observability", "archiver"}
    assert all(wired.values())
    assert isinstance(kwargs["r2_deleter"], CloudflareR2NamespaceDeleter)
    assert isinstance(kwargs["fly"], FlyAppDestroyer)
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
