"""`operator/bin/boot-harness`: see the package docstring for the contract.

Exit codes: 0 every check passed; 1 at least one check failed; 2 usage or a
refused target; 3 tooling error (no verdict: fly could not be driven).

`run` is the only subcommand that creates anything, and it creates exactly one
Machine in an app whose name must start with ``hermes-boot-harness`` and must be
no seat's ``fly_app`` (operator/customers/*/seat.yaml). It never touches D1, R2
or Infisical: every secret the boot requires is a per-run random dummy, and the
R2 endpoint is a dead loopback port, so the boot reaches no external system.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

from bin.lib.boot_harness import fixture, judge
from bin.lib.fleet.build import dockerfile_arg
from bin.lib.fleet.runner import CommandRunner, Runner, ToolingError

OPERATOR = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
APP_RE = re.compile(r"^hermes-boot-harness(-[a-z0-9]+)?$")
DEAD_ENDPOINT = "http://127.0.0.1:9"
# The seat's own [env] (operator/templates/fly.toml.template), harness-slugged.
PLAIN_ENV = {
    "HERMES_HOME": "/opt/data",
    "CUSTOMER_SLUG": fixture.SLUG,
    "SMD_CUSTOMER_SLUG": fixture.SLUG,
    "HERMES_LOG_LEVEL": "info",
    "R2_BUCKET_CONFIG": "smd-customer-config",
    "R2_SKILL_BODIES_BUCKET": f"ss-operator-{fixture.SLUG}-skills",
    "SMD_D1_AUDIT_BINDING": "/opt/data/audit/audit.db",
    "SMD_D1_AGENT_STATE_BINDING": "/opt/data/agent-state.db",
    "SMD_AUDIT_BROKER_SOCKET": "/run/smd-workspace-broker/broker.sock",
    "R2_ENDPOINT_URL": DEAD_ENDPOINT,
    # Harness-only: one attempt per aws call against the dead endpoint, so the
    # boot's many fail-soft R2 fetches cost seconds, not minutes, of retries.
    "AWS_MAX_ATTEMPTS": "1",
}
DUMMY_SECRETS = ("ANTHROPIC_API_KEY", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "WEBHOOK_SECRET_MCP")
# Each run mode: the guest it boots and the seat whose customer.yaml the fixture
# derives from, so the config's machine block (which the self-test reads) matches
# the Machine. 2x-4096 is smd-staging's size; 1x-1024 is ring 2's (scott,
# brokery, captain-mark), the size of the 2026-09-01 startup-watchdog crash loop.
SIZES = {
    "2x-4096": (("shared", 2, 4096), fixture.SOURCE_SLUG),
    "1x-1024": (("shared", 1, 1024), fixture.SOURCE_1GB),
}
GUEST = SIZES["2x-4096"][0]
POLL_S = 15.0


def seat_apps(operator: Path = OPERATOR) -> set[str]:
    apps = set()
    for seat in (operator / "customers").glob("*/seat.yaml"):
        app = (yaml.safe_load(seat.read_text(encoding="utf-8")) or {}).get("fly_app")
        if app:
            apps.add(str(app))
    return apps


def refuse_target(app: str, operator: Path = OPERATOR) -> str | None:
    if not APP_RE.match(app):
        return f"app {app!r} is not a boot-harness app (must match {APP_RE.pattern})"
    if app in seat_apps(operator):
        return f"app {app!r} is a seat's fly_app; the harness never runs on a seat"
    return None


IMAGE_SHA_RE = re.compile(r":rel-([0-9a-f]{7,40})-")


def source_text(rel: str, ref: str | None, operator: Path = OPERATOR) -> str:
    """A repo file as the candidate image's commit has it (the checkout's copy without a ref)."""
    if not ref:
        return (operator / rel).read_text(encoding="utf-8")
    res = CommandRunner().run(["git", "-C", str(operator.parent), "show", f"{ref}:operator/{rel}"], timeout=60)
    if not res.ok:
        raise ToolingError(f"cannot read operator/{rel} at {ref}: {res.stderr.strip()[:200]}")
    return res.stdout


def expectations(args: argparse.Namespace, operator: Path = OPERATOR) -> judge.Expect:
    """What the CANDIDATE pins: read at the commit its tag names, so a known-good
    image built from an older commit is judged against its own pin and manifest."""
    ref = getattr(args, "source_ref", None)
    found = IMAGE_SHA_RE.search(getattr(args, "image", "") or "")
    ref = ref or (found.group(1) if found else None)
    pin = args.expected_hermes_sha or dockerfile_arg(
        source_text("templates/Dockerfile", ref, operator), "HERMES_UPSTREAM_SHA"
    )
    subs = judge.subplugins_from_text(source_text("hermes-smd-overlay/plugin.yaml", ref, operator))
    return judge.Expect(
        hermes_sha=pin,
        subplugins=subs,
        boot_budget_s=args.boot_budget,
        brotlicffi=security_pin(ref, operator),
        require_virtual_env=exports_virtual_env(ref, operator),
        require_managed_jobs=bool(getattr(args, "keep_routines", False)),
    )


LAUNCH_PATH = ("templates/entrypoint.sh", "templates/bootstrap.sh", "runners/supervisor/entrypoint-supervisor.sh")


def exports_virtual_env(ref: str | None, operator: Path = OPERATOR) -> bool:
    """Does the candidate's own launch path export VIRTUAL_ENV? Required only then:
    the known-good v2026.9.14 image exports none and runs green on every seat."""
    for rel in LAUNCH_PATH:
        try:
            if re.search(r"^\s*export VIRTUAL_ENV=", source_text(rel, ref, operator), re.MULTILINE):
                return True
        except (OSError, ToolingError):
            continue
    return False


def security_pin(ref: str | None, operator: Path = OPERATOR) -> str | None:
    """brotlicffi as the candidate's Hermes security override pins it, or None if it pins none."""
    try:
        text = source_text("requirements/hermes-security.txt", ref, operator)
    except (OSError, ToolingError):
        return None
    found = re.search(r"^brotlicffi==([0-9][0-9A-Za-z.]*)", text, re.MULTILINE)
    return found.group(1) if found else None


def machine_run_argv(
    image: str,
    app: str,
    name: str,
    fixture_path: Path,
    dummy: dict[str, str],
    hold: bool = False,
    volume: str | None = None,
    guest: tuple[str, int, int] = GUEST,
) -> list[str]:
    argv = ["fly", "machine", "run", image, "-a", app, "--name", name, "--region", "lax", "--restart", "no"]
    argv += ["--vm-cpu-kind", guest[0], "--vm-cpus", str(guest[1]), "--vm-memory", str(guest[2]), "--detach"]
    argv += ["--file-local", f"/harness/collect.py={HERE / 'collect.py'}"]
    if volume:
        # A seat-like volume mounts over /opt/data, so the fixture goes to the
        # rootfs and the --postmortem prelude copies it onto the volume, where the
        # entrypoint's legacy migration adopts it exactly as it does without one.
        argv += ["-v", f"{volume}:/opt/data", "--file-local", f"/harness/customer.yaml={fixture_path}"]
        argv += _env_args(dummy) + (["--entrypoint", "/bin/sleep", "--", "infinity"] if hold else [])
        return argv
    argv += ["--file-local", f"/opt/data/customer.yaml={fixture_path}"]
    # A seat's voice-vault mirror exists before its first failed sync; the harness
    # volume is fresh and its R2 is dead. Without this directory bootstrap.sh's
    # failed-sync branch (`_voice_prev_n="$(find .../voice/cohort ... | wc -l)"`
    # under `set -euo pipefail`) exits 1 with no log line: found by the first
    # calibration run of rel-4cd01e8e-202610280041. A seat defect too (a fresh
    # seat whose first voice fetch fails dies the same way), reported, not fixed here.
    argv += ["--file-local", f"/opt/data/voice/cohort/.harness={fixture_path.parent / 'empty'}"]
    argv += _env_args(dummy)
    if hold:
        # Debug only: the Machine idles instead of booting, so a person can run
        # /app/entrypoint.sh by hand over ssh and read every line it prints.
        argv += ["--entrypoint", "/bin/sleep", "--", "infinity"]
    return argv


def _env_args(dummy: dict[str, str]) -> list[str]:
    out: list[str] = []
    for key, value in {**PLAIN_ENV, **dummy}.items():
        out += ["-e", f"{key}={value}"]
    return out


def masked(argv: list[str], dummy: dict[str, str]) -> str:
    text = " ".join(argv)
    for value in dummy.values():
        text = text.replace(value, "<per-run-random>")
    return text


def _ssh(app: str, machine: str, command: str) -> list[str]:
    return ["fly", "ssh", "console", "-a", app, "--machine", machine, "-C", command]


def _machine_id(text: str) -> str | None:
    m = re.search(r"Machine ID:\s*([0-9a-f]{10,})", text) or re.search(r"\b([0-9a-f]{14})\b", text)
    return m.group(1) if m else None


def _state(runner: Runner, app: str, machine: str) -> str:
    res = runner.run(["fly", "machine", "status", machine, "-a", app, "--display-config=false"], timeout=60)
    m = re.search(r"State:\s*(\w+)", res.text)
    return m.group(1) if m else "unknown"


def wait_ready(runner: Runner, app: str, machine: str, budget: float) -> tuple[float | None, bool]:
    """(seconds until the activation line appeared or None, whether the Machine is up to collect from)."""
    start = time.time()
    while time.time() - start < budget:
        if _state(runner, app, machine) in ("stopped", "destroyed", "failed"):
            return None, False
        try:
            res = runner.run(
                _ssh(app, machine, "/opt/hermes/.venv/bin/python3 -I /harness/collect.py --ready"), timeout=90
            )
            if res.rc == 0:
                return time.time() - start, True
            if res.rc == 3:
                return None, True  # gateway exited, Machine held up: collect the wreck
        except ToolingError:
            pass
        time.sleep(POLL_S)
    return None, _state(runner, app, machine) == "started"


VOLUME_PRELUDE = (
    "cp /harness/customer.yaml /opt/data/customer.yaml && "
    "/opt/hermes/.venv/bin/python3 -I /harness/collect.py --prelude && "
)
POSTMORTEM_START = "{ setsid nohup /usr/bin/tini -s -g -- /app/entrypoint.sh > /harness/boot.log 2>&1 < /dev/null & }"


def start_postmortem_boot(runner: Runner, app: str, machine: str, volume: bool = False) -> None:
    """--postmortem: the Machine idles (sleep), and the REAL entrypoint is started
    under tini over ssh, so a boot that dies leaves its Machine up for the collector.
    The boot is the image's own entrypoint; only its parent differs (PID 1 is sleep)."""
    deadline = time.time() + 300
    while _state(runner, app, machine) != "started":
        if time.time() > deadline:
            raise ToolingError("held Machine never reached started")
        time.sleep(5)
    command = (VOLUME_PRELUDE if volume else "") + POSTMORTEM_START
    b64 = base64.b64encode(command.encode("utf-8")).decode("ascii")
    res = runner.run(_ssh(app, machine, f'bash -c "echo {b64} | base64 -d | bash"'), timeout=120)
    if not res.ok:
        raise ToolingError(f"could not start the entrypoint on the held Machine: {res.text[-300:]}")


def collect(runner: Runner, app: str, machine: str, booted: bool = True) -> dict[str, Any] | None:
    # A gateway that never came up runs no self-test and no cron: do not wait for them.
    waits = "900 180" if booted else "20 20"
    try:
        res = runner.run(
            _ssh(app, machine, f"/opt/hermes/.venv/bin/python3 -I /harness/collect.py {waits}"), timeout=1500
        )
    except ToolingError:
        return None
    for line in reversed(res.stdout.splitlines()):
        if line.startswith("{"):
            doc = json.loads(line)
            return None if "refused" in doc else doc
    return None


def report(verdicts: list[judge.Verdict]) -> int:
    for v in verdicts:
        print(v.line())
    failed = [v for v in verdicts if not v.ok]
    print(
        f"\n{len(verdicts) - len(failed)}/{len(verdicts)} passed"
        + (f"; FAILED: {', '.join(v.check for v in failed)}" if failed else "")
    )
    return 1 if failed else 0


def cmd_fixture(args: argparse.Namespace) -> int:
    text = fixture.render(OPERATOR)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


def cmd_judge(args: argparse.Namespace) -> int:
    raw = Path(args.bundle).read_text(encoding="utf-8").strip()
    bundle = json.loads(raw) if raw and raw != "null" else None
    log = Path(args.log).read_text(encoding="utf-8") if args.log else ""
    allow = judge.load_allowlist(HERE / "log-allowlist.yaml")
    return report(judge.judge(bundle, log, expectations(args), allow, args.ready_after))


def throwaway_google_credential(runner: Runner) -> str:
    """A base64 service-account JSON with a fresh RSA key, wired to no Google project.

    The broker reports credential_ready only when this file materializes
    (workspace_broker/verbs.py), and the self-test's broker-socket-answers-health
    requires it, so the harness stages one exactly as reprovision-staging.sh does,
    from the same generator (operator/bin/gen-staging-google-cred.py).
    """
    gen = str(OPERATOR / "bin" / "gen-staging-google-cred.py")
    res = runner.run(["uv", "run", "--quiet", "--no-project", "--with", "cryptography", "python3", gen], timeout=180)
    if not res.ok or not res.stdout.strip().startswith("{"):
        raise ToolingError("could not generate the throwaway Google credential")
    return base64.b64encode(res.stdout.strip().encode("utf-8")).decode("ascii")


def _prepare(args: argparse.Namespace, runner: Runner) -> tuple[list[str], dict[str, str], Path]:
    dummy = {k: secrets.token_hex(24) for k in DUMMY_SECRETS}
    dummy["GOOGLE_SERVICE_ACCOUNT_JSON"] = throwaway_google_credential(runner)
    out = Path(args.out_dir or tempfile.mkdtemp(prefix="boot-harness-"))
    out.mkdir(parents=True, exist_ok=True)
    fx = out / "customer.yaml"
    guest, source = SIZES[getattr(args, "size", "2x-4096")]
    fx.write_text(fixture.render(OPERATOR, source, bool(getattr(args, "keep_routines", False))), encoding="utf-8")
    (out / "empty").write_text("", encoding="utf-8")
    # Random suffix: two runs started in the same second collided on the name.
    name = f"boot-harness-{int(time.time())}-{secrets.token_hex(3)}"
    hold = bool(getattr(args, "hold", False) or getattr(args, "postmortem", False))
    volume = getattr(args, "volume", None)
    return machine_run_argv(args.image, args.app, name, fx, dummy, hold, volume, guest), dummy, out


def cmd_plan(args: argparse.Namespace) -> int:
    refusal = refuse_target(args.app)
    if refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 2
    try:
        argv, dummy, out = _prepare(args, CommandRunner())
    except ToolingError as exc:
        print(f"tooling error: {exc}", file=sys.stderr)
        return 3
    print(masked(argv, dummy))
    print(f"then: poll collect.py --ready over ssh, collect the bundle, fly logs, judge, destroy. Fixture: {out}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    refusal = refuse_target(args.app)
    if refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 2
    runner = CommandRunner()
    stream: LogStream | None = None
    try:
        argv, dummy, out = _prepare(args, runner)
        stream = LogStream(args.app, out / "machine.stream.log")
        res = runner.run(argv, timeout=600)
    except ToolingError as exc:
        if stream is not None:
            stream.close()
        print(f"tooling error: {exc}", file=sys.stderr)
        return 3
    # flyctl gives up waiting for `started` while a 1.7 GB image is still pulling
    # and exits non-zero, but the Machine exists and starts on its own; a parsed
    # Machine ID is the success signal, and wait_ready judges whether it came up.
    machine = _machine_id(res.text)
    if not machine:
        stream.close()
        print(f"tooling error: fly machine run failed: {masked([res.text[-800:]], dummy)}", file=sys.stderr)
        return 3
    try:
        return _observe(runner, args, machine, out, stream)
    finally:
        if not args.keep:
            runner.run(["fly", "machine", "destroy", machine, "-a", args.app, "--force"], timeout=120)


def cmd_observe(args: argparse.Namespace) -> int:
    """Re-attach to a harness Machine a run left up (--keep, or an interrupted run)."""
    refusal = refuse_target(args.app)
    if refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 2
    out = Path(args.out_dir or tempfile.mkdtemp(prefix="boot-harness-"))
    out.mkdir(parents=True, exist_ok=True)
    return _observe(CommandRunner(), args, args.machine, out)


ANSI = re.compile(r"\x1b\[[0-9;]*m")


class LogStream:
    """`fly logs` streamed to a file for the whole run.

    `fly logs --no-tail` returns only the last ~100 buffered lines; on the first
    calibration run that cut off everything before the gateway's exit, including
    its cause. The harness app holds only harness Machines, so the app stream is
    this run's log. Started BEFORE `fly machine run`, so the first boot line is kept.
    """

    def __init__(self, app: str, path: Path) -> None:
        self.path = path
        self._fh = path.open("w", encoding="utf-8")
        self._proc = subprocess.Popen(  # noqa: S603 - fixed argv, never a shell
            ["fly", "logs", "-a", app], stdout=self._fh, stderr=subprocess.STDOUT, text=True
        )

    def close(self) -> str:
        time.sleep(5)  # let the last lines arrive
        self._proc.terminate()
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._fh.close()
        return ANSI.sub("", self.path.read_text(encoding="utf-8", errors="replace"))


def machine_lines(log: str, machine: str) -> str:
    """Only this Machine's lines: the app stream replays buffered lines of earlier
    harness Machines, and the first calibration run judged those as this boot's."""
    tag = f"[{machine}]"
    return "\n".join(ln for ln in log.splitlines() if tag in ln)


def _boot_round(runner: Runner, args: argparse.Namespace, machine: str) -> tuple[float | None, dict[str, Any] | None]:
    if getattr(args, "postmortem", False):
        start_postmortem_boot(runner, args.app, machine, volume=bool(getattr(args, "volume", None)))
    ready_after, up = wait_ready(runner, args.app, machine, args.boot_budget)
    return ready_after, (collect(runner, args.app, machine, booted=ready_after is not None) if up else None)


def _observe(runner: Runner, args: argparse.Namespace, machine: str, out: Path, stream: LogStream | None = None) -> int:
    allow = judge.load_allowlist(HERE / "log-allowlist.yaml")
    ex = expectations(args)
    ready_after, bundle = _boot_round(runner, args, machine)
    rounds = [("boot", ready_after, bundle)]
    if getattr(args, "restart_check", False):
        # Audit 10: the same Machine, on the same volume, restarted, must come up clean.
        runner.run(["fly", "machine", "restart", machine, "-a", args.app], timeout=300)
        ready_after, bundle = _boot_round(runner, args, machine)
        rounds.append(("restart", ready_after, bundle))
    if stream is not None:
        log = machine_lines(stream.close(), machine)
    else:
        log = ANSI.sub(
            "", runner.run(["fly", "logs", "-a", args.app, "--machine", machine, "--no-tail"], timeout=120).stdout
        )
    (out / "machine.log").write_text(log, encoding="utf-8")
    rc = 0
    for label, ready, round_bundle in rounds:
        (out / f"bundle.{label}.json").write_text(json.dumps(round_bundle, indent=2), encoding="utf-8")
        print(f"\n== {label} round == evidence: {out}/bundle.{label}.json, {out}/machine.log (Machine {machine})")
        # A --postmortem round's entrypoint output is in the bundle (/harness/boot.log),
        # and the Machine log is only the idle sleep, so each round judges its own bundle.
        round_log = "" if getattr(args, "postmortem", False) else log
        steal = ((round_bundle or {}).get("cpu") or {}).get("steal_pct")
        print(f"cpu steal since boot: {steal if steal is not None else 'unknown'}% (shared-cpu throttling)")
        rc |= report(judge.judge(round_bundle, round_log, ex, allow, ready))
    return rc


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="boot-harness", description=(__doc__ or "boot-harness").splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fixture")
    f.add_argument("--out")
    for name in ("judge", "plan", "run", "observe"):
        s = sub.add_parser(name)
        s.add_argument("--expected-hermes-sha")
        s.add_argument("--boot-budget", type=float, default=judge.DEFAULT_BOOT_BUDGET_S)
        s.add_argument("--source-ref", help="commit the image was built from (default: the sha in a rel-<sha>- tag)")
        if name == "judge":
            s.add_argument("bundle")
            s.add_argument("--log")
            s.add_argument("--ready-after", type=float)
        else:
            s.add_argument("--image", required=True)
            s.add_argument("--app", default="hermes-boot-harness")
            s.add_argument("--out-dir")
            s.add_argument("--keep", action="store_true", help="leave the Machine up for inspection")
            s.add_argument("--hold", action="store_true", help="debug: idle instead of booting (run with --keep)")
            s.add_argument(
                "--postmortem",
                action="store_true",
                help="boot the real entrypoint under a held Machine, so a crashing boot can still be collected",
            )
            s.add_argument(
                "--keep-routines",
                action="store_true",
                help="keep the source seat's authored routines (on a never-firing slot) so op-managed jobs exist",
            )
            s.add_argument("--size", choices=sorted(SIZES), default="2x-4096", help="guest and matching fixture")
            s.add_argument("--volume", help="mount this harness-app volume at /opt/data (implies --postmortem)")
            s.add_argument(
                "--restart-check", action="store_true", help="after the boot, restart the Machine and judge again"
            )
            if name == "observe":
                s.add_argument("--machine", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "volume", None):
        args.postmortem = True
    handler = {"fixture": cmd_fixture, "judge": cmd_judge, "plan": cmd_plan, "run": cmd_run, "observe": cmd_observe}[
        args.cmd
    ]
    return handler(args)
