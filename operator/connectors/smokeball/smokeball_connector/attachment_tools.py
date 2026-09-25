"""One registrar for every tool surface that starts at an emailed attachment.

WHY THIS EXISTS AS A FILE OF ITS OWN. ``server.py`` is size-ratcheted
(``tests/operator-module-size.test.ts``), and the ratchet only tightens: its
message when a baselined module grows is "split the module rather than raising
its baseline". So a second attachment surface cannot add a second import line
and a second register call down there. It adds them here instead, and
``server.py`` keeps exactly the two lines it already had — one import, one call
— now pointed at this module.

Registration ORDER is preserved: the vendor-invoice tools register first,
exactly as when ``server.py`` called them directly, so the tool list the
conformance suite reads does not move.
"""

from __future__ import annotations

from typing import Any

from .letter_tools import register as _register_letter_tools
from .vendor_invoice_tools import register as _register_vendor_invoice_tools
from .workbook_tools import register as _register_workbook_tools


def register(server: Any) -> None:
    """Register every attachment-rooted tool surface, in the original order.

    ``add_workbook`` is not attachment-rooted. It registers here because this
    is the one registrar the size-ratcheted ``server.py`` already calls, and it
    goes LAST so the earlier tools keep their positions."""
    _register_vendor_invoice_tools(server)
    _register_letter_tools(server)
    _register_workbook_tools(server)


__all__ = ["register"]
