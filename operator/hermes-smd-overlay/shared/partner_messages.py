"""A message one partner hands the agent for the other partner (ADR 0097).

The couples agent serves two people who each talk to it privately. One of them
says "tell Ben I think he's cute", and the agent passes it on. Before this
module the agent had only its outbound email tool, built for firm seats: on
2026-10-21 (hermes-captain-mark 23:16Z) it tried to email the partner, the
send was held at the confirm ceiling, and the agent told the sender the message
was "waiting for Scott (the account owner) to approve it", an approval that
exists nowhere. Nothing was delivered.

Three decisions, all in code and none in the prompt:

* **The recipient is the other person.** A couples seat has exactly two people
  (``people:``); the sender is the turn's structural speaker (their DM, their
  verified email, or their Telegram id in the group), and the recipient is
  whoever they are not. The model names nobody.
* **The sender's ask is the authority.** No approval, no confirm ceiling, no
  waiting on the recipient. Privacy by space (ADR 0091) fences what each
  partner can READ; what a partner chooses to say to the other is theirs to
  say, like any shared write (ADR 0091 point 4, Captain 2026-10-19).
* **What is delivered passes through one seam,** :func:`shape`. Today it is
  the sender's words, rendered plain, under one attribution line the model
  cannot drop. The design the seam is reserved for (an agent that finds the way
  to say a hard thing so it lands well) is ADR 0097's open half.

Delivery is by the recipient's authored channel: their Telegram DM when they
prefer Telegram and are bound (:mod:`shared.telegram_bot`), else email on the
broker's ``space`` lane, which admits exactly ``people[].email``. Every
delivered message is appended to one ledger on the volume, and the next private
turn of either partner is shown the recent ones (:func:`context_block`), since
a message the bot posted is in no Hermes session's history.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shared import report_render, send_anchor, send_dispatch, telegram_bot
from shared import space as _space

logger = logging.getLogger(__name__)

_PATH_ENV = "SMD_PARTNER_MESSAGES_PATH"
_DEFAULT_PATH = "/opt/data/smd-state/partner-messages.jsonl"

#: The longest message a partner can hand over, in characters of their own text.
MAX_TEXT = 2000
#: The one shaping mode built today: the sender's words, as written.
MODE_AS_WRITTEN = "as_written"
MODES = (MODE_AS_WRITTEN,)
CHANNEL_TELEGRAM = "telegram"
CHANNEL_EMAIL = "email"
#: What a partner's next private turn is shown.
RECENT_DAYS = 7
RECENT_MAX = 10
SKILL = "partner-messages"

_lock = threading.Lock()


class PartnerMessageError(ValueError):
    """The message could not be shaped, delivered or kept. The text is for the model."""


def ledger_path() -> Path:
    return Path(os.environ.get(_PATH_ENV) or _DEFAULT_PATH)


# ------------------------------------------------------------------ the seam


@dataclass(frozen=True)
class Shaped:
    """What the recipient reads, and how it was shaped from what the sender said."""

    mode: str
    text: str


def shape(text: str, *, sender: _space.Person, mode: str = MODE_AS_WRITTEN) -> Shaped:
    """The translation seam between what the sender said and what is delivered.

    ``as_written``: the sender's words, rendered to plain text (markdown
    markers off, dashes normalized), under an attribution line. The line is
    composed here so the model can neither drop it nor claim the words as its
    own. Other modes are the open half of ADR 0097 and are refused until built.
    """
    if mode not in MODES:
        raise PartnerMessageError(f"shaping mode {mode!r} is not built yet")
    clean = report_render.render_message_plain(str(text or "")).strip()
    if not clean:
        raise PartnerMessageError("there is nothing to pass along")
    if len(clean) > MAX_TEXT:
        raise PartnerMessageError(
            f"that is longer than a passed message can be ({MAX_TEXT} characters); "
            "ask them for a shorter version"
        )
    who = sender.name or "Your partner"
    return Shaped(mode=mode, text=f"{who} asked me to pass this along:\n\n{clean}")


# --------------------------------------------------------------- delivery


@dataclass(frozen=True)
class Delivery:
    channel: str
    ref: str


TelegramSend = Callable[[str, str], int]


def deliver(
    shaped: Shaped,
    *,
    sender: _space.Person,
    recipient: _space.Person,
    session_id: str,
    telegram_send: TelegramSend | None = None,
) -> Delivery:
    """Put the shaped text in front of the recipient on their authored channel.

    Telegram DM when they prefer Telegram and are bound; otherwise email to
    their ``people[].email`` on the ``space`` lane. Raises
    :class:`PartnerMessageError` when it did not reach them. ``telegram_send``
    is resolved at call time (tests swap :func:`telegram_bot.send_message`)."""
    if recipient.reached_by_telegram:
        post = telegram_send or telegram_bot.send_message
        try:
            msg_id = post(recipient.telegram_id, shaped.text)
        except telegram_bot.TelegramSendError as exc:
            raise PartnerMessageError(f"it did not reach them on Telegram ({exc})") from exc
        return Delivery(channel=CHANNEL_TELEGRAM, ref=str(msg_id))
    if not recipient.email:
        raise PartnerMessageError("there is no channel to reach them on")
    subject = f"A message from {sender.name or 'your partner'}"
    result = send_dispatch.dispatch(
        to=[recipient.email],
        subject=subject,
        text=shaped.text,
        anchor=None,
        lane=send_anchor.LANE_SPACE,
        session_id=session_id,
        templated=False,
        audit_extra={"skill_name": SKILL},
    )
    if not result.sent:
        raise PartnerMessageError(f"it did not reach them by email ({result.reason})")
    return Delivery(channel=CHANNEL_EMAIL, ref=str(result.message_id or ""))


# ----------------------------------------------------------------- ledger


@dataclass(frozen=True)
class Record:
    id: str
    at: str
    sender: str
    recipient: str
    given: str
    delivered: str
    mode: str
    channel: str
    ref: str


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def record(rec: Record, *, path: Path | None = None) -> None:
    """Append one delivered message to the ledger. Raises OSError when it cannot."""
    target = path or ledger_path()
    line = json.dumps(asdict(rec), ensure_ascii=False, sort_keys=True)
    with _lock:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def load(*, path: Path | None = None) -> list[Record]:
    """Every record in the ledger, oldest first. A malformed line is skipped and
    logged, never raised: one bad line must not hide the rest."""
    target = path or ledger_path()
    try:
        raw_lines = target.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    out: list[Record] = []
    for raw in raw_lines:
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
            out.append(Record(**{k: str(data.get(k, "")) for k in Record.__dataclass_fields__}))
        except (ValueError, TypeError):
            logger.warning("partner_messages: skipping a malformed ledger line")
    return out


def recent_for(
    person_id: str, *, now: datetime | None = None, path: Path | None = None
) -> list[Record]:
    """The messages this person sent or received in the last :data:`RECENT_DAYS`
    days, oldest first, at most :data:`RECENT_MAX`."""
    cutoff = _now(now) - timedelta(days=RECENT_DAYS)
    rows = []
    for rec in load(path=path):
        if person_id not in (rec.sender, rec.recipient):
            continue
        try:
            at = datetime.fromisoformat(rec.at)
        except ValueError:
            continue
        if at >= cutoff:
            rows.append(rec)
    return rows[-RECENT_MAX:]


def context_block(
    space: _space.Space | None,
    cfg: _space.SpaceConfig,
    *,
    now: datetime | None = None,
    path: Path | None = None,
) -> str:
    """What a PRIVATE turn is shown about messages passed between the partners.
    Empty for the shared space (a third reader of the group would see it) and
    for an unresolved turn."""
    if space is None or not space.private or not space.person_id:
        return ""
    rows = recent_for(space.person_id, now=now, path=path)
    if not rows:
        return ""
    names = {p.id: (p.name or p.id) for p in cfg.people}
    lines = [
        "Messages the two of them have passed through you in the last "
        f"{RECENT_DAYS} days, oldest first. The person you are talking with may "
        "refer to one; you delivered each of these yourself:"
    ]
    for rec in rows:
        when = rec.at[:16].replace("T", " ")
        lines.append(
            f"- {when} UTC, from {names.get(rec.sender, rec.sender)} to "
            f"{names.get(rec.recipient, rec.recipient)}, reached them by {rec.channel}: "
            f"{json.dumps(rec.given, ensure_ascii=False)}"
        )
    return "\n".join(lines)


# ------------------------------------------------------------- one call


def send(
    text: str,
    *,
    cfg: _space.SpaceConfig,
    sender: _space.Person,
    session_id: str,
    telegram_send: TelegramSend | None = None,
    now: datetime | None = None,
    path: Path | None = None,
) -> Record:
    """Shape, deliver and record one message from ``sender`` to the other
    person on the seat. Raises :class:`PartnerMessageError` with a sentence for
    the model when it could not; nothing is recorded then."""
    others = [p for p in cfg.people if p.id != sender.id]
    if len(cfg.people) != 2 or len(others) != 1:
        raise PartnerMessageError("passing a message works between the two of you only")
    recipient = others[0]
    shaped = shape(text, sender=sender)
    delivery = deliver(
        shaped,
        sender=sender,
        recipient=recipient,
        session_id=session_id,
        telegram_send=telegram_send,
    )
    rec = Record(
        id=uuid.uuid4().hex[:12],
        at=_now(now).isoformat(timespec="seconds"),
        sender=sender.id,
        recipient=recipient.id,
        given=report_render.render_message_plain(str(text or "")).strip(),
        delivered=shaped.text,
        mode=shaped.mode,
        channel=delivery.channel,
        ref=delivery.ref,
    )
    try:
        record(rec, path=path)
    except OSError:
        # It reached them; the ledger is what the NEXT turn reads. Loud, not fatal.
        logger.error(
            "partner_messages: delivered but the ledger could not be written", exc_info=True
        )
    return rec


def describe(rec: Record, cfg: _space.SpaceConfig) -> str:
    """One line for the model to restate: who it reached and how."""
    recipient = cfg.person(rec.recipient)
    name = (recipient.name or recipient.id) if recipient is not None else rec.recipient
    how = "on Telegram" if rec.channel == CHANNEL_TELEGRAM else "by email"
    return f"Delivered to {name} {how} just now."


__all__ = [
    "CHANNEL_EMAIL",
    "CHANNEL_TELEGRAM",
    "MAX_TEXT",
    "MODES",
    "MODE_AS_WRITTEN",
    "RECENT_DAYS",
    "RECENT_MAX",
    "SKILL",
    "Delivery",
    "PartnerMessageError",
    "Record",
    "Shaped",
    "context_block",
    "deliver",
    "describe",
    "ledger_path",
    "load",
    "recent_for",
    "record",
    "send",
    "shape",
]
