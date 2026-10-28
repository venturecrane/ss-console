"""The on-Machine half of the boot harness: observe, never judge.

Shipped to the harness Machine at /harness/collect.py (fly machine run
--file-local) and run as root over `fly ssh console`. STDLIB ONLY, run with the
image's interpreter in isolated mode (-I), so the collector does not depend on
the packages whose absence it is there to observe. It prints one JSON document
(the observation bundle) on stdout; judge.py decides what it means.

Order is load-bearing: the self-test result is read BEFORE the cron probe is
planted, so the probe's staged script can never be what a boot self-test check
saw.

This runs ONLY on a harness Machine. It refuses when CUSTOMER_SLUG is not
boot-harness, because it plants a cron job and probes the webhook port.
"""

from __future__ import annotations

import glob
import http.client
import json
import os
import subprocess
import sys
import time

SLUG = "boot-harness"
DATA = "/opt/data"
VENV_PY = "/opt/hermes/.venv/bin/python3"
SELFTEST_RESULTS = "/run/smd-selftest/results.json"
# /harness/boot.log is the entrypoint's own output in a --postmortem run.
LOG_GLOBS = (DATA + "/logs/*.log", DATA + "/profiles/*/logs/*.log", "/harness/boot.log")
GATEWAY_EXITED = "Supervisor: gateway exited"
HARNESS_FIXTURE = "/harness/customer.yaml"
LOG_OFFSETS = "/harness/log-offsets.json"
CRON_BEFORE = "/harness/cron-before.json"
LOG_TAIL_BYTES = 256 * 1024
OVERLAY_MODULES = ("shared", "shared.selfcheck", "bootstrap", "bootstrap.validate", "plugins")
PROBE_DIR = ".smd-boot-harness"
PROBE_NAME = "op-managed:boot-harness-cron-probe"

# The probe the cron runs: record that it fired, then decline to wake the agent,
# exactly as operator/templates/pre_run_gate.py does on a seat with no work, so
# no model call is ever made.
PROBE_SCRIPT = """import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
names = ("PYTHONPATH", "VIRTUAL_ENV", "INVOCATION_ID", "HERMES_HOME", "PATH")
record = {"at": int(time.time()), "executable": sys.executable, "version": sys.version.split()[0],
          "env_present": sorted(n for n in names if n in os.environ)}
with open(os.path.join(here, "fired"), "a", encoding="utf-8") as fh:
    fh.write(json.dumps(record) + "\\n")
print(json.dumps({"wakeAgent": False}))
"""

# Run with the GATEWAY's own environment (copied from /proc/<pid>/environ, never
# printed): where the modules the gateway imports resolve, and brotlicffi's version.
RESOLVE = """import importlib, importlib.metadata, json
out = {}
for name in ("shared", "anthropic", "aiohttp"):
    try:
        out[name] = getattr(importlib.import_module(name), "__file__", None) or "<namespace>"
    except Exception as exc:
        out[name] = "ERROR " + exc.__class__.__name__ + ": " + str(exc)[:200]
try:
    out["brotlicffi_version"] = importlib.metadata.version("brotlicffi")
except Exception as exc:
    out["brotlicffi_version"] = "ERROR " + exc.__class__.__name__
print(json.dumps(out))
"""

PLUGINS_ENABLED = """import glob, json, sys, yaml
out = {}
for path in sorted(glob.glob(sys.argv[1] + "/profiles/*/config.yaml")):
    doc = yaml.safe_load(open(path, encoding="utf-8")) or {}
    out[path] = ((doc.get("plugins") or {}).get("enabled")) or []
print(json.dumps(out))
"""

PLANT = """import json, sys
from pathlib import Path
from bootstrap.translate import _real_cron_store_for
store = _real_cron_store_for(Path(sys.argv[1]))(sys.argv[2])
for job in store.list_jobs(include_disabled=True):
    if job.get("name") == sys.argv[3]:
        store.remove_job(job["id"])
job = store.create_job(prompt="", schedule="* * * * *", name=sys.argv[3], skills=[sys.argv[4]],
                       no_agent=False, script=sys.argv[5], deliver="local")
print(json.dumps({"id": job.get("id")}))
"""

READ_JOB = """import json, sys
from pathlib import Path
from bootstrap.translate import _real_cron_store_for
store = _real_cron_store_for(Path(sys.argv[1]))(sys.argv[2])
hit = [j for j in store.list_jobs(include_disabled=True) if j.get("name") == sys.argv[3]]
print(json.dumps(hit[0] if hit else None, default=str))
"""


def _run(argv: list[str], timeout: float = 60, env: dict[str, str] | None = None) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)  # noqa: S603 - fixed argv lists built in this file, never a shell
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 124, f"{exc.__class__.__name__}: {exc}"
    return proc.returncode, (proc.stdout + proc.stderr)[-2000:]


def _as_hermes(argv: list[str]) -> list[str]:
    return ["setpriv", "--reuid=hermes", "--regid=hermes", "--init-groups", *argv]


def _http(method: str, port: int, path: str) -> int | str:
    """HTTP status from a LOOPBACK port, or the error's name. Host is fixed."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request(method, path, body=b"{}" if method == "POST" else None)
        return int(conn.getresponse().status)
    except (OSError, http.client.HTTPException) as exc:
        return f"{exc.__class__.__name__}: {exc}"
    finally:
        conn.close()


def wait_selftest(budget_s: float) -> dict | None:
    deadline = time.time() + budget_s
    while True:
        try:
            with open(SELFTEST_RESULTS, encoding="utf-8") as fh:
                doc = json.load(fh)
            if isinstance(doc, dict) and doc.get("finished_at"):
                return doc
        except (OSError, ValueError):
            pass
        if time.time() >= deadline:
            return None
        time.sleep(10)


def gateway_processes() -> list[str]:
    out = []
    for path in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            with open(path, "rb") as fh:
                cmd = fh.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        except OSError:
            continue
        if "hermes" in cmd and " gateway" in cmd and "collect.py" not in cmd:
            out.append(cmd[:300])
    return out


GATEWAY_ENV_NAMES = ("PYTHONPATH", "VIRTUAL_ENV", "INVOCATION_ID", "HERMES_HOME", "SMD_GATEWAY_PID", "TMPDIR")


def _gateway_pid() -> int | None:
    for path in sorted(glob.glob("/proc/[0-9]*/cmdline")):
        try:
            with open(path, "rb") as fh:
                argv = [a.decode("utf-8", "replace") for a in fh.read().split(b"\0") if a]
        except OSError:
            continue
        if any(a.endswith("/hermes") or a == "hermes" for a in argv) and argv[-2:] == ["gateway", "run"]:
            return int(path.split("/")[2])
    return None


def _environ(pid: int) -> dict[str, str]:
    with open(f"/proc/{pid}/environ", "rb") as fh:
        pairs = [kv.split(b"=", 1) for kv in fh.read().split(b"\0") if b"=" in kv]
    return {k.decode("utf-8", "replace"): v.decode("utf-8", "replace") for k, v in pairs}


def _started_epoch(pid: int) -> float | None:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            ticks = int(fh.read().rsplit(")", 1)[1].split()[19])
        with open("/proc/stat", encoding="utf-8") as fh:
            btime = next(int(ln.split()[1]) for ln in fh if ln.startswith("btime "))
    except (OSError, ValueError, StopIteration, IndexError):
        return None
    return btime + ticks / os.sysconf("SC_CLK_TCK")


def gateway_introspect() -> dict:
    """Facts about the live gateway process. Env VALUES never leave this function."""
    pid = _gateway_pid()
    if pid is None:
        return {"pid": None}
    with open(f"/proc/{pid}/cmdline", "rb") as fh:
        cmdline = " ".join(a.decode("utf-8", "replace") for a in fh.read().split(b"\0") if a)
    try:
        env = _environ(pid)
    except OSError as exc:
        return {"pid": pid, "cmdline": cmdline, "env_error": exc.__class__.__name__}
    rc, out = _run(_as_hermes([VENV_PY, "-c", RESOLVE]), env=env)
    resolved = json.loads(out.strip().splitlines()[-1]) if rc == 0 and out.strip() else {"error": out[-300:]}
    return {
        "pid": pid,
        "cmdline": cmdline,
        "multiplex_reasons": multiplex_reasons(),
        "started_epoch": _started_epoch(pid),
        "env_present": sorted(n for n in GATEWAY_ENV_NAMES if n in env),
        "smd_gateway_pid_matches": env.get("SMD_GATEWAY_PID") == str(pid),
        # TMPDIR is a path, never a credential: its value is reported.
        "tmpdir": env.get("TMPDIR"),
        "profile_scratch_dirs": {p: os.path.isdir(p) for p in sorted(glob.glob(DATA + "/profiles/*/cache/scratch"))},
        "resolved": resolved,
    }


def multiplex_reasons() -> dict:
    """Each profile's recorded multiplex decision (gateway_state.json), or None."""
    out = {}
    for path in sorted(glob.glob(DATA + "/profiles/*/gateway_state.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                out[path] = (json.load(fh) or {}).get("multiplex_standalone_reason")
        except (OSError, ValueError) as exc:
            out[path] = f"<unreadable: {exc.__class__.__name__}>"
    return out


def plugins_enabled() -> dict:
    rc, out = _run([VENV_PY, "-c", PLUGINS_ENABLED, DATA])
    if rc != 0 or not out.strip():
        return {"error": out[-300:]}
    return json.loads(out.strip().splitlines()[-1])


def _venv_keys(node: object, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            if "venv" in str(key).lower():
                found.append(here)
            found += _venv_keys(value, here)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found += _venv_keys(value, f"{path}[{i}]")
    return found


def install_facts() -> dict:
    """Every /opt/data/installs/*/facts.json, reduced to the key paths naming a venv."""
    out = {}
    for path in sorted(glob.glob(DATA + "/installs/*/facts.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                out[path] = _venv_keys(json.load(fh))
        except (OSError, ValueError) as exc:
            out[path] = [f"<unreadable: {exc.__class__.__name__}>"]
    return out


def read_logs() -> dict[str, str]:
    """Each log's tail, from where it ended before this boot when a prelude marked it."""
    logs = {}
    marked = _load(LOG_OFFSETS) or {}
    for pattern in LOG_GLOBS:
        for path in sorted(glob.glob(pattern)):
            try:
                size = os.path.getsize(path)
                start = int(marked.get(path, 0)) if isinstance(marked, dict) else 0
                start = start if start <= size else 0
                with open(path, "rb") as fh:
                    fh.seek(max(start, size - LOG_TAIL_BYTES))
                    logs[path] = fh.read().decode("utf-8", "replace")
            except OSError as exc:
                logs[path] = f"<unreadable: {exc.__class__.__name__}>"
    return logs


def filesystem() -> dict:
    try:
        entries = sorted(os.listdir(DATA))
    except OSError as exc:
        entries = [f"<unreadable: {exc.__class__.__name__}>"]
    return {
        "opt_data_entries": entries,
        "opt_data_tools": os.path.exists(DATA + "/tools"),
        "opt_data_installs": os.path.exists(DATA + "/installs"),
        "install_facts_venv_keys": install_facts(),
    }


def imports() -> dict:
    rc, out = _run([VENV_PY, "-c", "import aiohttp, yaml"])
    found = {"aiohttp_yaml_in_venv": {"rc": rc, "detail": out[-300:]}}
    for module in OVERLAY_MODULES:
        rc, out = _run(_as_hermes([VENV_PY, "-c", f"import {module}"]))
        found[module] = {"rc": rc, "detail": out[-300:]}
    return found


def http_probes() -> dict:
    return {
        "gate_health": _http("GET", 8643, "/health"),
        "adapter_unsigned_handoff": _http("POST", 8644, "/webhooks/handoff"),
    }


def _persona() -> str | None:
    homes = sorted(
        p for p in glob.glob(DATA + "/profiles/*") if os.path.isdir(p) and not os.path.basename(p).startswith(".")
    )
    return os.path.basename(homes[0]) if homes else None


def _stage_probe(persona: str) -> str:
    target = os.path.join(DATA, "profiles", persona, "scripts", PROBE_DIR)
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, "probe.py"), "w", encoding="utf-8") as fh:
        fh.write(PROBE_SCRIPT)
    _run(["chown", "-R", "hermes:hermes", os.path.join(DATA, "profiles", persona, "scripts")])
    return target


def cron_probe(skill: str, wait_s: float) -> dict:
    persona = _persona()
    if persona is None:
        return {"planted": False, "detail": "no profile home under /opt/data/profiles"}
    target = _stage_probe(persona)
    env = {**os.environ, "HERMES_HOME": DATA}
    args = [DATA + "/profiles", persona, PROBE_NAME]
    rc, out = _run(_as_hermes([VENV_PY, "-c", PLANT, *args, skill, PROBE_DIR + "/probe.py"]), env=env)
    if rc != 0:
        return {"planted": False, "persona": persona, "detail": out[-600:]}
    fired_path = os.path.join(target, "fired")
    deadline = time.time() + wait_s
    while not os.path.exists(fired_path) and time.time() < deadline:
        time.sleep(5)
    _, job = _run(_as_hermes([VENV_PY, "-c", READ_JOB, *args]), env=env)
    child = None
    if os.path.exists(fired_path):
        with open(fired_path, encoding="utf-8") as fh:
            lines = [ln for ln in fh.read().splitlines() if ln.startswith("{")]
        child = json.loads(lines[-1]) if lines else None
    return {"planted": True, "persona": persona, "fired": os.path.exists(fired_path), "child": child, "job": job[-600:]}


ACTIVE_MARKER = "SMD overlay ACTIVE + AUDITING on the live gateway"


def ready() -> int:
    """0 once any log carries the activation line; 3 once the gateway has exited
    (a --postmortem boot, whose Machine outlives it); 1 while still booting."""
    logs = read_logs().values()
    if any(ACTIVE_MARKER in text for text in logs):
        return 0
    return 3 if any(GATEWAY_EXITED in text for text in logs) else 1


def on_harness() -> bool:
    """The customer.yaml names the harness: the root-owned copy the entrypoint
    installed, or (before a --volume boot starts) the fixture the runner shipped.
    Never env, which an ssh session may not carry."""
    for path in ("/var/lib/smd-config/customer.yaml", HARNESS_FIXTURE):
        try:
            with open(path, encoding="utf-8") as fh:
                if any(line.strip() == f"customer_id: {SLUG}" for line in fh):
                    return True
        except OSError:
            continue
    return False


def cron_snapshot() -> dict:
    """Each profile's cron store as [{id, name}], read from jobs.json directly."""
    out = {}
    for path in sorted(glob.glob(DATA + "/profiles/*/cron/jobs.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError) as exc:
            out[path] = f"<unreadable: {exc.__class__.__name__}>"
            continue
        jobs = doc if isinstance(doc, list) else (doc or {}).get("jobs") or []
        out[path] = [{"id": j.get("id"), "name": j.get("name")} for j in jobs if isinstance(j, dict)]
    return out


def prelude() -> int:
    """Before a --volume boot: remember where every existing log ends (so this boot
    is judged on its own lines, not the volume's history) and what the cron store held."""
    offsets = {}
    for pattern in LOG_GLOBS:
        for path in glob.glob(pattern):
            try:
                offsets[path] = os.path.getsize(path)
            except OSError:
                continue
    with open(LOG_OFFSETS, "w", encoding="utf-8") as fh:
        json.dump(offsets, fh)
    with open(CRON_BEFORE, "w", encoding="utf-8") as fh:
        json.dump(cron_snapshot(), fh)
    print(json.dumps({"logs_marked": len(offsets)}))
    return 0


def _load(path: str) -> object:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def main() -> int:
    if not on_harness():
        print(
            json.dumps(
                {"refused": f"live customer.yaml is not {SLUG}'s; this collector runs only on a harness Machine"}
            )
        )
        return 2
    if sys.argv[1:2] == ["--ready"]:
        return ready()
    if sys.argv[1:2] == ["--prelude"]:
        return prelude()
    selftest_wait = float(sys.argv[1]) if len(sys.argv) > 1 else 900
    cron_wait = float(sys.argv[2]) if len(sys.argv) > 2 else 180
    bundle: dict = {"collected_at": time.time()}
    with open("/proc/uptime", encoding="utf-8") as fh:
        bundle["uptime_s"] = float(fh.read().split()[0])
    try:
        with open("/opt/hermes/HERMES_SHA", encoding="utf-8") as fh:
            bundle["hermes_sha"] = fh.read().strip()
    except OSError:
        bundle["hermes_sha"] = None
    bundle["selftest"] = wait_selftest(selftest_wait)
    bundle["gateway_processes"] = gateway_processes()
    bundle["gateway"] = gateway_introspect()
    bundle["plugins_enabled"] = plugins_enabled()
    bundle["filesystem"] = filesystem()
    bundle["imports"] = imports()
    bundle["http"] = http_probes()
    bundle["cron_before"] = _load(CRON_BEFORE)
    bundle["cron_after"] = cron_snapshot()  # before the probe is planted
    bundle["cron"] = cron_probe("workspace", cron_wait)
    bundle["logs"] = read_logs()
    print(json.dumps(bundle, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
