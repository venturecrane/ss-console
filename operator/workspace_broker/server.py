"""Unix-socket broker with peer-bound, single-use capability grants."""

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
from .agentmail_ops import (
    AgentMailOps,
    AgentMailRefused,
    AgentMailTransportError,
    collect_recipients,
)
from .audit_ledger import LedgerWriter
from .corrections import PROPOSED_STATUS, build_correction_row
from .establishment import EstablishmentStore
from .google_auth import materialize_credential
from .medchron_verbs import MedchronVerbs, medchron_dispatch
from .job_ledger import LEASE_TTL_SECONDS, JobLedgerWriter, now_and_lease_cutoff
from .msgraph_auth import materialize_credential as materialize_msgraph_credential
from .msgraph_auth import materialize_read_credential as materialize_msgraph_read_credential
from .msgraph_ops import MsGraphOps, MsGraphRefused, MsGraphTransportError
from .msgraph_ops import collect_recipients as collect_msgraph_recipients
from .operations import WorkspaceOperations
from .request_errors import error_response_for
from .send_witness import append_escalation_event

MAX_REQUEST_BYTES = 1_048_576
GRANT_TTL_SECONDS = 10


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


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
        signature = _b64encode(
            hmac.new(self._key, encoded.encode(), hashlib.sha256).digest()
        )
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
        return _b64encode(
            hmac.new(self._key, _canonical(receipt), hashlib.sha256).digest()
        )


#: What a transmit ops verb may contribute to its own audit row, by name.
#: A closed list rather than "copy the result": the result is a vendor-shaped
#: dict, and a wholesale copy would let a future field land in the ledger
#: without anyone deciding it should.
#:
#: ``sender_key``       ss#2497 — who a reply answered, hashed.
#: ``audit_row_token``  ss#2499 — the ULID stamped on the message as
#:                      ``X-SMD-Audit-Row``. The join that survives a failed
#:                      vendor-id lookup, because it is on the message itself.
#: ``vendor_message_id`` ss#2497's field name for the provider's own id.
#: ``graph_message_id`` the mailbox-local Graph id, which is what a Graph query
#:                      addresses; the vendor id is the RFC2822 one, which is
#:                      what survives outside the mailbox. Both, because a firm
#:                      asking "is this ours?" may hold either.
#: ``lookup``           whether that resolution succeeded, and why not. Recorded
#:                      so a blank id is never mistaken for a mailbox that had
#:                      nothing to find.
_OPS_AUDIT_KEYS: tuple[str, ...] = (
    "sender_key",
    "audit_row_token",
    "vendor_message_id",
    "graph_message_id",
    "lookup",
)

#: Of those, the ones that stay in the ledger and never travel back to the agent.
_AUDIT_ONLY_KEYS = frozenset({"sender_key", "audit_row_token"})

#: What a CALLER may contribute to a transmit's audit row, by name (WS-RENDER).
#: Same posture as ``session_id``/``matter_ref`` (ss#2497): attribution the
#: agent asserts about its own send, never authorization — the broker still
#: decides recipients from the seat's own config. Closed list so a caller
#: cannot widen the ledger.
#:
#: ``routing_leg``          which case-alert-routing leg picked the recipients
#:                          (central | matter_staff_responsible |
#:                          matter_staff_assisting | fallback).
#: ``rendered_body_sha256`` canonical_body_sha256 of the text the gate allowed,
#:                          computed by the overlay PRE-mutation (before the
#:                          html/plain attach) — the console's wake<->confirm
#:                          hash join (send_verify.py) compares it against the
#:                          EMITTED_WAKE stamps; arbiter fixture:
#:                          operator/contracts/fixtures/body-canon-vectors.json.
#: ``plain_body_sha256``    canonical_body_sha256 of the exact text/plain the
#:                          overlay handed the channel, computed POST-attach —
#:                          the console's confirm<->channel check compares it
#:                          against the body fetched back from the mailbox,
#:                          which stores the down-render, not the authored
#:                          markdown. OMITTED by the overlay when no down-render
#:                          happened, and that absence is MEANINGFUL to the
#:                          verifier ("text is still the authored bytes"), so it
#:                          must never be synthesized here.
#: ``body_variant``         full | skeleton — a skeleton match grades
#:                          ``degraded`` in the verifier, never BODY_DIVERGED.
#: ``skill_name``           the routine that authored the body, resolved by the
#:                          overlay from the CRON SESSION ID at emission
#:                          (cron_attribution.resolve_routine) — a scheduler
#:                          fact, not an agent assertion. Goes to its COLUMN,
#:                          not into metadata (``_append_send_row`` moves it),
#:                          because the console's wake<->confirm join
#:                          (send_verify.py) claims a wake BY SKILL and the
#:                          EMITTED_WAKE half already lives in that column.
#:                          Before this key, every CONFIRM row wrote NULL there
#:                          and the join fell through to hash-only attribution
#:                          (claims review 2026-09-04, B3).
#:
#: CLOSED ALLOWLIST, AND SILENTLY SO. The filter below drops any key not named
#: here with no error and no log, which is the right posture for an untrusted
#: caller-supplied dict but means a stamp the overlay adds WITHOUT a matching
#: entry here vanishes between the two repos and the verifier simply never sees
#: it. Adding a stamp is therefore a two-repo change; ``plain_body_sha256``
#: pairs with hermes-smd-overlay#338, ``skill_name`` with the overlay's
#: fix/prerendered-dispatch-skill-name pair PR.
#: (One physical line on purpose: tests/operator-module-size.test.ts ratchets
#: this module's logical-line count and only ever tightens. Comments are free.)
_CALLER_AUDIT_KEYS: tuple[str, ...] = ("routing_leg", "rendered_body_sha256", "plain_body_sha256", "body_variant", "skill_name")


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
            self.escalation_ledger_path = str(
                Path(audit_db_path).parent / "escalation-ledger.jsonl"
            )
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
            self, audit_db_path=audit_db_path, queue_dir=os.environ.get("SMD_MEDCHRON_QUEUE_DIR"))
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
            self.agentmail = AgentMailOps(
                credential, self.customer_path, self.customer_slug
            )
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
            self.msgraph = MsGraphOps(
                graph_credential, self.customer_path, read_credential_path=read_credential
            )
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

    def handle(
        self, request: dict[str, Any], peer_pid: int, peer_uid: int | None = None
    ) -> dict[str, Any]:
        action = request.get("action")
        # ss#2614: the five routine-11 verbs; gating and the one-pinned
        # action_type per transition live in medchron_verbs.py.
        if (action == "medchron_job_submit" or action == "medchron_job_status" or action == "medchron_allowance"
                or action == "medchron_job_list" or action == "medchron_job_record"):
            return medchron_dispatch(self.medchron, action, request, peer_pid, peer_uid)
        # ADR 0021 Stream B heartbeat: the ONE verb reachable by cron pre_run
        # children (agent uid, non-gateway PID). Deliberately narrow — the row's
        # action_type is locked to SUPPRESSED_WAKE, so this cannot be used to
        # forge any other audit row; the generic audit_append verb below keeps
        # its strict gateway-PID gate. The append still flows through the
        # hash-chained LedgerWriter (broker stamps id/ts; chain intact).
        if action == "suppressed_wake_append":
            if self.ledger is None:
                raise ValueError("audit ledger not configured on this broker")
            agent_uid = self._resolve_agent_uid()
            if agent_uid is None or peer_uid != agent_uid:
                raise PermissionError(
                    "suppressed_wake_append requires a caller running as the agent uid"
                )
            row = request.get("row")
            if not isinstance(row, dict):
                raise ValueError("suppressed_wake_append requires a 'row' object")
            if row.get("action_type") != "SUPPRESSED_WAKE":
                raise ValueError(
                    "suppressed_wake_append only accepts action_type=SUPPRESSED_WAKE"
                )
            row_id = self.ledger.append(row)
            return {"ok": True, "id": row_id}
        # ss-console #2253: the WAKE half of the same gate. The four gated cron
        # skills wrote a row when they suppressed and nothing when they woke, so
        # the one tick that mattered was the one tick with no row — on
        # 2026-08-10 a fabricated escalation email was discoverable only by
        # reading the mailbox. Same caller shape as the heartbeat verb above (a
        # cron pre_run child: agent uid, non-gateway PID), and deliberately a
        # SEPARATE verb rather than a widened suppressed_wake_append, so each
        # verb still pins exactly one action_type and stays auditable alone.
        #
        # The caller swallows this verb's failures — a wake is never gated on
        # its own audit row — which is exactly why the gate lives here and not
        # in the caller: a best-effort caller cannot be trusted to validate.
        if action == "emitted_wake_append":
            if self.ledger is None:
                raise ValueError("audit ledger not configured on this broker")
            agent_uid = self._resolve_agent_uid()
            if agent_uid is None or peer_uid != agent_uid:
                raise PermissionError(
                    "emitted_wake_append requires a caller running as the agent uid"
                )
            row = request.get("row")
            if not isinstance(row, dict):
                raise ValueError("emitted_wake_append requires a 'row' object")
            if row.get("action_type") != "EMITTED_WAKE":
                raise ValueError(
                    "emitted_wake_append only accepts action_type=EMITTED_WAKE"
                )
            row_id = self.ledger.append(row)
            return {"ok": True, "id": row_id}
        # WP-A escalation ledger append. Same caller shape as the heartbeat
        # verbs above: a cron pre_run or the agent's execute_code turn (agent
        # uid, non-gateway PID). Gated on the agent uid, and the write is
        # VALIDATED (escalation_ledger.validate_append) on both doors into
        # silence: an ``acked`` with no prior raise is rejected, and a
        # ``fired``/``chased`` this broker did not witness dispatching to a
        # person is rejected too (send_witness below). The LLM turn can append
        # only through this seam, never the file directly, so it can neither
        # silence an alarm that never rang nor claim one rang when it did not.
        # ts/id are stamped server-side, so the caller cannot backdate.
        # Serialized by an instance lock (the server is threaded) so the
        # tail-read + append stays consistent.
        # Body lives in send_witness.append_escalation_event — beside the witness
        # it has to consult, and out of this module's size ratchet.
        if action == "escalation_event_append":
            return append_escalation_event(self, request, peer_uid)
        # ss-console #2091 (ADR 0083 §4): the Operator CAPTURES a correction a
        # customer stated, and never applies one. Same caller shape as the
        # heartbeat verbs above (agent uid, non-gateway PID — an execute_code
        # turn), same one-pinned-action_type discipline, and the same reason:
        # this verb must not be able to forge any other row.
        #
        # THE ROW IS REBUILT, NOT FORWARDED. `build_correction_row` reads a
        # bounded field set off the request and constructs the row itself, so a
        # field the caller invents is dropped rather than stored, and `status`
        # is a broker-side constant that never appears on the wire. Validation
        # lives broker-side because the caller is the agent, and a schema the
        # agent enforces is a schema the agent can decline to enforce.
        #
        # NOTHING HERE REACHES A SPEC. This appends to the append-only audit
        # ledger the agent uid cannot open for write. Promotion into
        # `vaults/<slug>/output-classes.json` is portal-side, performed by a
        # Named Administrator, and the promoted bytes are the ones they submit.
        if action == "correction_propose":
            if self.ledger is None:
                raise ValueError("audit ledger not configured on this broker")
            agent_uid = self._resolve_agent_uid()
            if agent_uid is None or peer_uid != agent_uid:
                raise PermissionError(
                    "correction_propose requires a caller running as the agent uid"
                )
            row = build_correction_row(request.get("proposal"))
            row_id = self.ledger.append(row)
            return {"ok": True, "id": row_id, "status": PROPOSED_STATUS}
        # ADR 0085 (ss#2161/#2162, extended by ss-console#2529): conversational
        # establishment. Five narrow verbs by which an instructed voice/shape
        # submission crosses the agent -> broker trust boundary into the root
        # intake's spool. Same caller shape as correction_propose (agent uid,
        # non-gateway PID — an execute_code turn), same fail-closed uid gate,
        # and the same one-pinned-action_type discipline per WRITING verb
        # (RULE_PROPOSED on propose, SUBMITTED on submit, RESULT on status) so
        # none can forge any other row. establish_pending is a read and writes
        # no row at all.
        #
        # EVERYTHING STORED IS REBUILT. establishment.py reads a bounded field
        # set off each request, computes every hash server-side, and refuses —
        # never sanitizes — a malformed field. The agent's uid has no access to
        # the spool at any level; these verbs are the only door, and the root
        # intake independently re-verifies uid and hashes on the other side.
            # ss-console#2536: the same channel carrying a TOOL CALL instead of
            # a sentence. Same uid gate, same lock, same sweep, and the same
            # one-pinned-action_type-per-writing-verb discipline (ACT_PROPOSED
            # on propose, ACT_COMMITTED on commit).
            # ss-console#2546: the two ways a proposal ends WITHOUT being
            # committed, each its own verb under the same uid gate, the same
            # lock, the same sweep, and the same one-pinned-action_type
            # discipline (RULE_DECLINED on decline; RULE_LAPSED on the lapse
            # report, and nothing at all when the outcome being reported is a
            # decline, because RULE_DECLINED already recorded that).
            # ss-console#2546 (the duplicate-letter fix). The seat runs the
            # establishment plugin in TWO processes -- the gateway and its
            # webhook-gate child -- each with its own sweeper, so an in-process
            # once-guard is two guards and the requester was mailed the same
            # outcome letter twice (pilot-smokeball 2026-08-23, overlay
            # fc8f88c1, vfy_01M0QK1927KP54R7J13J2TH3WZ). These two verbs put the
            # claim in the one process both share. Neither WRITES a row -- which
            # of our processes is speaking is not a decision about the firm's
            # work -- so the one-pinned-action_type discipline has nothing to
            # pin here, exactly as with establish_pending and ops_ask_sent.
            # ss-console#2546 (the operations half): a routine, a schedule, a
            # channel, a memory setting, an autonomy level or an on/off is SMD's
            # to change (ADR 0085 as amended 2026-08-22), so the firm cannot
            # confirm one and none of the verbs above will touch the row. These
            # three record the ask, carry SMD's answer back, and mark the one
            # follow-up ask -- same uid gate, same lock, same sweep, and the same
            # one-pinned-action_type-per-writing-verb discipline
            # (OPS_REQUEST_RECORDED on propose, OPS_REQUEST_RESOLVED on resolve,
            # OPS_REQUEST_LAPSED on the lapse report; ops_ask_sent writes no row
            # at all, because being asked again is not a decision).
        if action in (
            "establish_stage_document", "establish_propose", "establish_pending", "establish_submit", "establish_status", "act_propose", "act_commit", "establish_decline", "establish_lapse_notified",
            "establish_notify_claim", "establish_notify_release", "ops_propose", "ops_resolve", "ops_ask_sent",
        ):
            if self.ledger is None:
                raise ValueError("audit ledger not configured on this broker")
            if self.establishment is None:
                raise ValueError("establishment spool not configured on this broker")
            agent_uid = self._resolve_agent_uid()
            if agent_uid is None or peer_uid != agent_uid:
                raise PermissionError(
                    f"{action} requires a caller running as the agent uid"
                )
            with self._establish_lock:
                # Opportunistic TTL sweep on every establishment call: the
                # broker is the only principal that can expire staging sets
                # and unread results (the intake owns only run dirs).
                # ss-console#2529 folds the pending-rules TTL into the same
                # sweep: expired proposals and long-committed ones go on every
                # establishment call, so the table stays bounded with no timer.
                self.establishment.sweep()
                if action == "establish_stage_document":
                    return self.establishment.stage_document(request)
                if action == "establish_propose":
                    return self.establishment.propose(request)
                if action == "establish_pending":
                    return self.establishment.pending_rules(request)
                if action == "establish_submit":
                    return self.establishment.submit(request)
                if action == "act_propose":
                    return self.establishment.act_propose(request)
                if action == "act_commit":
                    return self.establishment.act_commit(request)
                if action == "establish_decline":
                    return self.establishment.decline(request)
                if action == "establish_lapse_notified":
                    return self.establishment.lapse_notified(request)
                if action == "establish_notify_claim":
                    return self.establishment.notify_claim(request)
                if action == "establish_notify_release":
                    return self.establishment.notify_release(request)
                if action == "ops_propose":
                    return self.establishment.ops_propose(request)
                if action == "ops_resolve":
                    return self.establishment.ops_resolve(request)
                if action == "ops_ask_sent":
                    return self.establishment.ops_ask_sent(request)
                return self.establishment.status(request)
        # ss-console #1791: the webhook gate (overlay hermes-smd-webhook-gate)
        # records WEBHOOK_SUPPRESSED for an excluded delivery. It runs as the
        # agent uid on a NON-gateway PID — the same shape as the cron pre_run
        # children above — so the generic gateway-PID-gated audit_append refuses
        # it. This sibling verb gates on the agent uid and locks action_type to
        # WEBHOOK_SUPPRESSED, so it cannot forge any other row. Deliberately a
        # separate verb (not a widened suppressed_wake_append) so each verb pins
        # exactly one action_type and stays auditable in isolation.
        if action == "webhook_suppressed_append":
            if self.ledger is None:
                raise ValueError("audit ledger not configured on this broker")
            agent_uid = self._resolve_agent_uid()
            if agent_uid is None or peer_uid != agent_uid:
                raise PermissionError(
                    "webhook_suppressed_append requires a caller running as the agent uid"
                )
            row = request.get("row")
            if not isinstance(row, dict):
                raise ValueError("webhook_suppressed_append requires a 'row' object")
            if row.get("action_type") != "WEBHOOK_SUPPRESSED":
                raise ValueError(
                    "webhook_suppressed_append only accepts action_type=WEBHOOK_SUPPRESSED"
                )
            row_id = self.ledger.append(row)
            return {"ok": True, "id": row_id}
        if action == "health":
            return {
                "ok": True,
                "credential_ready": self.credential_path.is_file(),
                "customer_ready": self.customer_path.is_file(),
                "audit_ready": self.ledger is not None,
                "jobs_ready": self.job_ledger is not None,
                "supported_ops": self.operations.supported_operations(),
            }
        if peer_pid != self.gateway_pid:
            raise PermissionError("request did not originate from the gateway process")
        # OP-P1-4 append-only audit write. PID-gated (above): only the gateway
        # may write; execute_code/terminal children get a different peer PID and
        # are rejected. The broker stamps id/ts; there is no update/delete/drop
        # verb in this IPC surface — that absence is the append-only guarantee.
        if action == "audit_append":
            if self.ledger is None:
                raise ValueError("audit ledger not configured on this broker")
            row = request.get("row")
            if not isinstance(row, dict):
                raise ValueError("audit_append requires a 'row' object")
            row_id = self.ledger.append(row)
            return {"ok": True, "id": row_id}
        # ss#2258 transmit verbs. PID-gated (above) DELIBERATELY: unlike the
        # heartbeat/escalation verbs, these are not reachable by a cron pre_run
        # child or an execute_code turn. An agent-uid gate would let any future
        # agent-uid process dispatch a real message with no approval, which is a
        # weaker posture than today's — the gate has to tighten here, not relax.
        #
        # The residual is named honestly: an in-gateway rogue path can still
        # reach these verbs. What changed is that it can now only reach people
        # the seat's own config names, only from the seat's own inbox, and never
        # without leaving a row — because the row is written here, by the process
        # that holds the key, not by best-effort plugin code that can be skipped.
        if action in ("agentmail_send", "agentmail_reply"):
            return self._handle_agentmail(action, request)
        if action in ("msgraph_send", "msgraph_reply"):
            return self._handle_msgraph(action, request)
        # B1 durable-job control plane. PID-gated (above): only the gateway
        # (which hosts the worker thread) reaches these; execute_code/terminal
        # children get a different peer PID and are rejected, so the agent
        # cannot claim leases, raise budgets, or flip job status directly.
        if isinstance(action, str) and action.startswith("job_"):
            if self.job_ledger is None:
                raise ValueError("job ledger not configured on this broker")
            return self._handle_job(action, request)
        operation = str(request.get("operation") or "")
        payload = request.get("payload")
        if not operation.startswith("workspace_") or not isinstance(payload, dict):
            raise ValueError("operation and object payload are required")
        if not self.operations.supports(operation):
            raise ValueError(f"unsupported Workspace operation: {operation}")
        digest = hashlib.sha256(_canonical(payload)).hexdigest()
        if action == "authorize":
            grant = self.grants.mint(
                {
                    "customer_slug": self.customer_slug,
                    "operation": operation,
                    "payload_digest": digest,
                    "session_id": str(request.get("session_id") or ""),
                    "tool_call_id": str(request.get("tool_call_id") or ""),
                }
            )
            return {"ok": True, "grant": grant, "payload_digest": digest}
        if action == "execute":
            claims = self.grants.consume(
                str(request.get("grant") or ""),
                {
                    "customer_slug": self.customer_slug,
                    "operation": operation,
                    "payload_digest": digest,
                },
            )
            started = time.perf_counter()
            result = self.operations.dispatch(operation, payload)
            receipt = {
                "customer_slug": self.customer_slug,
                "operation": operation,
                "payload_digest": digest,
                "nonce": claims["nonce"],
                "executed_at": int(time.time()),
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            }
            receipt["signature"] = self.grants.sign_receipt(receipt)
            journal = self.credential_path.parent / "execution-receipts.jsonl"
            with journal.open("ab") as handle:
                handle.write(_canonical(receipt) + b"\n")
            journal.chmod(0o600)
            return {"ok": True, "result": result, "receipt": receipt}
        raise ValueError("unsupported broker action")

    def _handle_job(self, action: str, request: dict[str, Any]) -> dict[str, Any]:
        """Dispatch the B1 job-ledger verbs. Time is stamped server-side
        (``now_and_lease_cutoff``) — the broker is the single clock of record
        for lease timing, so callers never pass time over the wire."""
        jl = self.job_ledger
        assert jl is not None  # guarded by the caller
        if action == "job_create":
            row = request.get("row")
            if not isinstance(row, dict):
                raise ValueError("job_create requires a 'row' object")
            return {"ok": True, "id": jl.create(row)}
        if action == "job_list_claimable":
            now, cutoff = now_and_lease_cutoff(LEASE_TTL_SECONDS)
            return {"ok": True, "jobs": jl.list_claimable(now, cutoff)}
        if action == "job_list":
            # Observability read: every job row (terminal + live), newest first.
            # Powers the console's ``jobs`` runtime-read kind so the worker is
            # verifiable end-to-end over HTTPS. Read-only — no lease filter.
            return {"ok": True, "jobs": jl.list_all()}

        job_id = str(request.get("job_id") or "")
        if not job_id:
            raise ValueError(f"{action} requires job_id")
        if action == "job_read":
            return {"ok": True, "job": jl.read(job_id)}
        if action == "job_cancel":
            # ``ok`` == request processed; ``result`` == the verb's boolean
            # outcome. Keeping them separate lets a legitimately-false outcome
            # (e.g. a fenced-out record) return False instead of reading as a
            # transport refusal on the client.
            return {"ok": True, "result": jl.request_cancel(job_id)}
        if action == "job_claim":
            worker_id = str(request.get("worker_id") or "")
            if not worker_id:
                raise ValueError("job_claim requires worker_id")
            now, cutoff = now_and_lease_cutoff(LEASE_TTL_SECONDS)
            return {"ok": True, "lease_epoch": jl.claim(job_id, worker_id, now, cutoff)}

        # The remaining verbs are epoch-fenced: a stale worker's write is a
        # no-op (the ledger checks lease_epoch in the WHERE clause).
        epoch = request.get("lease_epoch")
        if not isinstance(epoch, int):
            raise ValueError(f"{action} requires an integer lease_epoch")
        if action == "job_heartbeat":
            now, _ = now_and_lease_cutoff(LEASE_TTL_SECONDS)
            return {"ok": True, "result": jl.heartbeat(job_id, epoch, now)}
        if action == "job_record":
            fields = request.get("fields")
            if not isinstance(fields, dict):
                raise ValueError("job_record requires a 'fields' object")
            return {"ok": True, "result": jl.record(job_id, epoch, fields)}
        step_key = str(request.get("step_key") or "")
        if not step_key:
            raise ValueError(f"{action} requires step_key")
        if action == "job_idem_begin":
            return {"ok": True, "decision": jl.idempotency_begin(job_id, step_key, epoch)}
        if action == "job_idem_complete":
            return {"ok": True, "result": jl.idempotency_complete(job_id, step_key, epoch)}
        raise ValueError(f"unsupported job action: {action}")

    # -- ss#2258 transmit --------------------------------------------------

    def _append_send_row(
        self,
        action_type: str,
        verb: str,
        metadata: dict[str, Any],
        *,
        session_id: str = "",
        matter_ref: str | None = None,
    ) -> None:
        """Record a transmit attempt. Body digests only — never the body.

        Written by the broker itself so the row cannot be skipped. The four
        unaudited messages of 2026-08 were possible because emission lived in
        plugin code that returned early whenever its audit client was unset; a
        row written here has no such branch.

        THE TWO JOINS (ss#2497). Measured on the live A&P ledger 2026-08-21
        (``vfy_01M0H8DR6JAPYVHFMNJZXQZ517``): ``session_id`` appeared on 0 of 9
        CONFIRM_SEND_DISPATCHED rows and ``matter_ref`` on none of them, so a
        send could not be tied to the turn that composed it or to the matter it
        concerned. Neither is knowable HERE — this process has no session and
        does not read matters — so both travel on the request beside the payload
        and are written straight through, unexamined. That is deliberate: they
        are attribution the agent asserts about its own turn, not authorization,
        and the broker's authority is over WHO may be written to, which it still
        decides for itself from the seat's own config.

        ``matter_ref`` goes to its COLUMN (``LedgerWriter`` accepts it as an
        agent column) rather than into metadata, because the column is what the
        portal audit record filters and indexes on. Empty values are omitted
        rather than written as ``""``, which the hash chain canonicalizes
        distinctly from NULL and which reads as a reference that is present and
        blank.

        ``skill_name`` (B3, claims review 2026-09-04) arrives inside
        ``metadata`` -- it rides ``audit_extra`` through the closed allowlist
        with the body stamps -- and is MOVED to its column here, for the same
        reason ``matter_ref`` has one: the console's wake<->confirm join reads
        ``skill_name`` off the column on both halves, and a value parked in
        metadata would be a fourth place to look. Popped, so it never appears
        twice; omitted when empty, like ``matter_ref``. The dict the caller
        passed is mutated on purpose -- every caller hands over a literal.
        """
        if self.ledger is None:
            return
        skill_name = metadata.pop("skill_name", "")
        row: dict[str, Any] = {
            "action_type": action_type,
            "actor": "operator",
            "actor_role": "agent",
            **({"skill_name": skill_name} if skill_name else {}),
            **({"matter_ref": matter_ref} if matter_ref else {}),
            "metadata": json.dumps(
                {
                    "customer": self.customer_slug,
                    "verb": verb,
                    **({"session_id": session_id} if session_id else {}),
                    **metadata,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        }
        self.ledger.append(row)

    def _dispatch_transmit(
        self,
        action: str,
        request: dict[str, Any],
        *,
        send: Any,
        reply: Any,
        refused: type[Exception],
        transport: type[Exception],
        attempted_for_send: Any,
        identity_key: str,
    ) -> dict[str, Any]:
        """Execute a fenced transmit and record its outcome either way.

        Shared by both mail channels ON PURPOSE. The row this writes is the only
        evidence that a send happened, and two hand-maintained copies of an audit
        writer drift — silently, and in the direction of the copy nobody is
        reading. Channel differences are parameters, not forks: which ops object,
        which exception pair, and what the sending identity is called
        (``inbox_id`` for AgentMail, ``mailbox`` for Graph).
        """
        payload = request.get("payload")
        if not isinstance(payload, dict):
            raise ValueError(f"{action} requires a 'payload' object")
        # ss#2497 — the audit joins, read from the REQUEST and never from the
        # payload. The payload is what reaches the vendor and is rebuilt from a
        # closed allowlist, so an audit field placed there would be dropped
        # silently. Both are optional: a caller that predates them writes exactly
        # the row it writes today, which is what lets the overlay and this
        # process be deployed in either order.
        session_id = request.get("session_id")
        session_id = session_id.strip() if isinstance(session_id, str) else ""
        matter_ref = request.get("matter_ref")
        matter_ref = matter_ref.strip() if isinstance(matter_ref, str) else ""
        # WS-RENDER: the caller's body-conformance stamps, read from the
        # REQUEST like the two joins above, filtered through a closed
        # allowlist (string values only). Optional at both ends, so the
        # overlay and this process deploy in either order.
        raw_extra = request.get("audit_extra") if isinstance(request.get("audit_extra"), dict) else {}
        audit_extra = {
            key: raw_extra[key].strip()
            for key in _CALLER_AUDIT_KEYS
            if isinstance(raw_extra.get(key), str) and raw_extra[key].strip()
        }
        # Digest what the caller asked to send, computed here, so the row proves
        # which content went out without the ledger ever holding the content.
        digest = hashlib.sha256(_canonical(payload)).hexdigest()
        # A reply names no recipient — it is derived from the source message — so
        # there is nothing to pre-record for it. Whoever it reached appears on the
        # dispatched row, resolved by the broker.
        attempted = [] if action.endswith("_reply") else attempted_for_send(payload)
        try:
            result = send(payload) if action.endswith("_send") else reply(payload)
        except refused as exc:
            self._append_send_row(
                "CONFIRM_SEND_FAILED",
                action,
                {
                    "outcome": "refused",
                    "reason": str(exc),
                    "recipients": attempted,
                    "input_digest": digest,
                    **audit_extra,
                },
                session_id=session_id,
                matter_ref=matter_ref,
            )
            raise
        except transport as exc:
            # A transport failure is NOT a policy refusal and must never read as
            # one: the seat was permitted to write and the vendor call failed.
            # The outcome is genuinely unknown (the message may have gone out),
            # which is exactly what the console-side reconciler exists to settle.
            self._append_send_row(
                "CONFIRM_SEND_FAILED",
                action,
                {
                    "outcome": "transport_error",
                    "reason": str(exc),
                    "recipients": attempted,
                    "input_digest": digest,
                    **audit_extra,
                },
                session_id=session_id,
                matter_ref=matter_ref,
            )
            raise
        self._append_send_row(
            "CONFIRM_SEND_DISPATCHED",
            action,
            {
                "outcome": "sent",
                "recipients": result.get("recipients") or [],
                "message_id": result.get("message_id") or "",
                identity_key: result.get(identity_key) or "",
                "input_digest": digest,
                **audit_extra,
                # The ops verb's own contributions to the row, written through by
                # NAME rather than by wholesale copy, so a transmit result can
                # never quietly widen what the ledger records.
                #
                # ss#2497 — on a REPLY the ops verb resolved the original sender
                # itself (it had to: a caller naming the sender could name any
                # sender), so the row can name the person it answered without an
                # address entering the ledger. A send names no such person and
                # contributes no key.
                #
                # ss#2499 — and on msgraph it contributes the message's identity,
                # which Graph's 202 does not return and which the broker goes and
                # looks up. Every key here is OPTIONAL: AgentMail contributes none
                # of them and writes exactly the row it writes today.
                **{
                    key: result[key]
                    for key in _OPS_AUDIT_KEYS
                    if isinstance(result.get(key), str) and result[key]
                },
            },
            session_id=session_id,
            matter_ref=matter_ref,
        )
        # ``sender_key`` and ``audit_row_token`` are audit provenance, not
        # transmit results: they stay in the row and do not travel back to the
        # agent. The fence deliberately does not tell the agent who it just wrote
        # to beyond what it already knew, and it does not hand back the audit key
        # that a later message could then be stamped with to borrow this row's
        # identity. The vendor ids DO go back — they are the agent's own message,
        # and naming it to the firm is the point of resolving them.
        return {
            "ok": True,
            **{k: v for k, v in result.items() if k not in _AUDIT_ONLY_KEYS},
        }

    def _handle_agentmail(self, action: str, request: dict[str, Any]) -> dict[str, Any]:
        if self.agentmail is None or self.ledger is None:
            raise ValueError(
                "agentmail transmit is not configured on this broker "
                "(needs SMD_AGENTMAIL_CREDENTIAL_PATH and an audit ledger)"
            )
        return self._dispatch_transmit(
            action,
            request,
            send=self.agentmail.send,
            reply=self.agentmail.reply,
            refused=AgentMailRefused,
            transport=AgentMailTransportError,
            attempted_for_send=collect_recipients,
            identity_key="inbox_id",
        )

    def _handle_msgraph(self, action: str, request: dict[str, Any]) -> dict[str, Any]:
        if self.msgraph is None or self.ledger is None:
            raise ValueError(
                "msgraph transmit is not configured on this broker "
                "(needs SMD_MSGRAPH_CREDENTIAL_PATH and an audit ledger)"
            )
        return self._dispatch_transmit(
            action,
            request,
            send=self.msgraph.send,
            reply=self.msgraph.reply,
            refused=MsGraphRefused,
            transport=MsGraphTransportError,
            attempted_for_send=collect_msgraph_recipients,
            identity_key="mailbox",
        )


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
        os.chmod(broker.socket_path, 0o660)  # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions - Broker owner and connector group require socket access; all other users remain denied.
        server.serve_forever()


if __name__ == "__main__":
    main()
