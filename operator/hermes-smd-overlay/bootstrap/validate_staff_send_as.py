"""``scope.staff_send_as`` validation (ss ADR 0089), split out of ``validate.py``.

Moved here (module-size ratchet) when ADR 0089 section 9 added a second
transport. Two halves:

* the entry shape, unchanged: exact person addresses, a name, no duplicates,
  each reachable under the seat's counterparty surface;
* the transport (``via``), new: one transport per seat, and for ``via: gmail``
  every precondition the broker's Gmail transport needs, so a seat that could
  not send safely is refused at authoring rather than discovered at send.

Mirrored by the console validator (``src/lib/operator/customer-yaml/
sections-staff-send-as.ts``, ``checkStaffSendAsTransport``); the parity
fixtures pin the two together.
"""

from __future__ import annotations

from typing import Any

#: The transports an entry may author (``workspace_broker/send_as_transport``).
VIAS = ("msgraph", "gmail")

#: Scope sets under which the DWD key can both SEARCH the owner's SENT mail (the
#: forgery guard) and SEND (``users.messages.send``). ``gmail.send`` alone
#: cannot search, so the guard could never pass: refused here, not at send.
GMAIL_SCOPE_SETS: tuple[frozenset[str], ...] = (
    frozenset({"https://www.googleapis.com/auth/gmail.modify"}),
    frozenset({"https://mail.google.com/"}),
    frozenset(
        {
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/gmail.readonly",
        }
    ),
)


def _canon(raw: Any) -> str | None:
    from bootstrap.validate import _canon_roster_address  # lazy: validate imports this module

    return _canon_roster_address(raw) if isinstance(raw, str) else None


def validate_staff_send_as(cfg: dict[str, Any], errors: list[str]) -> None:
    """Validate ``scope.staff_send_as`` (ss ADR 0089).

    The staff a send may carry as ``from``, each ``{address, name}``. The
    address is an EXACT person address (``local@domain``): sending as a domain
    is meaningless, and a person is who approves. The name is required and
    non-empty because it is what the approval email and the Operator call that
    person, and a name the seat had to invent is not one it may show. Addresses
    are compared case-insensitively and a duplicate is rejected so the list
    stays reviewable as the literal set of people the Operator may write as.
    Absent is valid (nobody may be sent as).

    Each address must also be REACHABLE: covered, exactly or by an ``@domain``
    grant, by ``scope.inbound_allow_from``, ``scope.admins`` or
    ``scope.outbound_roster``. The approval arrives as an email from that person
    and the approval request goes to them, so a staff member the seat can
    neither hear from nor write to could never approve anything.
    """
    scope = cfg.get("scope")
    if not isinstance(scope, dict):
        return
    raw = scope.get("staff_send_as")
    if raw is None:
        return
    if not isinstance(raw, list):
        errors.append(f"scope.staff_send_as must be a list; got {type(raw).__name__}")
        return
    reachable = _staff_reachable_set(scope)
    seen: set[str] = set()
    for i, entry in enumerate(raw):
        _validate_entry(entry, f"scope.staff_send_as[{i}]", reachable, seen, errors)
    validate_staff_send_as_transport(cfg, raw, errors)


def _validate_entry(
    entry: Any, prefix: str, reachable: set[str], seen: set[str], errors: list[str]
) -> None:
    if not isinstance(entry, dict):
        errors.append(f"{prefix}: must be a mapping with address and name")
        return
    address = entry.get("address")
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append(f"{prefix}.name: required non-empty string")
    if not isinstance(address, str) or not address.strip():
        errors.append(f"{prefix}.address: required non-empty string email address")
        return
    canon = _canon(address)
    if canon is None or canon.startswith("@"):
        errors.append(
            f"{prefix}.address: {address!r} must be an exact person address (local@domain)"
        )
        return
    if canon in seen:
        errors.append(f"{prefix}.address: duplicate staff_send_as address {canon!r}")
        return
    seen.add(canon)
    if canon not in reachable and f"@{canon.partition('@')[2]}" not in reachable:
        errors.append(
            f"{prefix}.address: {canon!r} is not covered by scope.inbound_allow_from, "
            "scope.admins or scope.outbound_roster, so the seat could neither ask "
            "them nor hear their approval"
        )


def _staff_reachable_set(scope: dict[str, Any]) -> set[str]:
    """Canonical addresses and ``@domain`` grants the seat can exchange mail with."""
    out: set[str] = set()
    for key in ("inbound_allow_from", "admins"):
        entries = scope.get(key)
        if isinstance(entries, list):
            out.update(c for c in (_canon(e) for e in entries) if c)
    roster = scope.get("outbound_roster")
    if isinstance(roster, list):
        out.update(
            c for c in (_canon(e.get("address")) for e in roster if isinstance(e, dict)) if c
        )
    return out


def validate_staff_send_as_transport(
    cfg: dict[str, Any], raw: list[Any], errors: list[str]
) -> None:
    """``via`` (ADR 0089 section 9): one transport per seat, and on ``gmail``
    every precondition the broker's Gmail transport needs. Fail-closed."""
    entries = [e for e in raw if isinstance(e, dict)]
    vias: set[str] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        via = entry.get("via", "msgraph")
        if via not in VIAS:
            errors.append(
                f"scope.staff_send_as[{i}].via: must be one of {', '.join(VIAS)}; got {via!r}"
            )
            return
        vias.add(via)
        if via == "msgraph" and "mailbox" in entry:
            errors.append(
                f"scope.staff_send_as[{i}].mailbox: only a via: gmail entry names a mailbox"
            )
    if len(vias) > 1:
        errors.append(
            "scope.staff_send_as: every entry must author the same via (one transport per seat)"
        )
        return
    if vias == {"gmail"}:
        _validate_gmail(cfg, entries, errors)


def _validate_gmail(cfg: dict[str, Any], entries: list[dict[str, Any]], errors: list[str]) -> None:
    google = cfg.get("google_auth")
    if not isinstance(google, dict) or google.get("mode") != "dwd":
        errors.append(
            "scope.staff_send_as: via: gmail needs google_auth.mode: dwd (the broker sends by impersonating the mailbox)"
        )
        return
    raw_scopes = google.get("scopes")
    scopes = {
        s.strip()
        for s in (raw_scopes if isinstance(raw_scopes, list) else [])
        if isinstance(s, str)
    }
    if not any(needed <= scopes for needed in GMAIL_SCOPE_SETS):
        errors.append(
            "scope.staff_send_as: via: gmail needs google_auth.scopes to include gmail.modify "
            "(or mail.google.com, or gmail.send with gmail.readonly): the broker must both send "
            "and search the owner's Sent mail"
        )
    email = (
        (cfg.get("connectors") or {}).get("Email")
        if isinstance(cfg.get("connectors"), dict)
        else None
    )
    if not isinstance(email, dict) or email.get("adapter") != "agentmail":
        errors.append(
            "scope.staff_send_as: via: gmail needs connectors.Email.adapter: agentmail "
            "(the approval email and the owner's reply ride the seat's AgentMail inbox)"
        )
    send_as = _authored_send_as(google)
    for i, entry in enumerate(entries):
        _validate_gmail_entry(entry, f"scope.staff_send_as[{i}]", send_as, errors)


def _authored_send_as(google: dict[str, Any]) -> dict[str, set[str]]:
    """``{mailbox: addresses it may send as}``: the DWD subject sends as itself;
    a managed mailbox as itself and its authored ``send_as`` list."""
    out: dict[str, set[str]] = {}
    subject = _canon(google.get("subject"))
    if subject:
        out[subject] = {subject}
    managed = google.get("managed_mailboxes")
    for mailbox in managed if isinstance(managed, list) else []:
        address = _canon(mailbox.get("address")) if isinstance(mailbox, dict) else None
        if not address:
            continue
        listed = mailbox.get("send_as")
        aliases = {
            c for c in (_canon(a) for a in (listed if isinstance(listed, list) else [])) if c
        }
        out.setdefault(address, set()).update({address, *aliases})
    return out


def _validate_gmail_entry(
    entry: dict[str, Any], prefix: str, send_as: dict[str, set[str]], errors: list[str]
) -> None:
    mailbox = _canon(entry.get("mailbox"))
    if mailbox is None or mailbox.startswith("@"):
        errors.append(
            f"{prefix}.mailbox: a via: gmail entry needs the exact Google Workspace mailbox it belongs to"
        )
        return
    if mailbox not in send_as:
        errors.append(
            f"{prefix}.mailbox: {mailbox!r} is not google_auth.subject or a google_auth.managed_mailboxes "
            "address, so the broker could never impersonate it"
        )
        return
    address = _canon(entry.get("address"))
    if address and address not in send_as[mailbox]:
        errors.append(
            f"{prefix}.address: {address!r} is not {mailbox!r} or one of its authored "
            "google_auth.managed_mailboxes[].send_as addresses"
        )


__all__ = ["GMAIL_SCOPE_SETS", "VIAS", "validate_staff_send_as", "validate_staff_send_as_transport"]
