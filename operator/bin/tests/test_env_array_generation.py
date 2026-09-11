"""Phase B Cut B — the generated env arrays MATCH the contract, and the
committed file is in sync.

bootstrap.sh sources operator/templates/_env-arrays.generated.sh instead of
hardcoding REQUIRED_ENV / OPTIONAL_ENV. This test is the CI guard that the
committed file is exactly what the generator would produce from
operator/contracts/env-consumption.yaml — so a contract edit that wasn't
followed by a regen is a red check, caught before deploy, never on a live boot.

Run::

    cd operator && python3 -m pytest bin/tests/test_env_array_generation.py -v
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_BIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BIN / "lib"))

import env_arrays as ea  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)

_ROOT = ea.repo_root()


def _committed() -> str:
    return ea.generated_path(_ROOT).read_text(encoding="utf-8")


def _bash_array(text: str, name: str) -> list[str]:
    m = re.search(rf"{name}=\((.*?)\)", text, re.DOTALL)
    assert m, f"{name} not found in generated file"
    out: list[str] = []
    for line in m.group(1).splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def test_committed_file_in_sync_with_contract() -> None:
    """The committed generated file == a fresh render from the contract.

    This is the `git diff --exit-code` guarantee, in-test: edit env-consumption
    without regenerating and CI goes red."""
    expected = ea.render_from_contract(ea.contract_path(_ROOT))
    assert _committed() == expected, (
        "operator/templates/_env-arrays.generated.sh is OUT OF SYNC with "
        "operator/contracts/env-consumption.yaml. Run: python3 operator/bin/gen-env-arrays.py"
    )


def test_arrays_match_contract_projection() -> None:
    """The arrays in the committed file equal the contract projection."""
    required, optional = ea.compute(ea.load_contract(ea.contract_path(_ROOT)))
    text = _committed()
    assert _bash_array(text, "REQUIRED_ENV") == required
    assert _bash_array(text, "OPTIONAL_ENV") == optional


def test_required_only_machine_reaching_required_vars() -> None:
    """Every REQUIRED_ENV var is requirement:required at a Machine-reaching stage —
    no provisioning-host / image-build var can sneak into the boot-required set."""
    vars_ = ea.load_contract(ea.contract_path(_ROOT))
    for name in _bash_array(_committed(), "REQUIRED_ENV"):
        spec = vars_[name]
        assert spec["requirement"] == "required", f"{name} in REQUIRED_ENV but contract says optional"
        assert spec["stage"] in ea.MACHINE_STAGES, f"{name} stage {spec['stage']} does not reach the Machine"


def test_cardinality_sane() -> None:
    """Non-empty required set, no overlap, total within the declared universe."""
    vars_ = ea.load_contract(ea.contract_path(_ROOT))
    required = _bash_array(_committed(), "REQUIRED_ENV")
    optional = _bash_array(_committed(), "OPTIONAL_ENV")
    assert required, "REQUIRED_ENV is empty — a broken projection would silently weaken boot"
    assert not (set(required) & set(optional)), "a var appears in both REQUIRED_ENV and OPTIONAL_ENV"
    assert len(required) + len(optional) <= len(vars_), "more arrayed vars than the contract declares"
