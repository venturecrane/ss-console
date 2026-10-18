"""The people a space-mode seat serves, as the broker reads them (ADR 0091).

A seat that authors ``people:`` (the couples role) is shared by more than one
person, and two of its sends reach exactly those people and nobody else:

* the ``space`` lane (``participant_fence.LANE_SPACE``): a scheduled routine's
  email to a person, or to both, when Telegram is not bound yet;
* a SHARED reply (``AgentMailOps.reply`` with ``shared: true``): an answer to a
  joint email thread, copied to every person the thread already included.

Both read the addresses here, from the seat's own config, fresh per transmit.
The overlay computes the same set and locks its drafts to it; this is the
second, independent half, held by the process that holds the key.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .recipient_policy import normalize_address

#: The space lane's name, as the overlay names it (``shared.send_anchor``).
LANE_SPACE = "space"


def people_emails(data: dict[str, Any]) -> tuple[str, ...]:
    """Every authored ``people[].email``, canonical, in authored order.

    Empty when the seat authors no ``people`` (not space mode). An entry that is
    not a mapping or carries no usable address is skipped: an allow input never
    widens on something unreadable."""
    people = data.get("people")
    out: list[str] = []
    for person in people if isinstance(people, list) else []:
        if not isinstance(person, dict):
            continue
        address = normalize_address(person.get("email"))
        if "@" in address and address not in out:
            out.append(address)
    return tuple(out)


def seat_people_emails(customer_path: Path) -> tuple[str, ...]:
    """:func:`people_emails` read from the seat's ``customer.yaml``. Raises when
    the file cannot be read (the caller refuses)."""
    loaded = yaml.safe_load(customer_path.read_text(encoding="utf-8"))
    return people_emails(loaded if isinstance(loaded, dict) else {})
