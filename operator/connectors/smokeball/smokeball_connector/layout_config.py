"""Which layout design is the firm's Negotiation Details tab, read off the
seat's own customer.yaml at call time (the ``post_intake_config`` shape).

    smokeball_layouts:
      settlement_negotiations_design: f6719448-d924-4c59-9c1e-3bd2b76550ca

WHY IT IS AUTHORED. Smokeball's layout list carries no layout names, only a
design id (``<base guid>_<matter type guid>``), and the design endpoint is
refused at the gateway. It also omits every empty field, so a matter whose
negotiation tab has no rows yet shows an item with no values at all: nothing on
it says what it is. A matter that already has rows identifies its own tab by
its keys; a firm-authored base guid is what finds the empty one, which is the
one a fill most often targets.

Unauthored (no file, no block, no key) is not an error: the tools fall back to
the keys on the matter. A malformed value is reported, never raised."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from .library import CUSTOMER_YAML_ENV, DEFAULT_CUSTOMER_YAML

CONFIG_BLOCK = "smokeball_layouts"
NEGOTIATION_KEY = "settlement_negotiations_design"
_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


@dataclass(frozen=True)
class LayoutConfig:
    negotiation_design: str | None = None
    error: str | None = None


def parse_layout_config(block: Any) -> LayoutConfig:
    if block is None:
        return LayoutConfig()
    if not isinstance(block, dict):
        return LayoutConfig(error=f"{CONFIG_BLOCK} must be a mapping")
    value = block.get(NEGOTIATION_KEY)
    if value is None:
        return LayoutConfig()
    if not isinstance(value, str) or not _GUID.match(value.strip()):
        return LayoutConfig(error=f"{CONFIG_BLOCK}.{NEGOTIATION_KEY} must be a layout design guid")
    return LayoutConfig(negotiation_design=value.strip().lower())


def load_layout_config(path: str | None = None) -> LayoutConfig:
    path = path or os.environ.get(CUSTOMER_YAML_ENV) or DEFAULT_CUSTOMER_YAML
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        return LayoutConfig()
    try:
        import yaml

        data = yaml.safe_load(raw) or {}
    except Exception as exc:  # noqa: BLE001 - an unparseable config is reported, never raised
        return LayoutConfig(error=f"customer.yaml not parseable: {exc.__class__.__name__}")
    return parse_layout_config(data.get(CONFIG_BLOCK) if isinstance(data, dict) else None)


__all__ = ["CONFIG_BLOCK", "NEGOTIATION_KEY", "LayoutConfig", "load_layout_config", "parse_layout_config"]
