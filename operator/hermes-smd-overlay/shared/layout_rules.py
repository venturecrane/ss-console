"""A firm's layout rule, compiled to a renderer key the drafting lane applies.

WHY (2026-11-05, the A&P audit). An attorney's rule about how the firm's
documents are laid out ("two blank lines before the court name") was installed
to ``specs/classes/work_product/format.md``, and nothing that renders those
documents reads that file: the drafting lane takes its layout from
``drafting-firm.yaml`` ``format.layout.<class>``, which only an SMD release
changes. So the firm was told "recorded" and the next brief ignored the rule.

WHAT THIS DOES. The mechanical tier only, CLAUSE BY CLAUSE. A rule splits into
clauses (:func:`clauses`), and a negation or a condition blocks only the clause
that carries it: an attorney's sentence commonly mixes a citation rule with a
layout one, and "never cite X" says nothing about the first page. A clause whose
words map to the renderer's layout keys, with values code can check, compiles to
``{key: value}``, or to ``{key: step}`` when it asks for more of something with
no number (``relative``, one step, which the lane resolves against the document's
effective value and pins so it applies once). The spec applier (root) writes
every rule of the class to ``classes/work_product/layout.json`` beside
``format.md``, and the drafting lane
(``operator/runners/jobs/jobs/drafting/firm_rules.py``) lays the compiled clauses
over ``format.layout.<class>`` at job start, so the render and the format check
read the same values. Any other clause stays prose: recorded and passed to SMD,
never called in effect.

CODE DECIDES, NO MODEL. The patterns below are the whole grammar, and
:func:`compile_rule` either returns keys whose values passed :func:`problems`
(the same ranges as the lane's ``revise/checklist.LAYOUT_KEYS``, pinned by a
parity test on the lane's side) or a reason it did not. A guess here would be a
rule the firm never gave, applied to a filed document.

STDLIB ONLY, deliberately: the lane's parity test loads this file by path, which
is why :func:`clause_truth` imports the store's reader inside the function.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

#: Schema of ``layout.json``. Bumped together with ``layout_store`` (both copies).
SCHEMA_VERSION = 2

#: How far a "more space" clause moves its key, in the key's own unit. One step:
#: he asked for more, and he can ask again.
RELATIVE_STEP = 1

#: A relative clause on a rule installed before this instant was answered by
#: hand, in the firm's config and the file the attorney then accepted, because
#: nothing could compile "more" yet. A step on top of that would give it twice.
#: The two kinds of rule are told apart here, once, and the lane records the old
#: ones as carried. ISO strings of one format compare in time order.
RELATIVE_FROM = "2026-11-05T15:00:00Z"

#: The line pitch "double spaced" means on a firm's pleading pages, in points.
PITCH_PT = 24.0

#: A pattern's answer for a clause asking for more, with no number.
_MORE_VALUE = "__relative__"

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


def _more(_m: re.Match[str]) -> str:
    return _MORE_VALUE


def _caption_rule(m: re.Match[str]) -> bool | str:
    """A line that stops at the caption's vertical rule is the renderer's "left"."""
    return "left" if "vertical line" in m.string else True


def _alt(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p) for p in patterns)


_HEADINGS_SPACED = r"\bheadings? (?:are |be |should be |also )?" + _SPACED
_MORE = r"\b(?:clear )?(?:more|extra|additional)(?: clear)? (?:space|spacing|room)\b"

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
        "front_attorney_gap_lines",
        _alt(
            _MORE
            + r".{0,40}?between (?:the )?attorney (?:info|information|caption|block)\b.{0,40}?"
            + r" and (?:the )?(?:party represented|party|who is represented)\b"
        ),
        _more,
        None,
    ),
    (
        "front_court_gap_lines",
        _alt(
            _MORE
            + r".{0,40}?between (?:the )?(?:attorneys? for|party represented)\b.{0,60}?"
            + r" and (?:the )?(?:\w+ )*?court\b"
        ),
        _more,
        None,
    ),
    (
        "front_caption_gap_lines",
        _alt(
            _MORE
            + r".{0,120}?between the court(?: name| s name| identification)? and (?:the )?caption\b"
        ),
        _more,
        None,
    ),
    (
        "caption_rule_below",
        _alt(
            r"\bsolid line\b.{0,80}?\bcaption\b",
            r"\bfull[- ]width line\b.{0,40}?\bcaption\b",
            r"\bline under (?:the )?(?:last line of the )?(?:case )?caption\b",
        ),
        _caption_rule,
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
        _alt(
            _HEADINGS_SPACED,
            _SPACED + r",? (?:including|even between|between)(?: the| paragraph)* headings\b",
        ),
        _spacing,
        None,
    ),
    (
        "line_spacing",
        _alt(r"\b" + _SPACED + r"\b"),
        _spacing,
        re.compile(
            r"\b(?:first|front) page\b|\bsignature|\bcaption|\babove the (?:brief|body)\b|"
            r"\btables?\b|\bcharts?\b|\bfootnotes?\b|\bquot|" + _HEADINGS_SPACED
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
        "table_font_pt",
        _alt(
            r"\bcharts?\b.{0,60}\b(?:enlarg\w+|larger|bigger|increas\w+)(?: the)? font\b",
            r"\b(?:enlarg\w+|larger|bigger)(?: the)? font\b.{0,40}\bcharts?\b",
        ),
        _more,
        None,
    ),
    (
        "front_single_spaced",
        _alt(
            r"\b(?:first|front) page (?:is |be |should be )?single[- ]spaced\b",
            r"\battorney caption\b.{0,40}\bsingle[- ]spaced\b",
            r"\b(?:everything|all information|all) above the (?:brief|body)\b.{0,40}\bsingle[- ]spaced\b",
            r"\bsingle[- ]spaced\b.{0,60}\babove the (?:brief|body)\b",
        ),
        _true,
        None,
    ),
    (
        "signature_single_spaced",
        _alt(
            r"\bsignature(?: block| line)?(?: of (?:the )?attorney)? "
            r"(?:is |be |should be |should always be )?single[- ]spaced\b",
            r"\bsingle[- ]space(?:d)? (?:the )?(?:attorney )?signature\b",
        ),
        _true,
        None,
    ),
    (
        "signature_date_beside",
        _alt(
            r"\bdate (?:beside|next to|on the same line as) the by\b",
            r"\bdate(?: line)?\b.{0,60}?\b(?:even with|level with|in line with)\b",
        ),
        _true,
        None,
    ),
)

#: A rule carrying any of these is not mechanical: a condition, an exception
#: or a negation is a judgement about a case, and code applies it to every case.
_NOT_MECHANICAL = re.compile(
    r"\b(?:not|never|no longer|don t|do not|doesn t|unless|except|only when|only if|if|when|whenever|sometimes|"
    r"usually|maybe|perhaps|prefer|try)\b"
)

_DOC_CLASS_WORDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("mediation_brief", re.compile(r"\b(?:mediation )?briefs?\b")),
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


#: What opens a new clause: an instruction's verb. Commas alone do not, because
#: a clause often lists things ("court, case caption, date").
_HEAD = (
    r"(?:leave|put|end|make|file|save|cite|mention|never|single space|double space|"
    r"perhaps|there (?:should|needs?)|should also|do not|don[\u2019']t)\b"
)
_SPLIT = re.compile(
    rf"\s*;\s*|(?<=[.!?])\s+|\s+[\u2013\u2014-]\s+|,\s+(?:and\s+)?(?={_HEAD})|\s+and\s+(?={_HEAD})"
    r"|,\s+(?=not\b)",
    re.I,
)
#: A rule's opening scope ("In every mediation brief,"), which is not a clause.
_SCOPE = re.compile(r"^(?:in|on|for|at)\b[^,]{0,50}$", re.I)
_CONTRAST = re.compile(r"^not\b", re.I)


def clauses(text: str) -> list[tuple[str, str]]:
    """``(what the attorney wrote, the words that compile)`` per clause.

    A contrast tail (", not on top of the block") belongs to the clause before
    it: it says the same thing again, and its "not" must not block that clause.
    """
    out: list[tuple[str, str]] = []
    pieces = [p.strip(" ,") for p in _SPLIT.split(str(text or "")) if p and p.strip(" ,.")]
    scope = ""
    for n, piece in enumerate(pieces):
        if n == 0 and len(pieces) > 1 and _SCOPE.match(piece):
            scope = piece + ", "
            continue
        if _CONTRAST.match(piece) and out:
            wrote, words = out[-1]
            out[-1] = (f"{wrote.rstrip('.')}, {piece}", words)
            continue
        wrote, scope = scope + piece, ""
        out.append((wrote, wrote))
    return out


def _pitch_safe(layout: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """``layout`` as a class on one line pitch must take it. Word-multiple spacing
    moves paragraphs off a pleading page's lines and the format check refuses the
    whole document, so "double spaced" means the pitch and anything else is prose."""
    if "line_spacing" not in layout and "heading_line_spacing" not in layout:
        return layout, ""
    if {layout.get("line_spacing", 2.0), layout.get("heading_line_spacing", 2.0)} != {2.0}:
        return {}, "a firm's pleading pages hold to one line pitch, so this spacing cannot apply"
    out = {k: v for k, v in layout.items() if k not in ("line_spacing", "heading_line_spacing")}
    if "line_spacing" in layout:
        out["line_spacing_pt"] = PITCH_PT
    if "heading_line_spacing" in layout:
        out.update(
            heading_line_spacing_pt=PITCH_PT,
            heading_space_before_pt=0.0,
            heading_space_after_pt=0.0,
        )
    return out, ""


def _compile_clause(
    words: str, doc_classes: list[str]
) -> tuple[dict[str, Any], dict[str, int], str]:
    """``(layout, relative, reason)``; ``reason`` non-empty means the clause is prose."""
    if _NOT_MECHANICAL.search(words):
        return (
            {},
            {},
            "the clause carries a condition or a negation, which code cannot apply to every document",
        )
    layout: dict[str, Any] = {}
    relative: dict[str, int] = {}
    for key, patterns, value, excluded in _PATTERNS:
        if excluded is not None and excluded.search(words):
            continue
        found = {json.dumps(value(m)) for pattern in patterns for m in pattern.finditer(words)}
        if len(found) > 1:
            return {}, {}, f"the clause gives {key} more than one value"
        if found:
            got = json.loads(found.pop())
            if got == _MORE_VALUE:
                relative[key] = RELATIVE_STEP
            else:
                layout[key] = got
    if not layout and not relative:
        return {}, {}, "the clause names no layout the renderer has a setting for"
    refused = [p for key, value in layout.items() for p in problems(key, value)]
    if refused:
        return {}, {}, "; ".join(refused)
    if any(c in BASE_CLASSES for c in doc_classes):
        layout, why = _pitch_safe(layout)
        if why:
            return {}, {}, why
    owned = sorted(k for k in (*layout, *relative) if k in BASE_OWNS)
    if owned and doc_classes and all(c in BASE_CLASSES for c in doc_classes):
        return (
            {},
            {},
            (
                f"{', '.join(owned)} is set by the firm's own Word file for this document, "
                "which the Operator cannot change in place"
            ),
        )
    return layout, relative, ""


def compile_rule(text: str, at: str = "", doc_classes: list[str] | None = None) -> dict[str, Any]:
    """One rule's words: ``{doc_classes, compiled: [...], prose: [...]}``.

    A compiled clause is ``{text, layout, relative, base_owned, carried}``: its
    absolute values, its one-step asks, the keys a firm's own Word file sets for
    a mediation brief (in effect everywhere else, and the letter says so), and
    whether a relative ask predates :data:`RELATIVE_FROM`. A prose clause is
    ``{text, reason}``. ``doc_classes`` is the kinds the rule names, whole rule,
    or ``[]`` for all of them; a caller that recorded the rule's kinds when it was
    confirmed passes them instead (``shared/layout_reach.py``). ``at`` is when the
    rule was installed.
    """
    whole = normalize(text)
    if doc_classes is None:
        doc_classes = [cls for cls, pattern in _DOC_CLASS_WORDS if pattern.search(whole)]
    compiled: list[dict[str, Any]] = []
    prose: list[dict[str, str]] = []
    for wrote, words in clauses(text):
        layout, relative, reason = _compile_clause(normalize(words), doc_classes)
        if reason:
            prose.append({"text": wrote, "reason": reason})
            continue
        compiled.append(
            {
                "text": wrote,
                "layout": layout,
                "relative": relative,
                "base_owned": sorted(k for k in (*layout, *relative) if k in BASE_OWNS),
                "carried": bool(relative and at and str(at) < RELATIVE_FROM),
            }
        )
    return {"doc_classes": doc_classes, "compiled": compiled, "prose": prose}


def compile_adjustments(adjustments: Any) -> dict[str, Any]:
    """The ``layout.json`` document for one class's adjustments, oldest first."""
    rules: list[dict[str, Any]] = []
    for adj in adjustments or ():
        if not isinstance(adj, dict):
            continue
        rule_id, text = str(adj.get("id") or ""), str(adj.get("text") or "")
        rules.append(
            {
                "rule_id": rule_id,
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                **compile_rule(text, str(adj.get("at") or "")),
            }
        )
    return {"schema_version": SCHEMA_VERSION, "rules": rules}


def layout_rel_path(output_class: str) -> str:
    return f"classes/{output_class}/{LAYOUT_NAME}"


def clause_truth(
    rule_id: str, output_class: str, spec_dir: str | Path | None = None
) -> dict[str, list[dict[str, Any]]] | None:
    """``{compiled, prose}`` for ``rule_id`` as the INSTALLED file holds it, read
    through the reader the lane uses, digest included; ``None`` when the seat has
    no usable answer. What the lane reads is what the letter may claim, so a torn
    store and a rule not yet installed both read as "recorded", the safe direction.
    """
    from shared import layout_store

    cls = str(output_class or "").strip().lower()
    if cls not in LAYOUT_RULE_CLASSES or not rule_id:
        return None
    root = spec_dir or os.environ.get("SMD_SPEC_DIR") or ""
    if not root:
        return None
    store = layout_store.read(root, problems)
    if store.status != layout_store.OK:
        return None
    for rule in store.rules:
        if rule.get("rule_id") == rule_id:
            return {"compiled": rule["compiled"], "prose": rule["prose"]}
    return None


def compiled_on_seat(rule_id: str, output_class: str, spec_dir: str | Path | None = None) -> bool:
    """True iff the installed, vouched-for file holds a compiled clause of ``rule_id``."""
    truth = clause_truth(rule_id, output_class, spec_dir)
    return bool(truth and truth["compiled"])


__all__ = [
    "BASE_CLASSES",
    "BASE_OWNS",
    "DOC_CLASSES",
    "LAYOUT_KEYS",
    "LAYOUT_NAME",
    "LAYOUT_PROPERTY",
    "LAYOUT_RULE_CLASSES",
    "SCHEMA_VERSION",
    "PITCH_PT",
    "RELATIVE_FROM",
    "RELATIVE_STEP",
    "clause_truth",
    "clauses",
    "compile_adjustments",
    "compile_rule",
    "compiled_on_seat",
    "layout_rel_path",
    "normalize",
    "problems",
]
