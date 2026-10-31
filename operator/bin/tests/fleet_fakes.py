"""In-memory stand-ins for the fleet CLI's adapters (D1, R2, Fly, smoke).

Each fake records what was asked of it so a test can assert what the rollout
DID (which image it deployed, whether it rolled back, whether a pointer was
written), not just what it returned. Nothing here touches the network.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from bin.lib.fleet import ids
from bin.lib.fleet.runner import Result, ToolingError
from bin.lib.fleet.smoke import Check

DIGEST = "sha256:" + "a" * 64
OLD_DIGEST = "sha256:" + "b" * 64
TARGET_REF = f"{ids.FLEET_REGISTRY}:rel-11111111-202610190000"
# Since the fold (ADR 0095) a release's overlay_ref IS its ss_console_sha: the
# heartbeat `version` the smoke compares against is the commit the image is from.
OVERLAY = "1" * 40


def release(**over: Any) -> dict[str, Any]:
    base = {
        "release_id": "img-202610190000-11111111",
        "kind": "image",
        "image_ref": TARGET_REF,
        "image_digest": DIGEST,
        "skills_version": None,
        "ss_console_sha": "1" * 40,
        "overlay_ref": OVERLAY,
        "volume_forward_compatible": 1,
        "declared_secrets": "[]",
    }
    base.update(over)
    return base


class FakeStore:
    def __init__(self) -> None:
        self.releases: dict[str, dict[str, Any]] = {}
        self.rollouts: dict[str, dict[str, Any]] = {}
        self.holds_rows: dict[str, dict[str, Any]] = {}
        self.status: dict[str, dict[str, Any]] = {}
        self.leases: dict[str, dict[str, Any]] = {}
        self.lease_expired = False
        self.completed: list[str] = []
        self.status_script: dict[str, list[dict[str, Any] | None]] = {}
        # What finished_at a terminal rollout gets (the D1 NOW_SQL); tests move it.
        self.now_iso = "2026-10-23T00:00:00Z"
        self.alerts: list[dict[str, Any]] = []
        self.holds_reads = 0

    # releases
    def insert_release(self, rec: dict[str, Any]) -> None:
        self.releases[rec["release_id"]] = dict(rec)

    def release(self, rid: str) -> dict[str, Any] | None:
        return self.releases.get(rid)

    def skills_release(self, version: str) -> dict[str, Any] | None:
        return next(
            (r for r in self.releases.values() if r.get("skills_version") == version and r["kind"] == "skills"), None
        )

    def bundle_release(self, name: str, version: str) -> dict[str, Any] | None:
        hits = [
            r for r in self.releases.values()
            if r["kind"] == "bundle" and r.get("artifact_name") == name and r.get("artifact_version") == version
        ]  # fmt: skip
        return hits[-1] if hits else None

    def release_for_sha(self, kind: str, sha: str) -> dict[str, Any] | None:
        hits = [r for r in self.releases.values() if r["kind"] == kind and r.get("ss_console_sha") == sha]
        return hits[-1] if hits else None

    def release_for_image(self, ref: str) -> dict[str, Any] | None:
        d = ids.digest_in_ref(ref)
        for r in self.releases.values():
            if r["kind"] == "image" and (r.get("image_digest") == d or r.get("image_ref") == ref.split("@")[0]):
                return r
        return None

    def latest_completed(self, kind: str) -> dict[str, Any] | None:
        done = [r for r in self.releases.values() if r["kind"] == kind and r.get("completed_last_ring_at")]
        return max(done, key=lambda r: r["completed_last_ring_at"]) if done else None

    def recent_releases(self, limit: int = 20) -> list[dict[str, Any]]:
        return list(self.releases.values())[:limit]

    def mark_completed(self, rid: str) -> None:
        self.completed.append(rid)
        if rid in self.releases:
            self.releases[rid]["completed_last_ring_at"] = "2026-10-19T00:00:00Z"

    # holds
    def holds(self) -> dict[str, dict[str, Any]]:
        self.holds_reads += 1
        return dict(self.holds_rows)

    def hold(self, seat: str, reason: str, held_by: str) -> None:
        self.holds_rows[seat] = {"customer_slug": seat, "reason": reason, "held_by": held_by}

    def unhold(self, seat: str) -> None:
        self.holds_rows.pop(seat, None)

    # status
    def fleet_status(self) -> dict[str, dict[str, Any]]:
        return dict(self.status)

    def seat_status(self, seat: str) -> dict[str, Any] | None:
        script = self.status_script.get(seat)
        if script:
            row = script.pop(0) if len(script) > 1 else script[0]
            if isinstance(row, Exception):
                raise row
            return copy.deepcopy(row)
        return copy.deepcopy(self.status.get(seat))

    def last_rollouts(self) -> dict[str, dict[str, Any]]:
        return {r["customer_slug"]: r for r in self.rollouts.values()}

    # rollouts
    def start_rollout(self, rec: dict[str, Any]) -> None:
        self.rollouts[rec["rollout_id"]] = {**rec, "state": "pending", "history": ["pending"]}

    def update_rollout(self, rid: str, state: str, **fields: Any) -> None:
        row = self.rollouts[rid]
        row.update(fields)
        row["state"] = state
        row["history"].append(state)
        if state in ("done", "rolled_back", "failed", "halted", "skipped_held", "tooling_error", "waiting_idle"):
            row["finished_at"] = self.now_iso

    def release_done_seats(self, rid: str) -> set[str]:
        latest: dict[str, str] = {}
        for r in self.rollouts.values():  # insertion order is start order
            if r["release_id"] == rid:
                latest[r["customer_slug"]] = r["state"]
        return {seat for seat, state in latest.items() if state == "done"}

    def release_latest(self, rid: str) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for r in self.rollouts.values():
            if r["release_id"] == rid:
                latest[r["customer_slug"]] = {"state": r["state"], "finished_at": r.get("finished_at")}
        return latest

    def open_alerts(self, seats: list[str], conditions: tuple[str, ...]) -> list[dict[str, Any]]:
        return [
            a for a in self.alerts
            if a["customer_slug"] in seats and a["condition"] in conditions and a.get("status", "open") == "open"
        ]  # fmt: skip

    def states(self) -> dict[str, str]:
        return {r["customer_slug"]: r["state"] for r in self.rollouts.values()}

    # lease (one table, keyed by lease_name; "rollout" is the fleet lease)
    @property
    def lease_row(self) -> dict[str, Any] | None:
        return self.leases.get("rollout")

    @lease_row.setter
    def lease_row(self, row: dict[str, Any] | None) -> None:
        if row is None:
            self.leases.pop("rollout", None)
        else:
            self.leases["rollout"] = row

    def acquire_lease(self, holder: str, rid: str, name: str = "rollout") -> dict[str, Any] | None:
        row = self.leases.get(name)
        expired = self.lease_expired and name == "rollout"
        if row is None or expired or row["holder"] == holder:
            self.leases[name] = {"lease_name": name, "holder": holder, "release_id": rid, "expires_at": "later"}
            if expired:
                self.lease_expired = False
        return dict(self.leases[name])

    def lease(self, name: str = "rollout") -> dict[str, Any] | None:
        return self.leases.get(name)

    def renew_lease(self, holder: str, name: str = "rollout") -> bool:
        row = self.leases.get(name)
        return bool(row and row["holder"] == holder)

    def release_lease(self, holder: str, name: str = "rollout") -> None:
        row = self.leases.get(name)
        if row and row["holder"] == holder:
            del self.leases[name]


class FakeR2:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.writes: list[str] = []
        self.deletes: list[str] = []
        self.fail_reads = False

    def get_bytes(self, key: str) -> bytes | None:
        if self.fail_reads:
            raise ToolingError("R2 down")
        return self.objects.get(key)

    def get_json(self, key: str) -> Any | None:
        raw = self.get_bytes(key)
        return None if raw is None else json.loads(raw)

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/json") -> None:
        self.objects[key] = data
        self.writes.append(key)

    def put_json(self, key: str, value: Any) -> bytes:
        data = json.dumps(value, sort_keys=True).encode()
        self.put_bytes(key, data)
        return data

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)
        self.deletes.append(key)

    def head(self, key: str) -> dict[str, Any] | None:
        return {} if key in self.objects else None

    def exists_prefix(self, prefix: str) -> bool:
        return any(k.startswith(prefix) for k in self.objects)


IDLE_FACTS: dict[str, Any] = {"lanes": [], "children": [], "crons": [], "errors": []}


class FakeFly:
    def __init__(self) -> None:
        self.deploys: list[tuple[str, str, str]] = []
        self.deploy_results: list[Result] = []
        self.probe_facts: dict[str, list[dict[str, Any]]] = {}
        self.quiesce_calls: list[tuple[str, str]] = []
        self.secrets: dict[str, tuple[set[str], set[str]]] = {}
        self.ssh_raises: set[str] = set()
        self.snapshots: list[str] = []
        self.no_machine: set[str] = set()
        self.machine_state = "started"
        self.guest_value: dict[str, Any] = {"cpu_kind": "shared", "cpus": 1, "memory_mb": 2048}
        self.plant_calls: list[tuple[str, str]] = []
        self.plant_answer = "ok"
        self.restarts: list[str] = []
        self.restart_results: list[Result] = []
        # Called after each restart (a test makes the seat's heartbeat answer here).
        self.on_restart: Any = None

    def app_exists(self, app: str) -> bool:
        return True

    def machines(self, app: str) -> list[dict[str, Any]]:
        if app in self.no_machine:
            return []
        return [{"state": self.machine_state, "config": {"image": self.machine_image(app), "guest": self.guest_value}}]

    def guest(self, app: str) -> dict[str, Any]:
        return dict(self.guest_value)

    def secret_names(self, app: str) -> tuple[set[str], set[str]]:
        return self.secrets.get(app, ({"ANTHROPIC_API_KEY", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"}, set()))

    def machine_image(self, app: str) -> str:
        return f"registry.fly.io/{app}:deployment-OLD"

    def saved_config(self, app: str) -> str:
        return f'app = "{app}"\n# the deployed toml\n'

    def volume_id(self, app: str, name: str = "hermes_state") -> str:
        return "vol_123"

    def snapshot(self, app: str, volume: str) -> str:
        self.snapshots.append(app)
        return "vs_abc"

    def deploy(self, app: str, image: str, config_path: str) -> Result:
        with open(config_path, encoding="utf-8") as fh:
            self.deploys.append((app, image, fh.read()))
        return self.deploy_results.pop(0) if self.deploy_results else Result(0, "deployed", "")

    def restart(self, app: str) -> Result:
        self.restarts.append(app)
        res = self.restart_results.pop(0) if self.restart_results else Result(0, "restarted", "")
        if res.ok and self.on_restart is not None:
            self.on_restart(app)
        return res

    def run_python(self, app: str, source: str, timeout: float = 90) -> Result:
        seat = app.removeprefix("hermes-")
        if seat in self.ssh_raises:
            raise ToolingError(f"ssh to {app} hung")
        if "SMD_SELFTEST_PLANT" in source:
            action = json.loads(json.loads(source.split("json.loads(", 2)[1].split(")\n", 1)[0]))["action"]
            self.plant_calls.append((seat, action))
            return Result(0, f"SMD_SELFTEST_PLANT {self.plant_answer} {action} old-tracker", "")
        if "SMD_QUIESCE" in source and "ARGS = json.loads" in source:
            action = "set" if '\\"action\\": \\"set\\"' in source else "clear"
            self.quiesce_calls.append((seat, action))
            return Result(0, "SMD_QUIESCE set {}" if action == "set" else "SMD_QUIESCE cleared", "")
        script = self.probe_facts.get(seat) or [IDLE_FACTS]
        facts = script.pop(0) if len(script) > 1 else script[0]
        return Result(0, "SMD_BUSY_PROBE " + json.dumps(facts), "")


class FakeSmoke:
    def __init__(self, *checks: Check) -> None:
        self.checks = list(checks)
        self.calls: list[tuple[str, str | None]] = []

    def run(self, seat: str, expected_hermes_sha: str | None) -> Check:
        self.calls.append((seat, expected_hermes_sha))
        return self.checks.pop(0) if self.checks else Check("pass", "green")


def fresh_status(
    image: str = TARGET_REF + "@" + DIGEST, overlay: str = OVERLAY, skills: str | None = None
) -> dict[str, Any]:
    return {
        "last_heartbeat_ts": "2099-01-01T00:00:00Z",
        "image_ref": image,
        "version": overlay,
        "skills_version": skills,
    }


HERMES_SHA = "3" * 40


def selftest_json(**over: Any) -> str:
    """A heartbeat `selftest` object, as stored in fleet_status.selftest_json."""
    doc = {
        "v": 1,
        "run_kind": "boot",
        "finished_at": "2099-01-01T00:00:00Z",
        "version": OVERLAY,
        "image_ref": TARGET_REF + "@" + DIGEST,
        "hermes_sha": HERMES_SHA,
        "total": 74,
        "failed_count": 0,
        "warned_count": 0,
        "failed_streak": 0,
        "failed": [],
        "warned": [],
    }
    doc.update(over)
    return json.dumps(doc)
