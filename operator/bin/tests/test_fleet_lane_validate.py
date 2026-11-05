"""The lane-input preflight asks the release's own validator about the seat's staged inputs.

2026-11-04 and 2026-11-05: a package rename, then one import line, moved every lane's schema-module
digest; the preflight refused every seat on the bytes alone and a person declared each change
harmless. Each test names its falsifier.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bin.lib.fleet.lane_validate import LANE_VALIDATORS, LaneValidator
from bin.lib.fleet.build import LANE_SCHEMA_MODULES
from bin.lib.fleet.preflight import LANE_VAULT_MARKERS, PreflightChecker, check_lanes, refused_lanes
from bin.lib.fleet.runner import CommandRunner, Result
from bin.tests.fleet_fakes import FakeFly, FakeR2, release

REPO = Path(__file__).resolve().parents[3]
GOOD = b"per_job_cap_usd: 5\nmonthly_budget_usd: 50\nusd_per_page: 0.01\n"
BAD = b"per_job_cap_usd: -1\nmonthly_budget_usd: 50\nusd_per_page: 0.01\nsurprise: 1\n"


def _head() -> str:
    out = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], check=True, capture_output=True, text=True)  # noqa: S603 - fixed git argv in a test
    return out.stdout.strip()


def _validator(marker: bytes | None) -> tuple[LaneValidator, FakeR2]:
    r2 = FakeR2()
    if marker is not None:
        r2.objects["vaults/smd-staging/records/records-firm.yaml"] = marker
    return LaneValidator(CommandRunner(), REPO, r2), r2


def test_every_lane_the_gate_digests_has_a_validator_and_a_marker() -> None:
    """FALSIFIER: add a lane to fleet build's digests without naming its validator here, and that
    lane's change would always fall back to the bytes-only refusal without anyone noticing."""
    assert set(LANE_VALIDATORS) == set(LANE_SCHEMA_MODULES) == set(LANE_VAULT_MARKERS)
    for lane, (marker, module) in LANE_VALIDATORS.items():
        assert (REPO / "operator/runners/jobs" / (module.replace(".", "/") + ".py")).is_file(), lane
        assert marker.startswith(LANE_VAULT_MARKERS[lane]), lane


def test_the_release_s_own_validator_judges_the_staged_marker() -> None:
    """Runs for real: git archive of this commit's runner package, the records validator in a child
    interpreter. FALSIFIER: return True without running the child and the bad marker passes."""
    sha = _head()
    good, _ = _validator(GOOD)
    assert good.validates("smd-staging", "records", sha) is True
    bad, _ = _validator(BAD)
    assert bad.validates("smd-staging", "records", sha) is False


def test_a_question_that_cannot_be_asked_is_none_never_a_pass() -> None:
    """FALSIFIER: treat any of these as True and a seat is passed by a broken instrument."""
    sha = _head()
    absent, _ = _validator(None)
    assert absent.validates("smd-staging", "records", sha) is None  # no marker in the vault
    good, r2 = _validator(GOOD)
    assert good.validates("smd-staging", "records", "0" * 40) is None  # a commit this clone never saw
    assert good.validates("smd-staging", "records", None) is None
    assert good.validates("smd-staging", "nosuchlane", sha) is None
    assert good.validates("smd-staging", "demand", sha) is None  # this seat stages no demand marker
    r2.fail_reads = True
    assert good.validates("smd-staging", "records", sha) is None  # the vault is unreachable
    broken = LaneValidator(CommandRunner(), REPO, _validator(GOOD)[1], python="/nonexistent/python")
    assert broken.validates("smd-staging", "records", sha) is None  # the child could not start


def test_the_validators_words_never_reach_the_caller() -> None:
    """A validator's message can quote a firm's own values; only a count crosses.
    FALSIFIER: print the problems in the child and this output carries the key name."""

    class Spy(CommandRunner):
        def __init__(self) -> None:
            self.results: list[Result] = []

        def run(self, argv, **kw):  # type: ignore[no-untyped-def]
            res = super().run(argv, **kw)
            self.results.append(res)
            return res

    spy = Spy()
    r2 = FakeR2()
    r2.objects["vaults/smd-staging/records/records-firm.yaml"] = BAD
    assert LaneValidator(spy, REPO, r2).validates("smd-staging", "records", _head()) is False
    child = spy.results[-1]
    assert child.stdout.strip() == "problems=2" and "surprise" not in child.text and "per_job_cap_usd" not in child.text


def test_check_lanes_takes_the_validator_s_answer_only_where_the_digest_differs() -> None:
    asked: list[str] = []

    def answer(value: bool | None):
        def validated(lane: str) -> bool | None:
            asked.append(lane)
            return value

        return validated

    # The digest matches: nothing is asked.
    assert check_lanes("s", {"records": "a"}, {"records": "a"}, ["records"], False, [], answer(False)) == ([], [])
    assert asked == []
    # It differs and the staged marker passes: a note, no problem, nobody declares anything.
    problems, notes = check_lanes("s", {"records": "new"}, {"records": "old"}, ["records"], False, [], answer(True))
    assert problems == [] and "passes this release's own validator" in notes[0]
    # It differs and the validator finds problems: refused, in words the retry reads as a lane refusal.
    problems, notes = check_lanes("s", {"records": "new"}, {"records": "old"}, ["records"], False, [], answer(False))
    assert (
        notes == []
        and "do not pass this release's validator; re-stage with operator/bin/reprovision.sh s" in problems[0]
    )
    assert refused_lanes("preflight refused: " + problems[0]) == {"records"}
    # FALSIFIER: it could not be asked, and the refusal is the one the gate always gave.
    problems, _ = check_lanes("s", {"records": "new"}, {"records": "old"}, ["records"], False, [], answer(None))
    assert "the lane's firm-input schema changed" in problems[0]
    problems, _ = check_lanes("s", {"records": "new"}, None, ["records"], False, [], answer(None))
    assert "recorded no lane-input digests" in problems[0]
    # An image from before fleet build recorded digests is judged the same way.
    problems, notes = check_lanes("s", {"records": "new"}, None, ["records"], False, [], answer(True))
    assert problems == [] and notes
    # A declaration is a person's call and is read first; --accept-lane-inputs never asks.
    asked.clear()
    decl = [{"since": "1" * 40, "lanes": ["records"], "by": "x", "date": "d", "why": "w"}]
    problems, notes = check_lanes("s", {"records": "new"}, {"records": "old"}, ["records"], False, decl, answer(False))
    assert problems == [] and "declared unchanged" in notes[0] and asked == []
    problems, notes = check_lanes("s", {"records": "new"}, {"records": "old"}, ["records"], True, [], answer(False))
    assert problems == [] and "accepted by --accept-lane-inputs" in notes[0] and asked == []


class _Stub:
    def __init__(self, answers: dict[str, bool | None]) -> None:
        self.answers = answers

    def validates(self, seat: str, lane: str, target_sha: str | None) -> bool | None:
        return self.answers.get(lane)


def test_a_halted_refusal_is_retried_once_the_staged_inputs_pass() -> None:
    """A firm's yaml is corrected and republished, or the validator could not be asked the first time:
    the tick's retry question now passes without a declaration or a laptop.
    FALSIFIER: require a declaration for every lane and this stays halted."""
    rel = {**release(), "ss_console_sha": "2" * 40}
    refused = (
        "preflight refused: lane records: the seat's staged inputs do not pass this release's validator; "
        "re-stage with operator/bin/reprovision.sh s; lane demand: the lane's firm-input schema changed; "
        "re-stage with operator/bin/reprovision.sh s"
    )

    def checker(answers: dict[str, bool | None]) -> PreflightChecker:
        return PreflightChecker(FakeFly(), FakeR2(), None, _Stub(answers))  # type: ignore[arg-type]

    assert checker({"records": True, "demand": True}).refusal_now_declared("s", rel, refused, "1" * 40) is True
    assert checker({"records": True, "demand": False}).refusal_now_declared("s", rel, refused, "1" * 40) is False
    assert checker({"records": True, "demand": None}).refusal_now_declared("s", rel, refused, "1" * 40) is False
    assert checker({}).refusal_now_declared("s", rel, "preflight refused: required env missing: X", "1" * 40) is False


@pytest.mark.parametrize("lane", sorted(LANE_VALIDATORS))
def test_each_lane_s_validator_imports_in_a_bare_interpreter(lane: str) -> None:
    """The child has this interpreter and pyyaml and nothing else of the runner's requirements.
    FALSIFIER: a lane's schema module starts importing a package the fleet CLI's venv lacks, and its
    answer silently becomes None (the bytes-only refusal) on every release."""
    r2 = FakeR2()
    marker, _ = LANE_VALIDATORS[lane]
    r2.objects[f"vaults/smd-staging/{marker}"] = b"{}\n"
    # An empty map is not valid for any lane; the point is that the validator RAN and counted.
    assert LaneValidator(CommandRunner(), REPO, r2).validates("smd-staging", lane, _head()) is False
