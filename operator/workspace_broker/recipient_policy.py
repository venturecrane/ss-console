"""Who a seat may write to, decided by the broker from the seat's own config.

Extracted from ``agentmail_auth`` when the second mail channel arrived (ss#2258
msgraph wave). Nothing here is vendor-specific: an address is an address, and the
authored counterparty surface is a property of the SEAT, not of whichever
transport happens to carry the message. Keeping one copy is the point — two
fences that disagree about what an address is are two different fences, and the
one that is wrong is the one an attacker uses.

WHY THIS FENCE EXISTS. On 2026-08-03/05/07/09 a rehearsal seat sent four
fabricated emails to a real client principal with **no audit row for any of
them**. Zero rows means the sending path never traversed the gateway's trust
hook, so no in-agent control could have stopped it — including controls we might
add — because the agent process held a credential that answers to whatever
reaches it. The Captain's requirement afterwards was exact: the seat "should
never have been able to send to an unapproved email address no matter where it
came from." "No matter where it came from" is only enforceable by whoever holds
the key, which is the broker.

WHAT THIS IS NOT. This is a *counterparty* fence: it answers "may this seat
contact this human at all?", never "was this particular send approved?" Approval
and the exposure ceiling stay in the gateway, the only place that knows about
pending approvals. The broker cannot see approval state and does not pretend to;
what it guarantees is that an unapproved *recipient* is unreachable, and that
every attempt leaves a row.

WHY THE AUTHORED UNION AND NOT ``outbound_roster`` ALONE. The first law-firm seat
authors no ``outbound_roster`` at all; its principals are named in
``scope.admins`` and reachable via a whole-domain grant in
``scope.inbound_allow_from``. A roster-only rule would refuse every legitimate
send on that seat. The union of what the seat's own config names is both correct
there and sufficient for the incident: the pilot's authored set is four SMD-owned
addresses plus two stand-in inboxes, and the recipient of those four messages is
on none of them, so all four are refused.
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from email.utils import parseaddr
from pathlib import Path
from typing import Any

import yaml


def canonicalize(text: str) -> str:
    """The ONE canonical form every comparison at this fence uses (ss#2284).

    ``unicodedata.normalize("NFC", …).strip().lower()`` — identical to the
    runtime classifier's ``_canonicalize_roster_entry``
    (``shared/recipient_classifier.py``), deliberately.

    NFC is the load-bearing part. ``é`` has two valid encodings (precomposed
    U+00E9, or ``e`` + U+0301), they are the same character to every human and
    every mail system, and ``.lower()`` alone leaves them unequal. Without this,
    a roster authored in one form and a recipient arriving in the other simply
    never match.

    Both directions of that mistake are real here, and one of them is dangerous:

    * on the ALLOW set a mismatch REFUSES — a legitimate client contact becomes
      silently unreachable, which is safe but wrong;
    * on ``domain_blocks`` a mismatch **fails open** — an authored block written
      in one form would not catch a recipient arriving in the other.

    NOT ``casefold()``, which some canonicalization advice recommends: casefold
    maps ``ß`` to ``ss``, so ``straße@x`` and ``strasse@x`` — different mailboxes —
    would collide, and on an allowlist a collision WIDENS the fence. ``lower()``
    can only ever fail to match, never over-match, which is the correct direction
    for a deny-by-default control.
    """
    return unicodedata.normalize("NFC", text).strip().lower()


def normalize_address(value: Any) -> str:
    """Reduce any address shape to its bare, canonical form.

    Mail carries addresses as ``"Display Name <addr@host>"`` at least as often as
    bare, and a fence that compares the display form against a roster refuses
    everyone — so this parses rather than merely lowercasing. Mirrors the
    overlay's ``inbound_message._bare_address`` deliberately: the two must agree
    on what an address IS, or the reply lane and the fence disagree about who
    sent a message. Mappings are tolerated for the same reason it does — and
    Graph in particular hands every address over as
    ``{"emailAddress": {"address": …}}``, so the nested form is not a nicety.
    """
    if isinstance(value, dict):
        for key in ("address", "email", "emailAddress"):
            nested = value.get(key)
            if isinstance(nested, dict):
                nested = nested.get("address") or nested.get("email")
            if isinstance(nested, str) and nested.strip():
                value = nested
                break
        else:
            return ""
    if not isinstance(value, str):
        return ""
    # Canonicalize BEFORE parsing: a decomposed character inside a display name
    # must not change how the address is extracted, and canonicalizing once at
    # the boundary is the whole point (ss#2284 — five roster matchers exist
    # seat-side and they do not agree; this fence adds no sixth spelling).
    parsed = parseaddr(canonicalize(value))[1]
    # parseaddr yields "" for input it cannot read as an address; falling back to
    # the raw string would let an unparseable value be compared against the
    # roster, and the only safe comparison for garbage is one that fails.
    return canonicalize(parsed) if parsed else ""


def sender_key(value: Any) -> str | None:
    """The audit row's answer to "which person" — ``sha256`` of the canonical
    address, or ``None`` when there is no address (ss#2497).

    HASHED, NOT STORED. The audit record is a file a firm exports and forwards,
    and the issue's non-goal is explicit: no row carries a raw address. A firm
    that holds its own people's addresses reproduces the key and finds every row
    that person caused; a reader who does not already know the address learns
    nothing. A rostered display name may ride alongside as a LABEL and is never
    the identity, because a display name is attacker-controlled text in every
    mail system.

    Built on :func:`normalize_address` and therefore on :func:`canonicalize`,
    which is the whole reason it lives in THIS module rather than beside a
    writer. A key is only a join if both ends reduce the same human to the same
    bytes, and the overlay's twin (``shared/audit_contract.sender_key``) applies
    the identical NFC + strip + lower before hashing. Hashing the raw string
    instead would give the same person two identities the day one mail client
    sends a decomposed ``é``.
    """
    address = normalize_address(value)
    if not address:
        return None
    return hashlib.sha256(address.encode("utf-8")).hexdigest()


def domain_of(address: str) -> str:
    _, separator, domain = address.partition("@")
    return domain if separator else ""


def split_authored(entries: Any) -> tuple[set[str], set[str]]:
    """Split authored entries into (exact addresses, domain grants).

    An entry beginning with ``@`` is a whole-domain grant (a law-firm seat
    authors ``@<its-own-domain>`` so every person at the firm is reachable).
    Anything else is an exact address. Entries that are not usable strings are
    DROPPED rather than guessed at — an unparseable entry must never widen the
    fence.
    """
    exact: set[str] = set()
    domains: set[str] = set()
    for entry in entries or []:
        # scope.outbound_roster entries are {address, class, note?} mappings;
        # inbound_allow_from and admins are bare strings.
        if isinstance(entry, dict):
            entry = entry.get("address")
        if not isinstance(entry, str):
            continue
        raw = canonicalize(entry)
        if not raw:
            continue
        # A domain grant is checked BEFORE address parsing: "@firm.example" is
        # not a valid address, so parseaddr discards it, and a grant silently
        # dropped here would refuse every person at that firm.
        if raw.startswith("@"):
            if len(raw) > 1:
                domains.add(raw[1:])
            continue
        parsed = normalize_address(raw)
        if parsed and "@" in parsed:
            exact.add(parsed)
    return exact, domains


def split_blocks(entries: Any) -> set[str]:
    """Domains denied to this seat, parsed from ``scope.domain_blocks``.

    Deliberately NOT ``split_authored``, because the allow list and the deny
    list need OPPOSITE failure directions and one parser cannot have both:

    * On an ALLOW list, an entry we cannot confidently read must be DROPPED. A
      bare ``firm.example`` written where ``@firm.example`` was meant would
      otherwise turn a typo into a whole-domain grant — a silent widening.
    * On a DENY list, that same entry must BLOCK. Dropping it fails OPEN, the
      one direction a deny control must never fail.

    Sharing ``split_authored`` gave the deny list the allow list's failure
    direction, and the consequence was live: ``domain_blocks: ['firm.example']``
    parsed to nothing and fenced nobody, while ``authored_policy``'s own
    docstring and ``tests/customer-yaml-validator.test.ts`` both presented the
    bare form as the way to write one. Found 2026-08-18 while fencing the first
    production client seat away from its own firm during bring-up; the config
    would have looked correct, validated, and protected no one.

    So every spelling an author might reasonably use resolves to a domain here:
    ``@firm.example``, bare ``firm.example``, and ``someone@firm.example`` — a
    full address blocks its whole domain, the conservative reading of a block
    list and what this module has always promised.
    """
    blocked: set[str] = set()
    for entry in entries or []:
        if isinstance(entry, dict):
            entry = entry.get("address")
        if not isinstance(entry, str):
            continue
        raw = canonicalize(entry)
        if not raw:
            continue
        if raw.startswith("@"):
            candidate = raw[1:]
        elif "@" in raw:
            # A full address, possibly in display form. Block its domain.
            candidate = domain_of(normalize_address(raw)) or domain_of(raw)
        else:
            # A bare domain. On this list — and ONLY this list — that blocks.
            candidate = raw
        if candidate:
            blocked.add(candidate)
    return blocked


@dataclass(frozen=True)
class RecipientPolicy:
    """The authored counterparty surface for one seat, read from customer.yaml."""

    exact: frozenset[str]
    domains: frozenset[str]
    blocked_domains: frozenset[str]
    #: Addresses/domains permitted to receive a REPLY — narrower than the send
    #: fence, because a reply goes to whoever wrote in and anyone on the internet
    #: can write in. Sourced from ``inbound_allow_from`` alone.
    reply_exact: frozenset[str]
    reply_domains: frozenset[str]

    def _blocked(self, address: str) -> bool:
        return domain_of(address) in self.blocked_domains

    def allows_recipient(self, address: str) -> bool:
        """May this seat send to this address at all? Deny overrides allow."""
        value = normalize_address(address)
        if not value or "@" not in value or self._blocked(value):
            return False
        return value in self.exact or domain_of(value) in self.domains

    def allows_reply_to(self, address: str) -> bool:
        """May this seat REPLY to this sender? (``inbound_allow_from`` only.)"""
        value = normalize_address(address)
        if not value or "@" not in value or self._blocked(value):
            return False
        return value in self.reply_exact or domain_of(value) in self.reply_domains


def _scope(customer_path: Path) -> dict[str, Any]:
    data = yaml.safe_load(customer_path.read_text(encoding="utf-8")) or {}
    scope = data.get("scope") or {}
    if not isinstance(scope, dict):
        raise RuntimeError("customer.yaml scope must be an object")
    return scope


def authored_policy(customer_path: Path) -> RecipientPolicy:
    """Derive the seat's counterparty surface from its own authored config.

    The union of ``scope.outbound_roster`` (typed outbound authorization),
    ``scope.inbound_allow_from`` (who may talk to the seat, hence who the seat may
    answer), and ``scope.admins`` (the Named Administrators), minus
    ``scope.domain_blocks``.

    An **empty** authored surface yields a policy that permits nothing. That is
    deliberate: a seat whose config names no counterparty has no one to write to,
    and "unconfigured" must read as a safety state, never as permission.

    The allow inputs and ``domain_blocks`` are parsed by DIFFERENT functions on
    purpose — see ``split_blocks``. An ambiguous entry must be dropped from an
    allow list and honoured on a deny list, and until 2026-08-18 both went
    through ``split_authored``, so an authored bare-domain block silently fenced
    nobody.
    """
    scope = _scope(customer_path)
    roster_exact, roster_domains = split_authored(scope.get("outbound_roster"))
    inbound_exact, inbound_domains = split_authored(scope.get("inbound_allow_from"))
    admin_exact, admin_domains = split_authored(scope.get("admins"))
    return RecipientPolicy(
        exact=frozenset(roster_exact | inbound_exact | admin_exact),
        domains=frozenset(roster_domains | inbound_domains | admin_domains),
        # A bare domain in domain_blocks ("evil.com"), an @-prefixed one, and a
        # full address all block the domain — the conservative reading of a
        # block list.
        blocked_domains=frozenset(split_blocks(scope.get("domain_blocks"))),
        reply_exact=frozenset(inbound_exact),
        reply_domains=frozenset(inbound_domains),
    )


__all__ = [
    "RecipientPolicy",
    "authored_policy",
    "canonicalize",
    "domain_of",
    "normalize_address",
    "split_authored",
    "split_blocks",
]
