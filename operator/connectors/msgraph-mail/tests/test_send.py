"""Coverage for the write surface: create_draft, send_message, reply_message.

The GOVERNANCE-CRITICAL assertion is that flat ``to`` / ``cc`` args (plain
addresses, a string or a list) become the Graph ``toRecipients`` /
``ccRecipients`` nesting INSIDE the client — the tool surface stays flat so the
overlay's recipient extraction reads it directly (email-channel-seam D4). No live
Graph calls (httpx.MockTransport).
"""

from __future__ import annotations

import json

import httpx

from msgraph_mail_connector.client import MsGraphClient

_TOKEN = {"access_token": "t", "expires_in": 3600, "token_type": "Bearer"}


def _capture_client(captured: list[httpx.Request]) -> MsGraphClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return httpx.Response(200, json=_TOKEN)
        captured.append(request)
        if (
            request.url.path.endswith("/sendMail")
            or request.url.path.endswith("/reply")
            or request.url.path.endswith("/replyAll")
        ):
            return httpx.Response(202)
        # create_draft -> Graph returns the created message
        return httpx.Response(201, json={"id": "draft-1", "isDraft": True})

    client = MsGraphClient(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        mailbox="operator@example.com",
    )
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def _body(request: httpx.Request) -> dict:
    return json.loads(request.content.decode())


# ---- send_message: flat args -> Graph nesting -----------------------------
def test_send_message_string_recipient_becomes_nesting() -> None:
    captured: list[httpx.Request] = []
    client = _capture_client(captured)
    result = client.send_mail(to="client@acme.com", subject="Hi", body_text="hello there")
    req = next(r for r in captured if r.url.path.endswith("/sendMail"))
    payload = _body(req)
    assert payload["saveToSentItems"] is True
    msg = payload["message"]
    assert msg["toRecipients"] == [{"emailAddress": {"address": "client@acme.com"}}]
    assert msg["body"] == {"contentType": "Text", "content": "hello there"}
    assert msg["subject"] == "Hi"
    assert "ccRecipients" not in msg  # none supplied -> absent, not empty
    assert result == {"status": "sent", "saveToSentItems": True}


def test_send_message_list_recipients_and_cc() -> None:
    captured: list[httpx.Request] = []
    client = _capture_client(captured)
    client.send_mail(
        to=["a@acme.com", "b@acme.com"],
        subject="Multi",
        body_text="body",
        cc="boss@acme.com",
    )
    msg = _body(next(r for r in captured if r.url.path.endswith("/sendMail")))["message"]
    assert msg["toRecipients"] == [
        {"emailAddress": {"address": "a@acme.com"}},
        {"emailAddress": {"address": "b@acme.com"}},
    ]
    assert msg["ccRecipients"] == [{"emailAddress": {"address": "boss@acme.com"}}]


def test_send_message_targets_pinned_mailbox_sendmail_path() -> None:
    captured: list[httpx.Request] = []
    client = _capture_client(captured)
    client.send_mail(to="x@acme.com", subject="s", body_text="b")
    req = next(r for r in captured if r.url.path.endswith("/sendMail"))
    assert req.url.path == "/v1.0/users/operator@example.com/sendMail"


def test_blank_addresses_are_dropped() -> None:
    captured: list[httpx.Request] = []
    client = _capture_client(captured)
    client.send_mail(to=["keep@acme.com", "", "  "], subject="s", body_text="b")
    msg = _body(next(r for r in captured if r.url.path.endswith("/sendMail")))["message"]
    assert msg["toRecipients"] == [{"emailAddress": {"address": "keep@acme.com"}}]


# ---- create_draft ---------------------------------------------------------
def test_create_draft_posts_message_to_messages_path() -> None:
    captured: list[httpx.Request] = []
    client = _capture_client(captured)
    result = client.create_draft(to="c@acme.com", subject="Draft", body_text="wip", cc=["cc@acme.com"])
    req = next(r for r in captured if r.url.path.endswith("/messages"))
    assert req.url.path == "/v1.0/users/operator@example.com/messages"
    payload = _body(req)
    # create_draft posts the message resource directly (not wrapped in "message").
    assert payload["toRecipients"] == [{"emailAddress": {"address": "c@acme.com"}}]
    assert payload["ccRecipients"] == [{"emailAddress": {"address": "cc@acme.com"}}]
    assert payload["body"] == {"contentType": "Text", "content": "wip"}
    assert result == {"id": "draft-1", "isDraft": True}


# ---- reply / replyAll -----------------------------------------------------
def test_reply_posts_comment_to_reply_path() -> None:
    captured: list[httpx.Request] = []
    client = _capture_client(captured)
    result = client.reply("msg-42", "on it")
    req = next(r for r in captured if r.url.path.endswith("/reply"))
    assert req.url.path == "/v1.0/users/operator@example.com/messages/msg-42/reply"
    assert _body(req) == {"comment": "on it"}
    assert result == {"status": "replied", "reply_all": False, "message_id": "msg-42"}


def test_reply_all_targets_replyall_path() -> None:
    captured: list[httpx.Request] = []
    client = _capture_client(captured)
    result = client.reply("msg-42", "to everyone", reply_all=True)
    req = next(r for r in captured if r.url.path.endswith("/replyAll"))
    assert req.url.path == "/v1.0/users/operator@example.com/messages/msg-42/replyAll"
    assert _body(req) == {"comment": "to everyone"}
    assert result["reply_all"] is True
