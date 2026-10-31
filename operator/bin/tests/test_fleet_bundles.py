"""The jobs bundle step of the release controller (ADR 0100 W3).

A jobs bundle reaches only the seats whose customer.yaml authors a job lane,
ring by ring, and only once a seat's image descends from base(S). On a seat the
pointer is written, the Machine restarts behind the busy gate, and the heartbeat
must report the version; anything else puts the previous pointer back and
restarts again. Each test names its falsifier.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from bin.lib.fleet import bundles, cli, ordering
from bin.lib.fleet.bundles import BundleRollout, authoring_rings, authoring_seats, judge_bundle_row
from bin.lib.fleet.busy import BusyGate
from bin.lib.fleet.r2 import POINTER_KEY_RE, bundle_manifest_key, bundle_pointer_key, bundle_tarball_key, image_key
from bin.lib.fleet.rings import Ring
from bin.lib.fleet.runner import CommandRunner, Result, ToolingError
from bin.lib.fleet.tick import TICK_PROGRESS, TICK_WAITING, Ticker
from bin.tests.fleet_fakes import DIGEST, FakeFly, FakeR2, FakeStore, fresh_status, release, selftest_json

REPO = Path(__file__).resolve().parents[3]
V1 = "202610310100-11111111"
V2 = "202610310200-22222222"
T0 = datetime(2026, 10, 31, tzinfo=timezone.utc).timestamp()
LANE_YAML = """personas:
  - name: operator
    skills:
      - name: medical-chronology-maintainer
"""
NO_LANE_YAML = """personas:
  - name: operator
    skills:
      - name: email-triage
"""


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


def bundle_rel(version: str, sha: str = "1" * 40) -> dict[str, Any]:
    return {"release_id": f"bnd-{version}", "kind": "bundle", "artifact_name": "jobs", "artifact_version": version,
            "ss_console_sha": sha, "image_ref": release()["image_ref"]}  # fmt: skip


class World:
    """One seat set against fakes. A restarted seat reports what its pointer says, unless told to fail."""

    def __init__(self, seats: list[str], caps: dict[str, Any] | None = None) -> None:
        self.store, self.r2, self.fly, self.clock = FakeStore(), FakeR2(), FakeFly(), Clock()
        self.logs: list[str] = []
        self.store.insert_release(release())
        self.r2.put_json(image_key(DIGEST, "image-capabilities.json"), caps or {"schema_version": 1})
        for seat in seats:
            self.store.status[seat] = {**fresh_status(), "bundles_json": json.dumps({"jobs": "image"})}
        # What a restarted seat ends up mounting: the pointer's version unless the seat refuses it.
        self.refuses: set[str] = set()
        self.selftest_failed: set[str] = set()
        self.fly.on_restart = self._booted
        self.gate = BusyGate(self.fly, "t", self.clock.sleep, self.clock)  # type: ignore[arg-type]
        self.roll = BundleRollout(self.store, self.r2, self.fly, self.gate, self.logs.append,  # type: ignore[arg-type]
                                  self.clock.sleep, self.clock, max_idle_wait=60)  # fmt: skip

    def _booted(self, app: str) -> None:
        seat = app.removeprefix("hermes-")
        pointer = self.r2.get_json(bundle_pointer_key(seat, "jobs"))
        version = (pointer or {}).get("version") or "image"
        if seat in self.refuses and pointer:
            version = "image"
        row = self.store.status[seat]
        row["bundles_json"] = json.dumps({"jobs": version})
        doc = json.loads(selftest_json(finished_at="2099-01-02T00:00:00Z"))
        if seat in self.selftest_failed:
            doc.update(failed_count=1, failed=["lane:medchron"])
        row["selftest_json"] = json.dumps(doc)

    def publish(self, version: str, sha: str = "1" * 40) -> dict[str, Any]:
        manifest = {"name": "jobs", "version": version, "sha256": "e" * 64, "size": 10, "ss_console_sha": sha,
                    "base_image_ref": release()["image_ref"], "python": "3.13.9"}  # fmt: skip
        self.r2.put_bytes(bundle_manifest_key("jobs", version), json.dumps(manifest).encode())
        self.r2.put_bytes(bundle_tarball_key("jobs", version), b"zstd")
        rel = bundle_rel(version, sha)
        self.store.insert_release(rel)
        return rel

    def run(self, seat: str, version: str) -> Any:
        rel = self.store.releases[f"bnd-{version}"]
        manifest, key, sha = self.roll.load_manifest("jobs", version)
        return self.roll.run(seat, 0, rel, manifest, key, sha, False)

    def pointer(self, seat: str) -> str | None:
        return (self.r2.get_json(bundle_pointer_key(seat, "jobs")) or {}).get("version")


# -- which seats ----------------------------------------------------------------------
def _repo_with(tmp_path: Path, seats: dict[str, str | None]) -> Path:
    shared = tmp_path / "operator/hermes-smd-overlay/shared"
    shared.mkdir(parents=True)
    (shared / "capabilities.py").symlink_to(REPO / bundles.CAPABILITIES)
    for seat, text in seats.items():
        d = tmp_path / "operator/customers" / seat
        d.mkdir(parents=True)
        if text is not None:
            (d / "customer.yaml").write_text(text)
    return tmp_path


def test_only_seats_that_author_a_lane_are_in_the_bundle_rings(tmp_path: Path) -> None:
    repo = _repo_with(tmp_path, {"lane-a": LANE_YAML, "core-b": NO_LANE_YAML, "lane-c": LANE_YAML})
    rings = [Ring(0, ["lane-a", "core-b"], None, 0), Ring(1, ["core-b"], None, 0), Ring(2, ["lane-c"], 1, 0)]
    authoring = authoring_seats(repo, ["lane-a", "core-b", "lane-c"])
    assert authoring == {"lane-a", "lane-c"}
    assert [r.seats for r in authoring_rings(rings, authoring)] == [["lane-a"], [], ["lane-c"]]


def test_an_unreadable_customer_yaml_is_a_tooling_error_not_a_seat_left_out(tmp_path: Path) -> None:
    # FALSIFIER: read "cannot read" as "authors nothing" and the seat silently never gets a bundle.
    repo = _repo_with(tmp_path, {"lane-a": LANE_YAML, "gone": None})
    with pytest.raises(ToolingError, match="cannot read"):
        authoring_seats(repo, ["lane-a", "gone"])


def test_the_real_fleet_splits_by_authored_lanes() -> None:
    """The repo's own seats: the law-firm lanes are authored where the persona enables them."""
    got = authoring_seats(REPO, ["smd-staging", "ashton-price", "brokery", "captain-mark"])
    assert {"smd-staging", "ashton-price"} <= got
    assert not {"brokery", "captain-mark"} & got


def test_a_seat_that_authors_no_lane_never_blocks_completion() -> None:
    """Filtered, not deferred. FALSIFIER: defer the non-authoring seat instead of
    filtering it out and the release never completes (tick.plan_tick)."""
    w = World(["lane-a", "core-b"])
    rel = w.publish(V1)
    rings = [Ring(0, ["lane-a", "core-b"], None, 0)]
    filtered = authoring_rings(rings, {"lane-a"})
    ticker = Ticker(w.store, w.logs.append, w.clock)  # type: ignore[arg-type]

    def do_seat_for(r: dict[str, Any]) -> Any:
        return lambda seat, ring: w.run(seat, r["artifact_version"])

    assert ticker.run_promoting(filtered, [rel], do_seat_for, "h") == TICK_PROGRESS
    assert rel["release_id"] in w.store.completed
    assert w.fly.restarts == ["hermes-lane-a"], "a seat that authors no lane is never touched"
    assert w.pointer("core-b") is None
    # The defect shape: the same seat deferred keeps the release open.
    w2 = World(["lane-a", "core-b"])
    rel2 = w2.publish(V1)
    deferring = Ticker(w2.store, w2.logs.append, w2.clock, defer=lambda r: {"core-b": "authors no lane"})  # type: ignore[arg-type]
    deferring.run_promoting(rings, [rel2], lambda r: lambda seat, ring: w2.run(seat, V1), "h")
    assert rel2["release_id"] not in w2.store.completed


# -- one seat ---------------------------------------------------------------------------
def test_a_seat_takes_the_bundle_when_its_heartbeat_reports_it() -> None:
    w = World(["lane-a"])
    w.publish(V1)
    out = w.run("lane-a", V1)
    assert out.state == "done", out.detail
    assert w.pointer("lane-a") == V1 and w.fly.restarts == ["hermes-lane-a"]
    assert ("lane-a", "clear") in w.fly.quiesce_calls
    written = w.r2.get_json(bundle_pointer_key("lane-a", "jobs"))
    assert written["manifest_key"] == bundle_manifest_key("jobs", V1) and len(written["manifest_sha256"]) == 64


def test_a_seat_that_never_reports_the_version_is_rolled_back_to_its_previous_pointer() -> None:
    """FALSIFIER: leave the pointer after a failed read-back and the seat's next
    boot mounts a bundle nothing verified."""
    w = World(["lane-a"])
    w.publish(V1)
    w.publish(V2)
    assert w.run("lane-a", V1).state == "done"
    w.refuses.add("lane-a")
    out = w.run("lane-a", V2)
    assert out.state == "rolled_back", out.detail
    assert w.pointer("lane-a") == V1, "the previous pointer is restored"
    assert w.fly.restarts == ["hermes-lane-a"] * 3, "deploy restart, then a rollback restart"
    assert "pointer restored to " + V1 in out.detail and out.halts


def test_a_seat_with_no_previous_pointer_has_it_removed_on_rollback() -> None:
    w = World(["lane-a"])
    w.publish(V1)
    w.refuses.add("lane-a")
    out = w.run("lane-a", V1)
    assert out.state == "rolled_back"
    assert w.pointer("lane-a") is None and bundle_pointer_key("lane-a", "jobs") in w.r2.deletes
    assert "read-back of jobs image: pass" in out.detail


def test_a_failing_self_test_rolls_the_seat_back_on_an_image_that_carries_it() -> None:
    w = World(["lane-a"], caps={"schema_version": 1, "selftest": True})
    w.publish(V1)
    w.selftest_failed.add("lane-a")
    out = w.run("lane-a", V1)
    assert out.state == "rolled_back" and "failed" in out.detail
    assert w.pointer("lane-a") is None
    w.selftest_failed.clear()
    assert w.run("lane-a", V1).state == "done"


def test_a_busy_seat_is_never_restarted_and_its_pointer_never_written() -> None:
    w = World(["lane-a"])
    w.publish(V1)
    busy = {"lanes": [{"file": "medchron", "running": "job-1", "age_s": 5, "daemon_alive": True}],
            "children": [], "crons": [], "errors": []}  # fmt: skip
    w.fly.probe_facts["lane-a"] = [busy]
    out = w.run("lane-a", V1)
    assert out.state == "waiting_idle"
    assert w.fly.restarts == [] and w.pointer("lane-a") is None


def test_a_restart_that_fails_restores_the_pointer() -> None:
    w = World(["lane-a"])
    w.publish(V1)
    w.fly.restart_results = [Result(1, "", "machine not found")]
    out = w.run("lane-a", V1)
    assert out.state == "tooling_error" and w.pointer("lane-a") is None


def test_a_manifest_without_its_tarball_is_refused() -> None:
    w = World(["lane-a"])
    w.publish(V1)
    del w.r2.objects[bundle_tarball_key("jobs", V1)]
    with pytest.raises(ToolingError, match="tarball is not"):
        w.roll.load_manifest("jobs", V1)


def test_the_bundle_pointer_is_the_only_new_key_the_controller_may_write() -> None:
    assert POINTER_KEY_RE.match(bundle_pointer_key("ashton-price", "jobs"))
    assert not POINTER_KEY_RE.match("vaults/ashton-price/bundles/jobs.tar.zst")
    assert not POINTER_KEY_RE.match("vaults/ashton-price/bundles/../skills-pointer.json")


def test_the_judge_waits_on_a_stale_or_different_report() -> None:
    since = datetime(2026, 10, 31, tzinfo=timezone.utc)
    row = {**fresh_status(), "bundles_json": json.dumps({"jobs": V1})}
    assert judge_bundle_row(row, since, "jobs", V1).cls == "pass"  # type: ignore[union-attr]
    assert judge_bundle_row(row, since, "jobs", V2) is None
    assert judge_bundle_row({**row, "last_heartbeat_ts": "2026-10-30T00:00:00Z"}, since, "jobs", V1) is None
    assert judge_bundle_row({**row, "bundles_json": "not json"}, since, "jobs", V1) is None


# -- base(S), on real git -----------------------------------------------------------------
def _git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, dict[str, str]]:
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key)
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(tmp_path), "PATH": os.environ["PATH"]}  # fmt: skip

    def git(*args: str) -> str:
        out = subprocess.run(["git", "-C", str(tmp_path), *args], env=env, check=True, capture_output=True, text=True)  # noqa: S603 - argv list of fixed git args, never a shell
        return out.stdout.strip()

    for rel in (ordering.SUBSTRATE_PATHS, ordering.ARTIFACT_PATHS, ordering.DETECT_SCRIPT):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, tmp_path / rel)
    git("init", "-q", "-b", "main")
    shas: dict[str, str] = {}

    def commit(label: str, path: str) -> str:
        f = tmp_path / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(label)
        git("add", "-A")
        git("commit", "-q", "-m", label)
        shas[label] = git("rev-parse", "HEAD")
        return shas[label]

    commit("image-1", "operator/templates/Dockerfile")
    commit("skills-only", "operator/skills/x/SKILL.md")
    commit("docs-only", "docs/readme.md")
    commit("ci-only", ".github/workflows/deploy.yml")
    commit("runner", "operator/runners/medchron/x.py")
    commit("skills-after-runner", "operator/skills/y/SKILL.md")
    git("update-ref", "refs/remotes/origin/main", shas["skills-after-runner"])
    return git, shas


def test_base_of_s_skips_artifact_docs_and_ci_commits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FALSIFIER: base(S) = S (the pre-W3 rule) and a skills-only merge waits for an
    image that merge never builds; drop the artifact exclude and base(skills-only)
    is skills-only itself."""
    _, shas = _git_repo(tmp_path, monkeypatch)
    runner = CommandRunner()
    assert ordering.base_commit(runner, tmp_path, shas["skills-only"]) == shas["image-1"]
    assert ordering.base_commit(runner, tmp_path, shas["ci-only"]) == shas["image-1"]
    assert ordering.base_commit(runner, tmp_path, shas["runner"]) == shas["runner"]
    assert ordering.base_commit(runner, tmp_path, shas["skills-after-runner"]) == shas["runner"]
    assert ordering.base_commit(runner, tmp_path, "9" * 40) is None


def test_a_skills_only_merge_goes_to_a_seat_on_the_image_before_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the CLI's own reads (cli._skills_defer and cli._bundles_defer), on real git."""
    _, shas = _git_repo(tmp_path, monkeypatch)
    store = FakeStore()
    store.insert_release(release(release_id="img-202610310000-aaaaaaaa", ss_console_sha=shas["image-1"]))
    store.status = {"on-image-1": fresh_status()}
    ctx = cli.Ctx(tmp_path, CommandRunner(), store, FakeR2(), FakeFly())  # type: ignore[arg-type]
    skills = {"ss_console_sha": shas["skills-only"], "skills_version": "202610310100-" + shas["skills-only"][:8]}
    assert cli._skills_defer(ctx, ["on-image-1"])(skills) == {}
    later = {"ss_console_sha": shas["skills-after-runner"], "skills_version": "202610310200-abcdef12"}
    assert "need an image at or after" in cli._skills_defer(ctx, ["on-image-1"])(later)["on-image-1"]
    bundle = bundle_rel("202610310300-" + shas["runner"][:8], shas["runner"])
    assert "waiting for the image" in cli._bundles_defer(ctx, ["on-image-1"])(bundle)["on-image-1"]
    store.insert_release(release(release_id="img-202610310400-bbbbbbbb", image_ref="registry.fly.io/hermes-fleet:r",
                                 image_digest="sha256:" + "c" * 64, ss_console_sha=shas["runner"]))  # fmt: skip
    store.status = {"on-runner": fresh_status(image="registry.fly.io/hermes-fleet:r@sha256:" + "c" * 64)}
    assert cli._bundles_defer(ctx, ["on-runner"])(bundle) == {}


def test_a_seat_already_on_a_newer_bundle_is_not_moved_back() -> None:
    status = {"a": {**fresh_status(), "bundles_json": json.dumps({"jobs": V2})}}
    got = bundles.deferrals(bundle_rel(V1), ["a"], status, lambda ref: "1" * 40, lambda o, n: True, lambda s: s)
    assert "already runs newer bundle jobs" in got["a"]


# -- the CLI ----------------------------------------------------------------------------------
def test_bundle_record_writes_a_bundle_row_and_base_prefers_this_merges_image(
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = FakeStore()
    sha = "1" * 40
    store.insert_release(release(ss_console_sha=sha))
    ctx = cli.Ctx(REPO, CommandRunner(), store, FakeR2(), FakeFly(), log=lambda m: None)  # type: ignore[arg-type]
    assert (
        cli.main(["bundle-record", "--version", V1, "--sha", sha, "--image-ref", release()["image_ref"]], ctx=ctx) == 0
    )
    row = store.releases[f"bnd-{V1}"]
    assert row["kind"] == "bundle" and row["artifact_name"] == "jobs" and row["artifact_version"] == V1
    assert cli.main(["bundle-record", "--version", "nope", "--sha", sha, "--image-ref", "x"], ctx=ctx) == 2
    capsys.readouterr()
    assert cli.main(["bundle-base", "--sha", sha], ctx=ctx) == 0
    assert capsys.readouterr().out.strip() == f"{release()['image_ref']}@{DIGEST}"
    # No image built at this commit and none completed: refused, never a guess.
    assert cli.main(["bundle-base", "--sha", "2" * 40], ctx=ctx) == 4


def test_fleet_status_shows_what_each_seat_runs_its_bundles_at() -> None:
    from bin.lib.fleet import status

    store = FakeStore()
    store.status = {
        "lane-a": {**fresh_status(), "bundles_json": json.dumps({"jobs": V1})},
        "lane-b": {**fresh_status(), "bundles_json": json.dumps({"jobs": "image"})},
        "core-c": fresh_status(),
    }
    rows = status.seat_rows(store, [Ring(0, ["lane-a", "lane-b", "core-c"], None, 0)])  # type: ignore[arg-type]
    assert [r["bundles"] for r in rows] == [f"jobs={V1}", "jobs=image", "-"]
    table = status.render_table(rows).splitlines()
    assert "bundles" in table[0] and f"jobs={V1}" in table[1]
    assert status.bundles_cell("not json") == "?"
    rel = [{"release_id": f"bnd-{V1}", "kind": "bundle", "artifact_name": "jobs", "artifact_version": V1}]
    assert f"bundle jobs {V1}" in status.render_releases(rel, False)


def test_a_latest_tick_with_no_bundle_in_flight_waits(tmp_path: Path) -> None:
    ctx = cli.Ctx(tmp_path, CommandRunner(), FakeStore(), FakeR2(), FakeFly(), log=lambda m: None)  # type: ignore[arg-type]
    args = cli._parser().parse_args(["release", "latest", "--tick"])
    assert cli._bundles_tick(ctx, args) == TICK_WAITING
