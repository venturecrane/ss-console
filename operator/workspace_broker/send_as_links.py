"""Approve buttons for a staff send-as draft (ss ADR 0089 amendment 5a).

A button in the approval email is a link, and a link is a credential, so what
lives here is the whole of what makes one safe to press: a signed token naming
ONE row, ONE approver, ONE verb and ONE expiry, and a signing key this process
mints at 0600 and no other uid can read. The seat's web gate carries a click to
the broker; it cannot mint, alter or replay one.

The other half of the control is not code: the broker sends an email carrying
buttons with no Sent Items copy, so the only mailbox holding the link is the
approver's, and boot smoke fails a send-as seat whose read app can open that
mailbox (operator/templates/msgraph-read-app-cannot-read-staff-probe.py).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import os
import json
import time
from typing import Any

#: The window a draft, and so its links, stay answerable. Mirrors
#: ``send_as_acts.SEND_AS_TTL_SECONDS``; kept here so this module needs nothing
#: from the one that imports it.
SEND_AS_TTL_SECONDS = 86_400


def tag_for(act_id: str) -> str:
    """The draft tag, the one string the approver, the email and the row share."""
    return f"[draft {act_id}]"

#: Where the link-token key lives: broker-owned, 0600, minted on first use. The
#: AGENT uid (and so the webhook gate, and so the model) can neither read nor
#: forge it, which is what makes an approve link a key the seat can check rather
#: than a string anybody can mint.
LINK_KEY_PATH = "/var/lib/smd-workspace-broker/send_as_link.key"


def approve_base_url() -> str:
    """Where an approve button points, or ``""`` when this seat has no web face.

    Authored first (``SMD_APPROVE_BASE_URL``), else the Machine's own hostname,
    which is where the seat's gate already answers. No base, no buttons: the
    email still carries the reply forms, which need nothing.
    """
    authored = (os.environ.get("SMD_APPROVE_BASE_URL") or "").strip().rstrip("/")
    if authored:
        return authored
    slug = (os.environ.get("SMD_CUSTOMER_SLUG") or os.environ.get("CUSTOMER_SLUG") or "").strip()
    return f"https://hermes-{slug}.fly.dev" if slug else ""



def _link_key() -> bytes:
    """The seat's link-signing key, minted once and kept broker-only."""
    try:
        with open(LINK_KEY_PATH, "rb") as fh:
            key = fh.read()
        if len(key) >= 32:
            return key
    except OSError:
        pass
    key = os.urandom(32)
    directory = os.path.dirname(LINK_KEY_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    fd = os.open(LINK_KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    return key


def link_token(row_id: str, approver: str, decision: str, expires_at: float) -> str:
    """A one-decision approve link: this row, this approver, this verb, this
    expiry, signed. It authorizes nothing by itself. ``decide_link`` re-reads the
    row and applies every check the emailed lane applies except the one that is
    about email (the Sent Items forgery probe, which a click cannot answer).
    """
    body = f"{row_id}.{approver}.{decision}.{int(expires_at)}"
    mac = hmac.new(_link_key(), body.encode("utf-8"), hashlib.sha256).digest()[:24]
    sig = base64.urlsafe_b64encode(mac).decode().rstrip("=")
    return f"{row_id}.{decision}.{int(expires_at)}.{sig}"


def verify_link_token(token: str, approver: str) -> tuple[str, str] | None:
    """``(row_id, decision)`` for a token that is intact, unexpired and this
    approver's, else ``None``."""
    parts = str(token or "").split(".")
    if len(parts) != 4:
        return None
    row_id, decision, expires = parts[0], parts[1], parts[2]
    try:
        expires_at = int(expires)
    except ValueError:
        return None
    if decision not in ("send", "cancel") or time.time() > expires_at:
        return None
    if not hmac.compare_digest(token, link_token(row_id, approver, decision, expires_at)):
        return None
    return row_id, decision



def approval_buttons(row_id: str, approver: str, expires_at: float) -> dict[str, str]:
    """``{"send": url, "cancel": url}``, or ``{}`` when this seat has no web face.

    Change is deliberately not a button: a revision needs the person's words, and
    a button cannot carry them.
    """
    base = approve_base_url()
    if not base:
        return {}
    return {
        decision: f"{base}/approve?t={link_token(row_id, approver, decision, expires_at)}"
        for decision in ("send", "cancel")
    }


def approval_html(body_text: str, buttons: dict[str, str]) -> str:
    """The same words, with Send and Cancel as buttons above them."""
    send_url = html.escape(buttons["send"], quote=True)
    cancel_url = html.escape(buttons["cancel"], quote=True)
    style = (
        "display:inline-block;padding:10px 18px;margin:0 8px 0 0;border-radius:6px;"
        "font-family:system-ui,sans-serif;font-size:15px;text-decoration:none;"
    )
    return (
        "<div>"
        f'<p><a href="{send_url}" style="{style}background:#1a7f37;color:#ffffff">Send it</a>'
        f'<a href="{cancel_url}" style="{style}background:#eeeeee;color:#111111">Cancel</a></p>'
        "<p style=\"font-family:system-ui,sans-serif;font-size:13px;color:#555\">"
        "To change it, reply to this email with what to change.</p>"
        + render_html(body_text)
        + "</div>"
    )


def render_html(body_text: str) -> str:
    """The HTML part, derived from the text by the broker so the digest covers it.

    Escaped, newlines kept. Link targets stay as written text (no anchors are
    minted), so what the approver read is what the recipient can see.
    """
    return "<div>" + html.escape(body_text).replace("\n", "<br>\n") + "</div>"


def approval_email(
    row_id: str, msg: dict[str, Any], policy: Any, tainted: bool, sources: list[str], name: str
) -> dict[str, Any]:
    tag = tag_for(row_id)
    lines = [
        f"{name}, the Operator has drafted this email to send from your address. Nothing has been sent.",
        "",
    ]
    for field in ("to", "cc"):
        for address in msg[field]:
            mark = "" if policy.allows_recipient(address) else "  (not on your firm's roster)"
            lines.append(f"{field.upper()}: {address}{mark}")
    lines += [f"SUBJECT: {msg['subject']}", "Replies will come to you and to the Operator.", ""]
    if tainted:
        shown = ", ".join(sources) if sources else "an outside message or document"
        lines += [f"Prepared after reading outside material: {shown}.", ""]
    lines += ["----- draft -----", msg["body_text"], "----- end of draft -----", ""]
    # Plain words first, because that is what a person types. The tagged forms
    # stay listed as the exact way to answer when more than one draft is waiting
    # (the seat reads the tag from this email's subject and the quoted copy under
    # a reply, so an ordinary "send" resolves itself).
    buttons = approval_buttons(row_id, msg["from"], time.time() + SEND_AS_TTL_SECONDS)
    lines += [
        "Reply to this email with one of these:",
        "send",
        "change: what to change",
        "cancel",
        "",
        "If more than one draft is waiting, answer with the draft named:",
        f"{tag} send",
        f"{tag} change: what to change",
        f"{tag} cancel",
        "",
        f"This draft expires in {SEND_AS_TTL_SECONDS // 3600} hours if you do not answer.",
    ]
    body_text = "\n".join(lines)
    email: dict[str, Any] = {
        "to": [msg["from"]],
        "subject": f"Approve: {msg['subject']} {tag}",
        "body_text": body_text,
    }
    if buttons:
        email["html"] = approval_html(body_text, buttons)
        # No copy in this mailbox's Sent Items: the buttons are keys, and a copy
        # here would put them where the agent can read its own approval mail and
        # click on its own behalf. The staff member's inbox is the only place the
        # link exists, and the boot probe asserts the agent cannot read that.
        email["no_sent_copy"] = True
    return email


def notify_link_decision(
    broker: Any, row: dict[str, Any], outcome: dict[str, Any], notice: Any
) -> None:
    """Tell the approver what a click just did, so a click they did not make is
    visible to them rather than only to an audit reader."""
    status = str(outcome.get("status") or "").upper()
    if status not in ("DISPATCHED", "CANCELLED", "FAILED"):
        return
    msg = json.loads(row["payload_json"])
    said = {
        "DISPATCHED": "was sent from your address",
        "CANCELLED": "was cancelled",
        "FAILED": "could not be sent, and nothing went out",
    }[status]
    try:
        notice(
            broker,
            row["approver"],
            f"{msg['subject']} {tag_for(row['id'])}",
            f"Using the button in the approval email, {tag_for(row['id'])} {said}. "
            "If that was not you, tell your Operator administrator now.",
        )
    except Exception:  # noqa: BLE001 - the decision stands; the notice is best effort
        pass
