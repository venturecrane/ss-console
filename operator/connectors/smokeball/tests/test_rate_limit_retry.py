"""A 429 is waited out, Retry-After first, and bounded.

2026-10-14 (A&P litigation proof): the run listed 26,000 files, Smokeball
answered 429, four attempts over 15 s were not enough, and the run failed at
inventory with "unexpected: SmokeballApiError". A rate limit clears with time:
the client now honors Retry-After (capped), backs off to the cap otherwise,
and still gives up after a bounded number of attempts so a gateway turn never
hangs on a limit that will not clear.

FALSIFIER: drop the Retry-After read and the 7 s sleep becomes 1 s; drop the
attempt bound and the persistent case loops forever; shrink the attempts back
to four and the clears-on-the-fifth case raises.
"""

from __future__ import annotations

from typing import Any

import pytest

from smokeball_connector import client as mod
from smokeball_connector.client import SmokeballApiError, SmokeballClient


class _Resp:
    def __init__(self, status: int, headers: dict[str, str] | None = None, body: Any = None) -> None:
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self.text = "" if body is None else str(body)
        self.content = b"" if body is None else b"x"

    def json(self) -> Any:
        return self._body


class _Http:
    def __init__(self, script: list[_Resp]) -> None:
        self.script = list(script)
        self.calls = 0

    def request(self, *_a: Any, **_k: Any) -> _Resp:
        self.calls += 1
        return self.script.pop(0) if len(self.script) > 1 else self.script[0]


def _client(script: list[_Resp], sleeps: list[float], monkeypatch: pytest.MonkeyPatch) -> SmokeballClient:
    c = SmokeballClient(region="us", environment="staging", client_id="cid", client_secret="sec", api_key="key")
    c._http = _Http(script)  # type: ignore[assignment]
    c._token, c._token_deadline = "tok", float("inf")
    monkeypatch.setattr(mod.time, "sleep", lambda s: sleeps.append(s))
    return c


def test_retry_after_is_honored_and_the_call_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    c = _client(
        [_Resp(429, {"retry-after": "7"}), _Resp(429), _Resp(200, body={"value": [1]})],
        sleeps,
        monkeypatch,
    )
    assert c.get("/matters") == {"value": [1]}
    # First wait is the server's 7 s; the second has no header: 2**1.
    assert sleeps == [7.0, 2.0]


def test_a_limit_that_clears_on_the_fifth_attempt_no_longer_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    c = _client([_Resp(429)] * 4 + [_Resp(200, body={"ok": True})], sleeps, monkeypatch)
    assert c.get("/matters") == {"ok": True}
    assert len(sleeps) == 4


def test_a_persistent_limit_is_bounded_and_named(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    c = _client([_Resp(429, {"retry-after": "900"}, body='{"message":"Too Many Requests"}')], sleeps, monkeypatch)
    with pytest.raises(SmokeballApiError) as exc:
        c.get("/matters")
    assert exc.value.status == 429
    http: Any = c._http
    assert http.calls == mod._RATE_LIMIT_ATTEMPTS
    # Every wait is capped, and there is one fewer wait than attempts.
    assert len(sleeps) == mod._RATE_LIMIT_ATTEMPTS - 1
    assert all(s == mod._RATE_LIMIT_MAX_SLEEP for s in sleeps)


def test_a_non_numeric_retry_after_falls_back_to_backoff() -> None:
    assert mod._rate_limit_sleep(_Resp(429, {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}), 3) == 8.0
    assert mod._rate_limit_sleep(_Resp(429), 0) == 1.0
    assert mod._rate_limit_sleep(_Resp(429), 10) == mod._RATE_LIMIT_MAX_SLEEP
