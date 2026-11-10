"""The one rule for which staff mailbox the Operator may READ.

A firm can let the Operator read a named staff member's mailbox (the office
manager's, say), so it can find a letter she received without her forwarding
it. That permission is the firm's to give, so it is authored, never inferred:

    staff_mailbox_reads:
      mailboxes:
        - someone@firm.example

on the seat's own customer.yaml. A mailbox not on the list is refused before any
Graph call. A missing block or an empty list refuses everything; a block or a
file that cannot be read refuses everything and says why.

AN APPROVER'S MAILBOX IS NEVER READABLE, EVEN WHEN AUTHORED (ADR 0089
amendment 5a). A person on ``scope.staff_send_as`` receives approval emails
carrying Send it buttons, and the broker keeps no Sent Items copy, so that
person's inbox is the only place the signed link exists. If the Operator could
read it, the Operator could lift its own approve link and press it. So any
address on ``scope.staff_send_as`` in the same file is refused, whatever
``staff_mailbox_reads`` says. A ``scope.staff_send_as`` that cannot be read as a
list refuses every staff read, because an approver list that cannot be
evaluated must not read as "nobody is an approver".

Tenant-side, the firm must ALSO scope the READ app's Exchange
ApplicationAccessPolicy to the mailbox. This rule is the code-layer belt; that
policy is the braces, and boot smoke asks Microsoft about every approver.

ONE RULE, SHIPPED TWICE, BYTE FOR BYTE. Two processes on a seat enforce it, each
in its own venv: the msgraph-mail connector (``list_staff_messages``,
``read_staff_message``) and the gateway (``mail_spool_message``). Neither venv
can import the other's package, so this file is installed in both:

    operator/connectors/msgraph-mail/msgraph_mail_connector/staff_mailbox_rule.py
    operator/hermes-smd-overlay/shared/staff_mailbox_rule.py

``operator/contracts/overlay-pairs.json`` records the pair as ``identical``, and
``tests/operator-overlay-pairs.test.ts`` fails unless the two hash the same.
Edit one, copy it over the other. Stdlib only, and no import of either package,
so the same bytes import in both places. Each caller keeps only how it reads
the file; the decision is here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CONFIG_BLOCK = "staff_mailbox_reads"

# Deliberately narrow: the address is interpolated into a Graph URL path, so a
# character that could change the path (/ ? # % \ or whitespace) never passes,
# even from the authored list.
_ADDRESS_RE = re.compile(r"^[a-z0-9._+'-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+$")


@dataclass(frozen=True)
class StaffMailboxes:
    """What the seat's customer.yaml authors for staff reads. ``error`` set
    means every staff read is refused, with that reason."""

    mailboxes: tuple[str, ...] = ()
    error: str | None = None
    rejected: tuple[str, ...] = ()
    # Every address on scope.staff_send_as: the people whose inboxes hold approve links.
    approvers: tuple[str, ...] = ()


def normalize_address(value: object) -> str | None:
    """A bare, lowercased address that is safe in a URL path, or None."""
    if not isinstance(value, str):
        return None
    addr = value.strip().lower()
    return addr if _ADDRESS_RE.fullmatch(addr) else None


def parse_staff_mailboxes(block: object) -> StaffMailboxes:
    """The ``staff_mailbox_reads`` block alone. Malformed entries are dropped
    and listed in ``rejected``; they are never trusted."""
    if block is None:
        return StaffMailboxes()
    if not isinstance(block, dict):
        return StaffMailboxes(error=f"{CONFIG_BLOCK} must be a mapping")
    raw = block.get("mailboxes")
    if raw is None:
        return StaffMailboxes()
    if not isinstance(raw, list):
        return StaffMailboxes(error=f"{CONFIG_BLOCK}.mailboxes must be a list of addresses")
    good: list[str] = []
    bad: list[str] = []
    for entry in raw:
        addr = normalize_address(entry)
        if addr is None:
            bad.append(str(entry)[:80])
        elif addr not in good:
            good.append(addr)
    return StaffMailboxes(mailboxes=tuple(good), rejected=tuple(bad))


def parse_send_as_approvers(scope: object) -> tuple[tuple[str, ...], str | None]:
    """``(approvers, error)`` from the ``scope`` mapping. Every
    ``staff_send_as`` address is kept, lowercased, whether or not it would pass
    as a readable address: an approver entry is a refusal, so a malformed one
    must still refuse, never be dropped."""
    unreadable = "so the send-as approvers cannot be told apart"
    if scope is None:
        return (), None
    if not isinstance(scope, dict):
        return (), f"scope must be a mapping, {unreadable}"
    raw = scope.get("staff_send_as")
    if raw is None:
        return (), None
    if not isinstance(raw, list):
        return (), f"scope.staff_send_as must be a list, {unreadable}"
    approvers: list[str] = []
    for entry in raw:
        address = entry.get("address") if isinstance(entry, dict) else None
        if isinstance(address, str) and address.strip():
            approvers.append(address.strip().lower())
    return tuple(approvers), None


def from_customer_yaml(data: object) -> StaffMailboxes:
    """Both halves of the rule from the whole parsed customer.yaml: the read
    list and the approvers it can never reach. A root that is not a mapping
    authors nothing, so it refuses everything."""
    if not isinstance(data, dict):
        return StaffMailboxes()
    reads = parse_staff_mailboxes(data.get(CONFIG_BLOCK))
    approvers, approver_error = parse_send_as_approvers(data.get("scope"))
    return StaffMailboxes(
        mailboxes=reads.mailboxes,
        error=reads.error or approver_error,
        rejected=reads.rejected,
        approvers=approvers,
    )


def decide(
    mailbox: object, *, own_mailbox: str, config: StaffMailboxes, own_mailbox_hint: str = ""
) -> tuple[str | None, str | None]:
    """``(address, None)`` when the firm authored this mailbox and nothing
    forbids it, else ``(None, reason)``. The reason names what IS authored, so
    a refusal says what would make the read possible."""
    if config.error:
        return None, f"{config.error}; no staff mailbox can be read until it is fixed"
    addr = normalize_address(mailbox)
    if addr is None:
        return None, "the mailbox is not a plain email address"
    if addr == (own_mailbox or "").strip().lower():
        return None, f"that is the Operator's own mailbox{own_mailbox_hint}"
    if addr in config.approvers:
        return None, (
            f"{addr} approves drafts sent in their name (scope.staff_send_as), and their "
            "mailbox holds the approve links for those drafts, so the Operator never reads it, "
            f"even if it is also listed under {CONFIG_BLOCK}"
        )
    if addr not in config.mailboxes:
        allowed = ", ".join(config.mailboxes) or "none"
        return None, (
            f"{addr} is not a staff mailbox the firm has authored for reading "
            f"(authored: {allowed}). Only the firm can add one."
        )
    return addr, None
