"""The audit's extra evidence for a claim cited to a HANDWRITTEN page.

The vision pass opens such a page with ``[handwritten]``. Its date is a
reading; the typed statements and bills of the same provider state the date
of service. A claim cited to one is verified with those statements beside the
page images (``update/handwritten.statement_block``), so the auditor can tell
a misread 7 from a real 1 instead of passing or failing the reading blind.

Best effort by design: anything this cannot read (no page map, no transcript)
adds nothing, and the claim is audited on its pages as before.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..update import body as B, handwritten as H
from .claims import body_of
from .page_text import PageIndex

_HEAD = re.compile(r"(?m)^\d{2}/\d{2}/\d{4}[^\n]*\n([^\n]*)")


class Evidence:
    def __init__(self, slug_dir: Path, unit: str, body: str) -> None:
        self.body = body
        self._pool: list[str] | None = None
        try:
            self.index: PageIndex | None = PageIndex(slug_dir, unit)
        except Exception:  # noqa: BLE001 - no page map or units file: no extra evidence, the claim is audited on its pages
            self.index = None

    def page(self, exhibit: int, page: int) -> str | None:
        if self.index is None:
            return None
        try:
            hit = self.index.resolve(exhibit, page)
            seg = self.index.segments(hit[0]).get(hit[1]) if hit else None
        except Exception:  # noqa: BLE001 - an unreadable transcript is no evidence, never a failed audit
            return None
        return seg[0] if seg else None

    def pool(self) -> list[str]:
        if self._pool is None:
            self._pool = []
            if self.index is not None:
                for fid in self.index.by_id:
                    self._pool.extend(t for t, _ in self.index.segments(fid).values())
        return self._pool

    def provider(self, claim: str) -> str:
        i = self.body.find(claim)
        heads = list(_HEAD.finditer(self.body[: max(i, 0)]))
        return B.provider_of("x\n" + heads[-1].group(1)) if heads else ""

    def block(self, c: dict[str, Any]) -> str:
        """The statements block for claim `c`, or '' when no cited page is
        handwritten or no typed statement of its provider is on file."""
        pages = c.get("pages") or []
        if not any(H.is_handwritten(self.page(int(c["exhibit"]), int(p))) for p in pages):
            return ""
        prov = self.provider(c["claim"])
        return H.statement_block(self.pool(), prov) if prov else ""


def statements_for(rnd: Any, c: dict[str, Any]) -> list[dict[str, str]]:
    """The content block to send beside claim `c`'s page images: the typed
    statements of its provider when it cites a handwritten page, else none.
    The round's Evidence is built once, from the document it audits."""
    with rnd._lock:
        ev = rnd.__dict__.get("hw_evidence")
        if ev is None:
            body = body_of(rnd.paths.doc.read_text(encoding="utf-8"))
            ev = rnd.__dict__["hw_evidence"] = Evidence(rnd.paths.slug_dir, rnd.paths.unit, body)
    text = ev.block(c)
    return [{"type": "text", "text": text}] if text else []
