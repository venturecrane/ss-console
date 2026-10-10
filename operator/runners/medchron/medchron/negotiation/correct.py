"""`medchron negotiate-correct-summary`: bring one tab's Operator-written
summary in line with its rows, by the same rule a run applies after a write
(summary.py), through the same narrow connector write (re-read before, read
back after; a firm-written summary is refused). No email. Without ``--write``
it only shows what it would write.

For a summary a run left stale before the rule existed (the 2026-10-10 first
live run: Peschke and Nersesyan)."""

from __future__ import annotations

from typing import Any

from smokeball_connector.layout_sections import operator_details

from . import summary as summary_mod


def bind_layout(client: Any, design: str) -> Any:
    """The connector's layout tools bound to this client and the firm's design."""
    from smokeball_connector import layout_tools
    from smokeball_connector.layout_config import LayoutConfig

    layout_tools._client = lambda: client
    layout_tools.load_layout_config = lambda path=None: LayoutConfig(negotiation_design=design)
    return layout_tools


def _tab(layout: Any, matter_id: str, plaintiff_index: int) -> dict[str, Any]:
    got = layout.get_matter_layouts(matter_id, section="Negotiation Details")
    if not isinstance(got, dict) or got.get("status") != "ok":
        raise RuntimeError("the matter's Negotiation Details could not be read")
    for item in got.get("items") or []:
        if int(item.get("parent_index") or 0) == plaintiff_index and item.get("negotiation") is not None:
            return item["negotiation"]
    raise RuntimeError(f"no Negotiation Details tab for plaintiff {plaintiff_index}")


def correct(layout: Any, matter_id: str, plaintiff_index: int, accepted: dict[int, str], write: bool) -> dict[str, Any]:
    """``{"before", "after", "status"}``; ``after`` is what the tab reads back
    when written, else the summary that would be written."""
    view = _tab(layout, matter_id, plaintiff_index)
    before = view.get("details")
    new = summary_mod.refreshed(before, view.get("rows") or [], accepted)
    if new is None:
        why = "current" if operator_details(before) else "not the Operator's summary"
        return {"status": f"unchanged ({why})", "before": before, "after": before}
    if not write:
        return {"status": "would_write", "before": before, "after": new}
    got = layout.refresh_operator_details(matter_id, new, before, plaintiff_index=plaintiff_index)
    status = got.get("status") if isinstance(got, dict) else "refused"
    reread = _tab(layout, matter_id, plaintiff_index).get("details")
    return {"status": status, "before": before, "after": reread, "result": got, "read_back_matches": reread == new}


__all__ = ["bind_layout", "correct"]
