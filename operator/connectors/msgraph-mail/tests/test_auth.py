"""Unit coverage for the app-only client-credentials token flow.

No live Graph calls: an ``httpx.MockTransport`` (built into httpx, no extra dep) is
injected into the client's HTTP engine so the real ``_mint_token`` / ``request``
logic runs against scripted responses.
"""

from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from msgraph_mail_connector.client import (
    MsGraphAuthError,
    MsGraphClient,
    build_client_from_env,
)


def _mock_client(handler, **overrides) -> MsGraphClient:
    kwargs = dict(
        tenant_id="tenant-abc",
        client_id="cid",
        client_secret="sec",
        mailbox="operator@example.com",
    )
    kwargs.update(overrides)
    client = MsGraphClient(**kwargs)
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def _token_handler(captured: list[httpx.Request], *, expires_in: int = 3600):
    """Answer the token endpoint with a bearer; echo any Graph call as {ok}."""

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return httpx.Response(
                200,
                json={
                    "access_token": "zzz-access-secret",
                    "expires_in": expires_in,
                    "token_type": "Bearer",
                },
            )
        return httpx.Response(200, json={"ok": True, "path": request.url.path})

    return handler


# ---- token mint form + endpoint -------------------------------------------
def test_client_credentials_mint_body_and_scope() -> None:
    captured: list[httpx.Request] = []
    client = _mock_client(_token_handler(captured))
    client.list_messages("inbox", 10)  # first tool call mints
    token_req = next(r for r in captured if r.url.path.endswith("/oauth2/v2.0/token"))
    form = parse_qs(token_req.content.decode())
    assert form["grant_type"] == ["client_credentials"]
    assert form["client_id"] == ["cid"]
    assert form["client_secret"] == ["sec"]
    assert form["scope"] == ["https://graph.microsoft.com/.default"]


def test_token_endpoint_carries_tenant_id() -> None:
    captured: list[httpx.Request] = []
    client = _mock_client(_token_handler(captured), tenant_id="my-tenant-guid")
    client.list_messages("inbox", 10)
    token_req = next(r for r in captured if r.url.path.endswith("/oauth2/v2.0/token"))
    assert str(token_req.url) == ("https://login.microsoftonline.com/my-tenant-guid/oauth2/v2.0/token")


# ---- caching --------------------------------------------------------------
def test_token_cached_until_near_expiry() -> None:
    captured: list[httpx.Request] = []
    client = _mock_client(_token_handler(captured))
    client.list_messages("inbox", 10)
    client.list_messages("inbox", 10)
    client.get_message("abc")
    token_reqs = [r for r in captured if r.url.path.endswith("/oauth2/v2.0/token")]
    assert len(token_reqs) == 1  # minted once, reused for the later calls


# ---- 401 re-mint retry ----------------------------------------------------
def test_401_re_mints_and_retries_once() -> None:
    state = {"minted": 0, "api_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/v2.0/token"):
            state["minted"] += 1
            return httpx.Response(
                200,
                json={
                    "access_token": f"tok-{state['minted']}",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        state["api_calls"] += 1
        if state["api_calls"] == 1:
            return httpx.Response(401, json={"error": {"code": "InvalidAuthToken"}})
        return httpx.Response(200, json={"ok": True})

    client = _mock_client(handler)
    result = client.get_message("abc")
    assert result == {"ok": True}
    assert state["minted"] == 2  # initial mint + one fresh mint after the 401
    assert state["api_calls"] == 2  # original + retry


# ---- missing-credential errors (fail closed at construction) --------------
@pytest.mark.parametrize("field", ["tenant_id", "client_id", "client_secret", "mailbox"])
def test_missing_credential_raises_at_construction(field: str) -> None:
    kwargs = dict(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        mailbox="operator@example.com",
    )
    kwargs[field] = ""
    with pytest.raises(ValueError, match=field):
        MsGraphClient(**kwargs)


def test_build_client_from_env_fails_closed_when_unset(monkeypatch) -> None:
    for k in (
        "MSGRAPH_TENANT_ID",
        "MSGRAPH_CLIENT_ID",
        "MSGRAPH_CLIENT_SECRET",
        "MSGRAPH_MAILBOX",
    ):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ValueError):
        build_client_from_env()


def test_build_client_from_env_constructs_when_present(monkeypatch) -> None:
    monkeypatch.setenv("MSGRAPH_TENANT_ID", "t")
    monkeypatch.setenv("MSGRAPH_CLIENT_ID", "c")
    monkeypatch.setenv("MSGRAPH_CLIENT_SECRET", "s")
    monkeypatch.setenv("MSGRAPH_MAILBOX", "operator@example.com")
    client = build_client_from_env()
    assert client.mailbox == "operator@example.com"


# ---- mint failure never echoes the secret ---------------------------------
def test_mint_failure_does_not_echo_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_client", "secret_echo": "sec"})

    client = _mock_client(handler)
    with pytest.raises(MsGraphAuthError) as exc:
        client.list_messages("inbox", 10)
    assert "sec" not in str(exc.value)
    assert "client_credentials" in str(exc.value)


def test_missing_access_token_in_response_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"expires_in": 3600, "token_type": "Bearer"})

    client = _mock_client(handler)
    with pytest.raises(MsGraphAuthError, match="no access_token"):
        client.list_messages("inbox", 10)
