"""The boot harness verdict: one PASS/FAIL line per act a seat must perform.

Pure. Input is the collector's observation bundle (collect.py; None when the
Machine never answered ssh) plus the Machine's own log text (`fly logs`), and
the expectations of the candidate (its Hermes pin and its declared overlay
sub-plugins, read from the checkout the image was built from). Output is a list
of Verdicts, every one of which is computed: there is no check that passes by
default, and a check whose evidence was not observed FAILS saying so, because
"could not look" must never read as "nothing wrong".

Where a seat already proves a thing about itself, this reads that proof rather
than re-implementing it: the in-image self-test (operator/templates/selftest.sh
over seat-checks.sh) is judged by fleet.smoke's own hollow-run rule, and the
overlay's activation hook's completion line is the evidence that the trust gate
fired and an audit row crossed the broker from the gateway process.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from bin.lib.boot_harness import audit
from bin.lib.boot_harness.model import DEFAULT_BOOT_BUDGET_S, Expect, Verdict
from bin.lib.fleet.smoke import _selftest_hollow

__all__ = ["DEFAULT_BOOT_BUDGET_S", "AllowRule", "Expect", "Verdict", "judge", "load_allowlist"]

ACTIVE_MARKER = "SMD overlay ACTIVE + AUDITING on the live gateway"
STANDALONE_REASON = "this profile is standalone (gateway.standalone: true); it serves only itself"
TRACEBACK_CONTEXT = 40
ACTIVATION_FAILED = "SMD OVERLAY ACTIVATION FAILED"
AUDIT_ROW = re.compile(r"audit row written \(before=(\d+) after=(\d+)\)")
REGISTERED = re.compile(r"hermes-smd-overlay registered (\d+) sub-plugin\(s\): ([^\n]*)")
MISSING_MODULE = re.compile(r"No module named '?([A-Za-z0-9_.]+)'?")
ERROR_LINE = re.compile(r"\b(ERROR|CRITICAL|FATAL)\b|Traceback \(most recent call last\)")
PLUGIN_LOAD_FAILED = re.compile(r"Failed to load plugin|failed to register")
AIOHTTP_MISSING = "aiohttp not installed"
ADAPTER_REFUSAL_CODES = (400, 401, 403)


@dataclass(frozen=True)
class AllowRule:
    pattern: re.Pattern[str]
    reason: str


def subplugins_from_text(text: str) -> tuple[str, ...]:
    doc = yaml.safe_load(text) or {}
    return tuple(Path(str(rel)).name for rel in doc.get("plugins") or [])


def declared_subplugins(plugin_yaml: Path) -> tuple[str, ...]:
    return subplugins_from_text(plugin_yaml.read_text(encoding="utf-8"))


def load_allowlist(path: Path) -> list[AllowRule]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rules = []
    for entry in doc.get("allow") or []:
        reason = str(entry.get("reason") or "").strip()
        if not reason:
            raise ValueError(f"{path}: allowlist entry {entry.get('pattern')!r} carries no reason")
        rules.append(AllowRule(re.compile(str(entry["pattern"])), reason))
    return rules


def all_logs(bundle: dict[str, Any] | None, machine_log: str) -> str:
    files = (bundle or {}).get("logs") or {}
    return "\n".join([machine_log, *(str(v) for v in files.values())])


def _unobserved(check: str, what: str) -> Verdict:
    return Verdict(check, False, f"not observed: {what} (the collector returned no bundle; Machine down?)")


def check_boot_ready(bundle: dict[str, Any] | None, logs: str, ready_after_s: float | None, ex: Expect) -> Verdict:
    name = "boot-ready"
    if ACTIVE_MARKER not in logs:
        return Verdict(name, False, "the gateway never logged overlay activation complete (it did not reach ready)")
    if bundle is None:
        return _unobserved(name, "Machine state after the activation line")
    if ready_after_s is not None and ready_after_s > ex.boot_budget_s:
        return Verdict(name, False, f"ready after {ready_after_s:.0f}s, over the {ex.boot_budget_s:.0f}s budget")
    took = "unmeasured" if ready_after_s is None else f"{ready_after_s:.0f}s"
    return Verdict(name, True, f"gateway reached ready ({took}) and the Machine answered the collector")


def check_activation(logs: str) -> Verdict:
    name = "overlay-activation-complete"
    if ACTIVATION_FAILED in logs:
        line = next(ln for ln in logs.splitlines() if ACTIVATION_FAILED in ln)
        return Verdict(name, False, line.strip()[:300])
    if ACTIVE_MARKER not in logs:
        return Verdict(name, False, "no activation completion line in any log")
    return Verdict(name, True, "activation hook completed on the live gateway")


def check_subplugins(logs: str, ex: Expect) -> Verdict:
    name = "overlay-subplugins-registered"
    failed = [ln.strip()[:200] for ln in logs.splitlines() if PLUGIN_LOAD_FAILED.search(ln)]
    if failed:
        return Verdict(name, False, "; ".join(failed[:3]))
    found = REGISTERED.findall(logs)
    if not found:
        return Verdict(name, False, "the overlay never logged its sub-plugin registration")
    got = {s.strip() for s in found[-1][1].split(",") if s.strip()}
    missing = sorted(set(ex.subplugins) - got)
    if missing:
        return Verdict(name, False, f"declared but not registered: {', '.join(missing)}")
    return Verdict(name, True, f"all {len(ex.subplugins)} declared sub-plugins registered")


def check_overlay_imports(bundle: dict[str, Any] | None, logs: str) -> Verdict:
    name = "overlay-packages-importable"
    missing = sorted({m for m in MISSING_MODULE.findall(logs)})
    if missing:
        return Verdict(name, False, f"a boot log reports No module named: {', '.join(missing)}")
    if bundle is None:
        return _unobserved(name, "overlay imports as the hermes uid")
    bad = [m for m, r in (bundle.get("imports") or {}).items() if m != "aiohttp_yaml_in_venv" and r.get("rc") != 0]
    if bad or not bundle.get("imports"):
        return Verdict(name, False, f"cannot import as hermes: {', '.join(bad) or 'no import probe ran'}")
    return Verdict(name, True, "shared, bootstrap and plugins import as the hermes uid; no missing-module line logged")


def check_webhook(bundle: dict[str, Any] | None, logs: str) -> Verdict:
    name = "webhook-adapter-up"
    if AIOHTTP_MISSING in logs:
        return Verdict(name, False, "a boot log reports 'Webhook: aiohttp not installed'")
    if bundle is None:
        return _unobserved(name, "the webhook ports")
    venv = (bundle.get("imports") or {}).get("aiohttp_yaml_in_venv") or {}
    http = bundle.get("http") or {}
    if venv.get("rc") != 0:
        return Verdict(name, False, f"aiohttp/yaml not importable from the Hermes venv: {venv.get('detail', '')[:200]}")
    if http.get("gate_health") != 200:
        return Verdict(name, False, f"front-door gate :8643/health answered {http.get('gate_health')!r}, want 200")
    if http.get("adapter_unsigned_handoff") not in ADAPTER_REFUSAL_CODES:
        return Verdict(
            name,
            False,
            f"adapter :8644 unsigned POST answered {http.get('adapter_unsigned_handoff')!r}, want a refusal",
        )
    return Verdict(name, True, "gate /health 200; adapter answers and refuses an unsigned webhook")


def _selftest_checks(bundle: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    doc = (bundle or {}).get("selftest") or {}
    return {str(c.get("id")): c for c in doc.get("checks") or [] if isinstance(c, dict)}


def check_selftest(bundle: dict[str, Any] | None) -> Verdict:
    name = "seat-selftest-green"
    if bundle is None:
        return _unobserved(name, "/run/smd-selftest/results.json")
    doc = bundle.get("selftest")
    if not isinstance(doc, dict):
        return Verdict(name, False, "the in-image self-test produced no result inside the wait budget")
    # results.json carries `failed`/`warned` as COUNTS (selftest.sh); the heartbeat
    # form fleet.smoke reads carries lists plus *_count. Read the ids off the checks.
    checks = [c for c in doc.get("checks") or [] if isinstance(c, dict)]
    failed = [str(c.get("id")) for c in checks if not c.get("ok") and not c.get("warn")]
    warned = [str(c.get("id")) for c in checks if c.get("warn")]
    count = doc.get("failed_count", doc.get("failed"))
    if failed or (isinstance(count, int) and count > 0) or (isinstance(count, list) and count):
        return Verdict(name, False, f"{doc.get('run_kind')} run failed: {'; '.join(failed[:8]) or count}")
    hollow = _selftest_hollow({**doc, "failed": failed, "warned": warned})
    if hollow:
        return Verdict(name, False, f"{doc.get('run_kind')} run {hollow}")
    return Verdict(name, True, f"{doc.get('run_kind')} run passed {len(checks)} checks, {len(warned)} warned")


def check_standalone(bundle: dict[str, Any] | None) -> Verdict:
    name = "gateway-single-profile-standalone"
    if bundle is None:
        return _unobserved(name, "gateway processes")
    procs = bundle.get("gateway_processes") or []
    if len(procs) != 1:
        return Verdict(name, False, f"{len(procs)} hermes gateway process(es), want exactly 1")
    if not re.search(r" -p [a-z0-9-]+ gateway run$", str(procs[0])):
        return Verdict(name, False, f"the gateway is not single-profile: {str(procs[0])[:160]}")
    check = _selftest_checks(bundle).get("gateway-multiplex-standalone")
    if check is not None:
        if not check.get("ok"):
            return Verdict(name, False, f"self-test gateway-multiplex-standalone: {check.get('detail', '')[:200]}")
        return Verdict(name, True, "one single-profile gateway; it recorded the gateway.standalone guard")
    # An image whose self-test predates that check: read the gateway's own record.
    reasons = (bundle.get("gateway") or {}).get("multiplex_reasons") or {}
    wrong = {k: v for k, v in reasons.items() if v not in (None, STANDALONE_REASON)}
    if wrong:
        return Verdict(name, False, f"gateway recorded a different multiplex decision: {wrong}")
    said = (
        "it recorded the standalone guard"
        if any(reasons.values())
        else "no multiplex decision recorded (pre-multiplex Hermes)"
    )
    return Verdict(name, True, f"one single-profile gateway; {said}")


def check_hermes_sha(bundle: dict[str, Any] | None, ex: Expect) -> Verdict:
    name = "hermes-sha-pinned"
    if not ex.hermes_sha or not re.fullmatch(r"[0-9a-f]{40}", ex.hermes_sha):
        return Verdict(name, False, f"no 40-hex expected pin to compare against ({ex.hermes_sha!r})")
    if bundle is None:
        return _unobserved(name, "/opt/hermes/HERMES_SHA")
    got = bundle.get("hermes_sha")
    if got != ex.hermes_sha:
        return Verdict(name, False, f"image runs Hermes {got!r}, candidate pins {ex.hermes_sha}")
    return Verdict(name, True, f"Hermes {got}")


def check_no_runtime_env(bundle: dict[str, Any] | None) -> Verdict:
    """Boot-path audit item 2: no venv recorded in installs/*/facts.json, no /opt/data/tools.

    /opt/data/installs existing is not itself the defect on v0.22 (the package
    manager records facts for an external plugin there); a venv entry in those
    facts, or a tools tree, is Hermes building its own environment at boot.
    """
    name = "no-runtime-env-build"
    if bundle is None:
        return _unobserved(name, "/opt/data")
    fs = bundle.get("filesystem") or {}
    if "opt_data_tools" not in fs:
        return Verdict(name, False, "the filesystem probe did not run")
    venvs = {p: keys for p, keys in (fs.get("install_facts_venv_keys") or {}).items() if keys}
    if fs.get("opt_data_tools") or venvs:
        built = (["/opt/data/tools exists"] if fs.get("opt_data_tools") else []) + [
            f"{p} records {', '.join(k[:3])}" for p, k in venvs.items()
        ]
        return Verdict(name, False, "Hermes built a runtime environment at boot: " + "; ".join(built))
    note = "present, no venv entry" if fs.get("opt_data_installs") else "absent"
    return Verdict(name, True, f"no /opt/data/tools; /opt/data/installs {note}")


def check_broker(bundle: dict[str, Any] | None, logs: str) -> Verdict:
    name = "broker-accepts-gateway-calls"
    rows = AUDIT_ROW.findall(logs)
    if not rows:
        return Verdict(name, False, "no activation audit-row line: the gateway never wrote through the broker")
    before, after = (int(x) for x in rows[-1])
    if after <= before:
        return Verdict(name, False, f"activation audit write did not land (before={before} after={after})")
    health = _selftest_checks(bundle).get("broker-socket-answers-health")
    if not health or not health.get("ok"):
        return Verdict(
            name, False, f"self-test broker-socket-answers-health: {(health or {}).get('detail', 'did not run')}"
        )
    return Verdict(name, True, f"gateway-origin audit row landed (before={before} after={after}); broker health ok")


def check_cron(bundle: dict[str, Any] | None) -> Verdict:
    name = "managed-cron-fires"
    if bundle is None:
        return _unobserved(name, "the cron probe")
    cron = bundle.get("cron") or {}
    if not cron.get("planted"):
        return Verdict(
            name, False, f"could not schedule a managed cron: {str(cron.get('detail', 'probe did not run'))[:300]}"
        )
    if not cron.get("fired"):
        return Verdict(name, False, f"scheduled on {cron.get('persona')} but never fired: {str(cron.get('job'))[:200]}")
    return Verdict(name, True, f"op-managed probe scheduled on {cron.get('persona')} and fired")


def check_log_clean(logs: str, allow: list[AllowRule]) -> Verdict:
    name = "boot-log-clean"
    lines = logs.splitlines()
    bad = []
    for i, ln in enumerate(lines):
        if not ERROR_LINE.search(ln):
            continue
        # A traceback is judged as its whole block (the line before names the
        # thread, the lines after the exception), so an allow rule can name the
        # exact failure rather than every traceback.
        text = "\n".join(lines[max(0, i - 1) : i + TRACEBACK_CONTEXT]) if "Traceback" in ln else ln
        if not any(r.pattern.search(text) for r in allow):
            bad.append(ln.strip())
    if bad:
        return Verdict(name, False, f"{len(bad)} unallowlisted error line(s); first: {bad[0][:240]}")
    return Verdict(name, True, f"no ERROR/CRITICAL/FATAL/Traceback outside the {len(allow)}-rule allowlist")


def judge(
    bundle: dict[str, Any] | None,
    machine_log: str,
    ex: Expect,
    allow: list[AllowRule],
    ready_after_s: float | None = None,
) -> list[Verdict]:
    logs = all_logs(bundle, machine_log)
    return [
        check_boot_ready(bundle, logs, ready_after_s, ex),
        check_activation(logs),
        check_subplugins(logs, ex),
        check_overlay_imports(bundle, logs),
        check_webhook(bundle, logs),
        check_selftest(bundle),
        check_standalone(bundle),
        check_hermes_sha(bundle, ex),
        check_no_runtime_env(bundle),
        check_broker(bundle, logs),
        check_cron(bundle),
        check_log_clean(logs, allow),
        *audit.checks(bundle, logs, ex),
    ]
