"""Unit coverage for file_attachment_to_matter's fetch leg (#1744) — the
cross-connector transfer guardrails: https-only, host allowlist (injected
content must not be able to direct arbitrary web content into a matter file),
no auth headers on the token-bearing URL, and the size cap."""

from __future__ import annotations

import httpx
import pytest

from smokeball_connector.client import SmokeballClient, SmokeballWriteError

_ATTACH_URL = "https://download.agentmail.to/attachments/att_1?token=tok123"


def _mock_client(handler) -> SmokeballClient:
    client = SmokeballClient(region="us", environment="staging", client_id="cid", client_secret="sec", api_key="apikey")
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def _handler(captured: list[httpx.Request], blob: bytes):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if str(request.url) == _ATTACH_URL:
            return httpx.Response(200, content=blob)
        return httpx.Response(404)

    return handler


def test_cdn_host_allowed() -> None:
    # Live finding 2026-07-07 (post-reprovision verification): AgentMail's
    # get_attachment mints URLs on cdn.agentmail.to; the allowlist only knew
    # download.agentmail.to and the transfer correctly fail-closed. Both
    # vendor hosts are now defaults.
    url = "https://cdn.agentmail.to/attachments/att_2?token=tok456"
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, content=b"%PDF-1.4 cdn copy")

    client = _mock_client(handler)
    assert client.fetch_attachment_url(url) == b"%PDF-1.4 cdn copy"


def test_fetch_attachment_happy_path_no_auth_headers() -> None:
    captured: list[httpx.Request] = []
    client = _mock_client(_handler(captured, b"%PDF-1.4 served set"))

    blob = client.fetch_attachment_url(_ATTACH_URL)

    assert blob == b"%PDF-1.4 served set"
    (req,) = captured
    assert "authorization" not in req.headers
    assert "x-api-key" not in req.headers


def test_fetch_attachment_refuses_disallowed_host() -> None:
    client = _mock_client(_handler([], b""))
    with pytest.raises(SmokeballWriteError, match="not an allowed attachment source"):
        client.fetch_attachment_url("https://evil.example.com/payload.pdf")


def test_fetch_attachment_refuses_http_scheme() -> None:
    client = _mock_client(_handler([], b""))
    with pytest.raises(SmokeballWriteError, match="not an allowed attachment source"):
        client.fetch_attachment_url("http://download.agentmail.to/attachments/att_1")


def test_fetch_attachment_host_override_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMOKEBALL_ATTACHMENT_URL_HOSTS", "files.example.test")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, content=b"ok")

    client = _mock_client(handler)
    assert client.fetch_attachment_url("https://files.example.test/a") == b"ok"
    # And the default host is no longer allowed once overridden.
    with pytest.raises(SmokeballWriteError, match="not an allowed attachment source"):
        client.fetch_attachment_url(_ATTACH_URL)


def test_fetch_attachment_refuses_oversized_body() -> None:
    big = b"x" * (25 * 1024 * 1024 + 1)
    client = _mock_client(_handler([], big))
    with pytest.raises(SmokeballWriteError, match="over the"):
        client.fetch_attachment_url(_ATTACH_URL)
