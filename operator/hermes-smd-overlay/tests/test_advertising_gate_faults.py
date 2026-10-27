"""A policy fault keeps the advertising gate ON on a marketing seat, and only there.

The 10-27 review (OC-1) proved three faults returned a fallback with the gate
OFF, so a law-firm marketing seat's sends skipped the California
attorney-advertising check whenever its customer.yaml could not be read, its
role file was missing, or its role file was unreadable. Off is the looser
direction (ADR 0098), so each of those must resolve ON for a seat that could be
a marketing seat. The other half matters as much: with no registers an ON gate
blocks every send, so a fault on a couples seat or another firm's seat must
leave the gate OFF.

The marketing marks are seat-local (``shared/policy_profile`` docstring): the
registers file ``entrypoint-marketing.sh`` stages only on a marketing seat, and
that script's lane state file. Both are pointed into ``tmp_path`` here, so the
test never reads the host's ``/var/lib`` or ``/run``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from shared import advertising_gate, policy_profile

_REPO = Path(__file__).resolve().parents[3]
_ROLES = _REPO / "operator" / "roles"


class _Unreadable:
    """A CustomerConfig whose raw document cannot be read."""

    @property
    def raw(self):
        raise OSError("customer.yaml unreadable")


@pytest.fixture
def unmarked(tmp_path, monkeypatch) -> Path:
    """A seat with neither marketing mark: the couples / other-firm case.

    The env names are literal on purpose: they are the entrypoint's contract
    (entrypoint-fetch-inputs.sh, entrypoint-marketing.sh), not this module's.
    """
    monkeypatch.setenv("SMD_MARKETING_REGISTERS", str(tmp_path / "no-registers.yaml"))
    monkeypatch.setenv("SMD_LANE_INPUTS_STATE_DIR", str(tmp_path / "run-smd-lanes"))
    return tmp_path


def _mark_registers(tmp_path: Path) -> None:
    (tmp_path / "no-registers.yaml").write_text("firm: {}\n")


def _mark_state(tmp_path: Path, state: str = "absent") -> None:
    d = tmp_path / "run-smd-lanes"
    d.mkdir(exist_ok=True)
    (d / "marketing.inputs").write_text(state + "\n")


def _bad_manifest(tmp_path: Path, role: str, body: str) -> str:
    roles = tmp_path / "roles"
    (roles / role).mkdir(parents=True, exist_ok=True)
    (roles / role / "role.yaml").write_text(body)
    return str(roles)


MARKETING = {"seat": {"role": "marketing"}}
COUPLES = {"seat": {"role": "couples"}}


# ---- the audit's three faults, on a marketing seat ------------------------
def test_unreadable_config_on_a_marked_seat_keeps_the_gate_on(unmarked) -> None:
    _mark_registers(unmarked)
    p = policy_profile.current(_Unreadable())
    assert p.source == "fallback:config_unreadable"
    assert p.advertising_gate == policy_profile.ADVERTISING_CA_LAW_FIRM
    assert advertising_gate.enabled(p)


def test_unreadable_config_with_only_the_lane_state_file_keeps_the_gate_on(unmarked) -> None:
    # A marketing seat whose registers were never fetched still ran the fetch,
    # and the state file says so. Its gate stays on (and blocks: no registers).
    _mark_state(unmarked, "absent")
    assert policy_profile.current(_Unreadable()).advertising_gate == "ca-law-firm"


def test_missing_role_file_on_the_marketing_role_keeps_the_gate_on(unmarked) -> None:
    p = policy_profile.current(MARKETING, roles_dir_override=str(unmarked / "nowhere"))
    assert p.source == "fallback:marketing:no_role_file"
    assert p.advertising_gate == "ca-law-firm"


@pytest.mark.parametrize(
    ("body", "cause"),
    [("role: [unclosed\n", "unreadable"), ("role: couples\n", "wrong_manifest")],
)
def test_unusable_role_file_on_the_marketing_role_keeps_the_gate_on(unmarked, body, cause) -> None:
    roles = _bad_manifest(unmarked, "marketing", body)
    p = policy_profile.current(MARKETING, roles_dir_override=roles)
    assert p.source == f"fallback:marketing:{cause}"
    assert p.advertising_gate == "ca-law-firm"


def test_a_garbled_role_slug_on_a_marked_seat_keeps_the_gate_on(unmarked) -> None:
    _mark_registers(unmarked)
    p = policy_profile.current({"seat": {"role": "Marketing!"}})
    assert p.source == "fallback:bad_role_slug"
    assert p.advertising_gate == "ca-law-firm"


# ---- the same faults never switch the gate on elsewhere -------------------
def test_unreadable_config_on_an_unmarked_seat_leaves_the_gate_off(unmarked) -> None:
    assert policy_profile.current(_Unreadable()).advertising_gate is None


def test_missing_role_file_on_the_couples_role_leaves_the_gate_off(unmarked) -> None:
    p = policy_profile.current(COUPLES, roles_dir_override=str(unmarked / "nowhere"))
    assert p.source == "fallback:couples:no_role_file"
    assert p.advertising_gate is None


@pytest.mark.parametrize("body", ["role: [unclosed\n", "role: marketing\n"])
def test_unusable_role_file_on_the_couples_role_leaves_the_gate_off(unmarked, body) -> None:
    roles = _bad_manifest(unmarked, "couples", body)
    assert policy_profile.current(COUPLES, roles_dir_override=roles).advertising_gate is None


def test_the_shipped_roles_resolve_as_authored(unmarked) -> None:
    assert policy_profile.current(MARKETING, roles_dir_override=str(_ROLES)).advertising_gate == (
        "ca-law-firm"
    )
    assert policy_profile.current(COUPLES, roles_dir_override=str(_ROLES)).advertising_gate is None


# ---- the fault-path role set agrees with the shipped role files -----------
def test_advertising_roles_is_exactly_the_roles_that_declare_the_gate() -> None:
    declared = set()
    for role_file in sorted(_ROLES.glob("*/role.yaml")):
        manifest = yaml.safe_load(role_file.read_text())
        policy = manifest.get("policy") or {}
        if isinstance(policy, dict) and "advertising_gate" in policy:
            declared.add(manifest["role"])
    assert declared, "no shipped role declares advertising_gate; the fixture moved"
    assert declared == set(policy_profile.ADVERTISING_ROLES)


def test_the_gate_and_the_fault_path_read_the_same_registers_path(unmarked) -> None:
    assert advertising_gate.registers_path() == policy_profile.marketing_registers_path()
    assert advertising_gate.REGISTERS_ENV == policy_profile.MARKETING_REGISTERS_ENV
