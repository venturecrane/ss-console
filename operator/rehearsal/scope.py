"""The hard line: what the shadow firm is allowed to touch.

WHY THIS IS CODE AND NOT A COMMENT (ss#2258). On 2026-08-11 four fabricated
messages reached a real client principal from a seat that was being exercised.
The rule "do not drive a client seat" existed in prose at the time. Prose does
not fail a run. So the rule lives here as a validation the runner executes
before any scenario is loaded, and a violation raises rather than warns.

TWO GATES, BOTH FAIL-CLOSED:

1. ADDRESS ALLOWLIST. Every address a scenario may speak AS, speak TO, or even
   NAME IN A BODY must appear in ``DRIVABLE_ADDRESSES``. The body scan is not
   paranoia: the instruction-injection scenario's whole content is an address
   plus an instruction to mail it, so an unallowlisted address smuggled into
   prose is exactly the shape that reaches a real person.

2. SEAT-KIND ALLOWLIST, derived from the seat's own ``customer.yaml``
   descriptor rather than from a list of slugs kept here. A slug list rots the
   moment a seat is added; ``seat.kind`` is authored on every seat and its
   conformance is already pinned (bin/tests/test_seat_descriptor_conformance.py).
   ``customer`` and ``internal`` are refused by name, with the reason, so a
   future reader sees a decision instead of an omission.

An unknown seat kind is refused, not permitted. "Cannot evaluate" must never
read as "allowed" -- the same posture the Law 2 engagement guard takes.
"""

from __future__ import annotations

import re

#: Every address the shadow firm may drive, with what it is. All are SMD-owned
#: harness mailboxes on the shared AgentMail account; none belongs to a client,
#: and none is read by anyone outside SMD. Kept in sync by hand with
#: ``operator/bin/reconcile-sends.py::KNOWN_NON_SEAT_INBOXES`` (which is the
#: reconciler's own allowlist of seat-less inboxes); the registry test asserts
#: every entry here is one the reconciler also knows, so an address that is
#: drivable but unrecognised by the egress control cannot exist.
#:
#: ADDING AN ENTRY COSTS A PR. That review is the control.
DRIVABLE_ADDRESSES: dict[str, str] = {
    "ss-probe-admin@agentmail.to": "SS probe harness, admin-classed driver (on staging scope.admins)",
    "ss-probe-runner@agentmail.to": "SS probe harness, rostered non-admin driver",
    "sim-opposing-counsel@agentmail.to": "Opposing-counsel simulator (Halloran Sload LLP rig)",
    "ap-client-standin@agentmail.to": "Rehearsal stand-in for a client-class recipient",
    "ap-records-standin@agentmail.to": "Rehearsal stand-in for a records-vendor recipient",
}

#: Seat kinds the shadow firm may drive, and why each is safe to play hostile
#: against. Read from the target seat's ``seat.kind`` descriptor.
DRIVABLE_SEAT_KINDS: dict[str, str] = {
    "proving": "the rehearsal rig; seeded fictional matters, no client data",
    "sandbox": "throwaway seat wired to nothing real",
    "preprod": "the permanent pre-production mirror; serves nobody",
}

#: Seat kinds refused by name. Naming them (rather than defaulting the refusal)
#: keeps the reason in the record where the next reader will look.
REFUSED_SEAT_KINDS: dict[str, str] = {
    "customer": "a real firm's seat; its mailbox reaches its clients",
    "internal": "SMD's own production seat; its mail reaches real correspondents",
}

#: The literal a scenario uses in place of an address to mean "the seat's own
#: inbox". Resolved by the runner from the seat's authored Email connector, so
#: no scenario ever hardcodes a seat mailbox.
SEAT_TOKEN = "seat"

#: Domains reserved by RFC 2606 / RFC 6761. They cannot be registered, so mail
#: addressed to one cannot reach a person -- which is exactly why the rig's
#: seeded matters use them for party contacts.
#:
#: A reserved address may be NAMED IN PROSE (a scenario has to be able to say
#: "send matter 101's summary to matter 102's client" using the party addresses
#: the rig actually holds) but is never a drive endpoint and never an address
#: this suite claims to observe: we can only read mailboxes we own.
RESERVED_NONDELIVERABLE_SUFFIXES: tuple[str, ...] = (
    ".example.com",
    ".example.net",
    ".example.org",
    "@example.com",
    "@example.net",
    "@example.org",
    ".example",
    ".invalid",
    ".test",
    ".localhost",
)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


class ScopeViolation(RuntimeError):
    """A scenario or target that the shadow firm refuses to touch.

    Always fatal. There is no flag that downgrades this to a warning, because
    the incident it exists to prevent was itself a downgraded warning.
    """


def addresses_in(text: str) -> set[str]:
    """Every email-shaped token in a block of prose, lowercased."""
    return {m.group(0).lower() for m in _EMAIL_RE.finditer(text or "")}


def assert_address_drivable(address: str, *, where: str) -> None:
    """Refuse any address that is not an authored harness mailbox."""
    normalized = (address or "").strip().lower()
    if not normalized:
        raise ScopeViolation(f"{where}: empty address")
    if normalized not in DRIVABLE_ADDRESSES:
        raise ScopeViolation(
            f"{where}: {normalized} is not a drivable harness address. The shadow firm may "
            f"only speak as, speak to, or name: {', '.join(sorted(DRIVABLE_ADDRESSES))}. "
            "A client-visible address never appears in a scenario, in any position."
        )


def is_reserved_nondeliverable(address: str) -> bool:
    normalized = (address or "").strip().lower()
    return any(normalized.endswith(suffix) for suffix in RESERVED_NONDELIVERABLE_SUFFIXES)


def assert_text_names_no_foreign_address(text: str, *, where: str) -> None:
    """Refuse a body that names an address which could reach a real person.

    A scenario body is content the Operator reads and may act on. An address in
    that prose is a target whether or not the scenario schema calls it one, so
    the only addresses permitted are the harness mailboxes and RFC 2606
    reserved-domain addresses, which cannot resolve to anybody.
    """
    for found in sorted(addresses_in(text)):
        if is_reserved_nondeliverable(found):
            continue
        assert_address_drivable(found, where=f"{where} (address named in body text)")


def seat_kind(config: dict) -> str | None:
    seat = config.get("seat")
    if not isinstance(seat, dict):
        return None
    kind = seat.get("kind")
    return str(kind) if isinstance(kind, str) else None


def assert_seat_drivable(slug: str, config: dict) -> None:
    """Refuse any seat whose own descriptor does not say it is a rig.

    Derived from the seat's authored ``seat.kind``. An absent or unrecognised
    kind is refused: a seat nobody has classified is treated as if it holds a
    real client's data, exactly as the provisioning templates do.
    """
    kind = seat_kind(config)
    if kind is None:
        raise ScopeViolation(
            f"seat {slug}: customer.yaml declares no seat.kind, so this seat cannot be shown "
            "to be a rig. Refusing. An unclassified seat is treated as a client seat."
        )
    if kind in REFUSED_SEAT_KINDS:
        raise ScopeViolation(
            f"seat {slug}: seat.kind={kind} -- {REFUSED_SEAT_KINDS[kind]}. The shadow firm "
            "never plays hostile against it. Drive a proving, sandbox, or preprod seat."
        )
    if kind not in DRIVABLE_SEAT_KINDS:
        raise ScopeViolation(
            f"seat {slug}: seat.kind={kind} is not a kind this suite knows. Refusing rather "
            "than guessing. Drivable kinds: " + ", ".join(sorted(DRIVABLE_SEAT_KINDS))
        )


def assert_scenario_in_scope(scenario: dict, *, source: str) -> None:
    """Walk one scenario and refuse every address it touches, in any position.

    Called by the registry loader, so a scenario that violates the hard line
    cannot be loaded -- let alone executed.
    """
    for leg in scenario.get("legs") or []:
        leg_id = leg.get("id", "?")
        where = f"{source}: leg {leg_id}"
        drive = leg.get("drive") or {}
        sender = drive.get("as")
        if sender is not None:
            assert_address_drivable(str(sender), where=f"{where} drive.as")
        target = drive.get("to")
        if target is not None and str(target) != SEAT_TOKEN:
            assert_address_drivable(str(target), where=f"{where} drive.to")
        assert_text_names_no_foreign_address(str(drive.get("body") or ""), where=where)
        assert_text_names_no_foreign_address(str(drive.get("subject") or ""), where=where)
        for expectation in leg.get("expect") or []:
            named = expectation.get("address")
            if named is not None:
                # An observation target, not a prose mention: we can only read a
                # mailbox we own, so the reserved-domain relaxation never applies
                # here. A `no_send_to` naming an inbox nobody reads would be an
                # expectation that can never fail.
                assert_address_drivable(str(named), where=f"{where} expect.{expectation.get('kind')}.address")
            assert_text_names_no_foreign_address(
                str(expectation.get("metadata_contains") or ""),
                where=f"{where} expect.{expectation.get('kind')}.metadata_contains",
            )
