"""Checks from the 2026-10-27 boot-path audit's "only determinable by booting" list.

Each item the audit could not settle from source becomes one PASS/FAIL line
here, read from the same observation bundle as judge.py. Items that need a
model turn, a volume, or a connector the harness fixture does not carry are not
faked as passes: they are absent from this list and named in the package
docstring's limits, so nobody mistakes an unrun probe for a green one.

  audit 1  plugins-enabled-key-written      (with judge's activation + sub-plugin count)
  audit 3  gateway-modules-resolve-in-venv
  audit 4  gateway-env-and-cmdline
  audit 5  startup-watchdog-disarmed        (a measurement; 1 GB timing needs --memory 1024)
  audit 6  (first half) folded into gateway-env-and-cmdline: INVOCATION_ID unset
  audit 7  cron-child-interpreter           (the cron pre_run child; MCP stdio child needs a connector)
  audit 8  no-job-broker-refusals
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from bin.lib.boot_harness.model import Expect, Verdict

OVERLAY_PLUGIN = "hermes-smd-overlay"
SITE_PACKAGES = re.compile(r"^/opt/hermes/\.venv/lib/python3\.\d+/site-packages/")
GATEWAY_CMDLINE = re.compile(r"/hermes -p [a-z0-9][a-z0-9-]* gateway run$")
WATCHDOG_DISARM = re.compile(r"[Ss]tartup watchdog disarm")
JOB_BROKER_REFUSED = "job broker refused"
TIMESTAMP = re.compile(r"(\d{4}-\d\d-\d\d)[T ](\d\d:\d\d:\d\d)(?:[.,](\d+))?")


def _unobserved(check: str, what: str) -> Verdict:
    return Verdict(check, False, f"not observed: {what} (the collector returned no bundle; Machine down?)")


def check_plugins_enabled(bundle: dict[str, Any] | None) -> Verdict:
    name = "plugins-enabled-key-written"
    if bundle is None:
        return _unobserved(name, "profile config.yaml plugins.enabled")
    found = bundle.get("plugins_enabled")
    if not isinstance(found, dict) or "error" in found or not found:
        return Verdict(
            name, False, f"could not read plugins.enabled: {str((found or {}).get('error', 'no profile config'))[:200]}"
        )
    missing = [path for path, enabled in found.items() if OVERLAY_PLUGIN not in (enabled or [])]
    if missing:
        return Verdict(name, False, f"{OVERLAY_PLUGIN} not in plugins.enabled of {', '.join(missing)}")
    return Verdict(name, True, f"{OVERLAY_PLUGIN} enabled by key in {len(found)} profile config(s)")


def check_modules(bundle: dict[str, Any] | None, ex: Expect) -> Verdict:
    name = "gateway-modules-resolve-in-venv"
    if bundle is None:
        return _unobserved(name, "module resolution in the gateway's environment")
    resolved = (bundle.get("gateway") or {}).get("resolved") or {}
    if not resolved or "error" in resolved:
        return Verdict(name, False, f"resolution probe did not run: {str(resolved.get('error', 'no gateway'))[:200]}")
    outside = [
        f"{m}={resolved.get(m)}"
        for m in ("shared", "anthropic", "aiohttp")
        if not SITE_PACKAGES.match(str(resolved.get(m)))
    ]
    if outside:
        return Verdict(name, False, "not from the Hermes venv's site-packages: " + "; ".join(outside)[:300])
    got = resolved.get("brotlicffi_version")
    if ex.brotlicffi and got != ex.brotlicffi:
        return Verdict(name, False, f"brotlicffi {got}, the candidate's security override pins {ex.brotlicffi}")
    return Verdict(name, True, f"shared, anthropic, aiohttp from the venv's site-packages; brotlicffi {got}")


def check_gateway_env(bundle: dict[str, Any] | None, ex: Expect) -> Verdict:
    name = "gateway-env-and-cmdline"
    if bundle is None:
        return _unobserved(name, "/proc/<gateway>/environ and cmdline")
    gw = bundle.get("gateway") or {}
    if not gw.get("pid"):
        return Verdict(name, False, "no `hermes ... gateway run` process found")
    present = set(gw.get("env_present") or [])
    problems = []
    if not GATEWAY_CMDLINE.search(str(gw.get("cmdline", ""))):
        problems.append(f"cmdline {str(gw.get('cmdline'))[:120]!r}")
    if "PYTHONPATH" in present:
        problems.append("PYTHONPATH is set")
    if ex.require_virtual_env and "VIRTUAL_ENV" not in present:
        problems.append("VIRTUAL_ENV is unset, and the candidate's launch path exports it")
    if "INVOCATION_ID" in present:
        problems.append("INVOCATION_ID is set")
    if not gw.get("smd_gateway_pid_matches"):
        problems.append("SMD_GATEWAY_PID is not the gateway's own pid")
    if problems:
        return Verdict(name, False, "; ".join(problems))
    venv = "VIRTUAL_ENV set" if "VIRTUAL_ENV" in present else "VIRTUAL_ENV unset (this image does not export it)"
    return Verdict(name, True, f"pid {gw['pid']}: no PYTHONPATH, {venv}, no INVOCATION_ID, SMD_GATEWAY_PID is its own")


def _epoch(line: str) -> float | None:
    found = TIMESTAMP.search(line)
    if not found:
        return None
    day, clock, frac = found.groups()
    stamp = datetime.strptime(f"{day} {clock}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return stamp.timestamp() + (float(f"0.{frac}") if frac else 0.0)


ACTIVE_MARKER = "SMD overlay ACTIVE + AUDITING on the live gateway"


def check_watchdog(bundle: dict[str, Any] | None, logs: str, ex: Expect) -> Verdict:
    """Exec-to-ready on the gateway: to Hermes' 'Startup watchdog disarm' line when
    this Hermes logs one, else to the overlay's activation line (a Hermes that
    predates the line). Fails when neither is logged, or the time is over budget."""
    name = "startup-watchdog-disarmed"
    lines = [ln for ln in logs.splitlines() if WATCHDOG_DISARM.search(ln)]
    marker = "watchdog disarm"
    if not lines:
        lines = [ln for ln in logs.splitlines() if ACTIVE_MARKER in ln]
        marker = "activation (this Hermes logs no disarm line)"
    if not lines:
        return Verdict(name, False, "neither a 'Startup watchdog disarm' nor an activation line in any log")
    started = ((bundle or {}).get("gateway") or {}).get("started_epoch")
    at = _epoch(lines[0])
    if started is None or at is None:
        return Verdict(name, False, f"{marker} logged, but exec-to-ready is not measurable from this bundle")
    took = at - float(started)
    if took > ex.boot_budget_s:
        return Verdict(
            name, False, f"{marker} {took:.0f}s after the gateway exec, over the {ex.boot_budget_s:.0f}s budget"
        )
    return Verdict(name, True, f"{marker} {took:.0f}s after the gateway exec")


def check_cron_child(bundle: dict[str, Any] | None) -> Verdict:
    name = "cron-child-interpreter"
    if bundle is None:
        return _unobserved(name, "the cron probe's child record")
    child = (bundle.get("cron") or {}).get("child")
    if not isinstance(child, dict):
        return Verdict(name, False, "the cron probe never recorded its interpreter (it did not fire)")
    exe, env = str(child.get("executable")), list(child.get("env_present") or [])
    if not exe.startswith("/opt/hermes/.venv/") or "PYTHONPATH" in env:
        return Verdict(name, False, f"pre_run child ran {exe} with env {env}")
    return Verdict(name, True, f"pre_run child: {exe} (Python {child.get('version')}), env names {env}")


PROBE_NAME = "op-managed:boot-harness-cron-probe"


def _jobs(store: Any) -> dict[str, list[tuple[str, str]]]:
    """(name, schedule) per profile store, the harness probe excluded."""
    if not isinstance(store, dict):
        return {}
    return {
        path: [
            (str(j.get("name")), str(j.get("schedule")))
            for j in jobs
            if isinstance(j, dict) and j.get("name") != PROBE_NAME
        ]
        for path, jobs in store.items()
        if isinstance(jobs, list)
    }


def check_cron_store(bundle: dict[str, Any], ex: Expect) -> Verdict:
    """Audit 11, on a --volume boot: every job the volume carried is there after the
    boot with the same schedule, no name is duplicated, and when the fixture
    authors routines at least one op-managed job exists (else the check is vacuous).
    Ids are not compared: the materializer removes and recreates managed jobs on
    every boot by design (bootstrap/cron_materialize.py, pass B)."""
    name = "cron-store-survives-boot"
    before, after = _jobs(bundle.get("cron_before")), _jobs(bundle.get("cron_after"))
    if not after and before:
        return Verdict(name, False, "the cron store could not be read after the boot")
    lost = sorted({f"{n} ({s})" for p, jobs in before.items() for n, s in jobs if (n, s) not in after.get(p, [])})
    dupes = sorted({n for jobs in after.values() for n, _ in jobs if [m for m, _ in jobs].count(n) > 1})
    managed = sorted({n for jobs in after.values() for n, _ in jobs if n.startswith("op-managed:")})
    if lost or dupes:
        return Verdict(name, False, f"lost or changed {lost[:5]}; duplicated {dupes[:5]}")
    if ex.require_managed_jobs and not managed:
        return Verdict(name, False, "the fixture authors routines but no op-managed job is in the store")
    total = sum(len(v) for v in after.values())
    return Verdict(name, True, f"{total} job(s) after boot, managed {managed}, none lost or changed, no duplicates")


def check_job_broker(logs: str) -> Verdict:
    name = "no-job-broker-refusals"
    count = sum(1 for ln in logs.splitlines() if JOB_BROKER_REFUSED in ln)
    if count:
        return Verdict(name, False, f"{count} 'job broker refused' line(s) this boot")
    return Verdict(name, True, "zero 'job broker refused' lines")


def checks(bundle: dict[str, Any] | None, logs: str, ex: Expect) -> list[Verdict]:
    volume = [check_cron_store(bundle, ex)] if bundle and bundle.get("cron_before") is not None else []
    return volume + [
        check_plugins_enabled(bundle),
        check_modules(bundle, ex),
        check_gateway_env(bundle, ex),
        check_watchdog(bundle, logs, ex),
        check_cron_child(bundle),
        check_job_broker(logs),
    ]
