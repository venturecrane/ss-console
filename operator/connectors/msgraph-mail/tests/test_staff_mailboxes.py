"""The staff-mailbox reads: a firm-authored allowlist, fail closed.

The load-bearing assertion is the refusal: a mailbox the firm did not author
never produces a Graph request at all. Every refusal test counts requests, and
the one authorized case proves the counter can see a request, so a refusal that
silently called Graph would fail here. No live Graph calls (MockTransport).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from msgraph_mail_connector import server as srv
from msgraph_mail_connector import staff_mailboxes as sm
from msgraph_mail_connector.client import MsGraphClient

_TOKEN = {"access_token": "t", "expires_in": 3600, "token_type": "Bearer"}
OWN = "operator@firm.example"
STAFF = "manager@firm.example"

_MESSAGE = {
    "id": "AAMk-1",
    "subject": "Discovery lists",
    "from": {"emailAddress": {"address": "Someone@Outside.example"}},
    "toRecipients": [{"emailAddress": {"address": STAFF}}],
    "ccRecipients": [],
    "receivedDateTime": "2026-09-22T16:00:00Z",
    "body": {"contentType": "Text", "content": "the chart is attached"},
    "conversationId": "conv-1",
}


def _client(seen: list[httpx.Request]) -> MsGraphClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return httpx.Response(200, json=_TOKEN)
        seen.append(request)
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"value": [_MESSAGE]})
        return httpx.Response(200, json=_MESSAGE)

    client = MsGraphClient(tenant_id="t", client_id="c", client_secret="s", mailbox=OWN)
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


@pytest.fixture
def seat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A seat whose customer.yaml the test authors, and a client that records
    every Graph request it would have made."""
    yaml_path = tmp_path / "customer.yaml"
    monkeypatch.setenv(sm.CUSTOMER_YAML_ENV, str(yaml_path))
    seen: list[httpx.Request] = []
    monkeypatch.setattr(srv, "_client", _client(seen))

    def author(text: str | None) -> None:
        if text is None:
            if yaml_path.exists():
                yaml_path.unlink()
        else:
            yaml_path.write_text(text, encoding="utf-8")

    return author, seen


AUTHORED = f"staff_mailbox_reads:\n  mailboxes:\n    - {STAFF.upper()}\n"


# ---- the authorized path, which also proves the counter sees a request ------
def test_authored_mailbox_lists_under_that_mailbox_only(seat) -> None:
    author, seen = seat
    author(AUTHORED)
    out = srv.list_staff_messages(STAFF, top=500)
    assert out == {"value": [_MESSAGE]}
    assert len(seen) == 1
    assert seen[0].url.path == f"/v1.0/users/{STAFF}/mailFolders/inbox/messages"
    assert seen[0].url.params["$top"] == "50"
    assert seen[0].url.params["$orderby"] == "receivedDateTime desc"


def test_search_replaces_orderby_and_is_quoted_safely(seat) -> None:
    author, seen = seat
    author(AUTHORED)
    srv.list_staff_messages(STAFF, search='discovery" OR from:x\\')
    params = seen[0].url.params
    assert "$orderby" not in params
    assert params["$search"].startswith('"') and params["$search"].endswith('"')
    assert params["$search"].count('"') == 2


def test_read_normalizes_with_the_staff_mailbox(seat) -> None:
    author, seen = seat
    author(AUTHORED)
    dto = srv.read_staff_message(STAFF, "AAMk-1")
    assert len(seen) == 1
    assert seen[0].url.path == f"/v1.0/users/{STAFF}/messages/AAMk-1"
    assert dto["mailbox"] == STAFF
    assert dto["from_addr"] == "someone@outside.example"
    assert "chart is attached" in dto["body_text"]


# ---- every refusal makes no Graph request ----------------------------------
@pytest.mark.parametrize(
    "yaml_text",
    [
        None,  # no customer.yaml on the seat
        "firm_identity:\n  name: x\n",  # no block
        "staff_mailbox_reads:\n  mailboxes: []\n",  # empty list
        "staff_mailbox_reads:\n  mailboxes:\n    - other@firm.example\n",  # someone else
        "staff_mailbox_reads: [unclosed\n",  # unparseable
        "staff_mailbox_reads:\n  mailboxes: manager@firm.example\n",  # not a list
    ],
)
def test_unauthored_is_refused_before_graph(seat, yaml_text) -> None:
    author, seen = seat
    author(yaml_text)
    for out in (srv.list_staff_messages(STAFF), srv.read_staff_message(STAFF, "AAMk-1")):
        assert out["status"] == "refused"
    assert seen == []


@pytest.mark.parametrize(
    "mailbox",
    [
        "manager@firm.example/../operator@firm.example",
        "manager@firm.example?$select=id",
        "manager@firm.example#x",
        "manager%40firm.example",
        " ",
        None,
    ],
)
def test_path_changing_addresses_are_refused(seat, mailbox) -> None:
    author, seen = seat
    author(AUTHORED)
    assert srv.list_staff_messages(mailbox)["status"] == "refused"
    assert seen == []


def test_own_mailbox_is_refused_even_when_authored(seat) -> None:
    author, seen = seat
    author(f"staff_mailbox_reads:\n  mailboxes:\n    - {OWN}\n")
    out = srv.read_staff_message(OWN, "AAMk-1")
    assert out["status"] == "refused" and "own mailbox" in out["reason"]
    assert seen == []


def test_bad_folder_is_refused(seat) -> None:
    author, seen = seat
    author(AUTHORED)
    assert srv.list_staff_messages(STAFF, folder="inbox/../x")["status"] == "refused"
    assert seen == []


def test_refusal_names_what_is_authored(seat) -> None:
    author, _ = seat
    author(AUTHORED)
    out = srv.list_staff_messages("stranger@firm.example")
    assert STAFF in out["reason"]


def test_malformed_authored_entries_are_dropped_not_trusted() -> None:
    cfg = sm.parse_staff_mailboxes({"mailboxes": ["ok@firm.example", "bad/@firm.example", 7]})
    assert cfg.mailboxes == ("ok@firm.example",)
    assert len(cfg.rejected) == 2
