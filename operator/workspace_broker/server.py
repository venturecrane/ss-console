"""Unix-socket broker with peer-bound, single-use capability grants.

The verb surface lives in ``verbs.py``: every verb declares who may call it
(gateway PID, agent uid, root, or anyone for the read-only ``health``) and the
dispatcher there checks that BEFORE calling a handler. Handlers are grouped by
what they write: ``audit_verbs`` (one-pinned-action_type appenders),
``establish_verbs`` (ADR 0085), ``job_verbs`` (B1 control plane),
``transmit_verbs`` (ss#2258, the only transmit path), ``workspace_verbs`` (the
grant pair), ``medchron_verbs`` (routine 11). This module keeps what is not a
verb: the grant store, the broker's construction from its environment, the
agent-uid resolver, and the socket server.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import socket
import socketserver
import struct
import threading
import time
from pathlib import Path
from typing import Any

from .agentmail_auth import materialize_credential as materialize_agentmail_credential
from .agentmail_ops import AgentMailOps
from .audit_ledger import LedgerWriter
from .canon import canonical as _canonical
from .establishment import EstablishmentStore
from .google_auth import materialize_credential
from .job_ledger import JobLedgerWriter
from .medchron_verbs import MedchronVerbs
from .msgraph_auth import materialize_credential as materialize_msgraph_credential
from .msgraph_auth import materialize_read_credential as materialize_msgraph_read_credential
from .msgraph_ops import MsGraphOps
from .operations import WorkspaceOperations
from .request_errors import error_response_for
from .verbs import dispatch

MAX_REQUEST_BYTES = 1_048_576
GRANT_TTL_SECONDS = 10


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class GrantStore:
    """Mint and consume payload-bound grants."""

    def __init__(self) -> None:
        self._key = secrets.token_bytes(32)
        self._pending: dict[str, int] = {}
        self._lock = threading.Lock()

    def mint(self, claims: dict[str, Any]) -> str:
        nonce = secrets.token_urlsafe(18)
        now = int(time.time())
        body = {**claims, "nonce": nonce, "iat": now, "exp": now + GRANT_TTL_SECONDS}
        encoded = _b64encode(_canonical(body))
        signature = _b64encode(hmac.new(self._key, encoded.encode(), hashlib.sha256).digest())
        with self._lock:
            self._pending[nonce] = body["exp"]
        return f"{encoded}.{signature}"

    def consume(self, token: str, expected: dict[str, Any]) -> dict[str, Any]:
        encoded, separator, signature = token.partition(".")
        if not separator:
            raise ValueError("malformed grant")
        actual = hmac.new(self._key, encoded.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(actual, _b64decode(signature)):
            raise ValueError("invalid grant signature")
        claims = json.loads(_b64decode(encoded))
        now = int(time.time())
        if claims.get("exp", 0) < now:
            raise ValueError("expired grant")
        for key, value in expected.items():
            if claims.get(key) != value:
                raise ValueError(f"grant {key} mismatch")
        nonce = claims.get("nonce")
        with self._lock:
            expiry = self._pending.pop(nonce, None)
        if expiry is None or expiry < now:
            raise ValueError("grant already used or unknown")
        return claims

    def sign_receipt(self, receipt: dict[str, Any]) -> str:
        """Sign execution evidence with the broker-only grant key."""
        return _b64encode(hmac.new(self._key, _canonical(receipt), hashlib.sha256).digest())


class Broker:
    """Authorize and execute reviewed Workspace operations."""

    # Class default so instances built via ``__new__`` (tests) and pre-WS5
    # images without SMD_AUDIT_DB_PATH have a defined, audit-disabled ledger.
    ledger: LedgerWriter | None = None
    # The path behind the escalation raise witness. Same default-disabled
    # posture as ``ledger``: a ``__new__`` instance or a pre-WS5 image has no
    # audit DB to consult, and send_witness reads that as "no witness exists
    # here and never did" — it allows the raise rather than converting an
    # audit-disabled seat into one that cannot escalate at all.
    audit_db_path: str | None = None
    # B1 job-control plane. Same default-disabled posture as ``ledger`` so
    # instances built via ``__new__`` (tests) and pre-B1 images have a defined,
    # job-disabled ledger.
    job_ledger: JobLedgerWriter | None = None
    # ADR 0021 Stream B: agent uid for the suppressed_wake_append heartbeat
    # verb. None (the ``__new__``/pre-heartbeat default) keeps the verb
    # fail-closed until __init__ resolves it from the gateway process.
    agent_uid: int | None = None
    # ADR 0085 establishment spool. Same default-disabled posture as the
    # ledgers: instances built via ``__new__`` (tests) and pre-0085 images have
    # a defined, establishment-disabled store. The class-level lock is shared
    # by design — one broker per process, and it only serializes the three
    # establish_* verbs (their sweep + read-modify-write of a staging set).
    establishment: EstablishmentStore | None = None
    _establish_lock = threading.Lock()
    # ss#2258: the ONLY transmit path on the Machine. Same default-disabled
    # posture as the ledgers — an instance built via ``__new__`` (tests) or an
    # image without the send credential configured has a defined, send-disabled
    # value, and the verbs below fail closed rather than reaching for a key.
    agentmail: AgentMailOps | None = None
    medchron: MedchronVerbs | None = None
    msgraph: MsGraphOps | None = None

    def __init__(self) -> None:
        self.socket_path = Path(os.environ["SMD_WORKSPACE_BROKER_SOCKET"])
        self.customer_path = Path(os.environ["SMD_CUSTOMER_YAML"])
        self.credential_path = Path(os.environ["SMD_WORKSPACE_CREDENTIAL_PATH"])
        self.customer_slug = os.environ["CUSTOMER_SLUG"]
        self.gateway_pid = int(os.environ["SMD_GATEWAY_PID"])
        materialize_credential(self.credential_path)
        self.operations = WorkspaceOperations(self.credential_path, self.customer_path)
        self.grants = GrantStore()
        # OP-P1-4: this broker also holds the only RW handle on the per-customer
        # audit ledger when SMD_AUDIT_DB_PATH is set (the entrypoint owns the
        # file to this uid; the agent uid can read but not write it). When
        # unset, the audit ledger is direct-write (legacy / pre-WS5 image) and
        # this broker does not touch it.
        audit_db_path = os.environ.get("SMD_AUDIT_DB_PATH")
        self.ledger = LedgerWriter(audit_db_path) if audit_db_path else None
        # Kept for the escalation raise witness, which re-reads this file
        # read-only (mode=ro) to confirm THIS broker dispatched to a person
        # before it will record a raise. See send_witness.dispatched_to_a_person.
        self.audit_db_path = audit_db_path
        # ADR 0021 Stream B: cron pre_run scripts run as subprocess CHILDREN of
        # the gateway (hermes cron/scheduler.py `subprocess.run`), so they share
        # the agent uid but never the gateway PID. The narrow heartbeat verb
        # below gates on uid instead — resolved lazily via _resolve_agent_uid()
        # because at BROKER start the gateway PID still belongs to the root
        # entrypoint (the exec-drop to the agent user happens after the broker
        # launches; live-caught on pilot-smokeball 2026-07-06).
        self.agent_uid = None
        # B1: the job ledger folds into the SAME broker-owned DB file (one
        # mount, one uid boundary). Mutable control state, distinct table set;
        # the audit_log append-only guarantee is untouched (no job verb writes
        # audit_log). Disabled when the audit DB is unconfigured (pre-B1 image).
        self.job_ledger = JobLedgerWriter(audit_db_path) if audit_db_path else None
        # WP-A escalation ledger: append-only JSONL of escalation telemetry
        # (fired/acked/handed_off/resolved), written ONLY by this broker so the
        # agent uid cannot forge an ack that silences a deadline alarm. It lives
        # beside the audit DB (same /run/smd-audit bind the broker already
        # writes; the agent reads the /opt/data/audit twin via audit-readers).
        # An explicit SMD_ESCALATION_LEDGER_PATH wins (the test seam).
        explicit_ledger = os.environ.get("SMD_ESCALATION_LEDGER_PATH")
        if explicit_ledger:
            self.escalation_ledger_path: str | None = explicit_ledger
        elif audit_db_path:
            self.escalation_ledger_path = str(Path(audit_db_path).parent / "escalation-ledger.jsonl")
        else:
            self.escalation_ledger_path = None
        self._escalation_lock = threading.Lock()
        # ADR 0085 (ss#2161/#2162): the establishment spool, when the
        # entrypoint created it and exported its path. Requires the audit
        # ledger — an establishment that cannot be audited must not run, so an
        # audit-disabled broker keeps the verbs fail-closed.
        establish_spool = os.environ.get("SMD_ESTABLISH_SPOOL_DIR")
        if establish_spool and self.ledger is not None:
            # ss-console#2529: the pending-rules table rides the SAME
            # broker-owned DB file the audit log and the job ledger use — one
            # mount, one uid boundary. It has to outlive the spool's 30-minute
            # TTL, because a rule proposed on Friday is confirmed on Monday and
            # every inbound email is its own session.
            self.establishment = EstablishmentStore(
                establish_spool,
                self.ledger,
                pending_db_path=audit_db_path,
                # ss-console#2536: an ACT is proposed only with the values the
                # firm authored, so the broker reads them itself, from the same
                # trusted customer.yaml it already holds for the mail identity.
                customer_path=self.customer_path,
            )
        else:
            self.establishment = None
        # ss#2614 routine 11: the chronology-package job ledger rides the same
        # broker-owned DB file (states, counts, cents; never content). The
        # queue dir is root:workspace-broker 0770 on the volume. Requires the
        # audit ledger: a job that cannot be recorded must not be queued.
        self.medchron = MedchronVerbs.build(
            self, audit_db_path=audit_db_path, queue_dir=os.environ.get("SMD_MEDCHRON_QUEUE_DIR")
        )
        # ss#2258: AgentMail transmit moves behind this uid boundary. The gateway
        # keeps an inbox-scoped key with message_send/draft_send WITHHELD, so the
        # agent process can read and draft but is vendor-refused from
        # transmitting; the send-capable key exists only in the 0600 file below.
        # Requires the audit ledger by design — a send that cannot be recorded
        # must not happen, which is the whole lesson of the four unaudited
        # messages that created this verb.
        agentmail_credential = os.environ.get("SMD_AGENTMAIL_CREDENTIAL_PATH")
        if agentmail_credential and self.ledger is not None:
            credential = Path(agentmail_credential)
            materialize_agentmail_credential(credential)
            self.agentmail = AgentMailOps(credential, self.customer_path, self.customer_slug)
        else:
            self.agentmail = None
        # ss#2258 msgraph wave. Same verb shape, same recipient fence, same
        # broker-written row — but only ONE of the two AgentMail fences, and the
        # difference is the vendor's, not ours: a Graph app-only token is
        # ``/.default`` (every permission the app registration holds), so there is
        # no send-incapable variant of the credential the agent already needs for
        # the delta poller and its mail tools. ``msgraph_auth`` carries the full
        # argument and what would close it. Ledger required for the same reason as
        # above: a send that cannot be recorded must not happen.
        msgraph_credential = os.environ.get("SMD_MSGRAPH_CREDENTIAL_PATH")
        if msgraph_credential and self.ledger is not None:
            graph_credential = Path(msgraph_credential)
            materialize_msgraph_credential(graph_credential)
            # overlay#280: the reply verb's sender-verification GET cannot run on
            # the send app under the two-app fence, so the broker also carries the
            # read app's credential in a second file. The ROOT entrypoint is the
            # only real writer (this process runs under env -i without secrets, so
            # the materialize call below is a shape-parity no-op); the file on
            # disk is what survives respawns.
            read_credential_env = os.environ.get("SMD_MSGRAPH_READ_CREDENTIAL_PATH")
            read_credential: Path | None = None
            if read_credential_env:
                read_credential = Path(read_credential_env)
                materialize_msgraph_read_credential(read_credential)
            self.msgraph = MsGraphOps(graph_credential, self.customer_path, read_credential_path=read_credential)
        else:
            self.msgraph = None

    def _resolve_agent_uid(self) -> int | None:
        """Resolve (and cache) the agent uid for the heartbeat verb.

        Precedence: explicit SMD_AGENT_UID from the entrypoint (which knows the
        agent user while still root), then a request-time stat of the gateway
        PID — by the time any pre_run fires, the entrypoint has exec-dropped
        into the agent user under the same PID. uid 0 is never accepted: the
        agent never runs as root, and a pre-exec-drop stat would read the root
        entrypoint. Unresolvable → None → the verb stays fail-closed.
        """
        if self.agent_uid is not None:
            return self.agent_uid
        env_uid = os.environ.get("SMD_AGENT_UID", "").strip()
        if env_uid.isdigit() and int(env_uid) != 0:
            self.agent_uid = int(env_uid)
            return self.agent_uid
        try:
            uid = os.stat(f"/proc/{self.gateway_pid}").st_uid
        except OSError:
            return None
        if uid == 0:
            return None
        self.agent_uid = uid
        return self.agent_uid

    def handle(self, request: dict[str, Any], peer_pid: int, peer_uid: int | None = None) -> dict[str, Any]:
        """One request in, one reply out. The verb table (``verbs.py``) owns
        both the gate and the dispatch; this method exists so the socket
        handler and every test keep calling ``broker.handle``."""
        return dispatch(self, request, peer_pid, peer_uid)


class RequestHandler(socketserver.StreamRequestHandler):
    """One newline-delimited JSON request per connection."""

    def handle(self) -> None:
        peer_pid, peer_uid, _ = struct.unpack(
            "3i",
            self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12),
        )
        raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            response = {"ok": False, "error": "request_too_large"}
        else:
            try:
                request = json.loads(raw)
                response = self.server.broker.handle(request, peer_pid, peer_uid)  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001 - every exception becomes one bounded reply shape
                response = error_response_for(exc)
        self.wfile.write(_canonical(response) + b"\n")


class ThreadedUnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> None:
    broker = Broker()
    broker.socket_path.parent.mkdir(parents=True, exist_ok=True)
    broker.socket_path.unlink(missing_ok=True)
    with ThreadedUnixServer(str(broker.socket_path), RequestHandler) as server:
        server.broker = broker  # type: ignore[attr-defined]
        # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions - Broker owner and connector group require socket access; all other users remain denied.
        os.chmod(broker.socket_path, 0o660)
        server.serve_forever()


if __name__ == "__main__":
    main()
