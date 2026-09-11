"""The system prompts, as package data, with the firm's heading menu filled
from its config so the prompt and the falsifier agree on what a heading is."""

from __future__ import annotations

from importlib import resources

from ..config import FirmConfig


def load(name: str, cfg: FirmConfig) -> str:
    text = resources.files(__name__).joinpath(f"{name}.md").read_text(encoding="utf-8")
    menu = " / ".join(str(h) for h in (cfg.get("format", "subsections") or []))
    return text.replace("{{HEADING_MENU}}", menu)
