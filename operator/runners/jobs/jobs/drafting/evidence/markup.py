"""The markup the drafter writes: prose over fact tokens only.

    {{F:F0001}}            cite (one id, or several: {{F:F0001,F0007}})
    {{Q:F0001}}            the fact's whole excerpt, quoted, with its cite
    {{Q:F0001|fragment}}   a contiguous fragment of that excerpt, quoted, with its cite
    {{N:F0003}}            a figure's value
    {{D:F0004}}            a date
    {{SUM:F0003,F0005}}    a sum code computes over figures
    {{COUNT:F0002,F0006}}  how many facts
    {{SPAN:F0004,F0009}}   the time between two dates (|weeks, |months or |years for another unit)
    {{T:charges}}          the charges table, built by code
    {{A:F0010}}            an attorney marker on an unconfirmed fact
    {{A:?|text}}           an attorney marker with the drafter's own question
    {{L:slug}}             a legal authority from the firm's authority store
    {{L:?|citation}}       an authority with no primary text on file (rendered with an attorney marker)

``parse`` reads them; ``validate`` checks their ids against the ledger and
each kind against the fact's kind. The lane's own ``{{FILL: ...}}`` and
``{{ATTORNEY: ...}}`` markers pass through untouched. Any other ``{{`` is a
malformed token: the drafter does not get to invent syntax.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from .shapes import TOKEN_KINDS, Finding, Ledger, Token

#: Markers the lane already uses; markup passes them through.
PASSTHROUGH = ("FILL", "ATTORNEY")
_TOKEN = re.compile(r"\{\{\s*(" + "|".join(TOKEN_KINDS) + r")\s*:([^{}]*)\}\}")
_PASS = re.compile(r"\{\{\s*(?:" + "|".join(PASSTHROUGH) + r")\s*:.*?\}\}", re.S)
_OPEN = re.compile(r"\{\{")
_ID = re.compile(r"^F\d{4,}$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SPAN_UNITS = ("days", "weeks", "months", "years")
TABLES = ("charges",)

#: For each id-bearing kind, the fact kinds it may name.
ALLOWED: dict[str, tuple[str, ...]] = {
    "F": ("fact", "figure", "date", "quote", "testimony"),
    "Q": ("fact", "figure", "date", "quote", "testimony"),
    "N": ("figure",),
    "D": ("date",),
    "SUM": ("figure",),
    "COUNT": ("fact", "figure", "date", "quote", "testimony"),
    "SPAN": ("date",),
    "A": ("fact", "figure", "date", "quote", "testimony", "inference"),
}


def passthrough_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _PASS.finditer(text)]


def parse(text: str) -> tuple[list[Token], list[Finding]]:
    """Every token in order, and a finding for every ``{{`` that is neither a
    token nor a passthrough marker, or a token whose arguments do not parse."""
    tokens: list[Token] = []
    findings: list[Finding] = []
    covered = passthrough_spans(text)
    for m in _TOKEN.finditer(text):
        tok, err = _token(m.group(1), m.group(2), m.start(), m.end(), m.group(0))
        if err:
            findings.append(Finding("malformed_token", err, m.start(), m.end(), m.group(0)))
        else:
            tokens.append(tok)  # type: ignore[arg-type]
        covered.append((m.start(), m.end()))
    for m in _OPEN.finditer(text):
        if not any(a <= m.start() < b for a, b in covered):
            end = text.find("}}", m.start())
            end = len(text) if end < 0 else end + 2
            findings.append(
                Finding(
                    "malformed_token", "not a markup token", m.start(), end, text[m.start() : min(end, m.start() + 80)]
                )
            )
    return tokens, findings


def _token(kind: str, args: str, start: int, end: int, raw: str) -> tuple[Token | None, str | None]:
    head, sep, rest = args.partition("|")
    free = rest.strip() if sep else None
    if kind in ("T", "L") or (kind == "A" and head.strip() == "?"):
        return _free_token(kind, head.strip(), free, start, end, raw)
    ids = tuple(i.strip() for i in head.split(",") if i.strip())
    err = _ids_error(kind, ids)
    if err:
        return None, err
    if kind == "SPAN":
        if free is not None and free.lower() not in SPAN_UNITS:
            return None, f"SPAN unit must be one of {', '.join(SPAN_UNITS)}"
        free = free.lower() if free else None
    elif free is not None and kind not in ("Q", "A"):
        return None, f"{kind} takes no text after |"
    if kind == "Q" and free is not None and not free:
        return None, "an empty quote fragment"
    return Token(kind, ids, start, end, raw, text=free), None


def _ids_error(kind: str, ids: tuple[str, ...]) -> str | None:
    if not ids:
        return "no fact id"
    bad = [i for i in ids if not _ID.match(i)]
    if bad:
        return f"not a fact id: {', '.join(bad)}"
    if kind in ("Q", "N", "D") and len(ids) != 1:
        return f"{kind} takes exactly one fact id"
    if kind == "SPAN" and len(ids) != 2:
        return "SPAN takes exactly two date ids"
    return None


def _free_token(
    kind: str, head: str, free: str | None, start: int, end: int, raw: str
) -> tuple[Token | None, str | None]:
    """T (a table name), L (an authority slug or ``?`` with its citation),
    and A:? (the drafter's question): tokens that name no fact."""
    if kind == "T":
        name = head.lower()
        if name not in TABLES:
            return None, f"unknown table {head!r}"
        return Token(kind, (), start, end, raw, name=name), None
    if head == "?":
        if not free:
            what = (
                "an authority with no primary text needs its citation"
                if kind == "L"
                else "an attorney question needs its text"
            )
            return None, f"{what} after |"
        return Token(kind, (), start, end, raw, text=free), None
    if not _SLUG.match(head) or free is not None:
        return None, f"not an authority slug: {head!r}"
    return Token(kind, (head,), start, end, raw), None


def validate(tokens: list[Token], ledger: Ledger, authorities: Any = None) -> list[Finding]:
    """Ids the ledger does not hold or rejected, and a kind the token cannot
    name (a figure token on a date, a fact token on an inference). An L slug
    is checked only when an authority store is given (``citation_of``)."""
    out: list[Finding] = []
    for t in tokens:
        if t.kind == "L":
            if t.ids and authorities is not None and citation_of(authorities, t.ids[0]) is None:
                out.append(
                    Finding("unknown_authority", f"{t.ids[0]} is not in the authority store", t.start, t.end, t.raw)
                )
            continue
        for fid in t.ids:
            f = ledger.get(fid)
            if f is None:
                out.append(Finding("unknown_id", f"{fid} is not in the fact index", t.start, t.end, t.raw))
                continue
            if not f.usable:
                out.append(
                    Finding("rejected_id", f"{fid} was rejected: its excerpt is not on the page", t.start, t.end, t.raw)
                )
                continue
            allowed = ALLOWED.get(t.kind, ())
            if f.kind not in allowed:
                hint = " (an unconfirmed fact takes {{A:id}})" if f.kind == "inference" else ""
                out.append(
                    Finding("kind_mismatch", f"{t.kind} cannot name {fid}, a {f.kind}{hint}", t.start, t.end, t.raw)
                )
            elif t.kind in ("N", "SUM") and f.unit == "":
                out.append(Finding("kind_mismatch", f"{fid} carries no parsed figure", t.start, t.end, t.raw))
        if t.kind == "SUM":
            units = {ledger.get(i).unit for i in t.ids if ledger.get(i) is not None}  # type: ignore[union-attr]
            roles = {ledger.get(i).role for i in t.ids if ledger.get(i) is not None}  # type: ignore[union-attr]
            if len(units) > 1:
                out.append(Finding("kind_mismatch", "SUM adds figures in different units", t.start, t.end, t.raw))
            if len(roles) > 1:
                out.append(
                    Finding(
                        "role_mixed",
                        f"SUM adds figures of different roles ({', '.join(sorted(str(r) for r in roles))})",
                        t.start,
                        t.end,
                        t.raw,
                    )
                )
            if len(set(t.ids)) != len(t.ids):
                out.append(Finding("duplicate_id", "SUM names an id twice", t.start, t.end, t.raw))
        if t.kind == "COUNT" and len(set(t.ids)) != len(t.ids):
            out.append(Finding("duplicate_id", "COUNT names an id twice", t.start, t.end, t.raw))
    return out


def citation_of(authorities: Any, slug: str) -> str | None:
    """A store's citation for ``slug``: a mapping of slug to citation, an
    object with ``citation(slug)`` (``verify.authorities.Store``), or a
    callable. None when the store does not hold it."""
    if authorities is None:
        return None
    if isinstance(authorities, Mapping):
        got = authorities.get(slug)
    elif hasattr(authorities, "citation"):
        got = authorities.citation(slug)
    else:
        got = authorities(slug)
    return str(got) if got else None


def strip_tokens(text: str) -> str:
    """The markup with every token and passthrough marker blanked to spaces of
    the same length, so offsets still index the original text."""
    chars = list(text)
    spans = passthrough_spans(text) + [(m.start(), m.end()) for m in _TOKEN.finditer(text)]
    for a, b in spans:
        for i in range(a, b):
            if chars[i] != "\n":
                chars[i] = " "
    return "".join(chars)


def ids_used(text: str) -> list[str]:
    tokens, _ = parse(text)
    seen: list[str] = []
    for t in tokens:
        for i in t.ids:
            if i not in seen:
                seen.append(i)
    return seen
