"""Coverage for the Graph message -> InboundMessage DTO normalization (spec D2):
HTML body stripping, bare/lowercased addresses, provider_refs, and fail-safe
degradation to empty/None (never an invented value).
"""

from __future__ import annotations

import httpx

from msgraph_mail_connector.client import MsGraphClient
from msgraph_mail_connector.normalize import html_to_text, normalize_message


def _raw(**over) -> dict:
    base = {
        "id": "AAMkAGm-id",
        "conversationId": "conv-123",
        "subject": "Re: intake",
        "from": {"emailAddress": {"name": "Christa", "address": "Christa@Example.COM"}},
        "toRecipients": [
            {"emailAddress": {"address": "Operator@Example.com"}},
            {"emailAddress": {"address": "second@Example.com"}},
        ],
        "ccRecipients": [{"emailAddress": {"address": "Boss@Example.com"}}],
        "receivedDateTime": "2026-07-24T15:30:00Z",
        "body": {"contentType": "text", "content": "plain body"},
    }
    base.update(over)
    return base


# ---- HTML stripping -------------------------------------------------------
def test_html_body_stripped_to_text() -> None:
    raw = _raw(
        body={
            "contentType": "HTML",
            "content": (
                "<html><head><style>p{color:red}</style></head>"
                "<body><p>Hello <b>World</b></p>"
                "<script>evil()</script>"
                "<p>Line two &amp; more</p></body></html>"
            ),
        }
    )
    dto = normalize_message(raw, mailbox="operator@example.com")
    assert dto["body_text"] == "Hello World\nLine two & more"


def test_html_to_text_decodes_entities_and_drops_scripts() -> None:
    text = html_to_text("<div>a &lt;b&gt; c</div><script>x=1</script>")
    assert text == "a <b> c"


def test_plain_body_passed_through() -> None:
    dto = normalize_message(_raw(), mailbox="operator@example.com")
    assert dto["body_text"] == "plain body"


# ---- address normalization ------------------------------------------------
def test_addresses_lowercased_and_bare() -> None:
    dto = normalize_message(_raw(), mailbox="operator@example.com")
    assert dto["from_addr"] == "christa@example.com"
    assert dto["to"] == ["operator@example.com", "second@example.com"]
    assert dto["cc"] == ["boss@example.com"]


# ---- provider_refs + identity fields --------------------------------------
def test_provider_refs_and_identity_populated() -> None:
    dto = normalize_message(_raw(), mailbox="operator@example.com")
    assert dto["provider"] == "msgraph"
    assert dto["mailbox"] == "operator@example.com"
    assert dto["message_id"] == "AAMkAGm-id"
    assert dto["thread_ref"] == "conv-123"
    assert dto["subject"] == "Re: intake"
    assert dto["received_at"] == "2026-07-24T15:30:00Z"
    assert dto["provider_refs"] == {
        "graph_message_id": "AAMkAGm-id",
        "conversation_id": "conv-123",
    }


# ---- fail-safe: missing fields degrade, never invent ----------------------
def test_missing_fields_degrade_to_empty_never_invented() -> None:
    dto = normalize_message({"id": "only-id"}, mailbox="operator@example.com")
    assert dto["from_addr"] == ""  # no sender -> empty, not a guess
    assert dto["to"] == []
    assert dto["cc"] == []
    assert dto["subject"] == ""
    assert dto["body_text"] == ""
    assert dto["thread_ref"] is None
    assert dto["received_at"] is None
    assert dto["provider_refs"]["conversation_id"] is None
    assert dto["provider_refs"]["graph_message_id"] == "only-id"


def test_malformed_recipient_shapes_are_dropped_not_invented() -> None:
    raw = _raw(
        from_="ignored",
        toRecipients=[
            {"emailAddress": {"address": ""}},  # blank -> dropped
            {"emailAddress": {}},  # no address -> dropped
            "not-a-dict",  # junk -> dropped
            {"emailAddress": {"address": "keep@example.com"}},
        ],
    )
    dto = normalize_message(raw, mailbox="operator@example.com")
    assert dto["to"] == ["keep@example.com"]


# ---- read_message tool path (client + normalize together) -----------------
def _mock_client(handler) -> MsGraphClient:
    client = MsGraphClient(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        mailbox="operator@example.com",
    )
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def test_get_message_hits_pinned_mailbox_path() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return httpx.Response(
                200,
                json={"access_token": "t", "expires_in": 3600, "token_type": "Bearer"},
            )
        return httpx.Response(200, json=_raw())

    client = _mock_client(handler)
    client.get_message("AAMkAGm-id")
    api_req = next(r for r in captured if "messages" in r.url.path)
    assert api_req.url.path == ("/v1.0/users/operator@example.com/messages/AAMkAGm-id")
