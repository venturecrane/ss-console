"""A review part too large for the model is split before it is sent, never
sent and refused (2026-11-21).

A real update's review planned a part of 1,187,421 input tokens; the API
refuses any request over 1,000,000, the refusal escaped the stage and the
whole update failed after 148 paid calls. Each test below fails on the code
before the change. Every name, date and value is invented.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import update_testkit as K
from jobs.medchron.update import review_parts as RP, review_repair as RR
from jobs.stages import update_review as UR
from test_update_finisher import _review
from test_update_review_text_pages import ENTRY, _doc, _image, _run, _shown


def _r1(sr: Any) -> list[dict[str, Any]]:
    return [kw["messages"][0]["content"] for kw in sr.doorway.review_calls if "-r1-" in kw["custom_id"]]


def test_an_oversized_part_is_split_and_every_page_is_still_read_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails if a request over the limit is sent, or if splitting drops or
    repeats a page: the 12-page window is reviewed as pieces under the limit."""
    monkeypatch.setattr(RP, "REQUEST_TOKENS_MAX", 10_000)  # about five pictures
    rc, sr = _run(tmp_path, monkeypatch, [_review()])
    assert rc == 0
    sent = _r1(sr)
    assert len(sent) > 1 and all(RP.request_tokens(c) <= 10_000 for c in sent)
    assert sorted(p for c in sent for p in _shown(c)) == list(range(1, 13))
    assert any("too large to send" in m for m in sr.logged)


def test_a_size_refusal_from_the_api_splits_the_part_instead_of_failing_the_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails if the API's own size refusal (a 400, "prompt is too long")
    escapes the stage: before, it failed the whole update."""

    class TooLong(Exception):
        status_code = 400

    plain = K.ScriptedDoor.call

    def call(self: Any, stage: str, **kw: Any) -> Any:
        if sum(1 for b in kw["messages"][0]["content"] if b.get("type") == "image") > 6:
            raise TooLong("prompt is too long: 1187421 tokens > 1000000 maximum")
        return plain(self, stage, **kw)

    monkeypatch.setattr(K.ScriptedDoor, "call", call)
    rc, sr = _run(tmp_path, monkeypatch, [_review()])
    assert rc == 0
    assert sorted(p for c in _r1(sr) for p in _shown(c)) == list(range(1, 13))
    assert any("refused for its size" in m for m in sr.logged)


def test_any_other_api_error_still_stops_the_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fails if the size catch swallows a refusal that is not about size."""

    class Denied(Exception):
        status_code = 401

    def call(self: Any, stage: str, **kw: Any) -> Any:
        raise Denied("invalid x-api-key")

    monkeypatch.setattr(K.ScriptedDoor, "call", call)
    with pytest.raises(Denied):
        _run(tmp_path, monkeypatch, [_review()])


def test_a_duplicate_check_is_split_by_its_entries() -> None:
    """A text-only duplicate check carries every entry of one long file and
    no page: it can only shrink by entries."""
    ents = RR.anchor_entries(_doc([ENTRY, ENTRY.replace("08/27/2026", "08/28/2026")]), {2}, K.HEADINGS)
    part = RP.Part("duplicates", ents, 2)
    content = RP.review_content(part, "CLIENT", _image, ents, [])
    halves = RP.size_halves(part, content)
    assert halves and [len(h.entries) for h in halves] == [1, 1] and all(not h.pages for h in halves)


def test_one_entry_on_one_page_still_too_large_is_held_not_failed() -> None:
    """Nothing smaller can be sent: the part is a NOT_REVIEWED finding (held
    for SMD and reviewed again next round), never an exception."""
    ents = RR.anchor_entries(_doc([ENTRY]), {2}, K.HEADINGS)
    part = RP.Part("pages", ents, 2, [1])
    content = RP.review_content(part, "CLIENT", _image, ents, [])
    assert RP.size_halves(part, content) is None
    got = UR._size_split(None, part, content, ents, [], "p1", 0)  # type: ignore[arg-type]
    assert [f.klass for f in got] == [UR.NOT_REVIEWED]
