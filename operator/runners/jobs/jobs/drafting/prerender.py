"""What code writes into the finished markdown before it is rendered.

The firm's citation policy (``citations.py``) and the opening demand computed
from the damages chart (``opening_demand.py``), in that order, for every
drafting path (``finish._render_unit``).
"""

from __future__ import annotations

from typing import Any, Mapping

from . import citations, opening_demand


def prepare(
    md: str, firm_data: Mapping[str, Any], cls: str, texts: Mapping[str, str]
) -> tuple[str, list[str], citations.Policy | None]:
    """(markdown, notes for the attorney, the citation policy ``format_check`` reads back)."""
    md, cited, policy = citations.for_class(md, firm_data, cls, texts)
    md, demand = opening_demand.fill(md, firm_data, cls)
    return md, cited + demand, policy
