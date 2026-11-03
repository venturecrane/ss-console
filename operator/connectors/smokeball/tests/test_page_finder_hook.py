"""The page finder's paid-call seam (ADR 0102 amendment 4): a job's cost cap
stops a build mid-batch, the index keeps every page already paid for, and a
file whose index covers every page makes no request at all."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from smokeball_connector import page_finder as pf

from .test_page_finder import FakeModel, _set, _spool, _work, model  # noqa: F401 - pytest fixtures (_spool, model) used by name, not by reference


class CapHit(pf.CallRefused):
    """A caller's own subclass: it must arrive unchanged out of locate."""


class Hook:
    """Refuses the Nth request; counts what was asked and what came back."""

    def __init__(self, refuse_at: int | None = None, *, refuse_kind: str | None = None) -> None:
        self.refuse_at, self.refuse_kind = refuse_at, refuse_kind
        self.before_calls: list[tuple[str, int]] = []
        self.after_calls: list[tuple[str, dict[str, int]]] = []
        self.lock = threading.Lock()
        self.stopped = False

    def before(self, model: str, est: int) -> None:
        with self.lock:
            self.before_calls.append((model, est))
            if self.stopped or (self.refuse_at is not None and len(self.before_calls) >= self.refuse_at):
                self.stopped = True
                raise CapHit("per_job_cap_usd: reached")

    def after(self, model: str, usage: dict[str, int]) -> None:
        with self.lock:
            self.after_calls.append((model, usage))


def test_a_hook_that_refuses_the_third_call_keeps_the_first_pages(
    model: FakeModel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMOKEBALL_VISION_WORKERS", "1")
    src = _set(tmp_path)  # 40 pages: five batches of 8
    hook = Hook(refuse_at=3)
    with pytest.raises(CapHit) as caught:
        pf.locate(src, "the visit", _work(tmp_path), hook=hook)
    assert isinstance(caught.value, CapHit)
    # Two batches went out; nothing after the stop.
    assert model.index_calls() == 2 and len(hook.after_calls) == 2
    saved = json.loads((pf.index_dir() / f"{pf.file_sha256(src)}.json").read_text(encoding="utf-8"))
    assert sorted(int(k) for k in saved["pages"]) == list(range(1, 17))
    assert saved["unreadable"] == {}  # pages the stop kept back are not "unreadable"
    assert saved["spent"] == {"input_tokens": 3200, "output_tokens": 160}
    # The hook is unset after the call.
    assert pf.CALL_HOOK is None


def test_spend_after_a_stop_is_bounded_by_the_calls_in_flight(
    model: FakeModel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workers = 3
    monkeypatch.setenv("SMOKEBALL_VISION_WORKERS", str(workers))
    hook = Hook(refuse_at=2)
    with pytest.raises(CapHit):
        pf.locate(_set(tmp_path), "the visit", _work(tmp_path), hook=hook)
    # Only calls that passed `before` were sent: at most the one before the
    # cap plus the workers already in flight.
    assert model.index_calls() == len(hook.after_calls) <= 1 + workers
    assert model.index_calls() < 5


def test_the_next_call_reads_only_the_pages_the_stop_kept_back(
    model: FakeModel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMOKEBALL_VISION_WORKERS", "1")
    src = _set(tmp_path)
    with pytest.raises(CapHit):
        pf.locate(src, "the visit", _work(tmp_path), hook=Hook(refuse_at=3))
    out = pf.locate(src, "the visit", _work(tmp_path), hook=Hook())
    assert out["readNow"] == 24 and out["fromCache"] == 16 and out["complete"]


def test_a_cached_whole_index_makes_no_request(model: FakeModel, tmp_path: Path) -> None:
    src = _set(tmp_path)
    pf.build_index(src, _work(tmp_path), window=None)
    calls = len(model.bodies)
    hook = Hook()
    built = pf.build_index(src, _work(tmp_path), window=None, hook=hook)
    assert len(model.bodies) == calls and hook.before_calls == []
    assert built["read_now"] == [] and built["tokens"] == {"input_tokens": 0, "output_tokens": 0}


def test_the_cached_return_does_not_split_the_file(
    model: FakeModel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _set(tmp_path)
    pf.build_index(src, _work(tmp_path), window=None)
    from smokeball_connector import page_split

    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("split ran on a covered index")

    monkeypatch.setattr(page_split, "split", boom)
    out = pf.locate(src, "the visit", _work(tmp_path))
    assert out["readNow"] == 0 and out["complete"] and out["pageCount"] == 40


def test_the_match_call_goes_through_the_hook_and_can_be_refused(model: FakeModel, tmp_path: Path) -> None:
    src = _set(tmp_path, 8)  # one index batch, then the match
    hook = Hook(refuse_at=2)
    with pytest.raises(CapHit):
        pf.locate(src, "the visit", _work(tmp_path), hook=hook)
    assert model.index_calls() == 1 and len(model.bodies) == 1
    # The pages read before the refused match are kept.
    again = pf.locate(src, "the visit", _work(tmp_path))
    assert again["readNow"] == 0


def test_locate_cached_matches_from_the_index_alone_and_reads_no_page(model: FakeModel, tmp_path: Path) -> None:
    src = _set(tmp_path)
    live = pf.locate(src, "the visit", _work(tmp_path))
    sha = pf.file_sha256(src)
    src.unlink()  # no PDF: the index alone
    reads = model.index_calls()
    cached = pf.locate_cached(sha, "the visit")
    assert model.index_calls() == reads and cached["items"] == live["items"] and cached["readNow"] == 0


def test_locate_cached_refuses_an_index_that_does_not_cover_the_file(model: FakeModel, tmp_path: Path) -> None:
    src = _set(tmp_path)
    pf.build_index(src, _work(tmp_path), window=(1, 8))
    with pytest.raises(pf.FinderRefused):
        pf.locate_cached(pf.file_sha256(src), "the visit")
    assert model.index_calls() == 1


def test_before_is_given_the_requests_model_and_an_estimate(model: FakeModel, tmp_path: Path) -> None:
    hook = Hook()
    pf.locate(_set(tmp_path, 8), "the visit", _work(tmp_path), hook=hook)
    (index_model, index_est), (match_model, _) = hook.before_calls
    assert index_model == match_model == pf.model()
    assert index_est >= 8 * 1600
    assert hook.after_calls[0][1] == {"input_tokens": 1600, "output_tokens": 80}
