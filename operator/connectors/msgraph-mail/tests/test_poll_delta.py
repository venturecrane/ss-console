"""Coverage for the inbox delta poll: nextLink pagination, the deltaLink cursor,
410 expired-cursor reset, the body-missing read-path fallback, and @removed
tombstone skipping. No live Graph calls (httpx.MockTransport).
"""

from __future__ import annotations

import httpx

import msgraph_mail_connector.server as server_mod
from msgraph_mail_connector.client import MsGraphClient

_TOKEN = {"access_token": "t", "expires_in": 3600, "token_type": "Bearer"}
_DELTA_BASE = (
    "https://graph.microsoft.com/v1.0/users/operator@example.com/"
    "mailFolders/inbox/messages/delta"
)


def _msg(mid: str, *, with_body: bool = True) -> dict:
    raw = {
        "id": mid,
        "conversationId": f"conv-{mid}",
        "subject": f"subject {mid}",
        "from": {"emailAddress": {"address": f"{mid}@example.com"}},
        "toRecipients": [{"emailAddress": {"address": "operator@example.com"}}],
        "receivedDateTime": "2026-07-24T12:00:00Z",
    }
    if with_body:
        raw["body"] = {"contentType": "text", "content": f"body {mid}"}
    return raw


def _client(handler) -> MsGraphClient:
    client = MsGraphClient(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        mailbox="operator@example.com",
    )
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def _is_token(request: httpx.Request) -> bool:
    return request.url.path.endswith("/oauth2/v2.0/token")


# ---- pagination + deltaLink -----------------------------------------------
def test_pagination_across_nextlink_and_returns_deltalink() -> None:
    page2 = _DELTA_BASE + "?$skiptoken=PAGE2"
    final_delta = _DELTA_BASE + "?$deltatoken=FINAL"

    def handler(request: httpx.Request) -> httpx.Response:
        if _is_token(request):
            return httpx.Response(200, json=_TOKEN)
        if "$skiptoken=PAGE2" in str(request.url):
            return httpx.Response(
                200,
                json={"value": [_msg("b")], "@odata.deltaLink": final_delta},
            )
        # first page
        return httpx.Response(
            200,
            json={"value": [_msg("a")], "@odata.nextLink": page2},
        )

    items, delta_link, cursor_reset = _client(handler).poll_delta()
    assert [i["id"] for i in items] == ["a", "b"]
    assert delta_link == final_delta
    assert cursor_reset is False


def test_first_call_sends_select() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if _is_token(request):
            return httpx.Response(200, json=_TOKEN)
        return httpx.Response(
            200, json={"value": [], "@odata.deltaLink": _DELTA_BASE + "?d=1"}
        )

    _client(handler).poll_delta()
    delta_req = next(r for r in captured if "messages/delta" in r.url.path)
    assert "%24select" in str(delta_req.url) or "$select" in str(delta_req.url)


# ---- @removed tombstones dropped ------------------------------------------
def test_removed_tombstones_are_skipped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_token(request):
            return httpx.Response(200, json=_TOKEN)
        return httpx.Response(
            200,
            json={
                "value": [
                    _msg("keep"),
                    {"id": "gone", "@removed": {"reason": "deleted"}},
                ],
                "@odata.deltaLink": _DELTA_BASE + "?d=1",
            },
        )

    items, _, _ = _client(handler).poll_delta()
    assert [i["id"] for i in items] == ["keep"]


# ---- 410 expired-cursor reset ---------------------------------------------
def test_410_on_provided_cursor_restarts_and_flags_reset() -> None:
    stale = _DELTA_BASE + "?$deltatoken=STALE"

    def handler(request: httpx.Request) -> httpx.Response:
        if _is_token(request):
            return httpx.Response(200, json=_TOKEN)
        if "$deltatoken=STALE" in str(request.url):
            return httpx.Response(410, json={"error": {"code": "SyncStateNotFound"}})
        # fresh restart of the base delta
        return httpx.Response(
            200,
            json={"value": [_msg("fresh")], "@odata.deltaLink": _DELTA_BASE + "?d=2"},
        )

    items, delta_link, cursor_reset = _client(handler).poll_delta(stale)
    assert [i["id"] for i in items] == ["fresh"]
    assert cursor_reset is True
    assert delta_link == _DELTA_BASE + "?d=2"


# ---- server tool: body-missing triggers a read-path fetch -----------------
def test_poll_delta_tool_fetches_body_when_delta_omits_it(monkeypatch) -> None:
    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if _is_token(request):
            return httpx.Response(200, json=_TOKEN)
        path = request.url.path
        if path.endswith("messages/delta"):
            return httpx.Response(
                200,
                json={
                    "value": [_msg("nobody", with_body=False)],
                    "@odata.deltaLink": _DELTA_BASE + "?d=1",
                },
            )
        # the read-path fetch for the body-less delta item
        fetched.append(path)
        return httpx.Response(200, json=_msg("nobody", with_body=True))

    client = _client(handler)
    monkeypatch.setattr(server_mod, "_client", client)
    out = server_mod.poll_delta()
    assert fetched == ["/v1.0/users/operator@example.com/messages/nobody"]
    assert out["messages"][0]["body_text"] == "body nobody"
    assert out["delta_link"] == _DELTA_BASE + "?d=1"
    assert "cursor_reset" not in out


def test_poll_delta_tool_survives_body_fetch_failure(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_token(request):
            return httpx.Response(200, json=_TOKEN)
        if request.url.path.endswith("messages/delta"):
            return httpx.Response(
                200,
                json={
                    "value": [_msg("nobody", with_body=False)],
                    "@odata.deltaLink": _DELTA_BASE + "?d=1",
                },
            )
        return httpx.Response(404, json={"error": {"code": "ErrorItemNotFound"}})

    client = _client(handler)
    monkeypatch.setattr(server_mod, "_client", client)
    out = server_mod.poll_delta()
    # Fail-safe: the message still returns, body_text degraded to "" (never invented).
    assert out["messages"][0]["message_id"] == "nobody"
    assert out["messages"][0]["body_text"] == ""


def test_poll_delta_tool_surfaces_cursor_reset(monkeypatch) -> None:
    stale = _DELTA_BASE + "?$deltatoken=STALE"

    def handler(request: httpx.Request) -> httpx.Response:
        if _is_token(request):
            return httpx.Response(200, json=_TOKEN)
        if "$deltatoken=STALE" in str(request.url):
            return httpx.Response(410, json={"error": {"code": "SyncStateNotFound"}})
        return httpx.Response(
            200,
            json={"value": [_msg("fresh")], "@odata.deltaLink": _DELTA_BASE + "?d=2"},
        )

    client = _client(handler)
    monkeypatch.setattr(server_mod, "_client", client)
    out = server_mod.poll_delta(stale)
    assert out["cursor_reset"] is True
    assert out["messages"][0]["message_id"] == "fresh"
