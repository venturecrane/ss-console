"""The doorway's size stop (request_size.py), through the real Doorway and the
real Ledger. The incident it answers (2026-11-22): one update review request of
1,187,421 tokens went to a 1,000,000-token model, was refused, and the job
failed partway with $41 spent. Every request here is invented."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from jobs import ledger as ledger_mod, llm, request_size as RS
from jobs.casefile.outcome import REASON as CASEFILE_REASON
from jobs.drafting.outcome import REASON as DRAFTING_REASON
from jobs.limits import LimitHold
from jobs.litigation.outcome import REASON as LITIGATION_REASON
from jobs.stages import entry_audit_io as EA

IMG = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}
BIG = "claude-sonnet-5"  # a 1,000,000-token model
SMALL = "claude-haiku-4-5"  # a 200,000-token model


# ---- a client that must never be reached with an over-limit request ------------
class _Usage:
    input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens = 10, 5, 0, 0


class _Block:
    type, text = "text", "ok"


class _Msg:
    content = [_Block()]
    stop_reason = "end_turn"
    usage = _Usage()


class _Count:
    def __init__(self, n: int) -> None:
        self.input_tokens = n


class _Messages:
    """messages.create/stream that record what was sent; count_tokens answers
    `counted` (an int, an exception to raise, or absent)."""

    def __init__(self, counted=None, limit: int | None = None, refuse: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.counts: list[dict] = []
        self.counted, self.limit, self.refuse = counted, limit, refuse
        if counted is not None:
            self.count_tokens = self._count

    def _count(self, **kw):
        self.counts.append(kw)
        if isinstance(self.counted, Exception):
            raise self.counted
        return _Count(int(self.counted))

    def create(self, **kw):
        if self.limit is not None:
            assert kw["max_tokens"] <= self.limit, "an over-limit request reached the client"
        self.calls.append(kw)
        if self.refuse is not None:
            raise self.refuse
        return _Msg()


class _Client:
    def __init__(self, **kw) -> None:
        self.messages = _Messages(**kw)


class _NeverCalled:
    """A client whose create and batches.create fail the test if reached."""

    def __init__(self, counted: int) -> None:
        self.messages = self
        self.batches = self
        self.counted = counted

    def count_tokens(self, **kw):
        return _Count(self.counted)

    def create(self, **kw):
        raise AssertionError("an over-limit request was sent")

    def stream(self, **kw):
        raise AssertionError("an over-limit request was streamed")


@pytest.fixture
def ledger(tmp_path: Path) -> ledger_mod.Ledger:
    return ledger_mod.Ledger(tmp_path / "runs" / "u" / "usage-ledger.jsonl")


def _door(ledger: ledger_mod.Ledger, client, **kw) -> llm.Doorway:
    return llm.Doorway(ledger, client=client, log=lambda *_: None, **kw)


def _pages(n: int) -> list[dict]:
    """A user turn of `n` page pictures: its local bound is n x IMAGE_TOKENS."""
    return [{"role": "user", "content": [IMG] * n + [{"type": "text", "text": "read"}]}]


# ---- the stop ------------------------------------------------------------------
def test_an_over_limit_call_is_never_sent_and_writes_a_zero_row(ledger: ledger_mod.Ledger) -> None:
    d = _door(ledger, _NeverCalled(counted=1_187_421))
    with pytest.raises(llm.RequestTooLarge) as caught:
        d.call("review", model=BIG, messages=_pages(200), max_tokens=32_000, custom_id="review-r1-p3")
    exc = caught.value
    assert (exc.stage, exc.model, exc.custom_id) == ("review", BIG, "review-r1-p3")
    assert (exc.measured, exc.limit, exc.max_tokens) == (1_187_421, 1_000_000, 32_000)
    assert str(exc).startswith("request_too_large: review request review-r1-p3 counts 1,187,421")
    [row] = ledger_mod.read_rows(ledger.path)
    assert row["error"] == "request_too_large" and row["custom_id"] == "review-r1-p3" and row["pages"] == 200
    assert (row["in"], row["out"], row["cache_read"], row["cache_write"]) == (0, 0, 0, 0)
    assert ledger_mod.unsent(row)


def test_streamed_calls_are_stopped_the_same_way(ledger: ledger_mod.Ledger) -> None:
    d = _door(ledger, _NeverCalled(counted=999_000))  # 999,000 + 16,000 output room > 1,000,000
    with pytest.raises(llm.RequestTooLarge):
        d.call("compose", model=BIG, messages=_pages(200), max_tokens=128_000, stream=True)


def test_output_only_overflow_lowers_max_tokens(ledger: ledger_mod.Ledger) -> None:
    c = _Client(counted=900_000, limit=100_000)
    lines: list[str] = []
    d = llm.Doorway(ledger, client=c, log=lines.append)
    r = d.call("compose", model=BIG, messages=_pages(200), max_tokens=128_000)
    assert r.text == "ok" and c.messages.calls[0]["max_tokens"] == 100_000
    assert any("max_tokens lowered 128,000 -> 100,000" in x for x in lines)
    assert c.messages.counts[0]["model"] == BIG and "max_tokens" not in c.messages.counts[0]


def test_a_request_that_fits_is_sent_unchanged_and_uncounted(ledger: ledger_mod.Ledger) -> None:
    """The common case: the local bound fits, no count is made, and the request
    is byte for byte what build_params makes (the goldens hold this)."""
    c = _Client(counted=AssertionError("counted a request that fits"))
    d = _door(ledger, c)
    msgs = [{"role": "user", "content": "x" * 100_000}]
    d.call("merge", model=SMALL, system="S", messages=msgs, max_tokens=64_000)
    want = llm.build_params("merge", model=SMALL, system="S", messages=msgs, max_tokens=64_000)
    assert c.messages.calls == [want] and c.messages.counts == []


def test_100k_input_with_64k_answer_on_a_200k_model_is_sent_unchanged(ledger: ledger_mod.Ledger) -> None:
    c = _Client(counted=100_000)
    d = _door(ledger, c)
    d.call("audit", model=SMALL, messages=_pages(30), max_tokens=64_000)  # bound 180,000 + 64,000: counted
    assert c.messages.counts and c.messages.calls[0]["max_tokens"] == 64_000


def test_100k_input_with_128k_answer_on_a_200k_model_is_sent_with_its_answer_fitted(
    ledger: ledger_mod.Ledger,
) -> None:
    """Not refused (100,000 plus 16,000 of answer fits); its answer room is
    lowered to the 100,000 the window leaves, so a long answer ends as
    max_tokens, which every stage reads as ran out, never as finished."""
    c = _Client(counted=100_000)
    d = _door(ledger, c)
    d.call("compose", model=SMALL, messages=_pages(30), max_tokens=128_000)
    assert len(c.messages.calls) == 1 and c.messages.calls[0]["max_tokens"] == 100_000


def test_an_unknown_model_gets_the_200k_window(ledger: ledger_mod.Ledger) -> None:
    assert RS.window_for("claude-opus-5-5") == RS.window_for("anthropic.claude-opus-5-5") == 1_000_000
    assert RS.window_for(SMALL) == 200_000 and RS.window_for("some-future-model") == 200_000
    large = ("claude-fable-5-2", "claude-sonnet-5", "claude-haiku-5-5", "claude-opus-4-6", "claude-sonnet-4-6")
    assert all(RS.window_for(m) == 1_000_000 for m in large)
    small = ("claude-haiku-4-5-20251001", "claude-sonnet-4-5", "claude-opus-4-20250514", "claude-opus-6")
    assert all(RS.window_for(m) == 200_000 for m in small), "a generation past 5 is not guessed large"
    d = _door(ledger, _NeverCalled(counted=190_000))
    with pytest.raises(llm.RequestTooLarge) as caught:
        d.call("audit", model="some-future-model", messages=_pages(40), max_tokens=16_000)
    assert caught.value.limit == 200_000


@pytest.mark.parametrize(
    "counted",
    [RuntimeError("count failed"), "rate_limited", None],
    ids=["count raises", "count 429", "client has no count_tokens"],
)
def test_a_count_that_cannot_be_made_sends_the_request(ledger: ledger_mod.Ledger, counted) -> None:
    if counted == "rate_limited":
        import anthropic
        import httpx

        req = httpx.Request("POST", "https://x")
        counted = anthropic.RateLimitError("slow down", response=httpx.Response(429, request=req), body=None)
    c = _Client(counted=counted)
    d = _door(ledger, c)
    d.call("audit", model=BIG, messages=_pages(200), max_tokens=32_000)
    assert len(c.messages.calls) == 1 and c.messages.calls[0]["max_tokens"] == 32_000
    assert not any(ledger_mod.unsent(r) for r in ledger_mod.read_rows(ledger.path))


def test_a_count_that_hangs_sends_the_request(ledger: ledger_mod.Ledger, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    monkeypatch.setattr(RS, "COUNT_TIMEOUT_S", 0.05)
    release = threading.Event()

    class Hangs(_Messages):
        def _count(self, **kw):
            release.wait(5)
            return _Count(10_000_000)

    c = _Client()
    c.messages = Hangs(counted=0)
    try:
        _door(ledger, c).call("audit", model=BIG, messages=_pages(200), max_tokens=32_000)
    finally:
        release.set()
    assert len(c.messages.calls) == 1


def test_an_injected_counter_measures_without_the_client(ledger: ledger_mod.Ledger) -> None:
    """A rehearsal counts through the doorway's own counter: nothing is sent."""
    seen: list[dict] = []

    def counter(params: dict) -> int:
        seen.append(params)
        return 2_000_000

    d = _door(ledger, _NeverCalled(counted=0), count_tokens=counter)
    with pytest.raises(llm.RequestTooLarge):
        d.call("review", model=BIG, messages=_pages(200), max_tokens=32_000)
    assert seen and seen[0]["model"] == BIG


def test_an_api_size_refusal_becomes_request_too_large(ledger: ledger_mod.Ledger) -> None:
    import anthropic
    import httpx

    req = httpx.Request("POST", "https://x")
    too_long = anthropic.BadRequestError(
        "prompt is too long: 1187421 tokens > 1000000 maximum",
        response=httpx.Response(400, request=req),
        body={"error": {"type": "invalid_request_error"}},
    )
    c = _Client(refuse=too_long)
    d = _door(ledger, c)
    with pytest.raises(llm.RequestTooLarge) as caught:
        d.call("review", model=BIG, messages=[{"role": "user", "content": "x"}], max_tokens=8, custom_id="r-1")
    assert caught.value.measured is None and caught.value.__cause__ is too_long
    assert len(c.messages.calls) == 1, "a 400 is never retried"
    [row] = ledger_mod.read_rows(ledger.path)
    assert row["error"] == "request_too_large" and row["in"] == 0


def test_a_400_that_is_not_about_size_is_raised_as_it_was(ledger: ledger_mod.Ledger) -> None:
    import anthropic
    import httpx

    req = httpx.Request("POST", "https://x")
    bad = anthropic.BadRequestError(
        "Could not process image", response=httpx.Response(400, request=req), body={"error": {}}
    )
    d = _door(ledger, _Client(refuse=bad))
    with pytest.raises(anthropic.BadRequestError):
        d.call("audit", model=BIG, messages=[{"role": "user", "content": "x"}], max_tokens=8)
    assert ledger_mod.read_rows(ledger.path) == []


# ---- the batch round -----------------------------------------------------------
class _Batches:
    def __init__(self) -> None:
        self.created: list[list[dict]] = []

    def create(self, requests):
        for r in requests:
            assert r["custom_id"] != "huge", "an over-limit item was submitted"
        self.created.append(requests)
        return type("B", (), {"id": f"b{len(self.created)}"})()

    def retrieve(self, bid):
        return type("S", (), {"processing_status": "ended"})()

    def results(self, bid):
        out = []
        for r in self.created[int(bid[1:]) - 1]:
            res = type("R", (), {"type": "succeeded", "message": _Msg()})()
            out.append(type("X", (), {"custom_id": r["custom_id"], "result": res})())
        return out


class _BatchClient:
    def __init__(self) -> None:
        self.batches = _Batches()
        self.messages = self

    def count_tokens(self, **kw):
        n = sum(1 for b in kw["messages"][0]["content"] if b.get("type") == "image")
        return _Count(1_500_000 if n > 180 else 1_000)


def test_a_batch_round_never_submits_an_over_limit_item(ledger: ledger_mod.Ledger, tmp_path: Path) -> None:
    c = _BatchClient()
    d = _door(ledger, c, batch_stages=frozenset({"vision"}))
    got: dict[str, str | None] = {}
    items = [llm.Item("small", _pages(1)), llm.Item("huge", _pages(200)), llm.Item("near", _pages(170))]
    s = d.batch_call(
        "vision",
        items,
        lambda it, r, e: got.__setitem__(it.custom_id, e),
        model=BIG,
        max_tokens=8_000,
        batch_dir=tmp_path / "b",
    )
    assert [r["custom_id"] for r in c.batches.created[0]] == ["small", "near"]
    assert sorted(s.ok) == ["near", "small"] and list(s.failed) == ["huge"]
    assert str(got["huge"]).startswith("request_too_large: vision request huge")
    assert s.timed_out == [], "a stopped item is a failure, never left as still processing"
    unsent = [r for r in ledger_mod.read_rows(ledger.path) if ledger_mod.unsent(r)]
    assert [(r["custom_id"], r["batch"]) for r in unsent] == [("huge", True)]


# ---- the reports and the lanes ---------------------------------------------------
def test_the_ledger_report_counts_no_unsent_row_as_a_call(ledger: ledger_mod.Ledger, pricing_path: Path) -> None:
    from jobs import budget

    ledger.record("review", BIG, _Usage(), effort=None, cache=False, batch=False)
    ledger.record("review", BIG, None, effort=None, cache=False, batch=False, error=RS.UNSENT)
    out = ledger_mod.report(ledger.path, budget.Pricing.load(pricing_path))
    assert "review" in out and '"calls": 1' in out


def test_the_size_refusal_check_moved_and_the_stage_wrapper_still_reads_it() -> None:
    exc = RS.RequestTooLarge(stage="read", model=BIG, custom_id="c", measured=2, limit=1, max_tokens=1)
    assert RS.is_size_refusal(exc) and EA.ModelError(exc).too_large()
    assert not EA.ModelError(RuntimeError("other")).too_large()


def test_the_chronology_holds_with_its_fixed_sentence() -> None:
    exc = RS.RequestTooLarge(stage="review", model=BIG, custom_id="c", measured=1_187_421, limit=1, max_tokens=1)
    hold = RS.as_hold(exc)
    assert isinstance(hold, LimitHold) and hold.reason == RS.CHRONOLOGY_HOLD
    assert not any(ch.isdigit() for ch in hold.reason), "the firm reads it: no token counts"
    try:
        raise EA.ModelError(exc) from exc  # a stage that wraps it
    except EA.ModelError as wrapped:
        assert RS.as_hold(wrapped).reason == RS.CHRONOLOGY_HOLD
    limit = LimitHold("per_job_cap_usd", "per_job_cap_usd: held")
    assert RS.as_hold(limit) is limit and RS.as_hold(RuntimeError("crash")) is None
    try:  # caught and handled, then an unrelated crash: a crash, not a size hold
        try:
            raise exc
        except RS.RequestTooLarge:
            raise RuntimeError("unrelated")
    except RuntimeError as later:
        assert RS.as_hold(later) is None


def test_the_reason_carries_a_code_every_coded_lane_accepts() -> None:
    exc = RS.RequestTooLarge(
        stage="digest", model=BIG, custom_id="d", measured=1_050_000, limit=1_000_000, max_tokens=64_000
    )
    for reason in (DRAFTING_REASON, LITIGATION_REASON, CASEFILE_REASON):
        assert reason.match(str(exc)), reason.pattern[:60]
    assert "1,050,000" in str(exc) and "1,000,000" in str(exc) and "digest" in str(exc)


def test_the_chronology_driver_holds_a_stage_whose_request_is_too_large(
    tmp_path: Path, data_root: Path, pricing_path: Path, fake_pipeline: Path
) -> None:
    import test_decisions_driver as dd

    class Client(dd._Meter):
        def count_tokens(self, **kw):
            return _Count(1_187_421)

    def stage(sr) -> int:
        sr.doorway.call("vision", model=BIG, messages=_pages(200), max_tokens=16)
        return 0

    jd = dd._job(tmp_path, data_root, "job-size-hold")
    dd._seed_extracted(data_root, pages=4)
    client = Client()
    d = dd._driver(jd, dd._firm(tmp_path), pricing_path, start="vision", client=client)
    d._runner_override = {"vision": stage}
    o = d.run()[0]
    assert o.outcome == "held" and o.stage == "vision" and o.reason == RS.CHRONOLOGY_HOLD
    assert client.calls == [] and o.dollars == 0


# ---- the ratchet: no paid call outside the doorway ---------------------------------
#: Where a paid call or a client is made, and why each may.
ALLOWED = {
    ("jobs/deadline.py", "messages.create"): "the one interactive send, behind Doorway.call",
    ("jobs/deadline.py", "messages.stream"): "the one streamed send, behind Doorway.call",
    ("jobs/llm.py", "batches.create"): "the batch round's submit, after its per-item size stop",
    ("jobs/llm.py", "Anthropic("): "the doorway's default client",
    ("jobs/medchron/driver.py", "Anthropic("): "the chronology's client factory; the client is handed to the doorway",
}
_PAID = {("messages", "create"), ("messages", "stream"), ("batches", "create")}


def _paid_calls(tree: ast.AST) -> list[str]:
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Attribute) and (f.value.attr, f.attr) in _PAID:
            found.append(f"{f.value.attr}.{f.attr}")
        elif (isinstance(f, ast.Attribute) and f.attr == "Anthropic") or (
            isinstance(f, ast.Name) and f.id == "Anthropic"
        ):
            found.append("Anthropic(")
    return found


def test_no_paid_call_is_made_outside_the_doorway() -> None:
    root = Path(__file__).resolve().parents[1]
    seen = set()
    for path in sorted((root / "jobs").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        for name in _paid_calls(ast.parse(path.read_text(encoding="utf-8"))):
            assert (rel, name) in ALLOWED, f"{rel}: {name} outside the doorway (llm.Doorway) skips its size stop"
            seen.add((rel, name))
    assert seen == set(ALLOWED), f"an allowed place no longer exists, drop it: {set(ALLOWED) - seen}"


def test_the_ratchet_can_fail() -> None:
    bad = ast.parse("client.messages.create(model='m')\nanthropic.Anthropic()\nc.messages.batches.create(requests=[])")
    assert _paid_calls(bad) == ["messages.create", "Anthropic(", "batches.create"]


def test_a_request_too_large_raised_by_the_doorway_reads_as_too_large_to_the_splitting_stages(
    ledger: ledger_mod.Ledger,
) -> None:
    """update_review and the update summary split when
    ``entry_audit_io.ModelError(exc).too_large()`` is true: a stop the doorway
    raises before sending, and a refusal it translates, must both read as too
    large, or those stages fail instead of splitting."""
    d = _door(ledger, _NeverCalled(counted=1_187_421))
    with pytest.raises(llm.RequestTooLarge) as stopped:
        d.call("review", model=BIG, messages=_pages(200), max_tokens=32_000, custom_id="r-2")
    import anthropic
    import httpx

    req = httpx.Request("POST", "https://x")
    refusal = anthropic.RequestTooLargeError("too large", response=httpx.Response(413, request=req), body=None)
    with pytest.raises(llm.RequestTooLarge) as translated:
        _door(ledger, _Client(refuse=refusal)).call("review", model=BIG, messages=_pages(1), max_tokens=8)
    for exc in (stopped.value, translated.value):
        assert llm.is_size_refusal(exc) and EA.ModelError(exc).too_large()
