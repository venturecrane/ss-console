"""The release gate has to be able to fail.

WHY THIS FILE EXISTS (ss#2531). The runner stamps the candidate overlay ref into
the run id it emits, and the release gate asks a bump PR to cite "a green run on
the candidate ref". Until this gate existed the runner never checked that the
rig was RUNNING that ref: the candidate was a string it was handed, so a green
id certifying ref X could have been produced entirely by a rig still on the
previous release. Three bumps in a row (#2518, #2525, #2531) cited no id at all,
because the only order the docs described was impossible.

So the checks here are not "the comparison is correct". They are that the runner
REFUSES, and drives nothing, in each position where the running ref fails to
support the claim the run would make: a mismatch, a seat whose seam cannot be
read, a seat with no seam configured, and a snapshot that carries no ref at all.
The matching case is here too, because a gate that refuses everything is an
outage wearing a gate's clothes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rehearsal import drivers, run as runner

RIG = "pilot-smokeball"
CANDIDATE = "4dbf415"
CANDIDATE_FULL = "4dbf415a9c1e77c0b3d2e5f18a4460b7c9d3e2f1"
STALE = "78c2544be1a90d3f2c7b5468e0a1d9c3f7b26e50"


class _FakeClient:
    """A seam that answers with whatever ref the test says the rig is running."""

    def __init__(self, ref: str | None, *, raises: BaseException | None = None) -> None:
        self._ref = ref
        self._raises = raises

    def read_config(self) -> dict:
        if self._raises is not None:
            raise self._raises
        if self._ref is None:
            return {"schema": "x"}  # a snapshot with no overlay_ref at all
        return {"schema": "x", "overlay_ref": {"value": self._ref, "source": "direct_url"}}


def _seam(monkeypatch, client) -> None:
    monkeypatch.setattr(runner, "make_seam_client", lambda slug: client)


class _DriversReached(Exception):
    """Raised in place of the first act, to prove the gate let the run past it."""


def _tripwire(monkeypatch) -> None:
    """Make the first thing after the gate explode, so "was it reached" is a fact."""

    def _boom(*args, **kwargs):
        raise _DriversReached()

    monkeypatch.setattr(drivers, "probe_capabilities", _boom)


def _drive(tmp_path, ref: str = CANDIDATE) -> int:
    return runner.main(["--seat", RIG, "--drive", "--overlay-ref", ref, "--out", str(tmp_path)])


# --- the refusals ------------------------------------------------------------


def test_a_rig_on_a_different_ref_is_refused_and_nothing_is_driven(tmp_path, monkeypatch, capsys) -> None:
    """The whole point. A run against a stale rig cannot become a green id."""
    _seam(monkeypatch, _FakeClient(STALE))
    _tripwire(monkeypatch)
    assert _drive(tmp_path) == runner.EXIT_REFUSED
    assert not list(tmp_path.glob("*")), "a refused run must leave no artifact to cite"
    err = capsys.readouterr().err
    assert "REFUSED" in err
    assert STALE[:12] in err and CANDIDATE in err
    assert f"reprovision {RIG}" in err


def test_an_unreadable_seam_is_refused_rather_than_assumed_current(tmp_path, monkeypatch, capsys) -> None:
    """Fail closed. An unreachable rig is the absence of the evidence, not the evidence."""
    _seam(monkeypatch, _FakeClient(CANDIDATE_FULL, raises=OSError("machine down")))
    _tripwire(monkeypatch)
    assert _drive(tmp_path) == runner.EXIT_REFUSED
    assert not list(tmp_path.glob("*"))
    assert "cannot read the overlay ref" in capsys.readouterr().err


def test_a_seat_with_no_seam_configured_is_refused(tmp_path, monkeypatch, capsys) -> None:
    """``seam_client_from_env`` returns None when the env is unset. That is unknown, not current."""
    monkeypatch.setattr(runner, "make_seam_client", lambda slug: None)
    _tripwire(monkeypatch)
    assert _drive(tmp_path) == runner.EXIT_REFUSED
    assert "unconfigured" in capsys.readouterr().err


def test_a_snapshot_with_no_overlay_ref_is_refused(tmp_path, monkeypatch, capsys) -> None:
    """A reachable seat that reports no ref is still a seat whose ref we do not know."""
    _seam(monkeypatch, _FakeClient(None))
    _tripwire(monkeypatch)
    assert _drive(tmp_path) == runner.EXIT_REFUSED
    assert "unknown" in capsys.readouterr().err


def test_the_gate_runs_before_anything_is_driven(tmp_path, monkeypatch) -> None:
    """Ordering, pinned. If the gate ran after the probe the refusal would come too
    late: the probe is the step that reaches the seat and the mail API."""
    _seam(monkeypatch, _FakeClient(STALE))
    calls: list[tuple] = []
    monkeypatch.setattr(drivers, "probe_capabilities", lambda *a, **k: calls.append(a))
    assert _drive(tmp_path) == runner.EXIT_REFUSED
    assert calls == [], "the seat was probed despite a stale rig"


# --- the other direction, so the gate is not simply always-refuse ------------


def test_a_matching_rig_is_let_through_to_the_drivers(tmp_path, monkeypatch) -> None:
    _seam(monkeypatch, _FakeClient(CANDIDATE_FULL))
    _tripwire(monkeypatch)
    with pytest.raises(_DriversReached):
        _drive(tmp_path)


def test_a_short_candidate_matches_the_full_sha_the_rig_reports(tmp_path, monkeypatch) -> None:
    """The Dockerfile pins a short ref and the seam reports a full sha. Prefix
    tolerance is what makes the gate usable; without it the gate would refuse
    every correctly reprovisioned rig, and someone would delete it."""
    _seam(monkeypatch, _FakeClient(CANDIDATE_FULL.upper()))
    _tripwire(monkeypatch)
    with pytest.raises(_DriversReached):
        _drive(tmp_path)


def test_the_observed_running_ref_is_recorded_on_the_run(tmp_path, monkeypatch) -> None:
    """The id's provenance is auditable only if the report says what was observed.

    No AgentMail key here, so every scenario SKIPS and the run is not green.
    That is deliberate: what is pinned is that the ref the gate READ, not the
    ref it was handed, lands in the artifact.
    """
    for variable in ("AGENTMAIL_API_KEY", "OPERATOR_RUNTIME_READ_SECRET", "OPERATOR_RUNTIME_READ_URL"):
        monkeypatch.delenv(variable, raising=False)
    _seam(monkeypatch, _FakeClient(CANDIDATE_FULL))
    assert _drive(tmp_path) == runner.EXIT_INCOMPLETE
    payloads = sorted(tmp_path.glob("*.json"))
    assert len(payloads) == 1
    document = json.loads(payloads[0].read_text())
    assert document["running_ref"] == CANDIDATE_FULL
    assert document["overlay_ref"] == CANDIDATE
    body = payloads[0].with_suffix(".md").read_text()
    assert CANDIDATE_FULL in body
