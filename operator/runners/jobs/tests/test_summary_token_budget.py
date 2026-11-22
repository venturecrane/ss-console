"""The record summary is bounded by the tokens of the largest request that
carries it (``jobs/summary_budget.py``). Every name, date and value here is
invented."""

from __future__ import annotations

import math
import re
from types import SimpleNamespace

import pytest

from jobs import summary_budget as sb
from jobs.demand import run as demand_run, summarize
from jobs.drafting import review as drafting_review
from jobs.drafting.evidence_cost import COMPOSE_FRAME_CHARS, DRAFT_TOKENS
from jobs.drafting.outcome import DraftingFailed
from jobs.drafting.run import DIGEST_BUDGET_TOKENS, DraftingRun

TARGET = re.compile(r"\ATARGET LENGTH: about [\d,]+ characters\.\n\n")


class Door:
    """A condense model that keeps ``keep`` of each group it is sent."""

    batch_stages: frozenset[str] = frozenset()

    def __init__(self, keep: float) -> None:
        self.keep, self.sent = keep, []

    def call(self, stage, *, messages, **_kw):
        assert stage == "condense"
        body = TARGET.sub("", messages[0]["content"])
        self.sent.append(len(messages[0]["content"]))
        return SimpleNamespace(text=body[: int(len(body) * self.keep)], stop_reason="end_turn")


def record(chars: int) -> str:
    """An invented summary of about ``chars`` characters, in document blocks."""
    block = "## CLINIC | Example Clinic visit | 2031-02-03 | (medical)\n" + (
        "- Example finding on an invented date (FILE: example-record.pdf, p. 4)\n" * 40
    )
    return "\n\n".join([block] * math.ceil(chars / (len(block) + 2)))[:chars]


def fits(summary: str, frame: int) -> bool:
    return sb.estimate_tokens(summary) + frame + sb.OUTPUT_TOKENS + sb.MARGIN_TOKENS <= sb.WINDOW_TOKENS


def test_the_budget_leaves_room_for_the_largest_carrying_request():
    review_frame = drafting_review.DISCOVERY_PAPERS_MAX_CHARS / sb.CHARS_PER_TOKEN + 2 * DRAFT_TOKENS
    assert sb.DRAFTING_FRAME_TOKENS >= review_frame >= COMPOSE_FRAME_CHARS / sb.CHARS_PER_TOKEN
    assert DIGEST_BUDGET_TOKENS == sb.DRAFTING_BUDGET_TOKENS == 636_000
    assert sb.DEMAND_BUDGET_TOKENS == 736_000
    # The old 1.6M-character ceiling did not fit at the measured density; the new one does.
    assert not fits("x" * 1_600_000, sb.DRAFTING_FRAME_TOKENS)
    assert fits("x" * sb.DRAFTING_BUDGET_CHARS, sb.DRAFTING_FRAME_TOKENS)
    assert summarize.budget_chars({"digest_budget_chars": 2_000_000}) == sb.DEMAND_BUDGET_CHARS
    assert summarize.budget_chars({"digest_budget_chars": 900_000}) == 900_000


def test_a_small_summary_passes_through_unchanged_and_nothing_is_sent():
    door, text = Door(0.5), record(400_000)
    out = summarize.bounded(text, sb.DRAFTING_BUDGET_TOKENS, door, "m", "P {{WINDOW}}", 2, lambda _l: None)
    assert out is text and door.sent == []
    # The first condense too: digests under the target are only joined.
    joined = summarize.condense([text, text], sb.DRAFTING_BUDGET_CHARS, door, "m", "P", 2, lambda _l: None)
    assert joined == text + "\n\n" + text and door.sent == []


@pytest.mark.parametrize(
    ("budget", "frame"),
    [(sb.DRAFTING_BUDGET_TOKENS, sb.DRAFTING_FRAME_TOKENS), (sb.DEMAND_BUDGET_TOKENS, sb.DEMAND_FRAME_TOKENS)],
)
def test_an_over_budget_summary_is_condensed_until_the_largest_request_fits(budget, frame):
    door, text, said = Door(0.9), record(1_700_000), []
    out = summarize.bounded(text, budget, door, "m", "P {{WINDOW}}", 2, said.append)
    assert fits(out, frame) and len(out) < len(text)
    assert sum("condensed again" in s for s in said) >= 2  # 0.9 a pass needs more than one
    # Each condense request is a piece group, never the whole summary.
    assert max(door.sent) < 500_000


def test_a_summary_that_will_not_shrink_ends_the_drafting_job_failed_with_the_reason():
    me = SimpleNamespace(doorway=Door(1.0), firm=SimpleNamespace(model=lambda _s: "m"), log=lambda _l: None)
    with pytest.raises(DraftingFailed, match=r"^stage_unfinished: summarize: the record summary is about"):
        DraftingRun._bounded(me, record(1_400_000))


def test_a_summary_still_over_after_every_pass_ends_failed():
    with pytest.raises(sb.SummaryTooLarge) as got:
        summarize.bounded(record(1_500_000), 100_000, Door(0.95), "m", "P {{WINDOW}}", 2, lambda _l: None)
    assert got.value.passes == sb.MAX_PASSES and got.value.budget == 100_000
    assert "firm" not in str(got.value)


def test_the_demand_lane_turns_it_into_a_failed_resumable_verdict(monkeypatch):
    def over(*_a, **_k):
        raise sb.SummaryTooLarge(800_000, sb.DEMAND_BUDGET_TOKENS, 1)

    monkeypatch.setattr(summarize, "digest", over)
    me = SimpleNamespace(data=None, doorway=None, firm=None, _workers=1, log=lambda _l: None)
    with pytest.raises(demand_run.DemandFailed, match="record summary is about 800,000 tokens"):
        demand_run.DemandRun._digest(me)


def test_pieces_rejoin_to_the_text_and_respect_the_cap():
    text = "a" * 250_000 + "\n\n" + "b\n" * 70_000 + "\n\n" + "c" * 10
    got = sb.pieces(text, 100_000)
    assert "\n\n".join(got).replace("\n", "") == text.replace("\n", "")
    assert all(len(p) <= 100_000 for p in got)
    assert sb.pieces("short") == ["short"]


def test_a_code_shrink_bounds_two_texts_at_once():
    # The ledger path's index and digest are measured together (evidence_lane._ev_index).
    def size(pair: tuple[str, str]) -> int:
        return max(len(pair[0]), len(pair[1]))

    def tighter(pair: tuple[str, str], target: int) -> tuple[str, str]:
        return pair[0][:target], pair[1][:target]

    out = sb.bound(("i" * 900, "d" * 1_200), 500, tighter, lambda _l: None, size)
    assert size(out) <= sb.budget_chars(500)
