"""Unit coverage for the two auth modes + the optional accountId URL prefix.

No live Smokeball calls: an ``httpx.MockTransport`` (built into httpx, no extra
dep) is injected into the client's HTTP engine so the real ``_mint_token`` /
``request`` logic runs against scripted responses. The client_credentials path is
deliberately re-asserted here so a regression in the auth-code branch can't
silently change the proven default.
"""

from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from smokeball_connector.client import SmokeballClient, SmokeballAuthError


def _mock_client(handler, **overrides) -> SmokeballClient:
    kwargs = dict(
        region="us",
        environment="staging",
        client_id="cid",
        client_secret="sec",
        api_key="apikey",
    )
    kwargs.update(overrides)
    client = SmokeballClient(**kwargs)
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def _token_handler(captured: list[httpx.Request], *, rotate: str | None = None):
    """A handler that answers the token endpoint and echoes any API call as {ok}."""

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path.endswith("/oauth2/token"):
            body: dict = {
                "access_token": "zzz-access-secret",
                "expires_in": 3600,
                "token_type": "Bearer",
            }
            if rotate is not None:
                body["refresh_token"] = rotate
            return httpx.Response(200, json=body)
        return httpx.Response(200, json={"ok": True, "path": request.url.path})

    return handler


# ---- grant selection ------------------------------------------------------
def test_client_credentials_mint_body_is_unchanged() -> None:
    captured: list[httpx.Request] = []
    client = _mock_client(_token_handler(captured))
    client.auth_status()
    token_req = next(r for r in captured if r.url.path.endswith("/oauth2/token"))
    form = parse_qs(token_req.content.decode())
    assert form["grant_type"] == ["client_credentials"]
    assert form["client_id"] == ["cid"]
    assert "refresh_token" not in form


def test_authorization_code_mints_via_refresh_token() -> None:
    captured: list[httpx.Request] = []
    client = _mock_client(_token_handler(captured), auth_mode="authorization_code", refresh_token="rt-123")
    client.auth_status()
    token_req = next(r for r in captured if r.url.path.endswith("/oauth2/token"))
    form = parse_qs(token_req.content.decode())
    assert form["grant_type"] == ["refresh_token"]
    assert form["refresh_token"] == ["rt-123"]
    assert form["client_id"] == ["cid"]


def test_authorization_code_requires_refresh_token() -> None:
    with pytest.raises(ValueError, match="refresh_token"):
        SmokeballClient(
            region="us",
            environment="staging",
            client_id="cid",
            client_secret="sec",
            api_key="apikey",
            auth_mode="authorization_code",
        )


def test_unknown_auth_mode_rejected() -> None:
    with pytest.raises(ValueError, match="auth_mode"):
        SmokeballClient(
            region="us",
            environment="staging",
            client_id="cid",
            client_secret="sec",
            api_key="apikey",
            auth_mode="implicit",
        )


def test_refresh_token_rotation_is_adopted() -> None:
    captured: list[httpx.Request] = []
    client = _mock_client(
        _token_handler(captured, rotate="rt-NEW"),
        auth_mode="authorization_code",
        refresh_token="rt-OLD",
    )
    client.auth_status()
    assert client._refresh_token == "rt-NEW"


# ---- accountId prefix -----------------------------------------------------
def test_no_account_id_means_no_prefix() -> None:
    captured: list[httpx.Request] = []
    client = _mock_client(_token_handler(captured))
    client.get("/matters")
    api_req = next(r for r in captured if r.url.path.endswith("/matters"))
    assert api_req.url.path == "/matters"


def test_account_id_prefixes_every_request() -> None:
    captured: list[httpx.Request] = []
    client = _mock_client(_token_handler(captured), account_id="acct-9")
    client.get("/matters")
    api_req = next(r for r in captured if r.url.path.endswith("/matters"))
    assert api_req.url.path == "/acct-9/matters"


def test_account_id_is_normalized_to_a_bare_segment() -> None:
    captured: list[httpx.Request] = []
    client = _mock_client(_token_handler(captured), account_id="/acct-9/")
    client.get("/matters")
    api_req = next(r for r in captured if r.url.path.endswith("/matters"))
    assert api_req.url.path == "/acct-9/matters"


# ---- auth_status surface (never leaks the token) --------------------------
def test_auth_status_reports_mode_not_token() -> None:
    captured: list[httpx.Request] = []
    client = _mock_client(
        _token_handler(captured),
        auth_mode="authorization_code",
        refresh_token="rt-123",
        account_id="acct-9",
    )
    status = client.auth_status()
    assert status["auth_mode"] == "authorization_code"
    assert status["account_scoped"] is True
    blob = repr(status)
    assert "rt-123" not in blob and "zzz-access-secret" not in blob


def test_mint_failure_does_not_echo_grant() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_grant", "secret_echo": "rt-123"})

    client = _mock_client(handler, auth_mode="authorization_code", refresh_token="rt-123")
    with pytest.raises(SmokeballAuthError) as exc:
        client.auth_status()
    assert "rt-123" not in str(exc.value)
    assert "authorization_code" in str(exc.value)


# ---- granted-scope introspection (JWT scope claim) ------------------------
def _make_jwt(payload: dict) -> str:
    """A minimal unsigned-shape JWT (header.payload.sig) for scope-decode tests."""
    import base64
    import json

    def seg(d: bytes) -> str:
        return base64.urlsafe_b64encode(d).rstrip(b"=").decode()

    return f"{seg(b'{}')}.{seg(json.dumps(payload).encode())}.sig"


def test_auth_status_decodes_granted_scopes() -> None:
    jwt = _make_jwt({"scope": "documents/read documents/write matters/read"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": jwt, "expires_in": 3600, "token_type": "Bearer"})

    client = _mock_client(handler, auth_mode="authorization_code", refresh_token="rt-1")
    status = client.auth_status()
    assert status["granted_scopes"] == [
        "documents/read",
        "documents/write",
        "matters/read",
    ]
    assert jwt not in repr(status)  # the token itself never leaks


def test_auth_status_granted_scopes_empty_for_opaque_token() -> None:
    captured: list[httpx.Request] = []
    # _token_handler mints access_token="zzz-access-secret" (not a JWT → no scopes).
    client = _mock_client(_token_handler(captured))
    assert client.auth_status()["granted_scopes"] == []


def test_mint_logs_granted_scopes_once(capsys) -> None:
    jwt = _make_jwt({"scope": "documents/read documents/write matters/read"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": jwt, "expires_in": 3600, "token_type": "Bearer"})

    client = _mock_client(handler, auth_mode="authorization_code", refresh_token="rt-1")
    client._mint_token()
    client._mint_token()  # second mint must NOT re-log
    err = capsys.readouterr().err
    assert err.count("[smokeball] authenticated") == 1
    assert "documents/write" in err
    assert jwt not in err  # the token itself is never logged


# ---- ADR 0054: durable refresh-token file (read + rotation persist) --------
def test_authorization_code_persists_rotated_token_to_file(tmp_path) -> None:
    token_file = tmp_path / "refresh_token"
    captured: list[httpx.Request] = []
    client = _mock_client(
        _token_handler(captured, rotate="rt-ROTATED"),
        auth_mode="authorization_code",
        refresh_token="rt-OLD",
        refresh_token_file=str(token_file),
    )
    client.auth_status()  # mints → Smokeball rotates → file rewritten
    assert client._refresh_token == "rt-ROTATED"
    assert token_file.read_text() == "rt-ROTATED"
    assert (token_file.stat().st_mode & 0o777) == 0o600


def test_rotation_keeps_a_group_shared_mode(tmp_path) -> None:
    # ss#2614: on a seat with the chronology runner the token is group-shared
    # (0660) between two uids; a rotation by either must not narrow it back to
    # 0600, or the other uid is locked out of Smokeball at the next mint.
    token_file = tmp_path / "refresh_token"
    token_file.write_text("rt-OLD")
    token_file.chmod(0o660)
    captured: list[httpx.Request] = []
    client = _mock_client(
        _token_handler(captured, rotate="rt-ROTATED"),
        auth_mode="authorization_code",
        refresh_token="rt-OLD",
        refresh_token_file=str(token_file),
    )
    client.auth_status()
    assert token_file.read_text() == "rt-ROTATED"
    assert (token_file.stat().st_mode & 0o777) == 0o660


def test_no_rotation_does_not_touch_file(tmp_path) -> None:
    token_file = tmp_path / "refresh_token"
    captured: list[httpx.Request] = []
    # rotate=None → the token response carries no new refresh_token.
    client = _mock_client(
        _token_handler(captured),
        auth_mode="authorization_code",
        refresh_token="rt-OLD",
        refresh_token_file=str(token_file),
    )
    client.auth_status()
    assert not token_file.exists()  # nothing rotated → nothing written


# ---- ss#2148: auth_status reports whether the durable file holds the
# ---- CURRENT refresh token (the silent-persist-failure race, observable) ----
def test_auth_status_reports_persisted_true_after_rotation(tmp_path) -> None:
    token_file = tmp_path / "refresh_token"
    token_file.write_text("rt-OLD")
    captured: list[httpx.Request] = []
    client = _mock_client(
        _token_handler(captured, rotate="rt-ROTATED"),
        auth_mode="authorization_code",
        refresh_token="rt-OLD",
        refresh_token_file=str(token_file),
    )
    status = client.auth_status()
    assert status["refresh_token_persisted"] is True  # rotated AND rewritten


def test_auth_status_reports_persisted_false_when_write_failed(tmp_path) -> None:
    # Point the durable file into a directory that does not exist: the
    # best-effort persist swallows the OSError, so without this flag the dead
    # state is invisible until the next restart bricks the connector.
    token_file = tmp_path / "no-such-dir" / "refresh_token"
    captured: list[httpx.Request] = []
    client = _mock_client(
        _token_handler(captured, rotate="rt-ROTATED"),
        auth_mode="authorization_code",
        refresh_token="rt-OLD",
        refresh_token_file=str(token_file),
    )
    status = client.auth_status()
    assert status["refresh_token_persisted"] is False


def test_auth_status_persisted_not_applicable_for_client_credentials() -> None:
    captured: list[httpx.Request] = []
    client = _mock_client(_token_handler(captured))
    status = client.auth_status()
    assert status["refresh_token_persisted"] is None


def test_read_refresh_token_file_then_env(tmp_path, monkeypatch) -> None:
    # Construction moved to client.build_client_from_env (single source of truth
    # shared with the egress reconciler); the token reader lives there now.
    from smokeball_connector import client

    token_file = tmp_path / "refresh_token"
    monkeypatch.delenv("SMOKEBALL_REFRESH_TOKEN", raising=False)
    assert client.read_refresh_token(str(token_file)) is None  # neither present

    monkeypatch.setenv("SMOKEBALL_REFRESH_TOKEN", "from-env")
    assert client.read_refresh_token(str(token_file)) == "from-env"  # env fallback

    token_file.write_text("from-file\n")
    assert client.read_refresh_token(str(token_file)) == "from-file"  # file wins


def test_build_client_from_env_constructs_and_fail_modes(tmp_path, monkeypatch) -> None:
    """The single-source env factory shared by the server and the egress reconciler."""
    from smokeball_connector import client

    for k in ("SMOKEBALL_CLIENT_ID", "SMOKEBALL_CLIENT_SECRET", "SMOKEBALL_API_KEY"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("SMOKEBALL_ENVIRONMENT", "staging")
    monkeypatch.setenv("SMOKEBALL_REGION", "us")
    monkeypatch.delenv("SMOKEBALL_REFRESH_TOKEN", raising=False)
    monkeypatch.setenv("SMOKEBALL_REFRESH_TOKEN_FILE", str(tmp_path / "absent"))

    # client_credentials (default) needs no refresh token → constructs fine.
    monkeypatch.delenv("SMOKEBALL_AUTH_MODE", raising=False)
    c = client.build_client_from_env()
    assert c.auth_mode == "client_credentials" and c.environment == "staging"

    # authorization_code with no token available → fail-closed ValueError (the
    # reconciler catches this as "not connected yet; skip, retry").
    monkeypatch.setenv("SMOKEBALL_AUTH_MODE", "authorization_code")
    with pytest.raises(ValueError):
        client.build_client_from_env()


# ---- self-heal: stale in-memory refresh token vs newer token file ----------
def test_rejected_mint_reloads_newer_token_from_file_and_retries(tmp_path) -> None:
    """A re-connect (OAuth callback) writes a NEW refresh token to the durable
    file while a long-running process still holds the old one in memory. The
    mint must re-read the file on rejection and retry once with the new token —
    the 2026-07-02 pilot-smokeball incident (stale cached MCP client → HTTP 400
    → breaker open)."""
    token_file = tmp_path / "refresh-token"
    token_file.write_text("rt-new\n")

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path.endswith("/oauth2/token"):
            form = parse_qs(request.content.decode())
            if form["refresh_token"] == ["rt-new"]:
                return httpx.Response(
                    200,
                    json={
                        "access_token": "zzz-access-secret",
                        "expires_in": 3600,
                        "token_type": "Bearer",
                    },
                )
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(200, json={"ok": True})

    client = _mock_client(
        handler,
        auth_mode="authorization_code",
        refresh_token="rt-stale",
        refresh_token_file=str(token_file),
    )
    client.auth_status()  # must succeed via the reload-retry path

    token_reqs = [r for r in captured if r.url.path.endswith("/oauth2/token")]
    assert len(token_reqs) == 2  # rejected stale mint, then one retry
    assert parse_qs(token_reqs[0].content.decode())["refresh_token"] == ["rt-stale"]
    assert parse_qs(token_reqs[1].content.decode())["refresh_token"] == ["rt-new"]
    assert client._refresh_token == "rt-new"  # adopted for future mints


def test_rejected_mint_with_no_newer_file_token_fails_once(tmp_path) -> None:
    """When the file holds the SAME token the process already has (a genuinely
    dead grant), there is nothing to heal with: fail after a single attempt —
    no blind second mint."""
    token_file = tmp_path / "refresh-token"
    token_file.write_text("rt-stale\n")

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(400, json={"error": "invalid_grant"})

    client = _mock_client(
        handler,
        auth_mode="authorization_code",
        refresh_token="rt-stale",
        refresh_token_file=str(token_file),
    )
    with pytest.raises(SmokeballAuthError):
        client.auth_status()
    assert len(captured) == 1
