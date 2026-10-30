"""The vendor's layout response shapes, parsed: pure functions with no client,
no clock and no import from this package, so any module can read a layout
without an import cycle (``matter_resolution`` reads layouts, and
``medicals_layout`` imports ``matter_resolution``)."""

from __future__ import annotations

from typing import Any


def _items(resp: Any) -> list[Any]:
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        for key in ("value", "items", "data", "results"):
            if isinstance(resp.get(key), list):
                return resp[key]
    return []


def layout_values(item: Any) -> dict[str, Any]:
    """A layout item's values as ``{key: value}``. The vendor returns them as a
    list of ``{key, value}`` pairs, sometimes wrapped in a one-element
    ``value`` list; a dict is accepted too."""
    if (
        isinstance(item, dict)
        and isinstance(item.get("value"), list)
        and item["value"]
        and isinstance(item["value"][0], dict)
        and "values" in item["value"][0]
    ):
        item = item["value"][0]
    values = item.get("values") if isinstance(item, dict) else None
    if isinstance(values, dict):
        return dict(values)
    out: dict[str, Any] = {}
    for entry in values or []:
        if isinstance(entry, dict) and "key" in entry:
            out[entry["key"]] = entry.get("value")
    return out
