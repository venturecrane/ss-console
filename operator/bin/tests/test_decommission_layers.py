"""Every decommission layer against a STATEFUL fake vendor, with a read-back.

A canned-response fake answers the same thing before and after a delete, so
it cannot tell a removal from a 2xx that kept the object. These fakes hold
state: a delete changes what the next list returns, unless the test marks an
object "sticky" (the vendor said yes and kept it). Each layer is asserted
three ways (Law 14):

* it removes the seat's resources and leaves every other seat's alone;
* after the removal the fake is READ AGAIN (the backend's own ``survey``) and
  absence is asserted, not inferred from the delete call having been made;
* a vendor that acknowledges a delete and keeps the object makes the step
  raise, rather than report a removal that did not happen.

The console layer runs against a real sqlite database built from
``migrations/`` with foreign keys on, so the ``customer_configs`` cascade to
``client_obligations`` and ``machine_credentials`` is the schema's own
behaviour, not a model of it.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
import urllib.parse
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))

from bin.lib.console_d1 import ConsoleD1  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
from bin.lib.decommission_backends import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    ARCHIVE_SEGMENT,
    OPS_SWEEP_REPO,
    AgentMailInboxDeprovisioner,
    CloudflareR2NamespaceDeleter,
    FlyAppDestroyer,
    HealthchecksAndFleetStatusCleanup,
    HttpResponse,
    WranglerVectorizeIndexDeleter,
    seat_inbox_address,
)
from bin.lib.decommission_inventory import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    SEAT_PROJECTION_TABLES,
)

REPO_ROOT = _HERE.parents[3]


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# R2: buckets of keys behind the Cloudflare REST shape the deleter speaks
# ---------------------------------------------------------------------------


class FakeR2:
    """``buckets`` maps a bucket name to its keys. Listing pages two keys at a
    time so the cursor path runs. ``sticky`` keys and buckets acknowledge a
    delete and stay."""

    PAGE = 2

    def __init__(self, buckets: dict[str, list[str]], *, sticky=(), sticky_buckets=(), list_status=200):
        self.buckets = {b: sorted(keys) for b, keys in buckets.items()}
        self.sticky = set(sticky)
        self.sticky_buckets = set(sticky_buckets)
        self.list_status = list_status
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url))
        parts = urllib.parse.urlsplit(url)
        tail = parts.path.split("/r2/buckets/", 1)[1]
        bucket, _, rest = tail.partition("/")
        bucket = urllib.parse.unquote(bucket)
        if rest == "objects" and method == "GET":
            if self.list_status != 200:
                return HttpResponse(self.list_status, {"errors": []})
            if bucket not in self.buckets:
                return HttpResponse(404, {"errors": [{"code": 10006}]})
            q = urllib.parse.parse_qs(parts.query)
            prefix = q.get("prefix", [""])[0]
            start = int(q.get("cursor", ["0"])[0])
            keys = [k for k in self.buckets[bucket] if k.startswith(prefix)]
            page = keys[start : start + self.PAGE]
            more = start + self.PAGE < len(keys)
            return HttpResponse(
                200,
                {
                    "result": [{"key": k} for k in page],
                    "result_info": {"cursor": str(start + self.PAGE) if more else ""},
                },
            )
        if rest.startswith("objects/") and method == "DELETE":
            key = urllib.parse.unquote(rest[len("objects/") :])
            if bucket not in self.buckets or key not in self.buckets[bucket]:
                return HttpResponse(404, None)
            if key not in self.sticky:
                self.buckets[bucket].remove(key)
            return HttpResponse(204, None)
        if rest == "" and method == "GET":
            return HttpResponse(200 if bucket in self.buckets else 404, {})
        if rest == "" and method == "DELETE":
            if bucket not in self.buckets:
                return HttpResponse(404, {})
            if self.buckets[bucket]:
                return HttpResponse(409, {"errors": [{"code": 10008}]})
            if bucket not in self.sticky_buckets:
                del self.buckets[bucket]
            return HttpResponse(204, None)
        raise AssertionError(f"unexpected R2 request {method} {url}")


def _r2_world(**kw) -> FakeR2:
    return FakeR2(
        {
            "smd-customer-config": [
                "vaults/acme/customer.yaml",
                "vaults/acme/medchron-firm.yaml",
                "vaults/acme/medchron-controls/controls.json",
                f"vaults/acme/{ARCHIVE_SEGMENT}final.zip",
                "customers/acme/legacy.yaml",
                "vaults/other/customer.yaml",
                "vaults/acme-two/customer.yaml",
            ],
            "ss-operator-acme-skills": ["skill-a/SKILL.md", "skill-b/SKILL.md", "skill-c/SKILL.md"],
            "ss-operator-other-skills": ["skill-a/SKILL.md"],
            # The smd seat's pre-rename bucket. The old deleter reached into it
            # for EVERY slug (prefix "<slug>/"); acme must never touch it.
            "ss-ai-employee-smd-skills": ["acme/skill-x/SKILL.md", "smd/skill/SKILL.md"],
            "smd-audit-archive": ["audit/acme/2026-10-01.jsonl"],
        },
        **kw,
    )


def test_r2_removes_the_seat_footprint_and_reads_it_back_absent():
    r2 = _r2_world()
    deleter = CloudflareR2NamespaceDeleter("acct", "tok", http=r2)
    assert deleter.survey("acme")["present"] is True

    manifest = _run(deleter.delete_namespace("acme"))

    # The seat's own bucket is emptied AND deleted, not just its keys.
    assert manifest["buckets_deleted"] == ["ss-operator-acme-skills"]
    assert "ss-operator-acme-skills" not in r2.buckets
    assert manifest["objects_deleted"] == 4 + 3
    assert manifest["archive_objects_retained"] == 1
    # Negative probe: the backend re-reads the vendor and finds nothing.
    after = deleter.survey("acme")
    assert after["present"] is False
    assert set(after["prefixes"].values()) == {0}
    assert all(not b["exists"] for b in after["buckets"].values())
    # Everything that is not this seat's is untouched, byte for byte.
    assert r2.buckets["ss-ai-employee-smd-skills"] == ["acme/skill-x/SKILL.md", "smd/skill/SKILL.md"]
    assert r2.buckets["ss-operator-other-skills"] == ["skill-a/SKILL.md"]
    assert r2.buckets["smd-audit-archive"] == ["audit/acme/2026-10-01.jsonl"]
    assert sorted(r2.buckets["smd-customer-config"]) == sorted(
        [f"vaults/acme/{ARCHIVE_SEGMENT}final.zip", "vaults/acme-two/customer.yaml", "vaults/other/customer.yaml"]
    )
    assert not any("ss-ai-employee-smd-skills" in u or "smd-audit-archive" in u for _, u in r2.calls)


def test_r2_deletes_a_seat_bucket_under_its_pre_rename_name():
    r2 = FakeR2({"smd-customer-config": [], "ss-ai-employee-acme-skills": ["s/SKILL.md"]})
    manifest = _run(CloudflareR2NamespaceDeleter("acct", "tok", http=r2).delete_namespace("acme"))
    assert manifest["buckets_deleted"] == ["ss-ai-employee-acme-skills"]
    assert set(r2.buckets) == {"smd-customer-config"}


def test_r2_keeps_a_seat_bucket_that_holds_the_archive_and_says_so():
    r2 = FakeR2(
        {"smd-customer-config": [], "ss-operator-acme-skills": [f"{ARCHIVE_SEGMENT}packet.tar.gz", "s/SKILL.md"]}
    )
    manifest = _run(CloudflareR2NamespaceDeleter("acct", "tok", http=r2).delete_namespace("acme"))
    seat = manifest["seat_buckets"]["ss-operator-acme-skills"]
    assert seat["bucket_deleted"] is False and seat["archive_objects_retained"] == 1
    assert r2.buckets["ss-operator-acme-skills"] == [f"{ARCHIVE_SEGMENT}packet.tar.gz"]


def test_r2_object_the_vendor_keeps_after_a_2xx_raises():
    r2 = _r2_world(sticky={"vaults/acme/customer.yaml"})
    with pytest.raises(RuntimeError, match="still holds 1 object"):
        _run(CloudflareR2NamespaceDeleter("acct", "tok", http=r2).delete_namespace("acme"))


def test_r2_bucket_the_vendor_keeps_after_a_2xx_raises():
    r2 = _r2_world(sticky_buckets={"ss-operator-acme-skills"})
    with pytest.raises(RuntimeError, match="still exists after delete"):
        _run(CloudflareR2NamespaceDeleter("acct", "tok", http=r2).delete_namespace("acme"))


def test_r2_already_gone_is_a_clean_skip():
    r2 = FakeR2({"smd-customer-config": ["vaults/other/customer.yaml"]})
    deleter = CloudflareR2NamespaceDeleter("acct", "tok", http=r2)
    manifest = _run(deleter.delete_namespace("acme"))
    assert manifest["skipped"] is True and manifest["reason"] == "namespace_already_empty"
    assert deleter.survey("acme")["present"] is False


def test_r2_list_error_surfaces():
    with pytest.raises(RuntimeError, match="r2 list"):
        _run(CloudflareR2NamespaceDeleter("acct", "tok", http=_r2_world(list_status=403)).delete_namespace("acme"))


# ---------------------------------------------------------------------------
# Vectorize
# ---------------------------------------------------------------------------


class FakeVectorize:
    def __init__(self, names, *, sticky=(), fail_delete=False):
        self.names = set(names)
        self.sticky = set(sticky)
        self.fail_delete = fail_delete

    def __call__(self, cmd):
        if cmd[:4] == ["npx", "wrangler", "vectorize", "list"]:
            return subprocess.CompletedProcess(cmd, 0, json.dumps([{"name": n} for n in sorted(self.names)]), "")
        if cmd[:4] == ["npx", "wrangler", "vectorize", "delete"]:
            if self.fail_delete:
                return subprocess.CompletedProcess(cmd, 1, "", "boom")
            if cmd[4] not in self.sticky:
                self.names.discard(cmd[4])
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"unexpected command {cmd}")


def test_vectorize_removes_the_seat_indexes_and_reads_them_back_absent():
    fake = FakeVectorize({"hermes-acme-vault", "hermes-acme-corrections", "hermes-other-vault"})
    deleter = WranglerVectorizeIndexDeleter(runner=fake)
    manifest = _run(deleter.delete_indexes("acme"))
    assert manifest["indexes_deleted"] == 2
    assert deleter.survey("acme") == {
        "present": False,
        "indexes": {"hermes-acme-vault": False, "hermes-acme-corrections": False},
    }
    assert fake.names == {"hermes-other-vault"}


def test_vectorize_index_kept_after_a_clean_exit_raises():
    fake = FakeVectorize({"hermes-acme-vault"}, sticky={"hermes-acme-vault"})
    with pytest.raises(RuntimeError, match="still listed"):
        _run(WranglerVectorizeIndexDeleter(runner=fake).delete_indexes("acme"))


def test_vectorize_absent_is_a_skip_and_failure_surfaces():
    manifest = _run(WranglerVectorizeIndexDeleter(runner=FakeVectorize(set())).delete_indexes("acme"))
    assert manifest["skipped"] is True and manifest["checked"] == ["hermes-acme-vault", "hermes-acme-corrections"]
    with pytest.raises(RuntimeError, match="vectorize delete"):
        _run(
            WranglerVectorizeIndexDeleter(runner=FakeVectorize({"hermes-acme-vault"}, fail_delete=True)).delete_indexes(
                "acme"
            )
        )


# ---------------------------------------------------------------------------
# AgentMail: inboxes and org-level webhooks
# ---------------------------------------------------------------------------


class FakeAgentMail:
    def __init__(self, inboxes, webhooks, *, sticky=(), list_status=200):
        self.inboxes = list(inboxes)
        self.webhooks = dict(webhooks)  # id -> url
        self.sticky = set(sticky)
        self.list_status = list_status

    def __call__(self, method, url, headers, body):
        path = urllib.parse.urlsplit(url).path.removeprefix("/v0")
        if method == "GET" and path == "/inboxes":
            if self.list_status != 200:
                return HttpResponse(self.list_status, {"error": "nope"})
            return HttpResponse(200, {"inboxes": [{"inbox_id": i} for i in self.inboxes]})
        if method == "GET" and path == "/webhooks":
            return HttpResponse(200, {"webhooks": [{"webhook_id": k, "url": v} for k, v in self.webhooks.items()]})
        if method == "DELETE" and path.startswith("/inboxes/"):
            ident = urllib.parse.unquote(path[len("/inboxes/") :])
            if ident not in self.inboxes:
                return HttpResponse(404, None)
            if ident not in self.sticky:
                self.inboxes.remove(ident)
            return HttpResponse(204, None)
        if method == "DELETE" and path.startswith("/webhooks/"):
            ident = urllib.parse.unquote(path[len("/webhooks/") :])
            if ident not in self.webhooks:
                return HttpResponse(404, None)
            if ident not in self.sticky:
                del self.webhooks[ident]
            return HttpResponse(204, None)
        raise AssertionError(f"unexpected AgentMail request {method} {url}")


def _mail_world(**kw) -> FakeAgentMail:
    return FakeAgentMail(
        ["Acme@agentmail.to", "other@agentmail.to"],
        {
            "wh-acme": "https://hermes-acme.fly.dev/webhooks/agentmail",
            "wh-other": "https://hermes-other.fly.dev/webhooks/agentmail",
            # Look-alikes that must survive: a longer slug, and a host that
            # merely starts with the seat's host.
            "wh-acme-two": "https://hermes-acme-two.fly.dev/webhooks/agentmail",
            "wh-evil": "https://hermes-acme.fly.dev.example.test/webhooks/agentmail",
        },
        **kw,
    )


def test_seat_inbox_address_prefers_authored_then_convention(tmp_path):
    y = tmp_path / "customer.yaml"
    y.write_text("connectors:\n  Email:\n    inbox_address: Ops@Example.Com\n")
    assert seat_inbox_address(y, "acme") == "ops@example.com"
    assert seat_inbox_address(tmp_path / "missing.yaml", "Acme ") == "acme@agentmail.to"


def test_agentmail_removes_the_inbox_and_the_seats_webhook_only(tmp_path):
    mail = _mail_world()
    deprov = AgentMailInboxDeprovisioner("key", tmp_path, http=mail)
    assert deprov.survey("acme")["present"] is True
    manifest = _run(deprov.deprovision("acme"))
    assert manifest["identities_removed"] == 1 and manifest["inbox_id"] == "Acme@agentmail.to"
    assert manifest["webhooks_removed"] == ["wh-acme"]
    assert deprov.survey("acme") == {
        "present": False,
        "inbox": "acme@agentmail.to",
        "inbox_present": False,
        "webhooks": [],
    }
    assert mail.inboxes == ["other@agentmail.to"]
    assert set(mail.webhooks) == {"wh-other", "wh-acme-two", "wh-evil"}


def test_agentmail_inbox_the_vendor_keeps_raises(tmp_path):
    with pytest.raises(RuntimeError, match="still listed after delete"):
        _run(
            AgentMailInboxDeprovisioner("key", tmp_path, http=_mail_world(sticky={"Acme@agentmail.to"})).deprovision(
                "acme"
            )
        )


def test_agentmail_webhook_the_vendor_keeps_raises(tmp_path):
    with pytest.raises(RuntimeError, match="webhook"):
        _run(AgentMailInboxDeprovisioner("key", tmp_path, http=_mail_world(sticky={"wh-acme"})).deprovision("acme"))


def test_agentmail_already_gone_is_a_skip_and_list_failure_surfaces(tmp_path):
    manifest = _run(AgentMailInboxDeprovisioner("key", tmp_path, http=FakeAgentMail([], {})).deprovision("acme"))
    assert manifest["skipped"] is True and manifest["reason"] == "inbox_already_absent"
    assert manifest["inbox"] == "acme@agentmail.to"
    with pytest.raises(RuntimeError, match="agentmail list"):
        _run(AgentMailInboxDeprovisioner("key", tmp_path, http=_mail_world(list_status=401)).deprovision("acme"))


# ---------------------------------------------------------------------------
# Fly + the ops workspace's GitHub sweep secrets
# ---------------------------------------------------------------------------


class FakeFlyGh:
    def __init__(self, apps, secrets=(), variables=(), *, sticky=(), fail_destroy="", gh_broken=False):
        self.apps = set(apps)
        self.names = {"secret": set(secrets), "variable": set(variables)}
        self.sticky = set(sticky)
        self.fail_destroy = fail_destroy
        self.gh_broken = gh_broken
        self.calls: list[list[str]] = []

    def __call__(self, cmd):
        cmd = list(cmd)
        self.calls.append(cmd)

        def ok(out: str = "") -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(cmd, 0, out, "")

        if cmd[:3] == ["fly", "apps", "list"]:
            return ok(json.dumps([{"Name": a} for a in sorted(self.apps)]))
        if cmd[:3] == ["fly", "apps", "destroy"]:
            if cmd[3] == self.fail_destroy:
                return subprocess.CompletedProcess(cmd, 1, "", "permission denied")
            if cmd[3] not in self.sticky:
                self.apps.discard(cmd[3])
            return ok()
        if cmd[0] == "gh":
            if self.gh_broken:
                return subprocess.CompletedProcess(cmd, 1, "", "gh: not logged in")
            assert cmd[cmd.index("--repo") + 1] == OPS_SWEEP_REPO
            kind, verb = cmd[1], cmd[2]
            if verb == "list":
                return ok(json.dumps([{"name": n} for n in sorted(self.names[kind])]))
            if verb == "delete":
                if cmd[3] not in self.sticky:
                    self.names[kind].discard(cmd[3])
                return ok()
        raise AssertionError(f"unexpected command {cmd}")


def _fly_world(**kw) -> FakeFlyGh:
    return FakeFlyGh(
        {"hermes-acme", "ops-acme", "hermes-other", "ops-other"},
        {"OPS_FLY_TOKEN__ACME", "OPS_HC_URL__ACME", "OPS_FLY_TOKEN__OTHER", "UNRELATED"},
        {"OPS_SWEEP_TOKEN_EXPIRES__ACME", "OPS_SWEEP_TOKEN_EXPIRES__OTHER"},
        **kw,
    )


def test_fly_removes_the_seat_its_workspace_and_the_sweep_names_and_reads_back():
    fake = _fly_world()
    fly = FlyAppDestroyer(runner=fake)
    assert fly.preflight("acme") is None
    manifest = _run(fly.destroy_machine("acme"))
    assert manifest["app_destroyed"] is True
    assert manifest["ops_workspace"]["destroyed"] is True
    assert sorted(manifest["ops_workspace"]["github_sweep_names_removed"]) == [
        "OPS_FLY_TOKEN__ACME",
        "OPS_HC_URL__ACME",
        "OPS_SWEEP_TOKEN_EXPIRES__ACME",
    ]
    after = fly.survey("acme")
    assert after["present"] is False
    assert after["apps"] == {"hermes-acme": False, "ops-acme": False}
    assert fake.apps == {"hermes-other", "ops-other"}
    assert fake.names["secret"] == {"OPS_FLY_TOKEN__OTHER", "UNRELATED"}
    assert fake.names["variable"] == {"OPS_SWEEP_TOKEN_EXPIRES__OTHER"}
    # gh is only ever asked for NAMES.
    assert all(c[-2:] == ["--json", "name"] for c in fake.calls if c[0] == "gh" and c[2] == "list")


@pytest.mark.parametrize("app", ["hermes-acme", "ops-acme"])
def test_fly_app_still_listed_after_destroy_raises(app):
    with pytest.raises(RuntimeError, match="still listed"):
        _run(FlyAppDestroyer(runner=_fly_world(sticky={app})).destroy_machine("acme"))


def test_fly_sweep_secret_kept_after_delete_raises():
    with pytest.raises(RuntimeError, match="sweep names still on"):
        _run(FlyAppDestroyer(runner=_fly_world(sticky={"OPS_HC_URL__ACME"})).destroy_machine("acme"))


def test_fly_preflight_refuses_when_gh_cannot_answer_and_touches_nothing():
    fake = _fly_world(gh_broken=True)
    assert "cannot read the ops sweep secrets" in FlyAppDestroyer(runner=fake).preflight("acme")
    assert not any(c[:3] == ["fly", "apps", "destroy"] for c in fake.calls)


def test_fly_already_gone_is_a_skip_and_destroy_failure_surfaces():
    manifest = _run(FlyAppDestroyer(runner=FakeFlyGh({"hermes-other"})).destroy_machine("acme"))
    assert manifest["skipped"] is True and manifest["reason"] == "app_already_absent"
    with pytest.raises(RuntimeError, match="fly apps destroy"):
        _run(FlyAppDestroyer(runner=_fly_world(fail_destroy="hermes-acme")).destroy_machine("acme"))


# ---------------------------------------------------------------------------
# Console: healthchecks.io + a real ss-console-db schema in sqlite
# ---------------------------------------------------------------------------


class FakeHealthchecks:
    def __init__(self, checks, *, sticky=()):
        self.checks = [dict(c) for c in checks]
        self.sticky = set(sticky)

    def __call__(self, method, url, headers, body):
        parts = urllib.parse.urlsplit(url)
        if method == "GET":
            tags = urllib.parse.parse_qs(parts.query).get("tag", [])
            hits = [c for c in self.checks if all(t in c["tags"].split() for t in tags)]
            return HttpResponse(200, {"checks": hits})
        if method == "DELETE":
            uuid = parts.path.rsplit("/", 1)[1]
            if not any(c["uuid"] == uuid for c in self.checks):
                return HttpResponse(404, {})
            if uuid not in self.sticky:
                self.checks = [c for c in self.checks if c["uuid"] != uuid]
            return HttpResponse(200, {})
        raise AssertionError(f"unexpected healthchecks request {method} {url}")


def _hc_world(**kw) -> FakeHealthchecks:
    return FakeHealthchecks(
        [
            {"name": "hermes-acme", "uuid": "u-acme", "tags": "operator acme"},
            {"name": "ops-acme", "uuid": "w-acme", "tags": "ops-workspace acme"},
            {"name": "hermes-other", "uuid": "u-other", "tags": "operator other"},
        ],
        **kw,
    )


class SqliteD1:
    """``npx wrangler d1 execute ... --command SQL`` against an in-memory
    sqlite holding every migration, foreign keys ON (as D1 enforces them).
    ``resurrect`` re-inserts a row after the Nth DELETE on a table, the shape
    of a heartbeat landing mid-cleanup."""

    def __init__(self) -> None:
        # Autocommit: sqlite ignores PRAGMA foreign_keys inside an open
        # transaction, and the driver's implicit one would leave the cascade
        # under test silently disabled.
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        for mig in sorted((REPO_ROOT / "migrations").glob("*.sql")):
            self.conn.executescript(mig.read_text(encoding="utf-8"))
        self.conn.execute("PRAGMA foreign_keys = ON")
        assert self.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        self.resurrect: dict[str, dict] = {}
        self.statements: list[str] = []

    def seed(self, table: str, **values) -> None:
        cols = list(self.conn.execute(f"PRAGMA table_info('{table}')"))
        row = {c[1]: ("x" if "TEXT" in (c[2] or "").upper() else 0) for c in cols if c[3] and c[4] is None}
        row.update(values)
        self.conn.execute("PRAGMA foreign_keys = OFF")
        keys = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        self.conn.execute(f"INSERT INTO {table} ({keys}) VALUES ({marks})", list(row.values()))  # noqa: S608 - test fixture, table names are literals in this file
        self.conn.execute("PRAGMA foreign_keys = ON")

    def count(self, table: str, slug: str, column: str = "customer_slug") -> int:
        return self.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (slug,)).fetchone()[0]  # noqa: S608 - test fixture; table and column are literals in this file, the slug is bound

    def __call__(self, cmd):
        assert cmd[:5] == ["npx", "wrangler", "d1", "execute", "ss-console-db"], cmd
        sql = cmd[cmd.index("--command") + 1]
        self.statements.append(sql)
        try:
            rows = [dict(r) for r in self.conn.execute(sql)]
        except sqlite3.Error as exc:
            return subprocess.CompletedProcess(cmd, 1, "", f"D1_ERROR: {exc}")
        if sql.startswith("DELETE FROM "):
            table = sql.split()[2]
            if table in self.resurrect:
                self.seed(table, **self.resurrect.pop(table))
        return subprocess.CompletedProcess(cmd, 0, json.dumps([{"results": rows}]), "")


def _seat(d1: SqliteD1, slug: str, entity: str) -> None:
    d1.seed("customer_configs", customer_slug=slug, entity_id=entity)
    d1.seed("fleet_status", customer_slug=slug, entity_id=entity, heartbeat_status="green")
    d1.seed("operator_runtime_summary", customer_slug=slug, entity_id=entity, summary_status="green")
    d1.seed("machine_credentials", customer_slug=slug, entity_id=entity)
    d1.seed("fleet_alert_state", customer_slug=slug, condition="heartbeat_red", status="open")
    d1.seed("fleet_holds", customer_slug=slug)
    d1.seed("mcp_issued_grants", customer_slug=slug, clerk_user_id="user_1")
    d1.seed("mcp_clerk_bindings", customer_slug=slug, entity_id=entity, resource_uri=f"https://{slug}.example/mcp")
    d1.seed("cost_telemetry", customer_slug=slug, date="2026-10-01", driver="llm")
    d1.seed("customer_config_history", customer_slug=slug, synced_by="ci")


def _obligation(d1: SqliteD1, slug: str, entity: str, key: str, state: str) -> None:
    d1.seed(
        "client_obligations",
        customer_slug=slug,
        entity_id=entity,
        stable_key=key,
        kind="deliverable",
        origin="imported",
        source_kind="github",
        state=state,
    )


def _console_world(*, open_obligation: bool = False) -> SqliteD1:
    d1 = SqliteD1()
    _seat(d1, "acme", "ent-acme")
    _seat(d1, "other", "ent-other")
    _obligation(d1, "acme", "ent-acme", "final-report", "cancelled")
    _obligation(d1, "acme", "ent-acme", "handoff-doc", "open" if open_obligation else "cancelled")
    _obligation(d1, "other", "ent-other", "their-thing", "open")
    return d1


def test_console_empties_every_projection_row_exports_obligations_and_reads_back(tmp_path):
    d1 = _console_world()
    hc = _hc_world()
    cleanup = HealthchecksAndFleetStatusCleanup("hc", ConsoleD1(runner=d1), http=hc, archive_root=tmp_path)
    before = cleanup.survey("acme")
    assert before["present"] is True and before["projection_rows"]["customer_configs"] == 1

    manifest = _run(cleanup.cleanup("acme"))

    # Every projection table, customer_configs included, is empty for the
    # slug, read straight from the database, not from the manifest.
    for table in SEAT_PROJECTION_TABLES:
        assert d1.count(table, "acme") == 0, table
        assert manifest["tables"][table]["rows_remaining"] == 0
    assert manifest["customer_configs_row_deleted"] is True
    # The cascade took the obligations, but only after they were exported.
    assert d1.count("client_obligations", "acme") == 0
    export = Path(manifest["client_obligations_exported"]["export_path"])
    assert sorted(r["stable_key"] for r in json.loads(export.read_text())) == ["final-report", "handoff-doc"]
    # History is retained by design.
    assert d1.count("cost_telemetry", "acme") == 1
    assert d1.count("customer_config_history", "acme") == 1
    # Another seat is untouched, in every table.
    for table in (*SEAT_PROJECTION_TABLES, "client_obligations"):
        assert d1.count(table, "other") == 1, table
    # Healthchecks: the seat's two checks gone, another seat's kept.
    assert [c["uuid"] for c in hc.checks] == ["u-other"]
    after = cleanup.survey("acme")
    assert after["present"] is False
    assert after["retained_rows"]["cost_telemetry"] == 1
    # The slug never travels as a quoted literal.
    assert all("'acme'" not in s for s in d1.statements)


def test_console_refuses_while_an_obligation_is_owed_and_deletes_nothing(tmp_path):
    d1 = _console_world(open_obligation=True)
    cleanup = HealthchecksAndFleetStatusCleanup("hc", ConsoleD1(runner=d1), http=_hc_world(), archive_root=tmp_path)
    assert "1 client obligation(s) for acme" in cleanup.preflight("acme")
    with pytest.raises(RuntimeError, match="obligation"):
        _run(cleanup.cleanup("acme"))
    assert d1.count("customer_configs", "acme") == 1
    assert d1.count("client_obligations", "acme") == 2
    assert not any(s.startswith("DELETE") for s in d1.statements)


def test_console_row_that_reappears_mid_cleanup_raises(tmp_path):
    d1 = _console_world()
    d1.resurrect["fleet_status"] = {"customer_slug": "acme", "entity_id": "ent-acme", "heartbeat_status": "green"}
    cleanup = HealthchecksAndFleetStatusCleanup(None, ConsoleD1(runner=d1), http=_hc_world(), archive_root=tmp_path)
    with pytest.raises(RuntimeError, match="still present"):
        _run(cleanup.cleanup("acme"))


def test_console_missing_table_raises_rather_than_reading_as_no_row(tmp_path):
    """Every migration is applied to ss-console-db, so "no such table" means
    the wrong database. It must not read as "nothing left"."""
    d1 = _console_world()
    d1.conn.execute("DROP TABLE fleet_holds")
    cleanup = HealthchecksAndFleetStatusCleanup(None, ConsoleD1(runner=d1), http=_hc_world(), archive_root=tmp_path)
    with pytest.raises(RuntimeError, match="no such table: fleet_holds"):
        _run(cleanup.cleanup("acme"))


def test_console_obligations_without_an_archive_dir_refuse_to_cascade():
    d1 = _console_world()
    cleanup = HealthchecksAndFleetStatusCleanup(None, ConsoleD1(runner=d1), http=_hc_world())
    with pytest.raises(RuntimeError, match="no archive dir"):
        _run(cleanup.cleanup("acme"))
    assert d1.count("customer_configs", "acme") == 1


def test_console_check_the_vendor_keeps_raises(tmp_path):
    cleanup = HealthchecksAndFleetStatusCleanup(
        "hc", ConsoleD1(runner=_console_world()), http=_hc_world(sticky={"u-acme"}), archive_root=tmp_path
    )
    with pytest.raises(RuntimeError, match="still listed after delete"):
        _run(cleanup.cleanup("acme"))


def test_console_without_a_healthchecks_key_still_clears_d1(tmp_path):
    d1 = _console_world()
    hc = _hc_world()
    manifest = _run(
        HealthchecksAndFleetStatusCleanup(None, ConsoleD1(runner=d1), http=hc, archive_root=tmp_path).cleanup("acme")
    )
    assert manifest["healthchecks_configured"] is False and manifest["healthchecks_check_cancelled"] is False
    assert d1.count("customer_configs", "acme") == 0
    assert len(hc.checks) == 3, "no key, no vendor call"


def test_console_rerun_on_a_retired_seat_is_a_clean_no_op(tmp_path):
    d1 = _console_world()
    cleanup = HealthchecksAndFleetStatusCleanup("hc", ConsoleD1(runner=d1), http=_hc_world(), archive_root=tmp_path)
    _run(cleanup.cleanup("acme"))
    again = _run(cleanup.cleanup("acme"))
    assert all(t["rows_deleted"] == 0 for t in again["tables"].values())
    assert again["client_obligations_exported"]["rows_exported"] == 0
