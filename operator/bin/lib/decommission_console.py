"""Step 09: the seat's console footprint (healthchecks.io + ss-console-db).

Split out of ``decommission_backends.py`` when the D1 side grew from three
tables to the full seat projection (``decommission_inventory.
SEAT_PROJECTION_TABLES``). The class keeps its name so the CLI wiring and
the tests read unchanged.

Two things make this more than a list of DELETEs:

* ``customer_configs`` is the FK parent of ``client_obligations`` with ON
  DELETE CASCADE (migrations/0117), so removing the seat row erases the
  register of what SMD owes that client. The register is never lost silently:
  :meth:`preflight` refuses a live run while any obligation is still owed
  (the CLI calls it before any destructive step, and :meth:`cleanup` calls it
  again first), and :meth:`cleanup` exports every obligation row for the slug
  to the archive dir before the delete and re-counts afterwards to report
  what the cascade took.
* Every number in the manifest is OBSERVED. A DELETE through ``wrangler d1
  execute`` returns an empty result set whether it removed a row or matched
  nothing, so each table is counted before and after, and the step raises if
  a projection row is still there when it finishes (Law 14: the backend is
  re-read and absence asserted, not assumed). A missing table RAISES: every
  migration is applied to ss-console-db, so "no such table" means the wrong
  database, and reading it as "no row" would report a cleanup that never ran.
"""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from bin.lib.console_d1 import ConsoleD1, sql_text
from bin.lib.decommission_inventory import (
    OBLIGATIONS_TABLE,
    RETAINED_SEAT_TABLES,
    SEAT_PROJECTION_TABLES,
    TERMINAL_OBLIGATION_STATES,
)

HEALTHCHECKS_API = "https://healthchecks.io/api/v3"

#: Back-compat name: the tables step 09 empties for the slug.
CONSOLE_SEAT_TABLES: tuple[str, ...] = SEAT_PROJECTION_TABLES

#: Manifest key prefixes the CLI report and older tests read.
_SHORT = {"operator_runtime_summary": "runtime_summary"}

_OPEN_STATES_SQL = " AND state NOT IN (" + ", ".join(f"'{s}'" for s in TERMINAL_OBLIGATION_STATES) + ")"


def healthchecks_check_name(slug: str) -> str:
    return f"hermes-{slug}"


def _count(d1: ConsoleD1, table: str, slug: str, column: str = "customer_slug", where: str = "") -> int:
    if not (table.isidentifier() and column.isidentifier()):
        raise ValueError(f"refusing a non-identifier table or column: {table!r}.{column!r}")
    # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query,python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
    sql = f"SELECT COUNT(*) AS n FROM {table} WHERE {column} = {sql_text(slug)}{where}"  # noqa: S608 - identifiers checked above; the slug is sql_text's hex blob literal; `where` is a module constant
    rows = d1.execute(sql)
    n = rows[0].get("n") if rows else None
    if isinstance(n, bool) or not isinstance(n, int):
        raise RuntimeError(f"d1 count on {table} returned {n!r}")
    return n


@dataclass
class HealthchecksAndFleetStatusCleanup:
    """Deletes the seat's healthchecks.io checks and its console projection rows."""

    hc_api_key: Optional[str]
    d1: ConsoleD1
    http: Any = None  # decommission_backends.Http; defaulted below to avoid an import cycle
    archive_root: Optional[Path] = None

    def __post_init__(self) -> None:
        if self.http is None:
            from bin.lib.decommission_backends import http_request

            self.http = http_request

    # --- healthchecks.io ---------------------------------------------------

    def _headers(self) -> dict:
        return {"X-Api-Key": self.hc_api_key or "", "Accept": "application/json"}

    def _find_check(self, slug: str, tag: str, name: str) -> Optional[str]:
        """The uuid of the seat's check under ``tag``, or None when absent."""
        if not self.hc_api_key:
            return None
        url = f"{HEALTHCHECKS_API}/checks/?tag={urllib.parse.quote(tag, safe='')}&tag=" + urllib.parse.quote(
            slug, safe=""
        )
        listing = self.http("GET", url, self._headers(), None)
        if listing.status != 200 or not isinstance(listing.body, dict):
            raise RuntimeError(f"healthchecks list failed: HTTP {listing.status}")
        for check in listing.body.get("checks") or []:
            if isinstance(check, dict) and check.get("name") == name:
                uuid = check.get("uuid")
                if not isinstance(uuid, str) or not uuid:
                    raise RuntimeError("healthchecks check has no uuid (read-only key?)")
                return uuid
        return None

    def _delete_check(
        self, slug: str, *, tag: str = "operator", name: Optional[str] = None
    ) -> tuple[bool, Optional[str]]:
        wanted = name or healthchecks_check_name(slug)
        uuid = self._find_check(slug, tag, wanted)
        if uuid is None:
            return False, None
        resp = self.http("DELETE", f"{HEALTHCHECKS_API}/checks/{uuid}", self._headers(), None)
        if resp.status not in (200, 204, 404):
            raise RuntimeError(f"healthchecks delete {uuid} failed: HTTP {resp.status}")
        if self._find_check(slug, tag, wanted) is not None:
            raise RuntimeError(f"healthchecks check {uuid} still listed after delete")
        return resp.status != 404, uuid

    # --- console D1 ----------------------------------------------------------

    def _open_obligations(self, slug: str) -> int:
        return _count(self.d1, OBLIGATIONS_TABLE, slug, where=_OPEN_STATES_SQL)

    def preflight(self, customer_slug: str) -> Optional[str]:
        """Why a live run must not start, or None. Deleting ``customer_configs``
        cascades the obligation register, so a seat SMD still owes something
        is not retired."""
        open_n = self._open_obligations(customer_slug)
        if open_n:
            return (
                f"{open_n} client obligation(s) for {customer_slug} are not verified, closed or cancelled "
                f"(.claude/bin/register list --client {customer_slug}). Deleting the seat row would cascade "
                "them out of the register; deliver or cancel them first"
            )
        return None

    def _export_obligations(self, slug: str) -> dict:
        # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query,python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        rows = self.d1.execute(f"SELECT * FROM {OBLIGATIONS_TABLE} WHERE customer_slug = {sql_text(slug)}")  # noqa: S608 - the table is a module constant; the slug is sql_text's hex blob literal
        if not rows:
            return {"rows_exported": 0, "export_path": None}
        if self.archive_root is None:
            raise RuntimeError("client obligations exist but no archive dir is wired to export them to")
        out_dir = self.archive_root / slug
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"client-obligations-{datetime.now(timezone.utc):%Y-%m-%d}.json"
        path.write_text(json.dumps(rows, sort_keys=True, indent=2, default=str), encoding="utf-8")
        if len(json.loads(path.read_text(encoding="utf-8"))) != len(rows):
            raise RuntimeError(f"obligations export at {path} did not read back")
        return {"rows_exported": len(rows), "export_path": str(path)}

    def _clear_table(self, table: str, slug: str) -> dict:
        before = _count(self.d1, table, slug)
        # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query,python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        self.d1.execute(f"DELETE FROM {table} WHERE customer_slug = {sql_text(slug)}")  # noqa: S608 - table is a SEAT_PROJECTION_TABLES constant; the slug is sql_text's hex blob literal
        after = _count(self.d1, table, slug)
        return {"rows_before": before, "rows_remaining": after, "rows_deleted": before - after}

    # --- survey and the step ---------------------------------------------------

    def survey(self, customer_slug: str) -> dict:
        """Read-only: what of the seat's console footprint exists now. The dry
        run prints it; the CLI re-runs it after a live run as the negative
        probe. Retained tables are counted too, so the report says how much
        history is kept, not just that some is."""
        checks = {
            "operator": self._find_check(customer_slug, "operator", healthchecks_check_name(customer_slug)),
            "ops-workspace": self._find_check(customer_slug, "ops-workspace", f"ops-{customer_slug}"),
        }
        projection = {t: _count(self.d1, t, customer_slug) for t in SEAT_PROJECTION_TABLES}
        retained = {t: _count(self.d1, t, customer_slug, col) for t, (col, _) in RETAINED_SEAT_TABLES.items()}
        return {
            "present": any(v is not None for v in checks.values()) or any(projection.values()),
            "healthchecks_configured": bool(self.hc_api_key),
            "healthchecks_checks": {k: v is not None for k, v in checks.items()},
            "projection_rows": projection,
            "client_obligations": {
                "total": _count(self.d1, OBLIGATIONS_TABLE, customer_slug),
                "open": self._open_obligations(customer_slug),
            },
            "retained_rows": retained,
        }

    async def cleanup(self, customer_slug: str) -> dict:
        # Re-checked here as well as in the CLI preflight: an obligation
        # recorded while steps 01 to 08 ran must still stop the cascade.
        problem = self.preflight(customer_slug)
        if problem:
            raise RuntimeError(problem)
        cancelled, uuid = self._delete_check(customer_slug)
        ws_cancelled, _ = self._delete_check(customer_slug, tag="ops-workspace", name=f"ops-{customer_slug}")
        exported = self._export_obligations(customer_slug)
        observed = {table: self._clear_table(table, customer_slug) for table in SEAT_PROJECTION_TABLES}
        # The final read-back: cascades and a racing writer (a heartbeat
        # landing between two DELETEs) are caught here, not assumed away.
        remaining = {t: n for t in SEAT_PROJECTION_TABLES if (n := _count(self.d1, t, customer_slug))}
        if remaining:
            raise RuntimeError(f"console rows still present for {customer_slug} after delete: {remaining}")
        manifest: dict = {
            "healthchecks_check_cancelled": cancelled,
            "healthchecks_check_uuid": uuid,
            "healthchecks_configured": bool(self.hc_api_key),
            "ops_workspace_check_cancelled": ws_cancelled,
            "customer_slug": customer_slug,
            "tables": observed,
            "client_obligations_exported": exported,
            "client_obligations_remaining": _count(self.d1, OBLIGATIONS_TABLE, customer_slug),
        }
        for table, obs in observed.items():
            key = _SHORT.get(table, table)
            manifest[f"{key}_rows_deleted"] = obs["rows_deleted"]
            manifest[f"{key}_rows_remaining"] = obs["rows_remaining"]
            manifest[f"{key}_row_deleted"] = obs["rows_remaining"] == 0
        return manifest


__all__ = ["CONSOLE_SEAT_TABLES", "HEALTHCHECKS_API", "HealthchecksAndFleetStatusCleanup", "healthchecks_check_name"]
