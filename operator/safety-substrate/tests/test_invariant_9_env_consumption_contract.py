"""Invariant 9 — env-consumption contract consistency (fail-closed at boot).

The headline guard against the voice-class regression: a variable the AGENT
consumes can never be marked stripped from the agent env. Phase 1 security
stripped the account-wide R2 key not knowing the voice plugin read it
(OP-P0-2); this invariant turns that contradiction into a boot failure at the
DECLARATION level, caught before the gateway starts.

This is a STATIC consistency check over operator/contracts/env-consumption.yaml
(no live-env probe, no grep), so it cannot false-positive and brick a healthy
Machine — only a genuinely inconsistent contract fails it. The complementary
"does the contract match bootstrap's actual REQUIRED/OPTIONAL/strip behaviour"
check is CI-side (tests/operator-env-contract conformance).
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve()

_VALID_STAGES = {
    "provisioning-host",
    "image-build",
    "boot",
    "agent",
    "broker",
    "mcp-subprocess",
}
_VALID_REQUIREMENT = {"required", "optional"}
_VALID_AGENT_ENV = {"held", "stripped", "n/a"}
_OFF_MACHINE = {"provisioning-host", "image-build"}


def _contract_path() -> Path | None:
    for cand in (
        Path("/app/contracts/env-consumption.yaml"),  # image runtime
        _HERE.parents[2] / "contracts" / "env-consumption.yaml",  # repo/CI
    ):
        if cand.is_file():
            return cand
    return None


def run() -> tuple[bool, str]:
    path = _contract_path()
    if path is None:
        return (
            False,
            "FAIL: env-consumption contract not found (expected /app/contracts/env-consumption.yaml)",
        )
    try:
        import yaml
    except ModuleNotFoundError:
        # Don't brick boot on a missing parser in a stripped venv; CI enforces
        # the same checks. yaml is present in the Machine venv at boot.
        return True, "SKIP: yaml unavailable in this interpreter; CI enforces the contract"

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    vars_ = data.get("vars")
    if not isinstance(vars_, dict) or not vars_:
        return False, "FAIL: env-consumption contract has no `vars` map"

    violations: list[str] = []
    for name, spec in vars_.items():
        if not isinstance(spec, dict):
            violations.append(f"{name}: not a mapping")
            continue
        stage = spec.get("stage")
        requirement = spec.get("requirement")
        agent_env = spec.get("agent_env")
        if stage not in _VALID_STAGES:
            violations.append(f"{name}: invalid stage {stage!r}")
        if requirement not in _VALID_REQUIREMENT:
            violations.append(f"{name}: invalid requirement {requirement!r}")
        if agent_env not in _VALID_AGENT_ENV:
            violations.append(f"{name}: invalid agent_env {agent_env!r}")
        # HEADLINE GUARD: an agent-consumed var MUST survive into the agent env.
        if stage == "agent" and agent_env == "stripped":
            violations.append(
                f"{name}: stage:agent but agent_env:stripped — the agent needs this "
                "var; stripping it is the voice-class regression"
            )
        # stripped => must name where it is stripped
        if agent_env == "stripped" and not spec.get("strip_site"):
            violations.append(f"{name}: agent_env:stripped but no strip_site named")
        # off-machine stages never have an agent env
        if stage in _OFF_MACHINE and agent_env != "n/a":
            violations.append(f"{name}: stage:{stage} must have agent_env:n/a, got {agent_env!r}")
        # on-machine stages must declare held|stripped, not n/a
        if stage not in _OFF_MACHINE and stage in _VALID_STAGES and agent_env == "n/a":
            violations.append(f"{name}: on-machine stage:{stage} must be held or stripped, not n/a")

    if violations:
        return (
            False,
            "FAIL: env-consumption contract inconsistent:\n  - " + "\n  - ".join(violations),
        )
    return (
        True,
        f"PASS: env-consumption contract consistent ({len(vars_)} vars; no agent-needed var is stripped)",
    )


if __name__ == "__main__":
    ok, msg = run()
    print(msg)
    sys.exit(0 if ok else 1)
