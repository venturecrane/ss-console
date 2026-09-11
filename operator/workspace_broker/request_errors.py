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

Lives in its own module so server.py, already at the module-size ratchet's
baseline, does not grow.
"""

from __future__ import annotations

import logging

PROTOCOL_EXCEPTIONS: tuple[type[BaseException], ...] = (
    PermissionError,  # every gate in handle() refuses with one
    ValueError,  # every malformed-request check in handle() refuses with one
)

_log = logging.getLogger("workspace_broker")


def error_response_for(exc: BaseException) -> dict:
    """Map an exception raised while handling one request to the reply dict."""
    if isinstance(exc, PROTOCOL_EXCEPTIONS):
        return {"ok": False, "error": type(exc).__name__, "message": str(exc)}
    _log.exception("unhandled %s while handling a broker request", type(exc).__name__)
    return {"ok": False, "error": "internal_error", "message": "internal error; see broker log"}
