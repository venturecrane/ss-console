# msgraph dead letters: what they are, when you are paged, how to replay

Overlay `shared/msgraph_poller.py` (overlay#275, overlay PR #278) never drops
inbound mail silently. When a delta item cannot be forwarded to the agent's
webhook door, the poller HOLDS its cursor and retries every cycle — a held
cursor still delivers new mail, so holding is cheap. Two outcomes exist:

- **Systemic fault** (gate down, signing-secret drift, adapter rejecting
  everything): every item fails, the cursor holds **indefinitely**, and Sentry
  receives `msgraph poller: inbound forwarding failing; cursor held`
  (warning, throttled). No mail is lost; it all delivers when the fault
  clears. Fix the fault; nothing needs replaying.
- **Item-specific poison** (one message fails while peers forward in the same
  cycle): after 5 mixed-cycle failures the raw payload is preserved to
  `/opt/data/msgraph/dead-letter/<sha16>.json` on the seat's volume, the item
  is marked seen, the cursor moves on, and Sentry receives
  `msgraph poller: dead-letter written` (warning). **That message will not
  reach the agent without the replay below.**

A third signal, `msgraph poller: dead-letter write failed` (error), means the
volume itself refused the write — the item stays held (never dropped) and the
machine needs attention.

## Replay

On the seat (`fly ssh console -a hermes-<slug>`), as the hermes user:

```bash
python3 -m shared.msgraph_replay /opt/data/msgraph/dead-letter/<file>.json
```

The tool re-normalizes the preserved raw item, rebuilds the byte-exact signed
envelope, and POSTs it through the same fenced webhook door live mail uses —
it grants nothing the poller does not. When the adapter starts a turn
(HTTP 202 `{"status":"accepted"}`) the file is renamed `*.replayed`; on
anything else it is left in place. Exit codes: 0 accepted, 1 rejected,
dropped, or transport failure, 2 bad input.

**A 2xx is not a delivery.** The Hermes webhook adapter answers **HTTP 200
`{"status":"duplicate"}`** when the request id (sha256 of the Graph message
id) is still in its 1-hour in-memory idempotency cache, and starts no turn.
That happens when the poller or an earlier replay sent the same message less
than an hour ago. The replay reads the response body (overlay PR #439): a
duplicate, or a 200 `{"status":"ignored"}` filter drop, exits 1, leaves the
file, and logs `replay: NOT delivered` with the request id and when the cache
entry expires (an hour after that earlier send, or on a gateway restart).
Replay again after that. Older overlay builds renamed the file and exited 0
on a duplicate; on a seat whose `OVERLAY_REF` predates #439, an exit 0 proves
nothing.

**Confirm a new session row before calling it recovered.** Exit 0 means the
adapter queued a turn, not that the turn ran. Check the seat's Hermes session
store for a new session with chat id `webhook:msgraph:<request id>` started
at the replay time. No new row means the message has still not reached the
agent: leave the incident open.

## After every incident

1. **Resolve the Sentry issue** for the constant message you were paged on.
   The captures use constant messages so events group — an unresolved issue
   swallows the NEXT incident's page (alert rules notify on first-seen /
   regression). Resolving is what re-arms the page.
2. Dead-letter files carry **full client mail bodies**. They get mailbox-grade
   handling: never copy contents into transcripts, tickets, or logs; delete
   the `*.replayed` file once the replay is confirmed by the new session row
   above and in the audit feed.

## Sources

- `hermes-smd-overlay:shared/msgraph_poller.py` (hold/poison/dead-letter) and
  `shared/msgraph_replay.py` (the replay tool), tests
  `tests/test_msgraph_poller.py`, `tests/test_msgraph_replay.py`
- Hermes `gateway/platforms/webhook.py` at the pinned `HERMES_REF`
  (`operator/templates/Dockerfile`): `_record_delivery_id` (1h TTL) and the
  `{"status":"duplicate"}` 200 answer
- overlay#275 carries the reachability contract and the runtime verify IDs
