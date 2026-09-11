"""Console D1 access for CI reconcilers, through ``npx wrangler d1 execute``.

Factored VERBATIM out of ``operator/bin/audit-chain-watch.py`` when the
cron-slot watchdog (``operator/bin/reconcile-wakes.py``) became its second
client: one credential shape (CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID) for
every console-side CI job, and no second D1 client to keep in step with the
first. audit-chain-watch re-exports every name, so its tests and callers read
unchanged.

INJECTION POSTURE (see ``sql_text``): ``wrangler d1 execute`` takes ``--command``
and ``--file`` and NOTHING ELSE -- checked against the installed CLI's own
``--help``, not assumed -- so there is no parameter binding on this path and
every value has to be inlined. Values are inlined as hex blob literals, which
carry no escape sequence to break out of.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

#: The alert row's driver prefix (audit-chain-watch's alert sink). Per SEAT, not
#: per entity: several seats can share one entity and the alert PK is
#: (entity_id, alert_date, driver), so a bare 'audit_chain' would let one seat's
#: finding overwrite another's on the same day. It also cannot collide with the
#: healthchecks writer, which uses ''.
ALERT_DRIVER_PREFIX = "audit_chain:"

#: The driver prefix a REHEARSAL writes under (ss#2500, ``--rehearse-mismatch``).
#: Deliberately distinct from :data:`ALERT_DRIVER_PREFIX`, because the alert PK is
#: (entity_id, alert_date, driver) and the insert is an upsert: a rehearsal
#: sharing the real driver would OVERWRITE a genuine finding written for the same
#: seat earlier the same day. Proving the alarm works must not be able to erase
#: the alarm. It also makes the row self-identifying on the dashboard and
#: deletable on its own.
REHEARSAL_DRIVER_PREFIX = "audit_chain:rehearsal:"

DEFAULT_DB = "ss-console-db"

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def _run(cmd: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(list(cmd), capture_output=True, text=True, check=False)


def sql_text(value: Optional[str]) -> str:
    """A TEXT literal that cannot be escaped out of, whatever the value contains.

    `wrangler d1 execute` takes `--command` and `--file` and NOTHING ELSE --
    checked against the installed CLI's own `--help`, not assumed -- so there is
    no parameter binding on this path and every value has to be inlined. Quote
    doubling is the usual answer and it is the wrong one here: part of what gets
    inlined is break text lifted out of a seat's own export, which is exactly
    the untrusted input an injection needs, and a control that could be made to
    rewrite the alerts table by a compromised seat would be worse than no
    control.

    A blob literal has no escape sequences to get wrong: every byte is two hex
    characters and the literal terminates at a fixed length. Cast back to TEXT
    on the way in so the column holds a string, not a blob.
    """
    if value is None:
        return "NULL"
    return f"CAST(x'{value.encode('utf-8').hex()}' AS TEXT)"


def sql_int(value: int) -> str:
    """An INTEGER literal. Typed, so a non-int raises here rather than inlining."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"sql_int refuses a non-integer: {value!r}")
    return str(value)


class ConsoleD1:
    """Read pins/roster and write alert rows through ``npx wrangler d1 execute``.

    Same access path as ci-reconcile-customer-configs.sh, deliberately: one
    credential shape (CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID) for every
    console-side CI job, and no second D1 client to keep in step with the first.
    """

    def __init__(self, db: str = DEFAULT_DB, runner: Runner = _run) -> None:
        self._db = db
        self._run = runner

    def execute(self, sql: str) -> list[dict]:
        proc = self._run(["npx", "wrangler", "d1", "execute", self._db, "--remote", "--json", "--command", sql])
        if proc.returncode != 0:
            raise RuntimeError(f"d1 execute failed: {proc.stderr.strip() or proc.stdout.strip()}")
        return first_result_set(proc.stdout)

    def newest_pin(self, slug: str) -> Optional[dict]:
        sql = (
            "SELECT audit_head, audit_rows, first_seen_heartbeat_ts, last_seen_heartbeat_ts "
            f"FROM audit_head_history WHERE customer_slug = {sql_text(slug)} "
            "ORDER BY id DESC LIMIT 1"
        )
        # See sql_text for why an inlined literal is the safe form here.
        # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query,python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        rows = self.execute(sql)
        return rows[0] if rows else None

    def provisioned_slugs(self) -> list[str]:
        """Every seat the console has a fleet_status row for -- the seats that exist.

        A constant statement: nothing is interpolated, so there is nothing to
        escape. Failure RAISES rather than returning [], because an empty roster
        read as "no seats" would turn an unreachable D1 into a quiet green run.
        """
        rows = self.execute("SELECT customer_slug FROM fleet_status")
        return sorted(str(r["customer_slug"]) for r in rows if isinstance(r.get("customer_slug"), str))

    def fleet_boot_rows(self) -> dict[str, dict]:
        """Per-seat heartbeat + uptime, the reprovision/boot artifact
        (fleet_status, migration 0044). Consumed by the cron-slot watchdog to
        suppress the boot window: boot = last_heartbeat_ts - uptime. A constant
        statement, nothing interpolated. Failure RAISES for the same reason
        provisioned_slugs does."""
        rows = self.execute("SELECT customer_slug, last_heartbeat_ts, process_uptime_seconds FROM fleet_status")
        return {
            str(r["customer_slug"]): {
                "last_heartbeat_ts": r.get("last_heartbeat_ts"),
                "process_uptime_seconds": r.get("process_uptime_seconds"),
            }
            for r in rows
            if isinstance(r.get("customer_slug"), str)
        }

    def count_where_slug(self, table: str, slug: str) -> int:
        """How many rows ``table`` holds for ``slug`` -- the negative probe.

        A DELETE through ``wrangler d1 execute --json`` comes back with an empty
        ``results`` array whether it removed a row or matched nothing, so a
        caller that wants to REPORT a removal has to read the table again. This
        is that read. ``table`` is a code-controlled identifier (refused unless
        it is one); ``slug`` travels as ``sql_text``'s hex literal.

        Failure RAISES rather than returning 0, for the same reason
        ``provisioned_slugs`` does: an unreachable D1 read as "nothing left"
        would turn a failed cleanup into a reported success.
        """
        if not table.isidentifier():
            raise ValueError(f"count_where_slug refuses a non-identifier table: {table!r}")
        # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query — table is a checked identifier; the slug is sql_text's hex blob literal, which has no escape sequence.
        # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query — not SQLAlchemy; the only interpolations are an identifier check and a fixed-alphabet hex literal.
        sql = f"SELECT COUNT(*) AS n FROM {table} WHERE customer_slug = {sql_text(slug)}"
        rows = self.execute(sql)
        if not rows or "n" not in rows[0]:
            raise RuntimeError(f"d1 count on {table} returned no row")
        n = rows[0]["n"]
        if isinstance(n, bool) or not isinstance(n, int):
            raise RuntimeError(f"d1 count on {table} returned a non-integer: {n!r}")
        return n

    def entity_id(self, slug: str) -> Optional[str]:
        # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query — see sql_text: no parameter binding exists on this CLI path; the interpolated text is a hex blob literal.
        # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query — not SQLAlchemy; the only interpolation is sql_text's fixed-alphabet hex literal.
        sql = f"SELECT entity_id FROM customer_configs WHERE customer_slug = {sql_text(slug)}"
        # See sql_text: wrangler d1 execute has no parameter binding (checked
        # against the installed CLI's own --help), and sql_text emits a hex blob
        # literal, which carries no escape sequence to break out of.
        # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query,python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        rows = self.execute(sql)
        return rows[0].get("entity_id") if rows else None

    def write_alert(
        self,
        *,
        entity_id: str,
        slug: str,
        summary: str,
        details: dict,
        driver_prefix: str = ALERT_DRIVER_PREFIX,
    ) -> None:
        """One ``audit_integrity`` row on the shared alert sink.

        Existing snooze / acknowledged columns are left alone: this control
        never undoes a Captain action on a row it wrote yesterday.

        ``driver_prefix`` exists only so a rehearsal can write under
        :data:`REHEARSAL_DRIVER_PREFIX` and therefore cannot upsert over a real
        finding for the same seat on the same day. Callers reporting a genuine
        finding leave it alone.
        """
        values = ", ".join(
            [
                sql_text(entity_id),
                sql_text(slug),
                sql_text(utc_date()),
                sql_text(f"{driver_prefix}{slug}"),
                "'audit_integrity'",
                sql_int(0),
                sql_int(0),
                sql_int(0),
                sql_int(0),
                sql_text(summary),
                sql_text(json.dumps(details, sort_keys=True)),
                "datetime('now')",
            ]
        )
        # Every interpolated value came through sql_text / sql_int above, so the
        # only thing reaching the statement is a hex blob literal or a bare
        # integer. That matters here more than anywhere else in this file: part
        # of `details` is break text lifted out of a seat's own export, which is
        # precisely the untrusted input an injection needs.
        # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query,python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        self.execute(
            "INSERT INTO cost_anomaly_alerts ("
            "entity_id, customer_slug, alert_date, driver, source, "
            "daily_cents, rolling_avg_cents, ratio_bps, threshold_bps, "
            "summary, details_json, detected_at"
            f") VALUES ({values}) "
            "ON CONFLICT(entity_id, alert_date, driver) DO UPDATE SET "
            "summary = excluded.summary, details_json = excluded.details_json, "
            "detected_at = excluded.detected_at"
        )

    def clear_rehearsal_alerts(self, *, slug: str) -> None:
        """Remove every rehearsal row for one seat, whatever day it was written.

        A rehearsal that leaves its own alarm standing has replaced one problem
        with another: the next person to read the dashboard cannot tell the drill
        from the fire. The WHERE clause is pinned to
        :data:`REHEARSAL_DRIVER_PREFIX` so this can never delete a real finding,
        and it is an equality match on the full driver string rather than a LIKE
        so no pattern character in a slug can widen it.
        """
        # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query,python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        self.execute(f"DELETE FROM cost_anomaly_alerts WHERE driver = {sql_text(f'{REHEARSAL_DRIVER_PREFIX}{slug}')}")


def first_result_set(stdout: str) -> list[dict]:
    """Pull the rows out of wrangler's --json envelope.

    Parsed, never assumed: wrangler prints a list of result objects for a
    multi-statement command and has also printed a bare object, so both are
    handled and anything else RAISES. Returning [] on an unrecognized envelope
    would read as "no pin recorded", which is the one wrong answer that turns
    into a HOLD nobody investigates.
    """
    payload = json.loads(stdout)
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    if not isinstance(payload, dict):
        raise RuntimeError("d1 execute returned an unrecognized envelope")
    results = payload.get("results")
    if results is None:
        return []
    if not isinstance(results, list):
        raise RuntimeError("d1 execute returned a non-list results field")
    return [r for r in results if isinstance(r, dict)]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_date() -> str:
    return utc_now().strftime("%Y-%m-%d")


__all__ = [
    "ALERT_DRIVER_PREFIX",
    "REHEARSAL_DRIVER_PREFIX",
    "ConsoleD1",
    "DEFAULT_DB",
    "Runner",
    "first_result_set",
    "sql_int",
    "sql_text",
    "utc_date",
    "utc_now",
]
