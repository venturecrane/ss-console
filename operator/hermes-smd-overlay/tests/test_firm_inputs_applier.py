"""firm_inputs_applier: a firm's lane configuration adopted live, never unvalidated.

Driven against a fake vault (HEAD/LIST/GET), a temp config dir and a stub
validator, so every branch of a commit is exercised without R2 or the bundle.
Each test names the falsifier that would turn it red.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from firm_inputs_applier import __main__ as main_mod
from firm_inputs_applier.applier import (
    KEEP_VERSIONS,
    LANES_BY_NAME,
    FirmInputsApplier,
    Outcome,
    ValidatorResult,
    authored_lanes,
    current_label,
    etag8,
)
from shared import fleet_facts
from shared.heartbeat import build_payload

SLUG = "pilot"
BEAT = {
    "heartbeat_ts": "2026-11-04T12:00:01Z",
    "last_audit_ts": None,
    "last_skill_ts": None,
    "uptime_seconds": None,
    "version": None,
}


class NotFound(Exception):
    def __init__(self) -> None:
        super().__init__("404")
        self.response = {"Error": {"Code": "404"}}


class Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class FakeVault:
    """An S3-shaped vault: objects with ETags, and a hook that fires mid-GET."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.on_get = None
        self.heads = 0

    def put(self, key: str, data: bytes, etag: str) -> None:
        self.objects[key] = (data, f'"{etag}"')

    def head_object(self, *, Bucket: str, Key: str):  # noqa: N803
        self.heads += 1
        if Key not in self.objects:
            raise NotFound()
        return {"ETag": self.objects[Key][1]}

    def list_objects_v2(self, *, Bucket: str, Prefix: str, ContinuationToken=None):  # noqa: N803
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        return {"Contents": [{"Key": k} for k in keys], "IsTruncated": False}

    def get_object(self, *, Bucket: str, Key: str):  # noqa: N803
        if self.on_get is not None:
            hook, self.on_get = self.on_get, None
            hook()
        return {"Body": Body(self.objects[Key][0])}


def publish_records(vault: FakeVault, etag: str, budget: str = "100") -> None:
    vault.put(f"vaults/{SLUG}/records/records-firm.yaml", f"budget: {budget}\n".encode(), etag)
    vault.put(f"vaults/{SLUG}/records/prompts/a.txt", b"prompt\n", "ffffffff00")


class StubValidator:
    """Exit 0 unless the staged records-firm.yaml says ``budget: bad``."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.code_override: int | None = None

    def __call__(self, argv: list[str]) -> ValidatorResult:
        self.calls.append(argv)
        if self.code_override is not None:
            return ValidatorResult(self.code_override, "validator missing")
        path = Path(argv[-1])
        text = (path / "records-firm.yaml" if path.is_dir() else path).read_text()
        if "bad" in text:
            return ValidatorResult(1, "records-firm.yaml: budget.monthly_usd must be a number")
        return ValidatorResult(0, "OK")


@pytest.fixture
def world(tmp_path: Path):
    vault = FakeVault()
    validator = StubValidator()
    owned: list[tuple[str, int]] = []
    applier = FirmInputsApplier(
        s3_client=vault,
        bucket="cfg",
        slug=SLUG,
        lanes=authored_lanes("records,medchron,note"),
        config_dir=tmp_path / "smd-config",
        state_dir=tmp_path / "run/smd-firm-inputs",
        lane_state_dir=tmp_path / "run/smd-lanes",
        validator=validator,
        chown=lambda p, uid, gid: owned.append((p, gid)),
        group_id=lambda g: {"jobs": 1001, "workspace-broker": 1002}[g],
        now=lambda: "2026-11-04T12:00:00Z",
        validator_ready=lambda path: True,
    )
    applier.config_dir.mkdir(parents=True)
    return applier, vault, validator, owned


def state(applier: FirmInputsApplier) -> dict:
    return json.loads((applier.state_dir / "state.json").read_text())


def test_etag8_and_the_authored_set() -> None:
    assert etag8('"1A2B3C4D5E6F"') == "1a2b3c4d"
    assert etag8('"abcdef12-3"') == "abcdef12"
    assert etag8(None) is None and etag8('"xyz"') is None
    # FALSIFIER: include a lane with no firm inputs (note, negotiation) and this fails.
    assert [lane.name for lane in authored_lanes("records, note,medchron,negotiation")] == [
        "records",
        "medchron",
    ]
    assert authored_lanes("") == ()


def test_a_published_change_is_staged_validated_and_swapped_atomically(world) -> None:
    """FALSIFIER: swap before validating, or skip the broker copy, and the asserts fail."""
    applier, vault, validator, owned = world
    publish_records(vault, "1a2b3c4d0000")
    assert applier.tick()["records"] is Outcome.APPLIED
    live = applier.config_dir / "records"
    assert os.path.islink(live) and os.readlink(live) == "records.v1a2b3c4d"
    assert (live / "records-firm.yaml").read_text() == "budget: 100\n"
    assert (live / "prompts/a.txt").is_file()
    assert validator.calls and validator.calls[0][1:3] == ["-m", "jobs.records.firm"]
    # Validated as the staging path, never the live one.
    assert ".records.stage-" in validator.calls[0][-1]
    broker = applier.config_dir / "records-broker-firm.yaml"
    assert broker.read_text() == "budget: 100\n"
    assert any(
        p == str(live.resolve() / "records-firm.yaml") or p.endswith("records-firm.yaml")
        for p, _ in owned
    )
    assert {gid for _, gid in owned} == {1001, 1002}
    assert oct(os.stat(live / "records-firm.yaml").st_mode & 0o777) == "0o640"
    assert (applier.lane_state_dir / "records.inputs").read_text() == "fetched\n"
    assert state(applier)["records"] == {
        "etag8": "1a2b3c4d",
        "verdict": "fetched",
        "applied_at": "2026-11-04T12:00:00Z",
    }
    # An unchanged ETag is a HEAD and nothing else.
    validator.calls.clear()
    assert applier.tick()["records"] is Outcome.UNCHANGED
    assert validator.calls == []


def test_a_refused_version_keeps_the_live_one_and_is_not_retried(world) -> None:
    """FALSIFIER: swap on exit 1, or forget the refused ETag, and this fails."""
    applier, vault, validator, _ = world
    publish_records(vault, "1a2b3c4d0000")
    applier.tick()
    publish_records(vault, "badbadba0000", budget="bad")
    assert applier.tick()["records"] is Outcome.REJECTED
    live = applier.config_dir / "records"
    assert os.readlink(live) == "records.v1a2b3c4d"
    assert (live / "records-firm.yaml").read_text() == "budget: 100\n"
    assert not (applier.config_dir / "records.vbadbadba").exists()
    assert (applier.config_dir / "records-broker-firm.yaml").read_text() == "budget: 100\n"
    st = state(applier)["records"]
    assert st["verdict"] == "rejected" and st["etag8"] == "1a2b3c4d"
    assert st["rejected_etag8"] == "badbadba"
    assert st["detail"].startswith("records-firm.yaml: budget")
    assert (applier.lane_state_dir / "records.inputs").read_text() == "rejected\n"
    # The same refused ETag is not re-staged, even by a new process (state survives).
    calls = len(validator.calls)
    fresh = FirmInputsApplier(**{**applier.__dict__, "_logged_head": {}})
    assert fresh.tick()["records"] is Outcome.UNCHANGED
    assert len(validator.calls) == calls
    # A corrected publish is adopted.
    publish_records(vault, "c0ffee000000", budget="200")
    assert applier.tick()["records"] is Outcome.APPLIED
    assert os.readlink(live) == "records.vc0ffee00"
    assert state(applier)["records"]["verdict"] == "fetched"


def test_a_validator_that_cannot_run_never_replaces_a_live_copy(world) -> None:
    """FALSIFIER: treat 'could not run' as ok and the live copy is replaced unchecked."""
    applier, vault, validator, _ = world
    validator.code_override = 127
    publish_records(vault, "1a2b3c4d0000")
    # No live copy: adopted unchecked (the runner's gate re-validates every job).
    assert applier.tick()["records"] is Outcome.APPLIED
    publish_records(vault, "22222222aaaa")
    assert applier.tick()["records"] is Outcome.RETRY
    assert os.readlink(applier.config_dir / "records") == "records.v1a2b3c4d"
    validator.code_override = None
    assert applier.tick()["records"] is Outcome.APPLIED


def test_a_publish_landing_mid_copy_is_thrown_away(world) -> None:
    """FALSIFIER: drop the second HEAD and a half-old, half-new copy is adopted."""
    applier, vault, validator, _ = world
    publish_records(vault, "1a2b3c4d0000")
    vault.on_get = lambda: publish_records(vault, "33333333bbbb", budget="300")
    assert applier.tick()["records"] is Outcome.RETRY
    assert not os.path.lexists(applier.config_dir / "records")
    assert not [p for p in applier.config_dir.iterdir() if p.name.startswith(".records.stage-")]
    assert applier.tick()["records"] is Outcome.APPLIED
    assert os.readlink(applier.config_dir / "records") == "records.v33333333"


def test_prune_keeps_three_versions_and_always_the_live_one(world) -> None:
    """FALSIFIER: prune by name, or past three, and the counts fail."""
    applier, vault, _, _ = world
    for i, etag in enumerate(("aaaaaaaa", "bbbbbbbb", "cccccccc", "dddddddd", "eeeeeeee")):
        publish_records(vault, etag + "00", budget=str(i))
        assert applier.tick()["records"] is Outcome.APPLIED
        stamp = 1_700_000_000 + i
        os.utime(applier.config_dir / f"records.v{etag}", (stamp, stamp))
    applier.prune(LANES_BY_NAME["records"])
    versions = sorted(
        p.name for p in applier.config_dir.iterdir() if p.name.startswith("records.v")
    )
    assert len(versions) == KEEP_VERSIONS
    assert versions == ["records.vcccccccc", "records.vdddddddd", "records.veeeeeeee"]
    assert os.readlink(applier.config_dir / "records") == "records.veeeeeeee"


def test_a_pre_versioning_directory_is_converted_not_lost(world) -> None:
    """FALSIFIER: os.replace onto a real directory fails; the legacy move makes room."""
    applier, vault, _, _ = world
    legacy = applier.config_dir / "records"
    legacy.mkdir()
    (legacy / "records-firm.yaml").write_text("budget: 1\n")
    publish_records(vault, "1a2b3c4d0000")
    assert applier.tick()["records"] is Outcome.APPLIED
    assert os.readlink(legacy) == "records.v1a2b3c4d"
    assert (applier.config_dir / "records.v00000000/records-firm.yaml").read_text() == "budget: 1\n"


def test_the_chronology_file_is_versioned_and_validated_by_its_loader(world) -> None:
    applier, vault, validator, _ = world
    vault.put(f"vaults/{SLUG}/medchron-firm.yaml", b"firm: one\n", "abcdef01")
    assert applier.tick()["medchron"] is Outcome.APPLIED
    live = applier.config_dir / "medchron-firm.yaml"
    assert os.readlink(live) == "medchron-firm.vabcdef01.yaml"
    assert live.read_text() == "firm: one\n"
    argv = validator.calls[-1]
    assert argv[1] == "-c" and "jobs.medchron.config import load" in argv[2]
    assert state(applier)["medchron"]["etag8"] == "abcdef01"


def test_an_unauthored_or_absent_lane_is_never_touched(world) -> None:
    """FALSIFIER: poll every lane in the table and demand gets a version dir."""
    applier, vault, _, _ = world
    vault.put(f"vaults/{SLUG}/demand/demand-firm.yaml", b"x\n", "12345678")
    outcomes = applier.tick()
    assert "demand" not in outcomes
    assert not os.path.lexists(applier.config_dir / "demand")
    assert outcomes["records"] is Outcome.ABSENT


def test_a_boot_adopted_version_is_recorded_without_refetching(world) -> None:
    """The entrypoint fragments adopt at boot; the first tick only records it."""
    applier, vault, validator, _ = world
    version = applier.config_dir / "records.v1a2b3c4d"
    version.mkdir()
    (version / "records-firm.yaml").write_text("budget: 100\n")
    os.symlink("records.v1a2b3c4d", applier.config_dir / "records")
    publish_records(vault, "1a2b3c4d0000")
    assert applier.tick()["records"] is Outcome.UNCHANGED
    assert validator.calls == []
    assert state(applier)["records"]["etag8"] == "1a2b3c4d"
    assert current_label(LANES_BY_NAME["records"], applier.config_dir) == "1a2b3c4d"


def test_the_heartbeat_carries_the_state_without_the_detail(world) -> None:
    """FALSIFIER: forward the state file whole and the validator's detail leaves the seat."""
    applier, vault, _, _ = world
    publish_records(vault, "1a2b3c4d0000")
    applier.tick()
    publish_records(vault, "badbadba0000", budget="bad")
    applier.tick()
    facts = fleet_facts.read_firm_inputs(applier.state_dir / "state.json")
    assert facts == {
        "records": {
            "etag8": "1a2b3c4d",
            "verdict": "rejected",
            "applied_at": "2026-11-04T12:00:00Z",
            "rejected_etag8": "badbadba",
        }
    }
    payload = build_payload(**BEAT, firm_inputs=facts)
    assert payload["firm_inputs"] == facts
    assert "firm_inputs" not in build_payload(**BEAT)
    assert fleet_facts.read_firm_inputs(applier.state_dir / "missing.json") is None


def test_main_once_ticks_and_always_exits_zero(world) -> None:
    applier, vault, _, _ = world
    publish_records(vault, "1a2b3c4d0000")
    loop = main_mod.FirmInputsPollLoop(applier=applier)
    assert main_mod.main(["--once"], loop_factory=lambda with_audit: loop) == 0
    assert os.readlink(applier.config_dir / "records") == "records.v1a2b3c4d"

    def refused(with_audit):
        raise SystemExit("required env var unset")

    assert main_mod.main(["--once"], loop_factory=refused) == 0


def test_a_missing_validator_is_noticed_before_any_download(world) -> None:
    """FALSIFIER: stage first and a seat without its bundle re-downloads the lane every tick."""
    applier, vault, validator, _ = world
    publish_records(vault, "1a2b3c4d0000")
    applier.tick()
    applier.validator_ready = lambda path: False
    gets = []
    vault.on_get = lambda: gets.append(1)
    publish_records(vault, "22222222aaaa")
    assert applier.tick()["records"] is Outcome.RETRY
    assert gets == [] and len(validator.calls) == 1
    applier.validator_ready = lambda path: True
    assert applier.tick()["records"] is Outcome.APPLIED
