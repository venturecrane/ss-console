"""The system prompts, as package data, with the firm's heading menu filled
from its config so the prompt and the falsifier agree on what a heading is,
and the firm's required wording filled from ``medchron.wording`` so the
writer prompts that mandate a phrase and the checker prompts that exempt it
read one constant (2026-10-23: the review held on wording the map prompt
ordered)."""

from __future__ import annotations

from importlib import resources

from .. import wording
from ..config import FirmConfig


def raw(name: str) -> str:
    """The prompt file as authored, tokens unfilled (the wording contract test reads it)."""
    return resources.files(__name__).joinpath(f"{name}.md").read_text(encoding="utf-8")


def load(name: str, cfg: FirmConfig) -> str:
    menu = " / ".join(str(h) for h in (cfg.get("format", "subsections") or []))
    return wording.fill(raw(name).replace("{{HEADING_MENU}}", menu))
