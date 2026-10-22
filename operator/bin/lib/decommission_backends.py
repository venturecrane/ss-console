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

* **R2**: every seat's config lives under ``vaults/<slug>/`` in the shared
  ``smd-customer-config`` bucket (written by CI and the portal); its
  agent-authored skill bodies live in its OWN bucket, ``ss-operator-<slug>-skills``
  (provision-customer.sh step 5b; safety invariant 7 requires that name), or
  ``ss-ai-employee-<slug>-skills`` for a seat provisioned before the #1189
  rename; the audit archive is ``audit/<slug>/`` in ``smd-audit-archive``. The
  deleter empties the shared prefixes, empties and deletes the seat's own
  bucket, and never touches the archive: it is the retention copy step 02 just
  wrote (``audit-retention.md``). Until 2026-10-22 this deleted the prefix
  ``<slug>/`` inside ``ss-ai-employee-smd-skills``, which is the smd seat's
  pre-rename bucket, and never removed the per-seat bucket at all. The names
  now come from ``decommission_inventory``, the one list of what a seat is.
* **Vectorize**: the two indexes the naming spec reserves per customer
  (``hermes-<slug>-vault``, ``hermes-<slug>-corrections``). None exist in the
  account today; the deleter lists first and reports ``indexes_already_absent``
  rather than failing on a name that was never created.
* **AgentMail**: the seat's own inbox, resolved the way the workspace broker
  resolves it (authored ``connectors.Email.inbox_address`` or the
  ``<slug>@agentmail.to`` convention), found in the account listing, then
  ``DELETE /v0/inboxes/{id}``. Deleting the inbox revokes its inbox-scoped keys.
  The seat's webhook (an org-level object delivering to
  ``hermes-<slug>.fly.dev``) does not go with the inbox, so it is deleted too.
* **Fly**: ``fly apps destroy hermes-<slug> --yes``, which takes the Machine,
  its volume (``/opt/data``: profile homes, cron stores, the extraction cache)
  and its secrets in one act; then the same for the client's ops workspace and
  its GitHub sweep secrets. ``fly apps list --json`` first, so a re-run on an
  already-destroyed seat is a clean skip, and again after, so the manifest
  reports what is observed gone. Volume snapshots outlive the app until their
  retention ends; ``decommission_inventory.remaining_by_design`` says so.
* **Observability** (``decommission_console``): the healthchecks.io checks and
  every console D1 projection row for the slug, ``customer_configs`` last.
* **Compliance packet** (not a deletion): ``EvidencePacketArchiver`` in
  ``bin/lib/decommission_archiver.py`` builds the signed evidence packet from
  the step-02 snapshot and reports what it read back from disk. Wired from
  ``EVIDENCE_PACKET_SIGNING_KEY_B64`` plus the wrangler token that reads the
  seat's chain pin; unwired, the ``--live`` gate refuses like any other.

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

from adapter.evidence.signing import SIGNING_KEY_ENV, EvidenceSigningError, load_signer
from bin.lib.console_d1 import ConsoleD1
from bin.lib.decommission_archiver import EvidencePacketArchiver
from bin.lib.decommission_console import (
    CONSOLE_SEAT_TABLES,
    HEALTHCHECKS_API,
    HealthchecksAndFleetStatusCleanup,
    healthchecks_check_name,
)
from bin.lib.decommission_inventory import (
    SHARED_CONFIG_BUCKET,
    ops_sweep_github_names,
    seat_skills_buckets,
    seat_vectorize_indexes,
    seat_webhook_host,
    shared_config_prefixes,
)

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
        # Scheme checked above: https:// only, so no file:// or ftp:// URL reaches urlopen.
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
CUSTOMER_CONFIG_BUCKET = SHARED_CONFIG_BUCKET
#: The retention copy. Never in the delete set, whatever prefix it appears under.
ARCHIVE_SEGMENT = "decommission-archive/"


def customer_r2_prefixes(slug: str) -> list[tuple[str, str]]:
    """The seat's key prefixes in SHARED buckets. Its own skills bucket is not
    a prefix: it is deleted whole (:func:`seat_skills_buckets`). The audit
    archive bucket is deliberately absent: it is the retention copy."""
    return shared_config_prefixes(slug)


@dataclass
class CloudflareR2NamespaceDeleter:
    """Deletes a seat's R2 footprint through the Cloudflare R2 API.

    Two shapes, both the inverse of provisioning: the seat's prefixes in the
    shared config bucket are emptied object by object, and the seat's own
    skill-bodies bucket (``ss-operator-<slug>-skills``, provision-customer.sh
    step 5b, plus its pre-#1189 name) is emptied and then deleted as a bucket.

    ``wrangler r2 object`` cannot list, so listing goes through
    ``GET /accounts/{id}/r2/buckets/{bucket}/objects?prefix=`` with the
    cursor the API hands back; deletion is one ``DELETE .../objects/{key}``
    per object (404 counts as already gone). Every prefix is listed again and
    every bucket fetched again after the delete, and the step raises if
    anything but the retention copy is still there.
    """

    account_id: str
    api_token: str
    http: Http = http_request
    prefixes: Callable[[str], list[tuple[str, str]]] = customer_r2_prefixes
    buckets: Callable[[str], list[str]] = seat_skills_buckets

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_token}", "Accept": "application/json"}

    def _bucket_url(self, bucket: str) -> str:
        return f"{CLOUDFLARE_API}/accounts/{self.account_id}/r2/buckets/{urllib.parse.quote(bucket, safe='')}"

    def _bucket_exists(self, bucket: str) -> bool:
        resp = self.http("GET", self._bucket_url(bucket), self._headers(), None)
        if resp.status == 200:
            return True
        if resp.status == 404:
            return False
        raise RuntimeError(f"r2 get bucket {bucket} failed: HTTP {resp.status}")

    def _deletable(self, keys: list[str], prefix: str) -> tuple[list[str], int]:
        keep = [k for k in keys if ARCHIVE_SEGMENT in k[len(prefix) :]]
        return [k for k in keys if k not in keep], len(keep)

    def survey(self, customer_slug: str) -> dict:
        """Read-only: objects (excluding the retention copy) under each shared
        prefix, and each per-seat bucket's existence and object count."""
        prefixes = {
            f"{bucket}/{prefix}": len(self._deletable(self._list(bucket, prefix), prefix)[0])
            for bucket, prefix in self.prefixes(customer_slug)
        }
        buckets: dict[str, dict] = {}
        for bucket in self.buckets(customer_slug):
            exists = self._bucket_exists(bucket)
            buckets[bucket] = {"exists": exists, "objects": len(self._list(bucket, "")) if exists else 0}
        present = any(prefixes.values()) or any(b["exists"] for b in buckets.values())
        return {"present": present, "prefixes": prefixes, "buckets": buckets}

    async def delete_namespace(self, customer_slug: str) -> dict:
        per_bucket: dict[str, dict[str, int]] = {}
        deleted = 0
        retained = 0
        for bucket, prefix in self.prefixes(customer_slug):
            doomed, kept = self._deletable(self._list(bucket, prefix), prefix)
            retained += kept
            for key in doomed:
                if self._delete(bucket, key):
                    deleted += 1
                    per_bucket.setdefault(bucket, {}).setdefault(prefix, 0)
                    per_bucket[bucket][prefix] += 1
            left = self._deletable(self._list(bucket, prefix), prefix)[0]
            if left:
                raise RuntimeError(f"r2 {bucket}/{prefix} still holds {len(left)} object(s) after delete")
        seat_buckets = {name: self._delete_bucket(name) for name in self.buckets(customer_slug)}
        retained += sum(b.get("archive_objects_retained", 0) for b in seat_buckets.values())
        deleted += sum(b.get("objects_deleted", 0) for b in seat_buckets.values())
        buckets_deleted = [n for n, b in seat_buckets.items() if b.get("bucket_deleted")]
        manifest: dict = {
            "objects_deleted": deleted,
            "archive_objects_retained": retained,
            "buckets": per_bucket,
            "seat_buckets": seat_buckets,
            "buckets_deleted": buckets_deleted,
        }
        if deleted == 0 and not buckets_deleted:
            manifest.update({"skipped": True, "reason": "namespace_already_empty"})
        return manifest

    def _delete_bucket(self, bucket: str) -> dict:
        """Empty the seat's own bucket, delete it, fetch it again. A bucket
        holding a retention copy is kept, and says so."""
        if not self._bucket_exists(bucket):
            return {"existed": False, "bucket_deleted": False}
        doomed, kept = self._deletable(self._list(bucket, ""), "")
        removed = sum(1 for key in doomed if self._delete(bucket, key))
        if kept:
            return {
                "existed": True,
                "bucket_deleted": False,
                "objects_deleted": removed,
                "archive_objects_retained": kept,
                "reason": "bucket holds the decommission archive; kept",
            }
        resp = self.http("DELETE", self._bucket_url(bucket), self._headers(), None)
        if resp.status not in (200, 204, 404):
            raise RuntimeError(f"r2 delete bucket {bucket} failed: HTTP {resp.status}")
        if self._bucket_exists(bucket):
            raise RuntimeError(f"r2 bucket {bucket} still exists after delete")
        return {"existed": True, "bucket_deleted": True, "objects_deleted": removed}

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


# ---------------------------------------------------------------------------
# Vectorize
# ---------------------------------------------------------------------------


customer_vectorize_indexes = seat_vectorize_indexes


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

    def survey(self, customer_slug: str) -> dict:
        existing = self._existing()
        found = {name: name in existing for name in customer_vectorize_indexes(customer_slug)}
        return {"present": any(found.values()), "indexes": found}

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
        if deleted and (left := [n for n in wanted if n in self._existing()]):
            raise RuntimeError(f"vectorize index(es) still listed after delete: {left}")
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

    def _address(self, customer_slug: str) -> str:
        return seat_inbox_address(self.customers_root / customer_slug / "customer.yaml", customer_slug)

    def _find_inbox(self, address: str) -> Optional[str]:
        listing = self.http("GET", f"{AGENTMAIL_API}/inboxes", self._headers(), None)
        if listing.status != 200 or not isinstance(listing.body, dict):
            raise RuntimeError(f"agentmail list inboxes failed: HTTP {listing.status}")
        for entry in listing.body.get("inboxes") or []:
            candidate = entry.get("inbox_id") if isinstance(entry, dict) else None
            if isinstance(candidate, str) and candidate.lower() == address:
                return candidate
        return None

    def _seat_webhooks(self, customer_slug: str) -> list[str]:
        """Ids of the org-level webhooks delivering to the seat's Machine."""
        listing = self.http("GET", f"{AGENTMAIL_API}/webhooks", self._headers(), None)
        if listing.status != 200 or not isinstance(listing.body, dict):
            raise RuntimeError(f"agentmail list webhooks failed: HTTP {listing.status}")
        host = seat_webhook_host(customer_slug)
        return [
            str(w["webhook_id"])
            for w in listing.body.get("webhooks") or []
            if isinstance(w, dict)
            and w.get("webhook_id")
            and urllib.parse.urlsplit(str(w.get("url") or "")).hostname == host
        ]

    def _delete(self, kind: str, ident: str) -> bool:
        resp = self.http(
            "DELETE", f"{AGENTMAIL_API}/{kind}/" + urllib.parse.quote(ident, safe=""), self._headers(), None
        )
        if resp.status in (200, 202, 204):
            return True
        if resp.status == 404:
            return False
        raise RuntimeError(f"agentmail delete {kind} {ident} failed: HTTP {resp.status}")

    def survey(self, customer_slug: str) -> dict:
        address = self._address(customer_slug)
        inbox = self._find_inbox(address) is not None
        hooks = self._seat_webhooks(customer_slug)
        return {"present": inbox or bool(hooks), "inbox": address, "inbox_present": inbox, "webhooks": hooks}

    async def deprovision(self, customer_slug: str) -> dict:
        address = self._address(customer_slug)
        inbox_id = self._find_inbox(address)
        removed = bool(inbox_id) and self._delete("inboxes", inbox_id)
        hooks = [h for h in self._seat_webhooks(customer_slug) if self._delete("webhooks", h)]
        # Read back both: a vendor that answered 2xx and kept the object is a
        # failure, not a success.
        if self._find_inbox(address) is not None:
            raise RuntimeError(f"agentmail inbox {address} still listed after delete")
        if left := self._seat_webhooks(customer_slug):
            raise RuntimeError(f"agentmail webhook(s) for {seat_webhook_host(customer_slug)} still listed: {left}")
        manifest: dict = {
            "skipped": not removed and not hooks,
            "identities_removed": int(removed),
            "webhooks_removed": hooks,
        }
        if removed:
            manifest["inbox_id"] = inbox_id
        else:
            manifest.update({"reason": "inbox_already_absent", "inbox": address})
        return manifest


# ---------------------------------------------------------------------------
# Fly
# ---------------------------------------------------------------------------


def fly_app_name(slug: str) -> str:
    return f"hermes-{slug}"


def ops_workspace_app_name(slug: str) -> str:
    """The client's ops workspace (operator/ops-machine/workspaces.json): the Fly
    app that holds that client's documents. Retired with the seat, because its
    volume IS the client's documents."""
    return f"ops-{slug}"


#: Where ``ops-workspace create`` stores the daily sweep's secrets.
OPS_SWEEP_REPO = "venturecrane/ss-console"


@dataclass
class FlyAppDestroyer:
    """``fly apps destroy hermes-<slug> --yes``: Machine, volume and secrets in
    one act, then the same for the client's ops workspace ``ops-<slug>`` when
    one exists (its volume holds the client's documents; leaving it would be a
    removal that is not complete). Lists first so an already-destroyed app is a
    skip, not an error, and lists again after so the manifest reports what is
    observed gone."""

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

    def _destroy(self, app: str) -> None:
        proc = self.runner(["fly", "apps", "destroy", app, "--yes"])
        if proc.returncode != 0:
            raise RuntimeError(f"fly apps destroy {app} failed: {proc.stderr.strip() or proc.stdout.strip()}")
        if app in self._apps():
            raise RuntimeError(f"fly apps destroy {app} reported success but the app is still listed")

    def _gh_names(self, kind: str) -> set[str]:
        """Names (never values: the API returns none) of the repo's Actions
        secrets or variables. A gh that cannot answer raises."""
        proc = self.runner(["gh", kind, "list", "--repo", OPS_SWEEP_REPO, "--json", "name"])
        if proc.returncode != 0:
            raise RuntimeError(f"gh {kind} list failed: {proc.stderr.strip() or proc.stdout.strip()}")
        try:
            rows = json.loads(proc.stdout or "[]")
        except ValueError as exc:
            raise RuntimeError(f"gh {kind} list returned non-JSON") from exc
        return {str(r["name"]) for r in rows if isinstance(r, dict) and "name" in r}

    def _sweep_names_present(self, customer_slug: str) -> dict[str, list[str]]:
        secrets, variables = ops_sweep_github_names(customer_slug)
        have_s, have_v = self._gh_names("secret"), self._gh_names("variable")
        return {"secret": [n for n in secrets if n in have_s], "variable": [n for n in variables if n in have_v]}

    def _remove_sweep_names(self, customer_slug: str) -> list[str]:
        """The ops workspace's daily-sweep secrets and variable on GitHub
        (``ops-workspace create`` sets them; ``ops-workspace destroy`` removes
        them). Removed, then listed again."""
        removed: list[str] = []
        for kind, names in self._sweep_names_present(customer_slug).items():
            for name in names:
                proc = self.runner(["gh", kind, "delete", name, "--repo", OPS_SWEEP_REPO])
                if proc.returncode != 0:
                    raise RuntimeError(f"gh {kind} delete {name} failed: {proc.stderr.strip() or proc.stdout.strip()}")
                removed.append(name)
        if left := [n for names in self._sweep_names_present(customer_slug).values() for n in names]:
            raise RuntimeError(f"ops sweep names still on {OPS_SWEEP_REPO}: {left}")
        return removed

    def preflight(self, customer_slug: str) -> Optional[str]:
        """Step 06 reads GitHub between two Fly destroys; a gh that cannot
        answer must stop the run before either, not between them."""
        try:
            self._sweep_names_present(customer_slug)
        except RuntimeError as exc:
            return f"cannot read the ops sweep secrets on {OPS_SWEEP_REPO}: {exc}"
        return None

    def survey(self, customer_slug: str) -> dict:
        present = self._apps()
        apps = {a: a in present for a in (fly_app_name(customer_slug), ops_workspace_app_name(customer_slug))}
        sweep = self._sweep_names_present(customer_slug)
        return {"present": any(apps.values()) or any(sweep.values()), "apps": apps, "ops_sweep_github": sweep}

    async def destroy_machine(self, customer_slug: str) -> dict:
        app = fly_app_name(customer_slug)
        ws = ops_workspace_app_name(customer_slug)
        present = self._apps()
        workspace: dict = {"app": ws, "existed": ws in present}
        if ws in present:
            self._destroy(ws)
            workspace["destroyed"] = True
        workspace["github_sweep_names_removed"] = self._remove_sweep_names(customer_slug)
        if app not in present:
            return {
                "skipped": True,
                "reason": "app_already_absent",
                "app_destroyed": False,
                "app": app,
                "ops_workspace": workspace,
            }
        self._destroy(app)
        return {"skipped": False, "app_destroyed": True, "app": app, "ops_workspace": workspace}


# ---------------------------------------------------------------------------
# Environment wiring
# ---------------------------------------------------------------------------

#: What each backend needs staged. The CLI prints this table on refusal so the
#: Captain sees exactly which credential is missing, never a bare "unwired".
BACKEND_REQUIREMENTS: dict[str, str] = {
    "r2_deleter": "CLOUDFLARE_API_TOKEN (or CF_API_TOKEN) + CLOUDFLARE_ACCOUNT_ID (or CF_ACCOUNT_ID)",
    "vectorize_deleter": "CLOUDFLARE_API_TOKEN (wrangler reads it)",
    "agentmail": "AGENTMAIL_API_KEY (the org key, not a seat key)",
    "fly": (
        "FLY_API_TOKEN (a staged token; a logged-in `fly` CLI does not count) + an authenticated `gh` "
        f"(removes the ops workspace's sweep secrets on {OPS_SWEEP_REPO})"
    ),
    "compliance_archiver": (
        f"{SIGNING_KEY_ENV} (signs the packet) + CLOUDFLARE_API_TOKEN (wrangler d1 reads the chain pin)"
    ),
    "observability": "HEALTHCHECKS_API_KEY + CLOUDFLARE_API_TOKEN (wrangler d1)",
}


def _console_pin_source(d1: ConsoleD1) -> Callable[[str], Optional[str]]:
    """The newest ``audit_head_history`` head the console recorded for a seat:
    the packet's truncation pin. ``None`` when the seat never heartbeated one,
    which the packet then states on its face."""

    def pin(slug: str) -> Optional[str]:
        row = d1.newest_pin(slug)
        head = row.get("audit_head") if row else None
        return head if isinstance(head, str) and head else None

    return pin


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


def signing_preflight(env: Optional[dict] = None) -> Optional[str]:
    """Why the staged signing key cannot sign a packet, or ``None`` when it can
    (or when no key is staged, which leaves the archiver unwired instead).

    Step 07 (the compliance archive) runs AFTER step 06 destroys the Fly app,
    and the key is loaded only there. A key that does not parse, or a run
    whose interpreter lacks ``cryptography`` (the decommission ran under a uv
    environment holding only pyyaml until 2026-10-06), used to halt the run
    between the two: the Machine gone, no packet, no tombstone, and the seat's
    machine credential never revoked by step 09. Loading the key here, before
    any destructive step, turns that into a refusal with nothing touched."""
    env = dict(os.environ) if env is None else env
    raw = (env.get(SIGNING_KEY_ENV) or "").strip()
    if not raw:
        return None
    try:
        load_signer({SIGNING_KEY_ENV: raw})
    except EvidenceSigningError as exc:
        return str(exc)
    return None


def backends_from_env(
    customer_slug: str,
    customers_root: Path,
    env: Optional[dict] = None,
    *,
    runner: Runner = run_subprocess,
    http: Http = http_request,
    audit_writer: Optional[object] = None,
    actor: str = "captain",
    archive_root: Optional[Path] = None,
) -> tuple[dict, dict[str, bool]]:
    """Construct every backend whose credentials are staged.

    Returns ``(pipeline_kwargs, wired)``: the kwargs to splat into
    ``DecommissionPipeline(...)`` and a per-backend wired/unwired map for the
    CLI's report. A backend with no credentials is simply absent, so the
    pipeline keeps its stub and ``unwired_destructive_backends()`` refuses a
    ``--live`` run exactly as before; nothing here weakens that gate.

    ``audit_writer`` is the decommission trail the compliance archiver writes
    the packet's ``COMPLIANCE_PACKET_EXPORTED`` row to, so the record of the
    decommission also records the packet it produced. ``archive_root`` is
    where step 09 exports the client obligations its ``customer_configs``
    delete cascades.
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
        kwargs["observability"] = HealthchecksAndFleetStatusCleanup(
            hc_key, ConsoleD1(runner=runner), http=http, archive_root=archive_root
        )
        wired["observability"] = True
    if (env.get(SIGNING_KEY_ENV) or "").strip() and wrangler_token:
        kwargs["archiver"] = EvidencePacketArchiver(
            customers_root=customers_root,
            signing_env={SIGNING_KEY_ENV: env[SIGNING_KEY_ENV]},
            audit_writer=audit_writer,
            pin_source=_console_pin_source(ConsoleD1(runner=runner)),
            actor=actor,
        )
        wired["compliance_archiver"] = True
    return kwargs, wired


__all__ = [
    "signing_preflight",
    "ARCHIVE_SEGMENT",
    "AgentMailInboxDeprovisioner",
    "BACKEND_REQUIREMENTS",
    "CONSOLE_SEAT_TABLES",
    "CUSTOMER_CONFIG_BUCKET",
    "CloudflareR2NamespaceDeleter",
    "HEALTHCHECKS_API",
    "OPS_SWEEP_REPO",
    "EvidencePacketArchiver",
    "FlyAppDestroyer",
    "HealthchecksAndFleetStatusCleanup",
    "HttpResponse",
    "WranglerVectorizeIndexDeleter",
    "backends_from_env",
    "customer_r2_prefixes",
    "customer_vectorize_indexes",
    "fly_app_name",
    "ops_workspace_app_name",
    "healthchecks_check_name",
    "seat_inbox_address",
]
