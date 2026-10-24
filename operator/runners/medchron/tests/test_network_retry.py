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
    assert d._daemon_state("01A")["infra_requeues"] == 1
    assert (d._daemon_state("01A").get("wake") or {}).get("pending") is not True, "no deliver turn for a requeue"
    # Not before its delay: the next tick runs nothing.
    assert d.tick() is None and (d.job_dir("01A") / "ran").read_text() == "1"
    d._now["t"] += 301  # type: ignore[attr-defined]
    assert d.tick() == "delivered"
    assert [s for _, s, _ in broker.records] == ["running", "running", "running", "delivered"]


def test_a_network_failure_past_the_retries_is_terminal_and_says_it_was_retried(tmp_path) -> None:
    from medchron import requeue

    d, broker = _daemon(tmp_path, _runner(99))
    _submit(d, broker, "01A")
    for _ in range(requeue.MAX_REQUEUES):
        assert d.tick() == "requeued"
        d._now["t"] += requeue.REQUEUE_DELAY_S + 1  # type: ignore[attr-defined]
    assert d.tick() == "failed"
    job, state, fields = broker.records[-1]
    assert state == "failed" and fields["reason"].startswith(
        f"retried after network error at update_review {requeue.MAX_REQUEUES} time(s), failed again"
    )
    d._now["t"] += 10_000  # type: ignore[attr-defined]
    ran = int((d.job_dir("01A") / "ran").read_text())
    assert d.tick() is None and ran == requeue.MAX_REQUEUES + 1, "terminal: bounded"


def test_a_failure_that_is_not_the_network_is_failed_at_once(tmp_path) -> None:
    script = _runner(1).replace('"transient": True', '"transient": False')
    d, broker = _daemon(tmp_path, script)
    _submit(d, broker, "01A")
    assert d.tick() == "failed" and broker.records[-1][1] == "failed"


# ---- stop audit row 20: a slow read-back re-runs the upload before it parks ----------
SHORT_REASON = (
    "upload: the folder read back short after the retries; the files may still be materializing, nothing was deleted"
)


def _upload_runner(short_runs: int, *, stage: str = "upload", reason: str = SHORT_REASON) -> str:
    """A fake runner whose upload reads back short for its first `short_runs`
    runs (Smokeball indexing slowly, the 2026-09-24 shape), then delivers."""
    held = json.dumps(
        [
            {
                "unit": "alpha",
                "outcome": "held",
                "stage": stage,
                "dollars": 31.0,
                "pages": 300,
                "documents": 40,
                "reason": reason,
            }
        ]
    )
    return _runner(0).replace(
        "if n <= 0:",
        f"if n <= {short_runs}:\n    print({held!r})\n    sys.exit(0)\nif False:",
    )


def test_a_short_upload_read_back_runs_the_upload_again_after_a_delay(tmp_path) -> None:
    from medchron import requeue

    d, broker = _daemon(tmp_path, _upload_runner(2))
    _submit(d, broker, "01A")
    assert d.tick() == "requeued"
    st = d._daemon_state("01A")
    assert st["state"] == "running" and st["infra_requeues"] == 1
    assert "held" not in [s for _, s, _ in broker.records], "a slow index is not parked as a hold"
    assert broker.records[-1][1] == "running" and "read back short" in broker.records[-1][2]["reason"]
    # Not before its delay.
    assert d.tick() is None and (d.job_dir("01A") / "ran").read_text() == "1"
    d._now["t"] += requeue.UPLOAD_DELAY_S + 1  # type: ignore[attr-defined]
    assert d.tick() == "requeued" and d._daemon_state("01A")["infra_requeues"] == 2
    d._now["t"] += requeue.UPLOAD_DELAY_S + 1  # type: ignore[attr-defined]
    assert d.tick() == "delivered"
    assert "held" not in [s for _, s, _ in broker.records]


def test_a_read_back_still_short_after_the_retries_parks_the_hold(tmp_path) -> None:
    from medchron import requeue

    d, broker = _daemon(tmp_path, _upload_runner(99))
    _submit(d, broker, "01A")
    for _ in range(requeue.MAX_REQUEUES):
        assert d.tick() == "requeued"
        d._now["t"] += requeue.UPLOAD_DELAY_S + 1  # type: ignore[attr-defined]
    assert d.tick() == "held"
    job, state, fields = broker.records[-1]
    assert (
        state == "held"
        and fields["reason"].startswith("upload: the folder read back short")
        and "retried after upload read back short" in fields["reason"]
    )
    d._now["t"] += 10 * requeue.UPLOAD_DELAY_S  # type: ignore[attr-defined]
    ran = (d.job_dir("01A") / "ran").read_text()
    assert d.tick() is None and (d.job_dir("01A") / "ran").read_text() == ran, "parked: bounded"
    assert int(ran) == requeue.MAX_REQUEUES + 1


SIGNAL_RUNNER = """
import json, os, signal, sys, pathlib
jd = pathlib.Path(sys.argv[1])
marker = jd / 'ran'
n = int(marker.read_text()) + 1 if marker.exists() else 1
marker.write_text(str(n))
if n <= {killed}:
    os.kill(os.getpid(), signal.SIGTERM)
print(json.dumps([{{"unit": "alpha", "outcome": "delivered", "reason": None, "stage": None, "dollars": 31.0,
    "pages": 300, "documents": 40, "folder_id": "folder-9",
    "files": [{{"name": "A.docx", "sha256": "ab", "bytes": 10, "confirmed": True}}],
    "proof": {{"readback": "confirmed", "files": [{{"name": "x.docx", "file_id": "f1", "size": 10,
        "sha256": "{sha}"}}], "review": "passed", "review_cents": 0}}}}]))
"""


def test_the_runner_exited_minus_15_without_a_verdict_is_retried_not_failed(tmp_path) -> None:
    """The history case (09-22, 10-13, 10-23): the child died of SIGTERM with
    the daemon still running (no Machine stop), and the job recorded "failed:
    the runner exited -15 without a verdict", terminal. It is now retried."""
    from medchron import requeue

    d, broker = _daemon(tmp_path, SIGNAL_RUNNER.format(killed=1, sha="a" * 64))
    _submit(d, broker, "01A")
    assert d.tick() == "requeued"
    assert "failed" not in [s for _, s, _ in broker.records]
    assert broker.records[-1][2]["reason"] == "retried after runner killed by a signal at an unnamed stage"
    d._now["t"] += requeue.REQUEUE_DELAY_S + 1  # type: ignore[attr-defined]
    assert d.tick() == "delivered"


def test_a_runner_killed_on_every_retry_lands_failed_as_before(tmp_path) -> None:
    from medchron import requeue

    d, broker = _daemon(tmp_path, SIGNAL_RUNNER.format(killed=99, sha="a" * 64))
    _submit(d, broker, "01A")
    for _ in range(requeue.MAX_REQUEUES):
        assert d.tick() == "requeued"
        d._now["t"] += requeue.REQUEUE_DELAY_S + 1  # type: ignore[attr-defined]
    assert d.tick() == "failed"
    assert "the runner exited -15 without a verdict" in broker.records[-1][2]["reason"]


def test_a_model_api_crash_is_retried_and_a_defect_crash_is_not(tmp_path) -> None:
    from medchron import requeue

    api = (
        "map: exit 1; last output: Traceback ... anthropic.InternalServerError: Error code: 500 - "
        "{'type': 'error', 'error': {'type': 'api_error', 'message': 'Internal server error'}}"
    )
    script = (
        _runner(1)
        .replace('"transient": True', '"transient": False')
        .replace("update_review: a part of the update's review did not finish; a rerun reviews again", api)
    )
    d, broker = _daemon(tmp_path, script)
    _submit(d, broker, "01A")
    assert d.tick() == "requeued" and "model API error" in broker.records[-1][2]["reason"]
    d._now["t"] += requeue.REQUEUE_DELAY_S + 1  # type: ignore[attr-defined]
    assert d.tick() == "delivered"
    assert requeue.cause([{"outcome": "failed", "reason": "map: KeyError: 'p'"}], "failed", {}, "map") is None
    assert requeue.cause([], "failed", {"reason": "the runner exited 137 without a verdict"}, None) is None
    assert requeue.cause([{"outcome": "held"}], "held", {"reason": "per_job_cap_usd: over"}, "vision") is None


INFRA_RUNNER = """
import json, sys, pathlib
jd = pathlib.Path(sys.argv[1])
marker = jd / 'ran'
n = int(marker.read_text()) + 1 if marker.exists() else 1
marker.write_text(str(n))
if n == 1:
    st = jd / 'data' / 'example' / 'runs' / 'alpha' / 'state.json'
    st.parent.mkdir(parents=True, exist_ok=True)
    st.write_text(json.dumps({"stages": {"map": {"status": "failed", "exit_code": %(code)d}}}))
    print(json.dumps([{"unit": "alpha", "outcome": "failed", "stage": "map", "dollars": 12.0, "pages": 300,
        "documents": 40, "transient": False, "reason": "map: exit %(code)d; last output: chunk-07 failed"}]))
    sys.exit(0)
print(json.dumps([{"unit": "alpha", "outcome": "delivered", "reason": None, "stage": None, "dollars": 31.0,
    "pages": 300, "documents": 40, "folder_id": "folder-9",
    "files": [{"name": "A.docx", "sha256": "ab", "bytes": 10, "confirmed": True}],
    "proof": {"readback": "confirmed", "files": [{"name": "x.docx", "file_id": "f1", "size": 10,
        "sha256": "%(sha)s"}], "review": "passed", "review_cents": 0}}]))
"""


def test_a_stage_exit_of_75_is_infrastructure_and_retried(tmp_path) -> None:
    """compose.INFRA_EXIT (75, #3464): map and repair return it when an API
    error outlived its retries. The daemon reads the stage's exit code off
    the job's state.json and retries; the same failure with exit 1 (a defect)
    is failed at once."""
    from medchron import requeue

    assert requeue.INFRA_EXIT == 75
    d, broker = _daemon(tmp_path, INFRA_RUNNER % {"code": 75, "sha": "a" * 64})
    _submit(d, broker, "01A")
    assert d.tick() == "requeued" and "model API error at map" in broker.records[-1][2]["reason"]
    d._now["t"] += requeue.REQUEUE_DELAY_S + 1  # type: ignore[attr-defined]
    assert d.tick() == "delivered"

    d2, broker2 = _daemon(tmp_path / "defect", INFRA_RUNNER % {"code": 1, "sha": "a" * 64})
    _submit(d2, broker2, "01B")
    assert d2.tick() == "failed" and broker2.records[-1][1] == "failed"


def test_the_infrastructure_wording_is_the_fallback_when_no_exit_code_is_on_file() -> None:
    from medchron import requeue

    o = {"outcome": "failed", "reason": "map incomplete (infrastructure): an API error outlived its retries"}
    assert requeue.cause([o], "failed", {}, "map") == "model_api"


def test_other_holds_are_not_re_run(tmp_path) -> None:
    """Only the read-back-short hold is a wait; a review hold, or the upload's
    refusal over bytes that changed, is not re-run by the daemon."""
    d, broker = _daemon(
        tmp_path, _upload_runner(9, stage="review", reason="review_held: the pre-delivery review found a must-fix")
    )
    _submit(d, broker, "01A")
    assert d.tick() == "held" and broker.records[-1][1] == "held"


def test_a_mixed_attempt_is_a_real_failure() -> None:
    from medchron import requeue

    net = {"outcome": "failed", "transient": True}
    real = {"outcome": "failed"}
    assert requeue.transient_failure([net]) and requeue.transient_failure([net, {"outcome": "delivered"}])
    assert not requeue.transient_failure([net, real]) and not requeue.transient_failure([])
    assert json.dumps(net)  # the verdict field is plain JSON
