"""Tests for rehearse-card.py.

The guards are the point. This harness sends real mail to a real seat as a real
admin, so the ways it can be WRONG are: speaking as someone the seat does not
trust (which measures the refusal path and reads as a product defect), speaking
a command the card gates on a real-world event, or recording silence as an
answer. Each has a test.
"""

from __future__ import annotations

import importlib.util
import io
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

_BIN = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("rehearse_card", _BIN / "rehearse-card.py")
rc = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["rehearse_card"] = rc
_spec.loader.exec_module(rc)


def test_locked_stages_are_skipped() -> None:
    """A locked stage names a real-world unlock (the principal's own drafting
    test). Rehearsing it would assert a readiness the firm has not granted."""
    card = {
        "stages": [
            {"id": "setup", "commands": [{"say": "a"}, {"say": "b"}]},
            {"id": "work", "locked": "unlocked by the firm's own-file test", "commands": [{"say": "c"}]},
        ]
    }
    out = rc.unlocked_commands(card)
    assert [c["say"] for c in out] == ["a", "b"]


def test_unlocked_work_stage_is_included() -> None:
    """The pilot deliberately leaves `work` unlocked — it is where drafting is
    proven before the client's test unlocks it there. A harness that always
    skipped `work` would silently never rehearse the drafting pass."""
    card = {"stages": [{"id": "work", "commands": [{"say": "draft it"}]}]}
    assert [c["say"] for c in rc.unlocked_commands(card)] == ["draft it"]


def test_real_cards_differ_exactly_as_authored() -> None:
    """pilot 18 / ashton-price 17 is a deliberate per-seat divergence, recorded
    in the pilot card's own header. If these ever match, one of them changed."""
    pilot = yaml.safe_load((rc.CUSTOMERS / "pilot-smokeball" / "initiation-card.yaml").read_text())
    ap = yaml.safe_load((rc.CUSTOMERS / "ashton-price" / "initiation-card.yaml").read_text())
    assert len(rc.unlocked_commands(pilot)) == 18
    assert len(rc.unlocked_commands(ap)) == 17


def test_seat_inbox_from_the_authored_connector() -> None:
    cfg = {
        "connectors": {
            "Email": {
                "enabled": True,
                "adapter": "agentmail",
                "webhook_url": "https://hermes-pilot-smokeball.fly.dev/webhooks/agentmail",
            }
        }
    }
    assert rc.seat_inbox(cfg) == "pilot-smokeball@agentmail.to"


def test_seat_with_no_channel_is_refused() -> None:
    """ashton-price today: no Email connector at all. 'Nothing to rehearse' must
    be a loud exit, not an empty transcript that reads like a clean run."""
    with pytest.raises(SystemExit) as e:
        rc.seat_inbox({"connectors": {}})
    assert e.value.code == 2


def test_disabled_connector_is_refused() -> None:
    with pytest.raises(SystemExit):
        rc.seat_inbox({"connectors": {"Email": {"enabled": False, "adapter": "agentmail"}}})


def test_quote_trail_is_stripped() -> None:
    """The transcript must hold the Operator's own words; a quoted trail would
    put the command back in the record as if the seat had said it."""
    body = "Here is my answer.\n\nOn Thu, Aug 13, 2026 at 12:25 AM UTC someone wrote:\n> the original"
    assert rc.strip_quote_trail(body) == "Here is my answer."


def test_quote_trail_stripper_leaves_clean_bodies_alone() -> None:
    assert rc.strip_quote_trail("just an answer") == "just an answer"


@pytest.mark.parametrize("slug", ["pilot-smokeball", "ashton-price"])
def test_real_cards_carry_expected_and_falsifier(slug: str) -> None:
    """The transcript pairs each reply with these two fields so a second reader
    can judge without re-deriving them. A command missing either would produce a
    transcript entry nobody can grade."""
    card = yaml.safe_load((rc.CUSTOMERS / slug / "initiation-card.yaml").read_text())
    for cmd in rc.unlocked_commands(card):
        assert str(cmd.get("expected", "")).strip(), f"{slug}: {cmd.get('say')!r} has no expected"
        assert str(cmd.get("falsifier", "")).strip(), f"{slug}: {cmd.get('say')!r} has no falsifier"


def test_harness_does_not_grade() -> None:
    """Deliberate absence. The first hand rehearsal was scored by the agent that
    wrote the messages and was wrong at least once; an automated grader here
    would industrialise that error. If a `grade`/`verdict` helper ever appears,
    this test should be the argument against it."""
    assert not [n for n in dir(rc) if n.lower() in {"grade", "judge", "verdict", "score"}]


# ---------------------------------------------------------------------------
# Microsoft Graph transport (ADR 0078).
#
# The AgentMail path proves itself against a seat we own. This one has to reach
# a mailbox in the CLIENT's tenant, so its failure modes are different: sending
# from an address with no inbox we can read, and reading a folder that keeps
# every prior rehearsal's reply forever. Both get a test.
# ---------------------------------------------------------------------------

_MSGRAPH_CFG = {
    "connectors": {
        "Email": {
            "enabled": True,
            "adapter": "msgraph",
            "backend": "mcp:msgraph-mail",
            "msgraph_auth": {
                "tenant_id": "tenant-guid",
                "client_id": "client-guid",
                "mailbox": "operator@example.test",
                "secret_ref": "fly-secret:MSGRAPH_CLIENT_SECRET",
            },
        }
    }
}

_FLOOR = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)
_SUBJECT = "Card 3 - matter-inbox-router answers the request"


class _FakeResponse:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body.encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


class _StubToken:
    def value(self) -> str:
        return "stub-bearer"


class _ExplodingToken:
    """A token that fails if anyone asks for it, so a test can assert that a
    given path never reached Graph at all."""

    def value(self) -> str:
        raise AssertionError("Graph must not be polled on this path")


def _sent_message(to: str, subject: str, stamp: str, body: str = "the answer") -> dict:
    return {
        "id": "msg-1",
        "subject": subject,
        "sentDateTime": stamp,
        "toRecipients": [{"emailAddress": {"address": to}}],
        "body": {"contentType": "text", "content": body},
    }


def test_msgraph_seat_address_is_the_authored_mailbox() -> None:
    """A msgraph seat's address is authored, not derived: the mailbox lives in
    the client's own tenant (ADR 0078) and has no hermes-<slug>.fly.dev host to
    parse it out of."""
    assert rc.email_adapter(_MSGRAPH_CFG) == "msgraph"
    assert rc.seat_inbox(_MSGRAPH_CFG) == "operator@example.test"


def test_msgraph_seat_without_a_mailbox_is_refused() -> None:
    cfg = {"connectors": {"Email": {"enabled": True, "adapter": "msgraph", "msgraph_auth": {}}}}
    with pytest.raises(SystemExit) as e:
        rc.seat_inbox(cfg)
    assert e.value.code == 2


def test_agentmail_derivation_is_untouched_by_the_second_transport() -> None:
    """The regression this whole change must not cause. Adding a transport that
    reads an authored address must not change how the AgentMail seat finds
    its own."""
    cfg = {
        "connectors": {
            "Email": {
                "enabled": True,
                "adapter": "agentmail",
                "webhook_url": "https://hermes-pilot-smokeball.fly.dev/webhooks/agentmail",
            }
        }
    }
    assert rc.email_adapter(cfg) == "agentmail"
    assert rc.seat_inbox(cfg) == "pilot-smokeball@agentmail.to"


def test_the_real_seats_resolve_as_authored() -> None:
    """Pinned against the two real customer.yaml files, because the whole point
    of reading the adapter from config is that config is what changes. If the
    paying client's channel moves, this fails here rather than at a rehearsal.

    The client's own mailbox address is read out of its config rather than
    written here: client identity belongs in operator/customers/, never in a
    test file (tests/client-identity-gate.test.ts)."""
    ap = yaml.safe_load((rc.CUSTOMERS / "ashton-price" / "customer.yaml").read_text())
    pilot = yaml.safe_load((rc.CUSTOMERS / "pilot-smokeball" / "customer.yaml").read_text())
    authored_mailbox = rc.email_connector(ap)["msgraph_auth"]["mailbox"]
    assert rc.email_adapter(ap) == "msgraph"
    assert rc.seat_inbox(ap) == authored_mailbox
    assert "@" in authored_mailbox and not authored_mailbox.endswith("@agentmail.to")
    assert rc.email_adapter(pilot) == "agentmail"
    assert rc.seat_inbox(pilot) == "pilot-smokeball@agentmail.to"


# ---- the per-seat secret name -------------------------------------------

_PROVISIONER = rc.REPO_ROOT / "operator" / "bin" / "provision-customer.sh"
_TR_PIPELINE = r"tr '\[:lower:\]-' '\[:upper:\]_' \| tr -cd 'A-Z0-9_'"


def test_slug_suffix_matches_the_provisioner_by_running_it() -> None:
    """The provisioner stages the client secret under a name it derives with a
    `tr` pipeline. If this tool derives a different name it reaches for a
    variable nobody set, on exactly the seat it exists to reach. So the
    pipeline is read out of the script and RUN, not paraphrased here: a change
    to either side fails this test."""
    found = re.search(_TR_PIPELINE, _PROVISIONER.read_text())
    assert found, "provision-customer.sh no longer derives the per-seat suffix as expected"
    pipeline = found.group(0).replace("\\", "")
    for slug in ("ashton-price", "pilot-smokeball", "a-b-c", "acme"):
        shell = subprocess.run(  # noqa: S603 - the test runs the provisioner's own tr pipeline over four literal slugs
            ["sh", "-c", f"printf '%s' '{slug}' | {pipeline}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert rc.slug_env_suffix(slug) == shell, slug


def test_hyphenated_slug_becomes_an_underscored_variable() -> None:
    assert rc.slug_env_suffix("ashton-price") == "ASHTON_PRICE"
    assert rc.msgraph_secret_env_names("ashton-price", _MSGRAPH_CFG["connectors"]["Email"]) == (
        "MSGRAPH_CLIENT_SECRET__ASHTON_PRICE",
        "MSGRAPH_CLIENT_SECRET",
    )


def test_secret_name_follows_the_authored_secret_ref() -> None:
    """The NAME is authored (msgraph_auth.secret_ref); only the VALUE is
    vaulted (ADR 0010). A seat that names a different Fly secret must be read
    from a differently named variable."""
    email = {"msgraph_auth": {"secret_ref": "fly-secret:FIRM_GRAPH_SECRET"}}
    assert rc.msgraph_secret_env_names("acme-co", email) == (
        "FIRM_GRAPH_SECRET__ACME_CO",
        "FIRM_GRAPH_SECRET",
    )


def test_unauthored_secret_ref_falls_back_to_the_default_name() -> None:
    assert rc.msgraph_secret_env_names("acme", {}) == (
        "MSGRAPH_CLIENT_SECRET__ACME",
        "MSGRAPH_CLIENT_SECRET",
    )


def test_the_paying_seat_names_the_variable_infisical_actually_holds() -> None:
    cfg = yaml.safe_load((rc.CUSTOMERS / "ashton-price" / "customer.yaml").read_text())
    per_seat, _ = rc.msgraph_secret_env_names("ashton-price", rc.email_connector(cfg))
    assert per_seat == "MSGRAPH_CLIENT_SECRET__ASHTON_PRICE"


# ---- environment, and what a failure is allowed to say -------------------


def test_missing_graph_secret_names_the_variable_and_leaks_no_others(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing credential must be diagnosable without dumping the environment.
    The message names the one variable and the recipe; every other secret in the
    process stays out of the transcript."""
    monkeypatch.setenv("RESEND_API_KEY", "re_live_should_not_appear")
    monkeypatch.setenv("AGENTMAIL_API_KEY", "am_should_not_appear")
    monkeypatch.setenv("SMOKEBALL_PROD_CLIENT_SECRET", "sb_should_not_appear")
    monkeypatch.delenv("MSGRAPH_CLIENT_SECRET__ASHTON_PRICE", raising=False)
    monkeypatch.delenv("MSGRAPH_CLIENT_SECRET", raising=False)

    with pytest.raises(SystemExit) as e:
        rc.msgraph_asker("ashton-price", _MSGRAPH_CFG, "scott@smd.services", "operator@example.test", 5)
    assert e.value.code == 2

    err = capsys.readouterr().err
    assert "MSGRAPH_CLIENT_SECRET__ASHTON_PRICE" in err
    assert "infisical run --env=prod --path=/ss" in err
    for leaked in ("re_live_should_not_appear", "am_should_not_appear", "sb_should_not_appear"):
        assert leaked not in err


def test_missing_resend_key_names_resend_not_agentmail(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        rc.msgraph_asker("ashton-price", _MSGRAPH_CFG, "scott@smd.services", "operator@example.test", 5)
    err = capsys.readouterr().err
    assert "RESEND_API_KEY" in err
    assert "AGENTMAIL" not in err


# ---- matching the reply --------------------------------------------------


def test_the_genuine_reply_is_matched() -> None:
    """The positive case, so the three rejections below are known to be
    rejecting something this function would otherwise accept."""
    msg = _sent_message("scott@smd.services", _SUBJECT, "2026-08-20T12:04:00Z")
    assert rc.msgraph_reply_matches(msg, "scott@smd.services", _SUBJECT, _FLOOR)


def test_a_prior_rehearsals_reply_is_not_this_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    """The silent false positive this transport could have had. Sent Items keeps
    every earlier rehearsal's answer forever, and one of them carries the same
    recipient and the same subject stem. Matching on those two alone would
    report an answer the seat did not just give."""
    stale = _sent_message("scott@smd.services", _SUBJECT, "2026-08-20T11:59:59Z")
    assert not rc.msgraph_reply_matches(stale, "scott@smd.services", _SUBJECT, _FLOOR)


def test_a_reply_sent_at_the_floor_is_not_counted() -> None:
    """Strictly after. The floor is stamped immediately before the send, so a
    message at that instant predates the ask."""
    msg = _sent_message("scott@smd.services", _SUBJECT, "2026-08-20T12:00:00Z")
    assert not rc.msgraph_reply_matches(msg, "scott@smd.services", _SUBJECT, _FLOOR)


def test_a_reply_to_someone_else_is_not_ours() -> None:
    msg = _sent_message("someone.else@firm.example", _SUBJECT, "2026-08-20T12:04:00Z")
    assert not rc.msgraph_reply_matches(msg, "scott@smd.services", _SUBJECT, _FLOOR)


def test_a_reply_about_another_command_is_not_ours() -> None:
    msg = _sent_message("scott@smd.services", "Card 9 - something else entirely", "2026-08-20T12:04:00Z")
    assert not rc.msgraph_reply_matches(msg, "scott@smd.services", _SUBJECT, _FLOOR)


def test_an_unreadable_timestamp_is_not_a_pass() -> None:
    """Silence is never a pass, and neither is a message we cannot date. Failing
    open here would let any Sent Items row stand in for the reply."""
    for stamp in ("", "not-a-date", None):
        msg = _sent_message("scott@smd.services", _SUBJECT, stamp)  # type: ignore[arg-type]
        assert not rc.msgraph_reply_matches(msg, "scott@smd.services", _SUBJECT, _FLOOR)


def test_recipient_match_ignores_case() -> None:
    msg = _sent_message("Scott@SMD.Services", _SUBJECT, "2026-08-20T12:04:00Z")
    assert rc.msgraph_reply_matches(msg, "scott@smd.services", _SUBJECT, _FLOOR)


def test_seven_digit_fractions_sort_later_not_earlier() -> None:
    """Why the timestamp is parsed and not string-compared. Graph emits up to
    seven fractional digits, and '.' sorts below 'Z', so a lexical compare calls
    a message later in the same second EARLIER and discards a genuine reply."""
    assert "2026-08-20T12:00:00.5000000Z" < "2026-08-20T12:00:00Z"
    later = rc.parse_stamp("2026-08-20T12:00:00.5000000Z")
    floor = rc.parse_stamp("2026-08-20T12:00:00Z")
    assert later is not None and floor is not None
    assert later > floor


# ---- the wire ------------------------------------------------------------


def test_resend_request_names_itself_to_cloudflare(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cloudflare fronts api.resend.com and answers urllib's default User-Agent
    with `403 error code: 1010` (observed live 2026-08-20). Without an explicit
    one, every ask on a msgraph seat is refused before Resend ever sees it."""
    seen: dict = {}

    def fake(req: object, timeout: int = 45) -> _FakeResponse:
        seen["url"] = req.full_url  # type: ignore[attr-defined]
        seen["headers"] = dict(req.headers)  # type: ignore[attr-defined]
        seen["body"] = json.loads(req.data.decode())  # type: ignore[attr-defined]
        return _FakeResponse(200, '{"id": "re_1"}')

    monkeypatch.setattr(rc.urllib.request, "urlopen", fake)
    status, _ = rc.resend_send("scott@smd.services", "operator@example.test", "Card 1 - x", "say", "k")

    assert status == 200
    assert seen["url"] == rc.RESEND_URL
    # urllib capitalizes header names as it stores them.
    assert seen["headers"]["User-agent"] == rc.USER_AGENT
    assert seen["body"] == {
        "from": "scott@smd.services",
        "to": ["operator@example.test"],
        "subject": "Card 1 - x",
        "text": "say",
    }


def test_sent_items_query_encodes_the_orderby_space() -> None:
    """urllib refuses a literal space in a URL, so `$orderby=sentDateTime desc`
    has to ship as %20 or the poll never runs."""
    assert "%20" in rc.SENT_ITEMS_QUERY
    assert " " not in rc.SENT_ITEMS_QUERY


def test_graph_read_asks_for_a_plain_text_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the Prefer header Graph returns HTML and the transcript fills with
    markup instead of the Operator's words."""
    seen: dict = {}

    def fake(req: object, timeout: int = 45) -> _FakeResponse:
        seen["headers"] = dict(req.headers)  # type: ignore[attr-defined]
        return _FakeResponse(200, '{"value": []}')

    monkeypatch.setattr(rc.urllib.request, "urlopen", fake)
    rc.graph_get("/users/x/mailFolders/SentItems/messages", "tok", rc.TEXT_BODY_PREFER)
    assert seen["headers"]["Prefer"] == 'outlook.body-content-type="text"'
    assert seen["headers"]["Authorization"] == "Bearer tok"


def test_a_refused_send_is_recorded_as_no_reply(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fail closed and say so. A refused send that returned a reply from an
    earlier run, or that polled at all, would put an answer in the transcript
    for a command the seat never received."""

    def refuse(req: object, timeout: int = 45) -> _FakeResponse:
        raise rc.urllib.error.HTTPError(
            req.full_url,
            403,
            "Forbidden",
            {},
            io.BytesIO(b"error code: 1010"),  # type: ignore[attr-defined,arg-type]
        )

    monkeypatch.setattr(rc.urllib.request, "urlopen", refuse)
    out = rc.ask_msgraph("scott@smd.services", "operator@example.test", "Card 1 - x", "say", "k", _ExplodingToken(), 5)
    assert out is None
    assert "SEND REFUSED" in capsys.readouterr().out


def test_the_reply_comes_back_from_the_seats_sent_items(monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end over a mocked wire: ship through Resend, read the seat's own
    outbound folder, strip the quoted trail, return the Operator's words."""
    urls: list[str] = []
    fresh = (datetime.now(timezone.utc) + timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    reply = "The matter is open.\n\nOn Thu, Aug 20, 2026 someone wrote:\n> the original"

    def fake(req: object, timeout: int = 45) -> _FakeResponse:
        url = req.full_url  # type: ignore[attr-defined]
        urls.append(url)
        if url == rc.RESEND_URL:
            return _FakeResponse(200, '{"id": "re_1"}')
        payload = {"value": [_sent_message("scott@smd.services", "Card 1 - x", fresh, reply)]}
        return _FakeResponse(200, json.dumps(payload))

    monkeypatch.setattr(rc.urllib.request, "urlopen", fake)
    out = rc.ask_msgraph("scott@smd.services", "operator@example.test", "Card 1 - x", "say", "k", _StubToken(), 30)

    assert out == "The matter is open."
    assert urls[0] == rc.RESEND_URL
    assert "/users/operator%40example.test/mailFolders/SentItems/messages" in urls[1]


def test_a_wire_sized_token_grant_survives_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first live run against the paying seat died before its first poll.

    `_open` capped every body at 400 characters. A real client-credentials grant
    is about 1,500 characters and its access_token string opens near character
    78, so the JSON came back unterminated and `graph_token` raised instead of
    returning a bearer. The mocked bodies in this file were all short, which is
    how the cap passed 35 tests and failed on the wire. A 2xx body must come
    back whole; only an error body is capped.
    """
    jwt = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9." + "a" * 1400 + ".sig"
    grant = json.dumps({"token_type": "Bearer", "expires_in": 3599, "ext_expires_in": 3599, "access_token": jwt})
    assert grant.index('"access_token"') < 400 < len(grant)

    def fake(req: object, timeout: int = 45) -> _FakeResponse:
        return _FakeResponse(200, grant)

    monkeypatch.setattr(rc.urllib.request, "urlopen", fake)
    token, ttl = rc.graph_token("tenant", "client", "secret")
    assert token == jwt
    assert ttl == 3599


def test_an_error_body_is_still_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cap exists so a caller that echoes a non-2xx body cannot spill a
    page of it into a log. Lifting it from success bodies must not lift it
    from error bodies."""
    import urllib.error

    def fake(req: object, timeout: int = 45) -> _FakeResponse:
        raise urllib.error.HTTPError("https://x", 500, "boom", None, io.BytesIO(b"e" * 1000))  # type: ignore[arg-type]

    monkeypatch.setattr(rc.urllib.request, "urlopen", fake)
    status, body = rc._open(rc.urllib.request.Request("https://graph.microsoft.com/v1.0/x"))
    assert status == 500
    assert len(body) == 400
