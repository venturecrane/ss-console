"""Pull-before-destroy: preserve Machine-local data through the ADR-0043 seam.

Issue #1355. The live Operator data plane is Machine-local sqlite (the
broker-owned audit ledger, the ADR-0016 ``persona_observations`` mirror, the
``agent_skills_inventory`` table). ``fly apps destroy`` is the designed
destruction mechanism for all of it — so anything the venture promised to
KEEP (the audit-retention carve-out, the evidence packet's memory snapshot)
must be pulled off the Machine BEFORE step ``06_fly_machine`` runs.

This module is the console-side half. The Machine-side half is overlay #67:
two authenticated read-only seam kinds —

* ``GET /runtime/audit_export``  — full 12-column ``audit_log`` rows,
  ascending-ULID keyset pagination (resume-safe).
* ``GET /runtime/memory_export?table=<t>`` — the ADR-0016 tables, rowid
  keyset pagination.

Auth matches the provisioning derivation exactly: the bearer is
``HMAC-SHA256(OPERATOR_RUNTIME_READ_SECRET, slug)`` hex — the same string
``provision-customer.sh`` stages on the Machine as
``OPERATOR_RUNTIME_READ_KEY``.

Outputs land in the per-customer archive dir:

* ``audit-log-{date}.csv`` — the compliance CSV (canonical column order).
* ``machine-snapshot-{date}.sqlite`` — ``audit_log`` + memory tables as real
  sqlite tables. This file doubles as the evidence generator's ``--read-db``
  input, which was always specified as "the per-customer audit-export
  snapshot" — the seam pull is what finally makes that input real. The
  snapshot's ``audit_log`` carries the hash-chain link columns as well as the
  12 compliance columns (ss#2500), because a snapshot without them cannot
  answer any question about the chain, including whether a pinned head is
  still in it.
* ``audit-log-manifest-{date}.json`` / key counts in the step manifest.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("aie.bin.seam_pull")

# Canonical audit_log column order (docs/specs/operator/d1-schema.md §1; the
# overlay serves exactly these keys from audit_export).
AUDIT_COLUMNS: tuple[str, ...] = (
    "id",
    "ts",
    "action_type",
    "actor",
    "actor_role",
    "skill_name",
    "matter_ref",
    "input_digest",
    "output_digest",
    "diff_digest",
    "trust_ceiling",
    "metadata",
)

# ADR-0016 Machine-local memory tables served by memory_export (mirror of the
# overlay's MEMORY_EXPORT_TABLES allow-list).
MEMORY_EXPORT_TABLES: tuple[str, ...] = (
    "persona_observations",
    "persona_observations_archive",
    "agent_skills_inventory",
    "peer_preferences",
)

# The hash-chain link columns (#1686). NOT part of AUDIT_COLUMNS, which is the
# frozen compliance-CSV column order, and preserved in the sqlite snapshot
# anyway (ss#2500): the snapshot is what the evidence generator reads as
# --read-db, and without these the packet cannot check a pinned head against the
# ledger at all. Written as NULL when the Machine's overlay does not serve them,
# so an older seat degrades to "unchecked" rather than to a crash.
CHAIN_LINK_COLUMNS: tuple[str, ...] = ("prev_hash", "row_hash")

_PAGE_LIMIT = 200  # overlay MAX_LIMIT


def derive_runtime_read_key(master: str, slug: str) -> str:
    """Derive the per-customer seam bearer — MUST match provision-customer.sh
    (``openssl dgst -sha256 -hmac "$SECRET"`` over the bare slug) and the
    console's TS ``deriveRuntimeReadKey``."""
    return hmac.new(master.encode("utf-8"), slug.encode("utf-8"), hashlib.sha256).hexdigest()


class SeamClient:
    """Minimal authenticated reader for one Machine's runtime-read seam."""

    def __init__(self, *, base_url: str, slug: str, key: str, timeout_seconds: float = 30.0):
        # Scheme is enforced HERE, fail-closed: urllib follows file:// and
        # ftp:// schemes, so a poisoned OPERATOR_RUNTIME_READ_URL must die at
        # construction, never reach urlopen.
        if not base_url.startswith("https://"):
            raise ValueError("seam base_url must be https:// (got a non-https scheme)")
        self._base = base_url.rstrip("/")
        self._slug = slug
        self._key = key
        self._timeout = timeout_seconds

    def read_page(self, kind: str, *, cursor: Optional[str] = None, table: Optional[str] = None) -> dict:
        params: dict[str, str] = {"limit": str(_PAGE_LIMIT)}
        if cursor:
            params["cursor"] = cursor
        if table:
            params["table"] = table
        url = f"{self._base}/runtime/{kind}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._key}",
                "X-Tenant-Slug": self._slug,
            },
            method="GET",
        )
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected — scheme is constructor-enforced https:// (file:// and ftp:// raise at SeamClient init); the host comes from operator env staged by Infisical, and every path segment is a module constant.
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310 — https enforced at construction
            payload = json.loads(resp.read().decode("utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
            raise ValueError(f"seam read {kind}: malformed page shape")
        return payload

    def read_config(self) -> dict:
        """GET /runtime/config — the materialized-state snapshot.

        Unlike the paginated kinds this returns a single dict (no ``entries``
        envelope), so it has its own reader. Used by the overlay-ref drift
        check to read each Machine's running ``overlay_ref.value``.
        """
        url = f"{self._base}/runtime/config"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._key}",
                "X-Tenant-Slug": self._slug,
            },
            method="GET",
        )
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected — scheme is constructor-enforced https://; host from operator env; path is a module constant.
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310 — https enforced at construction
            payload = json.loads(resp.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("seam read config: malformed snapshot shape")
        return payload

    def read_all(self, kind: str, *, table: Optional[str] = None) -> list[dict]:
        """Drain every page of one kind/table. Raises on any transport or
        shape error — preservation must fail LOUD, never archive a partial
        pull as if complete."""
        entries: list[dict] = []
        cursor: Optional[str] = None
        while True:
            page = self.read_page(kind, cursor=cursor, table=table)
            entries.extend(e for e in page["entries"] if isinstance(e, dict))
            cursor = page.get("cursor")
            if not cursor:
                return entries


def seam_client_from_env(slug: str) -> Optional[SeamClient]:
    """Construct a SeamClient from operator env, or None when unconfigured.

    ``OPERATOR_RUNTIME_READ_SECRET`` — the master (console-side only).
    ``OPERATOR_RUNTIME_READ_URL`` — Machine base URL; ``{app}`` expands to
    ``hermes-{slug}`` (the Fly app naming convention).
    """
    master = os.environ.get("OPERATOR_RUNTIME_READ_SECRET")
    url_template = os.environ.get("OPERATOR_RUNTIME_READ_URL")
    if not master or not url_template:
        return None
    base_url = url_template.replace("{app}", f"hermes-{slug}")
    return SeamClient(base_url=base_url, slug=slug, key=derive_runtime_read_key(master, slug))


def _snapshot_conn(snapshot_path: Path) -> sqlite3.Connection:
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(snapshot_path))


def _write_audit_snapshot(conn: sqlite3.Connection, rows: list[dict]) -> None:
    snapshot_columns = AUDIT_COLUMNS + CHAIN_LINK_COLUMNS
    cols = ", ".join(snapshot_columns)
    placeholders = ", ".join("?" for _ in snapshot_columns)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS audit_log ("
        "id TEXT PRIMARY KEY, ts TEXT NOT NULL, action_type TEXT NOT NULL, "
        "actor TEXT NOT NULL, actor_role TEXT, skill_name TEXT, matter_ref TEXT, "
        "input_digest TEXT, output_digest TEXT, diff_digest TEXT, "
        "trust_ceiling TEXT, metadata TEXT, prev_hash TEXT, row_hash TEXT)"
    )
    conn.executemany(
        f"INSERT OR REPLACE INTO audit_log ({cols}) VALUES ({placeholders})",
        [tuple(row.get(c) for c in snapshot_columns) for row in rows],
    )
    conn.commit()


def _write_memory_snapshot(conn: sqlite3.Connection, table: str, rows: list[dict]) -> None:
    """Materialize one memory_export table generically (schema follows the
    served keys; ``_rowid`` pagination key is dropped)."""
    if not rows:
        return
    keys = [k for k in rows[0].keys() if k != "_rowid"]
    # The column names are served by the seat and interpolated into DDL. A
    # quoted identifier is safe against everything except a quote in the name,
    # and a name that is not a Python identifier is not a column this schema
    # ever served (2026-09-09 review, Security LOW 6). Refuse rather than quote
    # around it: a hostile seat gets a clear error, not a table it named.
    bad = [k for k in keys if not k.isidentifier()]
    if bad:
        raise ValueError(f"memory export {table!r} served non-identifier column names: {bad!r}")
    col_defs = ", ".join(f'"{k}"' for k in keys)
    placeholders = ", ".join("?" for _ in keys)
    conn.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({col_defs})')  # noqa: S608 - same statement shape as the line above: table from MEMORY_EXPORT_TABLES, values bound — table from MEMORY_EXPORT_TABLES, columns isidentifier-checked above
    conn.executemany(
        f'INSERT INTO "{table}" ({col_defs}) VALUES ({placeholders})',  # noqa: S608 - same statement shape as the line above: table from MEMORY_EXPORT_TABLES, values bound — same table and column set as the CREATE above; values are bound
        [tuple(row.get(k) for k in keys) for row in rows],
    )
    conn.commit()


class SeamAuditLogPreserver:
    """Real :class:`bin.lib.decommission.AuditLogPreserver`: pulls the LIVE
    Machine-local audit ledger + ADR-0016 memory tables through the seam and
    writes the compliance CSV + the sqlite snapshot the evidence generator
    consumes as ``--read-db``.

    Idempotent per UTC date (same contract as the stub it replaces). Any
    transport failure raises — the decommission pipeline halts BEFORE the
    destructive steps, which is the entire point of pull-before-destroy.
    """

    def __init__(self, client: SeamClient) -> None:
        self._client = client

    async def preserve(self, customer_slug: str, archive_dir: Path, audit_log_days: int) -> dict:
        archive_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        date_part = now.strftime("%Y-%m-%d")
        manifest_path = archive_dir / f"audit-log-manifest-{date_part}.json"
        csv_path = archive_dir / f"audit-log-{date_part}.csv"
        snapshot_path = archive_dir / f"machine-snapshot-{date_part}.sqlite"
        preserve_until = (now + timedelta(days=audit_log_days)).isoformat()

        if manifest_path.exists():
            return {
                "skipped": True,
                "reason": "audit_log_already_preserved_today",
                "audit_log_days": audit_log_days,
                "preserve_until": preserve_until,
                "archive_path": str(manifest_path),
                "rows_preserved": 0,
                "stub": False,
            }

        audit_rows = self._client.read_all("audit_export")

        with csv_path.open("w", encoding="utf-8", newline="") as fp:
            writer = csv.writer(fp)
            writer.writerow(AUDIT_COLUMNS)
            for row in audit_rows:
                writer.writerow([row.get(c) for c in AUDIT_COLUMNS])

        memory_counts: dict[str, int] = {}
        conn = _snapshot_conn(snapshot_path)
        try:
            _write_audit_snapshot(conn, audit_rows)
            for table in MEMORY_EXPORT_TABLES:
                try:
                    rows = self._client.read_all("memory_export", table=table)
                except urllib.error.HTTPError as exc:
                    # A 400 means this Machine's overlay predates the table
                    # (gradual OVERLAY_REF rollout) — it legitimately has no
                    # such table, so it is zero rows, NOT a partial-pull halt.
                    # Any other status (500, auth, transport) still fails loud.
                    if exc.code != 400:
                        raise
                    logging.getLogger(__name__).warning(
                        "preserve: memory table %s not served by this Machine "
                        "(HTTP 400, overlay predates it); recording 0 rows",
                        table,
                    )
                    rows = []
                _write_memory_snapshot(conn, table, rows)
                memory_counts[table] = len(rows)
        finally:
            conn.close()

        manifest = {
            "customer_slug": customer_slug,
            "exported_at": now.isoformat(),
            "preserve_until": preserve_until,
            "audit_log_days": audit_log_days,
            "csv_path": str(csv_path),
            "snapshot_path": str(snapshot_path),
            "rows_preserved": len(audit_rows),
            "memory_rows_preserved": memory_counts,
            "source": "adr-0043 runtime-read seam (audit_export + memory_export)",
            "stub": False,
        }
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8")
        log.info(
            "seam preserve: customer=%s audit_rows=%d memory=%s",
            customer_slug,
            len(audit_rows),
            memory_counts,
        )
        return {
            "skipped": False,
            "audit_log_days": audit_log_days,
            "preserve_until": preserve_until,
            "archive_path": str(manifest_path),
            "csv_path": str(csv_path),
            "snapshot_path": str(snapshot_path),
            "rows_preserved": len(audit_rows),
            "memory_rows_preserved": memory_counts,
            "stub": False,
        }


__all__ = [
    "AUDIT_COLUMNS",
    "CHAIN_LINK_COLUMNS",
    "MEMORY_EXPORT_TABLES",
    "SeamAuditLogPreserver",
    "SeamClient",
    "derive_runtime_read_key",
    "seam_client_from_env",
]
