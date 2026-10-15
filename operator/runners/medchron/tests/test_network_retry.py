"""A network blip never ends a job (2026-10-14).

An offline update run stopped at `update_review` after $29.69 of work, its
audit already passed, because one APIConnectionError outlived the doorway's
three attempts and sixty seconds of backoff. On the seat the same event
records `failed`, which is terminal. Two layers now hold:

* the doorway retries a connection error, 429 or 5xx with exponential backoff
  and jitter for ten minutes of wall clock, without spending the attempts a
  hung stream's deadline is counted against, and never retries a 400 or an
  auth failure;
* a stage that still fails on one is requeued ONCE by the daemon, and the
  ledger row says so; a second network failure is terminal.

Synthetic throughout: fake clients, fake runners, no network.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace as NS
from typing import Any

import anthropic
import httpx
import pytest

from medchron import deadline, ledger as ledger_mod, llm
from medchron.deadline import CallDeadlineError

from test_upload_daemon import _daemon, _submit

REQ = httpx.Request("POST", "https://api.example.test/v1/messages")
MSGS = [{"role": "user", "content": "x"}]


def _conn() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(request=REQ)


def _status(cls: type, code: int) -> Exception:
    return cls(f"status {code}", response=httpx.Response(code, request=REQ), body=None)


def _msg(text: str = "ok") -> Any:
    usage = NS(input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return NS(content=[NS(type="text", text=text)], stop_reason="end_turn", usage=usage)


class Script:
    """messages.create that plays a script: an exception is raised, anything
    else is returned. Past the end it repeats the last entry."""

    def __init__(self, *steps: Any) -> None:
        self.steps, self.calls = list(steps), 0

    def create(self, **_: Any) -> Any:
        step = self.steps[min(self.calls, len(self.steps) - 1)]
        self.calls += 1
        if isinstance(step, BaseException):
            raise step
        return step


class FakeTime:
    """One clock for the doorway and for `time.sleep`, so the old code (which
    calls time.sleep directly) and the new (which takes an injected sleep) are
    measured the same way and neither really waits."""

    def __init__(self) -> None:
        self.now = 0.0
        self.waits: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.waits.append(s)
        self.now += s


def _doorway(tmp_path, client: Any, ft: FakeTime, monkeypatch: pytest.MonkeyPatch, logs: list[str]) -> llm.Doorway:
    monkeypatch.setattr(time, "sleep", ft.sleep)
    d = llm.Doorway(ledger_mod.Ledger(tmp_path / "ledger.jsonl"), client=NS(messages=client), log=logs.append)
    d.clock, d.sleep = ft.clock, ft.sleep
    return d


# ---- (a) a connection error followed by success is retried, not raised --------
def test_connection_errors_past_three_attempts_are_retried_until_the_call_answers(tmp_path, monkeypatch) -> None:
    ft, logs = FakeTime(), []
    client = Script(_conn(), _conn(), _status(anthropic.RateLimitError, 429), _conn(), _msg("answered"))
    d = _doorway(tmp_path, client, ft, monkeypatch, logs)
    r = d.call("update_review", model="m", max_tokens=10, messages=MSGS)
    assert r.text == "answered" and client.calls == 5
    assert d.gave_up == []
    assert any("answered after 4 network error(s)" in line for line in logs)
    rows = ledger_mod.read_rows(tmp_path / "ledger.jsonl")
    assert len(rows) == 1 and rows[0]["stage"] == "update_review"


def test_overloaded_529_and_a_500_are_network_class_too(tmp_path, monkeypatch) -> None:
    ft = FakeTime()
    over = _status(anthropic.InternalServerError, 529)
    client = Script(over, _status(anthropic.InternalServerError, 500), over, over, _msg())
    d = _doorway(tmp_path, client, ft, monkeypatch, [])
    assert d.call("compose", model="m", max_tokens=10, messages=MSGS).text == "ok" and client.calls == 5


# ---- (b) a 400 or an auth failure is not retried ----------------------------------
@pytest.mark.parametrize(
    ("cls", "code"),
    [
        (anthropic.BadRequestError, 400),
        (anthropic.AuthenticationError, 401),
        (anthropic.PermissionDeniedError, 403),
    ],
)
def test_a_request_error_is_raised_at_once(tmp_path, monkeypatch, cls: type, code: int) -> None:
    ft = FakeTime()
    client = Script(_status(cls, code), _msg())
    d = _doorway(tmp_path, client, ft, monkeypatch, [])
    with pytest.raises(cls):
        d.call("compose", model="m", max_tokens=10, messages=MSGS)
    assert client.calls == 1 and ft.waits == [] and d.gave_up == []


def test_an_unrecognised_error_gets_its_attempts_not_the_network_budget(tmp_path, monkeypatch) -> None:
    ft = FakeTime()
    client = Script(ValueError("a defect, not the network"))
    d = _doorway(tmp_path, client, ft, monkeypatch, [])
    with pytest.raises(ValueError):
        d.call("compose", model="m", max_tokens=10, messages=MSGS, attempts=3)
    assert client.calls == 3 and d.gave_up == []
    assert not deadline.is_transient(ValueError("x")) and deadline.is_transient(CallDeadlineError("x"))


# ---- (c) the backoff respects the budget and the deadline ---------------------------
def test_the_network_budget_is_ten_minutes_of_growing_jittered_waits_then_it_gives_up(tmp_path, monkeypatch) -> None:
    ft = FakeTime()
    client = Script(_conn())
    d = _doorway(tmp_path, client, ft, monkeypatch, [])
    with pytest.raises(anthropic.APIConnectionError):
        d.call("update_review", model="m", max_tokens=10, messages=MSGS)
    waits = ft.waits
    assert sum(waits) <= deadline.NETWORK_RETRY_S + 1e-6, "never waits past the budget"
    assert sum(waits) >= deadline.NETWORK_RETRY_S - 1e-6, "and spends all of it before giving up"
    assert max(waits) <= deadline.NETWORK_BACKOFF_CAP_S
    assert waits[1] > waits[0] * 0.9 and waits[2] > waits[1] * 0.9, f"exponential: {waits[:4]}"
    assert client.calls == len(waits) + 1 and client.calls > 3
    assert d.gave_up and "APIConnectionError" in d.gave_up[0] and d.gave_up[0].startswith("update_review")
    assert not (tmp_path / "ledger.jsonl").exists()


def test_the_budget_is_configurable_and_jitter_is_drawn_per_wait() -> None:
    draws = iter([0.0, 1.0, 0.0, 1.0])
    rc = deadline.RetryClock(attempts=3, backoff=10.0, budget_s=900.0, rand=lambda: next(draws))
    got = [rc.wait_after(_conn(), 0.0) for _ in range(4)]
    assert got == [5.0, 20.0, 20.0, 80.0]


def test_a_fast_network_failure_never_spends_an_attempt_meant_for_a_hung_call(tmp_path, monkeypatch) -> None:
    ft = FakeTime()
    hung = CallDeadlineError("model call aborted: no reply within its 1s deadline")
    client = Script(_conn(), hung, _conn(), _conn(), hung, _conn(), _msg("through"))
    d = _doorway(tmp_path, client, ft, monkeypatch, [])
    assert d.call("compose", model="m", max_tokens=10, messages=MSGS, attempts=3).text == "through"


def test_a_hung_call_is_still_bounded_by_its_attempts_whatever_the_budget(tmp_path, monkeypatch) -> None:
    ft = FakeTime()
    client = Script(CallDeadlineError("model call aborted: no reply within its 1s deadline"))
    d = _doorway(tmp_path, client, ft, monkeypatch, [])
    d.retry_budget_s = 10_000.0
    with pytest.raises(CallDeadlineError, match="on all 2 attempts"):
        d.call("compose", model="m", max_tokens=10, messages=MSGS, attempts=2)
    assert client.calls == 2 and d.gave_up and "CallDeadlineError" in d.gave_up[0]


def test_a_firm_cannot_set_the_budget_below_the_floor(firm_config_path) -> None:
    from medchron import config as config_mod
    import yaml

    data = yaml.safe_load(firm_config_path.read_text())
    data.setdefault("levers", {})["network_retry_s"] = 60
    assert any("network_retry_s" in p for p in config_mod.validate(data))
    data["levers"]["network_retry_s"] = 1200
    assert not any("network_retry_s" in p for p in config_mod.validate(data))


# ---- (d)/(e) a stage's network failure requeues once; the second is terminal --------
def _runner(transient_runs: int) -> str:
    """A fake runner that fails on the network for its first `transient_runs`
    runs, then delivers. Counts its runs in the job dir."""
    return f"""
import json, sys, pathlib
jd = pathlib.Path(sys.argv[1])
marker = jd / 'ran'
n = int(marker.read_text()) + 1 if marker.exists() else 1
marker.write_text(str(n))
if n <= {transient_runs}:
    print(json.dumps([{{"unit": "alpha", "outcome": "failed", "stage": "update_review", "dollars": 29.69,
        "pages": 300, "documents": 40, "transient": True,
        "reason": "update_review: a part of the update's review did not finish; a rerun reviews again"}}]))
    sys.exit(0)
print(json.dumps([{{"unit": "alpha", "outcome": "delivered", "reason": None, "stage": None, "dollars": 31.0,
    "pages": 300, "documents": 40, "folder_id": "folder-9",
    "files": [{{"name": "A.docx", "sha256": "ab", "bytes": 10, "confirmed": True}}],
    "proof": {{"readback": "confirmed", "files": [{{"name": "x.docx", "file_id": "f1", "size": 10,
        "sha256": "{"a" * 64}"}}], "review": "passed", "review_cents": 0}}}}]))
"""


def test_a_network_failure_requeues_the_job_once_and_it_is_never_marked_failed(tmp_path) -> None:
    d, broker = _daemon(tmp_path, _runner(1))
    _submit(d, broker, "01A")
    first = d.tick()
    assert first != "failed" and d._daemon_state("01A")["state"] != "failed"
    assert "failed" not in [s for _, s, _ in broker.records]
    note = broker.records[-1]
    assert note[1] == "running" and note[2]["reason"] == "retried after network error at update_review"
    assert d._daemon_state("01A")["network_requeues"] == 1
    assert (d._daemon_state("01A").get("wake") or {}).get("pending") is not True, "no deliver turn for a requeue"
    # Not before its delay: the next tick runs nothing.
    assert d.tick() is None and (d.job_dir("01A") / "ran").read_text() == "1"
    d._now["t"] += 301  # type: ignore[attr-defined]
    assert d.tick() == "delivered"
    assert [s for _, s, _ in broker.records] == ["running", "running", "running", "delivered"]


def test_a_second_network_failure_is_terminal_and_says_it_was_retried(tmp_path) -> None:
    d, broker = _daemon(tmp_path, _runner(5))
    _submit(d, broker, "01A")
    d.tick()
    d._now["t"] += 301  # type: ignore[attr-defined]
    assert d.tick() == "failed"
    job, state, fields = broker.records[-1]
    assert state == "failed" and fields["reason"].startswith(
        "retried after network error at update_review, failed again"
    )
    d._now["t"] += 10_000  # type: ignore[attr-defined]
    assert d.tick() is None and (d.job_dir("01A") / "ran").read_text() == "2", "terminal: never run a third time"


def test_a_failure_that_is_not_the_network_is_failed_at_once(tmp_path) -> None:
    script = _runner(1).replace('"transient": True', '"transient": False')
    d, broker = _daemon(tmp_path, script)
    _submit(d, broker, "01A")
    assert d.tick() == "failed" and broker.records[-1][1] == "failed"


def test_a_mixed_attempt_is_a_real_failure() -> None:
    from medchron import requeue

    net = {"outcome": "failed", "transient": True}
    real = {"outcome": "failed"}
    assert requeue.transient_failure([net]) and requeue.transient_failure([net, {"outcome": "delivered"}])
    assert not requeue.transient_failure([net, real]) and not requeue.transient_failure([])
    assert json.dumps(net)  # the verdict field is plain JSON
