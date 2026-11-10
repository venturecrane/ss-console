"""The staff mailboxes a seat's firm has authored for the Operator to READ: the
gateway's half.

``mail_spool_message`` can fetch a whole email from a named staff mailbox. It
may do so only when the rule in ``shared.staff_mailbox_rule`` allows it,
including its refusal of any approver on ``scope.staff_send_as``. That rule is
the same bytes the msgraph-mail connector runs for its staff reads (see the
rule's own docstring for how the two copies are kept identical). This module
only reads the seat's customer.yaml at call time and turns a refusal into
:class:`StaffMailboxRefused`.
"""

from __future__ import annotations

from typing import Any

from shared.customer_config import CustomerConfig, CustomerConfigError
from shared.staff_mailbox_rule import (
    CONFIG_BLOCK,
    StaffMailboxes,
    decide,
    from_customer_yaml,
    normalize_address,
)

__all__ = [
    "CONFIG_BLOCK",
    "StaffMailboxRefused",
    "authored",
    "authorize",
    "load",
    "normalize_address",
]


class StaffMailboxRefused(ValueError):
    """The mailbox is not one the firm authored, or the list cannot be read."""


def load(config: CustomerConfig | None = None) -> StaffMailboxes:
    """The rule's view of the seat's customer.yaml. A file that cannot be read
    refuses every staff read, because "unreadable" must not become "authored
    nothing" silently in a reply."""
    try:
        cfg = config if config is not None else CustomerConfig.from_volume()
        data = cfg.raw
    except (CustomerConfigError, OSError) as exc:
        return StaffMailboxes(error=f"the seat's customer.yaml could not be read: {exc}")
    return from_customer_yaml(data)


def authored(config: CustomerConfig | None = None) -> tuple[str, ...]:
    """The authored addresses, normalized. Raises when the config cannot be
    read or either block is malformed."""
    rules = load(config)
    if rules.error:
        raise StaffMailboxRefused(rules.error)
    return rules.mailboxes


def authorize(mailbox: Any, *, own_mailbox: str, config: CustomerConfig | None = None) -> str:
    """The normalized address when the rule allows it, else raise with the
    rule's reason, which names what IS authored."""
    addr, reason = decide(
        mailbox,
        own_mailbox=own_mailbox,
        config=load(config),
        own_mailbox_hint="; leave mailbox empty for it",
    )
    if addr is None:
        raise StaffMailboxRefused(reason or "refused")
    return addr
