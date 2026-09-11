"""msgraph_send / msgraph_reply verbs: PID-gated, recipient-fenced, audited.

The msgraph sibling of ``test_agentmail_send``, written against the same question
— "would this have stopped the incident?" — and against one the AgentMail file
does not have to ask.

On four days in 2026-08 a rehearsal seat sent fabricated email to a real client
principal and NOT ONE of those sends produced an audit row. The AgentMail fix
stacks two fences: the vendor makes the agent's key incapable of transmitting,
and the broker fences the recipient. This channel gets only the second, because
a Graph app-only token is always ``/.default`` and the agent legitimately needs
Graph credentials for the inbound poller and its mail tools.

So these tests pin what IS true here, and the file says plainly what is not:

* a recipient the seat's own config does not name is REFUSED — including in bcc,
  which delivers and which a fence reading only the visible recipients misses;
* a refusal still writes a row, because a silent refusal and a silent send look
  identical from outside;
* the reply lane checks the ORIGINAL SENDER, fetched broker-side, so "reply to
  whoever wrote in" cannot be aimed at an unapproved address;
* the mailbox comes from customer.yaml and a caller-supplied one is ignored;
* the verbs are unreachable from a non-gateway PID.

NOT proven here, and not provable here: that no path can reach Graph. Only a
second, read-only app registration in the tenant makes that sentence true.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker.transmit_verbs import dispatch_transmit
from workspace_broker.msgraph_auth import (
    load_credential,
    materialize_credential,
    seat_mailbox,
)
from workspace_broker.msgraph_ops import (
    AUDIT_ROW_HEADER,
    MsGraphOps,
    _audit_header_of,
    MsGraphRefused,
    MsGraphTransportError,
)
from workspace_broker.recipient_policy import sender_key
from workspace_broker.server import Broker

GATEWAY_PID = 42
AGENT_UID = 1000
SEAT = "smd-staging"
MAILBOX = "operator@opslab.example"
# A real person at a real firm, on no authored list of this seat — the shape of
# the address the incident actually reached.
UNAUTHORED = "someone@a-firm-this-seat-never-named.example"

# The staging seat's shape: an msgraph mailbox, two authored senders, no
# outbound_roster. A roster-only fence would refuse every legitimate send here.
STAGING_YAML = f"""
connectors:
  Email:
    adapter: msgraph
    backend: mcp:msgraph-mail
    enabled: true
    msgraph_auth:
      tenant_id: '11111111-1111-1111-1111-111111111111'
      client_id: '22222222-2222-2222-2222-222222222222'
      mailbox: {MAILBOX}
scope:
  inbound_allow_from:
    - scott@smd.services
  admins:
    - scott@smd.services
  domain_blocks: []
"""

# A law-firm shape on the Graph channel: a whole-domain inbound grant, principals
# named only in admins, and a blocked domain to prove deny beats allow.
FIRM_YAML = f"""
connectors:
  Email:
    adapter: msgraph
    enabled: true
    msgraph_auth:
      mailbox: {MAILBOX}
scope:
  inbound_allow_from:
    - '@examplefirm.example'
  admins:
    - chris@examplefirm.example
  domain_blocks:
    - blocked.example
"""

# A seat that authors NO counterparty at all. Must permit nothing: unconfigured
# is a safety state, never permission.
EMPTY_YAML = f"""
connectors:
  Email:
    adapter: msgraph
    enabled: true
    msgraph_auth:
      mailbox: {MAILBOX}
scope: {{}}
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


class FakeGraph:
    """Records requests and replays canned responses; no network is touched.

    Answers the token mint, returns an empty body for ``sendMail``/``reply`` (Graph
    really does answer 202 with nothing), serves a source message for the reply
    lane's independent sender fetch, and serves Sent Items for the ss#2499
    lookup.

    SENT ITEMS IS MODELLED, NOT STUBBED. It echoes back whatever
    ``internetMessageHeaders`` the send actually put on the wire, so a test can
    only find the message if the header really was stamped. A fixed canned
    response would pass whether or not the header existed, which is the failure
    mode of an instrument that cannot observe its own subject.

    ``sent_items_status`` makes the folder read fail with an HTTP status, and
    ``sent_items_misses`` makes it come back empty for the first N reads —
    Graph accepts a send before the copy lands, and that race is what the
    backoff exists for.
    """

    def __init__(
        self,
        *,
        source_from: str | None = None,
        conversation_id: str = "",
        sent_items_status: int | None = None,
        sent_items_misses: int = 0,
        reply_rejects_headers: bool = False,
        sent_items_conversation: str | None = None,
    ) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        #: (url, Authorization header) per Graph call — how the two-credential
        #: tests see WHICH app's token each request actually carried.
        self.auths: list[tuple[str, str]] = []
        self._source_from = source_from
        self._conversation_id = conversation_id
        self._sent_items_status = sent_items_status
        self._sent_items_misses = sent_items_misses
        self._reply_rejects_headers = reply_rejects_headers
        self._sent_items_conversation = sent_items_conversation
        #: Headers seen on the wire, oldest first — the folder replays these.
        self.transmitted_headers: list[list[dict]] = []
        self.sent_items_reads = 0

    def __call__(self, request, timeout=None):
        url = request.full_url
        raw = request.data
        body: dict | None = None
        if raw and url.endswith("/token"):
            body = {"form": raw.decode()}
        elif raw:
            body = json.loads(raw.decode())
        self.calls.append((request.method, url, body))
        if url.endswith("/token"):
            # The token is DERIVED FROM THE CLIENT_ID so a cache that leaks one
            # credential's token onto the other credential's request is visible
            # in the Authorization header rather than invisibly "working".
            form = urllib.parse.parse_qs((raw or b"").decode())
            client_id = (form.get("client_id") or ["?"])[0]
            return _Response(json.dumps({"access_token": f"tok-{client_id}", "expires_in": 3600}))
        self.auths.append((url, request.get_header("Authorization") or ""))
        if request.method == "GET" and "/mailFolders/sentitems/messages" in url:
            return self._sent_items()
        if request.method == "GET" and "/messages/" in url:
            return _Response(
                json.dumps(
                    {
                        "from": {"emailAddress": {"address": self._source_from or ""}},
                        "conversationId": self._conversation_id,
                    }
                )
            )
        if request.method == "POST":
            self._record_transmit(url, body)
        # sendMail / reply: 202, no body.
        return _Response("")

    def _record_transmit(self, url: str, body: dict | None) -> None:
        """Remember what the message that just went out actually carried."""
        message = (body or {}).get("message")
        headers = message.get("internetMessageHeaders") if isinstance(message, dict) else None
        if url.endswith("/reply") and self._reply_rejects_headers and headers:
            raise urllib.error.HTTPError(url, 400, "Bad Request", {}, None)  # type: ignore[arg-type]
        self.transmitted_headers.append(list(headers or []))

    def _sent_items(self) -> _Response:
        self.sent_items_reads += 1
        if self._sent_items_status is not None:
            raise urllib.error.HTTPError("sentitems", self._sent_items_status, "nope", {}, None)  # type: ignore[arg-type]
        if self.sent_items_reads <= self._sent_items_misses:
            return _Response(json.dumps({"value": []}))
        headers = self.transmitted_headers[-1] if self.transmitted_headers else []
        if not headers:
            return _Response(json.dumps({"value": []}))
        return _Response(
            json.dumps(
                {
                    "value": [
                        # A neighbour, so a lookup that returns the first row
                        # rather than the MATCHING one is visible.
                        {
                            "id": "AAMkNEIGHBOUR=",
                            "internetMessageId": "<neighbour@opslab.example>",
                            "internetMessageHeaders": [{"name": "x-other", "value": "no"}],
                        },
                        {
                            "id": "AAMkSENTCOPY=",
                            "internetMessageId": "<sent-copy@opslab.example>",
                            # Re-cased on purpose: Exchange is free to, and a
                            # case-sensitive compare would find nothing.
                            "internetMessageHeaders": [
                                {"name": h["name"].lower(), "value": h["value"]} for h in headers
                            ],
                            "conversationId": (
                                self._sent_items_conversation
                                if self._sent_items_conversation is not None
                                else self._conversation_id
                            ),
                        },
                    ]
                }
            )
        )

    def audit_token_on_the_wire(self) -> str:
        """The ``X-SMD-Audit-Row`` value the last transmit actually carried."""
        for header in self.transmitted_headers[-1] if self.transmitted_headers else []:
            if header.get("name") == AUDIT_ROW_HEADER:
                return str(header.get("value") or "")
        return ""

    def graph_posts(self) -> list[tuple[str, str, dict | None]]:
        return [c for c in self.calls if c[0] == "POST" and not c[1].endswith("/token")]

    def token_client_ids(self) -> list[str]:
        return [
            urllib.parse.parse_qs(c[2]["form"]).get("client_id", ["?"])[0]
            for c in self.calls
            if c[1].endswith("/token") and c[2]
        ]


def _seat(tmp_path: Path, yaml_text: str = STAGING_YAML) -> tuple[Path, Path, Path]:
    customer = tmp_path / "customer.yaml"
    customer.write_text(yaml_text)
    credential = tmp_path / "msgraph.json"
    credential.write_text(json.dumps({"tenant_id": "tid", "client_id": "cid-send", "client_secret": "shh"}))
    # The two-app fence's second file: the READ app's credential, distinct
    # client_id so token routing is observable (overlay#280).
    read_credential = tmp_path / "msgraph-read.json"
    read_credential.write_text(json.dumps({"tenant_id": "tid", "client_id": "cid-read", "client_secret": "shh2"}))
    return customer, credential, read_credential


def _ops(
    tmp_path: Path,
    http: FakeGraph,
    yaml_text: str = STAGING_YAML,
    *,
    with_read_credential: bool = True,
) -> MsGraphOps:
    customer, credential, read_credential = _seat(tmp_path, yaml_text)
    return MsGraphOps(
        credential,
        customer,
        read_credential_path=read_credential if with_read_credential else None,
        opener=http,
        # The Sent Items lookup waits between attempts (Graph accepts a send
        # before the copy lands). Injected as a no-op so the suite stays fast
        # WITHOUT anyone being tempted to shrink the live backoff to a value the
        # real mailbox cannot satisfy.
        sleep=lambda _seconds: None,
    )


# ---------------------------------------------------------------------------
# The fence — would it have stopped the incident?
# ---------------------------------------------------------------------------


def test_the_incident_recipient_is_refused(tmp_path: Path) -> None:
    """The whole point. An address no authored list names cannot be written to."""
    http = FakeGraph()
    ops = _ops(tmp_path, http)
    with pytest.raises(MsGraphRefused) as exc:
        ops.send({"to": [UNAUTHORED], "subject": "6 items need you", "body_text": "..."})
    assert UNAUTHORED in str(exc.value)
    # Nothing was transmitted, and no token was even minted: the refusal precedes
    # every network call, so a fenced send costs the credential nothing.
    assert http.calls == []


def test_an_authored_recipient_sends(tmp_path: Path) -> None:
    """Law 12: the refusal above means nothing if nothing can ever pass."""
    http = FakeGraph()
    ops = _ops(tmp_path, http)
    result = ops.send({"to": ["scott@smd.services"], "body_text": "hello"})
    assert result["mailbox"] == MAILBOX
    posts = http.graph_posts()
    assert len(posts) == 1 and posts[0][1].endswith("/sendMail")


def test_bcc_is_fenced_because_bcc_delivers(tmp_path: Path) -> None:
    """A fence reading only to/cc passes a message whose blind copy goes anywhere,
    and writes a row naming the wrong people — clean-looking and wrong."""
    http = FakeGraph()
    ops = _ops(tmp_path, http)
    with pytest.raises(MsGraphRefused) as exc:
        ops.send({"to": ["scott@smd.services"], "bcc": [UNAUTHORED], "body_text": "x"})
    assert UNAUTHORED in str(exc.value)
    assert http.calls == []


def test_a_domain_grant_authorizes_every_person_at_that_firm(tmp_path: Path) -> None:
    ops = _ops(tmp_path, FakeGraph(), FIRM_YAML)
    assert ops.send({"to": ["anyone@examplefirm.example"], "body_text": "hi"})["mailbox"]


def test_admins_are_reachable_with_no_outbound_roster(tmp_path: Path) -> None:
    ops = _ops(tmp_path, FakeGraph(), FIRM_YAML)
    assert ops.send({"to": ["chris@examplefirm.example"], "body_text": "hi"})["mailbox"]


def test_a_blocked_domain_beats_an_allow(tmp_path: Path) -> None:
    http = FakeGraph()
    ops = _ops(tmp_path, http, FIRM_YAML)
    with pytest.raises(MsGraphRefused):
        ops.send({"to": ["someone@blocked.example"], "body_text": "x"})
    assert http.calls == []


def test_an_unconfigured_seat_permits_nothing(tmp_path: Path) -> None:
    """Unconfigured is a safety state, never permission."""
    ops = _ops(tmp_path, FakeGraph(), EMPTY_YAML)
    with pytest.raises(MsGraphRefused):
        ops.send({"to": ["scott@smd.services"], "body_text": "x"})


def test_one_bad_recipient_refuses_the_whole_send(tmp_path: Path) -> None:
    """No partial sends: a message whose visible To is not what shipped is a lie."""
    http = FakeGraph()
    ops = _ops(tmp_path, http)
    with pytest.raises(MsGraphRefused):
        ops.send({"to": ["scott@smd.services"], "cc": [UNAUTHORED], "body_text": "x"})
    assert http.calls == []


def test_a_send_with_no_recipient_is_refused(tmp_path: Path) -> None:
    ops = _ops(tmp_path, FakeGraph())
    with pytest.raises(MsGraphRefused):
        ops.send({"subject": "S", "body_text": "B"})


def test_a_display_name_recipient_is_parsed_not_compared_raw(tmp_path: Path) -> None:
    """Mail carries "Name <addr>" as often as bare; comparing that raw refuses
    everyone, which would look like a working fence and be a broken product."""
    ops = _ops(tmp_path, FakeGraph())
    assert ops.send({"to": ['"Scott" <Scott@SMD.Services>'], "body_text": "x"})["mailbox"]


# ---------------------------------------------------------------------------
# Identity — the From is not the caller's to choose
# ---------------------------------------------------------------------------


def test_the_mailbox_comes_from_config_and_a_supplied_one_is_ignored(tmp_path: Path) -> None:
    http = FakeGraph()
    ops = _ops(tmp_path, http)
    ops.send(
        {
            "to": ["scott@smd.services"],
            "body_text": "x",
            "mailbox": "attacker@evil.example",
            "from": "attacker@evil.example",
            "sender": "attacker@evil.example",
        }
    )
    method, url, body = http.graph_posts()[0]
    assert f"/users/{MAILBOX}/sendMail" in url
    assert "evil.example" not in json.dumps(body)


def test_a_seat_with_no_authored_msgraph_mailbox_refuses(tmp_path: Path) -> None:
    """An agentmail seat has no Graph identity; absence must not become a default."""
    ops = _ops(
        tmp_path,
        FakeGraph(),
        "connectors:\n  Email:\n    adapter: agentmail\n    enabled: true\n"
        "scope:\n  admins:\n    - scott@smd.services\n",
    )
    with pytest.raises(MsGraphTransportError):
        ops.send({"to": ["scott@smd.services"], "body_text": "x"})


def test_seat_mailbox_reads_only_an_msgraph_adapter(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(STAGING_YAML)
    assert seat_mailbox(path) == MAILBOX
    path.write_text(STAGING_YAML.replace("adapter: msgraph", "adapter: agentmail"))
    assert seat_mailbox(path) == ""


# ---------------------------------------------------------------------------
# Body shaping — what reaches the wire, and what cannot
# ---------------------------------------------------------------------------


def test_both_body_spellings_reach_the_wire(tmp_path: Path) -> None:
    """The confirm path sends `body_text`, the send tool sends `text`. One verb
    serves both, so neither caller silently ships an empty message."""
    for key in ("body_text", "text"):
        http = FakeGraph()
        ops = _ops(tmp_path, http)
        ops.send({"to": ["scott@smd.services"], key: "the body"})
        _m, _u, body = http.graph_posts()[0]
        assert body["message"]["body"]["content"] == "the body"
        assert body["message"]["body"]["contentType"] == "Text"


def test_an_html_body_is_sent_as_html(tmp_path: Path) -> None:
    http = FakeGraph()
    ops = _ops(tmp_path, http)
    ops.send({"to": ["scott@smd.services"], "text": "plain", "html": "<p>rich</p>"})
    _m, _u, body = http.graph_posts()[0]
    assert body["message"]["body"] == {"contentType": "HTML", "content": "<p>rich</p>"}


def test_unknown_payload_keys_never_reach_the_wire(tmp_path: Path) -> None:
    """A closed allowlist: no grant, approval marker, or caller bookkeeping rides
    along, and no `from`-shaped key can reach Graph at all."""
    http = FakeGraph()
    ops = _ops(tmp_path, http)
    ops.send(
        {
            "to": ["scott@smd.services"],
            "body_text": "x",
            "smd_grant": "secret-grant",
            "approved": True,
        }
    )
    _m, _u, body = http.graph_posts()[0]
    assert "smd_grant" not in json.dumps(body)
    assert set(body["message"]) <= {
        "subject",
        "body",
        "toRecipients",
        "ccRecipients",
        "bccRecipients",
        "replyTo",
        # ss#2499. On the list because the BROKER puts it there, never a caller
        # — the test below proves a caller cannot.
        "internetMessageHeaders",
    }


def test_a_caller_cannot_stamp_its_own_audit_header(tmp_path: Path) -> None:
    """The header is the key the reconciler treats as exact. A caller that could
    set it could stamp this send with another send's audit key, and the ledger
    would then hold two rows claiming one message."""
    http = FakeGraph()
    ops = _ops(tmp_path, http)
    ops.send(
        {
            "to": ["scott@smd.services"],
            "body_text": "x",
            "internetMessageHeaders": [{"name": AUDIT_ROW_HEADER, "value": "FORGED"}],
        }
    )
    _m, _u, body = http.graph_posts()[0]
    assert "FORGED" not in json.dumps(body)
    assert http.audit_token_on_the_wire() and http.audit_token_on_the_wire() != "FORGED"


def test_a_202_with_no_body_is_success_not_a_parse_error(tmp_path: Path) -> None:
    """Graph answers sendMail with an empty body. Reading that as malformed would
    turn every successful send into a reported failure."""
    ops = _ops(tmp_path, FakeGraph())
    assert ops.send({"to": ["scott@smd.services"], "body_text": "x"})["message_id"] == ""


# ---------------------------------------------------------------------------
# The reply lane — anyone can email this mailbox
# ---------------------------------------------------------------------------


def test_reply_refuses_when_the_original_sender_is_not_authored(tmp_path: Path) -> None:
    http = FakeGraph(source_from=UNAUTHORED)
    ops = _ops(tmp_path, http)
    with pytest.raises(MsGraphRefused):
        ops.reply({"message_id": "AAMk123", "comment": "sure"})
    assert http.graph_posts() == []


def test_reply_allows_an_authored_sender(tmp_path: Path) -> None:
    """Law 12 control for the refusal above."""
    http = FakeGraph(source_from="scott@smd.services")
    ops = _ops(tmp_path, http)
    result = ops.reply({"message_id": "AAMk123", "comment": "sure"})
    assert result["recipients"] == ["scott@smd.services"]
    assert http.graph_posts()[0][1].endswith("/reply")


def test_reply_uses_the_fetched_sender_not_a_supplied_one(tmp_path: Path) -> None:
    """A caller that can name the sender can name any sender."""
    http = FakeGraph(source_from=UNAUTHORED)
    ops = _ops(tmp_path, http)
    with pytest.raises(MsGraphRefused):
        ops.reply({"message_id": "AAMk123", "comment": "sure", "from": "scott@smd.services"})


def test_reply_refuses_when_the_sender_cannot_be_determined(tmp_path: Path) -> None:
    ops = _ops(tmp_path, FakeGraph(source_from=""))
    with pytest.raises(MsGraphRefused):
        ops.reply({"message_id": "AAMk123", "comment": "sure"})


# ---------------------------------------------------------------------------
# Two credentials, one verb (overlay#280) — the GET reads, the POST sends
# ---------------------------------------------------------------------------


def test_reply_fetches_with_the_read_token_and_posts_with_the_send_token(
    tmp_path: Path,
) -> None:
    """The incident: on a two-app seat the send app cannot read, so a reply whose
    sender-verification GET rides the send token 403s forever. The GET must carry
    the READ app's token and the POST the SEND app's — asserted from the actual
    Authorization headers, so a shared token cache (the GET mints first and would
    poison the POST) cannot pass."""
    http = FakeGraph(source_from="scott@smd.services")
    ops = _ops(tmp_path, http)
    ops.reply({"message_id": "AAMk123", "comment": "sure"})
    # Classified by URL, not by method: ss#2499 added a THIRD call (the Sent
    # Items read), which is a GET on the read token, and a rule that lumped it
    # in with the sender fetch would let a regression on either hide behind the
    # other.
    auth_by_url = dict(http.auths)
    fetch = next(u for u in auth_by_url if "/messages/AAMk123?" in u)
    post = next(u for u in auth_by_url if u.endswith("/reply"))
    lookup = next(u for u in auth_by_url if "/mailFolders/sentitems/messages" in u)
    assert auth_by_url[fetch] == "Bearer tok-cid-read"
    assert auth_by_url[post] == "Bearer tok-cid-send"
    assert auth_by_url[lookup] == "Bearer tok-cid-read"


def test_reply_mints_two_distinct_tokens(tmp_path: Path) -> None:
    http = FakeGraph(source_from="scott@smd.services")
    ops = _ops(tmp_path, http)
    ops.reply({"message_id": "AAMk123", "comment": "sure"})
    assert sorted(http.token_client_ids()) == ["cid-read", "cid-send"]


def test_the_send_itself_rides_the_send_app_and_the_lookup_the_read_app(
    tmp_path: Path,
) -> None:
    """The transmit is still the send app's, alone. ss#2499 adds a Sent Items
    read afterwards, and that one MUST be the read app's — the send app holds
    ``Mail.Send`` only (overlay#280) and would 403 on a folder listing, so a
    lookup on the send token could never work on a real two-app seat."""
    http = FakeGraph()
    ops = _ops(tmp_path, http)
    ops.send({"to": ["scott@smd.services"], "body_text": "x"})
    assert sorted(http.token_client_ids()) == ["cid-read", "cid-send"]
    by_url = dict(http.auths)
    transmit = next(u for u in by_url if u.endswith("/sendMail"))
    lookup = next(u for u in by_url if "/mailFolders/sentitems/messages" in u)
    assert by_url[transmit] == "Bearer tok-cid-send"
    assert by_url[lookup] == "Bearer tok-cid-read"


def test_reply_fails_closed_without_a_read_credential(tmp_path: Path) -> None:
    """No read credential means the sender cannot be verified, and an unverified
    reply lane is an exfiltration primitive — so nothing is attempted at all."""
    http = FakeGraph(source_from="scott@smd.services")
    ops = _ops(tmp_path, http, with_read_credential=False)
    with pytest.raises(MsGraphTransportError) as exc:
        ops.reply({"message_id": "AAMk123", "comment": "sure"})
    assert "read credential" in str(exc.value)
    assert http.calls == []  # not even a token mint


@pytest.mark.parametrize(
    "message_id",
    [
        "../../users/someone-else@evil.example/messages/x",
        "AAMk123/../../me/sendMail",
        "AAMk123?$select=body",
        "AAMk123#frag",
        "AAMk 123",
    ],
)
def test_a_message_id_cannot_restructure_the_graph_url(tmp_path: Path, message_id: str) -> None:
    """Segments reach Graph RAW (matching the live-proven wire format), so an id
    carrying `/` would add a path element and one carrying `?` a query string.
    Validated rather than escaped: a refusal is visible, a failed lookup is not."""
    http = FakeGraph(source_from="scott@smd.services")
    ops = _ops(tmp_path, http)
    with pytest.raises(MsGraphRefused):
        ops.reply({"message_id": message_id, "comment": "ok"})
    assert http.calls == []


def test_a_real_graph_id_shape_is_accepted(tmp_path: Path) -> None:
    """Law 12 control: the guard above is worthless if it refuses real ids.

    Graph message ids are a URL-safe base64 variant and routinely end in `=`.
    """
    real_shape = "AAMkAGI2THVSAAA-9xQdAAA=_-.~"
    http = FakeGraph(source_from="scott@smd.services")
    ops = _ops(tmp_path, http)
    ops.reply({"message_id": real_shape, "comment": "ok"})
    # And it reached Graph UNESCAPED — no %3D for the trailing '='.
    url = http.graph_posts()[0][1]
    assert f"/messages/{real_shape}/reply" in url
    assert "%" not in url.split("/messages/")[1]


def test_reply_refuses_without_a_message_id_or_a_comment(tmp_path: Path) -> None:
    ops = _ops(tmp_path, FakeGraph(source_from="scott@smd.services"))
    with pytest.raises(MsGraphRefused):
        ops.reply({"comment": "sure"})
    with pytest.raises(MsGraphRefused):
        ops.reply({"message_id": "AAMk123", "comment": "   "})


# ---------------------------------------------------------------------------
# ss#2489 — the reply body reaches the firm with its line structure intact
#
# The incident: Graph composes the /reply message IN HTML, so a plain-text
# comment lands in an HTML body and every newline collapses. Four replies
# reached hermes-ashton-price's principal as one unbroken block on 2026-08-20.
# ---------------------------------------------------------------------------


_MULTILINE = "Line one.\n\nLine two."
_RENDERED = "<div><p>Line one.</p><p>Line two.</p></div>"


def _without_audit_header(body: dict | None) -> dict:
    """The reply body as it was BEFORE ss#2499 added its one header.

    The tests below are about the ss#2489 body shape — html vs comment, and the
    fact that the two are mutually exclusive. Rewriting each of them to carry the
    audit header inline would bury the property each one exists to pin. Stripping
    the header here keeps those assertions exact and leaves "is the header on the
    wire" to the tests that are actually about that.
    """
    out = json.loads(json.dumps(body or {}))
    message = out.get("message")
    if isinstance(message, dict):
        message.pop("internetMessageHeaders", None)
        if not message:
            out.pop("message")
    return out


def test_reply_sends_an_html_body_when_one_was_rendered(tmp_path: Path) -> None:
    http = FakeGraph(source_from="scott@smd.services")
    ops = _ops(tmp_path, http)
    ops.reply({"message_id": "AAMk123", "comment": _MULTILINE, "html": _RENDERED})
    body = _without_audit_header(http.graph_posts()[0][2])
    assert body == {"message": {"body": {"contentType": "HTML", "content": _RENDERED}}}


def test_reply_never_sends_comment_and_body_together(tmp_path: Path) -> None:
    """Graph answers 400 when both are present, so the two are exclusive. This is
    the falsifier for the test above: an implementation that merely ADDED the
    html alongside the comment would satisfy that assertion's spirit and 400 on
    the wire."""
    http = FakeGraph(source_from="scott@smd.services")
    ops = _ops(tmp_path, http)
    ops.reply({"message_id": "AAMk123", "comment": _MULTILINE, "html": _RENDERED})
    body = http.graph_posts()[0][2] or {}
    assert "comment" not in body


def test_reply_without_html_is_byte_identical_to_today(tmp_path: Path) -> None:
    """The blast radius stays at zero for a caller that sends no html — an older
    overlay against a newer broker replies exactly as it does now."""
    http = FakeGraph(source_from="scott@smd.services")
    ops = _ops(tmp_path, http)
    ops.reply({"message_id": "AAMk123", "comment": "sure"})
    assert _without_audit_header(http.graph_posts()[0][2]) == {"comment": "sure"}


def test_reply_accepts_an_html_only_body(tmp_path: Path) -> None:
    """An html body IS a body: the empty-reply refusal must not fire on it."""
    http = FakeGraph(source_from="scott@smd.services")
    ops = _ops(tmp_path, http)
    ops.reply({"message_id": "AAMk123", "html": _RENDERED})
    assert _without_audit_header(http.graph_posts()[0][2]) == {
        "message": {"body": {"contentType": "HTML", "content": _RENDERED}}
    }


def test_reply_still_refuses_when_both_halves_are_empty(tmp_path: Path) -> None:
    ops = _ops(tmp_path, FakeGraph(source_from="scott@smd.services"))
    with pytest.raises(MsGraphRefused):
        ops.reply({"message_id": "AAMk123", "comment": "   ", "html": "  "})


def test_the_html_reply_is_still_sender_fenced(tmp_path: Path) -> None:
    """The new body shape must not route around the check that matters."""
    http = FakeGraph(source_from=UNAUTHORED)
    ops = _ops(tmp_path, http)
    with pytest.raises(MsGraphRefused):
        ops.reply({"message_id": "AAMk123", "comment": _MULTILINE, "html": _RENDERED})
    assert http.graph_posts() == []


def test_the_reply_lane_is_narrower_than_the_send_lane(tmp_path: Path) -> None:
    """``admins`` may be written to; only ``inbound_allow_from`` may be answered.

    On FIRM_YAML the admin is NOT on inbound_allow_from as an exact entry — the
    domain grant is what covers them — so this also pins that the reply check
    honours domain grants rather than exact entries alone.
    """
    http = FakeGraph(source_from="chris@examplefirm.example")
    ops = _ops(tmp_path, http, FIRM_YAML)
    assert ops.reply({"message_id": "m1", "comment": "ok"})["recipients"]


# ---------------------------------------------------------------------------
# Credential custody
# ---------------------------------------------------------------------------


def test_a_partially_staged_credential_refuses_to_boot(tmp_path: Path, monkeypatch) -> None:
    """A half-wired send path must not boot a seat that believes it can send."""
    monkeypatch.setenv("MSGRAPH_SEND_TENANT_ID", "tid")
    monkeypatch.setenv("MSGRAPH_SEND_CLIENT_ID", "cid")
    monkeypatch.delenv("MSGRAPH_SEND_CLIENT_SECRET", raising=False)
    with pytest.raises(RuntimeError) as exc:
        materialize_credential(tmp_path / "msgraph.json")
    assert "MSGRAPH_SEND_CLIENT_SECRET" in str(exc.value)
    assert "tid" not in str(exc.value)  # names, never values


def test_no_staged_credential_is_a_no_op_not_an_error(tmp_path: Path, monkeypatch) -> None:
    """A seat with no msgraph connector stages nothing; absence fails closed at
    send time, which is where it belongs."""
    for name in ("MSGRAPH_SEND_TENANT_ID", "MSGRAPH_SEND_CLIENT_ID", "MSGRAPH_SEND_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    target = tmp_path / "msgraph.json"
    materialize_credential(target)
    assert not target.exists()


def test_a_materialized_credential_is_0600(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MSGRAPH_SEND_TENANT_ID", "tid")
    monkeypatch.setenv("MSGRAPH_SEND_CLIENT_ID", "cid")
    monkeypatch.setenv("MSGRAPH_SEND_CLIENT_SECRET", "shh")
    target = tmp_path / "msgraph.json"
    materialize_credential(target)
    assert target.stat().st_mode & 0o777 == 0o600
    assert load_credential(target)["client_secret"] == "shh"


@pytest.mark.parametrize("content", ["", "not json", "[]", json.dumps({"tenant_id": "t", "client_id": "c"})])
def test_an_unusable_credential_file_reads_as_absent(tmp_path: Path, content: str) -> None:
    """Every failure mode collapses to "no credential", so a truncated or partial
    file refuses rather than half-attempting a send with a partial value."""
    path = tmp_path / "msgraph.json"
    path.write_text(content)
    assert load_credential(path) == {}


def test_a_missing_credential_refuses_at_send_time(tmp_path: Path) -> None:
    customer = tmp_path / "customer.yaml"
    customer.write_text(STAGING_YAML)
    ops = MsGraphOps(tmp_path / "absent.json", customer, opener=FakeGraph())
    with pytest.raises(MsGraphTransportError):
        ops.send({"to": ["scott@smd.services"], "body_text": "x"})


def test_the_client_secret_never_appears_in_a_token_error(tmp_path: Path) -> None:
    """The token endpoint echoes request parameters in its error bodies, and one
    of those parameters is the secret. Status only, never the body."""
    import urllib.error

    def _reject(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    customer, credential, _read = _seat(tmp_path)
    ops = MsGraphOps(credential, customer, opener=_reject)
    with pytest.raises(MsGraphTransportError) as exc:
        ops.send({"to": ["scott@smd.services"], "body_text": "x"})
    assert "401" in str(exc.value)
    assert "shh" not in str(exc.value)


# ---------------------------------------------------------------------------
# The verb surface — gating and the audit row
# ---------------------------------------------------------------------------


class RecordingLedger:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def append(self, row: dict) -> str:
        self.rows.append(row)
        return f"row-{len(self.rows)}"


def _broker(tmp_path: Path, http: FakeGraph, yaml_text: str = STAGING_YAML) -> Broker:
    broker = Broker.__new__(Broker)
    broker.customer_slug = SEAT
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.ledger = RecordingLedger()
    broker.msgraph = _ops(tmp_path, http, yaml_text)
    return broker


def _meta(broker: Broker, index: int = 0) -> dict:
    return json.loads(broker.ledger.rows[index]["metadata"])


def test_verb_is_unreachable_from_a_non_gateway_pid(tmp_path: Path) -> None:
    """Not agent-uid gated, by design: a cron child must not be able to send."""
    broker = _broker(tmp_path, FakeGraph())
    with pytest.raises(PermissionError):
        broker.handle(
            {"action": "msgraph_send", "payload": {"to": ["scott@smd.services"]}},
            peer_pid=9999,
            peer_uid=AGENT_UID,
        )
    assert broker.ledger.rows == []


def test_dispatch_writes_a_row_naming_the_mailbox_and_recipients(tmp_path: Path) -> None:
    broker = _broker(tmp_path, FakeGraph())
    response = broker.handle(
        {
            "action": "msgraph_send",
            "payload": {"to": ["scott@smd.services"], "body_text": "hi"},
        },
        peer_pid=GATEWAY_PID,
        peer_uid=AGENT_UID,
    )
    assert response["ok"]
    row = broker.ledger.rows[0]
    assert row["action_type"] == "CONFIRM_SEND_DISPATCHED"
    meta = _meta(broker)
    assert meta["outcome"] == "sent"
    assert meta["recipients"] == ["scott@smd.services"]
    assert meta["mailbox"] == MAILBOX
    assert meta["verb"] == "msgraph_send"
    assert meta["input_digest"]


def test_a_refusal_is_audited_not_silent(tmp_path: Path) -> None:
    """A refused send that leaves no trace is indistinguishable from no send."""
    broker = _broker(tmp_path, FakeGraph())
    with pytest.raises(MsGraphRefused):
        broker.handle(
            {"action": "msgraph_send", "payload": {"to": [UNAUTHORED], "body_text": "x"}},
            peer_pid=GATEWAY_PID,
            peer_uid=AGENT_UID,
        )
    assert broker.ledger.rows[0]["action_type"] == "CONFIRM_SEND_FAILED"
    meta = _meta(broker)
    assert meta["outcome"] == "refused"
    assert meta["recipients"] == [UNAUTHORED]


def test_a_transport_failure_is_not_recorded_as_a_refusal(tmp_path: Path) -> None:
    """The seat WAS permitted to write and the vendor call failed; the message may
    even have gone out. Recording that as "forbidden" would be a lie in the
    ledger's own language, and the reconciler reads this field."""
    import urllib.error

    def _boom(request, timeout=None):
        if request.full_url.endswith("/token"):
            return _Response(json.dumps({"access_token": "tok", "expires_in": 3600}))
        raise urllib.error.HTTPError(request.full_url, 503, "nope", {}, None)

    customer, credential, _read = _seat(tmp_path)
    broker = Broker.__new__(Broker)
    broker.customer_slug = SEAT
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.ledger = RecordingLedger()
    broker.msgraph = MsGraphOps(credential, customer, opener=_boom)
    with pytest.raises(MsGraphTransportError):
        broker.handle(
            {
                "action": "msgraph_send",
                "payload": {"to": ["scott@smd.services"], "body_text": "x"},
            },
            peer_pid=GATEWAY_PID,
            peer_uid=AGENT_UID,
        )
    assert _meta(broker)["outcome"] == "transport_error"


def test_the_row_never_carries_the_body(tmp_path: Path) -> None:
    broker = _broker(tmp_path, FakeGraph())
    broker.handle(
        {
            "action": "msgraph_send",
            "payload": {
                "to": ["scott@smd.services"],
                "subject": "Quarterly",
                "body_text": "SECRET-BODY-TEXT",
            },
        },
        peer_pid=GATEWAY_PID,
        peer_uid=AGENT_UID,
    )
    assert "SECRET-BODY-TEXT" not in json.dumps(broker.ledger.rows)


def test_an_unconfigured_broker_refuses_the_verb(tmp_path: Path) -> None:
    """No ops object ⇒ no send. Absence must not read as permission."""
    broker = _broker(tmp_path, FakeGraph())
    broker.msgraph = None
    with pytest.raises(ValueError):
        broker.handle(
            {"action": "msgraph_send", "payload": {"to": ["scott@smd.services"]}},
            peer_pid=GATEWAY_PID,
            peer_uid=AGENT_UID,
        )


def test_the_reply_verb_writes_its_own_row(tmp_path: Path) -> None:
    broker = _broker(tmp_path, FakeGraph(source_from="scott@smd.services"))
    broker.handle(
        {"action": "msgraph_reply", "payload": {"message_id": "m1", "comment": "ok"}},
        peer_pid=GATEWAY_PID,
        peer_uid=AGENT_UID,
    )
    meta = _meta(broker)
    assert meta["verb"] == "msgraph_reply"
    assert meta["recipients"] == ["scott@smd.services"]


# ---------------------------------------------------------------------------
# The audit joins (ss#2497)
#
# Measured on the live ashton-price ledger 2026-08-21
# (vfy_01M0H8DR6JAPYVHFMNJZXQZ517): session_id appeared on 0 of 9
# CONFIRM_SEND_DISPATCHED rows and matter_ref on none of them. A send row that
# cannot say which turn composed it or which matter it concerned is what the
# cross-matter question (ss#2167) falls into: the ledger shows the reads and the
# sends and cannot connect them, and that silence reads as innocence.
# ---------------------------------------------------------------------------


def test_the_send_row_carries_the_session_and_the_matter(tmp_path: Path) -> None:
    """FALSIFIER: drop the two kwargs from the append_send_row call and both
    assertions fail while every other row assertion in this file stays green,
    which is exactly how the gap survived."""
    broker = _broker(tmp_path, FakeGraph())
    broker.handle(
        {
            "action": "msgraph_send",
            "payload": {"to": ["scott@smd.services"], "body_text": "hi"},
            "session_id": "20260820_195837_68d654ce",
            "matter_ref": "matter-uuid-1",
        },
        peer_pid=GATEWAY_PID,
        peer_uid=AGENT_UID,
    )
    row = broker.ledger.rows[0]
    # matter_ref goes to the COLUMN, which is what the portal record filters on.
    assert row["matter_ref"] == "matter-uuid-1"
    assert _meta(broker)["session_id"] == "20260820_195837_68d654ce"


def test_a_refusal_row_carries_them_too(tmp_path: Path) -> None:
    """A refused send is the row an investigator most wants to place in a
    session. FALSIFIER: thread the joins only through the success path."""
    broker = _broker(tmp_path, FakeGraph())
    with pytest.raises(MsGraphRefused):
        broker.handle(
            {
                "action": "msgraph_send",
                "payload": {"to": [UNAUTHORED], "body_text": "x"},
                "session_id": "sess-2",
                "matter_ref": "matter-uuid-2",
            },
            peer_pid=GATEWAY_PID,
            peer_uid=AGENT_UID,
        )
    assert broker.ledger.rows[0]["matter_ref"] == "matter-uuid-2"
    assert _meta(broker)["session_id"] == "sess-2"


def test_a_caller_that_sends_no_joins_writes_the_row_it_writes_today(tmp_path: Path) -> None:
    """The deployment-order property. An overlay that predates this change sends
    neither field, and the row must be exactly what it was, with no empty
    strings: the chain canonicalizes "" distinctly from NULL, and an empty
    matter_ref reads as a reference that is present and blank."""
    broker = _broker(tmp_path, FakeGraph())
    broker.handle(
        {
            "action": "msgraph_send",
            "payload": {"to": ["scott@smd.services"], "body_text": "hi"},
        },
        peer_pid=GATEWAY_PID,
        peer_uid=AGENT_UID,
    )
    assert "matter_ref" not in broker.ledger.rows[0]
    assert "session_id" not in _meta(broker)
    assert "skill_name" not in broker.ledger.rows[0]


def test_audit_extra_rides_the_msgraph_row_through_the_same_allowlist(tmp_path: Path) -> None:
    """The Graph channel shares ``transmit_verbs.dispatch_transmit`` with AgentMail ON PURPOSE
    (one audit writer, no forked copy to drift), but until this test nothing
    on the paying seat's channel proved the caller stamps arrive here at all.
    Same closed allowlist, same column placement: the body stamps land in
    metadata, ``skill_name`` (B3, claims review 2026-09-04) on its column, an
    unlisted key drops, and none of it reaches the wire.

    FALSIFIER: route msgraph_send through a private audit writer that forgets
    ``audit_extra`` and every metadata assertion fails while the AgentMail twin
    stays green -- which is exactly the drift the shared writer forbids.
    """
    http = FakeGraph()
    broker = _broker(tmp_path, http)
    sha = "a" * 64
    broker.handle(
        {
            "action": "msgraph_send",
            "payload": {"to": ["scott@smd.services"], "body_text": "hi"},
            "session_id": "sess-5",
            "audit_extra": {
                "routing_leg": "central",
                "rendered_body_sha256": sha,
                "body_variant": "full",
                "skill_name": "deadline-miss-escalator",
                "not_allowlisted": "dropped",
            },
        },
        peer_pid=GATEWAY_PID,
        peer_uid=AGENT_UID,
    )
    row = broker.ledger.rows[0]
    meta = _meta(broker)
    assert meta["routing_leg"] == "central"
    assert meta["rendered_body_sha256"] == sha
    assert meta["body_variant"] == "full"
    assert "not_allowlisted" not in meta
    assert row["skill_name"] == "deadline-miss-escalator"
    assert "skill_name" not in meta
    wire = json.dumps(http.graph_posts()[0][2])
    assert "deadline-miss-escalator" not in wire
    assert sha not in wire


def test_a_refused_msgraph_send_still_names_its_routine(tmp_path: Path) -> None:
    """The refusal row is the one an investigator most wants attributed: a
    refused full body precedes the skeleton fallback, and the column is what
    ties both rows to the wake that authored them."""
    broker = _broker(tmp_path, FakeGraph())
    with pytest.raises(MsGraphRefused):
        broker.handle(
            {
                "action": "msgraph_send",
                "payload": {"to": [UNAUTHORED], "body_text": "x"},
                "audit_extra": {"skill_name": "deadline-miss-escalator", "body_variant": "full"},
            },
            peer_pid=GATEWAY_PID,
            peer_uid=AGENT_UID,
        )
    row = broker.ledger.rows[0]
    assert row["action_type"] == "CONFIRM_SEND_FAILED"
    assert row["skill_name"] == "deadline-miss-escalator"
    assert _meta(broker)["body_variant"] == "full"


def test_the_joins_never_reach_the_vendor(tmp_path: Path) -> None:
    """They are audit attribution, not content. The Graph message is built from a
    closed allowlist, so this also proves the fields were read from the REQUEST
    and not smuggled through the payload."""
    http = FakeGraph()
    broker = _broker(tmp_path, http)
    broker.handle(
        {
            "action": "msgraph_send",
            "payload": {"to": ["scott@smd.services"], "body_text": "hi"},
            "session_id": "sess-3",
            "matter_ref": "matter-uuid-3",
        },
        peer_pid=GATEWAY_PID,
        peer_uid=AGENT_UID,
    )
    wire = json.dumps(http.graph_posts()[0][2])
    assert "sess-3" not in wire
    assert "matter-uuid-3" not in wire


def test_a_reply_row_names_the_person_it_answered_without_an_address(tmp_path: Path) -> None:
    """The broker is the ONLY party that can do this: it fetched the source
    message itself precisely because a caller naming the sender could name any
    sender. Hashed, because this row is exported.

    FALSIFIER: return the sender address instead of its key and the last
    assertion fails; drop the field and the first two do.
    """
    broker = _broker(tmp_path, FakeGraph(source_from="scott@smd.services"))
    response = broker.handle(
        {
            "action": "msgraph_reply",
            "payload": {"message_id": "AAMk123", "comment": "sure"},
            "session_id": "sess-4",
        },
        peer_pid=GATEWAY_PID,
        peer_uid=AGENT_UID,
    )
    meta = _meta(broker)
    assert meta["sender_key"] == sender_key("scott@smd.services")
    assert "@" not in meta["sender_key"]
    assert "scott@smd.services" not in json.dumps(meta["sender_key"])
    # Audit provenance stays in the row: the agent is told nothing new about who
    # it just wrote to beyond what it already knew.
    assert "sender_key" not in response


def test_a_plain_send_contributes_no_sender_key(tmp_path: Path) -> None:
    """Law 12 control for the test above: a send names no inbound person, so a
    key on that row would be invented rather than resolved."""
    broker = _broker(tmp_path, FakeGraph())
    broker.handle(
        {
            "action": "msgraph_send",
            "payload": {"to": ["scott@smd.services"], "body_text": "hi"},
        },
        peer_pid=GATEWAY_PID,
        peer_uid=AGENT_UID,
    )
    assert "sender_key" not in _meta(broker)


def test_the_sender_key_matches_the_overlay_recipe(tmp_path: Path) -> None:
    """A key is only a join if both ends reduce the same human to the same bytes.
    The overlay hashes NFC + strip + lower of the address
    (``shared/audit_contract.sender_key``); this pins the identical recipe on
    the broker side, so the two ends meet.

    FALSIFIER: hash the raw string here and the case/spacing variants below stop
    matching, which is a person appearing in the ledger as three people.
    """
    import hashlib

    assert sender_key("scott@smd.services") == hashlib.sha256(b"scott@smd.services").hexdigest()
    assert sender_key("  Scott@SMD.Services  ") == sender_key("scott@smd.services")
    assert sender_key("Scott Durgan <scott@smd.services>") == sender_key("scott@smd.services")
    assert sender_key("") is None
    assert sender_key(None) is None


# ---------------------------------------------------------------------------
# ss#2499 — the message can be found again
#
# Graph answers both verbs 202 with no body, so every msgraph audit row carried
# an empty id: 9 of 9 CONFIRM_SEND_DISPATCHED and 8 of 8 REPLY_SENT on the live
# A&P ledger (vfy_01M0H8DR6JAPYVHFMNJZXQZ517). A row that cannot be joined to
# the mailbox cannot answer "is this send one of yours?", which is the only
# question an audit log is asked about a message nobody expected.
#
# Two joins come out of this, and the second is the one that survives a bad day:
# the vendor id when the lookup worked, and the header itself always, because it
# is ON THE MESSAGE and the console-side reconciler reads it from the mailbox.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "wire_name",
    ["X-SMD-Audit-Row", "x-smd-audit-row", "X-SMD-AUDIT-ROW", "x-SMD-audit-ROW"],
)
def test_the_header_is_recognised_whatever_case_it_comes_back_in(wire_name: str) -> None:
    """RFC5322 makes header names case-insensitive and Exchange re-cases them
    freely. A case-sensitive read would find nothing and look like a message that
    never carried the header — a broken instrument wearing a clean result.

    Asserted over the helper rather than through a send, because the fake mailbox
    can only replay ONE casing and a test that pins that casing pins the fixture
    rather than the property."""
    assert _audit_header_of({"internetMessageHeaders": [{"name": wire_name, "value": "01ABC"}]}) == "01ABC"


def test_a_foreign_header_is_not_read_as_the_audit_one() -> None:
    """The other half: case-insensitive must not mean loose."""
    assert _audit_header_of({"internetMessageHeaders": [{"name": "x-ms-exchange-crosstenant", "value": "01ABC"}]}) == ""


def test_every_send_carries_an_audit_header(tmp_path: Path) -> None:
    http = FakeGraph()
    ops = _ops(tmp_path, http)
    result = ops.send({"to": ["scott@smd.services"], "body_text": "x"})
    _m, _u, body = http.graph_posts()[0]
    assert body["message"]["internetMessageHeaders"] == [{"name": AUDIT_ROW_HEADER, "value": result["audit_row_token"]}]


def test_the_header_value_is_the_token_written_onto_the_row(tmp_path: Path) -> None:
    """The whole join in one assertion: what went out on the wire is what the
    audit row will claim. A token minted twice — once for the header, once for
    the result — would pass every other test in this file and join nothing."""
    http = FakeGraph()
    ops = _ops(tmp_path, http)
    result = ops.send({"to": ["scott@smd.services"], "body_text": "x"})
    assert http.audit_token_on_the_wire() == result["audit_row_token"] != ""


def test_two_sends_carry_two_different_tokens(tmp_path: Path) -> None:
    """A constant token would join every send to every row — an exact match that
    is always right and never useful."""
    http = FakeGraph()
    ops = _ops(tmp_path, http)
    first = ops.send({"to": ["scott@smd.services"], "body_text": "x"})
    second = ops.send({"to": ["scott@smd.services"], "body_text": "y"})
    assert first["audit_row_token"] != second["audit_row_token"]


def test_the_send_learns_both_ids_from_sent_items(tmp_path: Path) -> None:
    """Law 12 control for every failure test below: the lookup can succeed."""
    http = FakeGraph()
    ops = _ops(tmp_path, http)
    result = ops.send({"to": ["scott@smd.services"], "body_text": "x"})
    assert result["vendor_message_id"] == "<sent-copy@opslab.example>"
    assert result["graph_message_id"] == "AAMkSENTCOPY="
    assert result["lookup"] == "ok"


def test_the_lookup_selects_the_header_field_and_bounds_itself(tmp_path: Path) -> None:
    """``internetMessageHeaders`` is not returned unless it is SELECTED by name,
    so an unselected lookup finds nothing and looks like an unstamped mailbox."""
    http = FakeGraph()
    ops = _ops(tmp_path, http)
    ops.send({"to": ["scott@smd.services"], "body_text": "x"})
    url = next(u for u, _a in http.auths if "/mailFolders/sentitems/messages" in u)
    assert "internetMessageHeaders" in url
    assert "$top=" in url
    assert f"/users/{MAILBOX}/mailFolders/sentitems/messages" in url


def test_the_lookup_reads_only_this_seats_mailbox(tmp_path: Path) -> None:
    """4.6: the read surface is one mailbox. Every Graph path this process builds
    is rooted at the pinned address, and the folder read is no exception."""
    http = FakeGraph()
    ops = _ops(tmp_path, http)
    ops.send({"to": ["scott@smd.services"], "body_text": "x"})
    for url, _auth in http.auths:
        assert f"/users/{MAILBOX}/" in url


def test_the_lookup_takes_the_matching_message_not_the_newest(tmp_path: Path) -> None:
    """The folder's first row is a neighbour with a different header. A lookup
    that returned ``value[0]`` would attach some other message's id to this row —
    an id that is present, wrong, and indistinguishable from a right one."""
    http = FakeGraph()
    ops = _ops(tmp_path, http)
    result = ops.send({"to": ["scott@smd.services"], "body_text": "x"})
    assert result["vendor_message_id"] != "<neighbour@opslab.example>"


def test_the_lookup_retries_while_the_copy_is_still_landing(tmp_path: Path) -> None:
    """Graph ACCEPTS a send before the Sent Items copy exists, so the first read
    can legitimately come back empty. Giving up there would report a lookup
    failure on a perfectly ordinary send."""
    http = FakeGraph(sent_items_misses=2)
    ops = _ops(tmp_path, http)
    result = ops.send({"to": ["scott@smd.services"], "body_text": "x"})
    assert result["lookup"] == "ok"
    assert http.sent_items_reads == 3


def test_a_lookup_failure_is_recorded_and_never_raised(tmp_path: Path) -> None:
    """The message is already gone. Raising here would tell the caller its send
    failed when it did not, and a caller that retries a delivered message sends
    it twice — trading a missing id for a duplicate message to a client."""
    http = FakeGraph(sent_items_status=403)
    ops = _ops(tmp_path, http)
    result = ops.send({"to": ["scott@smd.services"], "body_text": "x"})
    assert result["lookup"].startswith("failed:") and "403" in result["lookup"]
    # No id is claimed, and no empty one is invented either: the row records the
    # reason instead, which is the difference between "we could not find it" and
    # the silent blank this issue exists to end.
    assert "vendor_message_id" not in result and "graph_message_id" not in result
    # The join that survives it: the header went out regardless, so the mailbox
    # side of the reconciliation still works.
    assert http.audit_token_on_the_wire() == result["audit_row_token"] != ""


def test_a_seat_with_no_read_credential_records_that_it_could_not_look(
    tmp_path: Path,
) -> None:
    """Distinct from a failed lookup: nothing was wrong with the mailbox, this
    seat has no credential to read it with. A shared wording would make a
    single-app seat look like a mailbox that lost a message."""
    http = FakeGraph()
    ops = _ops(tmp_path, http, with_read_credential=False)
    result = ops.send({"to": ["scott@smd.services"], "body_text": "x"})
    assert result["lookup"].startswith("skipped:")
    assert http.sent_items_reads == 0


def test_a_reply_carries_the_header_and_learns_its_ids(tmp_path: Path) -> None:
    http = FakeGraph(source_from="scott@smd.services", conversation_id="AAQkCONV=")
    ops = _ops(tmp_path, http)
    result = ops.reply({"message_id": "AAMk123", "html": _RENDERED})
    assert http.audit_token_on_the_wire() == result["audit_row_token"] != ""
    assert result["vendor_message_id"] == "<sent-copy@opslab.example>"
    assert result["lookup"] == "ok"


def test_a_bare_comment_reply_carries_the_header_beside_the_comment(
    tmp_path: Path,
) -> None:
    """Headers only, never a body: ``comment`` and ``message.body`` are the pair
    Graph answers 400 to, and that exclusion is about the BODY, not about the
    message object."""
    http = FakeGraph(source_from="scott@smd.services")
    ops = _ops(tmp_path, http)
    ops.reply({"message_id": "AAMk123", "comment": "sure"})
    body = http.graph_posts()[0][2] or {}
    assert body["comment"] == "sure"
    assert set(body["message"]) == {"internetMessageHeaders"}


def test_a_reply_refused_the_header_is_re_sent_unstamped_and_says_so(
    tmp_path: Path,
) -> None:
    """The honest fallback. ``message`` beside ``comment`` is documented but has
    never been observed on this tenant's wire, and the reply lane is client
    facing. A 400 means Graph rejected the BODY and sent nothing, so the reply
    goes out unstamped rather than failing — and the row says exactly that,
    because a silently unstamped send is the state this issue exists to end."""
    http = FakeGraph(source_from="scott@smd.services", reply_rejects_headers=True)
    ops = _ops(tmp_path, http)
    result = ops.reply({"message_id": "AAMk123", "comment": "sure"})
    # The reply still reached the firm.
    assert [u for _m, u, _b in http.graph_posts() if u.endswith("/reply")]
    assert http.transmitted_headers[-1] == []
    assert result["audit_row_token"] == ""
    assert "HTTP 400" in result["lookup"]


def test_a_non_400_reply_failure_still_propagates(tmp_path: Path) -> None:
    """The falsifier for the fallback above. A 500 or a timeout may have
    delivered, so re-sending would risk the same message twice; only a 400 is
    known to have sent nothing."""
    http = FakeGraph(source_from="scott@smd.services")
    ops = _ops(tmp_path, http)
    original = http._record_transmit
    attempts: list[str] = []

    def explode(url, body):
        # ONCE, not always. A double that failed every attempt would let a
        # "retry everything" implementation pass this test by failing its retry
        # too — the mutation would be invisible behind the double.
        if url.endswith("/reply") and not attempts:
            attempts.append(url)
            raise urllib.error.HTTPError(url, 503, "nope", {}, None)  # type: ignore[arg-type]
        original(url, body)

    http._record_transmit = explode  # type: ignore[method-assign]
    with pytest.raises(MsGraphTransportError):
        ops.reply({"message_id": "AAMk123", "comment": "sure"})


def test_a_reply_lookup_reports_a_conversation_disagreement(tmp_path: Path) -> None:
    """The conversationId is a cross-check on an exact match, never the match
    itself. It cannot be silently preferred, and it cannot be silently ignored."""
    http = FakeGraph(
        source_from="scott@smd.services",
        conversation_id="AAQkCONV=",
        sent_items_conversation="AAQkSOMETHINGELSE=",
    )
    ops = _ops(tmp_path, http)
    result = ops.reply({"message_id": "AAMk123", "html": _RENDERED})
    assert result["lookup"].startswith("ok:") and "different conversation" in result["lookup"]


def test_the_sender_fetch_selects_the_conversation_it_will_cross_check(
    tmp_path: Path,
) -> None:
    http = FakeGraph(source_from="scott@smd.services", conversation_id="AAQkCONV=")
    ops = _ops(tmp_path, http)
    ops.reply({"message_id": "AAMk123", "comment": "sure"})
    fetch = next(u for u, _a in http.auths if "/messages/AAMk123?" in u)
    assert "conversationId" in fetch and "from" in fetch


def test_the_send_row_carries_the_message_identity(tmp_path: Path) -> None:
    """The row a firm reads. Before ss#2499 this was ``message_id: ''`` on 9 of 9
    live rows, which is why the reconciler had nothing to join on.

    FALSIFIER: drop ``vendor_message_id`` from ``_OPS_AUDIT_KEYS`` and this fails
    while every other row assertion in the file stays green."""
    broker = _broker(tmp_path, FakeGraph())
    broker.handle(
        {
            "action": "msgraph_send",
            "payload": {"to": ["scott@smd.services"], "body_text": "hi"},
        },
        peer_pid=GATEWAY_PID,
        peer_uid=AGENT_UID,
    )
    meta = _meta(broker)
    assert meta["vendor_message_id"] == "<sent-copy@opslab.example>"
    assert meta["graph_message_id"] == "AAMkSENTCOPY="
    assert meta["lookup"] == "ok"
    assert meta["audit_row_token"]


def test_the_row_records_a_failed_lookup_rather_than_a_blank_id(tmp_path: Path) -> None:
    """A blank id and an unrecorded failure look identical from outside, and the
    second one is the state ss#2499 exists to end."""
    broker = _broker(tmp_path, FakeGraph(sent_items_status=500))
    broker.handle(
        {
            "action": "msgraph_send",
            "payload": {"to": ["scott@smd.services"], "body_text": "hi"},
        },
        peer_pid=GATEWAY_PID,
        peer_uid=AGENT_UID,
    )
    meta = _meta(broker)
    assert meta["lookup"].startswith("failed:")
    assert "vendor_message_id" not in meta
    # And the row is STILL joinable, from the header on the message itself.
    assert meta["audit_row_token"]


def test_the_audit_token_never_travels_back_to_the_agent(tmp_path: Path) -> None:
    """It is the key the reconciler treats as exact. An agent that learned this
    send's token could stamp a later message with it and hide the later one
    behind this row. The vendor ids DO go back — they are the agent's own
    message, and naming it to the firm is the point of resolving them."""
    broker = _broker(tmp_path, FakeGraph())
    response = broker.handle(
        {
            "action": "msgraph_send",
            "payload": {"to": ["scott@smd.services"], "body_text": "hi"},
        },
        peer_pid=GATEWAY_PID,
        peer_uid=AGENT_UID,
    )
    assert "audit_row_token" not in response and "sender_key" not in response
    assert response["vendor_message_id"] == "<sent-copy@opslab.example>"
    assert _meta(broker)["audit_row_token"]


def test_an_agentmail_shaped_result_writes_exactly_the_row_it_writes_today(
    tmp_path: Path,
) -> None:
    """The passthrough is a closed list of OPTIONAL keys, so the channel that
    contributes none of them is untouched. Asserted at the dispatcher rather than
    reasoned about, because "AgentMail is unaffected" is the kind of claim that
    is true right up until someone copies a result wholesale."""
    broker = _broker(tmp_path, FakeGraph())
    dispatch_transmit(
        broker,
        "agentmail_send",
        {"payload": {"to": ["scott@smd.services"], "text": "hi"}},
        send=lambda _p: {"message_id": "<am-1>", "recipients": ["scott@smd.services"], "inbox_id": "seat@agentmail.to"},
        reply=lambda _p: {},
        refused=MsGraphRefused,
        transport=MsGraphTransportError,
        attempted_for_send=lambda p: list(p.get("to") or []),
        identity_key="inbox_id",
    )
    meta = _meta(broker)
    assert meta["message_id"] == "<am-1>"
    assert not [k for k in ("vendor_message_id", "audit_row_token", "lookup") if k in meta]
