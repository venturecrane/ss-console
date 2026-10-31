"""The one predicate for what a seat authors (ADR 0100): shared/capabilities.py."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from shared import capabilities as cap

OVERLAY = Path(__file__).resolve().parents[1]
OPERATOR = OVERLAY.parent
RUNNER = OPERATOR / "runners" / "medchron" / "medchron"


def _doc(*skills: str | dict, connectors: dict | None = None) -> dict:
    entries = [s if isinstance(s, dict) else {"name": s} for s in skills]
    return {"personas": [{"name": "operator", "skills": entries}], "connectors": connectors or {}}


def test_a_lane_is_authored_by_enabling_its_skill() -> None:
    assert cap.authored_lanes(_doc("litigation-status", "inbox-triage")) == ["litigation"]


def test_a_disabled_skill_authors_nothing() -> None:
    assert cap.authored_lanes(_doc({"name": "litigation-status", "enabled": False})) == []


def test_lanes_come_back_in_registry_order_whatever_the_persona_order() -> None:
    doc = _doc("smd-note", "medical-chronology-maintainer", "demand-letter-drafter")
    assert cap.authored_lanes(doc) == ["medchron", "demand", "note"]


def test_a_seat_with_no_lane_skill_runs_no_lane() -> None:
    assert cap.authored_lanes(_doc("couples-checkin", "partner-messages")) == []
    assert cap.authored_lanes(None) == []
    assert cap.authored_lanes({"personas": "not-a-list"}) == []


def test_a_connector_capability_is_authored_by_binding_its_backend() -> None:
    bound = _doc(connectors={"PracticeManagement": {"backend": "mcp:smokeball"}})
    assert cap.authors_connector(bound, "smokeball")
    off = _doc(connectors={"PracticeManagement": {"backend": "mcp:smokeball", "enabled": False}})
    assert not cap.authors_connector(off, "smokeball")
    other = _doc(connectors={"Email": {"backend": "build:msgraph-mail"}})
    assert not cap.authors_connector(other, "smokeball")


def test_an_unknown_connector_capability_is_an_error_not_a_quiet_no() -> None:
    with pytest.raises(KeyError):
        cap.authors_connector(_doc(), "smokebal")


def test_the_registry_agrees_with_the_runner_lane_skills() -> None:
    """Each runner lane module's own SKILL constant is the skill the registry keys on."""
    modules = {
        "demand": "demand_lane.py",
        "drafting": "drafting_lane.py",
        "litigation": "litigation_lane.py",
        "casefile": "casefile_lane.py",
        "negotiation": "negotiation_lane.py",
        "note": "note_lane.py",
    }
    for lane, name in modules.items():
        tree = ast.parse((RUNNER / name).read_text(encoding="utf-8"))
        skill = next(
            node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "SKILL" for t in node.targets)
            and isinstance(node.value, ast.Constant)
        )
        assert cap.JOB_LANES[lane] == skill, lane
    assert set(cap.JOB_LANES) == {"medchron", *modules}


def test_every_lane_skill_is_a_shipped_skill() -> None:
    for skill in cap.JOB_LANES.values():
        assert (OPERATOR / "skills" / skill / "SKILL.md").is_file(), skill


def test_the_cli_is_what_boot_reads(tmp_path: Path) -> None:
    import yaml

    path = tmp_path / "customer.yaml"
    path.write_text(
        yaml.safe_dump(_doc("smd-note", connectors={"P": {"backend": "mcp:smokeball"}}))
    )
    script = OVERLAY / "shared" / "capabilities.py"
    lanes = subprocess.run(
        [sys.executable, "-I", str(script), "lanes", str(path)], capture_output=True, text=True
    )  # noqa: S603 - the module under test, by path
    assert lanes.returncode == 0 and lanes.stdout.split() == ["note"]
    yes = subprocess.run([sys.executable, "-I", str(script), "connector", "smokeball", str(path)])  # noqa: S603 - the module under test, by path
    assert yes.returncode == 0
    (tmp_path / "bare.yaml").write_text("personas: []\n")
    no = subprocess.run(
        [sys.executable, "-I", str(script), "connector", "smokeball", str(tmp_path / "bare.yaml")]
    )  # noqa: S603 - the module under test, by path
    assert no.returncode == 1


def test_an_unreadable_config_authors_nothing_and_says_why(tmp_path: Path) -> None:
    script = OVERLAY / "shared" / "capabilities.py"
    out = subprocess.run(  # noqa: S603 - the module under test, by path
        [sys.executable, "-I", str(script), "lanes", str(tmp_path / "missing.yaml")],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0 and out.stdout == "" and "cannot read" in out.stderr


def test_the_real_seats_author_what_their_configs_say() -> None:
    """Pins today's fleet so a config edit that drops a client's lane is a visible diff here."""

    def seat(slug: str) -> dict:
        return cap.read(OPERATOR / "customers" / slug / "customer.yaml") or {}

    assert cap.authored_lanes(seat("ashton-price")) == list(cap.JOB_LANES)
    assert cap.authors_connector(seat("ashton-price"), "smokeball")
    assert cap.authors_connector(seat("pilot-smokeball"), "smokeball")
    for slug in ("captain-mark", "scott", "brokery", "ashton-price-marketing"):
        assert cap.authored_lanes(seat(slug)) == [], slug
        assert not cap.authors_connector(seat(slug), "smokeball"), slug
