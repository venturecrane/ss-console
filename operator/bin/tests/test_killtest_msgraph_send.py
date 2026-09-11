"""The kill test's own guardrails (ss#2499).

A script that deliberately puts an unaudited message into a real mailbox is worth
exactly as much as its fence. These tests pin the fence and the shape of what it
writes, in BOTH modes; the run itself is a Captain act and is not performed here.

Plant mode is fenced identically to send mode on purpose. It transmits nothing,
which makes it read as harmless, and that reading is the thing these tests exist
to refuse: an item in a firm's Sent Items that the firm did not send is a worse
artifact than a message, because that folder is the firm's own record.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_BIN = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("killtest_msgraph_send", _BIN / "killtest-msgraph-send.py")
kill = importlib.util.module_from_spec(_spec)
sys.modules["killtest_msgraph_send"] = kill
_spec.loader.exec_module(kill)


def test_the_paying_firms_seat_is_refused():
    """A client's mailbox is the client's. Posting a message into it to see
    whether our watchdog barks would leave an unexplained artifact in a firm's
    own correspondence -- and SMD has no UI access there, so the firm would be
    the only party who could see it."""
    with pytest.raises(kill.KillTestRefused) as exc:
        kill.guard("ashton-price")
    assert "sandbox" in str(exc.value)


def test_an_unknown_seat_is_refused():
    with pytest.raises(kill.KillTestRefused):
        kill.guard("some-seat-nobody-authored")


def test_the_sandbox_seat_is_allowed():
    """Law 12 control: a fence that refuses everything proves nothing."""
    assert kill.guard("smd-staging")


def test_the_fence_is_not_a_flag(capsys):
    """--confirm gates the transmit; it does not widen WHERE it may transmit. A
    flag is a thing an operator in a hurry passes.

    Same reason as the plant twin below for reading stderr: this seat exits 2 on
    a missing credential too, so the exit code alone does not say the fence is
    what stopped it."""
    assert kill.main(["--seat", "ashton-price", "--confirm"]) == 2
    assert "is not a sandbox seat" in capsys.readouterr().err


def test_a_dry_run_transmits_nothing(monkeypatch, capsys, authored_staging):
    def explode(*_a, **_k):  # pragma: no cover - must never be reached
        raise AssertionError("a dry run reached the network")

    monkeypatch.setattr(kill, "mint_token", explode)
    monkeypatch.setattr(kill, "transmit", explode)
    assert kill.main(["--seat", "smd-staging"]) == 0
    assert "DRY RUN" in capsys.readouterr().out


def test_the_message_carries_no_audit_header():
    """The whole experiment. A stamped message would be matched by the header and
    the run would come back clean -- a kill test that always passes."""
    payload = kill.killtest_message(kill.killtest_subject())
    assert "internetMessageHeaders" not in payload["message"]
    # Every key the message carries, so a header cannot arrive under another
    # spelling. (The body PROSE names the header; that is explanation for a human
    # who finds this in a mailbox, and explanation is not a header.)
    assert set(payload["message"]) == {"subject", "body", "toRecipients"}


def test_the_subject_carries_the_marker_and_a_creation_stamp():
    """Same marker as the AgentMail kill test of 2026-08-13, so the two read as
    one control. The stamp is the probe-artifact contract: whoever finds this in
    a mailbox must be able to tell it is a probe and when it was made."""
    subject = kill.killtest_subject(datetime(2026, 8, 21, 14, 30, tzinfo=timezone.utc))
    assert subject.startswith("[UNAUDITED-KILLTEST-2258] 2026-08-21T14:30Z")


def test_the_message_is_addressed_to_smd_and_never_a_client():
    """A kill test that reaches a client is not a test."""
    payload = kill.killtest_message("s")
    recipients = [r["emailAddress"]["address"] for r in payload["message"]["toRecipients"]]
    assert recipients == ["team@smd.services"]


# --------------------------------------------------------------------------
# plant mode
# --------------------------------------------------------------------------


_STAGING_MAILBOX = "operator@smdopslab.onmicrosoft.com"
_STAGING_CONFIG = {
    "mailbox": _STAGING_MAILBOX,
    "tenant_id": "f11d2887-b7f2-4464-a9c6-d4db2166b43c",
    "client_id": "authored-from-customer-yaml",
}


@pytest.fixture
def authored_staging(monkeypatch):
    """Pin ``_seat_config`` for the tests that exercise ``main()``.

    Those tests assert argument handling, the dry-run contract and the plant
    path. None of them is about what ``smd-staging`` currently authors -- yet
    reading the live customer.yaml made them depend on it, and parking that
    seat's msgraph connector on 2026-08-21 (the M365 licences are not being
    retained) turned six unrelated unit tests red. A unit test of this tool
    should not move when an operator parks a connector.

    The yaml-reading path keeps its own direct coverage: ``guard`` is tested
    against the real allowlist above, and ``read_credential`` is tested against
    ``_STAGING_CONFIG`` below.
    """
    monkeypatch.setattr(kill, "_seat_config", lambda slug: dict(_STAGING_CONFIG))


class _Created:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class FakeGraph:
    """Replays one create response and RECORDS the request, so the URL, method
    and body are asserted from the wire rather than trusted."""

    def __init__(self, payload):
        self._payload = payload
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(
            (
                request.get_method(),
                request.full_url,
                json.loads(request.data.decode()),
            )
        )
        return _Created(self._payload)


def _clear_graph_env(monkeypatch):
    for name in (
        "MSGRAPH_CLIENT_ID",
        "MSGRAPH_CLIENT_SECRET",
        "MSGRAPH_CLIENT_ID__SMD_STAGING",
        "MSGRAPH_CLIENT_SECRET__SMD_STAGING",
    ):
        monkeypatch.delenv(name, raising=False)


def test_plant_is_not_the_default_mode(monkeypatch, capsys, authored_staging):
    """The original invocation keeps its original meaning. A mode flag that
    changes what an unchanged command line does is a trap, not a feature."""
    monkeypatch.setattr(kill, "mint_token", lambda *_a, **_k: "tok")
    monkeypatch.setattr(kill, "plant", lambda *_a, **_k: {"id": "should-not-happen"})
    reached = []
    monkeypatch.setattr(kill, "send_credential", lambda _s: reached.append(1) or ("i", "s"))
    monkeypatch.setattr(kill, "transmit", lambda *_a, **_k: None)
    assert kill.main(["--seat", "smd-staging", "--confirm"]) == 0
    assert reached == [1], "an omitted --mode must still take the send path"
    assert "mode=send" in capsys.readouterr().out


def test_both_modes_parse_and_an_invented_mode_does_not(capsys, authored_staging):
    """A typo'd mode must not fall through to a default that transmits."""
    assert kill.main(["--seat", "smd-staging", "--mode", "plant"]) == 0
    assert "mode plant" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        kill.main(["--seat", "smd-staging", "--mode", "planted"])


def test_the_paying_firms_seat_is_refused_in_plant_mode_too(capsys):
    """Plant transmits nothing, which makes it read as harmless. It is not: it
    leaves an item in a folder the firm reads as its own record of what it sent.
    The fence is on the SEAT, not on whether the wire carried anything.

    Asserts WHICH gate fired, not just the exit code. With the fence removed this
    run still exits 2 -- on a missing credential, several steps later and only by
    luck -- so an exit-code-only assertion would go on passing with no fence at
    all. (Falsifier 2026-08-21: deleting the guard call for plant mode left the
    code-only version green and this version red.)"""
    assert kill.main(["--seat", "ashton-price", "--mode", "plant", "--confirm"]) == 2
    assert "is not a sandbox seat" in capsys.readouterr().err


def test_a_plant_dry_run_reaches_no_network(monkeypatch, capsys, authored_staging):
    def explode(*_a, **_k):  # pragma: no cover - must never be reached
        raise AssertionError("a dry run reached the network")

    monkeypatch.setattr(kill, "mint_token", explode)
    monkeypatch.setattr(kill, "plant", explode)
    assert kill.main(["--seat", "smd-staging", "--mode", "plant"]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "transmitting nothing" in out


def test_plant_builds_the_documented_graph_create_request():
    """POST /users/{mailbox}/mailFolders/sentitems/messages, per Graph v1.0
    'Create Message' in a mail folder. Asserted from the recorded request because
    a vendor shape that is assumed rather than pinned is the shape that drifts."""
    http = FakeGraph({"id": "AAMk-planted", "internetMessageId": "<planted@x>"})
    subject = kill.killtest_subject(mode=kill.MODE_PLANT)
    kill.plant(
        _STAGING_MAILBOX,
        "tok",
        kill.planted_message(subject, _STAGING_MAILBOX),
        opener=http,
    )
    method, url, body = http.requests[-1]
    assert method == "POST"
    assert url == (f"https://graph.microsoft.com/v1.0/users/{_STAGING_MAILBOX}/mailFolders/sentitems/messages")
    # Every property the documented create needs, and no envelope around them:
    # this is a message resource posted to a folder, not a sendMail payload.
    assert set(body) == {
        "subject",
        "body",
        "toRecipients",
        "isDraft",
        "sentDateTime",
    }
    assert body["subject"] == subject
    assert body["isDraft"] is False
    assert body["body"]["contentType"] == "Text"


def test_the_planted_item_carries_no_audit_header():
    """Same experiment as send mode. A stamped item would be matched by the
    header and the run would come back clean -- a kill test that always passes."""
    payload = kill.planted_message("s", _STAGING_MAILBOX)
    assert "internetMessageHeaders" not in payload


def test_the_planted_item_carries_a_sent_time():
    """Not cosmetic. The reconciler pages Sent Items ordered by sentDateTime and
    stops at the --days boundary (reconcile-sends.py:446, :454), so an item with
    no sent time sorts past every window and is never reached -- the plant would
    be invisible and the clean run would be misread as the control working."""
    stamped = kill.planted_message("s", _STAGING_MAILBOX, datetime(2026, 8, 21, 14, 30, 5, tzinfo=timezone.utc))
    assert stamped["sentDateTime"] == "2026-08-21T14:30:05Z"


def test_the_planted_item_is_addressed_to_the_seats_own_mailbox():
    """It is never transmitted, so a recipient outside the tenant would be a
    fiction printed in a folder that staff read as a record of real sends."""
    payload = kill.planted_message("s", _STAGING_MAILBOX)
    addresses = [r["emailAddress"]["address"] for r in payload["toRecipients"]]
    assert addresses == [_STAGING_MAILBOX]


def test_the_planted_subject_says_which_mode_made_it():
    subject = kill.killtest_subject(datetime(2026, 8, 21, 14, 30, tzinfo=timezone.utc), mode=kill.MODE_PLANT)
    assert subject.startswith("[UNAUDITED-KILLTEST-2258] 2026-08-21T14:30Z mode=plant")


def test_a_created_response_without_an_id_is_an_error():
    """A 201 is not a success. Without an id there is nothing exact to baseline
    and nothing to go and delete, so an unnamed item in a mailbox would be
    reported as a passing test."""
    http = FakeGraph({"internetMessageId": "<no-graph-id@x>"})
    with pytest.raises(kill.KillTestRefused) as exc:
        kill.plant(_STAGING_MAILBOX, "tok", {"subject": "s"}, opener=http)
    assert "no message id" in str(exc.value)


def test_a_created_response_with_an_id_is_returned_whole():
    """Law 12 control: an error path that fires on everything proves nothing."""
    http = FakeGraph({"id": "AAMk-planted", "internetMessageId": "<planted@x>"})
    created = kill.plant(_STAGING_MAILBOX, "tok", {"subject": "s"}, opener=http)
    assert created["id"] == "AAMk-planted"
    assert created["internetMessageId"] == "<planted@x>"


def test_plant_prefers_the_seats_own_read_credentials(monkeypatch):
    _clear_graph_env(monkeypatch)
    monkeypatch.setenv("MSGRAPH_CLIENT_ID__SMD_STAGING", "per-seat-id")
    monkeypatch.setenv("MSGRAPH_CLIENT_SECRET__SMD_STAGING", "per-seat-secret")
    monkeypatch.setenv("MSGRAPH_CLIENT_ID", "shared-id")
    monkeypatch.setenv("MSGRAPH_CLIENT_SECRET", "shared-secret")
    client_id, secret = kill.read_credential("smd-staging", _STAGING_CONFIG)
    assert (client_id, secret) == ("per-seat-id", "per-seat-secret")


def test_plant_falls_back_to_the_unsuffixed_pair(monkeypatch):
    """How the sandbox's are staged in Infisical today. Without this the plant
    refuses on a seat the reconciler itself authenticates against fine."""
    _clear_graph_env(monkeypatch)
    monkeypatch.setenv("MSGRAPH_CLIENT_ID", "shared-id")
    monkeypatch.setenv("MSGRAPH_CLIENT_SECRET", "shared-secret")
    assert kill.read_credential("smd-staging", _STAGING_CONFIG) == (
        "shared-id",
        "shared-secret",
    )


def test_the_authored_client_id_is_the_last_resort_for_the_id(monkeypatch):
    """The client id is a public identifier the seat already authors. Falling
    back to it means plant talks to the same app reconcile-sends.py does."""
    _clear_graph_env(monkeypatch)
    monkeypatch.setenv("MSGRAPH_CLIENT_SECRET__SMD_STAGING", "per-seat-secret")
    client_id, _secret = kill.read_credential("smd-staging", _STAGING_CONFIG)
    assert client_id == "authored-from-customer-yaml"


def test_there_is_no_fallback_for_the_secret(monkeypatch):
    """A shared secret picked up by accident would authenticate as another app
    against another mailbox. A refusal is the better outcome."""
    _clear_graph_env(monkeypatch)
    monkeypatch.setenv("MSGRAPH_CLIENT_ID", "shared-id")
    with pytest.raises(kill.KillTestRefused) as exc:
        kill.read_credential("smd-staging", _STAGING_CONFIG)
    assert "MSGRAPH_CLIENT_SECRET" in str(exc.value)


def test_plant_never_asks_for_the_send_credential(monkeypatch, authored_staging):
    """The whole reason plant exists is that smd-staging has no SEND app
    (ss#2467). If it reached for one it would refuse on the seat it was built
    for, and the falsifier would still have nowhere to run."""

    def explode(*_a, **_k):  # pragma: no cover - must never be reached
        raise AssertionError("plant mode reached for the SEND credential")

    monkeypatch.setattr(kill, "send_credential", explode)
    monkeypatch.setattr(kill, "mint_token", lambda *_a, **_k: "tok")
    monkeypatch.setattr(kill, "plant", lambda *_a, **_k: {"id": "i", "internetMessageId": "<m>"})
    _clear_graph_env(monkeypatch)
    monkeypatch.setenv("MSGRAPH_CLIENT_SECRET", "shared-secret")
    assert kill.main(["--seat", "smd-staging", "--mode", "plant", "--confirm"]) == 0


def test_plant_prints_both_ids_so_the_baseline_entry_is_exact(monkeypatch, capsys, authored_staging):
    """Send mode cannot do this -- Graph answers sendMail with 202 and no body.
    Plant gets the created message back, so the id is printed rather than hunted
    for by subject in a mailbox."""
    monkeypatch.setattr(kill, "mint_token", lambda *_a, **_k: "tok")
    monkeypatch.setattr(
        kill,
        "plant",
        lambda *_a, **_k: {
            "id": "AAMk-planted",
            "internetMessageId": "<planted@x>",
            "sentDateTime": "2026-08-21T14:30:05Z",
            "isDraft": False,
        },
    )
    _clear_graph_env(monkeypatch)
    monkeypatch.setenv("MSGRAPH_CLIENT_SECRET", "shared-secret")
    assert kill.main(["--seat", "smd-staging", "--mode", "plant", "--confirm"]) == 0
    out = capsys.readouterr().out
    assert "AAMk-planted" in out
    assert "<planted@x>" in out
    assert "2026-08-21T14:30:05Z" in out
    assert "Nothing was transmitted." in out
