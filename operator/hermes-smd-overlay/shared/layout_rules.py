"""A firm's layout rule, compiled to a renderer key the drafting lane applies.

WHY (2026-11-05, the A&P audit). An attorney's rule about how the firm's
documents are laid out ("two blank lines before the court name") was installed
to ``specs/classes/work_product/format.md``, and nothing that renders those
documents reads that file: the drafting lane takes its layout from
``drafting-firm.yaml`` ``format.layout.<class>``, which only an SMD release
changes. So the firm was told "recorded" and the next brief ignored the rule.

WHAT THIS DOES. The mechanical tier only. A rule whose words map to ONE of the
renderer's layout keys, with a value code can check, compiles to
``{key: value}``; the spec applier (root) writes every compiled rule of the
class to ``classes/work_product/layout.json`` beside ``format.md``, and the
drafting lane (``operator/runners/jobs/jobs/drafting/firm_rules.py``) lays them
over ``format.layout.<class>`` at job start, so the render and the format check
read the same values. A rule that does not map, or maps ambiguously, or carries
a condition or a negation, stays prose: recorded, never called in effect.

CODE DECIDES, NO MODEL. The patterns below are the whole grammar, and
:func:`compile_rule` either returns keys whose values passed :func:`problems`
(the same ranges as the lane's ``revise/checklist.LAYOUT_KEYS``, pinned by a
parity test on the lane's side) or a reason it did not. A guess here would be a
rule the firm never gave, applied to a filed document.

STDLIB ONLY, deliberately: the lane's parity test loads this file by path.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

#: Schema of ``layout.json``. Bumped together with the lane's reader.
SCHEMA_VERSION = 1

#: The output classes whose rules can compile to layout keys: the drafting
#: lane's documents. Every class here has a reader in the jobs runner.
LAYOUT_RULE_CLASSES: frozenset[str] = frozenset({"work_product"})

#: The property a layout rule is installed under.
LAYOUT_PROPERTY = "format"

#: The file beside ``<class>/format.md``.
LAYOUT_NAME = "layout.json"

#: The drafting lane's document classes (``jobs.drafting.firm.CLASSES``).
DOC_CLASSES = ("mediation_brief", "discovery_set", "discovery_response", "memo", "depo_outline")

#: A copy of the lane's ``revise/checklist.LAYOUT_KEYS``: key -> (type, lo, hi).
#: A copy rather than an import because the seat runs the two in different
#: venvs; ``tests/test_drafting_firm_rules.py`` fails if they ever differ.
LAYOUT_KEYS: dict[str, tuple[Any, Any, Any]] = {
    "line_spacing": (float, 1.0, 3.0),
    "heading_line_spacing": (float, 1.0, 3.0),
    "line_spacing_pt": (float, 6.0, 72.0),
    "heading_line_spacing_pt": (float, 6.0, 72.0),
    "heading_space_before_pt": (float, 0.0, 48.0),
    "heading_space_after_pt": (float, 0.0, 48.0),
    "table_font_pt": (float, 8.0, 20.0),
    "table_border_pt": (float, 0.0, 6.0),
    "table_header_fill": (str, None, None),
    "table_total_fill": (str, None, None),
    "front_single_spaced": (bool, None, None),
    "front_attorney_gap_lines": (int, 0, 10),
    "front_court_gap_lines": (int, 0, 10),
    "front_caption_gap_lines": (int, 0, 10),
    "caption_rule_below": ("rule", None, None),
    "signature_date_beside": (bool, None, None),
    "signature_single_spaced": (bool, None, None),
    "signature_gap_lines": (int, 0, 10),
}
_HEX = re.compile(r"^[0-9A-Fa-f]{6}$")

#: The document classes a firm may have made in its own Word file
#: (``jobs.drafting.firm.FIRM_BASE_CLASSES``), and the keys that file owns and
#: the lane's in-place editor cannot move on it (``brief_base.BASE_OWNS``). A
#: rule setting one of these keys for such a class is not compiled: the lane
#: could not apply it there, so the firm must not be told it is in effect.
BASE_CLASSES = ("mediation_brief", "memo")
BASE_OWNS = ("table_font_pt", "table_header_fill", "table_total_fill", "table_border_pt")


def problems(key: str, value: Any) -> list[str]:
    """Why ``value`` is not a valid ``key``, or ``[]``. The lane's ranges."""
    spec = LAYOUT_KEYS.get(key)
    if spec is None:
        return [f"layout key {key!r} is not one of {sorted(LAYOUT_KEYS)}"]
    typ, lo, hi = spec
    if typ is str:
        return (
            []
            if isinstance(value, str) and _HEX.match(value)
            else [f"layout {key} is a six-digit RRGGBB colour"]
        )
    if typ is bool:
        return [] if isinstance(value, bool) else [f"layout {key} is true or false"]
    if typ == "rule":
        return (
            []
            if isinstance(value, bool) or value == "left"
            else [f'layout {key} is true, false or "left"']
        )
    if typ is int:
        ok = isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi
        return [] if ok else [f"layout {key} is a whole number from {lo} to {hi}"]
    ok = (
        isinstance(value, (int, float)) and not isinstance(value, bool) and lo <= float(value) <= hi
    )
    return [] if ok else [f"layout {key} is a number from {lo} to {hi}"]


_WORDS = {
    "a": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}
_N = r"(?P<n>\d+(?:\.\d+)?|" + "|".join(sorted(_WORDS, key=len, reverse=True)) + r")"
_BLANK = _N + r" (?:single )?blank (?:single )?lines?"
_SPACED = r"(?P<s>single|double|1\.5|one and a half)[- ]spaced"


def _num(m: re.Match[str]) -> float:
    raw = m.group("n")
    return float(_WORDS[raw]) if raw in _WORDS else float(raw)


def _gap(m: re.Match[str]) -> int | float:
    n = _num(m)
    return int(n) if n.is_integer() else n


def _spacing(m: re.Match[str]) -> float:
    return {"single": 1.0, "double": 2.0}.get(m.group("s"), 1.5)


def _true(_m: re.Match[str]) -> bool:
    return True


def _alt(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p) for p in patterns)


_HEADINGS_SPACED = r"\bheadings? (?:are |be |should be |also )?" + _SPACED

#: (key, patterns, value of a match, words that rule the key out). Matched
#: against the rule's words lowered, quotes dropped, spacing collapsed.
_PATTERNS: tuple[tuple[str, tuple[re.Pattern[str], ...], Any, re.Pattern[str] | None], ...] = (
    (
        "front_court_gap_lines",
        _alt(
            _BLANK
            + r" (?:before|above|ahead of) the (?:name of the court|court s name|court name|court)\b",
            _BLANK
            + r" (?:between|from) (?:the )?(?:attorneys? for|party represented)\b.{0,60}? (?:and|to) the court\b",
        ),
        _gap,
        None,
    ),
    (
        "front_caption_gap_lines",
        _alt(
            _BLANK + r" (?:before|above) the caption\b",
            _BLANK + r" between the court(?: s name| name)? and the caption\b",
        ),
        _gap,
        None,
    ),
    (
        "front_attorney_gap_lines",
        _alt(_BLANK + r" (?:before|above) (?:the )?attorneys? for\b"),
        _gap,
        None,
    ),
    (
        "signature_gap_lines",
        _alt(_N + r" blank (?:signature )?lines? (?:above|before) the (?:dated|by)\b"),
        _gap,
        None,
    ),
    (
        "heading_line_spacing",
        _alt(_HEADINGS_SPACED, _SPACED + r",? including (?:the )?headings\b"),
        _spacing,
        None,
    ),
    (
        "line_spacing",
        _alt(r"\b" + _SPACED + r"\b"),
        _spacing,
        re.compile(
            r"\b(?:first|front) page\b|\bsignature|\bcaption|\btables?\b|\bcharts?\b|\bfootnotes?\b|\bquot|"
            + _HEADINGS_SPACED
        ),
    ),
    (
        "heading_space_before_pt",
        _alt(
            _N
            + r" ?(?:pt|points?) (?:of )?(?:space |spacing )?(?:before|above) (?:each |every |the |all )?headings?\b"
        ),
        _num,
        None,
    ),
    (
        "heading_space_after_pt",
        _alt(
            _N
            + r" ?(?:pt|points?) (?:of )?(?:space |spacing )?(?:after|below) (?:each |every |the |all )?headings?\b"
        ),
        _num,
        None,
    ),
    (
        "table_font_pt",
        _alt(
            r"\b(?:tables?|charts?)\b(?: are| in| at| should be| be)* "
            + _N
            + r" ?(?:pt|points?|point type)\b",
            _N + r" ?(?:pt|point) (?:font |type )?(?:in|for) (?:the |all )?(?:tables|charts)\b",
        ),
        _num,
        None,
    ),
    (
        "front_single_spaced",
        _alt(r"\b(?:first|front) page (?:is |be |should be )?single[- ]spaced\b"),
        _true,
        None,
    ),
    (
        "signature_single_spaced",
        _alt(r"\bsignature(?: block)? (?:is |be |should be )?single[- ]spaced\b"),
        _true,
        None,
    ),
    (
        "signature_date_beside",
        _alt(r"\bdate (?:beside|next to|on the same line as) the by\b"),
        _true,
        None,
    ),
)

#: A rule carrying any of these is not mechanical: a condition, an exception
#: or a negation is a judgement about a case, and code applies it to every case.
_NOT_MECHANICAL = re.compile(
    r"\b(?:not|never|no longer|don t|do not|doesn t|unless|except|only when|only if|if|when|whenever|sometimes|"
    r"usually|maybe|prefer|try)\b"
)

_DOC_CLASS_WORDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("mediation_brief", re.compile(r"\bmediation briefs?\b")),
    (
        "discovery_response",
        re.compile(r"\bdiscovery responses?\b|\bresponses to (?:discovery|interrogatories)\b"),
    ),
    ("discovery_set", re.compile(r"\bdiscovery (?:sets?|requests?)\b|\binterrogatories\b")),
    ("memo", re.compile(r"\bmemos?\b|\bmemoranda\b")),
    ("depo_outline", re.compile(r"\bdepo(?:sition)? outlines?\b")),
)


def normalize(text: str) -> str:
    """Words only, lowered: quotes and dashes differ between a mail client and a
    model's copy; ``1.5`` keeps its point."""
    s = str(text or "").lower().replace("’", "'").replace("'", " ")
    s = re.sub(r"[\"“”:;()]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def compile_rule(text: str) -> tuple[dict[str, Any] | None, list[str], str]:
    """``(layout, doc_classes, reason)`` for one rule's words.

    ``layout`` is ``{key: value}`` with every value valid, or ``None`` with
    ``reason`` saying why the rule stays prose (no key, two values for one key,
    a condition, an out-of-range value). ``doc_classes`` is the document kinds
    the rule names, or ``[]`` for all of them.
    """
    words = normalize(text)
    if not words:
        return None, [], "the rule has no words"
    if _NOT_MECHANICAL.search(words):
        return (
            None,
            [],
            "the rule carries a condition or a negation, which code cannot apply to every document",
        )
    layout: dict[str, Any] = {}
    for key, patterns, value, excluded in _PATTERNS:
        if excluded is not None and excluded.search(words):
            continue
        found = {json.dumps(value(m)) for pattern in patterns for m in pattern.finditer(words)}
        if len(found) > 1:
            return None, [], f"the rule gives {key} more than one value"
        if found:
            layout[key] = json.loads(found.pop())
    if not layout:
        return None, [], "the rule names no layout the renderer has a setting for"
    refused = [p for key, value in layout.items() for p in problems(key, value)]
    if refused:
        return None, [], "; ".join(refused)
    doc_classes = [cls for cls, pattern in _DOC_CLASS_WORDS if pattern.search(words)]
    reaches_base = not doc_classes or any(c in BASE_CLASSES for c in doc_classes)
    owned = sorted(k for k in layout if k in BASE_OWNS)
    if owned and reaches_base:
        return (
            None,
            [],
            (
                f"{', '.join(owned)} is set by the firm's own Word file for this document, "
                "which the Operator cannot change in place"
            ),
        )
    return layout, doc_classes, ""


def compile_adjustments(adjustments: Any) -> dict[str, Any]:
    """The ``layout.json`` document for one class's adjustments, oldest first."""
    rules: list[dict[str, Any]] = []
    prose: list[dict[str, Any]] = []
    for adj in adjustments or ():
        if not isinstance(adj, dict):
            continue
        rule_id, text = str(adj.get("id") or ""), str(adj.get("text") or "")
        layout, doc_classes, reason = compile_rule(text)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if layout is None:
            prose.append({"rule_id": rule_id, "text_sha256": digest, "reason": reason})
        else:
            rules.append(
                {
                    "rule_id": rule_id,
                    "text_sha256": digest,
                    "layout": layout,
                    "doc_classes": doc_classes,
                }
            )
    return {"schema_version": SCHEMA_VERSION, "rules": rules, "prose": prose}


def layout_rel_path(output_class: str) -> str:
    return f"classes/{output_class}/{LAYOUT_NAME}"


def compiled_on_seat(rule_id: str, output_class: str, spec_dir: str | Path | None = None) -> bool:
    """True iff the INSTALLED ``layout.json`` holds ``rule_id`` as compiled.

    The installed file, not a fresh compile of the text: what the lane reads is
    what the letter may claim. Unreadable, absent or malformed reads False, so
    the firm is told "recorded", the safe direction.
    """
    cls = str(output_class or "").strip().lower()
    if cls not in LAYOUT_RULE_CLASSES or not rule_id:
        return False
    root = spec_dir or os.environ.get("SMD_SPEC_DIR") or ""
    if not root:
        return False
    try:
        doc = json.loads((Path(root) / layout_rel_path(cls)).read_text("utf-8"))
    except (OSError, ValueError):
        return False
    rules = doc.get("rules") if isinstance(doc, dict) else None
    return isinstance(rules, list) and any(
        isinstance(r, dict) and r.get("rule_id") == rule_id for r in rules
    )


__all__ = [
    "BASE_CLASSES",
    "BASE_OWNS",
    "DOC_CLASSES",
    "LAYOUT_KEYS",
    "LAYOUT_NAME",
    "LAYOUT_PROPERTY",
    "LAYOUT_RULE_CLASSES",
    "SCHEMA_VERSION",
    "compile_adjustments",
    "compile_rule",
    "compiled_on_seat",
    "layout_rel_path",
    "normalize",
    "problems",
]
