"""The staff mailboxes a firm has authored for the Operator to READ.

Every other tool in this connector is pinned to the Operator's own mailbox. A
firm can also let the Operator read a named staff member's mailbox (the office
manager's, say), so it can find a letter she received without her forwarding it.
That permission is the firm's to give, so it is authored, never inferred:

    staff_mailbox_reads:
      mailboxes:
        - someone@firm.example

on the seat's own customer.yaml, read at call time (the vendor_invoice_intake
shape: no translate step). A mailbox not on the list is refused before any Graph
call. A missing file, a missing block, or an empty list refuses everything; an
unparseable file refuses everything and says why. Nothing here can send, draft,
move or delete: the staff tools are reads, and the send path stays pinned.

Tenant-side, the firm must ALSO add the mailbox to the READ app's Exchange
ApplicationAccessPolicy scope group. This list is the code-layer belt; that
policy is the braces. Either one missing and the read fails.

AN APPROVER'S MAILBOX IS NEVER READABLE, EVEN WHEN AUTHORED (ADR 0089
amendment 5a). A person on ``scope.staff_send_as`` receives approval emails
carrying Send it buttons, and the broker keeps no Sent Items copy, so that
person's inbox is the only place the signed link exists. If the Operator could
read it, the Operator could lift its own approve link and press it. So any
address on ``scope.staff_send_as`` in the same file is refused here before any
Graph call, whatever ``staff_mailbox_reads`` says; the console validator refuses
a config that authors the overlap, and boot smoke asks Microsoft about every
approver. A ``scope.staff_send_as`` that cannot be read as a list refuses every
staff read, because an approver list that cannot be evaluated must not read as
"nobody is an approver".
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace

CONFIG_BLOCK = "staff_mailbox_reads"
DEFAULT_CUSTOMER_YAML = "/var/lib/smd-config/customer.yaml"
CUSTOMER_YAML_ENV = "SMD_CUSTOMER_YAML_PATH"

# Deliberately narrow: the address is interpolated into a Graph URL path, so a
# character that could change the path (/ ? # % \ or whitespace) never passes,
# even from the authored list.
_ADDRESS_RE = re.compile(r"^[a-z0-9._+'-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+$")


@dataclass(frozen=True)
class StaffMailboxes:
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
    if scope is None:
        return (), None
    if not isinstance(scope, dict):
        return (), "scope must be a mapping, so the send-as approvers cannot be told apart"
    raw = scope.get("staff_send_as")
    if raw is None:
        return (), None
    if not isinstance(raw, list):
        return (), "scope.staff_send_as must be a list, so the send-as approvers cannot be told apart"
    approvers: list[str] = []
    for entry in raw:
        address = entry.get("address") if isinstance(entry, dict) else None
        if isinstance(address, str) and address.strip():
            approvers.append(address.strip().lower())
    return tuple(approvers), None


def load_staff_mailboxes(path: str | None = None) -> StaffMailboxes:
    """Read the block off the seat's live customer.yaml at call time, so an
    authored change reaches the next read without a restart."""
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
    if not isinstance(data, dict):
        return StaffMailboxes()
    reads = parse_staff_mailboxes(data.get(CONFIG_BLOCK))
    approvers, approver_error = parse_send_as_approvers(data.get("scope"))
    return replace(reads, approvers=approvers, error=reads.error or approver_error)


def authorize(
    mailbox: object, *, own_mailbox: str, config: StaffMailboxes | None = None
) -> tuple[str | None, str | None]:
    """``(address, None)`` when the firm authored this mailbox, else
    ``(None, reason)``. The reason names what IS authored, so a refusal says
    what would make the read possible."""
    cfg = config if config is not None else load_staff_mailboxes()
    if cfg.error:
        return None, f"refused: {cfg.error}; no staff mailbox can be read until it is fixed"
    addr = normalize_address(mailbox)
    if addr is None:
        return None, "refused: not a plain email address"
    if addr == (own_mailbox or "").strip().lower():
        return None, "refused: that is the Operator's own mailbox; use list_messages or read_message"
    if addr in cfg.approvers:
        return None, (
            f"refused: {addr} approves drafts sent in their name (scope.staff_send_as), and their "
            "mailbox holds the approve links for those drafts, so the Operator never reads it, "
            "even if it is also listed under staff_mailbox_reads"
        )
    if addr not in cfg.mailboxes:
        allowed = ", ".join(cfg.mailboxes) or "none"
        return None, (
            f"refused: {addr} is not a staff mailbox the firm has authored for reading "
            f"(authored: {allowed}). Only the firm can add one."
        )
    return addr, None
