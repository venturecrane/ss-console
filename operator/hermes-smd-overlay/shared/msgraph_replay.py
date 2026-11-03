"""Replay a dead-lettered msgraph item through the gate→router path (overlay#275).

The poller preserves a permanently-failing inbound message as
``<state-dir>/dead-letter/<sha16>.json`` holding ``{"reason", "message_id",
"raw"}`` where ``raw`` is the UN-normalized Graph item. This tool is the
documented recovery path the dead-letter log line points at:

    python3 -m shared.msgraph_replay /opt/data/msgraph/dead-letter/<file>.json
    python3 -m shared.msgraph_replay --message-id <graph message id>   # re-wake (ADR 0102)

It re-normalizes ``raw`` exactly as the poller would, rebuilds the byte-exact
stamped envelope (compact separators — the HMAC is over these bytes), signs
with the route secret, and POSTs to the same loopback adapter the poller uses.
On a 2xx that started a turn the file is renamed ``<file>.replayed`` so the
recovery is visible and idempotent; on anything else the file is left untouched.
A 2xx is not by itself a delivery: the adapter answers HTTP 200
``{"status":"duplicate"}`` when the request id is in its 1h in-memory
idempotency cache, and HTTP 200 ``{"status":"ignored"}`` for a filtered event.
Both dropped the item, so both exit 1 with the file left in place. A replay enters the
model ONLY through the same fenced webhook door as live mail — this tool grants
nothing that the poller does not.

A re-wake is not done at the adapter's 2xx either. The adapter queues the POST
and the gateway's hooks run after it answers, so the webhook router could still
drop the delivery (its replay guard dropped a re-wake 39 minutes after the
previous one on A&P, 2026-11-03, and nothing said so). A re-wake therefore signs
its own id into the body (``shared.rewake_outcome.MARKER_KEY``), which gives it
its own replay key, and then waits for the router's outcome journal to say a
turn started. It exits 0 only on ``turn_started``; a refusal exits 1 with the
router's reason, and so does no turn inside the bounded wait.

Exit codes follow bootstrap/cli.py: 0 accepted, 1 rejected/transport failure,
2 bad input (missing file, id-less item, missing secret/mailbox).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from collections.abc import Callable

from shared import forward_signature, msgraph_client, rewake_outcome
from shared.msgraph_poller import (
    EVENT_TYPE,
    SOURCE,
    _post_forward,
)
from shared.secrets import get_secret

logger = logging.getLogger("hermes-smd-msgraph-replay")

_SIGNING_SECRET_ENV = "WEBHOOK_SECRET_MSGRAPH"

#: The Hermes webhook adapter's idempotency TTL (``_idempotency_ttl = 3600`` in
#: hermes ``gateway/platforms/webhook.py``). In-memory: a gateway restart clears it.
_ADAPTER_IDEMPOTENCY_TTL_S = 3600

#: 2xx answers from the adapter that mean it did NOT start a turn. ``duplicate``
#: is the idempotency cache hit (HTTP 200); ``ignored`` is an event/filter/script
#: drop (HTTP 200). Neither is a delivery.
_DROPPED_STATUSES = frozenset({"duplicate", "ignored"})

#: How long a re-wake waits for its turn to start. The adapter answers before
#: the gateway runs the dispatch hooks; a webhook turn's first LLM call follows
#: within seconds unless the gateway is busy with another turn.
_TURN_WAIT_S = 180.0
_TURN_POLL_S = 2.0


def _adapter_status(payload: bytes) -> str:
    """The adapter's ``{"status": ...}`` field, or "" when absent/unparseable."""
    try:
        parsed = json.loads(payload.decode("utf-8")) if payload else None
    except (UnicodeDecodeError, ValueError):
        return ""
    if isinstance(parsed, dict) and isinstance(parsed.get("status"), str):
        return parsed["status"]
    return ""


def build_envelope(
    raw: dict, *, mailbox: str, rewake_id: str | None = None
) -> tuple[bytes, str] | None:
    """The poller's exact forward body + message id, or None for an id-less item.

    Byte-parity with ``MsGraphPoller._forward`` is load-bearing: same key order,
    same compact separators, because the signature is over these exact bytes.
    A re-wake appends ONE signed field, the re-wake id; ``event_id`` and the DTO
    keep the original Graph message id, so the request index, reply threading
    and every other consumer see the client's own email."""
    dto = msgraph_client.normalize_message(raw, mailbox=mailbox)
    message_id = dto.get("message_id") or ""
    if not message_id:
        return None
    envelope: dict = {
        "source": SOURCE,
        "event_type": EVENT_TYPE,
        "inbound_message": dto,
        "event_id": message_id,
    }
    if rewake_id is not None:
        envelope[rewake_outcome.MARKER_KEY] = rewake_id
    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    return body, message_id


def await_turn(
    rewake_id: str,
    *,
    wait_s: float = _TURN_WAIT_S,
    poll_s: float = _TURN_POLL_S,
    outcome_dir: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Wait for the router's outcome for one re-wake. 0 only on a started turn."""
    deadline = clock() + wait_s
    last: dict | None = None
    while True:
        last = rewake_outcome.read(rewake_id, directory=outcome_dir)
        state = (last or {}).get("state")
        if state == rewake_outcome.STATE_TURN_STARTED:
            logger.info(
                "rewake: DELIVERED. The router dispatched re-wake %s and a webhook turn "
                "started (session %r).",
                rewake_id[:12],
                (last or {}).get("session_id") or "unknown",
            )
            return 0
        if state == rewake_outcome.STATE_REFUSED:
            logger.error(
                "rewake: NOT delivered. The adapter accepted it, then the webhook router "
                "refused it: %s. No turn ran.",
                (last or {}).get("reason") or "no reason recorded",
            )
            return 1
        if clock() >= deadline:
            break
        sleep(poll_s)
    if state == rewake_outcome.STATE_DISPATCHED:
        logger.error(
            "rewake: NOT confirmed. The router dispatched re-wake %s to the gateway, but no "
            "webhook turn started within %ds. Check the seat's newest webhook:msgraph "
            "session before re-waking again.",
            rewake_id[:12],
            int(wait_s),
        )
    else:
        logger.error(
            "rewake: NOT confirmed. The adapter accepted re-wake %s, but the router recorded "
            "no outcome within %ds (journal %s). The gateway did not run the dispatch, or "
            "this seat's image predates the re-wake journal.",
            rewake_id[:12],
            int(wait_s),
            outcome_dir or rewake_outcome.outcome_dir(),
        )
    return 1


def rewake(
    message_id: str,
    *,
    mailbox: str,
    signing_secret: str,
    client: object | None = None,
    forward_fn: Callable[..., tuple[int, bytes]] | None = None,
    state_dir: str | None = None,
    wait_s: float = _TURN_WAIT_S,
    poll_s: float = _TURN_POLL_S,
    outcome_dir: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Re-wake the Operator on an EXISTING inbound email, by its Graph id.

    For SMD to have the seat answer a request again once a capability it lacked
    has shipped (ADR 0102: an attorney's request for records, answered "I cannot
    attach files" the first time). The message is fetched from the seat's own
    mailbox on the agent's read credential, written as a dead-letter-shaped file
    under ``<state_dir>/rewake/``, and replayed through :func:`replay`, so it
    enters the model ONLY through the same fenced webhook door as live mail:
    the sender is re-verified, the roster and every floor apply, and the reply
    is the model's own in its own turn. Nothing here composes or sends anything.

    Run on the seat, as the hermes user. A reply already committed for this
    email in the RUNNING gateway is held as a duplicate; the adapter drops a
    repeat within its 1 h idempotency window. Both are reported, never forced.

    Success is a started turn, not an accepted POST: after the adapter's 2xx
    this waits (up to ``wait_s``) for the router's outcome journal and exits 1
    with the router's reason when it refused the delivery or no turn began.
    """
    if not message_id or "/" in message_id or len(message_id) > 512:
        logger.error("rewake: %r is not a Graph message id", message_id)
        return 2
    graph = client if client is not None else msgraph_client.build_client_from_env()
    if graph is None:
        logger.error("rewake: no Graph client on this seat (MSGRAPH_* unset)")
        return 2
    try:
        raw = graph.get_message(message_id)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 - report, don't traceback, on a seat console
        logger.error("rewake: cannot read message %s (%s)", message_id, exc)
        return 1
    if not isinstance(raw, dict) or not raw.get("id"):
        logger.error("rewake: message %s did not read back as a message", message_id)
        return 1
    root = state_dir or os.path.join(os.environ.get("HERMES_HOME") or "/opt/data", "msgraph")
    base = os.path.join(root, "rewake")
    os.makedirs(base, mode=0o700, exist_ok=True)
    name = hashlib.sha256(message_id.encode("utf-8")).hexdigest()[:16] + ".json"
    path = os.path.join(base, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"reason": "smd_rewake", "message_id": message_id, "raw": raw}, fh)
    # A FRESH request id, never the one the original intake used. The gateway's
    # admission ledger is durable: it already holds sha256(message_id) from the
    # day the email first arrived, so a re-wake under that id is answered 503
    # admission_conflict on every attempt, restart or not (found live on A&P,
    # 2026-11-02). A dead-letter replay keeps the original id on purpose: that
    # email never got through, and a second copy of it must still collapse.
    nonce = os.urandom(8).hex()
    request_id = hashlib.sha256(f"{message_id}:rewake:{nonce}".encode()).hexdigest()
    # The same id is signed into the body as the re-wake marker: the router's
    # replay guard keys this delivery on it (the message id alone is already in
    # its cache from the first intake), and journals its outcome under it.
    code = replay(
        path,
        mailbox=mailbox,
        signing_secret=signing_secret,
        forward_fn=forward_fn,
        request_id=request_id,
        rewake_id=request_id,
    )
    if code != 0:
        return code
    return await_turn(
        request_id, wait_s=wait_s, poll_s=poll_s, outcome_dir=outcome_dir, sleep=sleep
    )


def replay(
    path: str,
    *,
    mailbox: str,
    signing_secret: str,
    forward_fn: Callable[..., tuple[int, bytes]] | None = None,
    request_id: str | None = None,
    rewake_id: str | None = None,
) -> int:
    """Replay one dead-letter file. Returns the process exit code.

    ``forward_fn`` returns ``(status, response_body)``; the body is read because
    the adapter answers HTTP 200 ``{"status":"duplicate"}`` for an idempotency
    cache hit, which dropped the replay rather than delivering it."""
    try:
        with open(path, encoding="utf-8") as fh:
            letter = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.error("replay: cannot read %s (%s)", path, exc)
        return 2
    raw = letter.get("raw") if isinstance(letter, dict) else None
    if not isinstance(raw, dict):
        logger.error("replay: %s carries no raw item", path)
        return 2
    built = build_envelope(raw, mailbox=mailbox, rewake_id=rewake_id)
    if built is None:
        logger.error(
            "replay: item in %s has no message id; it cannot be replayed through the "
            "idempotent door — handle manually",
            path,
        )
        return 2
    body, message_id = built
    timestamp, signature = forward_signature.sign_forward(body, signing_secret)
    request_id = request_id or hashlib.sha256(message_id.encode("utf-8")).hexdigest()
    forward = forward_fn or _post_forward
    try:
        status, payload = forward(
            body=body, signature=signature, timestamp=timestamp, request_id=request_id
        )
    except Exception as exc:  # noqa: BLE001 — report, don't traceback, on a seat console
        logger.error("replay: forward failed (%s)", exc)
        return 1
    if not 200 <= status < 300:
        logger.error("replay: adapter rejected the item (HTTP %d); file left in place", status)
        return 1
    adapter_status = _adapter_status(payload)
    if adapter_status == "duplicate":
        logger.error(
            "replay: NOT delivered. The adapter answered HTTP %d status=duplicate: it "
            "dropped the item because X-Request-ID %s was already seen within its %ds "
            "in-memory idempotency cache (the poller or an earlier replay sent this "
            "message less than an hour ago). File left in place. The cache entry "
            "expires %ds after that earlier send, or on a gateway restart; confirm no "
            "new webhook:msgraph session ran, then replay again after that.",
            status,
            request_id,
            _ADAPTER_IDEMPOTENCY_TTL_S,
            _ADAPTER_IDEMPOTENCY_TTL_S,
        )
        return 1
    if adapter_status in _DROPPED_STATUSES:
        logger.error(
            "replay: NOT delivered. The adapter answered HTTP %d status=%s (no turn "
            "started); file left in place. Body: %s",
            status,
            adapter_status,
            payload[:300].decode("utf-8", "replace"),
        )
        return 1
    replayed = path + ".replayed"
    try:
        os.replace(path, replayed)
    except OSError as exc:
        logger.warning("replay: accepted (HTTP %d) but rename failed (%s)", status, exc)
        return 0
    logger.info(
        "replay: accepted (HTTP %d status=%s); %s -> %s. Confirm a new webhook:msgraph "
        "session row for this delivery before calling it recovered.",
        status,
        adapter_status or "?",
        path,
        replayed,
    )
    return 0


def main(
    argv: list[str] | None = None, *, forward_fn: Callable[..., tuple[int, bytes]] | None = None
) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(
        prog="python3 -m shared.msgraph_replay",
        description="Replay a dead-lettered msgraph item through the fenced webhook door.",
    )
    parser.add_argument("path", nargs="?", help="dead-letter JSON file to replay")
    parser.add_argument(
        "--message-id",
        default=None,
        help="re-wake the Operator on an existing inbound email by its Graph id (ADR 0102)",
    )
    parser.add_argument(
        "--mailbox",
        default=None,
        help="operator mailbox for DTO normalization (default: MSGRAPH_MAILBOX)",
    )
    args = parser.parse_args(argv)

    mailbox = args.mailbox
    if not mailbox:
        try:
            mailbox = get_secret("MSGRAPH_MAILBOX")
        except KeyError:
            logger.error("replay: MSGRAPH_MAILBOX unset and no --mailbox given")
            return 2
    signing_secret = os.environ.get(_SIGNING_SECRET_ENV)
    if not signing_secret:
        logger.error("replay: %s unset — cannot sign the envelope", _SIGNING_SECRET_ENV)
        return 2
    if args.message_id:
        return rewake(
            args.message_id, mailbox=mailbox, signing_secret=signing_secret, forward_fn=forward_fn
        )
    if not args.path:
        logger.error("replay: give a dead-letter file or --message-id")
        return 2
    return replay(args.path, mailbox=mailbox, signing_secret=signing_secret, forward_fn=forward_fn)


if __name__ == "__main__":
    sys.exit(main())
