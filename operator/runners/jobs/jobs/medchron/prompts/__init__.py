"""The system prompts, as package data, with the firm's heading menu filled
from its config so the prompt and the falsifier agree on what a heading is,
and the firm's required wording filled from ``jobs.wording`` so the
writer prompts that mandate a phrase and the checker prompts that exempt it
read one constant (2026-10-23: the review held on wording the map prompt
ordered). ``{{DEPTH_RULE}}`` is filled the same way, from ``depth-rule.md``:
the writer and the reviewers carry one text for how much of a record an entry
states (2026-11-18: the writer was told to keep routine visits brief and the
reviewer to report anything left out, and omitted content was 56 and 80
percent of the must-fix findings on two real updates)."""

from __future__ import annotations

from importlib import resources

from ... import wording
from ..config import FirmConfig


def raw(name: str) -> str:
    """The prompt file as authored, tokens unfilled (the wording contract test reads it)."""
    return resources.files(__name__).joinpath(f"{name}.md").read_text(encoding="utf-8")


DEPTH_TOKEN = "{{DEPTH_RULE}}"
# The prompts that carry the rule: the writer, and the reviewer that can report
# content as missing. ``tests/test_depth_rule.py`` holds them to it.
DEPTH_PROMPTS = ("map-system", "update-review-system", "entry-read-system")


def depth_rule() -> str:
    return raw("depth-rule").strip()


def load(name: str, cfg: FirmConfig) -> str:
    menu = " / ".join(str(h) for h in (cfg.get("format", "subsections") or []))
    text = raw(name).replace(DEPTH_TOKEN, depth_rule())
    return wording.fill(text.replace("{{HEADING_MENU}}", menu))
