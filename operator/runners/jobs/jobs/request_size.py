"""The doorway's size stop: a model request too large for its model is never sent.

Why this exists (2026-11-22): a chronology update review sent one request of
1,187,421 input tokens to a model whose window is 1,000,000. The API refused
it, the stage failed partway with a generic message, and the job lost $41 of
paid work and about four and a half hours. Nothing in the runner measured a
request before it went out. This module does, in the one doorway every paid
call walks through (``llm.Doorway.call`` and the batch round), so no stage can
forget to.

The measurement, cheapest first:

1. A LOCAL UPPER BOUND, no network: the UTF-8 bytes of the request's
   messages, system prompt and tools (images taken out) times
   ``TOKENS_PER_BYTE``, plus ``IMAGE_TOKENS`` per image. When the bound plus
   ``max_tokens`` fits the model's window the request is sent as built,
   uncounted: no added latency, and byte-identical requests, on the common case.
2. Otherwise an EXACT COUNT (``messages.count_tokens``, free) with the same
   model, messages, system, tools and thinking. Input plus
   ``min(max_tokens, OUTPUT_FLOOR)`` over the window: ``RequestTooLarge``,
   with a ledger row of zero usage and ``error="request_too_large"`` so the
   attempt is on file and costs nothing. Input plus ``max_tokens`` over the
   window only: ``max_tokens`` is lowered to fit, and the line is logged. The
   API would accept the request (Claude 4.5 models and newer stop at the window
   with ``stop_reason: "model_context_window_exceeded"``, per the context
   windows page), but every stage here reads ``max_tokens`` as "ran out" and
   splits or asks again on it; an unfamiliar stop reason would read as a
   finished answer.
3. A count that cannot be made (a client with no ``count_tokens``, a 429, any
   error, or no answer in ``COUNT_TIMEOUT_S``) sends the request: the count is
   a guard, and an API that refuses it for its size is translated below.

An API refusal for size (HTTP 413, or a 400 that says too long / too large)
raised by the send is translated into ``RequestTooLarge`` with no measured
figure: the API bills nothing for a refused request.

How a lane ends on it is the lane's: the chronology driver holds with
``CHRONOLOGY_HOLD`` (``as_hold``), the other lanes end ``failed`` and
resumable with the SMD-facing reason ``str(exc)``, which starts with the
reason code ``request_too_large``.

``DoorwayError`` lives here, not in ``llm.py``, only so this module does not
import the doorway it is called from; ``llm`` re-exports it.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any, Callable

from .ledger import UNSENT
from .stop_request import before_paid_call as _before_paid_call

#: Tokens per byte of serialized request text, an upper bound. Set from the
#: census of real requests. Start: on 148 real review parts the stage's own
#: chars/4 estimate came to 0.46 to 0.65 of the API's count (design pass,
#: 2026-11-22), so the densest real text ran about 0.54 tokens per character;
#: a UTF-8 byte is never less than a character and JSON escaping only adds
#: bytes, so 0.6 per byte sits above every measured request.
TOKENS_PER_BYTE = 0.6
#: Tokens one image block can cost, an upper bound. Set from the census. Start:
#: the review stage prices a page picture at 1,800 tokens, label included
#: (``medchron/update/review_pages.PAGE_TOKENS``); 6,000 is over three times that.
IMAGE_TOKENS = 6_000
#: The least output room a request must keep. A request whose input leaves less
#: than this (or than its own ``max_tokens``, if smaller) cannot answer usefully
#: and is not sent. Kept small (review 11-22): a 994,000-token audit call with an
#: 8,000-token answer fits today and must not be refused; between this and its
#: own ``max_tokens`` the ceiling is lowered instead.
OUTPUT_FLOOR = 1_000
#: Seconds the exact count may take before the request is sent uncounted.
COUNT_TIMEOUT_S = 60.0
#: A model this module cannot place gets the smallest window any current model
#: has: the safe side, at worst a free count.
DEFAULT_WINDOW = 200_000
#: The 1,000,000-token window, by generation rather than by id (the context
#: windows page, fetched 2026-11-22): every generation-5 model of every family
#: (Fable, Mythos, Opus, Sonnet, Haiku), and Opus and Sonnet from 4.6 on. Every
#: other Claude model has 200,000 (Haiku 4.5 among them). The jobs' pinned tiers
#: (Opus 5.5 and Sonnet 5, ``medchron/probes.py``) are generation 5. Model ids
#: themselves live in the firm config, not here (the sprawl audit's
#: ``model-id-literals``: one home for model facts).
LARGE_WINDOW = 1_000_000
_FAMILIES = frozenset({"fable", "mythos", "opus", "sonnet", "haiku"})
_MODEL = re.compile(r"^claude-([a-z]+)-(\d+)(?:-(\d+))?(?:-|$)")
#: What the chronology's hold says: fixed, no token counts (the firm can read it).
CHRONOLOGY_HOLD = "One part of the file is larger than a single reading allows; nothing was charged for that step."
#: The request fields ``messages.count_tokens`` takes, as the doorway builds them.
_COUNTED = ("model", "messages", "system", "tools", "tool_choice", "thinking", "output_config")

Counter = Callable[[dict[str, Any]], "int | None"]


class DoorwayError(RuntimeError):
    pass


class RequestTooLarge(DoorwayError):
    """A request too large for its model: never sent, or refused unbilled.
    ``measured`` is the counted input, None when the API refused it uncounted."""

    def __init__(
        self, *, stage: str, model: str, custom_id: str | None, measured: int | None, limit: int, max_tokens: int
    ) -> None:
        self.stage, self.model, self.custom_id = stage, model, custom_id
        self.measured, self.limit, self.max_tokens = measured, limit, max_tokens
        super().__init__(self.reason())

    def reason(self) -> str:
        """The SMD-facing sentence: stage, size and limit. Never shown to a firm."""
        who = f"{self.stage} request {self.custom_id or '(unnamed)'}"
        if self.measured is None:
            return f"{UNSENT}: {who} was refused by the API for its size (window {self.limit:,} tokens); nothing was charged"
        room = min(self.max_tokens, OUTPUT_FLOOR)
        return (
            f"{UNSENT}: {who} counts {self.measured:,} input tokens; with {room:,} for its answer it is over "
            f"the {self.model} window of {self.limit:,}; it was not sent and nothing was charged"
        )


def known(model: str) -> bool:
    """True when ``window_for`` places the model from its id. A model it cannot
    place (a newer generation, a provider-prefixed id it cannot read) is never
    refused before sending: the API decides, and ``refused`` translates its answer."""
    m = _MODEL.match(str(model).rsplit("anthropic.", 1)[-1])
    return m is not None and m.group(1) in _FAMILIES and int(m.group(2)) <= 5


def window_for(model: str) -> int:
    """The model's context window from its family and generation; a model id
    this cannot read, or a generation past 5, gets ``DEFAULT_WINDOW``."""
    m = _MODEL.match(str(model).rsplit("anthropic.", 1)[-1])
    if m is None or m.group(1) not in _FAMILIES:
        return DEFAULT_WINDOW
    family, major, minor = m.group(1), int(m.group(2)), int(m.group(3) or 0)
    if major == 5 or (major == 4 and minor >= 6 and minor < 100 and family in ("opus", "sonnet")):
        return LARGE_WINDOW
    return DEFAULT_WINDOW


def is_size_refusal(exc: BaseException) -> bool:
    """True for ``RequestTooLarge`` and for an API refusal of the request's size
    (HTTP 413, or a 400 saying too large / too long / too many). Moved here
    from ``stages/entry_audit_io.ModelError.too_large``, its one caller shape."""
    if isinstance(exc, RequestTooLarge):
        return True
    status, msg = getattr(exc, "status_code", None), str(exc).lower()
    return status == 413 or (status == 400 and any(w in msg for w in ("too large", "too long", "too many")))


def upper_bound(params: dict[str, Any]) -> int:
    """A token count no real request of this shape exceeds, without the network."""
    images = 0

    def strip(o: Any) -> Any:
        nonlocal images
        if isinstance(o, dict):
            if o.get("type") == "image":
                images += 1
                return None
            return {k: strip(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [strip(v) for v in o]
        return o

    body = {k: strip(params.get(k)) for k in ("system", "messages", "tools")}
    n = len(json.dumps(body, ensure_ascii=False, default=str).encode("utf-8"))
    return int(n * TOKENS_PER_BYTE) + images * IMAGE_TOKENS


def client_counter(client: Any) -> Counter:
    """The exact count through ``client.messages.count_tokens``, on its own
    thread, or None when it is missing, fails, or takes ``COUNT_TIMEOUT_S``."""

    def count(params: dict[str, Any]) -> int | None:
        fn: Any = getattr(getattr(client, "messages", None), "count_tokens", None)
        if not callable(fn):
            return None
        got: list[Any] = []
        kw = {k: params[k] for k in _COUNTED if k in params}

        def work() -> None:
            try:
                res: Any = fn(**kw)
                got.append(int(res.input_tokens))
            except Exception:  # noqa: BLE001 - a count that fails (429, 5xx, anything) sends the request uncounted
                got.append(None)

        t = threading.Thread(target=work, daemon=True)
        t.start()
        t.join(COUNT_TIMEOUT_S)
        return got[0] if got else None

    return count


def _unsent(door: Any, exc: RequestTooLarge, *, batch: bool, pages: int) -> RequestTooLarge:
    door.ledger.record(
        exc.stage,
        exc.model,
        None,
        effort=None,
        cache=False,
        batch=batch,
        pages=pages,
        custom_id=exc.custom_id,
        error=UNSENT,
    )
    door.log(f"  {exc}")
    return exc


def fit(
    door: Any, stage: str, params: dict[str, Any], *, custom_id: str | None, pages: int, batch: bool = False
) -> dict[str, Any]:
    """``params`` unchanged when they fit; with ``max_tokens`` lowered when only
    the answer would overflow; else ``RequestTooLarge`` (after its zero row)."""
    model, max_tokens = str(params["model"]), int(params["max_tokens"])
    limit = window_for(model)
    if not known(model) or upper_bound(params) + max_tokens <= limit:
        return params
    counter = door.count_tokens or client_counter(door._client(None))
    measured = counter(params)
    _before_paid_call(stage, None)  # SMD's stop, again: the count may have taken a minute
    if measured is None:
        return params
    if measured + min(max_tokens, OUTPUT_FLOOR) > limit:
        exc = RequestTooLarge(
            stage=stage, model=model, custom_id=custom_id, measured=measured, limit=limit, max_tokens=max_tokens
        )
        raise _unsent(door, exc, batch=batch, pages=pages)
    if measured + max_tokens > limit:
        door.log(
            f"  {stage}: {measured:,} input tokens; max_tokens lowered {max_tokens:,} -> {limit - measured:,} to fit"
        )
        return {**params, "max_tokens": limit - measured}
    return params


def refused(
    door: Any, exc: BaseException, stage: str, params: dict[str, Any], *, custom_id: str | None, pages: int
) -> None:
    """Re-raise an API size refusal from the send as ``RequestTooLarge``; return otherwise."""
    if isinstance(exc, RequestTooLarge) or not is_size_refusal(exc):
        return
    model = str(params["model"])
    rtl = RequestTooLarge(
        stage=stage,
        model=model,
        custom_id=custom_id,
        measured=None,
        limit=window_for(model),
        max_tokens=int(params["max_tokens"]),
    )
    raise _unsent(door, rtl, batch=False, pages=pages) from exc


def as_hold(exc: BaseException) -> Any:
    """The chronology's hold for a stage exception: the ``LimitHold`` itself, or
    a ``LimitHold`` with ``CHRONOLOGY_HOLD`` when a ``RequestTooLarge`` is in
    its explicit chain (``raise ... from``, or a wrapper's ``.cause``, as
    ``entry_audit_io.ModelError`` carries it); None otherwise. The implicit
    ``__context__`` is not followed: a stage that caught a size stop and split
    the work would otherwise have an unrelated later crash read as a size hold."""
    from .limits import LimitHold

    if isinstance(exc, LimitHold):
        return exc
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, RequestTooLarge):
            return LimitHold("request_size", CHRONOLOGY_HOLD)
        nxt = getattr(cur, "cause", None)
        cur = nxt if isinstance(nxt, BaseException) else cur.__cause__
    return None
