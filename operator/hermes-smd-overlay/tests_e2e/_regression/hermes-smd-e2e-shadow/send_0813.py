"""The 2026-08-13 send-handler shape, committed on purpose (Sentry SMD-OPERATOR-1B).

``smd_send_message`` shipped in overlay ``53a3aab`` (2026-08-11) as the handler
below: keyword-only, so Hermes' ``entry.handler(args, **kwargs)`` raised
``TypeError: _smd_send_message() takes 0 positional arguments but 1 was given``
inside ``registry.dispatch`` on every call, and the body read its payload from
``kwargs``, so a signature-only fix would have sent an empty message and still
reported "Sent". Write-up:
``docs/runbooks/operator/incidents/2026-08-13-send-tool-dispatch-shape.md``.

``tests_e2e/test_regression_bites.py`` puts this handler behind the real
``smd_send_message`` registry entry, drives one model tool call through the real
agent loop, and asserts that the harness's own dispatch assertion FAILS. That
keeps "the harness still catches the 08-13 class" a green assertion on every
run. Do not fix this file; it is the defect.
"""

from __future__ import annotations

from typing import Any


def _smd_send_message(**kwargs: Any) -> str:  # the defect: no positional slot
    from shared import outbound_send

    payload = dict(kwargs)  # the second defect: the payload is read from kwargs
    message_id = outbound_send.send_message(
        payload=payload, session_id=str(kwargs.get("session_id") or "")
    )
    return f"Sent (message {message_id})."
