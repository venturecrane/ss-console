"""The boot harness: its judge can FAIL on the boots that actually broke (Law 12),
passes a healthy one, and its targets and fixture can never reach a seat or a person."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from bin.lib.boot_harness import cli, collect, fixture, judge

OPERATOR = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "boot-harness"
PIN = "a" * 40
SITE = "/opt/hermes/.venv/lib/python3.14/site-packages/"
SUBS = judge.declared_subplugins(OPERATOR / "hermes-smd-overlay" / "plugin.yaml")
EXPECT = judge.Expect(hermes_sha=PIN, subplugins=SUBS, brotlicffi="1.2.1.0")
ALLOW = judge.load_allowlist(OPERATOR / "bin" / "lib" / "boot_harness" / "log-allowlist.yaml")

HEALTHY_LOG = "\n".join(
    [
        "2026-10-27T05:00:01Z [entrypoint] Cross-machine isolation boot check PASSED (ADR 0009 / SEC-22)",
        f"INFO hermes_smd.overlay: hermes-smd-overlay registered {len(SUBS)} sub-plugin(s): {', '.join(SUBS)}",
        "INFO hermes_smd.overlay.activation: SMD overlay ACTIVE + AUDITING on the live gateway: 9 hook type(s) in the "
        "live singleton, trust gate fired on banned 'email_send', audit row written (before=3 after=4) | cost breaker: armed",
        "2026-10-28 03:21:40,100 INFO gateway.run: Startup watchdog disarmed",
    ]
)


def _check(cid: str, ok: bool = True) -> dict[str, Any]:
    return {"id": cid, "ok": ok, "warn": False, "detail": ""}


def healthy_bundle() -> dict[str, Any]:
    return {
        "hermes_sha": PIN,
        "selftest": {
            "run_kind": "boot",
            "finished_at": "2026-10-27T05:06:00Z",
            "total": 3,
            "failed": [],
            "failed_count": 0,
            "warned_count": 0,
            "checks": [
                _check("customer-yaml-present"),
                _check("gateway-multiplex-standalone"),
                _check("broker-socket-answers-health"),
            ],
        },
        "gateway_processes": ["/opt/hermes/.venv/bin/python3 /opt/hermes/.venv/bin/hermes -p crane gateway run"],
        "gateway": {
            "pid": 812,
            "cmdline": "/opt/hermes/.venv/bin/python3 /opt/hermes/.venv/bin/hermes -p crane gateway run",
            "started_epoch": 1793157600.0,
            "env_present": ["HERMES_HOME", "SMD_GATEWAY_PID", "VIRTUAL_ENV"],
            "smd_gateway_pid_matches": True,
            "resolved": {
                "shared": SITE + "shared/__init__.py",
                "anthropic": SITE + "anthropic/__init__.py",
                "aiohttp": SITE + "aiohttp/__init__.py",
                "brotlicffi_version": "1.2.1.0",
            },
        },
        "plugins_enabled": {"/opt/data/profiles/crane/config.yaml": ["hermes-smd-overlay"]},
        "filesystem": {
            "opt_data_entries": ["audit", "profiles"],
            "opt_data_tools": False,
            "opt_data_installs": False,
            "install_facts_venv_keys": {},
        },
        "imports": {m: {"rc": 0, "detail": ""} for m in ("aiohttp_yaml_in_venv", *collect.OVERLAY_MODULES)},
        "http": {"gate_health": 200, "adapter_unsigned_handoff": 401},
        "cron": {
            "planted": True,
            "persona": "crane",
            "fired": True,
            "child": {"executable": "/opt/hermes/.venv/bin/python3", "version": "3.14.0", "env_present": ["PATH"]},
            "job": "{}",
        },
        "logs": {},
    }


def verdicts(bundle: dict[str, Any] | None, log: str, ready: float | None = 120.0) -> dict[str, judge.Verdict]:
    return {v.check: v for v in judge.judge(bundle, log, EXPECT, ALLOW, ready)}


def failed(bundle: dict[str, Any] | None, log: str) -> set[str]:
    return {c for c, v in verdicts(bundle, log).items() if not v.ok}


def test_a_healthy_boot_passes_every_check() -> None:
    got = verdicts(healthy_bundle(), HEALTHY_LOG)
    assert [c for c, v in got.items() if not v.ok] == [], [v.line() for v in got.values()]
    assert len(got) == 18


def test_the_real_known_good_capture_passes_every_check() -> None:
    """Calibration: rel-4cd01e8e-202610280041 (Hermes v2026.9.14, what every seat
    runs green) booted on the harness 2026-10-28. Any FAIL here is a harness defect."""
    bundle = json.loads((FIXTURES / "harness-rel-4cd01e8e-good.bundle.json").read_text(encoding="utf-8"))
    log = (FIXTURES / "harness-rel-4cd01e8e-good.machine.log").read_text(encoding="utf-8")
    # The image's own manifest, not today's: a sub-plugin added later must not fail this capture.
    at_image = tuple(s.strip() for s in judge.REGISTERED.findall(judge.all_logs(bundle, log))[-1][1].split(","))
    good = judge.Expect(hermes_sha="345cd2b057a452236de401d3534b8502a7465e8d", subplugins=at_image)
    got = judge.judge(bundle, log, good, ALLOW, 244.0)
    assert [v.line() for v in got if not v.ok] == []


# -- Law 12: the boots that broke on staging ---------------------------------------
def test_the_v0225_package_manager_boot_fails() -> None:
    """The REAL postmortem capture of rel-7e968373-202610280245 (the image that
    crash-looped staging), collected from a held harness Machine after its gateway
    died: Hermes downloaded its own Python and uv into /opt/data/tools, recorded a
    venv in installs/*/facts.json, and that env lacked yaml, aiohttp and shared."""
    raw = (FIXTURES / "harness-rel-7e968373-postmortem.bundle.json").read_text(encoding="utf-8")
    bundle = json.loads(raw)
    log = (FIXTURES / "harness-rel-7e968373-postmortem.machine.log").read_text(encoding="utf-8")
    real = judge.Expect(hermes_sha="bf0203a794a07aac3ff764972ab9087baa4e4f4e", subplugins=SUBS)
    got = {v.check: v for v in judge.judge(bundle, log, real, ALLOW, None)}
    assert sorted(c for c, v in got.items() if v.ok) == [
        "hermes-sha-pinned",
        "no-job-broker-refusals",
        "plugins-enabled-key-written",
    ]
    assert "aiohttp not installed" in got["webhook-adapter-up"].detail
    assert "yaml" in got["overlay-packages-importable"].detail
    assert "/opt/data/tools" in got["no-runtime-env-build"].detail


def test_the_rel_7e968373_shared_import_crash_loop_fails() -> None:
    """The REAL harness capture of the image that crash-looped staging: the Machine
    stopped with the gateway, so the collector got nothing, and every check that
    needs the boot to have happened fails, naming the two signals staging showed."""
    log = (FIXTURES / "harness-rel-7e968373.log").read_text(encoding="utf-8")
    got = verdicts(None, log, ready=None)
    # A log-only absence check may honestly pass on a dead boot.
    assert [c for c, v in got.items() if v.ok] == ["no-job-broker-refusals"]
    assert "shared" in got["overlay-packages-importable"].detail
    assert "ACTIVATION FAILED" in got["overlay-activation-complete"].detail
    assert "aiohttp not installed" in got["webhook-adapter-up"].detail


@pytest.mark.parametrize(
    ("mutate", "check"),
    [
        (lambda b: b.update(hermes_sha="b" * 40), "hermes-sha-pinned"),
        (lambda b: b["gateway_processes"].append("hermes -p other gateway run"), "gateway-single-profile-standalone"),
        (lambda b: b["gateway_processes"].__setitem__(0, "hermes gateway run"), "gateway-single-profile-standalone"),
        (
            lambda b: b["selftest"]["checks"].pop(1) and b["gateway"].update(multiplex_reasons={"/p": "multiplexing"}),
            "gateway-single-profile-standalone",
        ),
        (lambda b: b["selftest"].update(failed=["matter-mixing-fence"], failed_count=1), "seat-selftest-green"),
        (lambda b: b["selftest"].update(total=0), "seat-selftest-green"),
        (lambda b: b["imports"]["shared"].update(rc=1), "overlay-packages-importable"),
        (lambda b: b["imports"]["aiohttp_yaml_in_venv"].update(rc=1), "webhook-adapter-up"),
        (lambda b: b["http"].update(gate_health="ConnectionRefusedError"), "webhook-adapter-up"),
        (lambda b: b["http"].update(adapter_unsigned_handoff=202), "webhook-adapter-up"),
        (lambda b: b["filesystem"].update(install_facts_venv_keys={"/f.json": ["venv"]}), "no-runtime-env-build"),
        (lambda b: b["plugins_enabled"].update({"/p/config.yaml": []}), "plugins-enabled-key-written"),
        (
            lambda b: b["gateway"]["resolved"].update(shared="/opt/data/installs/x/shared/__init__.py"),
            "gateway-modules-resolve-in-venv",
        ),
        (lambda b: b["gateway"]["resolved"].update(brotlicffi_version="1.2.0.2"), "gateway-modules-resolve-in-venv"),
        (lambda b: b["gateway"]["env_present"].append("PYTHONPATH"), "gateway-env-and-cmdline"),
        (lambda b: b["gateway"]["env_present"].append("INVOCATION_ID"), "gateway-env-and-cmdline"),
        (lambda b: b["gateway"].update(cmdline="hermes gateway run"), "gateway-env-and-cmdline"),
        (lambda b: b["cron"]["child"].update(executable="/usr/bin/python3"), "cron-child-interpreter"),
        (lambda b: b["cron"].update(fired=False), "managed-cron-fires"),
        (lambda b: b["cron"].update(planted=False, detail="ImportError"), "managed-cron-fires"),
        (lambda b: b["selftest"]["checks"].pop(2), "broker-accepts-gateway-calls"),
    ],
)
def test_each_check_fails_on_its_own_signal(mutate: Any, check: str) -> None:
    bundle = copy.deepcopy(healthy_bundle())
    mutate(bundle)
    assert failed(bundle, HEALTHY_LOG) == {check}


@pytest.mark.parametrize(
    ("line", "check"),
    [
        ("ERROR overlay fan-out: hermes-smd-trust failed to register", "overlay-subplugins-registered"),
        ("audit row written (before=4 after=4)", "broker-accepts-gateway-calls"),
        ("ERROR something unexpected", "boot-log-clean"),
        ("WARNING job broker refused request: not the gateway", "no-job-broker-refusals"),
    ],
)
def test_log_signals_fail_their_check(line: str, check: str) -> None:
    assert check in failed(healthy_bundle(), HEALTHY_LOG + "\n" + line)


def test_no_selftest_result_fails_every_check_that_reads_it() -> None:
    bundle = healthy_bundle()
    bundle["selftest"] = None
    assert failed(bundle, HEALTHY_LOG) == {
        "seat-selftest-green",
        "broker-accepts-gateway-calls",
    }


def test_a_missing_subplugin_registration_fails() -> None:
    short = HEALTHY_LOG.replace(f", {SUBS[-1]}", "")
    assert "overlay-subplugins-registered" in failed(healthy_bundle(), short)


def test_virtual_env_is_required_only_when_the_candidate_exports_it() -> None:
    bundle = healthy_bundle()
    bundle["gateway"]["env_present"].remove("VIRTUAL_ENV")
    assert "gateway-env-and-cmdline" not in failed(bundle, HEALTHY_LOG)
    strict = judge.Expect(hermes_sha=PIN, subplugins=SUBS, brotlicffi="1.2.1.0", require_virtual_env=True)
    got = {v.check: v for v in judge.judge(bundle, HEALTHY_LOG, strict, ALLOW, 120.0)}
    assert not got["gateway-env-and-cmdline"].ok


def test_a_selftest_that_predates_the_standalone_check_falls_back_to_the_gateway_record() -> None:
    bundle = healthy_bundle()
    bundle["selftest"]["checks"].pop(1)
    assert "gateway-single-profile-standalone" not in failed(bundle, HEALTHY_LOG)


def test_installs_without_a_venv_entry_passes() -> None:
    bundle = healthy_bundle()
    bundle["filesystem"].update(opt_data_installs=True, install_facts_venv_keys={"/opt/data/installs/x/facts.json": []})
    assert "no-runtime-env-build" not in failed(bundle, HEALTHY_LOG)


def test_no_watchdog_disarm_line_fails() -> None:
    log = HEALTHY_LOG.replace("Startup watchdog disarmed", "")
    assert "startup-watchdog-disarmed" in failed(healthy_bundle(), log)


def test_boot_over_budget_fails() -> None:
    got = verdicts(healthy_bundle(), HEALTHY_LOG, ready=EXPECT.boot_budget_s + 1)
    assert not got["boot-ready"].ok


def test_profile_log_files_are_judged_too() -> None:
    bundle = healthy_bundle()
    bundle["logs"] = {"/opt/data/profiles/crane/logs/errors.log": "Webhook: aiohttp not installed"}
    assert "webhook-adapter-up" in failed(bundle, HEALTHY_LOG)


def test_an_allowlist_rule_needs_a_reason(tmp_path: Path) -> None:
    path = tmp_path / "allow.yaml"
    path.write_text(yaml.safe_dump({"allow": [{"pattern": "x"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="no reason"):
        judge.load_allowlist(path)


def test_an_allowlisted_line_is_tolerated(tmp_path: Path) -> None:
    path = tmp_path / "allow.yaml"
    path.write_text(yaml.safe_dump({"allow": [{"pattern": "dead endpoint", "reason": "harness R2 is a dead port"}]}))
    allow = judge.load_allowlist(path)
    log = HEALTHY_LOG + "\nERROR dead endpoint"
    assert judge.check_log_clean(log, allow).ok


# -- targets and fixture --------------------------------------------------------------
def test_every_seat_app_is_refused() -> None:
    apps = cli.seat_apps(OPERATOR)
    assert "hermes-smd-staging" in apps
    for app in apps:
        assert cli.refuse_target(app, OPERATOR) is not None
    assert cli.refuse_target("hermes-boot-harness", OPERATOR) is None
    assert cli.refuse_target("hermes-fleet", OPERATOR) is not None


def test_the_fixture_reaches_nobody_and_names_no_seat() -> None:
    text = fixture.render(OPERATOR)
    doc = yaml.safe_load(text)
    assert doc["customer_id"] == fixture.SLUG
    assert fixture.SOURCE_SLUG not in yaml.safe_dump(doc)
    assert fixture.reachable_addresses(doc) == []
    for key in ("connectors", "webhook_triggers", "mcp_connector"):
        assert key not in doc
    assert all("cron" not in p for p in doc["personas"])


def test_the_run_command_carries_no_real_secret() -> None:
    dummy = {k: "d" * 48 for k in cli.DUMMY_SECRETS}
    argv = cli.machine_run_argv("img", "hermes-boot-harness", "n", Path("/x/customer.yaml"), dummy)
    pairs = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    env = dict(p.split("=", 1) for p in pairs)
    assert env["R2_ENDPOINT_URL"] == cli.DEAD_ENDPOINT
    assert env["CUSTOMER_SLUG"] == fixture.SLUG
    assert set(cli.DUMMY_SECRETS) <= set(env)
    assert "<per-run-random>" in cli.masked(argv, dummy) and "d" * 48 not in cli.masked(argv, dummy)


def test_the_collector_refuses_off_the_harness(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.setattr(collect, "on_harness", lambda: False)
    assert collect.main() == 2
    assert "refused" in json.loads(capsys.readouterr().out)


def test_the_1gb_fixture_is_a_1gb_seat_that_reaches_nobody() -> None:
    text = fixture.render(OPERATOR, fixture.SOURCE_1GB)
    doc = yaml.safe_load(text)
    assert doc["customer_id"] == fixture.SLUG
    assert doc["machine"] == {"size": "shared-cpu-1x", "memory_mb": 1024}
    assert fixture.reachable_addresses(doc) == []
    for key in ("connectors", "webhook_triggers", "telegram", "record_stores"):
        assert key not in doc
    assert all("cron" not in p for p in doc["personas"])
    assert f"vaults/{fixture.SOURCE_1GB}/" not in yaml.safe_dump(doc)
    assert cli.SIZES["1x-1024"][0] == ("shared", 1, 1024)


# -- --volume: the cron store a seat volume carried survives the boot (audit 11) --------
def _store(*names: str) -> dict[str, list[dict[str, str]]]:
    return {"/opt/data/profiles/crane/cron/jobs.json": [{"id": str(i), "name": n} for i, n in enumerate(names)]}


def test_a_volume_boot_that_keeps_its_cron_store_passes() -> None:
    bundle = healthy_bundle()
    bundle["cron_before"] = _store("op-managed:a", "op-managed:b")
    bundle["cron_after"] = _store("op-managed:a", "op-managed:b", "op-managed:boot-harness-cron-probe")
    got = verdicts(bundle, HEALTHY_LOG)
    assert got["cron-store-survives-boot"].ok, got["cron-store-survives-boot"].detail


@pytest.mark.parametrize(
    "after",
    [_store("op-managed:a"), _store("op-managed:a", "op-managed:b", "op-managed:b"), {}],
)
def test_a_volume_boot_that_loses_or_duplicates_a_job_fails(after: dict[str, Any]) -> None:
    bundle = healthy_bundle()
    bundle["cron_before"] = _store("op-managed:a", "op-managed:b")
    bundle["cron_after"] = after
    assert not verdicts(bundle, HEALTHY_LOG)["cron-store-survives-boot"].ok


def test_a_changed_schedule_or_no_managed_job_when_routines_are_authored_fails() -> None:
    bundle = healthy_bundle()
    path = "/opt/data/profiles/crane/cron/jobs.json"
    bundle["cron_before"] = {path: [{"id": "1", "name": "op-managed:a", "schedule": "x"}]}
    bundle["cron_after"] = {path: [{"id": "2", "name": "op-managed:a", "schedule": "y"}]}
    assert not verdicts(bundle, HEALTHY_LOG)["cron-store-survives-boot"].ok
    bundle["cron_after"] = {path: [{"id": "2", "name": "op-managed:a", "schedule": "x"}]}
    assert verdicts(bundle, HEALTHY_LOG)["cron-store-survives-boot"].ok  # a new id is by design
    bundle["cron_before"] = bundle["cron_after"] = {path: []}
    strict = judge.Expect(hermes_sha=PIN, subplugins=SUBS, brotlicffi="1.2.1.0", require_managed_jobs=True)
    got = {v.check: v for v in judge.judge(bundle, HEALTHY_LOG, strict, ALLOW, 120.0)}
    assert not got["cron-store-survives-boot"].ok


def test_keep_routines_keeps_the_seats_routine_on_a_never_firing_slot() -> None:
    doc = yaml.safe_load(fixture.render(OPERATOR, fixture.SOURCE_1GB, keep_routines=True))
    crons = [c for p in doc["personas"] for c in p.get("cron") or []]
    assert crons and all(c["schedule"] == fixture.NEVER_IN_A_RUN for c in crons)
    assert fixture.reachable_addresses(doc) == []


def test_a_run_without_a_volume_carries_no_cron_store_line() -> None:
    assert "cron-store-survives-boot" not in verdicts(healthy_bundle(), HEALTHY_LOG)


def test_a_volume_run_mounts_it_and_ships_the_fixture_to_the_rootfs() -> None:
    argv = cli.machine_run_argv("img", "hermes-boot-harness", "n", Path("/x/customer.yaml"), {}, True, "hermes_state")
    assert "hermes_state:/opt/data" in argv
    assert "/harness/customer.yaml=/x/customer.yaml" in argv
    assert not any(a.startswith("/opt/data/") for a in argv)
