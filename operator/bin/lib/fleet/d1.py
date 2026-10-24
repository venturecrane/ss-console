"""D1 over wrangler: the fleet tables (migration 0129) and fleet_status.

Same access path provision-customer.sh step 6d uses: ``npx wrangler d1 execute
ss-console-db --remote --json --command <sql>``, run from the repo root (the
binding lives in wrangler.toml). There are no bound parameters on this path, so
every statement is built from values that passed an ``ids`` validator or went
through ``ids.sql_str``. A wrangler failure is a :class:`ToolingError`, never an
empty result: "could not look" must not read as "nothing there".
"""

from __future__ import annotations

import json
from typing import Any

from . import ids
from .runner import Runner, ToolingError, require_ok

DB = "ss-console-db"
LEASE_NAME = "rollout"
LEASE_TTL = "+2 hours"
NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%SZ','now')"

ROLLOUT_STATES = frozenset(
    {
        "pending",
        "waiting_idle",
        "deploying",
        "smoking",
        "done",
        "rolled_back",
        "failed",
        "halted",
        "skipped_held",
        "tooling_error",
    }
)


class D1:
    """Run SQL against the console database and return result rows."""

    def __init__(self, runner: Runner, repo_root: str) -> None:
        self.runner = runner
        self.repo_root = repo_root

    def query(self, sql: str) -> list[dict[str, Any]]:
        res = self.runner.run(
            ["npx", "--quiet", "wrangler", "d1", "execute", DB, "--remote", "--json", "--command", sql],
            timeout=120,
            cwd=self.repo_root,
        )
        require_ok(res, "wrangler d1 execute")
        try:
            payload = json.loads(res.stdout)
        except json.JSONDecodeError as exc:
            raise ToolingError(f"wrangler returned non-JSON output: {res.stdout[:200]!r}") from exc
        rows: list[dict[str, Any]] = []
        for block in payload if isinstance(payload, list) else [payload]:
            if isinstance(block, dict) and block.get("success") is False:
                raise ToolingError(f"D1 statement failed: {block.get('error') or block}")
            rows.extend(r for r in (block.get("results") or []) if isinstance(r, dict))
        return rows


class FleetStore:
    """The fleet's records. One method per question the CLI asks."""

    def __init__(self, d1: D1) -> None:
        self.d1 = d1

    # -- releases ---------------------------------------------------------
    def insert_release(self, rec: dict[str, Any]) -> None:
        cols = (
            "release_id", "kind", "image_ref", "image_digest", "skills_version", "ss_console_sha",
            "overlay_ref", "volume_forward_compatible", "declared_secrets", "notes",
        )  # fmt: skip
        ids.release_id(rec["release_id"])
        values = [
            ids.sql_str(rec.get(c)) if c != "volume_forward_compatible" else ids.sql_int(rec.get(c, 1)) for c in cols
        ]
        self.d1.query(
            f"INSERT INTO fleet_releases ({', '.join(cols)}, created_at) VALUES ({', '.join(values)}, {NOW_SQL})"  # noqa: S608 - values are ids.sql_str hex literals, ids-validated identifiers or constants
        )

    def release(self, release_id: str) -> dict[str, Any] | None:
        rows = self.d1.query(
            f"SELECT * FROM fleet_releases WHERE release_id = {ids.sql_str(ids.release_id(release_id))}"  # noqa: S608 - values are ids.sql_str hex literals, ids-validated identifiers or constants
        )
        return rows[0] if rows else None

    def skills_release(self, version: str) -> dict[str, Any] | None:
        rows = self.d1.query(
            "SELECT * FROM fleet_releases WHERE kind = 'skills' AND skills_version = "  # noqa: S608 - values are ids.sql_str hex literals, ids-validated identifiers or constants
            f"{ids.sql_str(ids.skills_version(version))} ORDER BY created_at DESC LIMIT 1"
        )
        return rows[0] if rows else None

    def release_for_image(self, image_ref: str) -> dict[str, Any] | None:
        """The image release a seat's reported FLY_IMAGE_REF names (by digest or by ref)."""
        digest = ids.digest_in_ref(image_ref)
        if digest:
            where = f"image_digest = {ids.sql_str(digest)}"
        else:
            where = f"image_ref = {ids.sql_str(image_ref.split('@', 1)[0])}"
        rows = self.d1.query(
            f"SELECT * FROM fleet_releases WHERE kind = 'image' AND {where} ORDER BY created_at DESC LIMIT 1"  # noqa: S608 - values are ids.sql_str hex literals, ids-validated identifiers or constants
        )
        return rows[0] if rows else None

    def latest_completed(self, kind: str) -> dict[str, Any] | None:
        if kind not in ("image", "skills"):
            raise ValueError(kind)
        rows = self.d1.query(
            f"SELECT * FROM fleet_releases WHERE kind = '{kind}' AND completed_last_ring_at IS NOT NULL "  # noqa: S608 - values are ids.sql_str hex literals, ids-validated identifiers or constants
            "ORDER BY completed_last_ring_at DESC LIMIT 1"
        )
        return rows[0] if rows else None

    def recent_releases(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.d1.query(f"SELECT * FROM fleet_releases ORDER BY created_at DESC LIMIT {ids.sql_int(limit)}")  # noqa: S608 - values are ids.sql_str hex literals, ids-validated identifiers or constants

    def mark_completed(self, release_id: str) -> None:
        self.d1.query(
            f"UPDATE fleet_releases SET completed_last_ring_at = {NOW_SQL} WHERE release_id = {ids.sql_str(ids.release_id(release_id))}"  # noqa: S608 - values are ids.sql_str hex literals, ids-validated identifiers or constants
        )

    # -- holds --------------------------------------------------------------
    def holds(self) -> dict[str, dict[str, Any]]:
        return {r["customer_slug"]: r for r in self.d1.query("SELECT * FROM fleet_holds")}

    def hold(self, seat: str, reason: str, held_by: str) -> None:
        self.d1.query(
            "INSERT INTO fleet_holds (customer_slug, reason, held_by, held_at) VALUES "  # noqa: S608 - values are ids.sql_str hex literals, ids-validated identifiers or constants
            f"({ids.sql_str(ids.slug(seat))}, {ids.sql_str(reason)}, {ids.sql_str(held_by)}, {NOW_SQL}) "
            "ON CONFLICT(customer_slug) DO UPDATE SET reason = excluded.reason, held_by = excluded.held_by, held_at = excluded.held_at"
        )

    def unhold(self, seat: str) -> None:
        self.d1.query(f"DELETE FROM fleet_holds WHERE customer_slug = {ids.sql_str(ids.slug(seat))}")  # noqa: S608 - values are ids.sql_str hex literals, ids-validated identifiers or constants

    # -- status ---------------------------------------------------------------
    def fleet_status(self) -> dict[str, dict[str, Any]]:
        return {r["customer_slug"]: r for r in self.d1.query("SELECT * FROM fleet_status")}

    def seat_status(self, seat: str) -> dict[str, Any] | None:
        rows = self.d1.query(f"SELECT * FROM fleet_status WHERE customer_slug = {ids.sql_str(ids.slug(seat))}")  # noqa: S608 - values are ids.sql_str hex literals, ids-validated identifiers or constants
        return rows[0] if rows else None

    def last_rollouts(self) -> dict[str, dict[str, Any]]:
        rows = self.d1.query(
            "SELECT r.* FROM fleet_rollouts r JOIN (SELECT customer_slug, MAX(started_at) AS m FROM fleet_rollouts "
            "GROUP BY customer_slug) x ON r.customer_slug = x.customer_slug AND r.started_at = x.m"
        )
        return {r["customer_slug"]: r for r in rows}

    # -- rollouts -------------------------------------------------------------
    def start_rollout(self, rec: dict[str, Any]) -> None:
        self.d1.query(
            "INSERT INTO fleet_rollouts (rollout_id, release_id, customer_slug, ring, state, to_image, "  # noqa: S608 - values are ids.sql_str hex literals, ids-validated identifiers or constants
            "from_skills_version, to_skills_version, started_at) VALUES ("
            f"{ids.sql_str(rec['rollout_id'])}, {ids.sql_str(ids.release_id(rec['release_id']))}, "
            f"{ids.sql_str(ids.slug(rec['customer_slug']))}, {ids.sql_int(rec['ring'])}, 'pending', "
            f"{ids.sql_str(rec.get('to_image'))}, {ids.sql_str(rec.get('from_skills_version'))}, "
            f"{ids.sql_str(rec.get('to_skills_version'))}, {NOW_SQL})"
        )

    def update_rollout(self, rollout_id: str, state: str, **fields: Any) -> None:
        if state not in ROLLOUT_STATES:
            raise ValueError(f"unknown rollout state {state!r}")
        allowed = {"from_image", "from_toml", "snapshot_id", "smoke_class", "detail", "from_skills_version"}
        sets = [f"state = {ids.sql_str(state)}"]
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"not a rollout column: {key}")
            sets.append(f"{key} = {ids.sql_str(value)}")
        if state in ("done", "rolled_back", "failed", "halted", "skipped_held", "tooling_error", "waiting_idle"):
            sets.append(f"finished_at = {NOW_SQL}")
        self.d1.query(f"UPDATE fleet_rollouts SET {', '.join(sets)} WHERE rollout_id = {ids.sql_str(rollout_id)}")  # noqa: S608 - values are ids.sql_str hex literals, ids-validated identifiers or constants

    def release_done_seats(self, release_id: str) -> set[str]:
        """Seats whose latest rollout OF THIS RELEASE ended done.

        Completion is read from these records, not from one run's outcome: a
        release fixed seat by seat (``--only``) completes the moment its last
        seat is done, and a seat left waiting_idle or held keeps it incomplete.
        """
        rid = ids.sql_str(ids.release_id(release_id))
        rows = self.d1.query(
            "SELECT r.customer_slug FROM fleet_rollouts r JOIN (SELECT customer_slug, MAX(started_at) AS m "  # noqa: S608 - values are ids.sql_str hex literals, ids-validated identifiers or constants
            f"FROM fleet_rollouts WHERE release_id = {rid} GROUP BY customer_slug) x "
            "ON r.customer_slug = x.customer_slug AND r.started_at = x.m "
            f"WHERE r.release_id = {rid} AND r.state = 'done'"
        )
        return {r["customer_slug"] for r in rows}

    def release_latest(self, release_id: str) -> dict[str, dict[str, Any]]:
        """Each seat's latest rollout OF THIS RELEASE: {slug: {state, finished_at}}.

        What a tick resumes from: a seat whose latest is ``done`` is not touched
        again, a halting state stops the release until a human acts, and the
        newest ``finished_at`` of a ring's done seats starts its bake.
        """
        rid = ids.sql_str(ids.release_id(release_id))
        rows = self.d1.query(
            "SELECT r.customer_slug, r.state, r.finished_at FROM fleet_rollouts r JOIN (SELECT customer_slug, "  # noqa: S608 - values are ids.sql_str hex literals, ids-validated identifiers or constants
            f"MAX(started_at) AS m FROM fleet_rollouts WHERE release_id = {rid} GROUP BY customer_slug) x "
            f"ON r.customer_slug = x.customer_slug AND r.started_at = x.m WHERE r.release_id = {rid}"
        )
        return {r["customer_slug"]: {"state": r.get("state"), "finished_at": r.get("finished_at")} for r in rows}

    def open_alerts(self, seats: list[str], conditions: tuple[str, ...]) -> list[dict[str, Any]]:
        """Open fleet_alert_state rows for ``conditions`` on ``seats`` (the bake's second half)."""
        if not seats:
            return []
        who = ", ".join(ids.sql_str(ids.slug(s)) for s in seats)
        what = ", ".join(ids.sql_str(c) for c in conditions)
        return self.d1.query(
            "SELECT customer_slug, condition, opened_at FROM fleet_alert_state WHERE status = 'open' "  # noqa: S608 - values are ids.sql_str hex literals, ids-validated identifiers or constants
            f"AND customer_slug IN ({who}) AND condition IN ({what})"
        )

    # -- lease ------------------------------------------------------------------
    # One table, two scopes. LEASE_NAME is the fleet lease a full rollout holds
    # while it walks the rings; seat_lease_name(slug) is held for the length of
    # one seat's deploy (a rollout's, or a reprovision's). Same conditional
    # insert, same TTL and takeover of an expired holder.
    def acquire_lease(self, holder: str, release_id: str, name: str = LEASE_NAME) -> dict[str, Any] | None:
        """Take a lease if free or expired. Returns the current row (ours or theirs)."""
        self.d1.query(
            "INSERT INTO fleet_lease (lease_name, holder, release_id, acquired_at, expires_at) VALUES "  # noqa: S608 - values are ids.sql_str hex literals, ids-validated identifiers or constants
            f"({ids.sql_str(name)}, {ids.sql_str(holder)}, {ids.sql_str(release_id)}, {NOW_SQL}, "
            f"strftime('%Y-%m-%dT%H:%M:%SZ','now','{LEASE_TTL}')) "
            "ON CONFLICT(lease_name) DO UPDATE SET holder = excluded.holder, release_id = excluded.release_id, "
            "acquired_at = excluded.acquired_at, expires_at = excluded.expires_at "
            f"WHERE fleet_lease.expires_at < {NOW_SQL} OR fleet_lease.holder = excluded.holder"
        )
        return self.lease(name)

    def lease(self, name: str = LEASE_NAME) -> dict[str, Any] | None:
        rows = self.d1.query(f"SELECT * FROM fleet_lease WHERE lease_name = {ids.sql_str(name)}")  # noqa: S608 - values are ids.sql_str hex literals, ids-validated identifiers or constants
        return rows[0] if rows else None

    def renew_lease(self, holder: str, name: str = LEASE_NAME) -> bool:
        self.d1.query(
            f"UPDATE fleet_lease SET expires_at = strftime('%Y-%m-%dT%H:%M:%SZ','now','{LEASE_TTL}') "  # noqa: S608 - values are ids.sql_str hex literals, ids-validated identifiers or constants
            f"WHERE lease_name = {ids.sql_str(name)} AND holder = {ids.sql_str(holder)}"
        )
        row = self.lease(name)
        return bool(row and row.get("holder") == holder)

    def release_lease(self, holder: str, name: str = LEASE_NAME) -> None:
        self.d1.query(
            f"DELETE FROM fleet_lease WHERE lease_name = {ids.sql_str(name)} AND holder = {ids.sql_str(holder)}"  # noqa: S608 - values are ids.sql_str hex literals, ids-validated identifiers or constants
        )


def seat_lease_name(seat: str) -> str:
    """The lease one seat's deploy holds (a rollout's or a reprovision's)."""
    return f"seat:{ids.slug(seat)}"


def decode_json_list(text: str | None) -> list[str]:
    if not text:
        return []
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return []
    return [str(v) for v in value] if isinstance(value, list) else []
