"""The fleet CLI's parts: rings, identifiers, classifiers, adapters, build helpers, CLI exits."""

from __future__ import annotations

import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest

from bin.lib.fleet import cli, ids
from bin.lib.fleet.build import ImageExtractor, dockerfile_arg, lane_inputs, required_env_from_arrays
from bin.lib.fleet.d1 import D1, FleetStore
from bin.lib.fleet.fly import classify_deploy, digest_from_build_output
from bin.lib.fleet.r2 import R2
from bin.lib.fleet.rings import load_rings, parallel_for
from bin.lib.fleet.runner import Result, ToolingError
from bin.lib.fleet.smoke import classify_smoke
from bin.tests.fleet_fakes import FakeFly, FakeR2, FakeStore, release

OPERATOR = Path(__file__).resolve().parents[2]
REPO = OPERATOR.parent


# -- rings ------------------------------------------------------------------------
def test_real_rings_file_puts_the_paying_seat_straight_after_staging() -> None:
    """2026-10-30 (Captain): one client, so its fixes go right after staging, not an hour behind our seats."""
    rings = load_rings(OPERATOR / "fleet/rings.yaml", OPERATOR / "customers")
    assert rings[0].seats == ["smd-staging"] and rings[0].bake_minutes == 0
    assert rings[1].seats == ["ashton-price"] and rings[1].bake_minutes == 0
    assert parallel_for(rings[-1], 8) == 1
    every = [s for r in rings for s in r.seats]
    assert len(every) == len(set(every))


def test_unknown_seat_joins_the_last_ring(tmp_path: Path) -> None:
    cust = tmp_path / "customers"
    for name in ("a-seat", "b-seat", "new-seat", "_template", "old.decommissioned.2026-01-01"):
        (cust / name).mkdir(parents=True)
        (cust / name / "customer.yaml").write_text("x: 1\n")
    rings_file = tmp_path / "rings.yaml"
    rings_file.write_text("rings:\n  - ring: 0\n    seats: [a-seat]\n  - ring: 1\n    seats: [b-seat]\n")
    rings = load_rings(rings_file, cust)
    assert rings[-1].seats == ["b-seat", "new-seat"]
    assert rings[-1].max_parallel == 1


def test_unprovisioned_seat_is_in_no_ring(tmp_path: Path) -> None:
    """A seat.yaml with fly_app: null has no app: no ring takes it, even one naming it.

    FALSIFIER: drop the unprovisioned filter from live_seats and the backstop puts
    new-seat back in the last ring, where a release would deploy to no app.
    """
    cust = tmp_path / "customers"
    for name in ("a-seat", "new-seat", "named-seat", "unreadable-seat"):
        (cust / name).mkdir(parents=True)
        (cust / name / "customer.yaml").write_text("x: 1\n")
    (cust / "a-seat" / "seat.yaml").write_text("fly_app: hermes-a-seat\n")
    for name in ("new-seat", "named-seat"):
        (cust / name / "seat.yaml").write_text("fly_app: null\nunprovisioned_reason: no app yet\n")
    (cust / "unreadable-seat" / "seat.yaml").write_text("fly_app: [unclosed\n")
    rings_file = tmp_path / "rings.yaml"
    rings_file.write_text("rings:\n  - ring: 0\n    seats: [a-seat, named-seat]\n")
    rings = load_rings(rings_file, cust)
    every = [s for r in rings for s in r.seats]
    assert "new-seat" not in every and "named-seat" not in every
    # A seat.yaml that cannot be read is NOT a reason to skip: the seat stays in a ring.
    assert every == ["a-seat", "unreadable-seat"]


def test_real_unprovisioned_seats_are_in_no_ring() -> None:
    rings = load_rings(OPERATOR / "fleet/rings.yaml", OPERATOR / "customers")
    every = {s for r in rings for s in r.seats}
    assert "ashton-price-marketing" not in every


def test_seat_in_two_rings_is_refused(tmp_path: Path) -> None:
    (tmp_path / "c" / "a-seat").mkdir(parents=True)
    f = tmp_path / "r.yaml"
    f.write_text("rings:\n  - ring: 0\n    seats: [a-seat]\n  - ring: 1\n    seats: [a-seat]\n")
    with pytest.raises(ValueError, match="two rings"):
        load_rings(f, tmp_path / "c")


def test_bake_minutes_is_read_per_ring_with_the_default_when_absent(tmp_path: Path) -> None:
    (tmp_path / "c" / "a-seat").mkdir(parents=True)
    (tmp_path / "c" / "a-seat" / "customer.yaml").write_text("x: 1\n")
    f = tmp_path / "r.yaml"
    f.write_text("rings:\n  - ring: 0\n    seats: [a-seat]\n    bake_minutes: 45\n  - ring: 1\n    seats: []\n")
    rings = load_rings(f, tmp_path / "c")
    assert [r.bake_minutes for r in rings] == [45, 10]
    f.write_text("rings:\n  - ring: 0\n    seats: [a-seat]\n    bake_minutes: soon\n")
    with pytest.raises(ValueError, match="bake_minutes"):
        load_rings(f, tmp_path / "c")
    # The real file states every ring's bake but the last.
    real = load_rings(OPERATOR / "fleet/rings.yaml", OPERATOR / "customers")
    assert all(r.bake_minutes >= 0 for r in real)


# -- identifiers -------------------------------------------------------------------
def test_sql_str_is_a_hex_literal_that_cannot_break_out() -> None:
    lit = ids.sql_str("x'); DROP TABLE fleet_releases; --")
    assert lit.startswith("CAST(x'") and "DROP" not in lit
    assert ids.sql_str(None) == "NULL"


@pytest.mark.parametrize("bad", ["UPPER", "a", "-lead", "trail-", "a b", "a;b", "../x"])
def test_slug_shape_refuses(bad: str) -> None:
    with pytest.raises(ValueError):
        ids.slug(bad)


def test_image_matches_by_digest_or_repo_tag() -> None:
    d = "sha256:" + "a" * 64
    target = f"{ids.FLEET_REGISTRY}:rel-1"
    assert ids.image_matches(f"{target}@{d}", target, d)
    assert not ids.image_matches(f"{target}@sha256:{'b' * 64}", target, d)
    assert ids.image_matches(target, target, d)
    assert not ids.image_matches("registry.fly.io/hermes-scott:deployment-1", target, d)
    assert not ids.image_matches(None, target, d)


# -- classifiers ------------------------------------------------------------------------
def test_classify_smoke_distinguishes_fail_from_no_verdict() -> None:
    assert classify_smoke(0, "all good").cls == "pass"
    ran = "[t] [smoke/x]   FAILED: matter-mixing-fence — command failed: probe\n"
    assert classify_smoke(1, ran).cls == "fail"
    nov = "[t] [smoke/x]   FAILED: a — no verdict from the Machine on two attempts (ssh failed): c\n"
    assert classify_smoke(1, nov).cls == "tooling_error"
    assert classify_smoke(1, "[t] [smoke/x] FATAL: machine-state-started — state=stopped").cls == "fail"
    assert classify_smoke(1, "Run ENDED EARLY at an unguarded error").cls == "tooling_error"


def test_classify_deploy() -> None:
    assert classify_deploy(Result(0, "", "")) == "ok"
    assert classify_deploy(Result(1, "", "Error: timeout reached waiting for health checks to pass")) == "fail"
    assert classify_deploy(Result(1, "", "Error: context deadline exceeded")) == "tooling_error"
    assert classify_deploy(Result(1, "", "something nobody has seen")) == "tooling_error"


def test_digest_from_build_output() -> None:
    out = "image: registry.fly.io/hermes-fleet:rel-x\nimage size: 2 GB\n--> digest: sha256:" + "f" * 64 + "\n"
    assert digest_from_build_output(out) == "sha256:" + "f" * 64
    assert digest_from_build_output("no digest here") is None


# -- adapters ------------------------------------------------------------------------------
class ScriptRunner:
    def __init__(self, *results: Result) -> None:
        self.results = list(results)
        self.calls: list[list[str]] = []

    def run(self, argv, *, input_text=None, timeout=120, cwd=None, env=None):
        self.calls.append(list(argv))
        return self.results.pop(0)


def test_d1_parses_rows_and_turns_failure_into_tooling_error() -> None:
    ok = Result(0, json.dumps([{"results": [{"customer_slug": "x"}], "success": True}]), "")
    store = FleetStore(D1(ScriptRunner(ok), "/repo"))
    assert store.seat_status("xx") == {"customer_slug": "x"}
    with pytest.raises(ToolingError):
        FleetStore(D1(ScriptRunner(Result(1, "", "Authentication error")), "/repo")).seat_status("xx")
    with pytest.raises(ToolingError):
        FleetStore(
            D1(ScriptRunner(Result(0, json.dumps([{"success": False, "error": "no such table"}]), "")), "/repo")
        ).holds()


def test_d1_statement_inlines_values_as_hex() -> None:
    runner = ScriptRunner(Result(0, "[]", ""))
    FleetStore(D1(runner, "/repo")).hold("smd-staging", "it's a demo", "captain")
    sql = runner.calls[0][-1]
    assert "it's" not in sql and "'smd-staging'" not in sql and "CAST(x'" in sql


def test_r2_refuses_writes_outside_the_fleet_key_spaces() -> None:
    r2 = R2(ScriptRunner(), {"R2_ENDPOINT_URL": "https://x", "R2_ACCESS_KEY_ID": "k", "R2_SECRET_ACCESS_KEY": "s"})
    with pytest.raises(ValueError):
        r2.put_bytes("vaults/scott/customer.yaml", b"x")
    with pytest.raises(ValueError):
        r2.delete("vaults/scott/customer.yaml")


def test_r2_without_credentials_is_a_tooling_error() -> None:
    with pytest.raises(ToolingError, match="infisical"):
        R2(ScriptRunner(), {}).get_bytes("fleet/skills/x.json")


# -- build helpers ------------------------------------------------------------------------
def test_dockerfile_pins_read_from_the_real_dockerfile() -> None:
    text = (OPERATOR / "templates/Dockerfile").read_text()
    assert ids.SHA_RE.match(dockerfile_arg(text, "HERMES_UPSTREAM_SHA") or "")
    # No overlay pin since the fold (ADR 0095): the overlay is built from this tree.
    assert dockerfile_arg(text, "OVERLAY_REF") is None
    assert "operator/hermes-smd-overlay/" in text


def test_required_env_from_the_real_generated_arrays() -> None:
    req = required_env_from_arrays((OPERATOR / "templates/_env-arrays.generated.sh").read_text())
    assert "ANTHROPIC_API_KEY" in req and "AGENTMAIL_API_KEY" not in req


def test_lane_inputs_digest_every_lane_schema() -> None:
    assert set(lane_inputs(REPO)) == {"medchron", "demand", "drafting", "litigation", "casefile"}


def test_volume_id_prefers_the_attached_volume_over_an_orphan() -> None:
    from bin.lib.fleet.fly import Fly

    vols = [
        {"id": "vol_orphan", "name": "hermes_state", "attached_machine_id": None},
        {"id": "vol_live", "name": "hermes_state", "attached_machine_id": "812e65f9411798"},
    ]
    assert Fly(ScriptRunner(Result(0, json.dumps(vols), "")), ".").volume_id("hermes-x") == "vol_live"
    both = [dict(v, attached_machine_id="m") for v in vols]
    with pytest.raises(ToolingError):
        Fly(ScriptRunner(Result(0, json.dumps(both), "")), ".").volume_id("hermes-x")


def _layer(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_extractor_reads_artifacts_from_small_layers_newest_first(monkeypatch: pytest.MonkeyPatch) -> None:
    layers = {
        "sha256:l1": _layer({"app/image-manifest.json": b"OLD"}),
        "sha256:l2": _layer(
            {"app/image-manifest.json": b'{"new": 1}', "app/_env-arrays.generated.sh": b"REQUIRED_ENV=(\n  A\n)\n"}
        ),
        "sha256:big": b"",
        "sha256:l3": _layer({"app/image-capabilities.json": b"{}"}),
    }
    manifest = {
        "layers": [
            {"digest": "sha256:l1", "size": 10},
            {"digest": "sha256:l2", "size": 10},
            {"digest": "sha256:l3", "size": 10},
            {"digest": "sha256:big", "size": 10**10},
        ]
    }
    ex = ImageExtractor(ScriptRunner(Result(0, "tok", "")))
    monkeypatch.setattr(ex, "_manifest", lambda repo, digest: json.dumps(manifest).encode())
    monkeypatch.setattr(ex, "_blob", lambda ref: layers[ref.split("@", 1)[1]])
    found = ex.extract(f"{ids.FLEET_REGISTRY}:rel-1@sha256:" + "a" * 64)
    assert found["image-manifest.json"] == b'{"new": 1}'
    assert set(found) == {"image-manifest.json", "image-capabilities.json", "_env-arrays.generated.sh"}


def test_extractor_waits_out_registry_propagation(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 404 on the manifest right after a push is propagation, not absence:
    the build retries on the schedule and reads the image once it is visible.
    2026-10-23: the first folded image recorded no capabilities on one GET."""
    from bin.lib.fleet.build import MANIFEST_RETRY_DELAYS_S, RegistryNotReady

    manifest = {"layers": [{"digest": "sha256:l1", "size": 10}]}
    answers = [RegistryNotReady("registry GET /v2/x/manifests/sha256:a returned HTTP 404"), None, None]
    slept: list[float] = []
    logged: list[str] = []
    ex = ImageExtractor(ScriptRunner(Result(0, "tok", "")), sleep=slept.append, log=logged.append)

    def manifest_or_404(repo: str, digest: str) -> bytes:
        exc = answers.pop(0)
        if exc is not None:
            raise exc
        return json.dumps(manifest).encode()

    monkeypatch.setattr(ex, "_manifest", manifest_or_404)
    monkeypatch.setattr(ex, "_blob", lambda ref: _layer({"app/image-capabilities.json": b"{}"}))
    found = ex.extract(f"{ids.FLEET_REGISTRY}:rel-1@sha256:" + "a" * 64)
    assert found == {"image-capabilities.json": b"{}"}
    assert slept == [MANIFEST_RETRY_DELAYS_S[0]] and len(logged) == 1 and "retrying" in logged[0]


def test_extractor_gives_up_after_the_retry_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falsifier: a digest the registry never shows exhausts the schedule and
    raises a ToolingError (which upload_artifacts records as 'without
    capabilities'); any non-404 failure is raised at once with no retry."""
    from bin.lib.fleet.build import MANIFEST_RETRY_DELAYS_S, RegistryNotReady

    slept: list[float] = []
    ex = ImageExtractor(ScriptRunner(Result(0, "tok", "")), sleep=slept.append)
    monkeypatch.setattr(ex, "_manifest", lambda repo, digest: (_ for _ in ()).throw(RegistryNotReady("HTTP 404")))
    with pytest.raises(ToolingError, match="HTTP 404 after 5 attempts over 225s"):
        ex.extract(f"{ids.FLEET_REGISTRY}:rel-1@sha256:" + "b" * 64)
    assert slept == list(MANIFEST_RETRY_DELAYS_S)

    slept.clear()
    monkeypatch.setattr(ex, "_manifest", lambda repo, digest: (_ for _ in ()).throw(ToolingError("HTTP 503")))
    with pytest.raises(ToolingError, match="HTTP 503"):
        ex.extract(f"{ids.FLEET_REGISTRY}:rel-1@sha256:" + "b" * 64)
    assert slept == []


def test_tag_digest_is_the_sha256_of_the_manifest_the_registry_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    import hashlib

    body = b'{"schemaVersion": 2, "layers": []}'
    asked: list[tuple[str, str]] = []
    ex = ImageExtractor(ScriptRunner(Result(0, "tok", "")))
    monkeypatch.setattr(ex, "_manifest", lambda repo, ref: asked.append((repo, ref)) or body)
    assert ex.tag_digest(f"{ids.FLEET_REGISTRY}:rel-1") == "sha256:" + hashlib.sha256(body).hexdigest()
    assert asked == [(ids.FLEET_REGISTRY, "rel-1")]
    with pytest.raises(ToolingError, match="not a tagged image reference"):
        ex.tag_digest(f"{ids.FLEET_REGISTRY}@sha256:" + "a" * 64)


class _TagExtractor:
    def __init__(self, answer: str | Exception) -> None:
        self.answer = answer

    def tag_digest(self, image_ref: str) -> str:
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _builder(answer: str | Exception, logged: list[str]):
    from bin.lib.fleet.build import Builder

    return Builder(Path("."), None, None, None, None, _TagExtractor(answer), logged.append)  # type: ignore[arg-type]


def test_the_build_records_the_registrys_digest_over_the_build_log() -> None:
    """2026-10-25: the build log's last sha256 was not the pushed image; the
    release recorded a digest the registry 404s and skills releases refused."""
    reg, log_digest = "sha256:" + "2" * 64, "sha256:" + "b" * 64
    logged: list[str] = []
    assert _builder(reg, logged).registry_digest("r:t", log_digest) == reg
    assert any("not the registry's" in m for m in logged)
    logged.clear()
    assert _builder(reg, logged).registry_digest("r:t", reg) == reg and logged == []
    assert _builder(reg, logged).registry_digest("r:t", None) == reg


def test_an_unreadable_registry_falls_back_to_the_build_log_and_says_so() -> None:
    logged: list[str] = []
    printed = "sha256:" + "c" * 64
    assert _builder(ToolingError("HTTP 404 after 5 attempts"), logged).registry_digest("r:t", printed) == printed
    assert any("could not read" in m for m in logged)


# -- render-fly-toml ------------------------------------------------------------------------
def test_render_fly_toml_sourced_mode_renders_every_token(tmp_path: Path) -> None:
    out = tmp_path / "fly.toml"
    script = (
        f"SLUG=probe-seat FLY_REGION=lax MACHINE_SIZE=shared-cpu-2x MEMORY_MB=4096 MACHINE_CPU_KIND=shared "
        f'MACHINE_CPUS=2 R2_BUCKET_CONFIG=smd-customer-config TEMPLATE_DIR="{OPERATOR}/templates"; '
        f'source "{OPERATOR}/bin/lib/render-fly-toml.sh"; render_fly_toml "{out}"'
    )
    subprocess.run(["bash", "-c", script], check=True, timeout=30)  # noqa: S603 - literal bash argv; the script is this test's own constant text
    text = out.read_text()
    assert 'app = "hermes-probe-seat"' in text and "kill_timeout = 300" in text and 'kill_signal = "SIGTERM"' in text
    assert 'R2_SKILL_BODIES_BUCKET = "ss-operator-probe-seat-skills"' in text and "cpus = 2" in text


def test_render_fly_toml_refuses_a_bad_value(tmp_path: Path) -> None:
    script = (
        f'SLUG="evil/../x" FLY_REGION=lax MACHINE_SIZE=shared-cpu-2x MEMORY_MB=4096 MACHINE_CPU_KIND=shared '
        f'MACHINE_CPUS=2 R2_BUCKET_CONFIG=b TEMPLATE_DIR="{OPERATOR}/templates"; '
        f'source "{OPERATOR}/bin/lib/render-fly-toml.sh"; render_fly_toml "{tmp_path}/f.toml"'
    )
    assert subprocess.run(["bash", "-c", script], timeout=30, capture_output=True).returncode != 0  # noqa: S603 - literal bash argv; the script is this test's own constant text


# -- CLI ------------------------------------------------------------------------------------
def _ctx(store: FakeStore) -> cli.Ctx:
    return cli.Ctx(REPO, ScriptRunner(), store, FakeR2(), FakeFly(), log=lambda m: None)  # type: ignore[arg-type]


def test_cli_second_release_while_lease_held_exits_4() -> None:
    store = FakeStore()
    rel = release()
    store.insert_release(rel)
    store.lease_row = {"lease_name": "rollout", "holder": "other", "release_id": "img-zzzzzzzz", "expires_at": "2099"}
    assert cli.main(["release", rel["release_id"]], ctx=_ctx(store)) == 4
    assert store.rollouts == {}


def test_cli_unknown_release_and_bad_ids_refuse() -> None:
    store = FakeStore()
    assert cli.main(["release", "img-doesnotexist"], ctx=_ctx(store)) == 4
    assert cli.main(["release", "not a release"], ctx=_ctx(store)) == 2
    assert cli.main(["release"], ctx=_ctx(store)) == 4


def test_cli_hold_unhold_and_latest_image(capsys: pytest.CaptureFixture[str]) -> None:
    store = FakeStore()
    assert cli.main(["hold", "scott", "--reason", "client demo"], ctx=_ctx(store)) == 0
    assert store.holds_rows["scott"]["reason"] == "client demo"
    assert cli.main(["unhold", "scott"], ctx=_ctx(store)) == 0 and "scott" not in store.holds_rows
    assert cli.main(["latest-image"], ctx=_ctx(store)) == 1
    rel = release(completed_last_ring_at="2026-10-19T01:00:00Z")
    store.insert_release(rel)
    capsys.readouterr()
    assert cli.main(["latest-image"], ctx=_ctx(store)) == 0
    assert capsys.readouterr().out.strip() == rel["image_ref"]


def test_cli_seat_image_reads_the_machine_and_names_a_new_seat(capsys: pytest.CaptureFixture[str]) -> None:
    ctx = _ctx(FakeStore())
    assert cli.main(["seat-image", "scott"], ctx=ctx) == 0
    assert capsys.readouterr().out.strip() == "registry.fly.io/hermes-scott:deployment-OLD"
    ctx.fly.no_machine.add("hermes-scott")  # type: ignore[attr-defined]
    assert cli.main(["seat-image", "scott"], ctx=ctx) == 1  # an app with no Machine yet is a new seat
    assert capsys.readouterr().out == ""


def test_cli_seat_image_could_not_tell_is_not_new_seat() -> None:
    class DownFly(FakeFly):
        def machines(self, app: str) -> list[dict]:
            raise ToolingError("fly machines list timed out")

    ctx = cli.Ctx(REPO, ScriptRunner(), FakeStore(), FakeR2(), DownFly(), log=lambda m: None)  # type: ignore[arg-type]
    assert cli.main(["seat-image", "scott"], ctx=ctx) == 3


def test_cli_with_seat_lease_passes_the_exit_code_and_always_releases() -> None:
    store = FakeStore()
    assert cli.main(["with-seat-lease", "scott", "--", "true"], ctx=_ctx(store)) == 0
    assert cli.main(["with-seat-lease", "scott", "--", "sh", "-c", "exit 7"], ctx=_ctx(store)) == 7
    assert "seat:scott" not in store.leases
    store.leases["seat:scott"] = {"lease_name": "seat:scott", "holder": "a-rollout", "release_id": "img-x"}
    assert cli.main(["with-seat-lease", "scott", "--", "true"], ctx=_ctx(store)) == 4
    assert store.leases["seat:scott"]["holder"] == "a-rollout"
    assert cli.main(["with-seat-lease", "scott"], ctx=_ctx(store)) == 2


def _done(seat: str, ring: int):
    from bin.lib.fleet.rollout import SeatOutcome

    return SeatOutcome(seat, "done", "ok")


def _waves_args(only: str | None) -> object:
    import argparse

    return argparse.Namespace(only=only, dry_run=False, parallel=1)


def test_cli_only_runs_beside_a_full_rollout_and_never_takes_the_fleet_lease() -> None:
    store = FakeStore()
    rel = release()
    store.insert_release(rel)
    store.lease_row = {"lease_name": "rollout", "holder": "full", "release_id": "img-zzzzzzzz", "expires_at": "2099"}
    seats: list[str] = []

    def do_seat(seat: str, ring: int):
        store.start_rollout({"rollout_id": f"r-{seat}", "release_id": rel["release_id"], "customer_slug": seat,
                             "ring": ring})  # fmt: skip
        store.update_rollout(f"r-{seat}", "done")
        seats.append(seat)
        return _done(seat, ring)

    assert cli._run_waves(_ctx(store), _waves_args("scott"), rel, do_seat, "fixer") == 0  # type: ignore[arg-type]
    assert seats == ["scott"] and store.lease_row["holder"] == "full"


def test_cli_completion_is_stamped_once_from_the_records() -> None:
    store = FakeStore()
    rel = release()
    store.insert_release(rel)
    every = [s for r in load_rings(OPERATOR / "fleet/rings.yaml", OPERATOR / "customers") for s in r.seats]

    def do_seat(seat: str, ring: int):
        store.start_rollout({"rollout_id": f"r-{seat}-{len(store.rollouts)}", "release_id": rel["release_id"],
                             "customer_slug": seat, "ring": ring})  # fmt: skip
        store.update_rollout(f"r-{seat}-{len(store.rollouts) - 1}", "done")
        return _done(seat, ring)

    for seat in every[:-1]:
        cli._run_waves(_ctx(store), _waves_args(seat), rel, do_seat, "h")  # type: ignore[arg-type]
    assert store.completed == []
    cli._run_waves(_ctx(store), _waves_args(every[-1]), rel, do_seat, "h")  # type: ignore[arg-type]
    assert store.completed == [rel["release_id"]]
    cli._run_waves(_ctx(store), _waves_args(every[0]), store.release(rel["release_id"]), do_seat, "h")  # type: ignore[arg-type]
    assert store.completed == [rel["release_id"]]  # never re-stamped


def test_cli_dry_run_summary_claims_nothing_about_the_release() -> None:
    # 2026-10-21: a dry run of a release done on every seat printed "release
    # not yet done on every seat". A dry run records nothing, so it reads no
    # completion and must say only that it changed nothing.
    import argparse

    store = FakeStore()
    rel = release(completed_last_ring_at="2026-10-22T01:33:34Z")
    store.insert_release(rel)
    logs: list[str] = []
    ctx = cli.Ctx(REPO, ScriptRunner(), store, FakeR2(), FakeFly(), log=logs.append)  # type: ignore[arg-type]
    args = argparse.Namespace(only="scott", dry_run=True, parallel=1)
    assert cli._run_waves(ctx, args, rel, _done, "h") == 0  # type: ignore[arg-type]
    assert logs[-1] == "dry run: nothing deployed, nothing recorded"
    assert not any("not yet done" in line for line in logs)
    assert store.rollouts == {} and store.completed == []


def test_cli_busy_exit_codes() -> None:
    store = FakeStore()
    ctx = _ctx(store)
    assert cli.main(["busy", "scott"], ctx=ctx) == 0
    ctx.fly.probe_facts["scott"] = [
        {"lanes": [], "children": [{"file": "child.pid", "pid": "4", "alive": True}], "crons": [], "errors": []}
    ]  # type: ignore[attr-defined]
    assert cli.main(["busy", "scott"], ctx=ctx) == 1
    ctx.fly.ssh_raises.add("scott")  # type: ignore[attr-defined]
    assert cli.main(["busy", "scott"], ctx=ctx) == 3


def test_launcher_runs() -> None:
    proc = subprocess.run([str(OPERATOR / "bin/fleet"), "--help"], capture_output=True, text=True, timeout=120)  # noqa: S603 - the repo's own launcher, literal argv
    assert proc.returncode == 0 and "release" in proc.stdout


# -- status: the identifier-gate rollback lever (ADR 0093) --------------------------
def test_status_names_a_seat_carrying_the_identifier_gate_lever() -> None:
    from bin.lib.fleet import status
    from bin.lib.fleet.rings import Ring

    class OneBrokenFly(FakeFly):
        def secret_names(self, app: str) -> tuple[set[str], set[str]]:
            if app == "hermes-c-seat":
                raise ToolingError("fly secrets list failed")
            return super().secret_names(app)

    fly = OneBrokenFly()
    fly.secrets["hermes-a-seat"] = ({"ANTHROPIC_API_KEY", status.LEVER}, set())
    fly.secrets["hermes-d-seat"] = (set(), {status.LEVER})  # staged counts too
    levers = status.lever_flags(fly, ["a-seat", "b-seat", "c-seat", "d-seat"])
    # Unreadable is None, never False: unknown must not read as unset.
    assert levers == {"a-seat": True, "b-seat": False, "c-seat": None, "d-seat": True}

    rows = status.seat_rows(FakeStore(), [Ring(1, ["a-seat", "b-seat", "c-seat"])], levers)
    assert [r["identifier_gate_lever"] for r in rows] == [True, False, None]
    table = status.render_table(rows).splitlines()
    assert table[0].rstrip().endswith("id-lever")
    assert [line.rstrip().rsplit(" ", 1)[-1] for line in table[1:]] == ["SET", "-", "?"]
