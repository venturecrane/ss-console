"""The broker's per-request exception reply: a bounded vocabulary.

``RequestHandler.handle`` (server.py) catches everything a verb handler raises
and writes one reply. Only the exceptions the broker raises ON PURPOSE keep
their message, because those sentences were written for the peer: a refused
verb names its gate (``PermissionError``), a malformed request names the field
(``ValueError``, of which ``json.JSONDecodeError`` is one). Everything else is
an accident, and an accident's message is not a bounded vocabulary: a
``KeyError`` carries a key that may be data, an ``OSError`` a path, a vendor
client's exception a URL. Those are logged broker-side and replied to as
``internal_error`` (2026-09-10 review, Security LOW 7).

The transmit verbs' refusals are deliberate too, and the 2026-10-06 review
(N9) found them masked: ``MsGraphRefused``/``MsGraphTransportError`` and
``AgentMailRefused``/``AgentMailTransportError`` are ``RuntimeError``s, so a
recipient-policy refusal, an attachment refusal and a vendor failure all
reached the overlay as ``internal_error``. The overlay keys its
send-without-attachment retry on the "attachment refused:" prefix in the
message, so that retry never fired. They now subclass ``BrokerRefusal``,
which keeps ``RuntimeError`` as its base (existing ``except RuntimeError``
callers are unchanged) and marks the class as one whose every raise site
writes a broker-authored sentence: never a vendor response body, never
``str()`` of a vendor client exception. A new subclass inherits that
obligation; ``tests/test_request_errors.py`` pins the four.

Lives in its own module so server.py, already at the module-size ratchet's
baseline, does not grow.
"""

from __future__ import annotations

import logging


class BrokerRefusal(RuntimeError):
    """A deliberate broker refusal or failure whose message was written for the peer.

    Defined here (this module imports nothing from the broker) so the ops
    modules can subclass it without an import cycle. Subclassing it is a
    promise about every raise site: the message is a broker-authored sentence
    (a status code at most from the vendor), never vendor text.
    """


PROTOCOL_EXCEPTIONS: tuple[type[BaseException], ...] = (
    PermissionError,  # every gate in handle() refuses with one
    ValueError,  # every malformed-request check in handle() refuses with one
    BrokerRefusal,  # the transmit verbs' refusals and transport failures (review 2026-10-06 N9)
)

_log = logging.getLogger("workspace_broker")


def error_response_for(exc: BaseException) -> dict:
    """Map an exception raised while handling one request to the reply dict."""
    if isinstance(exc, PROTOCOL_EXCEPTIONS):
        return {"ok": False, "error": type(exc).__name__, "message": str(exc)}
    _log.exception("unhandled %s while handling a broker request", type(exc).__name__)
    return {"ok": False, "error": "internal_error", "message": "internal error; see broker log"}
