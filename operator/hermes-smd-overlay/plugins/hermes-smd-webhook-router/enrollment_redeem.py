"""An invite-code redeem's after-effects: the welcome, the training start, SMD's notice.

Split out of the router's ``__init__`` (which is at its module-size ceiling)
when the client champion's code was added. :func:`redeem_enrollment` asks the
broker to redeem and, on an enrollment, sends the fixed welcome and tells SMD;
:func:`start_training` creates a representative's progress record on a seat
that authors training. A champion (``shared.sender_enrollment.CHAMPION_LABEL``,
the label the broker answers the redeem with) gets the champion welcome and no
training start: no progress record and no library-change mark, so nothing a
representative is sent reaches them. Never raises.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from shared import agentmail_broker, send_anchor, sender_auth, sender_enrollment
from shared.customer_config import OPS_REPLY_DOMAINS

logger = logging.getLogger("hermes_smd_webhook_router")


def start_training(cfg: Any, address: str) -> bool:
    """On a seat that authors representative training (ss-console #3946),
    create the person's progress record and library-change mark by code at
    enrollment, and say so, so the welcome points them at the learning path.
    Every other seat: False, nothing written, the welcome unchanged. Never raises."""
    try:
        from shared import library_notice, training

        if not training.authored(cfg):
            return False
        if not training.start_progress(address, config=cfg):
            logger.warning("hermes-smd-webhook-router: progress record not created at enrollment")
        # The newest entry, so no change made before they joined is emailed to them.
        return library_notice.mark_newest(cfg, address) or True
    except Exception as exc:  # noqa: BLE001 - the enrollment stands; the first lesson creates it
        logger.warning("hermes-smd-webhook-router: training start failed (%s)", type(exc).__name__)
        return False


def _redeem(dto: Any, code: str) -> dict | None:
    """The broker's answer when it enrolled the sender, else None."""
    try:
        authenticated, reason = sender_auth.sender_authenticated(dto)
    except Exception:  # noqa: BLE001 - could not tell is not authenticated
        authenticated, reason = False, sender_auth.READ_FAILED
    try:
        result = sender_enrollment.redeem(
            message_id=dto.message_id,
            sender=dto.from_addr,
            code=code,
            authenticated=authenticated,
            auth_reason=reason,
        )
    except Exception as exc:  # noqa: BLE001 - nothing enrolled; the message is dropped
        logger.warning(
            "hermes-smd-webhook-router: invite-code redeem failed (%s); nothing enrolled",
            type(exc).__name__,
        )
        return None
    outcome = result.get("outcome") if isinstance(result, dict) else None
    logger.info(
        "hermes-smd-webhook-router: invite-code attempt from %s: %s", dto.from_addr, outcome
    )
    return result if isinstance(result, dict) and result.get("enrolled") is True else None


def _notify_smd(cfg: Any, dto: Any, result: dict, name: Any, label: Any, demo: bool) -> None:
    smd = [a for a in cfg.admins if a.rsplit("@", 1)[-1] in OPS_REPLY_DOMAINS]
    if not smd:
        return
    try:
        agentmail_broker.send_message(
            {
                "to": smd,
                "subject": sender_enrollment.notice_subject(demo=demo),
                "text": sender_enrollment.notice_text(
                    address=dto.from_addr,
                    slug=cfg.slug,
                    code_id=str(result.get("code_id") or ""),
                    name=name,
                    demo=demo,
                    label=label,
                ),
            }
        )
    except Exception as exc:  # noqa: BLE001 - the audit row and heartbeat still say it
        logger.warning(
            "hermes-smd-webhook-router: enrollment notice to SMD not sent (%s)", type(exc).__name__
        )


def redeem_enrollment(
    cfg: Any, dto: Any, code: str, *, start: Callable[[Any, str], bool] = start_training
) -> None:
    """Ask the broker to redeem; on an enrollment send the fixed welcome and
    tell SMD. Logs name the outcome only, never the code. Never raises."""
    result = _redeem(dto, code)
    if result is None:
        return
    name = result.get("name") if isinstance(result.get("name"), str) else None
    label = result.get("label") if isinstance(result.get("label"), str) else None
    demo = sender_enrollment.is_demo(cfg.raw)
    champion = not demo and label == sender_enrollment.CHAMPION_LABEL
    trains = False if demo or champion else start(cfg, dto.from_addr)
    try:
        agentmail_broker.send_reply(
            dto.message_id,
            text=sender_enrollment.welcome_text(
                name, demo=demo, training=trains, champion=champion
            ),
            anchor=send_anchor.message_anchor(dto.message_id, "agentmail"),
        )
    except Exception as exc:  # noqa: BLE001 - the enrollment stands; SMD is told below
        logger.warning("hermes-smd-webhook-router: welcome not sent (%s)", type(exc).__name__)
    _notify_smd(cfg, dto, result, name, label, demo)


__all__ = ["redeem_enrollment", "start_training"]
