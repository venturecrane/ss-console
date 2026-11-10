"""The transcription's ``HANDWRITING:`` field stays out of demand and drafting text.

The page prompt is the chronology's (``stages/vision.SYSTEM``), whose REQUIRED
first line is ``HANDWRITING: yes|no``. The chronology turns that line into its
page mark (``handwriting_field``); the demand and drafting transcription
(``demand/transcribe.py``) kept the raw reply, so the field line sat in the
source text their drafters read (the 2026-11-10 lanes review, finding F2).

Each case runs the REAL ``transcribe.run`` over a real one-page PDF through the
REAL ``llm.Doorway`` and usage ledger, at $0: a fresh page answered by a fake
client, and a resumed job whose saved partial holds a raw reply from before the
fix.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from anthropic.types import Message, TextBlock, Usage
from jobs.demand import transcribe
from jobs.ledger import Ledger
from jobs.llm import Doorway
from jobs.stages import vision


class Client:
    """Answers every page with ``reply``; counts the calls."""

    def __init__(self, reply: str) -> None:
        self.reply, self.calls = reply, 0
        self.messages = self

    def create(self, **params: Any) -> Message:
        self.calls += 1
        return Message(
            id="msg_test",
            type="message",
            role="assistant",
            model=str(params["model"]),
            content=[TextBlock(type="text", text=self.reply)],
            stop_reason="end_turn",
            stop_sequence=None,
            usage=Usage(input_tokens=1500, output_tokens=60),
        )


def _job(tmp_path: Path) -> tuple[Path, Path]:
    data = tmp_path / "data"
    data.mkdir()
    pdf = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(pdf))
    text = data / "doc-1.txt"
    text.write_text("[p.1]\n\x01\x02\n", encoding="utf-8")
    row = {
        "id": "doc-1",
        "name": "scan.pdf",
        "source_path": str(pdf),
        "pages": 1,
        "transcribe": "all",
        "text_path": str(text),
    }
    (data / "extracted.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    return data, text


@pytest.mark.parametrize("answer", ["yes", "no"])
def test_a_fresh_page_reaches_the_source_text_without_the_field(tmp_path, answer):
    """MUTATION: keep the raw reply in ``_merge`` and the field line is in the text."""
    data, text = _job(tmp_path)
    client = Client(f"HANDWRITING: {answer}\nBP 142/102 on 2024-03-01.")
    door = Doorway(ledger=Ledger(data / "usage-ledger.jsonl"), client=client, log=lambda _m: None)
    transcribe.run(data, door, "claude-sonnet-5", lambda _m: None, 1)
    out = text.read_text(encoding="utf-8")
    assert client.calls == 1
    assert "HANDWRITING" not in out
    assert out == "[p.1] (machine transcription)\nBP 142/102 on 2024-03-01.\n"


def test_a_resumed_job_drops_the_field_from_a_page_it_already_paid_for(tmp_path):
    data, text = _job(tmp_path)
    (data / "partial").mkdir()
    (data / "partial" / "doc-1.jsonl").write_text(
        json.dumps({"page": 1, "text": "HANDWRITING: no\nVisit on 2024-03-01."}) + "\n", encoding="utf-8"
    )
    client = Client("never asked")
    door = Doorway(ledger=Ledger(data / "usage-ledger.jsonl"), client=client, log=lambda _m: None)
    transcribe.run(data, door, "claude-sonnet-5", lambda _m: None, 1)
    assert client.calls == 0
    assert text.read_text(encoding="utf-8") == "[p.1] (machine transcription)\nVisit on 2024-03-01.\n"


def test_the_chronology_page_mark_is_unchanged():
    """The chronology keeps its own reading of the field: ``[handwritten]`` on
    yes, nothing on no, and a reply without the field marked handwritten."""
    assert vision.handwriting_field("HANDWRITING: yes\nBP 142/102") == "[handwritten]\nBP 142/102"
    assert vision.handwriting_field("HANDWRITING: no\nBP 142/102") == "BP 142/102"
    assert vision.handwriting_field("BP 142/102") == "[handwritten]\nBP 142/102"
    assert vision.handwriting_field(vision.FAILED) == vision.FAILED


# ---- the $0 probe: which content-keyed caches key on this text, and does a seat re-pay -------------
#
# Downstream of the transcription, three caches key on the page text: the
# digest's chunks (``summarize.Summarizer``: the chunk's sha), the evidence
# path's extraction windows (``drafting/evidence/extract``: the window's sha),
# and everything keyed on the draft the digest feeds. All three live in the
# JOB's own data dir. The transcribe stage runs once per job and is skipped on
# a resume once done, and a demand re-run (``demand/seed``) carries the
# transcribe stage done with its text as it was. So text already transcribed
# is never re-merged, its caches still match, and the change re-pays nothing;
# a new job builds its caches from the new text the first time, at the same
# price (a few tokens fewer per scanned page).


class StreamClient(Client):
    def stream(self, **params: Any) -> "StreamClient":
        self._msg = self.create(**params)
        return self

    def __enter__(self) -> "StreamClient":
        return self

    def __exit__(self, *a: Any) -> None:
        return None

    def get_final_message(self) -> Message:
        return self._msg


OLD = "[p.1] (machine transcription)\nHANDWRITING: no\nVisit on 2024-03-01.\n"
NEW = "[p.1] (machine transcription)\nVisit on 2024-03-01.\n"


def _summarizer(data: Path, client: Client) -> Any:
    from jobs.demand.summarize import TRUNCATION_MARK, Summarizer

    client.reply = f"## MEDICAL | the visit\n{TRUNCATION_MARK}"
    door = Doorway(ledger=Ledger(data / "usage-ledger.jsonl"), client=client, log=lambda _m: None)
    return Summarizer(data, door, "claude-sonnet-5", "DIGEST", 1000, 1, lambda _m: None)


def test_probe_the_digest_and_extraction_caches_key_on_the_page_text(tmp_path):
    from jobs.drafting.evidence.extract import DocPages, windows

    data = tmp_path / "data"
    client = StreamClient("")
    s = _summarizer(data, client)
    s.run_round({"00": OLD})
    s.run_round({"00": OLD})
    assert client.calls == 1  # the same text is read from the cache
    s.run_round({"00": NEW})
    assert client.calls == 2  # changed text in the SAME data dir would be read again
    old_w = windows({"a": DocPages("a", {1: OLD}, frozenset({1}), None)}, "claude-sonnet-5")
    new_w = windows({"a": DocPages("a", {1: NEW}, frozenset({1}), None)}, "claude-sonnet-5")
    assert old_w[0].sha != new_w[0].sha


def test_probe_a_seat_re_pays_nothing_for_text_it_already_transcribed(tmp_path):
    """A demand re-run seeded from a job transcribed before the fix carries the
    transcribe stage done and its text unchanged, so its digest cache hits."""
    from jobs.demand import seed

    old_job, new_job = tmp_path / "old", tmp_path / "new"
    (old_job / "data" / "digest").mkdir(parents=True)
    (old_job / "data" / "doc-1.txt").write_text(OLD, encoding="utf-8")
    state = {"transcribe": {"status": "done"}, "summarize": {"status": "done"}}
    (old_job / "data" / "state.json").write_text(json.dumps(state), encoding="utf-8")
    client = StreamClient("")
    _summarizer(old_job / "data", client).run_round({"00": OLD})
    assert client.calls == 1
    carried = seed.seed(new_job, old_job)
    assert "transcribe" in carried
    text = (new_job / "data" / "doc-1.txt").read_text(encoding="utf-8")
    assert text == OLD  # never re-merged: transcribe is done
    _summarizer(new_job / "data", client).run_round({"00": text})
    assert client.calls == 1  # nothing re-paid
