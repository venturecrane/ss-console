"""The vendors the mailbox keeper talks to, faked at the wire; everything of
ours is real.

A test builds the REAL ``Broker`` (as test_agentmail_send.py builds it) with the
real ``WorkspaceOperations`` over ``FakeGmail`` (the slice of the Gmail v1 client
the broker calls, holding threads that change as they are modified), the real
``AgentMailOps`` over ``FakeAgentMail`` (an HTTP opener answering the inbox
listing, the send and the sent-message listing), and the real
``model_call.call`` over ``FakeModel`` (``model_call.https_post`` replaced: a
recorded Messages API answer per thread). Nothing of ours is stubbed.

Addresses are example.* only (the client identity gate).
"""

from __future__ import annotations

import base64
import email
import json
import time
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Any

import workspace_broker.operations as ops_mod
from workspace_broker import mailbox_keeper_mail as mail
from workspace_broker import mailbox_keeper_store as store
from workspace_broker import model_call
from workspace_broker.agentmail_ops import AgentMailOps
from workspace_broker.operations import WorkspaceOperations
from workspace_broker.server import Broker

GATEWAY_PID = 42
AGENT_UID = 1000
SEAT = "smd-office"
SEAT_INBOX = "smd-office@agentmail.to"
MAILBOX = "owner@owner.example"
ALIAS = "scott@smd.services"
OWNERS = f"{MAILBOX},{ALIAS}"
RECIPIENT = ALIAS
ALERT_FROM = "SMD Services Ops <team@smd.services>"
GOOD_AUTH = "mx.google.com; dkim=pass header.i=@smd.services header.s=resend header.b=AbC; spf=pass"
NO_DKIM = "mx.google.com; dkim=none; spf=softfail smtp.mailfrom=evil.example"
UUID1 = "11111111-2222-4333-8444-555555555555"
NOW_MS = int(time.time() * 1000)
HOUR = 3_600_000


def seat_yaml(settings_extra: str = "", schedule: str = "47 * * * *") -> str:
    return f"""
schema_version: 1
connectors:
  Email:
    adapter: agentmail
    enabled: true
    inbox_address: {SEAT_INBOX}
google_auth:
  mode: dwd
  subject: {MAILBOX}
  scopes: [https://www.googleapis.com/auth/gmail.modify]
  managed_mailboxes:
    - address: {MAILBOX}
      send_as: [{ALIAS}, {MAILBOX}]
scope:
  inbound_allow_from: [{ALIAS}, {MAILBOX}]
  admins: [{ALIAS}]
safety:
  sticky_stop:
    cost_cap_daily_cents: 300
personas:
  - name: smd-operations
    skills:
      - name: mailbox-keeper
        version: pending
        initiation: {{manual: false, scheduled: true, webhook: false}}
        enabled: true
        settings:
          mailbox: {MAILBOX}
          owner_addresses: '{OWNERS}'
          digest_recipient: {RECIPIENT}
          needs_you_after_hours: 24
          window_days: 14
          send_after_hour: 0
{settings_extra}
    cron:
      - skill: mailbox-keeper
        schedule: '{schedule}'
        pre_run: pre_run.py
        wake_policy: pre_run_decides
"""


CLASSIFY_SETTINGS = """          alert_label: 'SMD Ops'
          classify: true
          classify_model: claude-sonnet-5-5
          receipts_label: Receipts
          cost_fence_cents: 150
          draft_signoff: "Thanks,\\nScott"
          watch_senders: '@tax.example'"""


# ------------------------------------------------------------------ messages


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def alert_html(happened: str, todo: str, detail: str) -> str:
    # alertHtml in workers/fleet-alerts/src/alert-words.ts, byte for byte in shape.
    return (
        f"<p><strong>WHAT HAPPENED:</strong> {happened}</p>"
        f"<p><strong>WHAT YOU NEED TO DO:</strong> {todo}</p>"
        "<hr><p><strong>DETAILS, for the agent who picks this up</strong><br>"
        f"Seat: example-seat<br>Condition: heartbeat red<br>Detail: {detail}</p>"
    )


def msg(
    mid: str,
    thread: str,
    age_hours: float,
    sender: str,
    subject: str,
    *,
    html: str | None = None,
    text: str | None = None,
    labels: tuple[str, ...] = ("INBOX",),
    in_reply_to: str = "",
    auth: str = GOOD_AUTH,
    headers: tuple[tuple[str, str], ...] = (),
    to: str = MAILBOX,
) -> dict[str, Any]:
    hdrs = [
        {"name": "From", "value": sender},
        {"name": "To", "value": to},
        {"name": "Subject", "value": subject},
        # SES rewrites the alert's own Message-ID on delivery: never keyed on.
        {"name": "Message-ID", "value": f"<0100{mid}@email.example>"},
        {"name": "Authentication-Results", "value": auth},
    ]
    if in_reply_to:
        hdrs += [{"name": "In-Reply-To", "value": in_reply_to}, {"name": "References", "value": in_reply_to}]
    hdrs += [{"name": k, "value": v} for k, v in headers]
    parts = []
    if text is not None:
        parts.append({"mimeType": "text/plain", "body": {"data": b64(text)}})
    if html is not None:
        parts.append({"mimeType": "text/html", "body": {"data": b64(html)}})
    return {
        "id": mid,
        "threadId": thread,
        "labelIds": list(labels),
        "internalDate": str(NOW_MS - int(age_hours * HOUR)),
        "payload": {"mimeType": "multipart/alternative", "headers": hdrs, "parts": parts},
    }


def ops_msg(mid: str, thread: str, age: float, subject: str, happened: str = "Something needs you.", **kw: Any):
    return msg(mid, thread, age, ALERT_FROM, subject, html=alert_html(happened, "Look.", "x"), **kw)


def thread(tid: str, *messages: dict[str, Any]) -> dict[str, Any]:
    return {"id": tid, "messages": list(messages)}


# ------------------------------------------------------------------ Gmail


class _Call:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


def _header(m: dict[str, Any], name: str) -> str:
    for h in m["payload"]["headers"]:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


class FakeGmail:
    """The slice of the Gmail v1 client the broker calls. Threads change as the
    broker modifies them, so a later search sees what an earlier write did."""

    def __init__(self, threads: list[dict[str, Any]], *, page_size: int = 2, fail_on: str = "") -> None:
        self.by_id = {t["id"]: json.loads(json.dumps(t)) for t in threads}
        self.page_size = page_size
        self.fail_on = fail_on
        self.trashed: list[str] = []
        self.list_calls: list[dict[str, Any]] = []
        self.label_ids: dict[str, str] = {"INBOX": "INBOX"}
        self.modified: list[tuple[str, list[str], list[str]]] = []
        self.made_drafts: list[dict[str, Any]] = []
        self.on_get: Any = None

    def users(self):
        return self

    def messages(self):
        return _Messages(self)

    def threads(self):
        return _Threads(self)

    def drafts(self):
        return _Drafts(self)

    def labels(self):
        return _Labels(self)

    def inbox(self, tid: str) -> bool:
        return any("INBOX" in m["labelIds"] for m in self.by_id[tid]["messages"])

    def label_names(self, tid: str) -> set[str]:
        names = {v: k for k, v in self.label_ids.items()}
        return {names.get(x, x) for m in self.by_id[tid]["messages"] for x in m["labelIds"]}


class _Messages:
    def __init__(self, g: FakeGmail) -> None:
        self.g = g

    def list(self, **kw):
        self.g.list_calls.append(kw)
        if self.g.fail_on == "list":
            raise OSError("gmail down")
        q = str(kw.get("q") or "")
        sender = q.split("from:", 1)[1].split()[0] if "from:" in q else ""
        ids = [
            {"id": m["id"], "threadId": t["id"]}
            for t in self.g.by_id.values()
            if t["id"] not in self.g.trashed
            for m in t["messages"]
            if ("in:inbox" not in q or "INBOX" in m["labelIds"]) and (not sender or sender in _header(m, "From"))
        ]
        start = int(kw.get("pageToken") or 0)
        page = ids[start : start + self.g.page_size]
        nxt = start + self.g.page_size
        body: dict[str, Any] = {"messages": page}
        if nxt < len(ids):
            body["nextPageToken"] = str(nxt)
        return _Call(lambda: body)


class _Threads:
    def __init__(self, g: FakeGmail) -> None:
        self.g = g

    def get(self, *, userId, id, format):
        if self.g.fail_on == "get":
            raise OSError("thread read failed")
        if self.g.on_get:
            self.g.on_get(self.g, id)
        return _Call(lambda: json.loads(json.dumps(self.g.by_id[id])))

    def trash(self, *, userId, id):
        self.g.trashed.append(id)
        return _Call(lambda: {"id": id, "messages": self.g.by_id[id]["messages"]})

    def modify(self, *, userId, id, body):
        if self.g.fail_on == "modify":
            raise OSError("modify failed")
        add, remove = list(body.get("addLabelIds") or []), list(body.get("removeLabelIds") or [])
        self.g.modified.append((id, add, remove))
        for m in self.g.by_id[id]["messages"]:
            m["labelIds"] = [x for x in m["labelIds"] if x not in remove] + [x for x in add if x not in m["labelIds"]]
        return _Call(lambda: {"id": id})


class _Labels:
    def __init__(self, g: FakeGmail) -> None:
        self.g = g

    def list(self, *, userId):
        return _Call(lambda: {"labels": [{"id": v, "name": k} for k, v in self.g.label_ids.items()]})

    def create(self, *, userId, body):
        if self.g.fail_on == "label_create":
            raise OSError("label create failed")
        label_id = f"Label_{len(self.g.label_ids)}"
        self.g.label_ids[body["name"]] = label_id
        return _Call(lambda: {"id": label_id, "name": body["name"]})


class _Drafts:
    def __init__(self, g: FakeGmail) -> None:
        self.g = g

    def create(self, *, userId, body):
        raw = body["message"]["raw"]
        parsed = email.message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        draft_id = f"r-{len(self.g.made_drafts) + 1}"
        record = {"id": draft_id, "thread_id": body["message"].get("threadId"), "headers": dict(parsed.items())}
        record["body"] = parsed.get_payload(decode=True).decode()
        self.g.made_drafts.append(record)
        tid = record["thread_id"]
        if tid in self.g.by_id:
            self.g.by_id[tid]["messages"].append(
                {
                    "id": draft_id,
                    "threadId": tid,
                    "labelIds": ["DRAFT"],
                    "internalDate": str(NOW_MS),
                    "payload": {"headers": []},
                }
            )
        return _Call(lambda: {"id": draft_id, "message": {"id": draft_id, "threadId": tid}})


# ------------------------------------------------------------------ AgentMail


class FakeAgentMail:
    """An HTTP opener for AgentMail: the inbox listing, the send, and the sent
    listing (which returns this inbox's sends, plus ``elsewhere``: sends another
    machine made)."""

    def __init__(self) -> None:
        self.sends: list[dict[str, Any]] = []
        self.elsewhere: list[dict[str, Any]] = []
        self.listings: list[dict[str, list[str]]] = []
        self.fail_listing = False

    def sent_rows(self) -> list[dict[str, Any]]:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        own = [
            {"message_id": f"msg_{i}", "labels": ["sent"], "timestamp": now, "to": s["to"], "subject": s["subject"]}
            for i, s in enumerate(self.sends, 1)
        ]
        return own + self.elsewhere

    def __call__(self, request, timeout=None):
        url = urllib.parse.urlsplit(request.full_url)
        if request.get_method() == "GET" and url.path.endswith("/messages"):
            if self.fail_listing:
                raise urllib.error.HTTPError(request.full_url, 503, "unavailable", {}, None)
            self.listings.append(urllib.parse.parse_qs(url.query))
            return _Response(json.dumps({"count": len(self.sent_rows()), "messages": self.sent_rows()}))
        if url.path.endswith("/messages/send"):
            self.sends.append(json.loads(request.data.decode()))
            return _Response(json.dumps({"message_id": f"msg_{len(self.sends)}", "thread_id": "th_1"}))
        return _Response(json.dumps({"inboxes": [{"inbox_id": SEAT_INBOX}]}))


class _Response:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> bytes:
        return self._text.encode()

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False


# ------------------------------------------------------------------ the model


class FakeModel:
    """``model_call.https_post`` as the Messages API answers it: one recorded
    answer per thread, chosen by the SUBJECT line of the request. An answer is
    a dict (sent as the model's JSON text), a str (sent as raw text), or the
    string ``"!error"`` (the call fails)."""

    def __init__(self, answers: dict[str, Any], *, input_tokens: int = 3000, output_tokens: int = 600) -> None:
        self.answers = answers
        self.requests: list[dict[str, Any]] = []
        self.usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}

    def __call__(self, host, method, path, headers, body, deadline):
        assert host == model_call.API_HOST and path == model_call.API_PATH
        assert headers["x-api-key"] == "sk-test-key"
        request = json.loads(body.decode())
        self.requests.append(request)
        user = request["messages"][0]["content"]
        subject = next(line[len("SUBJECT: ") :] for line in user.splitlines() if line.startswith("SUBJECT: "))
        answer = next((v for k, v in self.answers.items() if k in subject), {"kind": "notice", "what": "A notice."})
        if answer == "!error":
            raise model_call.ModelCallError("model call failed: HTTP 529")
        text = answer if isinstance(answer, str) else json.dumps(answer)
        stop = "refusal" if answer == "!refusal" else "end_turn"
        return {
            "id": f"msg_{len(self.requests)}",
            "type": "message",
            "role": "assistant",
            "model": request["model"],
            "content": [{"type": "text", "text": "" if stop == "refusal" else text}],
            "stop_reason": stop,
            "usage": self.usage,
        }


class RecordingLedger:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def append(self, row: dict[str, Any]) -> str:
        self.rows.append(row)
        return f"row-{len(self.rows)}"


def materialize_key(path: Path) -> None:
    """The REAL boot materializer (the overlay's copy, which finds the pricing
    table beside itself), with a test key."""
    from shared import model_call as overlay_model_call

    assert overlay_model_call.materialize_credential(path, environ={"ANTHROPIC_API_KEY": "sk-test-key"})


def build_broker(
    tmp_path: Path, monkeypatch, gmail: FakeGmail, http: FakeAgentMail, yaml_text: str, model=None
) -> Broker:
    customer = tmp_path / "customer.yaml"
    customer.write_text(yaml_text)
    credential = tmp_path / "agentmail.json"
    credential.write_text("am_test_key")
    monkeypatch.setattr(ops_mod, "service", lambda *a, **k: gmail)
    monkeypatch.setenv(store.STATE_DIR_ENV, str(tmp_path / "keeper"))
    monkeypatch.setattr(mail, "SLEEP", lambda _s: None)
    key_file = tmp_path / "model.json"
    materialize_key(key_file)
    monkeypatch.setenv("SMD_MODEL_CREDENTIAL_PATH", str(key_file))
    if model is not None:
        monkeypatch.setattr(model_call, "https_post", model)
    else:
        monkeypatch.setattr(model_call, "https_post", _no_model)
    broker = Broker.__new__(Broker)
    broker.customer_slug = SEAT
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.ledger = RecordingLedger()
    broker.customer_path = customer
    broker.operations = WorkspaceOperations(tmp_path / "google.json", customer)
    broker.agentmail = AgentMailOps(credential, customer, SEAT, opener=http)
    return broker


def _no_model(*_a, **_k):
    raise AssertionError("no model call is allowed in this test")


def run(broker: Broker, mode: str = "scheduled", uid: int = AGENT_UID, **extra: Any) -> dict[str, Any]:
    if mode != "dry_run":
        extra.setdefault("model", "allowed")  # what the pre_run sends when the seat's cost breaker allows it
    return broker.handle(
        {"action": "mailbox_keeper_run", "mode": mode, **extra}, peer_pid=GATEWAY_PID + 7, peer_uid=uid
    )
