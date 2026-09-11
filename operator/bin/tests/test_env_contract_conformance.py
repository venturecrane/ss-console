"""Conformance: the env-consumption contract MATCHES the scripts' reality.

Invariant 9 (operator/safety-substrate) checks the contract is internally
consistent (no agent-consumed var marked stripped). THIS CI-side check verifies
the contract is not merely aspirational: bootstrap.sh's REQUIRED_ENV /
OPTIONAL_ENV arrays and the actual `unset` strip sites must agree with the
contract. Drift here = red check, caught before deploy.

Run::

    cd operator && python -m pytest bin/tests/test_env_contract_conformance.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_OP = Path(__file__).resolve().parents[2]
_CONTRACT = _OP / "contracts" / "env-consumption.yaml"
_BOOTSTRAP = _OP / "templates" / "bootstrap.sh"
_ENTRYPOINT = _OP / "templates" / "entrypoint.sh"
# Phase B Cut B: REQUIRED_ENV / OPTIONAL_ENV are generated from the contract and
# live here (bootstrap.sh sources this file), not inline in bootstrap.sh.
_ENV_ARRAYS = _OP / "templates" / "_env-arrays.generated.sh"


def _contract() -> dict:
    return (yaml.safe_load(_CONTRACT.read_text(encoding="utf-8")) or {}).get("vars", {})


def _bash_array(text: str, name: str) -> set[str]:
    m = re.search(rf"{name}=\((.*?)\)", text, re.DOTALL)
    assert m, f"{name} array not found"
    out: set[str] = set()
    for line in m.group(1).splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(line)
    return out


def _unset_vars(text: str) -> set[str]:
    out: set[str] = set()
    for m in re.finditer(r"^\s*unset\s+(.+)$", text, re.MULTILINE):
        for tok in m.group(1).split():
            if re.fullmatch(r"[A-Z_][A-Z0-9_]*", tok):
                out.add(tok)
    return out


def test_required_env_is_declared_required() -> None:
    contract = _contract()
    for var in _bash_array(_ENV_ARRAYS.read_text(encoding="utf-8"), "REQUIRED_ENV"):
        assert var in contract, f"generated REQUIRED_ENV {var} missing from the contract"
        assert contract[var].get("requirement") == "required", (
            f"{var} is in REQUIRED_ENV but the contract marks it {contract[var].get('requirement')!r}"
        )


def test_optional_env_not_marked_required() -> None:
    contract = _contract()
    for var in _bash_array(_ENV_ARRAYS.read_text(encoding="utf-8"), "OPTIONAL_ENV"):
        if var in contract:
            assert contract[var].get("requirement") == "optional", (
                f"{var} is in OPTIONAL_ENV but the contract marks it required"
            )


def test_contract_bootstrap_strips_are_real() -> None:
    contract = _contract()
    bootstrap_unset = _unset_vars(_BOOTSTRAP.read_text(encoding="utf-8"))
    for var, spec in contract.items():
        if spec.get("agent_env") == "stripped" and str(spec.get("strip_site", "")).endswith("bootstrap.sh"):
            assert var in bootstrap_unset, (
                f"contract says {var} is stripped in bootstrap.sh, but there is no `unset {var}` there"
            )


def test_contract_entrypoint_strips_are_real() -> None:
    contract = _contract()
    entry_unset = _unset_vars(_ENTRYPOINT.read_text(encoding="utf-8"))
    for var, spec in contract.items():
        if spec.get("agent_env") == "stripped" and str(spec.get("strip_site", "")).endswith("entrypoint.sh"):
            assert var in entry_unset, (
                f"contract says {var} is stripped in entrypoint.sh, but there is no `unset {var}` there"
            )


def test_r2_account_wide_strip_is_declared() -> None:
    """The R2 account-wide strip in bootstrap (the voice-class site) must be
    declared stripped in the contract, so a future strip can't silently
    de-credential the agent without updating the contract that invariant 9
    then guards."""
    contract = _contract()
    bootstrap_unset = _unset_vars(_BOOTSTRAP.read_text(encoding="utf-8"))
    for var in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        assert var in bootstrap_unset, f"expected bootstrap.sh to unset {var}"
        assert contract.get(var, {}).get("agent_env") == "stripped", (
            f"{var} is unset in bootstrap but not declared stripped in the contract"
        )
