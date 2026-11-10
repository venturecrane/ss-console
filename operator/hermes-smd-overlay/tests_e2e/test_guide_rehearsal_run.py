"""The guide-role rehearsal: two enrolled people email a guide seat, at $0.

Named ``test_*`` because it is test-harness code (the repository's literal
census recognizes test code by name); it defines no test of its own, so pytest
collects nothing from it.

Run as its own process (``python -m tests_e2e.test_guide_rehearsal_run --root DIR --out
FILE``) by ``test_guide_rehearsal.py``, because Hermes' plugin manager and tool
registry are process globals and the harness's other seat (the self-serve
template, ``conftest.py``) already owns them in the pytest process.

WHAT RUNS IS THE SEAT'S OWN CODE PATH, with only the edges stubbed:

* the seat config is the authored guide seat (``operator/customers/smd-guide``)
  with SMD's addresses replaced by reserved ``example.test`` ones and three
  stores walled per person added (notes, leads, and a progress store only code
  writes);
* it is translated by the real ``bootstrap.translate`` into a profile under a
  temp copy of the seat volume, and every path knob points at the place it has on a seat
  (``agent-state.db``, ``requests.db``, ``held_replies.db``, the enrolled list,
  the persona home), so the inventory reads the volume a seat would have;
* each email goes through the webhook router's real ``pre_gateway_dispatch``
  (signature, sender authentication, the roster with the enrolled list, the
  request index, the inbound audit row), and the turn through Hermes' own
  ``AIAgent`` on the ``webhook`` platform, its hooks and its tool registry;
* the model is the scripted stub (``_model_stub.py``) and the broker is the
  stub broker (``_broker.py``). Nothing leaves loopback; nothing costs money.

Departures from a seat, each recorded: the store paths and the volume root live
in the temp directory (``shared.record_store.VOLUME_ROOT`` is pointed there);
the audit ledger is the direct SQLite transport (``SMD_D1_AUDIT_BINDING``);
``mcp_servers`` and ``platforms`` are dropped from the profile, as in the rest
of the harness.

It writes what it saw to ``--out`` (each turn's tool results and what the model
was handed) and leaves the temp seat under ``--root`` for the parent's canary
inventory and purge.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import yaml

OVERLAY = Path(__file__).resolve().parents[1]
GUIDE_YAML = OVERLAY.parent / "customers" / "smd-guide" / "customer.yaml"
PERSONA = "guide"

A = "rep-a@example.test"
B = "rep-b@example.test"
ADMIN = "admin@example.test"
A_MARK = "CANARY-A-k4r7v9"
B_MARK = "CANARY-B-p2w8j5"
SIGNING_SECRET = "e2e-guide-signing-secret-not-a-secret"
INBOX = "guide@agentmail.example.test"

#: The stores the rehearsal seat authors, relative to its volume.
STORES = {
    "rep-notes": {"partition": "sender"},
    "rep-leads": {"partition": "sender"},
    "rep-progress": {"partition": "sender", "managed": True},
}

#: The rehearsal's daily limit per listed sender (the guide authors 30).
DAILY_LIMIT = 3


def seat_yaml(volume: Path) -> str:
    """The authored guide seat, with reserved addresses and the stores added."""
    doc: dict[str, Any] = yaml.safe_load(GUIDE_YAML.read_text(encoding="utf-8"))
    doc["users"] = [{"email": ADMIN, "role": "principal", "full_name": "Admin Example"}]
    doc["scope"]["inbound_allow_from"] = [ADMIN]
    doc["scope"]["admins"] = [ADMIN]
    doc["escalation"] = {
        "red_flag_recipients": [ADMIN],
        "failure_recipients": [ADMIN],
        # Request cards carry who and what happened, never words.
        "request_card_text": False,
    }
    # The real guide seat switches per-sender memory off; the rehearsal keeps
    # it on, because removing a person's memory rows is part of what it proves.
    doc["memory"].pop("peer_memory_enabled", None)
    # A held reply ends in the fixed line; redrafts capped at the default.
    doc["send_policy"]["reply"]["held_notice"] = True
    # One listed sender gets DAILY_LIMIT emails a day answered.
    doc["sender_enrollment"]["daily_limit"] = DAILY_LIMIT
    doc["connectors"]["Email"]["inbox_address"] = INBOX
    doc["record_stores"] = [
        {"name": name, "path": str(volume / "rep" / name.removeprefix("rep-")), **policy}
        for name, policy in STORES.items()
    ]
    from tests_e2e._training_turns import author_training

    author_training(doc)
    return yaml.safe_dump(doc, sort_keys=False)


def _seat_env(root: Path, volume: Path) -> None:
    from tests_e2e.conftest import _STATE_PATHS, _STUB_API_KEY

    state = root / "state"
    # Every knob the harness points somewhere, then the ones the inventory reads
    # at the place each has on a seat (/opt/data/...).
    paths = {name: str(state / leaf) for name, leaf in _STATE_PATHS.items()}
    paths.update(
        {
            "SMD_D1_AGENT_STATE_BINDING": str(volume / "agent-state.db"),
            "SMD_REQUEST_INDEX_DB_PATH": str(volume / "requests.db"),
            "SMD_HELD_REPLY_DB_PATH": str(volume / "held_replies.db"),
            "SMD_HELP_STATE_DB_PATH": str(volume / "smd" / "help_state.db"),
            "SMD_SENDER_ENROLLMENT_ENROLLED": str(volume / "sender-enrollment" / "enrolled.json"),
            "SMD_D1_AUDIT_BINDING": str(volume / "audit" / "audit.db"),
            "SMD_SENDER_DAILY_DB_PATH": str(volume / "smd" / "sender_daily.db"),
        }
    )
    paths.pop("SMD_SENT_LINES_DIR", None)  # under the persona home, as on a seat
    for value in paths.values():
        Path(value).parent.mkdir(parents=True, exist_ok=True)
    os.environ.update(
        {
            "SMD_CUSTOMER_SLUG": "smd-guide",
            "CUSTOMER_SLUG": "smd-guide",
            "SMD_ACTIVE_PERSONA": PERSONA,
            "SMD_CUSTOMER_YAML_PATH": str(root / "customer.yaml"),
            "SMD_WEBHOOK_SIGNING_SECRET": SIGNING_SECRET,
            "SMD_WORKSPACE_BROKER_SOCKET": str(root / "b.sock"),
            "ANTHROPIC_API_KEY": _STUB_API_KEY,
            **paths,
        }
    )
    os.environ.pop("SMD_SENT_LINES_DIR", None)


def _enroll(volume: Path, *addresses: str) -> None:
    path = volume / "sender-enrollment" / "enrolled.json"
    entries = [
        {"address": a, "label": "rehearsal", "code_id": "c_000000000000", "enrolled_at": "x"}
        for a in addresses
    ]
    path.write_text(json.dumps({"schema": 1, "enrolled": entries}), encoding="utf-8")


class _Capture(logging.Handler):
    """Every log record the run emits, as the seat's log stream would carry it."""

    def __init__(self, path: Path) -> None:
        super().__init__(level=logging.DEBUG)
        self.handle_file = path.open("a", encoding="utf-8")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.handle_file.write(f"{record.name} {record.levelname} {record.getMessage()}\n")
        except Exception:  # noqa: BLE001 - a log line is never worth the run
            pass


def hermes_render(template: str, payload: dict) -> str:
    """Hermes' webhook ``_render_prompt`` dot-path substitution (pinned ref,
    ``gateway/platforms/webhook.py``): ``str(value)`` for each ``{a.b}``."""

    def resolve(m: re.Match) -> str:
        value: Any = payload
        for part in m.group(1).split("."):
            value = value.get(part, "{" + m.group(1) + "}") if isinstance(value, dict) else value
        return str(value)

    return re.sub(r"\{([a-zA-Z0-9_.]+)\}", resolve, template)


def _module(suffix: str) -> Any:
    found = [m for name, m in sys.modules.items() if name.endswith(suffix)]
    if not found:
        raise AssertionError(f"no loaded module ends with {suffix!r}")
    return found[0]


class Rehearsal:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.volume = root / "opt-data"
        self.turns: list[dict[str, Any]] = []
        self.model_log: list[dict[str, Any]] = []
        #: Each later slice's own evidence, by slice name.
        self.extra: dict[str, Any] = {}

    # -- the seat -----------------------------------------------------------

    def build(self) -> None:
        from tests_e2e._broker import StubBroker
        from tests_e2e._model_stub import AnthropicMessagesStub, ModelScript
        from tests_e2e._seat import build_seat_home
        from tests_e2e.conftest import DispatchRecorder, HookRecorder, _install_recorders

        self.volume.mkdir(parents=True)
        from shared import record_store

        # This seat's volume root is the temp volume, not the real one.
        record_store.VOLUME_ROOT = self.volume
        _seat_env(self.root, self.volume)
        _enroll(self.volume, A, B)
        self.script = ModelScript()
        self.model = AnthropicMessagesStub(self.script)
        self.broker = StubBroker(str(self.root / "b.sock"))
        self.profile = build_seat_home(
            self.root,
            model_base_url=self.model.base_url,
            extra_plugins=[],
            customer_yaml_text=seat_yaml(self.volume),
            hermes_root=self.volume,
            persona=PERSONA,
        )
        os.environ["HERMES_HOME"] = str(self.profile)
        self.hooks, self.dispatches, self.owners = HookRecorder(), DispatchRecorder(), {}
        _install_recorders(self.hooks, self.dispatches, self.owners)
        # The seat's log stream is INFO and above. (At DEBUG the model client
        # logs whole request bodies, a person's words included: a run with
        # DEBUG on would put them in the logs, which are named-retained.)
        logging.getLogger().addHandler(_Capture(self.root / "captured.log"))
        logging.getLogger().setLevel(logging.INFO)

        from hermes_cli.plugins import discover_plugins

        discover_plugins(force=True)
        self.router = _module("hermes_smd_webhook_router")
        from hermes_state import SessionDB

        self.session_db = SessionDB(self.profile / "state.db")
        from hermes_cli.runtime_provider import resolve_runtime_provider
        from run_agent import AIAgent

        runtime = resolve_runtime_provider(requested=None)
        # The email surface: the platform a seat's webhook turn runs on.
        self.agent = AIAgent(
            model="claude-sonnet-4-6",
            api_key=runtime.get("api_key"),
            base_url=runtime.get("base_url"),
            provider=runtime.get("provider"),
            api_mode=runtime.get("api_mode"),
            max_iterations=8,
            quiet_mode=True,
            load_soul_identity=True,
            skip_memory=True,
            platform="webhook",
            session_id="guide-boot",
            session_db=self.session_db,
            disabled_toolsets=["cronjob", "messaging", "clarify"],
        )

    def stop(self) -> None:
        self.model.stop()
        self.broker.stop()

    # -- one turn -----------------------------------------------------------

    def _payload(self, sender: str, mid: str, body: str) -> dict[str, Any]:
        domain = sender.rsplit("@", 1)[-1]
        return {
            "source": "agentmail",
            "event_type": "message.received",
            "message": {
                "inbox_id": INBOX,
                "message_id": mid,
                "thread_id": f"thr-{mid.strip('<>')}",
                "from": f"Rep Example <{sender}>",
                "to": [INBOX],
                "subject": "notes",
                "text": body,
                "extracted_text": body,
                "authentication_results": {
                    "spf": "pass",
                    "dkim": "pass",
                    "dmarc": "pass",
                    "dmarc_domain": domain,
                },
            },
        }

    def _deliver(self, payload: dict[str, Any], event_id: str) -> Any:
        """The router's own intake, header-bearing so the signature is checked,
        with the EMPTY dispatch session the live email path carries."""
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(SIGNING_SECRET.encode(), raw, hashlib.sha256).hexdigest()
        headers = {"X-Webhook-Signature": signature, "X-Webhook-Id": event_id}
        return self.router.on_pre_gateway_dispatch(
            payload=payload, raw_body=raw, headers=headers, session_id=""
        )

    def turn(
        self,
        label: str,
        calls: list[tuple[str, dict[str, Any]]],
        *,
        sender: str | None,
        body: str = "",
        final_text: str = "",
    ) -> dict[str, Any]:
        from tests_e2e._drive import fresh_ladder, fresh_session
        from tests_e2e._model_stub import ScriptedTurn

        fresh_ladder()
        session = fresh_session(self.agent)
        marker = f"[e2e-turn {uuid.uuid4().hex}]"
        self.script.reset(marker)
        if calls:
            self.script.push(ScriptedTurn(tool_calls=calls))
        if final_text:
            self.script.push(ScriptedTurn(text=f"{final_text}"))
        mark = self.dispatches.mark()
        hook_mark = self.hooks.mark()
        broker_mark = self.broker.mark()
        verdict: Any = None
        if sender is not None:
            mid = f"<{uuid.uuid4().hex}@mail.example.test>"
            payload = self._payload(sender, mid, f"{body} {marker}")
            verdict = self._deliver(payload, event_id=mid)
            from bootstrap.translate import _INBOUND_EMAIL_PROMPT

            prompt = hermes_render(_INBOUND_EMAIL_PROMPT, payload)
        else:
            prompt = f"Scheduled check of the stores. {body} {marker}"
        if verdict == {"action": "skip"}:
            # The router dropped it (the daily limit): a seat starts no turn.
            self.script.reset("")
            record = {"label": label, "sender": sender, "router_verdict": verdict}
            record["replies"] = self._replies_since(broker_mark, wait=False)
            self.turns.append(record)
            return record
        result = self.agent.run_conversation(prompt)
        offered = sorted({t for r in self.script.requests for t in r.tools})
        record = {
            "label": label,
            "session": session,
            "sender": sender,
            "router_verdict": verdict if isinstance(verdict, (dict, type(None))) else str(verdict),
            "exit": str((result or {}).get("turn_exit_reason") or ""),
            "pending": self.script.pending(),
            "offered": offered,
            # What session id Hermes handed pre_tool_call for this turn's store
            # calls: a fact about the pinned Hermes the wall does not rely on.
            "pre_tool_call_sessions": sorted(
                {
                    str(kw.get("session_id") or "")
                    for kw in self.hooks.fired_since(hook_mark, "pre_tool_call")
                    if str(kw.get("tool_name") or "").startswith("record_store")
                }
            ),
            "calls": [
                {
                    "tool": c.tool,
                    "args": c.args,
                    "result": c.result
                    if isinstance(c.result, str)
                    else json.dumps(c.result, default=str),
                }
                for c in self.dispatches.since(mark)
            ],
        }
        # The reply plugin settles a turn's end on its own worker thread; the
        # sends it makes (the text reply, or the held line) are read back here.
        record["replies"] = self._replies_since(broker_mark, wait=sender is not None)
        for req in [*self.script.requests, *self.script.foreign]:
            self.model_log.append({"turn": label, "tools": req.tools, "body": req.body})
        self.turns.append(record)
        return record

    def _replies_since(self, mark: int, *, wait: bool) -> list[str]:
        """The texts of the replies that reached the broker since ``mark``,
        waiting (bounded) for the end-of-turn worker when one is owed."""
        deadline = time.monotonic() + (10.0 if wait else 0.0)
        while True:
            texts = [
                str((r.get("payload") or r).get("text") or "")
                for r in self.broker.since(mark)
                if r.get("action") == "agentmail_reply"
            ]
            if texts or time.monotonic() >= deadline:
                return texts
            time.sleep(0.05)

    def post_cards(self) -> list[dict[str, Any]]:
        """The request cards this seat would post to SMD an hour from now, as the
        heartbeat leg builds them, captured instead of sent."""
        from shared import request_cards

        posted: list[dict[str, Any]] = []

        def capture(_url: str, _headers: dict, body: bytes, _timeout: float) -> int:
            posted.append(json.loads(body))
            return 200

        request_cards.send_due_cards(
            slug="smd-guide",
            key="e2e",
            url=request_cards.card_url("http://127.0.0.1:9/api/internal/heartbeat"),
            audit_db_path=os.environ["SMD_D1_AUDIT_BINDING"],
            post_fn=capture,
            now_fn=lambda: time.time() + 3 * 3600,
        )
        return posted

    def ledger_rows(self) -> list[dict[str, Any]]:
        """Every audit row this run wrote, as the work-health triage reads them."""
        import sqlite3

        conn = sqlite3.connect(os.environ["SMD_D1_AUDIT_BINDING"])
        conn.row_factory = sqlite3.Row
        try:
            return [
                dict(r) for r in conn.execute("SELECT id, ts, action_type, metadata FROM audit_log")
            ]
        finally:
            conn.close()


def run(root: Path) -> dict[str, Any]:
    r = Rehearsal(root)
    r.build()
    try:
        r.turn(
            "a-keeps-a-note",
            [
                (
                    "record_store_write",
                    {
                        "store": "rep-notes",
                        "name": "acme.md",
                        "content": f"Acme: met the CFO. {A_MARK}",
                    },
                ),
                (
                    "record_store_write",
                    {
                        "store": "rep-leads",
                        "name": "acme.md",
                        "content": f"Acme lead, warm. {A_MARK}",
                    },
                ),
                (
                    "record_peer_preference",
                    {"preference": f"Keep answers short. {A_MARK}", "source": "stated"},
                ),
            ],
            sender=A,
            body=f"Please keep this: Acme, met the CFO. {A_MARK}",
        )
        r.turn(
            "b-looks-across-the-wall",
            [
                ("record_store_list", {"store": "rep-notes"}),
                ("record_store_read", {"store": "rep-notes", "name": "acme.md"}),
                ("record_store_list", {"store": "rep-leads"}),
                (
                    "record_store_write",
                    {
                        "store": "rep-notes",
                        "name": "beta.md",
                        "content": f"Beta: call Tuesday. {B_MARK}",
                    },
                ),
                (
                    "record_store_write",
                    {"store": "rep-progress", "name": "p.json", "content": "{}"},
                ),
            ],
            sender=B,
            body=f"Note: Beta, call Tuesday. {B_MARK}",
        )
        r.turn(
            "a-reads-her-own",
            [
                ("record_store_list", {"store": "rep-notes"}),
                ("record_store_read", {"store": "rep-notes", "name": "acme.md"}),
            ],
            sender=A,
            body="What did I note about Acme?",
        )
        r.turn(
            "a-scheduled-turn-has-no-person",
            [("record_store_list", {"store": "rep-notes"})],
            sender=None,
        )
        # A reply the checks hold: a figure nobody read. Code tells her, in the
        # fixed line, that it could not answer.
        r.turn(
            "a-reply-is-held",
            [],
            sender=A,
            body="What is the Acme deal worth?",
            final_text="Your Acme deal is worth $95,000.",
        )
        # Her fourth email of the day is over the limit of three: not run, one
        # notice. Her fifth is not run and gets nothing.
        r.turn("a-over-the-limit", [], sender=A, body=f"One more question. {A_MARK}")
        r.turn("a-over-again", [], sender=A, body=f"And another. {A_MARK}")
        cards = r.post_cards()
        rows = r.ledger_rows()

        from tests_e2e._training_turns import run_training

        r.extra["training"] = run_training(r, A, B, A_MARK, B_MARK)
    finally:
        r.stop()
    (root / "cards.json").write_text(json.dumps(cards, default=str), encoding="utf-8")
    (root / "model-requests.jsonl").write_text(
        "".join(json.dumps(m, default=str) + "\n" for m in r.model_log), encoding="utf-8"
    )
    (root / "broker-requests.json").write_text(
        json.dumps(r.broker.since(0), default=str), encoding="utf-8"
    )
    return {
        "volume": str(r.volume),
        "profile": str(r.profile),
        "turns": r.turns,
        "cards": cards,
        "ledger_rows": rows,
        "overlay_tools": sorted(n for n, o in r.owners.items() if o == "hermes-smd-overlay"),
        **r.extra,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    from tests_e2e._seat import install_network_guard, scrub_environment

    scrub_environment()
    os.environ["HERMES_DISABLE_LAZY_INSTALLS"] = "1"
    install_network_guard()
    src = os.environ.get("HERMES_AGENT_SRC", "")
    if src and src not in sys.path:
        sys.path.insert(0, src)
    evidence = run(Path(args.root))
    Path(args.out).write_text(json.dumps(evidence, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
