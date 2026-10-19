"""`fleet release --skills`: the capability gate, the pointer, the repoint.

FALSIFIERS
  * a seat whose image lacks a need is refused and its pointer is NOT written
    (drop the gate and the pointer appears);
  * an unknown image is refused, never assumed capable;
  * no heartbeat confirming the version -> the pointer goes back to the
    previous one and the rollout halts.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from bin.lib.fleet import ids
from bin.lib.fleet.r2 import image_key, pointer_key, skills_manifest_key
from bin.lib.fleet.rings import Ring
from bin.lib.fleet.runner import ToolingError
from bin.lib.fleet.skills import SkillsRollout, unmet_needs
from bin.lib.fleet.waves import Lease, Waves
from bin.tests.fleet_fakes import DIGEST, OLD_DIGEST, FakeR2, FakeStore, fresh_status, release

VERSION = "202610191200-abcdef12"
OLD_VERSION = "202610181200-12345678"
CAPS = {
    "schema_version": 1,
    "paths": ["/app/operator/templates/drafting/drafting_gate_check.py", "/opt/connectors/smokeball/.venv/bin/python"],
    "tools": ["email_search", "medchron_job_submit"],
    "verbs": ["note_record"],
    "action_classes": ["read", "internal_write"],
}
MANIFEST = {
    "version": VERSION,
    "source_sha": "c" * 40,
    "files": {"smd-note/SKILL.md": {"sha256": "d" * 64, "size": 10}},
    "retired": [],
    "needs": {
        "smd-note": {
            "paths": ["/app/operator/templates/drafting"],
            "tools": ["email_search"],
            "verbs": ["note_record"],
            "action_classes": ["read"],
        },
    },
}


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


def world(manifest: dict | None = None):
    store, r2, clock = FakeStore(), FakeR2(), Clock()
    raw = json.dumps(manifest or MANIFEST).encode()
    r2.put_bytes(skills_manifest_key(VERSION), raw)
    r2.put_json(image_key(DIGEST, "image-capabilities.json"), CAPS)
    store.insert_release(release())
    for seat in ("smd-staging", "pilot-smokeball"):
        store.status[seat] = fresh_status(skills=VERSION)
        r2.put_json(
            pointer_key(seat),
            {"version": OLD_VERSION, "manifest_key": skills_manifest_key(OLD_VERSION), "manifest_sha256": "0" * 64},
        )
    r2.writes.clear()
    roll = SkillsRollout(store, r2, lambda m: None, clock.sleep, clock)  # type: ignore[arg-type]
    return store, r2, roll, raw


def run(store, roll, rings, dry_run=False):
    manifest, key, sha = roll.load_manifest(VERSION)
    rel = {"release_id": f"skl-{VERSION}", "kind": "skills", "skills_version": VERSION}
    waves = Waves(store, lambda m: None)
    lease = Lease(store, "t", rel["release_id"])
    with lease:
        return waves.run(
            rings,
            lambda s, r: roll.run(s, r, rel, manifest, key, sha, dry_run),
            release=rel,
            only=None,
            parallel=1,
            dry_run=dry_run,
            lease=lease,
        )


def test_unmet_needs_names_each_gap() -> None:
    caps = dict(CAPS, tools=[], paths=[])
    missing = unmet_needs(MANIFEST, caps)
    assert "smd-note: tool email_search" in missing
    assert "smd-note: path /app/operator/templates/drafting" in missing
    assert unmet_needs(MANIFEST, CAPS) == []


def test_capable_seats_get_the_pointer_and_confirm() -> None:
    store, r2, roll, raw = world()
    result = run(store, roll, [Ring(0, ["smd-staging"]), Ring(1, ["pilot-smokeball"], 1)])
    assert set(store.states().values()) == {"done"} and result.completed
    pointer = r2.get_json(pointer_key("smd-staging"))
    assert pointer == {
        "version": VERSION,
        "manifest_key": skills_manifest_key(VERSION),
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
    }


def test_capability_unsatisfied_seat_is_refused_and_no_pointer_written() -> None:
    needy = json.loads(json.dumps(MANIFEST))
    needy["needs"]["smd-note"]["tools"].append("brand_new_tool")
    store, r2, roll, _ = world(needy)
    result = run(store, roll, [Ring(0, ["smd-staging"]), Ring(1, ["pilot-smokeball"], 1)])
    states = store.states()
    assert states["smd-staging"] == "failed"
    assert "brand_new_tool" in next(r for r in store.rollouts.values() if r["customer_slug"] == "smd-staging")["detail"]
    assert pointer_key("smd-staging") not in r2.writes  # FALSIFIER: drop the gate and this pointer is written
    assert result.halted and states["pilot-smokeball"] == "halted"


def test_unknown_image_is_refused() -> None:
    store, r2, roll, _ = world()
    store.status["smd-staging"] = dict(fresh_status(image=f"{ids.FLEET_REGISTRY}:x@{OLD_DIGEST}"))
    run(store, roll, [Ring(0, ["smd-staging"], 1)])
    assert store.states()["smd-staging"] == "failed"
    assert "cannot tell what the seat's image provides" in next(iter(store.rollouts.values()))["detail"]
    assert r2.writes == []


def test_no_confirming_heartbeat_repoints_to_previous_and_halts() -> None:
    store, r2, roll, _ = world()
    store.status["smd-staging"] = fresh_status(skills=OLD_VERSION)  # never flips
    result = run(store, roll, [Ring(0, ["smd-staging"]), Ring(1, ["pilot-smokeball"], 1)])
    assert store.states()["smd-staging"] == "rolled_back"
    assert r2.get_json(pointer_key("smd-staging"))["version"] == OLD_VERSION
    assert result.halted and store.states()["pilot-smokeball"] == "halted"


def test_repoint_with_no_previous_pointer_removes_it() -> None:
    store, r2, roll, _ = world()
    r2.objects.pop(pointer_key("smd-staging"))
    store.status["smd-staging"] = fresh_status(skills=None)
    run(store, roll, [Ring(0, ["smd-staging"], 1)])
    assert pointer_key("smd-staging") in r2.deletes
    assert pointer_key("smd-staging") not in r2.objects


def test_missing_manifest_is_a_tooling_error() -> None:
    store, r2, roll, _ = world()
    r2.objects.pop(skills_manifest_key(VERSION))
    with pytest.raises(ToolingError):
        roll.load_manifest(VERSION)


def test_dry_run_writes_no_pointer() -> None:
    store, r2, roll, _ = world()
    manifest, key, sha = roll.load_manifest(VERSION)
    rel = {"release_id": f"skl-{VERSION}", "kind": "skills", "skills_version": VERSION}
    out = roll.run("smd-staging", 0, rel, manifest, key, sha, True)
    assert out.state == "pending" and r2.writes == [] and store.rollouts == {}
