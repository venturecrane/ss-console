"""Which path a brief is drafted on: ``drafting-firm.yaml`` ``evidence:``.

    evidence:
      mode:        digest | ledger   default digest
      classes:     [class]           default [mediation_brief]; ledger only
      page_window: int 1..50         default 10 (pages per extract read)
      image_pages: int 0..5000       default 400 (page images read, at most)
      prompts:     {extract?, compose?}   paths pinned in inputs; the MARKUP
                                     block is appended by code either way

The section is OPTIONAL and every key in it is optional: absent, every class
drafts on the digest path it always has. ``ledger`` (the evidence-first path,
``evidence/``, ``evidence_lane.py``) is accepted only for the classes in
``LEDGER_CLASSES``: a served paper (``compose.STRICT_CLASSES``) and every class
no proof run has measured stays on the digest path, and naming one is a schema
error, never a silent fallback.

``image_pages`` bounds the page images read behind signed or verified claims;
a page past it is NOT read and its fact is raised to the attorney as
unconfirmed (``images.confirm`` with no image), never stated as confirmed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

KEY = "evidence"
MODES = ("digest", "ledger")
#: The classes the ledger path may draft. The mediation brief is the one the
#: method was measured on (2026-11-04); a settlement brief is drafted as one.
LEDGER_CLASSES = ("mediation_brief",)
DEFAULT_WINDOW = 10
DEFAULT_IMAGE_PAGES = 400
KEYS = {"mode": "str", "classes": "list[str]", "page_window": "int", "image_pages": "int", "prompts": "map"}
RANGES = {"page_window": (1, 50), "image_pages": (0, 5000)}
PROMPT_KEYS = ("extract", "compose")


@dataclass(frozen=True)
class Settings:
    """``ledger``: this job drafts on the evidence path."""

    ledger: bool
    page_window: int = DEFAULT_WINDOW
    image_pages: int = DEFAULT_IMAGE_PAGES
    extract_prompt: str | None = None
    compose_prompt: str | None = None


def _int_ok(v: Any, lo: int, hi: int) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _classes(body: dict[str, Any], classes: tuple[str, ...]) -> list[str]:
    v = body.get("classes")
    if v is None:
        return []
    if not (isinstance(v, list) and v and all(isinstance(x, str) for x in v)):
        return [f"{KEY}.classes: expected a non-empty list of class names"]
    out = [f"{KEY}.classes: unknown class {x!r} (expected {list(classes)})" for x in v if x not in classes]
    if body.get("mode") == "ledger":
        out += [
            f"{KEY}.classes: {x!r} cannot draft on the ledger path (only {list(LEDGER_CLASSES)})"
            for x in v
            if x in classes and x not in LEDGER_CLASSES
        ]
    return out


def _prompts(body: dict[str, Any]) -> list[str]:
    p = body.get("prompts")
    if p is None:
        return []
    if not isinstance(p, dict):
        return [f"{KEY}.prompts: expected a map"]
    out = [f"{KEY}.prompts.{k}: unknown key (expected {list(PROMPT_KEYS)})" for k in p if k not in PROMPT_KEYS]
    out += [f"{KEY}.prompts.{k}: expected a path" for k, v in p.items() if not (isinstance(v, str) and v.strip())]
    return out


def validate(body: Any, classes: tuple[str, ...]) -> list[str]:
    """Every problem in the ``evidence`` section as ``evidence.key: message``."""
    if not isinstance(body, dict):
        return [f"{KEY}: expected a map"]
    out = [f"{KEY}.{k}: unknown key (closed key set)" for k in body if k not in KEYS]
    if "mode" in body and body["mode"] not in MODES:
        out.append(f"{KEY}.mode: expected one of {list(MODES)}")
    for k, (lo, hi) in RANGES.items():
        if k in body and not _int_ok(body[k], lo, hi):
            out.append(f"{KEY}.{k}: expected an integer {lo}..{hi}")
    return out + _classes(body, classes) + _prompts(body)


def referenced_paths(data: dict[str, Any]) -> list[tuple[str, str]]:
    """(where, path) for the prompt files the section names (pinned in inputs)."""
    p = (data.get(KEY) or {}).get("prompts") or {}
    return [(f"{KEY}.prompts.{k}", v) for k, v in p.items()]


def settings(firm_data: dict[str, Any], cls: str, read: Any = None) -> Settings:
    """The path this job's class drafts on. ``read(rel)`` reads a pinned
    input (``DraftingFirm.read``); without it the prompt overrides stay None."""
    body = firm_data.get(KEY) or {}
    classes = tuple(body.get("classes") or LEDGER_CLASSES)
    ledger = body.get("mode") == "ledger" and cls in classes and cls in LEDGER_CLASSES
    p = body.get("prompts") or {}
    return Settings(
        ledger=ledger,
        page_window=int(body.get("page_window", DEFAULT_WINDOW)),
        image_pages=int(body.get("image_pages", DEFAULT_IMAGE_PAGES)),
        extract_prompt=read(p["extract"]) if read and p.get("extract") else None,
        compose_prompt=read(p["compose"]) if read and p.get("compose") else None,
    )
