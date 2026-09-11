"""Real destructive backends for the decommission pipeline (closes #1123's gap).

``bin/lib/decommission.py`` was built fail-closed on purpose: a ``--live``
run refuses (exit 5) while any destructive backend is still a stub, because
a pipeline that tombstones the customer dir and exits 0 while the Fly
Machine, its R2 vault, and its inbox all remain is a silent breach of the
offboarding promise. That refusal was the whole of the pipeline's
protection for months, and the 2026-08-25 seat retirement was done by hand
across git, D1, R2 and Fly as a result.

This module is the other half: one real implementation per Protocol, each
built as the exact inverse of ``operator/bin/provision-customer.sh`` and of
what the account actually holds (listed read-only 2026-09-10):

* **R2** is NOT one bucket per customer as ``r2-vectorize-naming.md``
  planned. Every seat's config lives under ``vaults/<slug>/`` in the shared
  ``smd-customer-config`` bucket (written by CI and the portal), agent-authored
  skills under ``<slug>/`` in ``ss-ai-employee-smd-skills``, and the audit
  archive under ``audit/<slug>/`` in ``smd-audit-archive``. The deleter walks
  the first two by prefix and never touches the third: the archive is the
  retention copy step 02 just wrote (``audit-retention.md``).
* **Vectorize**: the two indexes the naming spec reserves per customer
  (``hermes-<slug>-vault``, ``hermes-<slug>-corrections``). None exist in the
  account today; the deleter lists first and reports ``indexes_already_absent``
  rather than failing on a name that was never created.
* **AgentMail**: the seat's own inbox, resolved the way the workspace broker
  resolves it (authored ``connectors.Email.inbox_address`` or the
  ``<slug>@agentmail.to`` convention), found in the account listing, then
  ``DELETE /v0/inboxes/{id}``. Deleting the inbox revokes its inbox-scoped keys.
* **Fly**: ``fly apps destroy hermes-<slug> --yes``, which takes the Machine,
  its volume (``/opt/data``: profile homes, cron stores, the extraction cache)
  and its secrets in one act. ``fly apps list --json`` first, so a re-run on
  an already-destroyed seat is a clean skip.
* **Observability**: the healthchecks.io check ``hermes-<slug>`` (found by tag,
  deleted by uuid) and the console D1 rows in ``fleet_status`` and
  ``operator_runtime_summary`` (through ``ConsoleD1``, the same
  ``wrangler d1 execute`` path every console-side reconciler uses).

Every backend is idempotent: absence is reported as ``skipped`` with a reason,
never raised. Every backend takes its transport (an HTTP opener or a
subprocess runner) as a constructor argument so the tests drive it with fakes
and the CLI drives it with the real thing. Nothing here reads a credential
from anywhere but the environment ``backends_from_env`` is handed.

Stdlib + the repo's own helpers only: this runs inside the same
``uv run --with pyyaml`` toolchain as the CLI.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from bin.lib.console_d1 import ConsoleD1, sql_text

# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def run_subprocess(cmd: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(list(cmd), capture_output=True, text=True, check=False)  # noqa: S603 - list argv, no shell; callers pass flyctl and wrangler commands built in this module


class HttpResponse:
    """The slice of an HTTP response the backends read: status + parsed body."""

    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self.body = body


Http = Callable[[str, str, dict, Optional[dict]], HttpResponse]


def http_request(method: str, url: str, headers: dict, body: Optional[dict]) -> HttpResponse:
    """urllib transport. A non-2xx status comes back as a response, not an
    exception, so callers decide what 404 means for their step.

    Only ``https://`` is dispatched. ``urlopen`` would happily follow ``file://``
    or ``ftp://``; every caller today builds its URL from an ``https://``
    constant, and this check is what makes that a property of the transport
    rather than a claim about the callers, in a module whose whole purpose is
    to delete things.
    """
    if not url.startswith("https://"):
        raise ValueError(f"http_request dispatches https:// only, refused {url.split(':', 1)[0]}://")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=dict(headers))  # noqa: S310 - the https:// check three lines above refuses every other scheme
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - scheme checked above: https:// only, so no file:// or ftp:// can reach urlopen
            return HttpResponse(resp.status, _parse(resp.read()))
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, _parse(exc.read()))


def _parse(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# R2
# ---------------------------------------------------------------------------

CLOUDFLARE_API = "https://api.cloudflare.com/client/v4"
CUSTOMER_CONFIG_BUCKET = "smd-customer-config"
SKILLS_BUCKET = "ss-ai-employee-smd-skills"
#: The retention copy. Never in the delete set, whatever prefix it appears under.
ARCHIVE_SEGMENT = "decommission-archive/"


def customer_r2_prefixes(slug: str) -> list[tuple[str, str]]:
    """Every (bucket, prefix) a seat's objects can live under. The audit
    archive bucket is deliberately absent: it is the retention copy."""
    return [
        (CUSTOMER_CONFIG_BUCKET, f"vaults/{slug}/"),
        (CUSTOMER_CONFIG_BUCKET, f"customers/{slug}/"),
        (SKILLS_BUCKET, f"{slug}/"),
    ]


@dataclass
class CloudflareR2NamespaceDeleter:
    """Deletes a seat's objects by prefix through the Cloudflare R2 API.

    ``wrangler r2 object`` cannot list, so listing goes through
    ``GET /accounts/{id}/r2/buckets/{bucket}/objects?prefix=`` with the
    cursor the API hands back; deletion is one ``DELETE .../objects/{key}``
    per object (404 counts as already gone).
    """

    account_id: str
    api_token: str
    http: Http = http_request
    prefixes: Callable[[str], list[tuple[str, str]]] = customer_r2_prefixes

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_token}", "Accept": "application/json"}

    def _list(self, bucket: str, prefix: str) -> list[str]:
        keys: list[str] = []
        cursor: Optional[str] = None
        while True:
            query = "prefix=" + urllib.parse.quote(prefix, safe="") + "&per_page=1000"
            if cursor:
                query += "&cursor=" + urllib.parse.quote(cursor, safe="")
            url = f"{CLOUDFLARE_API}/accounts/{self.account_id}/r2/buckets/{bucket}/objects?{query}"
            resp = self.http("GET", url, self._headers(), None)
            if resp.status != 200 or not isinstance(resp.body, dict):
                raise RuntimeError(f"r2 list {bucket}/{prefix} failed: HTTP {resp.status}")
            result = resp.body.get("result") or []
            keys.extend(str(o["key"]) for o in result if isinstance(o, dict) and "key" in o)
            info = resp.body.get("result_info") or {}
            cursor = info.get("cursor") if isinstance(info, dict) else None
            if not cursor or not result:
                return keys

    def _delete(self, bucket: str, key: str) -> bool:
        url = f"{CLOUDFLARE_API}/accounts/{self.account_id}/r2/buckets/{bucket}/objects/" + urllib.parse.quote(
            key, safe=""
        )
        resp = self.http("DELETE", url, self._headers(), None)
        if resp.status in (200, 204):
            return True
        if resp.status == 404:
            return False
        raise RuntimeError(f"r2 delete {bucket}/{key} failed: HTTP {resp.status}")

    async def delete_namespace(self, customer_slug: str) -> dict:
        per_bucket: dict[str, dict[str, int]] = {}
        deleted = 0
        retained = 0
        for bucket, prefix in self.prefixes(customer_slug):
            keys = self._list(bucket, prefix)
            for key in keys:
                if ARCHIVE_SEGMENT in key[len(prefix) :]:
                    retained += 1
                    continue
                if self._delete(bucket, key):
                    deleted += 1
                    per_bucket.setdefault(bucket, {}).setdefault(prefix, 0)
                    per_bucket[bucket][prefix] += 1
        manifest: dict = {
            "objects_deleted": deleted,
            "archive_objects_retained": retained,
            "buckets": per_bucket,
        }
        if deleted == 0:
            manifest.update({"skipped": True, "reason": "namespace_already_empty"})
        return manifest


# ---------------------------------------------------------------------------
# Vectorize
# ---------------------------------------------------------------------------


def customer_vectorize_indexes(slug: str) -> list[str]:
    return [f"hermes-{slug}-vault", f"hermes-{slug}-corrections"]


@dataclass
class WranglerVectorizeIndexDeleter:
    """``wrangler vectorize list --json`` then ``wrangler vectorize delete``
    for each of the seat's two indexes that actually exists."""

    runner: Runner = run_subprocess

    def _existing(self) -> set[str]:
        proc = self.runner(["npx", "wrangler", "vectorize", "list", "--json"])
        if proc.returncode != 0:
            raise RuntimeError(f"vectorize list failed: {proc.stderr.strip() or proc.stdout.strip()}")
        try:
            payload = json.loads(proc.stdout)
        except ValueError as exc:
            raise RuntimeError("vectorize list returned non-JSON") from exc
        names: set[str] = set()
        for entry in payload if isinstance(payload, list) else []:
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                names.add(entry["name"])
        return names

    async def delete_indexes(self, customer_slug: str) -> dict:
        wanted = customer_vectorize_indexes(customer_slug)
        existing = self._existing()
        deleted: list[str] = []
        for name in wanted:
            if name not in existing:
                continue
            proc = self.runner(["npx", "wrangler", "vectorize", "delete", name])
            if proc.returncode != 0:
                raise RuntimeError(f"vectorize delete {name} failed: {proc.stderr.strip() or proc.stdout.strip()}")
            deleted.append(name)
        manifest: dict = {"indexes_deleted": len(deleted), "indexes": deleted}
        if not deleted:
            manifest.update({"skipped": True, "reason": "indexes_already_absent", "checked": wanted})
        return manifest


# ---------------------------------------------------------------------------
# AgentMail
# ---------------------------------------------------------------------------

AGENTMAIL_API = "https://api.agentmail.to/v0"


def seat_inbox_address(customer_yaml_path: Path, customer_slug: str) -> str:
    """The seat's own inbox, resolved exactly as the workspace broker resolves
    it (``workspace_broker/agentmail_auth.seat_inbox_address``): the authored
    ``connectors.Email.inbox_address`` when present, else the slug convention.
    Re-implemented here rather than imported so the Captain-side CLI does not
    grow a broker import path; the rule is three lines and pinned by test."""
    if customer_yaml_path.exists():
        try:
            import yaml  # the CLI runs under `uv run --with pyyaml`

            data = yaml.safe_load(customer_yaml_path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 - an unreadable yaml falls back to the convention
            data = {}
        connectors = data.get("connectors") if isinstance(data, dict) else None
        email = connectors.get("Email") if isinstance(connectors, dict) else None
        authored = email.get("inbox_address") if isinstance(email, dict) else None
        if isinstance(authored, str) and authored.strip():
            return authored.strip().lower()
    return f"{customer_slug.strip().lower()}@agentmail.to"


@dataclass
class AgentMailInboxDeprovisioner:
    """Finds the seat's inbox in the account listing and deletes it."""

    api_key: str
    customers_root: Path
    http: Http = http_request

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}

    async def deprovision(self, customer_slug: str) -> dict:
        address = seat_inbox_address(self.customers_root / customer_slug / "customer.yaml", customer_slug)
        listing = self.http("GET", f"{AGENTMAIL_API}/inboxes", self._headers(), None)
        if listing.status != 200 or not isinstance(listing.body, dict):
            raise RuntimeError(f"agentmail list inboxes failed: HTTP {listing.status}")
        inbox_id: Optional[str] = None
        for entry in listing.body.get("inboxes") or []:
            candidate = entry.get("inbox_id") if isinstance(entry, dict) else None
            if isinstance(candidate, str) and candidate.lower() == address:
                inbox_id = candidate
                break
        if inbox_id is None:
            return {
                "skipped": True,
                "reason": "inbox_already_absent",
                "identities_removed": 0,
                "inbox": address,
            }
        resp = self.http(
            "DELETE",
            f"{AGENTMAIL_API}/inboxes/" + urllib.parse.quote(inbox_id, safe=""),
            self._headers(),
            None,
        )
        if resp.status in (200, 202, 204):
            return {"skipped": False, "identities_removed": 1, "inbox_id": inbox_id}
        if resp.status == 404:
            return {
                "skipped": True,
                "reason": "inbox_already_absent",
                "identities_removed": 0,
                "inbox": address,
            }
        raise RuntimeError(f"agentmail delete inbox {inbox_id} failed: HTTP {resp.status}")


# ---------------------------------------------------------------------------
# Fly
# ---------------------------------------------------------------------------


def fly_app_name(slug: str) -> str:
    return f"hermes-{slug}"


@dataclass
class FlyAppDestroyer:
    """``fly apps destroy hermes-<slug> --yes``: Machine, volume and secrets in
    one act. Lists first so an already-destroyed app is a skip, not an error."""

    runner: Runner = run_subprocess

    def _apps(self) -> set[str]:
        proc = self.runner(["fly", "apps", "list", "--json"])
        if proc.returncode != 0:
            raise RuntimeError(f"fly apps list failed: {proc.stderr.strip() or proc.stdout.strip()}")
        try:
            payload = json.loads(proc.stdout)
        except ValueError as exc:
            raise RuntimeError("fly apps list returned non-JSON") from exc
        return {
            str(a["Name"])
            for a in (payload if isinstance(payload, list) else [])
            if isinstance(a, dict) and "Name" in a
        }

    async def destroy_machine(self, customer_slug: str) -> dict:
        app = fly_app_name(customer_slug)
        if app not in self._apps():
            return {"skipped": True, "reason": "app_already_absent", "app_destroyed": False, "app": app}
        proc = self.runner(["fly", "apps", "destroy", app, "--yes"])
        if proc.returncode != 0:
            raise RuntimeError(f"fly apps destroy {app} failed: {proc.stderr.strip() or proc.stdout.strip()}")
        return {"skipped": False, "app_destroyed": True, "app": app}


# ---------------------------------------------------------------------------
# Observability: healthchecks.io + console D1 rows
# ---------------------------------------------------------------------------

HEALTHCHECKS_API = "https://healthchecks.io/api/v3"


def healthchecks_check_name(slug: str) -> str:
    return f"hermes-{slug}"


#: The console D1 tables that hold a row per seat and must be empty for the
#: slug after decommission. ``machine_credentials`` is the seat's own
#: control-plane bearer (migration 0114): deleting the row is what revokes the
#: key, so a retired Machine's copy stops authenticating even if the Fly
#: destroy is the step that halted.
CONSOLE_SEAT_TABLES: tuple[str, ...] = (
    "operator_runtime_summary",
    "fleet_status",
    "machine_credentials",
)


def _is_missing_table(exc: Exception, table: str) -> bool:
    return f"no such table: {table}" in str(exc)


@dataclass
class HealthchecksAndFleetStatusCleanup:
    """Deletes the seat's healthchecks.io check and its console D1 rows.

    The manifest reports what was OBSERVED, not what was attempted: a
    ``DELETE`` through ``wrangler d1 execute`` returns an empty result set
    whether it removed a row or matched nothing, so every table is counted
    before and after and the count after is what ``*_rows_remaining`` and the
    ``*_row_deleted`` flags are computed from. That is the negative probe the
    venture's removal doctrine asks for, done by the pipeline instead of by
    hand afterwards.
    """

    hc_api_key: Optional[str]
    d1: ConsoleD1
    http: Http = http_request

    def _delete_check(self, slug: str) -> tuple[bool, Optional[str]]:
        if not self.hc_api_key:
            return False, None
        headers = {"X-Api-Key": self.hc_api_key, "Accept": "application/json"}
        listing = self.http(
            "GET",
            f"{HEALTHCHECKS_API}/checks/?tag=operator&tag=" + urllib.parse.quote(slug, safe=""),
            headers,
            None,
        )
        if listing.status != 200 or not isinstance(listing.body, dict):
            raise RuntimeError(f"healthchecks list failed: HTTP {listing.status}")
        wanted = healthchecks_check_name(slug)
        for check in listing.body.get("checks") or []:
            if not isinstance(check, dict) or check.get("name") != wanted:
                continue
            uuid = check.get("uuid")
            if not isinstance(uuid, str) or not uuid:
                raise RuntimeError("healthchecks check has no uuid (read-only key?)")
            resp = self.http("DELETE", f"{HEALTHCHECKS_API}/checks/{uuid}", headers, None)
            if resp.status in (200, 204):
                return True, uuid
            if resp.status == 404:
                return False, uuid
            raise RuntimeError(f"healthchecks delete {uuid} failed: HTTP {resp.status}")
        return False, None

    def _clear_table(self, table: str, slug: str) -> dict:
        """Count, delete, count again. Returns the observed numbers for one table.

        ``machine_credentials`` may not exist yet on a D1 whose migrations
        predate 0114; that is reported as ``table_present: False`` rather than
        raised, because the absence of the table is itself the observation that
        there is no credential row to revoke.
        """
        try:
            before = self.d1.count_where_slug(table, slug)
        except RuntimeError as exc:
            if table == "machine_credentials" and _is_missing_table(exc, table):
                return {"table_present": False, "rows_before": 0, "rows_remaining": 0, "rows_deleted": 0}
            raise
        # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query — table comes from CONSOLE_SEAT_TABLES; the slug is sql_text's hex blob literal.
        # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query — not SQLAlchemy; the only interpolations are a module constant and a fixed-alphabet hex literal.
        self.d1.execute(f"DELETE FROM {table} WHERE customer_slug = {sql_text(slug)}")  # noqa: S608 - table is a CONSOLE_SEAT_TABLES constant; the slug is sql_text's hex blob literal
        after = self.d1.count_where_slug(table, slug)
        return {
            "table_present": True,
            "rows_before": before,
            "rows_remaining": after,
            "rows_deleted": before - after,
        }

    async def cleanup(self, customer_slug: str) -> dict:
        cancelled, uuid = self._delete_check(customer_slug)
        observed = {table: self._clear_table(table, customer_slug) for table in CONSOLE_SEAT_TABLES}
        manifest: dict = {
            "healthchecks_check_cancelled": cancelled,
            "healthchecks_check_uuid": uuid,
            "healthchecks_configured": bool(self.hc_api_key),
            "customer_slug": customer_slug,
        }
        short = {
            "operator_runtime_summary": "runtime_summary",
            "fleet_status": "fleet_status",
            "machine_credentials": "machine_credentials",
        }
        for table, obs in observed.items():
            key = short[table]
            manifest[f"{key}_rows_deleted"] = obs["rows_deleted"]
            manifest[f"{key}_rows_remaining"] = obs["rows_remaining"]
            # True only when the table was read back empty for this slug.
            manifest[f"{key}_row_deleted"] = obs["table_present"] and obs["rows_remaining"] == 0
        manifest["machine_credentials_table_present"] = observed["machine_credentials"]["table_present"]
        return manifest


# ---------------------------------------------------------------------------
# Environment wiring
# ---------------------------------------------------------------------------

#: What each backend needs staged. The CLI prints this table on refusal so the
#: Captain sees exactly which credential is missing, never a bare "unwired".
BACKEND_REQUIREMENTS: dict[str, str] = {
    "r2_deleter": "CLOUDFLARE_API_TOKEN (or CF_API_TOKEN) + CLOUDFLARE_ACCOUNT_ID (or CF_ACCOUNT_ID)",
    "vectorize_deleter": "CLOUDFLARE_API_TOKEN (wrangler reads it)",
    "agentmail": "AGENTMAIL_API_KEY (the org key, not a seat key)",
    "fly": "FLY_API_TOKEN (a staged token; a logged-in `fly` CLI does not count)",
    "observability": "HEALTHCHECKS_API_KEY + CLOUDFLARE_API_TOKEN (wrangler d1)",
}


def _fly_authenticated(env: dict) -> bool:
    """Only a staged ``FLY_API_TOKEN`` arms ``fly apps destroy``.

    A logged-in ``fly`` CLI used to count. It must not: the Captain's shell is
    logged in most of the time, so the destroyer was armed on every run without
    anyone staging anything, and the ``--live`` refusal silently stopped
    covering the one layer that takes the Machine, its volume and its secrets
    in a single act. The other four backends already require an explicit
    variable; this makes Fly the same.
    """
    return bool(env.get("FLY_API_TOKEN"))


def backends_from_env(
    customer_slug: str,
    customers_root: Path,
    env: Optional[dict] = None,
    *,
    runner: Runner = run_subprocess,
    http: Http = http_request,
) -> tuple[dict, dict[str, bool]]:
    """Construct every backend whose credentials are staged.

    Returns ``(pipeline_kwargs, wired)``: the kwargs to splat into
    ``DecommissionPipeline(...)`` and a per-backend wired/unwired map for the
    CLI's report. A backend with no credentials is simply absent, so the
    pipeline keeps its stub and ``unwired_destructive_backends()`` refuses a
    ``--live`` run exactly as before; nothing here weakens that gate.
    """
    env = dict(os.environ) if env is None else env
    kwargs: dict = {}
    wired: dict[str, bool] = {name: False for name in BACKEND_REQUIREMENTS}

    cf_token = env.get("CLOUDFLARE_API_TOKEN") or env.get("CF_API_TOKEN")
    cf_account = env.get("CLOUDFLARE_ACCOUNT_ID") or env.get("CF_ACCOUNT_ID")
    wrangler_token = env.get("CLOUDFLARE_API_TOKEN")

    if cf_token and cf_account:
        kwargs["r2_deleter"] = CloudflareR2NamespaceDeleter(cf_account, cf_token, http=http)
        wired["r2_deleter"] = True
    if wrangler_token:
        kwargs["vectorize_deleter"] = WranglerVectorizeIndexDeleter(runner=runner)
        wired["vectorize_deleter"] = True
    agentmail_key = env.get("AGENTMAIL_API_KEY")
    if agentmail_key:
        kwargs["agentmail"] = AgentMailInboxDeprovisioner(agentmail_key, customers_root, http=http)
        wired["agentmail"] = True
    if _fly_authenticated(env):
        kwargs["fly"] = FlyAppDestroyer(runner=runner)
        wired["fly"] = True
    hc_key = env.get("HEALTHCHECKS_API_KEY")
    if hc_key and wrangler_token:
        kwargs["observability"] = HealthchecksAndFleetStatusCleanup(hc_key, ConsoleD1(runner=runner), http=http)
        wired["observability"] = True
    return kwargs, wired


__all__ = [
    "ARCHIVE_SEGMENT",
    "AgentMailInboxDeprovisioner",
    "BACKEND_REQUIREMENTS",
    "CloudflareR2NamespaceDeleter",
    "FlyAppDestroyer",
    "HealthchecksAndFleetStatusCleanup",
    "HttpResponse",
    "WranglerVectorizeIndexDeleter",
    "backends_from_env",
    "customer_r2_prefixes",
    "customer_vectorize_indexes",
    "fly_app_name",
    "healthchecks_check_name",
    "seat_inbox_address",
]
