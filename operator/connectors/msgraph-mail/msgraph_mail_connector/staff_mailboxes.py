"""The staff mailboxes a firm has authored for the Operator to READ: the
connector's half.

Every other tool in this connector is pinned to the Operator's own mailbox. The
two staff tools (``list_staff_messages``, ``read_staff_message``) read a named
staff member's mailbox only when the rule in ``staff_mailbox_rule`` allows it,
including its refusal of any approver on ``scope.staff_send_as``. This module
only reads the seat's live customer.yaml at call time (the vendor_invoice_intake
shape: no translate step) and hands the parsed file to that rule. Nothing here
can send, draft, move or delete: the staff tools are reads, and the send path
stays pinned.
"""

from __future__ import annotations

import os

from .staff_mailbox_rule import (
    CONFIG_BLOCK,
    StaffMailboxes,
    decide,
    from_customer_yaml,
    normalize_address,
    parse_send_as_approvers,
    parse_staff_mailboxes,
)

__all__ = [
    "CONFIG_BLOCK",
    "CUSTOMER_YAML_ENV",
    "DEFAULT_CUSTOMER_YAML",
    "StaffMailboxes",
    "authorize",
    "load_staff_mailboxes",
    "normalize_address",
    "parse_send_as_approvers",
    "parse_staff_mailboxes",
]

DEFAULT_CUSTOMER_YAML = "/var/lib/smd-config/customer.yaml"
CUSTOMER_YAML_ENV = "SMD_CUSTOMER_YAML_PATH"


def load_staff_mailboxes(path: str | None = None) -> StaffMailboxes:
    """Read the seat's live customer.yaml at call time, so an authored change
    reaches the next read without a restart. A missing file authors nothing; an
    unparseable one refuses every read and says why."""
    path = path or os.environ.get(CUSTOMER_YAML_ENV) or DEFAULT_CUSTOMER_YAML
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        return StaffMailboxes()
    try:
        import yaml

        data = yaml.safe_load(raw) or {}
    except Exception as exc:  # noqa: BLE001 - unparseable config refuses every read, reported
        return StaffMailboxes(error=f"customer.yaml not parseable: {exc.__class__.__name__}")
    return from_customer_yaml(data)


def authorize(
    mailbox: object, *, own_mailbox: str, config: StaffMailboxes | None = None
) -> tuple[str | None, str | None]:
    """``(address, None)`` when the rule allows this mailbox, else
    ``(None, "refused: <reason>")``."""
    cfg = config if config is not None else load_staff_mailboxes()
    addr, reason = decide(
        mailbox,
        own_mailbox=own_mailbox,
        config=cfg,
        own_mailbox_hint="; use list_messages or read_message",
    )
    return (addr, None) if addr is not None else (None, f"refused: {reason}")
