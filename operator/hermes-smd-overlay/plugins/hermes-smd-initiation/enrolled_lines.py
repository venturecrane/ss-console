"""The platform-stated lines about an enrolled sender (ADR 0106), for ``pre_llm_call``.

Split out of the plugin's ``__init__`` (at its module-size ceiling) when the
client champion was added: the name SMD issued the person's code for, on a
seat that switches enrollment on, read from the broker-owned enrolled list and
re-validated as a plain name. A champion's role line is
``champion_fence.on_turn_start``'s.

Never raises; a fault states nothing rather than something wrong.
"""

from __future__ import annotations

from typing import Any


def enrolled_name_line(cfg: Any, sender: str) -> str:
    """The name SMD issued an enrolled person's invite code for, as one
    platform-stated line, or ``""``. Never a placeholder when there is no name."""
    try:
        from shared import sender_enrollment

        if not sender_enrollment.enabled_in(cfg.raw):
            return ""
        name = sender_enrollment.enrolled_name(sender)
    except Exception:  # noqa: BLE001 - no name is said rather than a wrong one
        return ""
    if not name:
        return ""
    return (
        f"\n- This sender connected with an invite code SMD issued for {name}; "
        "address them by that name."
    )


__all__ = ["enrolled_name_line"]
