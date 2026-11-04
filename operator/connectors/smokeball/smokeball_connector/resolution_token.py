"""The resolution token: the only thing that opens a write on a matter the model chose.

It opened ``stage_vendor_invoice`` first (2026-09-18) and
``file_attachment_pages_to_matter`` next, each checking it in its own body. Every
other create or render tool now declares it once at registration and the SDK
checks it on the MCP wrapper (``operator_connector_sdk.resolution``, wired here
by ``resolution_gate``), through :func:`claim` and :func:`restore`.

THE DEFECT THIS CLOSES (proven live 2026-09-18). An invoice naming a client and
nothing else, on a tenant carrying TWO open matters for that client, was staged
on one of them. The skill body already said to resolve a matter from two facts
and to flag anything it could not place, and the firm's own instruction is that
the Operator "flags anything it can't match instead of guessing". The prose was
correct and the model resolved anyway.

So ambiguity is no longer a judgement the model makes. It is a VALUE the routine
receives: ``resolve_invoice_matter`` searches the tenant, returns a closed
verdict, and mints a token ONLY when the verdict is ``unique``. The stage tool
refuses without one. A model that decides in its own head that a matter is the
right one has nothing to pass, and the string it would have to invent is a
32-hex secret it never saw.

WHY A TOKEN AND NOT A FLAG. Every cheaper option is forgeable by prose. A
boolean argument (``resolved=True``), a repeated matched-on list, an echoed
verdict string: each is a sentence the model can type, so each re-creates the
defect one layer down, where it is harder to see. The token is minted from
``secrets`` inside this process and never derived from anything the model can
compute, so possession of it IS the evidence that the search ran and came back
unique.

WHY IN PROCESS AND NOT ON DISK (the difference from the attachment spool). The
spool is a file under ``/opt/data`` because its two halves run in different
processes from different repos: the overlay's mail tool writes, this connector
reads. Both halves of a resolution run inside THIS connector process, one tool
call after another on the same stdio server, so the store is a dict behind a
lock. That is not merely cheaper, it is the fail-closed direction: a connector
restart forgets every outstanding resolution, and a forgotten resolution refuses
the write rather than permitting one whose search nobody can point at.

Four ways to be handed a token and refuse, each a different sentence, because a
model that cannot tell them apart retries the wrong one:

* not the issued shape -- the argument is not a resolution token at all;
* unknown -- never minted here, already used, or expired and pruned;
* expired -- minted here, past its TTL, not yet pruned;
* bound elsewhere -- a real resolution, for a DIFFERENT matter. This is the
  interesting one: it is what a model does when it resolves matter A, reads the
  ambiguity, and stages on B anyway.

Single use, and consumed at the last possible moment: a refusal that happens
before the write leaves the token spendable (the ledger read, the byte re-check
and the duplicate scan all precede it), but once a POST is attempted the token
is gone, so one resolution can never write two entries.
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import dataclass

#: The wire form: ``resolution:<32 lowercase hex>``. Prefixed so a token can
#: never be confused with the spool's ``spool:<token>`` -- the two are handed to
#: the same skill in the same turn and mean entirely different things.
TOKEN_PREFIX = "resolution:"
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")

#: A resolution is good for one turn's work, not for a session. Minutes, because
#: the tenant can change underneath it: a matter closed, renumbered, or a second
#: matter opened for the same client is exactly the state this guards against.
TTL_SECONDS = 900.0

#: Bound on the store. One turn mints one resolution per invoice; a mailbox
#: message carrying more attachments than this is not a vendor invoice.
MAX_LIVE = 64


#: The machine-readable reason a token was refused, one per sentence below. An
#: observe-mode row records the code, never the token or any argument's text.
CODE_MISSING = "missing"
CODE_MALFORMED = "malformed"
CODE_UNKNOWN = "unknown"
CODE_EXPIRED = "expired"
CODE_BOUND_ELSEWHERE = "bound_elsewhere"
CODES = frozenset({CODE_MISSING, CODE_MALFORMED, CODE_UNKNOWN, CODE_EXPIRED, CODE_BOUND_ELSEWHERE})

#: The tools that mint a token. Named in the refusal sentences so a model that
#: was refused knows what to call, and in every gated tool's description
#: (``server.py`` passes the same names to ``ConnectorServer``; a conformance
#: test holds the two equal).
RESOLVER_TOOLS = ("resolve_matter_by_id", "resolve_invoice_matter")
RESOLVERS = " or ".join(RESOLVER_TOOLS)


class ResolutionRefused(Exception):
    """A token that does not open this matter. The message is the reason, in the
    words the reply needs; callers surface it and create nothing. ``code`` is
    the same reason as one of :data:`CODES`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Resolution:
    """What a minted token stands for: the matter the search settled on, and the
    facts that settled it. ``matched_on`` is carried so the write's result can
    say HOW the matter was resolved without the model restating it."""

    matter_id: str
    matter_number: str | None
    matched_on: tuple[str, ...]
    minted_at: float


_LOCK = threading.Lock()
_LIVE: dict[str, Resolution] = {}


def _token_body(token: object) -> str:
    """The hex body of a well-formed token, or refuse. Never raises on type."""
    text = token.strip() if isinstance(token, str) else ""
    if not text.lower().startswith(TOKEN_PREFIX):
        raise ResolutionRefused(
            CODE_MISSING,
            f"no matter resolution: call {RESOLVERS} first and pass the matter_resolution it "
            "returned (nothing was created)",
        )
    body = text[len(TOKEN_PREFIX) :].strip()
    if not TOKEN_RE.match(body):
        raise ResolutionRefused(
            CODE_MALFORMED, "that is not a resolution token; resolve the matter again (nothing was created)"
        )
    return body


def _prune(now: float) -> None:
    """Drop expired entries. Called under the lock."""
    for key in [k for k, r in _LIVE.items() if now - r.minted_at > TTL_SECONDS]:
        _LIVE.pop(key, None)


def mint(matter_id: str, matter_number: str | None, matched_on: tuple[str, ...]) -> str:
    """Issue a token for one uniquely resolved matter.

    Called ONLY on a ``unique`` verdict, by ``matter_resolution.resolve_matter``
    and ``matter_by_id.resolve_matter_by_id``. There is no other caller and no
    argument by which one could be requested."""
    now = time.monotonic()
    body = secrets.token_hex(16)
    with _LOCK:
        _prune(now)
        while len(_LIVE) >= MAX_LIVE:
            oldest = min(_LIVE, key=lambda k: _LIVE[k].minted_at)
            _LIVE.pop(oldest, None)
        _LIVE[body] = Resolution(
            matter_id=str(matter_id),
            matter_number=matter_number,
            matched_on=tuple(matched_on),
            minted_at=now,
        )
    return f"{TOKEN_PREFIX}{body}"


def _check(body: str, matter_id: str, now: float) -> Resolution:
    """The live record for ``body`` if it names THIS matter, or refuse. Called
    under the lock; drops an expired record as it finds it."""
    record = _LIVE.get(body)
    if record is not None and now - record.minted_at > TTL_SECONDS:
        _LIVE.pop(body, None)
        raise ResolutionRefused(
            CODE_EXPIRED,
            "that matter resolution has expired; resolve the matter again and write once with the "
            "new resolution (nothing was created)",
        )
    if record is None:
        raise ResolutionRefused(
            CODE_UNKNOWN,
            "that matter resolution is unknown: it was already used, or it was never issued on "
            "this seat; resolve the matter again (nothing was created)",
        )
    if record.matter_id != str(matter_id):
        raise ResolutionRefused(
            CODE_BOUND_ELSEWHERE,
            "that matter resolution was issued for a different matter; write on the matter id "
            f"the resolution returned, or call {RESOLVERS} again (nothing was created)",
        )
    return record


def verify(token: object, matter_id: str) -> Resolution:
    """The resolution this token stands for, if it is live and names THIS matter.

    Does not consume. Every refusal names which of the four it is."""
    body = _token_body(token)
    with _LOCK:
        return _check(body, matter_id, time.monotonic())


def consume(token: object, matter_id: str) -> Resolution:
    """Verify and spend, atomically. After this the token opens nothing."""
    body = _token_body(token)
    with _LOCK:
        record = _check(body, matter_id, time.monotonic())
        _LIVE.pop(body, None)
    return record


@dataclass(frozen=True)
class Claim:
    """A token taken out of circulation by :func:`claim`, held until the write
    it opened either returns (spent) or raises (:func:`restore` puts it back)."""

    body: str
    resolution: Resolution


def claim(token: object, matter_id: str) -> Claim:
    """Verify and take, in one locked step: the SDK gate's door.

    The difference from ``verify`` then ``consume`` is the window between them.
    The gated tools run on worker threads, and two calls holding one token could
    both pass a ``verify`` before either consumed it. A claimed token is already
    gone, so the second call is refused as ``unknown`` and one resolution can
    never open two writes."""
    body = _token_body(token)
    with _LOCK:
        record = _check(body, matter_id, time.monotonic())
        _LIVE.pop(body, None)
    return Claim(body=body, resolution=record)


def restore(held: Claim) -> None:
    """Put a claimed token back: the write it opened raised. Its age is kept, so
    a restore never extends a resolution past its TTL."""
    with _LOCK:
        _LIVE.setdefault(held.body, held.resolution)


def _reset_for_tests() -> None:
    """Empty the store. A test seam, and reachable only from inside this
    process: no tool exposes it and no argument reaches it."""
    with _LOCK:
        _LIVE.clear()


__all__ = [
    "CODES",
    "CODE_BOUND_ELSEWHERE",
    "CODE_EXPIRED",
    "CODE_MALFORMED",
    "CODE_MISSING",
    "CODE_UNKNOWN",
    "MAX_LIVE",
    "RESOLVERS",
    "RESOLVER_TOOLS",
    "TOKEN_PREFIX",
    "TOKEN_RE",
    "TTL_SECONDS",
    "Claim",
    "Resolution",
    "ResolutionRefused",
    "claim",
    "consume",
    "mint",
    "restore",
    "verify",
]
