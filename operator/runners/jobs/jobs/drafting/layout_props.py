"""What a firm's library file says for each layout property the order of
precedence ranks (design 2026-11-06, piece 5; ``layout_precedence.py``).

TWO WAYS OF READING, both on the file itself and both at no model cost:

* the page margins, read off the file's first section the way the pleading
  check reads a filed page (``pleading.check``), in inches;
* every layout key a rule can set and a filed brief can be read for
  (``revise/filed.value``), read off a fictional brief laid into the file by the
  class's own plan (``firm_rules.PROBE_MD``, ``base_lay.lay_out``): a marked file
  holds marked places, not a brief, so the value a draft made in it would carry
  is read off such a draft. Its words are fictional and never delivered; the
  probe lives and dies in a temp dir.

A value that cannot be read is ``None``, and nothing downstream treats ``None``
as a difference: an unread property never wins for a firm's edit, never
replaces a rule and never leaves the format check.

THE COURT'S RULES the order puts first, on pleading paper only (``COURT``):
California Rules of Court, rule 2.107 (a left margin of at least one inch, a
right margin of at least one half inch) and rule 2.108 (lines one and a half or
double spaced: the paper's line pitch, which ``pleading.check`` holds every line
to). No model call.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path
from typing import Any

MARGINS = "page_margins"
#: The layout keys ``revise/filed.value`` reads off a filed brief, in the order shown.
READ_KEYS = (
    "line_spacing_pt",
    "line_spacing",
    "heading_line_spacing_pt",
    "heading_line_spacing",
    "heading_space_before_pt",
    "heading_space_after_pt",
    "table_font_pt",
    "table_border_pt",
    "front_attorney_gap_lines",
    "front_court_gap_lines",
    "front_caption_gap_lines",
)
#: The keys the paper's line pitch decides on pleading paper (rule 2.108).
PITCH_KEYS = frozenset(READ_KEYS[:6])
#: Rule 2.107's floors on pleading paper: (left, right), in inches.
COURT_FLOORS_IN = (1.0, 0.5)
COURT = {
    MARGINS: "California Rules of Court, rule 2.107",
    "pitch": "California Rules of Court, rule 2.108",
}
#: The pleading check's own tolerance (``pleading.check``): 635 EMU, in inches.
MARGIN_TOLERANCE_IN = 635 / 914400
_EMU_PER_IN = 914400


def margins_of(blob: bytes) -> tuple[float, float, float, float] | None:
    """(top, right, bottom, left) of the file's first section, in inches."""
    import docx

    try:
        sec = docx.Document(io.BytesIO(blob)).sections[0]
        got = (sec.top_margin, sec.right_margin, sec.bottom_margin, sec.left_margin)
    except Exception:  # noqa: BLE001 - a file the reader cannot open has no margins to rank
        return None
    if any(v is None for v in got):
        return None
    return tuple(round(int(v) / _EMU_PER_IN, 4) for v in got)  # type: ignore[return-value]


def within_court(margins: Any) -> bool:
    """Whether ``margins`` (top, right, bottom, left) keep rule 2.107's floors."""
    if not isinstance(margins, (list, tuple)) or len(margins) != 4:
        return False
    left_floor, right_floor = COURT_FLOORS_IN
    return float(margins[3]) + MARGIN_TOLERANCE_IN >= left_floor and (
        float(margins[1]) + MARGIN_TOLERANCE_IN >= right_floor
    )


def same(prop: str, a: Any, b: Any) -> bool:
    """Whether two read values are one value. ``None`` is never a difference."""
    if a is None or b is None:
        return True
    if prop == MARGINS:
        if not isinstance(a, (list, tuple)) or not isinstance(b, (list, tuple)) or len(a) != len(b):
            return False
        return all(abs(float(x) - float(y)) <= MARGIN_TOLERANCE_IN for x, y in zip(a, b))
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    try:
        return abs(float(a) - float(b)) <= 0.01
    except (TypeError, ValueError):
        return a == b


def _probe_values(blob: bytes, cls: str, paper: str) -> dict[str, Any]:
    """The read keys off a fictional brief laid into ``blob`` by the class's plan,
    the way ``base_lay`` lays a draft (not through its loader: this is a reading,
    not a document the job is made in)."""
    from ..firmbase.base import FirmBase, Source
    from . import base_lay, brief_base, firm_rules
    from .revise import filed

    out: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp:
        try:
            base = FirmBase.load(cls, Source("", "probe", "the library file", ""), blob)
            path, _r = brief_base.render_into(
                firm_rules.PROBE_MD, Path(tmp) / "probe.docx", base, paper, base_lay.PLANS[cls][0]
            )
        except Exception:  # noqa: BLE001 - a file the probe cannot be laid in reads nothing, never a difference
            return {}
        for key in READ_KEYS:
            try:
                out[key] = filed.value(path, key)
            except Exception:  # noqa: BLE001 - one unreadable key reads as None, the rest still read
                out[key] = None
    return out


def read(blob: bytes | None, contract: str, cls: str, paper: str) -> dict[str, Any]:
    """Each property's value in the file: the margins for any file, and the read
    keys for a marked file whose class has a plan. ``{}`` with no file."""
    from . import base_lay

    if blob is None:
        return {}
    out: dict[str, Any] = {MARGINS: margins_of(blob)}
    if contract == "marked" and cls in base_lay.PLANS:
        out.update(_probe_values(blob, cls, paper))
    return out


__all__ = [
    "COURT",
    "COURT_FLOORS_IN",
    "MARGINS",
    "PITCH_KEYS",
    "READ_KEYS",
    "margins_of",
    "read",
    "same",
    "within_court",
]
