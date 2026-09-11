"""The shipped registry loads, covers every incident class, and degrades honestly.

Two things are pinned here that no scenario file can pin about itself: that the
suite as shipped still replays every incident class we have actually had, and
that running it without credentials produces SKIPPED and a non-zero exit rather
than a quiet green.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rehearsal import registry, run as runner
from rehearsal.scoring import EXPECT_KINDS, PASS, SKIPPED

REPO_ROOT = Path(__file__).resolve().parents[3]

#: One scenario per incident class we have had. Deleting a scenario should cost
#: a conversation about which incident we have decided to stop rehearsing.
REQUIRED_INCIDENT_CLASSES = {
    "unaudited_send",
    "cross_matter_send",
    "fabrication_under_failure",
    "instruction_injection",
    "degraded_dependency",
    "authority_confusion",
}


def test_the_shipped_registry_loads_and_validates() -> None:
    scenarios = registry.load_scenarios()
    assert len(scenarios) >= 6


def test_every_known_incident_class_is_rehearsed() -> None:
    classes = {s["incident_class"] for s in registry.load_scenarios()}
    missing = REQUIRED_INCIDENT_CLASSES - classes
    assert not missing, f"incident classes with no standing rehearsal: {sorted(missing)}"


def test_every_scenario_names_the_incident_it_replays_and_its_own_falsifier() -> None:
    for scenario in registry.load_scenarios():
        assert scenario["replays"], f"{scenario['id']} replays nothing"
        assert len(str(scenario["falsifier"]).split()) >= 15, (
            f"{scenario['id']}: a falsifier that does not say what would make this scenario meaningless is decoration"
        )


def test_every_expectation_kind_used_is_one_the_scorer_evaluates() -> None:
    for scenario in registry.load_scenarios():
        for leg in scenario["legs"]:
            for expectation in leg["expect"]:
                assert expectation["kind"] in EXPECT_KINDS


def test_the_connector_outage_scenario_requires_a_declared_fault() -> None:
    """Otherwise it grades a healthy seat and calls the degraded path proven."""
    scenario = next(s for s in registry.load_scenarios() if s["id"] == "connector-down-mid-task")
    assert "fault_injection" in scenario["requires"]


class _RigOnTheCandidate:
    """A rig seam that answers with whatever ref the run is certifying.

    Lets the ss#2531 running-ref gate pass so the check below can be about the
    thing it was written for. The gate's own refusals are pinned in
    test_running_ref_gate.py.
    """

    def __init__(self, ref: str) -> None:
        self._ref = ref

    def read_config(self) -> dict:
        return {"schema": "x", "overlay_ref": {"value": self._ref, "source": "direct_url"}}


def test_running_without_credentials_skips_and_exits_non_zero(tmp_path, monkeypatch) -> None:
    """The honest-degradation contract, exercised end to end.

    No AgentMail key, no audit seam: every scenario must report SKIPPED with a
    reason, the report must say NOT GREEN, and the process must not exit 0. A
    suite that exits 0 here would let a release gate cite a run that touched
    nothing.
    """
    for variable in ("AGENTMAIL_API_KEY", "OPERATOR_RUNTIME_READ_SECRET", "OPERATOR_RUNTIME_READ_URL"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(runner, "make_seam_client", lambda slug: _RigOnTheCandidate(runner.pinned_overlay_ref()))
    code = runner.main(["--seat", "pilot-smokeball", "--drive", "--out", str(tmp_path)])
    assert code == runner.EXIT_INCOMPLETE
    reports = sorted(tmp_path.glob("*.md"))
    assert len(reports) == 1
    body = reports[0].read_text()
    assert "NOT GREEN" in body
    rows = [line for line in body.splitlines() if line.startswith("| `")]
    assert rows, "the report has no scenario table"
    assert all(f"| {SKIPPED} |" in row for row in rows), rows
    assert not any(f"| {PASS} |" in row for row in rows), rows
    assert reports[0].stem.endswith("-notgreen")


def test_the_runner_refuses_a_client_seat_before_driving_anything(tmp_path, capsys) -> None:
    code = runner.main(["--seat", "ashton-price", "--drive", "--out", str(tmp_path)])
    assert code == runner.EXIT_REFUSED
    assert not list(tmp_path.glob("*"))
    assert "REFUSED" in capsys.readouterr().err


def test_the_runner_refuses_an_unknown_seat(tmp_path) -> None:
    assert runner.main(["--seat", "no-such-seat", "--drive", "--out", str(tmp_path)]) == runner.EXIT_REFUSED


def test_the_suite_sends_nothing_unless_it_is_armed(tmp_path, capsys) -> None:
    """Arming is explicit, and this test is why.

    During development an invocation meant as a dry run put three live probes
    into the rig seat inside a minute, because the AgentMail and seam
    credentials are ambient in any shell that has run `infisical run`. A tool
    that fires adversarial mail the moment it is typed is one shell environment
    away from firing it at something that matters. Without --drive the runner
    plans, writes no report, and exits non-zero.
    """
    code = runner.main(["--seat", "pilot-smokeball", "--out", str(tmp_path)])
    assert code == runner.EXIT_REFUSED
    assert not list(tmp_path.glob("*")), "a plan-only invocation must produce no run artifact"
    out = capsys.readouterr().out
    assert "PLAN ONLY" in out and "Nothing sent" in out


def test_list_drives_nothing(capsys) -> None:
    assert runner.main(["--list"]) == runner.EXIT_GREEN
    assert "Nothing driven." in capsys.readouterr().out


def test_the_overlay_ref_default_is_read_from_the_dockerfile() -> None:
    """The run certifies a ref, so the ref cannot be a value someone types in."""
    ref = runner.pinned_overlay_ref()
    assert ref != "unknown" and len(ref) >= 7


@pytest.mark.parametrize(
    "document",
    [
        REPO_ROOT / "docs" / "handbook" / "deployment-release.md",
        REPO_ROOT / "docs" / "runbooks" / "operator" / "shadow-firm.md",
    ],
)
def test_the_release_gate_is_documented_where_the_bump_is_performed(document: Path) -> None:
    """Wiring a gate into a doc nobody reads at bump time is not wiring it.

    Pinned so the requirement cannot be quietly dropped from the procedure while
    the suite keeps existing and nobody runs it.
    """
    text = document.read_text()
    assert "shadow firm" in text.lower()
    assert "run id" in text.lower()
