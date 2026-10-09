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

from .event_delete import register as _register_event_delete_tools
from .form_letters import register as _register_form_letter_tools
from .letter_tools import register as _register_letter_tools
from .medicals_provider import register as _register_medicals_provider_tools
from .medicals_tools import register as _register_medicals_tools
from .memo_tools import register as _register_memo_tools
from .vendor_invoice_tools import register as _register_vendor_invoice_tools
from .workbook_tools import register as _register_workbook_tools
from .records_order_tools import register as _register_records_order_tools
from .sr1_form import register as _register_sr1_tools
from .sr19_form import register as _register_sr19_tools
from .funding_case_eval import register as _register_funding_case_eval_tools
from .layout_tools import register as _register_layout_tools


def register(server: Any) -> None:
    """Register every attachment-rooted tool surface, in the original order.

    ``add_workbook`` is not attachment-rooted. It registers here because this
    is the one registrar the size-ratcheted ``server.py`` already calls, and it
    goes LAST so the earlier tools keep their positions. The calendar-event
    deletion pair is not attachment-rooted either and registers after it for the same reason,
    and ``update_memo`` (the in-place file-note write, memo_tools.py) after that,
    and ``add_medicals_row`` (the Medicals tab write, medicals_tools.py) after
    that. Then ``add_medicals_provider`` (a facility with no bill,
    medicals_provider.py) and ``render_firm_form_letter`` (the firm's own
    rep-letter form, filled, form_letters.py), neither attachment-rooted, last
    for the same reason."""
    _register_vendor_invoice_tools(server)
    _register_letter_tools(server)
    _register_workbook_tools(server)
    _register_event_delete_tools(server)
    _register_memo_tools(server)
    _register_medicals_tools(server)
    _register_medicals_provider_tools(server)
    _register_form_letter_tools(server)
    # The records-order tools (records_order_tools.py), after everything else.
    _register_records_order_tools(server)
    # The DMV SR1 prefill (sr1_form.py), after everything else.
    _register_sr1_tools(server)
    # The DMV SR 19C prefill (sr19_form.py), after the SR1.
    _register_sr19_tools(server)
    # The funding case evaluation prefill (funding_case_eval.py), after the SR19.
    _register_funding_case_eval_tools(server)
    # The layout read and the Negotiation Details write (layout_tools.py), last.
    _register_layout_tools(server)


__all__ = ["register"]
