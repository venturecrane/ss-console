"""msgraph_discard_draft: delete the relay's working draft, and nothing else.

The relay transmits each reply as a new message, so the draft the agent's
create_draft made stayed in the operator mailbox's Drafts folder (ashton-price,
50+ by 2026-10-12). This verb deletes one such draft. Each test pins a guard
and fails without it.

Run::

    cd operator && python3 -m pytest workspace_broker/tests/test_msgraph_discard_draft.py -q
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import verbs
from workspace_broker.msgraph_drafts import ABSENT, DISCARDED, discard_draft
from workspace_broker.msgraph_ops import MsGraphOps, MsGraphRefused, MsGraphTransportError
from workspace_broker.server import Broker

MAILBOX = "operator@opslab.example"
DRAFT = "AAMkDRAFT0001="
YAML = f"""
connectors:
  Email:
    adapter: msgraph
    enabled: true
    msgraph_auth:
      mailbox: {MAILBOX}
scope:
  admins:
    - scott@smd.services
"""


class _Response:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> bytes:
        return self._text.encode()

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False


class FakeMailbox:
    """One message, a draft unless told otherwise; records every Graph call."""

    def __init__(self, *, is_draft: bool = True, exists: bool = True) -> None:
        self.is_draft = is_draft
        self.exists = exists
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, request, timeout=None):
        url = request.full_url
        if url.endswith("/token"):
            form = urllib.parse.parse_qs((request.data or b"").decode())
            client_id = (form.get("client_id") or ["?"])[0]
            return _Response(json.dumps({"access_token": f"tok-{client_id}", "expires_in": 3600}))
        self.calls.append((request.method, url, request.get_header("Authorization") or ""))
        if not self.exists:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
        if request.method == "GET":
            return _Response(json.dumps({"id": DRAFT, "isDraft": self.is_draft}))
        if request.method == "DELETE":
            self.exists = False
            return _Response("")
        raise AssertionError(f"unexpected {request.method} {url}")

    def deletes(self) -> list[str]:
        return [u for m, u, _a in self.calls if m == "DELETE"]


def _ops(tmp_path: Path, http: FakeMailbox, *, with_read: bool = True) -> MsGraphOps:
    customer = tmp_path / "customer.yaml"
    customer.write_text(YAML)
    send = tmp_path / "msgraph.json"
    send.write_text(json.dumps({"tenant_id": "tid", "client_id": "cid-send", "client_secret": "s"}))
    read = tmp_path / "msgraph-read.json"
    read.write_text(json.dumps({"tenant_id": "tid", "client_id": "cid-read", "client_secret": "r"}))
    return MsGraphOps(send, customer, read_credential_path=read if with_read else None, opener=http)


def test_a_draft_is_deleted_on_the_read_app(tmp_path: Path) -> None:
    http = FakeMailbox()
    assert discard_draft(_ops(tmp_path, http), DRAFT) == DISCARDED
    assert http.deletes() == [f"https://graph.microsoft.com/v1.0/users/{MAILBOX}/messages/{DRAFT}"]
    assert all(auth == "Bearer tok-cid-read" for _m, _u, auth in http.calls)


def test_a_message_that_is_not_a_draft_is_never_deleted(tmp_path: Path) -> None:
    """FALSIFIER: drop the isDraft check and a sent or received message is deleted."""
    http = FakeMailbox(is_draft=False)
    with pytest.raises(MsGraphRefused, match="not a draft"):
        discard_draft(_ops(tmp_path, http), DRAFT)
    assert http.deletes() == []


def test_a_draft_already_gone_is_absent(tmp_path: Path) -> None:
    http = FakeMailbox(exists=False)
    assert discard_draft(_ops(tmp_path, http), DRAFT) == ABSENT


@pytest.mark.parametrize("bad", ["", "AAMk/../inbox", "AAMk?$top=1"])
def test_an_id_that_could_restructure_the_path_is_refused(tmp_path: Path, bad: str) -> None:
    http = FakeMailbox()
    with pytest.raises(MsGraphRefused):
        discard_draft(_ops(tmp_path, http), bad)
    assert http.calls == []


def test_no_read_credential_fails_closed(tmp_path: Path) -> None:
    http = FakeMailbox()
    with pytest.raises(MsGraphTransportError):
        discard_draft(_ops(tmp_path, http, with_read=False), DRAFT)
    assert http.calls == []


def test_the_verb_is_gateway_only_and_dispatches(tmp_path: Path) -> None:
    http = FakeMailbox()
    broker = Broker.__new__(Broker)
    broker.gateway_pid = 4242
    broker.agent_uid = 1001
    broker.msgraph = _ops(tmp_path, http)
    request = {"action": "msgraph_discard_draft", "payload": {"draft_id": DRAFT}}
    with pytest.raises(PermissionError):
        verbs.dispatch(broker, request, 9999, 1001)
    assert http.calls == []
    assert verbs.dispatch(broker, request, 4242, 2002) == {"ok": True, "outcome": DISCARDED}
