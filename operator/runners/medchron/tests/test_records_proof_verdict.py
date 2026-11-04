"""A records proof run ending held with cuts is the designed outcome of a
capped run (2026-11-03: ``lane-proof accept`` refused dry run 3 because its
verdict was ``held``). In proof mode the verdict is ``dry_run`` with the real
ending beside it (``would_be``); a run that never reached a reply worth
reviewing stays ``held`` and judges failed. Names are invented."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from medchron import proof_mode, proof_run
from records_testkit import FakeFinderModel, FakeSmokeball, pdf
from test_records_end_states import VISIT, _job, _rec, by_sections

MRI = "the Eastgate Imaging MRI"


def _capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, dict[str, Any], str, str]:
    model = FakeFinderModel(match_fn=by_sections)
    sb = FakeSmokeball()
    small = sb.add(1, "Northside Clinic records.pdf", pdf(10))
    big = sb.add(2, "Eastgate Imaging set.pdf", pdf(2313))
    recs = [_rec(VISIT), _rec(MRI, provider="Eastgate Imaging")]
    r, v = _job(tmp_path, monkeypatch, model, sb, recs, f"Please send {VISIT} and {MRI}.")
    return r, v, small, big


def test_a_capped_proof_run_with_cuts_is_dry_run_would_be_held_and_judges_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(proof_mode.PROOF_ENV, "1")
    r, v, small, big = _capped(tmp_path, monkeypatch)
    assert v["verdict"] == "dry_run" and v["would_be"] == "held"
    assert v["reason"] == f"per_job_cap_usd: {big[:8]} 2313 pages" and v["cents"] >= 0
    assert [c["file_id"] for c in v["cuts"]] == [small] and [i["outcome"] for i in v["items"]] == ["attached", "held"]
    assert (r.job.job_dir / "proof" / "reply.txt").is_file()
    passed, items = proof_run.judge([v])
    assert passed
    assert proof_run.why(passed, items) == f"would be held: per_job_cap_usd: {big[:8]} 2313 pages"


class _NoListing(FakeSmokeball):
    def get(self, path: str, **params: Any) -> Any:
        if path.endswith("/documents/files"):
            raise RuntimeError("listing down")
        return super().get(path, **params)


@pytest.mark.parametrize("why", ["finder_failed", "scope_unresolved"])
def test_a_proof_run_that_never_reached_a_reviewable_reply_judges_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, why: str
) -> None:
    monkeypatch.setenv(proof_mode.PROOF_ENV, "1")
    model = FakeFinderModel(match_fn=by_sections)
    sb = _NoListing() if why == "finder_failed" else FakeSmokeball()
    sb.add(1, "Northside Clinic records.pdf", pdf(10))
    recs = [_rec(MRI, provider="Eastgate Imaging")]  # names no file on the matter
    _r, v = _job(tmp_path, monkeypatch, model, sb, recs, f"Please send {MRI}.")
    assert v["verdict"] == "held" and v["reason"].startswith(why) and "would_be" not in v
    assert proof_run.judge([v])[0] is False


def test_a_proof_run_whose_every_match_fails_judges_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reviewer's probe on #3725: the match model never answers, both
    records hold, nothing is cut; that proof was judged passed."""
    monkeypatch.setenv(proof_mode.PROOF_ENV, "1")
    model = FakeFinderModel(match_fn=lambda _what, _secs: None)
    sb = FakeSmokeball()
    sb.add(1, "Northside Clinic records.pdf", pdf(10))
    xray = "the Northside Clinic x-ray"
    _r, v = _job(tmp_path, monkeypatch, model, sb, [_rec(VISIT), _rec(xray)], f"Please send {VISIT} and {xray}.")
    assert v["verdict"] == "held" and "would_be" not in v and v["cuts"] == []
    assert proof_run.judge([v])[0] is False


@pytest.mark.parametrize(
    ("hold", "outcomes", "ending"),
    [
        ("cut_refused: a record was found and no cut carries it", ["attached", "held"], None),
        ("match_unsettled: 00000001 the match did not answer", ["attached", "held"], None),
        ("monthly_budget_usd: the month's budget is reached", ["held", "held"], None),
        ("per_job_cap_usd: 00000002 2313 pages", ["held"], None),
        ("per_job_cap_usd: 00000002 2313 pages", ["not_found", "held"], "held"),
        ("per_job_cap_usd: 00000002 2313 pages", ["not_established", "held"], "held"),
        (None, ["attached"], "delivered"),
    ],
)
def test_a_proof_is_reviewable_only_when_it_settled_a_record(
    hold: str | None, outcomes: list[str], ending: str | None
) -> None:
    from medchron.records.outcome import proof_ending

    assert proof_ending(hold, [{"n": n, "outcome": o} for n, o in enumerate(outcomes, 1)]) == ending


def test_outside_proof_mode_a_capped_run_is_held_as_before(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(proof_mode.PROOF_ENV, raising=False)
    r, v, small, _big = _capped(tmp_path, monkeypatch)
    assert v["verdict"] == "held" and "would_be" not in v and [c["file_id"] for c in v["cuts"]] == [small]
    assert not (r.job.job_dir / "proof").exists()
    assert json.loads((r.data / "facts.json").read_text())["hold_reason"].startswith("per_job_cap_usd")
