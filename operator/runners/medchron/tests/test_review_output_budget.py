"""The pre-delivery review must finish on a long update (2026-10-14): a
422-entry update's review stopped at max_tokens twice (138,138 in / 16,000
out, effort high), never reached its VERDICT line, and the retry resent the
identical request. Each test fails on the code before the fix (68689026).
Every value is invented."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest
import update_testkit as K

from medchron.proof import review as proof_review


class Doorway:
    """A stub model: runs out of output (``max_tokens``, no verdict) whenever
    ``truncates`` says so, else answers with a verdict."""

    def __init__(
        self,
        truncates: Callable[[int, str], bool],
        answer: Callable[[str], str] = lambda user: "VERDICT: 0 MUST-FIX",
    ) -> None:
        self.truncates, self.answer = truncates, answer
        self.calls: list[dict[str, Any]] = []

    def call(self, stage: str, **kw: Any) -> SimpleNamespace:
        self.calls.append({"stage": stage, **kw})
        user = kw["messages"][0]["content"]
        if self.truncates(kw["max_tokens"], user):
            return SimpleNamespace(stop_reason="max_tokens", text="| SHOULD-FIX | 6 | half a table")
        return SimpleNamespace(stop_reason="end_turn", text=self.answer(user))

    def requests(self) -> list[str]:
        """Each request as sent, less its custom_id (the only field a retry
        varied before the fix)."""
        return [json.dumps({k: v for k, v in c.items() if k != "custom_id"}, sort_keys=True) for c in self.calls]


def _no_identical_request(d: Doorway) -> None:
    reqs = d.requests()
    assert len(reqs) == len(set(reqs)), "an identical review request was resent"


def _sr(d: Doorway) -> SimpleNamespace:
    return SimpleNamespace(
        cfg=K.cfg(), slug="example-matter", unit=SimpleNamespace(unit="alpha", client_name="Alpha Example", dob=""),
        job=SimpleNamespace(units=[], incident_date=K.INCIDENT), doorway=d, log=lambda msg: None,
    )  # fmt: skip


def test_a_review_that_needs_more_than_16000_output_tokens_completes() -> None:
    d = Doorway(lambda budget, user: budget <= 16_000)
    out = proof_review.run_model(d, "claude-opus-5-5", "sys", "material", "k", (1, 2, 3, 4))
    assert out == [] and d.calls[-1]["max_tokens"] >= 64_000
    _no_identical_request(d)


def test_a_max_tokens_stop_is_never_retried_with_the_identical_request() -> None:
    d = Doorway(lambda budget, user: True)
    with pytest.raises(proof_review.ReviewIncomplete):
        proof_review.run_model(d, "claude-opus-5-5", "sys", "material", "k", (1, 2, 3, 4))
    _no_identical_request(d)
    assert [c["max_tokens"] for c in d.calls] == [64_000, 128_000]


def _entry(n: int) -> str:
    return (
        f"{n:02d}/10/2026\nExample Imaging | Medical Diagnoses\n\nMedical Diagnoses\n"
        f"Study {n} reviewed with a finding recorded for visit {n}. (Exhibit 2 - p. {n})"
    )


def _studies(user: str) -> int:
    return user.count(" reviewed with a finding recorded ")


def test_a_long_update_is_reviewed_in_smaller_parts_and_the_strictest_part_holds(tmp_path: Path) -> None:
    from medchron.stages import review as R

    rd = tmp_path / "runs" / "alpha"
    (rd / "update").mkdir(parents=True)
    new = [_entry(n) for n in range(11, 27)]  # 16 added entries
    (rd / "update" / "base.md").write_text(K.base_md())
    (rd / "update" / "updated-chronology.md").write_text(K.base_md(entries=(K.ENTRY_PRE, K.ENTRY_POST, *new)))

    def answer(user: str) -> str:
        row = "| MUST-FIX | 1 wrong client | an entry is another client's | entry 13/10/2026 |\n"
        return (row if "Study 13 reviewed" in user else "") + "VERDICT: 0 MUST-FIX"

    # Any part holding more than four added entries runs out of output at
    # every budget: only splitting finishes it.
    d = Doorway(lambda budget, user: _studies(user) > 4, answer)
    sr = _sr(d)
    found = R._model_findings(sr, rd, R._model(sr), "", [], [])  # type: ignore[arg-type]
    _no_identical_request(d)
    done = "".join(c["messages"][0]["content"] for c in d.calls if _studies(c["messages"][0]["content"]) <= 4)
    assert all(f"Study {n} reviewed" in done for n in range(11, 27))  # every added entry, in a finished part
    assert len([f for f in found if f.grade == proof_review.MUST]) == 1  # one part's MUST-FIX
    rec = proof_review.save(rd, found, model="m", lane="chronology")
    assert proof_review.status(rec) == "held"  # holds the whole review


def test_a_part_that_cannot_be_split_further_fails_resumable_with_a_reason(tmp_path: Path) -> None:
    from medchron.stages import review as R

    rd = tmp_path / "runs" / "alpha"
    rd.mkdir(parents=True)
    d = Doorway(lambda budget, user: True)
    sr = _sr(d)
    with pytest.raises(proof_review.ReviewIncomplete, match="could not be split smaller"):
        R._model_findings(sr, rd, R._model(sr), "one line", [], [])  # type: ignore[arg-type]
    _no_identical_request(d)


def test_parts_are_cut_between_entries_and_bounded_in_entries() -> None:
    from medchron.stages import review as R

    text = "ADDED ENTRIES:\n" + "\n".join(_entry(n % 28 + 1) for n in range(150)) + "\n"
    parts = R.chunks(text, 10_000_000)
    assert "".join(parts) == text and len(parts) >= 3
    assert all(_studies(p) <= R.REVIEW_PART_MAX_ENTRIES for p in parts)
    assert all(p == parts[0] or R._DATE_HEAD.match(p) for p in parts)  # no part starts mid-entry
