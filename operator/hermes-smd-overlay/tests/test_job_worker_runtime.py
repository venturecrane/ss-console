"""Tests for the pure parts of the B1 worker runtime binding (ADR 0051).

The Hermes/infra functions (build_hermes_agent, cost readers, delivery, the
thread) are staging-exercised. The readiness barrier is pure and gates whether
the worker may claim a job, so it is unit-tested.
"""

from __future__ import annotations

import logging
import os

import pytest

from shared.job_ledger_client import JobLedgerError
from shared.job_worker_runtime import (
    _is_broker_unreachable,
    _unreachable_log_level,
    put_result,
    readiness_ok,
)

# Env vars that, when all present, switch put_result onto the R2 path. The
# entrypoint/config-applier contract: same names the ADR-0044 applier reads.
_R2_ENV = (
    "R2_ENDPOINT_URL",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET_CONFIG",
    "CUSTOMER_SLUG",
    "SMD_CUSTOMER_SLUG",
)


@pytest.fixture
def no_r2_env(monkeypatch):
    """Ensure no ambient R2 results env leaks into a fallback test."""
    for name in _R2_ENV:
        monkeypatch.delenv(name, raising=False)


def test_readiness_all_pass():
    assert readiness_ok([lambda: True, lambda: True]) is True


def test_readiness_one_fails():
    assert readiness_ok([lambda: True, lambda: False]) is False


def test_readiness_empty_is_ready():
    assert readiness_ok([]) is True


def test_readiness_raising_check_is_not_ready():
    def boom():
        raise OSError("broker socket not listening")

    assert readiness_ok([lambda: True, boom]) is False


# -- put_result: R2 result store (ADR 0051 Decision 8) ------------------------


def test_put_result_returns_r2_ref_with_injected_uploader(monkeypatch):
    """With R2 env present and a fake uploader injected, put_result PUTs to the
    per-customer bucket under jobs/<slug>/<id>.md and returns an r2:// ref."""
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://acct.r2.cloudflarestorage.com")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setenv("R2_BUCKET_CONFIG", "smd-cust-bucket")
    monkeypatch.setenv("CUSTOMER_SLUG", "demo-law")

    calls: list[tuple[str, str, bytes]] = []

    def fake_uploader(bucket: str, key: str, body: bytes) -> None:
        calls.append((bucket, key, body))

    ref = put_result({"id": "job-123"}, "the result body", uploader=fake_uploader)

    assert ref == "r2://smd-cust-bucket/jobs/demo-law/job-123.md"
    assert calls == [("smd-cust-bucket", "jobs/demo-law/job-123.md", b"the result body")]


def test_put_result_falls_back_to_volume_when_r2_env_absent(no_r2_env, tmp_path, monkeypatch):
    """With R2 env unset (local/CI), put_result writes to the volume and returns
    a file:// ref — so unit envs still produce a retrievable artifact."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    ref = put_result({"id": "job-abc"}, "fallback body")

    expected = tmp_path / "job_results" / "job-abc.md"
    assert ref == f"file://{expected}"
    assert expected.read_text(encoding="utf-8") == "fallback body"


def test_put_result_falls_back_to_volume_when_uploader_raises(tmp_path, monkeypatch):
    """An R2 PUT failure must not lose the result: fall back to the volume."""
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://acct.r2.cloudflarestorage.com")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setenv("R2_BUCKET_CONFIG", "smd-cust-bucket")
    monkeypatch.setenv("CUSTOMER_SLUG", "demo-law")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    def boom_uploader(bucket: str, key: str, body: bytes) -> None:
        raise RuntimeError("R2 unreachable")

    ref = put_result({"id": "job-xyz"}, "resilient body", uploader=boom_uploader)

    expected = tmp_path / "job_results" / "job-xyz.md"
    assert ref == f"file://{expected}"
    assert expected.read_text(encoding="utf-8") == "resilient body"


# -- broker-unreachable severity (2026-08-10) ---------------------------------
# The worker has no shutdown path: Hermes exposes no process-lifecycle hook to
# plugins, so on machine teardown the broker exits while this daemon thread is
# still sweeping. That produces a connection-refused sweep failure identical in
# shape to a genuinely dead broker. The loop separates them by persistence —
# a dying process never reaches a second consecutive miss.


def test_broker_unreachable_recognises_refused_and_missing_socket():
    """Both shapes of "the socket would not take a connection" count."""
    for cause in (ConnectionRefusedError(111, "Connection refused"), FileNotFoundError()):
        exc = JobLedgerError("job broker socket error")
        exc.__cause__ = cause
        assert _is_broker_unreachable(exc) is True


def test_broker_unreachable_excludes_a_hung_broker():
    """A timeout means the broker ACCEPTED and then failed to answer — a hung
    broker is a real fault, not teardown, so it must not be softened."""
    exc = JobLedgerError("job broker socket error")
    exc.__cause__ = TimeoutError("timed out")
    assert _is_broker_unreachable(exc) is False


def test_broker_unreachable_excludes_unrelated_failures():
    """Only a wrapped transport refusal qualifies: not other JobLedgerErrors
    (e.g. the unset-env constructor raise, which has no cause), and not
    arbitrary sweep crashes."""
    assert _is_broker_unreachable(JobLedgerError("SMD_WORKSPACE_BROKER_SOCKET is unset")) is False
    assert _is_broker_unreachable(ValueError("something else entirely")) is False
    # Pins the TYPE guard specifically: a refused connection raised by anything
    # other than the job-ledger transport is not this failure mode. Without
    # this case the guard can be deleted and the suite still passes.
    foreign = ValueError("some other client's socket")
    foreign.__cause__ = ConnectionRefusedError(111, "Connection refused")
    assert _is_broker_unreachable(foreign) is False


def test_first_miss_stays_below_the_sentry_event_threshold():
    """The whole point: miss #1 must NOT reach Sentry. sentry_init sets no
    LoggingIntegration, so the SDK default event_level=ERROR applies and
    anything below ERROR is a breadcrumb only."""
    assert _unreachable_log_level(1) == logging.WARNING
    assert _unreachable_log_level(1) < logging.ERROR


def test_second_consecutive_miss_escalates_to_a_real_outage():
    """One sweep interval later the process is still alive, which teardown does
    not survive. That is an outage and must reach Sentry."""
    assert _unreachable_log_level(2) == logging.ERROR


def test_one_outage_reaches_sentry_once_not_every_sweep():
    """SMD-OPERATOR-2G (2026-10-05): a 75s gap produced a Sentry event at misses
    2, 3, 4 and 5. Only the escalation pages; the rest are breadcrumbs."""
    levels = [_unreachable_log_level(n) for n in range(1, 41)]
    assert levels.count(logging.ERROR) == 1
    assert levels[1] == logging.ERROR
    assert all(lvl == logging.WARNING for i, lvl in enumerate(levels) if i != 1)


def test_session_db_open_failure_retries_instead_of_killing_the_worker(caplog):
    """#3564: v0.22.5's first open of an empty v2026.9.14 state.db raised in the
    worker thread and the seat ran with no durable-job worker. FALSIFIER: let the
    exception escape and this raises instead of returning the fourth open."""
    import sqlite3

    from shared import job_worker_runtime as rt

    attempts: list[int] = []
    slept: list[float] = []

    class Flaky:
        def __init__(self) -> None:
            attempts.append(1)
            if len(attempts) < 4:
                raise sqlite3.OperationalError("no such savepoint: fts_align_empty")

    with caplog.at_level(logging.INFO, logger=rt.logger.name):
        db = rt._open_session_db(Flaky, sleep=slept.append)
    assert isinstance(db, Flaky) and len(attempts) == 4
    assert slept == [2.0, 5.0, 15.0]
    levels = [r.levelno for r in caplog.records if "SessionDB open failed" in r.message]
    assert levels == [logging.WARNING, logging.WARNING, logging.ERROR]
    assert any("opened after 3 failed attempt(s)" in r.message for r in caplog.records)


def test_session_db_retry_backoff_caps_at_a_minute():
    from shared import job_worker_runtime as rt

    slept: list[float] = []
    n = {"i": 0}

    class Flaky:
        def __init__(self) -> None:
            n["i"] += 1
            if n["i"] <= 8:
                raise RuntimeError("down")

    rt._open_session_db(Flaky, sleep=slept.append)
    assert slept == [2.0, 5.0, 15.0, 30.0, 60.0, 60.0, 60.0, 60.0]


# -- who starts the worker thread ---------------------------------------------
# Bootstrap step 8 (the safety-substrate invariant run) is a short-lived python
# that registers every overlay plugin, broker socket in its env. Before
# 2026-10-28 that started a worker thread in a process about to exit, and every
# boot logged "RuntimeError: can't register atexit after shutdown" from it. The
# worker is the gateway's alone.


@pytest.fixture
def worker_spawns(monkeypatch):
    """Reset the once-only latch and record threads instead of starting them."""
    from shared import job_worker_runtime as rt

    spawned: list[str] = []

    class _RecordingThread:
        def __init__(self, *, target, name, daemon):
            self.name = name

        def start(self):
            spawned.append(self.name)

    monkeypatch.setattr(rt, "_started", False)
    monkeypatch.setattr(rt.threading, "Thread", _RecordingThread)
    monkeypatch.setenv("SMD_WORKSPACE_BROKER_SOCKET", "/run/smd/broker.sock")
    return rt, spawned


def test_a_non_gateway_process_with_the_broker_socket_starts_no_worker(worker_spawns, monkeypatch):
    rt, spawned = worker_spawns
    monkeypatch.delenv("SMD_GATEWAY_PID", raising=False)
    assert rt.start_worker_thread() is False
    monkeypatch.setenv("SMD_GATEWAY_PID", str(os.getpid() + 1))
    assert rt.start_worker_thread() is False
    assert spawned == []


def test_the_gateway_starts_the_worker_once_even_after_a_refused_call(worker_spawns, monkeypatch):
    rt, spawned = worker_spawns
    monkeypatch.delenv("SMD_GATEWAY_PID", raising=False)
    assert rt.start_worker_thread() is False
    monkeypatch.setenv("SMD_GATEWAY_PID", str(os.getpid()))
    assert rt.start_worker_thread() is True
    assert rt.start_worker_thread() is False
    assert spawned == ["smd-job-worker"]
