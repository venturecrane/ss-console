"""The seat's ``status_from_events`` switch (ADR 0104; ``logic_in_code`` in
customer.yaml): whether a defendant's status comes from its events.

The reader is the Smokeball connector's copy of ``logic_in_code.py``. The
runner's venv installs that package and the ``jobs`` bundle ships the two
together (``operator/templates/jobs-runtime.sh``), so the runner carries no copy
of its own. Absent is ``observe``; a value it cannot read is ``enforce``.
"""

from __future__ import annotations

from typing import Callable

from smokeball_connector import logic_in_code

#: Where a job keeps its mode in ``data/state.json``.
STATE_KEY = "status_from_events"
ENFORCE = logic_in_code.ENFORCE


def current(log: Callable[[str], None]) -> str:
    """The switch as the seat's config says now. Read fresh, kept nowhere here."""
    switch = logic_in_code.load_switch(logic_in_code.STATUS_FROM_EVENTS)
    if switch.error:
        log(f"[{STATE_KEY}] enforcing: {switch.error}")
    return switch.mode
