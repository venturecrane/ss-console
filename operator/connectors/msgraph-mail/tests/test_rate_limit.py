"""A 429 from Graph waits as long as Graph says, capped (Retry-After).

Graph names the back-off in Retry-After, and a retry before then is throttled
again, so a fixed 1-2-4 s backoff could exhaust its attempts inside one
throttle window. No live Graph calls (MockTransport); time.sleep is recorded.
"""

from __future__ import annotations

import httpx
import pytest

from msgraph_mail_connector import client as client_mod
from msgraph_mail_connector.client import MsGraphApiError, MsGraphClient

_TOKEN = {"access_token": "t", "expires_in": 3600, "token_type": "Bearer"}


def _client(script: list[httpx.Response]) -> tuple[MsGraphClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return httpx.Response(200, json=_TOKEN)
        seen.append(request)
        return script.pop(0)

    client = MsGraphClient(tenant_id="t", client_id="c", client_secret="s", mailbox="op@firm.example")
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return client, seen


def _throttled(retry_after: str | None) -> httpx.Response:
    return httpx.Response(429, headers={"Retry-After": retry_after} if retry_after is not None else {})


@pytest.mark.parametrize(
    ("retry_after", "slept"),
    [("7", 7.0), (None, 1.0), ("600", 30.0), ("Wed, 21 Oct 2026 07:28:00 GMT", 1.0)],
)
def test_429_waits_as_long_as_graph_says_capped(monkeypatch, retry_after, slept) -> None:
    waits: list[float] = []
    monkeypatch.setattr(client_mod.time, "sleep", waits.append)
    client, seen = _client([_throttled(retry_after), httpx.Response(200, json={"value": []})])
    assert client.list_messages("inbox", 10) == {"value": []}
    assert waits == [slept]
    assert len(seen) == 2


def test_a_429_that_never_clears_raises_without_a_last_wasted_wait(monkeypatch) -> None:
    waits: list[float] = []
    monkeypatch.setattr(client_mod.time, "sleep", waits.append)
    client, seen = _client([_throttled("2") for _ in range(client_mod._MAX_ATTEMPTS)])
    with pytest.raises(MsGraphApiError) as exc:
        client.list_messages("inbox", 10)
    assert exc.value.status == 429
    assert len(seen) == client_mod._MAX_ATTEMPTS
    assert waits == [2.0] * (client_mod._MAX_ATTEMPTS - 1)
