"""Code writes every cite, quote, figure, date, aggregate and table, last.

``render`` fills each markup token from the ledger: a cite as
``(FILE: name, p. N)`` or ``(Surname Dep. 45:3-7)``, a quote as the page's own
characters with its cite, a figure from its parsed value, a sum or a count or
an interval computed here, the charges table from ``howell``. It returns the
text and a span map: where each piece of code-written text sits and which
facts it traces to.

``assert_spans`` is the proof run after everything else: every span is still
exactly what code wrote (any later edit to a cite, a quote or a figure is a
failure), every span re-renders from verified facts, and nothing outside the
spans reads as a hand-typed cite, quote, figure or date.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

from .. import howell
from . import lint as lint_mod
from . import markup
from .shapes import Fact, Finding, Ledger, Span, Token
from .verify import locate

_DEPONENT = (
    re.compile(r"(?i)\bdepo(?:sition)?(?:\s+transcript)?\s+of\s+((?:[A-Z][A-Za-z'.-]*\s+){0,3}[A-Z][A-Za-z'-]+)"),
    re.compile(r"^((?:[A-Z][A-Za-z'.-]*[\s_]+){0,2}[A-Z][A-Za-z'-]+)[\s_,-]+(?:\(?\d.*?\)?[\s_-]+)?depo", re.I),
)


class RenderError(RuntimeError):
    def __init__(self, findings: list[Finding]) -> None:
        self.findings = findings
        super().__init__("; ".join(f.line() for f in findings[:5]) + (" ..." if len(findings) > 5 else ""))


@dataclass(frozen=True)
class Rendered:
    text: str
    spans: tuple[Span, ...]


@dataclass
class SpanReport:
    ok: bool
    spans: int
    traced: int
    problems: list[Finding] = field(default_factory=list)


# ---- one piece of each kind --------------------------------------------------------------


def surname(f: Fact, deponents: Mapping[str, str] | None = None) -> str | None:
    """The deponent's surname: the caller's table, a ``deponent:`` tag, or
    the transcript's file name ("Deposition of Jane Example.pdf")."""
    if deponents and f.file in deponents:
        return deponents[f.file]
    for t in f.tags:
        if t.lower().startswith("deponent:"):
            got = t.split(":", 1)[1].strip()
            if got:
                return got.split()[-1]
    stem = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", f.file)
    for rx in _DEPONENT:
        m = rx.search(stem)
        if m:
            return m.group(1).replace("_", " ").split()[-1]
    return None


def lines_text(f: Fact) -> str:
    assert f.tpage is not None and f.lines is not None
    a, b = f.lines
    end = next((int(t.split(":", 1)[1]) for t in f.tags if t.startswith("tpage_end:")), None)
    if end is not None and end != f.tpage:
        return f"{f.tpage}:{a}-{end}:{b}"
    return f"{f.tpage}:{a}" if a == b else f"{f.tpage}:{a}-{b}"


def cite_parts(facts: Sequence[Fact], deponents: Mapping[str, str] | None = None) -> str:
    """The inside of a cite's parentheses: testimony by deponent page and
    line, everything else by file and page, one file's pages together."""
    order: list[tuple[str, str]] = []
    pages: dict[str, list[int]] = {}
    at: dict[str, list[str]] = {}
    for f in facts:
        tx = f.tpage is not None and f.lines is not None
        who = surname(f, deponents) if tx else None
        if who:
            key = f"{who} Dep. {lines_text(f)}"
            if ("dep", key) not in order:
                order.append(("dep", key))
            continue
        if f.file not in pages:
            pages[f.file], at[f.file] = [], []
            order.append(("file", f.file))
        if f.page not in pages[f.file]:
            pages[f.file].append(f.page)
        if tx and lines_text(f) not in at[f.file]:
            at[f.file].append(lines_text(f))
    parts: list[str] = []
    for kind, key in order:
        if kind == "dep":
            parts.append(key)
            continue
        ps = sorted(pages[key])
        p = f"p. {ps[0]}" if len(ps) == 1 else "pp. " + _page_list(ps)
        parts.append(f"FILE: {key}, {p}" + (f", at {', '.join(at[key])}" if at[key] else ""))
    return "; ".join(parts)


def _page_list(pages: list[int]) -> str:
    out, run = [], [pages[0]]
    for p in pages[1:]:
        if p == run[-1] + 1:
            run.append(p)
        else:
            out.append(run)
            run = [p]
    out.append(run)
    return ", ".join(f"{r[0]}-{r[-1]}" if len(r) > 1 else str(r[0]) for r in out)


def figure_text(f: Fact) -> str:
    v = str(f.value)
    if f.unit == "usd":
        return howell.fmt(Decimal(v))
    if f.unit == "pct":
        return f"{v}%"
    return v


def date_text(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{calendar.month_name[d.month]} {d.day}, {d.year}"


def _months_between(a: date, b: date) -> int:
    m = (b.year - a.year) * 12 + (b.month - a.month)
    return m - (1 if b.day < a.day else 0)


def span_text(a: Fact, b: Fact, unit: str | None) -> str:
    x, y = sorted((date.fromisoformat(str(a.value)), date.fromisoformat(str(b.value))))
    unit = unit or "days"
    if unit == "days":
        n = (y - x).days
    elif unit == "weeks":
        n = (y - x).days // 7
    elif unit == "months":
        n = _months_between(x, y)
    else:
        n = _months_between(x, y) // 12
    word = unit[:-1] if n == 1 else unit
    return f"{n} {word}"


def quote_text(f: Fact, fragment: str | None) -> str | None:
    """The quoted characters: the whole excerpt, or the excerpt's own slice
    for the fragment's words (None when the excerpt does not carry them).
    Inner double quotation marks become single, as a quotation in a brief
    sets them; nothing else changes."""
    text = f.excerpt
    if fragment is not None:
        hit = locate(fragment, text)
        if hit is None:
            return None
        a, b = hit
        while b < len(text) and text[b] in "!?" and fragment.rstrip().endswith(text[b]):
            b += 1
        text = text[a:b]
    return text.replace('"', "'").replace("“", "‘").replace("”", "’")


#: What an authority with no primary text on file carries in the brief.
NO_PRIMARY = "{{ATTORNEY: no primary text of this authority is on file; confirm the citation and what it holds}}"


def piece(
    t: Token,
    ledger: Ledger,
    charges: list[dict[str, Any]] | None,
    deponents: Mapping[str, str] | None,
    authorities: Any = None,
) -> str:
    """The text one token renders to. Assumes ``markup.validate`` passed."""
    facts = [ledger.get(i) for i in t.ids]
    fs: list[Fact] = [f for f in facts if f is not None]
    if t.kind == "F":
        return f"({cite_parts(fs, deponents)})"
    if t.kind == "Q":
        q = quote_text(fs[0], t.text)
        if q is None:
            raise RenderError(
                [Finding("quote_fragment", f"{fs[0].id}'s excerpt does not carry those words", t.start, t.end, t.raw)]
            )
        return f'"{q}" ({cite_parts(fs, deponents)})'
    if t.kind == "N":
        return figure_text(fs[0])
    if t.kind == "D":
        return date_text(str(fs[0].value))
    if t.kind == "SUM":
        total = sum((Decimal(str(f.value)) for f in fs), Decimal("0"))
        if fs[0].unit == "usd":
            return howell.fmt(total)
        return f"{total.normalize():f}" + ("%" if fs[0].unit == "pct" else "")
    if t.kind == "COUNT":
        return str(len(fs))
    if t.kind == "SPAN":
        return span_text(fs[0], fs[1], t.text)
    if t.kind == "T":
        if charges is None:
            raise RenderError([Finding("no_table", "no charges table was built for this job", t.start, t.end, t.raw)])
        return howell.markdown(charges)
    if t.kind == "L":
        if not t.ids:
            return f"{t.text} {NO_PRIMARY}"
        cite = markup.citation_of(authorities, t.ids[0])
        if cite is None:
            raise RenderError(
                [Finding("unknown_authority", f"{t.ids[0]} is not in the authority store", t.start, t.end, t.raw)]
            )
        return cite
    if t.kind == "A":
        if not fs:
            return "{{ATTORNEY: " + str(t.text) + "}}"
        f = fs[0]
        why = f"; {f.image_reading}" if f.image_reading else ""
        return "{{ATTORNEY: confirm before relying on it: " + f.claim + f" ({cite_parts([f], deponents)}){why}" + "}}"
    raise RenderError([Finding("malformed_token", f"unknown token kind {t.kind}", t.start, t.end, t.raw)])


# ---- the stage -------------------------------------------------------------------------------


def render(
    text: str,
    ledger: Ledger,
    charges: list[dict[str, Any]] | None = None,
    deponents: Mapping[str, str] | None = None,
    authorities: Any = None,
) -> Rendered:
    """The markup with every token filled. Refuses (``RenderError``) on any
    token that does not parse or names a fact it cannot: render is the last
    word, never a best effort."""
    tokens, bad = markup.parse(text)
    bad += markup.validate(tokens, ledger, authorities)
    if bad:
        raise RenderError(bad)
    out: list[str] = []
    spans: list[Span] = []
    cursor = 0
    pos = 0
    for t in tokens:
        out.append(text[cursor : t.start])
        pos += t.start - cursor
        s = piece(t, ledger, charges, deponents, authorities)
        spans.append(Span(t.kind, t.ids, pos, pos + len(s), s, t.raw))
        out.append(s)
        pos += len(s)
        cursor = t.end
    out.append(text[cursor:])
    return Rendered("".join(out), tuple(spans))


def assert_spans(
    rendered: str,
    spans: Iterable[Span],
    ledger: Ledger,
    charges: list[dict[str, Any]] | None = None,
    deponents: Mapping[str, str] | None = None,
    exempt: Iterable[str] = (),
    authorities: Any = None,
) -> SpanReport:
    """The proof that every cite, quote, figure and date in ``rendered`` is
    code's, from verified facts, unedited.

    * Each span re-renders, from the ledger as it stands, to exactly its text
      (it traces to usable facts).
    * Each span's text still stands in ``rendered``, in order: at its offset,
      or later when prose before it changed length. Missing, it was edited.
    * Outside the spans, nothing is a hand-typed cite, quotation of four words
      or more, dollar figure or date (``exempt`` text, the firm's skeleton,
      excepted)."""
    spans = list(spans)
    problems: list[Finding] = []
    traced = 0
    for s in spans:
        tokens, bad = markup.parse(s.raw)
        if bad or len(tokens) != 1:
            problems.append(Finding("span_untraced", "the span's token does not parse", s.start, s.end, s.raw))
            continue
        bad = markup.validate(tokens, ledger, authorities)
        if bad:
            problems += [Finding("span_untraced", b.message, s.start, s.end, s.text[:80]) for b in bad]
            continue
        try:
            again = piece(tokens[0], ledger, charges, deponents, authorities)
        except RenderError as exc:
            problems += exc.findings
            continue
        if again != s.text:
            problems.append(
                Finding("span_untraced", "the span no longer renders from its facts", s.start, s.end, s.text[:80])
            )
            continue
        traced += 1
    cursor = 0
    placed: list[tuple[int, int]] = []
    for s in spans:
        if rendered[s.start : s.end] == s.text and s.start >= cursor:
            at = s.start
        else:
            at = rendered.find(s.text, cursor)
        if at < 0:
            problems.append(
                Finding("span_edited", "a code-written span was changed after render", s.start, s.end, s.text[:80])
            )
            continue
        placed.append((at, at + len(s.text)))
        cursor = at + len(s.text)
    blank = list(rendered)
    for a, b in placed:
        for i in range(a, b):
            if blank[i] != "\n":
                blank[i] = " "
    outside = "".join(blank)
    for f in lint_mod.hand_typed(markup.strip_tokens(outside), exempt):
        problems.append(Finding("untraced_" + f.code, f.message, f.start, f.end, f.snippet, f.sentence))
    return SpanReport(not problems, len(spans), traced, problems)
