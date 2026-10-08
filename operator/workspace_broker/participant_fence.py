"""The participant fence: a firm person gets Operator mail only if they were on
the request it answers, or the firm authored them for the job sending it.

WHY (2026-10-07). a law firm's Operator emailed an attorney who was not on the
requester's request, about a held chronology. Her rule, verbatim from the letter: "prevent
the operator from sending the attorney emails when not originated on the
original request." Every fence before this one was roster- or domain-based, so
everyone at the firm's own domain was reachable from any turn, about anything.
The firm authors its posture, and the Operator answers to whoever asked.

THE RULE, per recipient (to, cc AND bcc; bcc delivers):

* an SMD address is always allowed;
* an outside recipient (client, vendor, opposing) is not this fence's business:
  the per-skill autonomy ceilings and approvals already govern it, unchanged;
* a FIRM recipient (one the seat's config classifies as staff) is allowed only
  when at least one of these holds:

  (a) they were From, To or Cc on the ANCHOR, the email the send answers, which
      the broker reads out of the mailbox itself (never off the wire);
  (b) the firm authored them for the sending job's LANE: ``rule_dispatch`` is
      ``scope.rule_requests_to``, ``escalation`` is ``escalation.*_recipients``,
      ``skill:<name>`` is that enabled skill's own ``settings.recipient``;
  (c) the anchor came from SMD and they are on ``scope.admins``.

  When the anchor's From is an authored device (``scope.device_senders``), its
  ``replies_to`` counts as a participant, so the scanner keeps working.

A REPLY must answer the anchor itself or a message in the anchor's own
conversation; a reply with no anchor is refused outright.

THIS MODULE IS PURE. It reads customer.yaml and decides; it never touches a
mailbox. Reading the anchor is ``participant_lookup``'s job, handed in as a
callable, so the decision can be replayed offline over a seat's history
(operator/bin/participant-fence-replay.py) with exactly the code that enforces
it. A lookup that cannot complete raises :class:`FenceRefused` with
``FENCE_UNVERIFIABLE``: "could not look" is a refusal, never a transport error
and never a pass.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .recipient_policy import (
    canonicalize,
    domain_of,
    normalize_address,
    sender_key,
    split_authored,
    split_device_replies,
)

#: SMD's own domains. Mirrors the overlay's ``customer_config.OPS_REPLY_DOMAINS``.
SMD_DOMAINS = frozenset({"smd.services", "smdurgan.com"})

FENCE_PARTICIPANTS = "participants"
FENCE_UNVERIFIABLE = "participants_unverifiable"
FENCES = (FENCE_PARTICIPANTS, FENCE_UNVERIFIABLE)

LANE_RULE_DISPATCH = "rule_dispatch"
LANE_ESCALATION = "escalation"
LANE_SKILL_PREFIX = "skill:"
#: The broker's own staff send-as mail (approval emails, notices to the
#: approver). Never accepted off the wire: only ``send_as_transport`` names it.
LANE_SEND_AS = "send_as"
_SKILL_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")

MESSAGE_KINDS = ("graph_message", "agentmail_message")
JOB_KINDS = ("medchron_job", "demand_job", "drafting_job")
RULE_KIND = "rule"
ANCHOR_KINDS = (*MESSAGE_KINDS, *JOB_KINDS, RULE_KIND)
_ANCHOR_FIELD = {
    "graph_message": "graph_message_id",
    "agentmail_message": "message_id",
    "medchron_job": "job_id",
    "demand_job": "job_id",
    "drafting_job": "job_id",
    RULE_KIND: "proposal_id",
}
_ANCHOR_ID = re.compile(r"^[A-Za-z0-9=_.@<>+:-]{1,512}$")

#: What the Operator is told on a refusal, and what the overlay keys its own
#: decision sentence on. The words after it are the decision itself.
REFUSAL_MARKER = "participant fence:"
DECISION = (
    "Nothing was sent. Reply to the person who asked if you still can, otherwise "
    "send only to them, and say who should see this email; send it to nobody else. "
    "SMD has been told."
)


class FenceRefused(Exception):
    """The fence refused this transmit. ``fence`` is the closed vocabulary the
    audit row and the heartbeat carry; ``refused`` is the refused people as
    sender keys (hashes), never addresses."""

    def __init__(self, fence: str, message: str, refused: Iterable[str] = ()) -> None:
        super().__init__(f"{REFUSAL_MARKER} {message} {DECISION}")
        self.fence = fence
        self.refused = sorted({k for k in (sender_key(a) for a in refused) if k})


@dataclass(frozen=True)
class Anchor:
    kind: str
    ident: str

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, _ANCHOR_FIELD[self.kind]: self.ident}


@dataclass(frozen=True)
class Participants:
    """The anchor email as the fence needs it: who sent it, who it reached."""

    sender: str
    to: tuple[str, ...] = ()
    cc: tuple[str, ...] = ()
    conversation_id: str = ""
    message_id: str = ""

    def everyone(self) -> frozenset[str]:
        return frozenset(a for a in (self.sender, *self.to, *self.cc) if a)


@dataclass(frozen=True)
class SeatFacts:
    """What one seat's customer.yaml says about people, read fresh per transmit."""

    typed: tuple[tuple[str, str], ...] = ()
    internal_exact: frozenset[str] = frozenset()
    internal_domains: frozenset[str] = frozenset()
    admins: frozenset[str] = frozenset()
    device_replies: tuple[tuple[str, str], ...] = ()
    lanes: dict[str, frozenset[str]] = field(default_factory=dict)

    def classify(self, address: str) -> str:
        """``smd`` | ``firm`` | ``outside``. Mirrors the overlay classifier
        (``recipient_classifier._classify_one_typed``): the typed outbound
        roster decides first, and only an address it is silent about falls to
        the internal roster (reply roster + the authored firm lists)."""
        value = normalize_address(address)
        domain = domain_of(value)
        if domain in SMD_DOMAINS:
            return "smd"
        matched = {cls for entry, cls in self.typed if entry == value or entry == f"@{domain}"}
        if matched:
            return "firm" if matched == {"firm_staff"} else "outside"
        if value in self.internal_exact or domain in self.internal_domains:
            return "firm"
        return "outside"

    def device_target(self, sender: str) -> str:
        for device, target in self.device_replies:
            if device == sender:
                return target
        return ""


def _addresses(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value]
    return [a for a in (normalize_address(v) for v in items if isinstance(v, (str, dict))) if "@" in a]


def _roster_entry(raw: str) -> str:
    """A typed roster entry as compared: ``@domain`` kept, an address canonical."""
    value = canonicalize(raw)
    return value if value.startswith("@") else normalize_address(value)


def _skill_lanes(personas: Any) -> dict[str, frozenset[str]]:
    lanes: dict[str, frozenset[str]] = {}
    for persona in personas if isinstance(personas, list) else []:
        skills = persona.get("skills") if isinstance(persona, dict) else None
        for skill in skills if isinstance(skills, list) else []:
            if not isinstance(skill, dict) or skill.get("enabled") is not True:
                continue
            name, settings = skill.get("name"), skill.get("settings")
            if not isinstance(name, str) or not isinstance(settings, dict):
                continue
            found = _addresses(settings.get("recipient"))
            if found:
                key = LANE_SKILL_PREFIX + name
                lanes[key] = lanes.get(key, frozenset()) | frozenset(found)
    return lanes


def _escalation_lane(escalation: Any) -> frozenset[str]:
    """Every authored ``*_recipients`` list under ``escalation`` and its
    ``case_alert_routing``: the central, failure and fallback lists."""
    found: set[str] = set()
    blocks = [escalation] if isinstance(escalation, dict) else []
    if blocks and isinstance(escalation.get("case_alert_routing"), dict):
        blocks.append(escalation["case_alert_routing"])
    for block in blocks:
        for key, value in block.items():
            if isinstance(key, str) and key.endswith("_recipients"):
                found.update(_addresses(value))
    return frozenset(found)


def seat_facts(customer_path: Path) -> SeatFacts:
    """Read the seat's own config. Unreadable raises (the caller refuses)."""
    loaded = yaml.safe_load(customer_path.read_text(encoding="utf-8"))
    data: dict[str, Any] = loaded if isinstance(loaded, dict) else {}
    raw_scope = data.get("scope")
    scope: dict[str, Any] = raw_scope if isinstance(raw_scope, dict) else {}
    typed = tuple(
        (_roster_entry(e["address"]), str(e["class"]))
        for e in scope.get("outbound_roster") or []
        if isinstance(e, dict) and isinstance(e.get("address"), str) and isinstance(e.get("class"), str)
    )
    inbound_exact, inbound_domains = split_authored(scope.get("inbound_allow_from"))
    admins, _ = split_authored(scope.get("admins"))
    routing, _ = split_authored(scope.get("rule_requests_to"))
    staff, _ = split_authored(scope.get("staff_send_as"))
    devices = split_device_replies(scope.get("device_senders"))
    lanes = {
        LANE_RULE_DISPATCH: frozenset(routing),
        LANE_ESCALATION: _escalation_lane(data.get("escalation")),
        LANE_SEND_AS: frozenset(staff),
        **_skill_lanes(data.get("personas")),
    }
    return SeatFacts(
        typed=typed,
        internal_exact=frozenset(inbound_exact | admins | routing | staff | {t for _, t in devices}),
        internal_domains=frozenset(inbound_domains),
        admins=frozenset(admins),
        device_replies=devices,
        lanes=lanes,
    )


def parse_anchor(raw: Any, *, kinds: tuple[str, ...] = ANCHOR_KINDS) -> Anchor | None:
    """The anchor a request names, or None. A malformed one is refused, never
    dropped: a caller that sent one meant something by it."""
    if raw is None:
        return None
    kind = raw.get("kind") if isinstance(raw, dict) else None
    if kind not in kinds:
        raise FenceRefused(FENCE_PARTICIPANTS, f"the anchor must be one of {list(kinds)}.")
    key = _ANCHOR_FIELD[str(kind)]
    ident = raw.get(key)
    if set(raw) != {"kind", key} or not isinstance(ident, str) or not _ANCHOR_ID.match(ident.strip()):
        raise FenceRefused(FENCE_PARTICIPANTS, f"a {kind} anchor carries exactly its {key}.")
    return Anchor(str(kind), ident.strip())


def parse_lane(raw: Any) -> str | None:
    """A caller's lane, or None. ``send_as`` is the broker's own and refused here."""
    if raw is None or raw == "":
        return None
    if raw in (LANE_RULE_DISPATCH, LANE_ESCALATION):
        return str(raw)
    if isinstance(raw, str) and raw.startswith(LANE_SKILL_PREFIX) and _SKILL_SLUG.match(raw[len(LANE_SKILL_PREFIX) :]):
        return raw
    raise FenceRefused(FENCE_PARTICIPANTS, f"{raw!r} is not a lane this seat knows.")


def allowed_by_anchor(facts: SeatFacts, found: Participants) -> frozenset[str]:
    """Everyone the anchor makes reachable: its participants, a device's
    authored person, and the admins when SMD wrote it."""
    allowed = set(found.everyone())
    device = facts.device_target(found.sender)
    if device:
        allowed.add(device)
    if domain_of(found.sender) in SMD_DOMAINS:
        allowed |= facts.admins
    return frozenset(allowed)


def check_send(
    facts: SeatFacts,
    recipients: Iterable[str],
    *,
    lane: str | None,
    anchor: Anchor | None,
    resolve: Callable[[Anchor], Participants],
) -> None:
    """Refuse unless every firm recipient is a participant, lane-authored, or an
    admin on SMD-originated work. Looks nothing up when it need not."""
    firm = sorted({normalize_address(r) for r in recipients if facts.classify(r) == "firm"})
    remaining = [a for a in firm if a not in facts.lanes.get(lane or "", frozenset())]
    if not remaining:
        return
    if anchor is None:
        raise FenceRefused(
            FENCE_PARTICIPANTS,
            f"{len(remaining)} firm recipient(s) are not authored for this send and it answers no request: "
            + ", ".join(remaining)
            + ".",
            remaining,
        )
    refused = [a for a in remaining if a not in allowed_by_anchor(facts, resolve(anchor))]
    if refused:
        raise FenceRefused(
            FENCE_PARTICIPANTS,
            f"{len(refused)} firm recipient(s) were not on the request this send answers: " + ", ".join(refused) + ".",
            refused,
        )


def check_reply(
    facts: SeatFacts,
    target: str,
    *,
    anchor: Anchor | None,
    resolve: Callable[[Anchor], Participants],
    target_kind: str,
) -> None:
    """A reply answers the anchor's own email or one in its conversation, and
    reaches only someone the anchor allows. ``target_kind`` is the channel's
    message kind (``graph_message`` / ``agentmail_message``). A reply to the
    anchor itself needs no lookup: Graph and AgentMail derive its recipient from
    that email, so it reaches the anchor's own sender."""
    if anchor is None:
        raise FenceRefused(FENCE_PARTICIPANTS, "a reply must name the email it answers, and this one names none.")
    if anchor.kind == target_kind and anchor.ident == target:
        return
    home = resolve(anchor)
    if home.message_id == target:
        return
    other = resolve(Anchor(target_kind, target))
    if not other.conversation_id or other.conversation_id != home.conversation_id:
        raise FenceRefused(
            FENCE_PARTICIPANTS, "the reply answers an email outside the conversation of the request it serves."
        )
    if facts.classify(other.sender) == "firm" and other.sender not in allowed_by_anchor(facts, home):
        raise FenceRefused(
            FENCE_PARTICIPANTS,
            f"the reply would reach {other.sender}, who was not on the request it serves.",
            [other.sender],
        )


__all__ = [
    "ANCHOR_KINDS",
    "DECISION",
    "FENCES",
    "FENCE_PARTICIPANTS",
    "FENCE_UNVERIFIABLE",
    "JOB_KINDS",
    "LANE_ESCALATION",
    "LANE_RULE_DISPATCH",
    "LANE_SEND_AS",
    "LANE_SKILL_PREFIX",
    "MESSAGE_KINDS",
    "REFUSAL_MARKER",
    "RULE_KIND",
    "SMD_DOMAINS",
    "Anchor",
    "FenceRefused",
    "Participants",
    "SeatFacts",
    "allowed_by_anchor",
    "check_reply",
    "check_send",
    "parse_anchor",
    "parse_lane",
    "seat_facts",
]
