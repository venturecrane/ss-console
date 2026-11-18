"""How the update review and the finisher show a PAGE, and what a request's
pages cost. $0, no model.

A page goes as its own text when ``medchron/page_mode.py`` says the text can
be trusted, else as its picture, under the same label either way
(``Exhibit E p. P (...)``), so a finding names a page the same way whichever
was shown. A text page's block opens with ``PAGE_TEXT`` so a request's page
reads can be counted from the request itself (``page_reads``); the ledger's
``pages`` keeps counting pictures only, as for every other lane.
"""

from __future__ import annotations

from typing import Any, Callable

from .. import page_mode as PM

#: Opens the block that carries a page's text in place of its picture.
PAGE_TEXT = "PAGE TEXT (this page's own text layer, shown instead of its image):"
#: The input tokens one page shown as a PICTURE costs, its label included
#: (the stage's projection figure, ``stages/update_review_cost.py``).
PAGE_TOKENS = 1_800
#: Characters per input token, for a page shown as TEXT.
CHARS_PER_TOKEN = 4
#: A page's label block, priced with a text page.
LABEL_TOKENS = 20

Images = Callable[[int, int], dict[str, Any] | None]
Modes = Callable[[int, int], PM.Mode]


def page_blocks(ex: int, pg: int, label: str, images: Images, modes: Modes | None) -> list[dict[str, Any]]:
    """The blocks that show page `pg` of exhibit `ex`: its label, then its
    text or its picture; none when it has neither (a page past the end)."""
    head = {"type": "text", "text": f"Exhibit {ex} p. {pg}{label}:"}
    m = modes(ex, pg) if modes is not None else None
    if m is not None and m.kind == PM.TEXT and m.text:
        return [head, {"type": "text", "text": f"{PAGE_TEXT}\n{m.text}"}]
    img = images(ex, pg)
    return [head, img] if img is not None else []


def page_reads(content: list[dict[str, Any]]) -> dict[str, int]:
    """The pages one request shows: as text, as pictures, and the characters
    of page text it carries."""
    text = [b["text"] for b in content if b.get("type") == "text" and str(b.get("text", "")).startswith(PAGE_TEXT)]
    return {
        "text_pages": len(text),
        "image_pages": sum(1 for b in content if b.get("type") == "image"),
        "page_chars": sum(len(t) - len(PAGE_TEXT) - 1 for t in text),
    }


def mode_tokens(m: PM.Mode | None) -> int:
    """The input tokens one page costs as the rule shows it."""
    if m is not None and m.kind == PM.TEXT and m.text:
        return LABEL_TOKENS + len(m.text) // CHARS_PER_TOKEN
    return PAGE_TOKENS


def named_pages(found: list[Any], cap: int) -> list[tuple[int, int]]:
    """The pages a finisher request shows: every page a finding names, then
    the page either side of each in the same exhibit, at most `cap`."""
    named = list(dict.fromkeys((int(f.exhibit), int(f.page)) for f in found if f.exhibit and f.page))
    side = [(x, p + d) for x, p in named for d in (-1, 1) if p + d >= 1]
    return list(dict.fromkeys(named + side))[:cap]
