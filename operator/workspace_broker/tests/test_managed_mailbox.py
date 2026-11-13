"""Managed-mailbox authorization invariants for the Workspace broker.

The broker holds the DWD service-account credential and can impersonate any user
in the Workspace, so it must independently validate the requested impersonation
subject (`mailbox`) and send-as identity (`from`) against its own read of
authored `customer.yaml` — never trusting the gateway. These tests pin that
fail-closed boundary without requiring the google-* libraries (the subject/from
checks all run before the lazy google import).
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import workspace_broker.operations as ops_mod
from workspace_broker.google_auth import authored_identities, credentials
from workspace_broker.operations import WorkspaceOperations

MANAGED = "smdurgan@smdurgan.com"
SEND_AS = ["scott@smd.services", "team@smd.services", "smdurgan@smdurgan.com"]


def _customer(tmp_path: Path, *, managed: bool = True) -> Path:
    google_auth: dict = {
        "mode": "dwd",
        "subject": "crane@smd.services",
        "scopes": ["https://www.googleapis.com/auth/gmail.modify"],
    }
    if managed:
        google_auth["managed_mailboxes"] = [{"address": MANAGED, "send_as": SEND_AS}]
    path = tmp_path / "customer.yaml"
    path.write_text(yaml.safe_dump({"schema_version": 1, "google_auth": google_auth}))
    return path


def _ops(tmp_path: Path, **kwargs) -> WorkspaceOperations:
    return WorkspaceOperations(tmp_path / "google.json", _customer(tmp_path, **kwargs))


def _decode_raw(raw: str) -> str:
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()


def test_authored_identities_includes_default_and_managed(tmp_path: Path) -> None:
    config = yaml.safe_load(_customer(tmp_path).read_text())["google_auth"]
    default, allowed, send_as = authored_identities(config)
    assert default == "crane@smd.services"
    assert allowed == {"crane@smd.services", MANAGED}
    assert send_as[MANAGED] == set(SEND_AS)


def test_authored_identities_no_managed_block(tmp_path: Path) -> None:
    config = yaml.safe_load(_customer(tmp_path, managed=False).read_text())["google_auth"]
    default, allowed, send_as = authored_identities(config)
    assert allowed == {"crane@smd.services"}
    assert send_as == {}


def test_credentials_reject_unauthored_subject(tmp_path: Path) -> None:
    cred = tmp_path / "google.json"
    cred.write_text(json.dumps({"type": "service_account", "client_email": "x@y.iam"}))
    with pytest.raises(RuntimeError, match="not an authored impersonation target"):
        credentials(cred, _customer(tmp_path), subject="intruder@evil.com")


def test_validate_from_allows_authored_send_as(tmp_path: Path) -> None:
    ops = _ops(tmp_path)
    ops._validate_from(MANAGED, "team@smd.services")  # no raise
    ops._validate_from(MANAGED, "")  # empty is a no-op


def test_validate_from_rejects_unauthored_identity(tmp_path: Path) -> None:
    ops = _ops(tmp_path)
    with pytest.raises(RuntimeError, match="not an authored send-as"):
        ops._validate_from(MANAGED, "intruder@evil.com")


def test_validate_from_rejects_send_as_for_wrong_mailbox(tmp_path: Path) -> None:
    # team@ is a send-as of the managed mailbox, never of the default crane@ box.
    ops = _ops(tmp_path)
    with pytest.raises(RuntimeError, match="not an authored send-as"):
        ops._validate_from("", "team@smd.services")


def test_gmail_search_threads_mailbox_to_subject(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}

    def fake_service(api, version, cred, customer, subject=""):
        captured["subject"] = subject
        return MagicMock()

    monkeypatch.setattr(ops_mod, "service", fake_service)
    _ops(tmp_path).gmail_search({"query": "is:unread", "mailbox": MANAGED})
    assert captured["subject"] == MANAGED


def test_gmail_search_default_subject_when_no_mailbox(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}

    def fake_service(api, version, cred, customer, subject=""):
        captured["subject"] = subject
        return MagicMock()

    monkeypatch.setattr(ops_mod, "service", fake_service)
    _ops(tmp_path).gmail_search({"query": "is:unread"})
    assert captured["subject"] == ""  # broker resolves "" → authored default


def test_gmail_search_without_query_lists_all(tmp_path: Path, monkeypatch) -> None:
    # Regression: a bare "list/read the mailbox" request (e.g. "review the receipts
    # mailbox") carries no `query`. The broker must default q to "" — Gmail's
    # messages.list returns recent messages on an empty q — not KeyError on the
    # missing key. The KeyError previously surfaced to the agent as
    # `BrokerError: 'query'`, which it then confabulated into a fake result.
    svc = MagicMock()

    def fake_service(api, version, cred, customer, subject=""):
        return svc

    monkeypatch.setattr(ops_mod, "service", fake_service)
    # No "query" key present — must not raise.
    _ops(tmp_path).gmail_search({"mailbox": MANAGED})
    list_call = svc.users.return_value.messages.return_value.list
    assert list_call.call_args.kwargs["q"] == ""


def test_create_draft_sets_from_and_threads_mailbox(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}
    built = MagicMock()

    def fake_service(api, version, cred, customer, subject=""):
        captured["subject"] = subject
        return built

    monkeypatch.setattr(ops_mod, "service", fake_service)
    _ops(tmp_path).gmail_create_draft(
        {
            "to": "client@example.com",
            "subject": "Re: scheduling",
            "body": "Confirming Tuesday.",
            "mailbox": MANAGED,
            "from": "team@smd.services",
        }
    )
    assert captured["subject"] == MANAGED
    create_kwargs = built.users.return_value.drafts.return_value.create.call_args.kwargs
    decoded = _decode_raw(create_kwargs["body"]["message"]["raw"])
    assert "From: team@smd.services" in decoded


def test_create_draft_rejects_unauthored_from(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="not an authored send-as"):
        _ops(tmp_path).gmail_create_draft(
            {
                "to": "client@example.com",
                "subject": "Re: scheduling",
                "body": "x",
                "mailbox": MANAGED,
                "from": "intruder@evil.com",
            }
        )


def test_supported_operations_handshake_lists_gmail(tmp_path: Path) -> None:
    ops = _ops(tmp_path)
    supported = ops.supported_operations()
    assert "workspace_gmail_create_draft" in supported
    assert "workspace_gmail_search" in supported
    assert supported == sorted(supported)
    # Wave A does not add send — guards against the doc-vs-code drift the plan warns of.
    assert "workspace_gmail_send" not in supported


# ---- Slice 1 (2026-11-13): whole-thread trash and search paging -------------


def test_trash_thread_refuses_an_unauthored_mailbox(tmp_path: Path, monkeypatch) -> None:
    # The subject check runs in credentials(), before any Google call, so an
    # unauthored mailbox can never have a thread trashed.
    cred = tmp_path / "google.json"
    cred.write_text(json.dumps({"type": "service_account", "client_email": "x@y.iam"}))

    def fake_service(api, version, cred_path, customer, subject=""):
        credentials(cred_path, customer, subject=subject)
        return MagicMock()

    monkeypatch.setattr(ops_mod, "service", fake_service)
    with pytest.raises(RuntimeError, match="not an authored impersonation target"):
        _ops(tmp_path).gmail_trash_thread({"thread_id": "t1", "mailbox": "intruder@evil.com"})


def test_trash_thread_trashes_the_whole_thread(tmp_path: Path, monkeypatch) -> None:
    svc = MagicMock()
    captured: dict = {}

    def fake_service(api, version, cred, customer, subject=""):
        captured["subject"] = subject
        return svc

    trash = svc.users.return_value.threads.return_value.trash
    trash.return_value.execute.return_value = {"id": "t1", "messages": [{"id": "a"}, {"id": "b"}]}
    monkeypatch.setattr(ops_mod, "service", fake_service)
    out = _ops(tmp_path).gmail_trash_thread({"thread_id": "t1", "mailbox": MANAGED})
    assert captured["subject"] == MANAGED
    assert trash.call_args.kwargs == {"userId": "me", "id": "t1"}
    assert out == {"thread_id": "t1", "trashed": True, "messages": 2}
    # Never a per-message call: a thread is trashed whole or not at all.
    assert not svc.users.return_value.messages.return_value.trash.called


def test_trash_thread_requires_a_thread_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="thread_id"):
        _ops(tmp_path).gmail_trash_thread({"mailbox": MANAGED})


def test_trash_thread_is_in_the_reviewed_surface_and_send_is_not(tmp_path: Path) -> None:
    supported = _ops(tmp_path).supported_operations()
    assert "workspace_gmail_trash_thread" in supported
    assert "workspace_gmail_send" not in supported


def test_gmail_search_pages_return_every_page(tmp_path: Path, monkeypatch) -> None:
    pages = {
        "": {"messages": [{"id": "1", "threadId": "a"}], "nextPageToken": "p2"},
        "p2": {"messages": [{"id": "2", "threadId": "b"}], "nextPageToken": "p3"},
        "p3": {"messages": [{"id": "3", "threadId": "c"}]},
    }
    seen: list[str] = []
    svc = MagicMock()

    def fake_list(**kwargs):
        token = kwargs.get("pageToken", "")
        seen.append(token)
        call = MagicMock()
        call.execute.return_value = pages[token]
        return call

    svc.users.return_value.messages.return_value.list.side_effect = fake_list
    monkeypatch.setattr(ops_mod, "service", lambda *a, **k: svc)
    ops = _ops(tmp_path)
    token, ids = "", []
    while True:
        page = ops.gmail_search({"query": "in:inbox", "mailbox": MANAGED, "page_token": token})
        ids += [m["id"] for m in page["messages"]]
        token = page["next_page_token"]
        if not token:
            break
    assert ids == ["1", "2", "3"]
    assert seen == ["", "p2", "p3"]


def test_gmail_search_without_page_token_keeps_the_bare_list(tmp_path: Path, monkeypatch) -> None:
    svc = MagicMock()
    listed = svc.users.return_value.messages.return_value.list
    listed.return_value.execute.return_value = {"messages": [{"id": "1"}], "nextPageToken": "p2"}
    monkeypatch.setattr(ops_mod, "service", lambda *a, **k: svc)
    assert _ops(tmp_path).gmail_search({"query": "x"}) == [{"id": "1"}]
    assert "pageToken" not in listed.call_args.kwargs
