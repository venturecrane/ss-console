"""Tests for bin/lib/voice_corpus.py — the voice-tracer ingest wiring.

Covers the three things that make this safe and useful:

* The leak invariant (assert_style_only) passes on a real content-free
  diff, and FAILS on a planted raw string and on a secret-shaped token —
  the privacy guarantee is enforced, not asserted.
* build_sample reuses the real differ, emits the exact runtime R2 key
  shape, and the emitted JSON contains none of the source words.
* Corpus extraction pulls the author's prose and filters harness noise,
  tool_result blocks, pasted code, and trivially short turns.

Run::

    cd operator && python -m pytest bin/tests/test_voice_corpus.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))  # operator/ on sys.path

from bin.lib.voice_corpus import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    VoiceLeakError,
    assert_style_only,
    build_sample,
    extract_corpus,
    extract_user_messages,
)

# A terse, declarative message in the author's register — no greeting/signoff.
_SCOTT_STYLE = (
    "Stop. Verify the secret values, not just the keys. "
    "We ship through PRs. Never push to main. Figure it out."
)


# ---------------------------------------------------------------------------
# Leak invariant
# ---------------------------------------------------------------------------


def test_assert_style_only_passes_on_real_diff():
    sample = build_sample(_SCOTT_STYLE, slug="smd", cohort="unassigned")
    # build_sample already runs the guard; assert it directly too.
    assert_style_only(sample.diff_dict, cohort="unassigned")


def test_assert_style_only_fails_on_planted_raw_string():
    """The core safety proof: a literal body fragment in any field aborts."""
    leaky = {
        "greeting_style": "none",
        "signoff_style": "none",
        "opener_template": "Hey Sarah, here is the wire transfer for the Henderson matter",
        "punctuation_rhythm": {"period_per_100": 10.0},
        "recipient_cohort": "unassigned",
    }
    with pytest.raises(VoiceLeakError):
        assert_style_only(leaky, cohort="unassigned")


def test_assert_style_only_fails_on_secret_token():
    leaky = {
        "greeting_style": "none",
        "closer_template": "sk-abcdef0123456789abcdef",
        "recipient_cohort": "unassigned",
    }
    with pytest.raises(VoiceLeakError):
        assert_style_only(leaky, cohort="unassigned")


def test_cohort_tag_is_allowed_but_arbitrary_strings_are_not():
    assert_style_only(
        {"greeting_style": "none", "recipient_cohort": "law-firm-clients"},
        cohort="law-firm-clients",
    )
    with pytest.raises(VoiceLeakError):
        assert_style_only(
            {"greeting_style": "none", "recipient_cohort": "law-firm-clients"},
            cohort="unassigned",  # tag doesn't match the declared cohort
        )


# ---------------------------------------------------------------------------
# build_sample
# ---------------------------------------------------------------------------


def test_build_sample_is_content_free():
    """No source word survives into the emitted JSON."""
    sample = build_sample(_SCOTT_STYLE, slug="smd", cohort="unassigned")
    blob = sample.diff_bytes.decode("utf-8").lower()
    for word in ["henderson", "wire", "secret", "transfer", "verify", "ship", "main"]:
        assert word not in blob, f"source word {word!r} leaked into the sample JSON"


def test_build_sample_r2_key_matches_runtime_contract():
    sample = build_sample(_SCOTT_STYLE, slug="smd", cohort="unassigned")
    assert sample.r2_key.startswith("vaults/smd/voice/cohort/unassigned/")
    assert sample.r2_key.endswith(".json")
    # round-trips as JSON with the expected style fields
    parsed = json.loads(sample.diff_bytes)
    assert parsed["greeting_style"] == "none"
    assert "sentence_length_distribution" in parsed
    assert "punctuation_rhythm" in parsed


# ---------------------------------------------------------------------------
# Corpus extraction
# ---------------------------------------------------------------------------


def _write_transcript(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")


def test_extract_user_messages_filters_noise(tmp_path):
    transcript = tmp_path / "t.jsonl"
    _write_transcript(
        transcript,
        [
            # keep: real prose, role=user, string content
            {"message": {"role": "user", "content": "Let's reverse course and fix this. Figure it out, please, and ship it cleanly."}},
            # drop: system-reminder noise
            {"message": {"role": "user", "content": "<system-reminder>do x</system-reminder> blah blah blah more words here"}},
            # drop: assistant turn
            {"message": {"role": "assistant", "content": "Sure, I will do that for you right away sir."}},
            # drop: tool_result block (list content, non-text)
            {"message": {"role": "user", "content": [{"type": "tool_result", "content": "exit 0"}]}},
            # drop: pasted code fence
            {"message": {"role": "user", "content": "```python\ndef f():\n    return 1\n```"}},
            # drop: too short
            {"message": {"role": "user", "content": "yes do it"}},
            # keep: text block in a list
            {"message": {"role": "user", "content": [{"type": "text", "text": "Many features of the harness must be configurable, including the send threshold."}]}},
        ],
    )
    msgs = list(extract_user_messages(transcript, min_words=5))
    assert len(msgs) == 2
    assert any("reverse course" in m for m in msgs)
    assert any("configurable" in m for m in msgs)


def test_extract_user_messages_rejects_agent_prompts_and_markdown(tmp_path):
    """Agent role-prompts and pasted skill/markdown docs are not the author's
    voice — they read as instructions TO an agent and would teach the opposite
    of the author's terse first-person register."""
    transcript = tmp_path / "t.jsonl"
    _write_transcript(
        transcript,
        [
            # drop: second-person agent role prompts (skill-authored, pasted)
            {"message": {"role": "user", "content": "You are Claude Code, an interactive agent that helps with software engineering tasks."}},
            {"message": {"role": "user", "content": "Your task is to review the diff and report every correctness bug you can find."}},
            {"message": {"role": "user", "content": "Output only the final answer as a single JSON object with no other commentary."}},
            # drop: pasted markdown doc / skill definition header
            {"message": {"role": "user", "content": "# /ship - Ship to Production\n\nCommit, push, PR, CI, merge, and confirm deployment all in one shot."}},
            # keep: the author's own terse first-person prose
            {"message": {"role": "user", "content": "Stop guessing. Verify the secret values, then ship it cleanly through a PR."}},
        ],
    )
    msgs = list(extract_user_messages(transcript, min_words=5))
    assert len(msgs) == 1
    assert "Verify the secret values" in msgs[0]
    # case-insensitive: lowercased agent opener must also be rejected
    assert not any(m.lower().startswith(("you are", "your task", "output only")) for m in msgs)


def test_extract_corpus_dedupes_and_limits(tmp_path):
    t1 = tmp_path / "a.jsonl"
    t2 = tmp_path / "b.jsonl"
    dup = {"message": {"role": "user", "content": "This exact sentence repeats across two different transcripts verbatim."}}
    _write_transcript(t1, [dup])
    _write_transcript(t2, [dup, {"message": {"role": "user", "content": "A second distinct message with enough words to pass the prose filter cleanly."}}])
    corpus = extract_corpus([t1, t2], min_words=5)
    texts = [c["text"] for c in corpus]
    assert len(texts) == 2  # the duplicate is collapsed
    corpus_limited = extract_corpus([t1, t2], min_words=5, limit=1)
    assert len(corpus_limited) == 1


def test_extract_corpus_malformed_lines_are_skipped(tmp_path):
    transcript = tmp_path / "bad.jsonl"
    transcript.write_text(
        "not json\n"
        + json.dumps({"message": {"role": "user", "content": "A perfectly good prose message with sufficient length to be kept."}})
        + "\n{also not json",
        encoding="utf-8",
    )
    corpus = extract_corpus([transcript], min_words=5)
    assert len(corpus) == 1
