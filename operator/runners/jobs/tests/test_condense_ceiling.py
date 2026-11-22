"""A condense answer that reaches its output ceiling is never kept cut off
(``demand/summarize.condense``, which drafting's digest calls too). Every name,
date and value here is invented."""

from __future__ import annotations

import re
from types import SimpleNamespace

from jobs.demand import summarize

TARGET = re.compile(r"\ATARGET LENGTH: about [\d,]+ characters\.\n\n")


def digest(tag: str, chars: int) -> str:
    line = f"- {tag}: invented finding on 2031-02-03 (FILE: example-{tag}.pdf, p. 7)\n"
    return f"## CLINIC | Example {tag} | 2031-02-03 | (medical)\n" + line * (chars // len(line))


class Door:
    """Answers each group with its first half; a body over ``ceiling``
    characters comes back cut off with stop reason ``max_tokens``."""

    def __init__(self, ceiling: int, batch: bool = False) -> None:
        self.ceiling, self.asked = ceiling, []
        self.batch_stages = frozenset({"condense"}) if batch else frozenset()

    def _answer(self, user: str) -> SimpleNamespace:
        body = TARGET.sub("", user)
        if len(body) > self.ceiling:
            return SimpleNamespace(text=body[: self.ceiling // 4], stop_reason="max_tokens")
        return SimpleNamespace(text=f"<{body[:40]}..{body[-40:]}>", stop_reason="end_turn")

    def call(self, stage, *, messages, max_tokens, **kw):
        assert stage == "condense" and max_tokens == summarize.CONDENSE_MAX_TOKENS
        self.asked.append((kw["custom_id"], messages[0]["content"]))
        return self._answer(messages[0]["content"])

    def batch_call(self, stage, items, on_result, **_kw):
        for it in items:
            self.asked.append((it.custom_id, it.messages[0]["content"]))
            on_result(it, self._answer(it.messages[0]["content"]), None)


def run(digests: list[str], budget: int, door: Door, tmp_path=None) -> str:
    return summarize.condense(digests, budget, door, "m", "P {{WINDOW}}", 2, lambda _l: None, batch_dir=tmp_path)


def test_a_group_under_the_ceiling_is_sent_as_always():
    ds = [digest("alpha", 90_000), digest("bravo", 90_000), digest("charlie", 90_000)]
    door = Door(ceiling=10_000_000)
    out = run(ds, 135_000, door)  # ratio 0.5: one 270K group, target 135K, under the cap
    body = "\n\n".join(ds)
    ratio = 135_000 / len(body)
    assert door.asked == [
        (
            f"condense-{summarize.sha(body)[:8]}",
            f"TARGET LENGTH: about {int(len(body) * ratio):,} characters.\n\n{body}",
        )
    ]
    assert out.startswith("<## CLINIC | Example alpha") and out.endswith("p. 7)\n>")


def test_a_group_whose_target_passes_the_ceiling_is_made_smaller_first():
    ds = [digest(t, 95_000) for t in ("alpha", "bravo", "charlie", "delta")]
    groups = summarize.groups_for(ds, 0.95)
    assert len(groups) == 4  # two together would ask for about 180K, past the 172,800 cap
    assert all(sum(map(len, g)) * 0.95 <= summarize.TARGET_MAX_CHARS for g in groups)
    assert summarize.groups_for(ds, 0.5) == [ds[:3], ds[3:]]  # unchanged while the target fits


def test_a_reply_that_hits_max_tokens_is_split_and_both_halves_are_kept():
    ds = [digest("alpha", 60_000), digest("bravo", 60_000)]
    door = Door(ceiling=70_000)  # the whole group is over it; each digest is under
    out = run(ds, 60_000, door)
    assert len(door.asked) == 3
    assert "Example alpha" in out and "Example bravo" in out
    assert "example-alpha.pdf" in out and "example-bravo.pdf" in out  # each tail, cites included


def test_one_digest_over_the_ceiling_is_split_at_a_paragraph_and_ends():
    one = digest("echo", 50_000) + "\n\n" + digest("foxtrot", 50_000)
    door = Door(ceiling=60_000)
    out = run([one], 40_000, door)
    assert len(door.asked) == 3 and "Example echo" in out and "example-foxtrot.pdf" in out
    a, b = summarize.halves(["x" * 11])
    assert len(a[0]) < 11 and len(b[0]) < 11 and a[0] + b[0] == "x" * 11


def test_a_queued_reply_that_hits_max_tokens_is_redone_in_halves(tmp_path):
    ds = [digest("golf", 60_000), digest("hotel", 60_000)]
    door = Door(ceiling=70_000, batch=True)
    out = run(ds, 60_000, door, tmp_path)
    assert len(door.asked) == 3 and "example-golf.pdf" in out and "example-hotel.pdf" in out


def test_a_reply_that_keeps_running_out_stops_after_a_bounded_number_of_splits():
    """Review 11-22: a reply that runs out for a reason other than size (a loop)
    must not be split until the input is one character, doubling the calls each
    level. After SPLIT_DEPTH_MAX halvings the stage ends instead."""
    import pytest

    ds = [digest("alpha", 50_000)]
    door = Door(ceiling=10)  # every body "runs out"
    with pytest.raises(RuntimeError, match="still reached its ceiling after"):
        run(ds, 40_000, door)
    assert len(door.asked) <= 1 + 2 + 4 + 8
