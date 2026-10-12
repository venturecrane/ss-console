"""The canonical hash of an HTML mail body, shared by the seat and the reconciler.

WHY THIS EXISTS (ss#3030 follow-up). On msgraph the broker hands Graph ONLY
the html part of a report send (``msgraph_ops._message``), so Sent Items holds
HTML, and no stamp hashed html: the reconciler's independent channel check
could verify no templated msgraph send at all. The broker now stamps
``html_body_sha256`` -- this module's hash of the exact html it handed Graph --
on CONFIRM_SEND_DISPATCHED, and the reconciler hashes the Sent Items body with
the SAME function. One implementation, imported by both (the reconciler runs
from an editable install of ``operator/``), so there is no second copy to drift.

WHY IT IS NOT A BYTE HASH. Exchange does not store the html it is given. It
parses it into MAPI and re-serializes it on read, and the Graph docs offer no
representation that preserves the submitted bytes (``$value`` MIME is rebuilt
from the same store, in Windows-1252). Calibrated against the internal
smd-staging mailbox on 2026-10-12 (drafts created through Graph, read back;
fixtures in ``operator/contracts/fixtures/html-canon-calibration.json``),
Exchange:

* wraps the body in ``<html><head><meta http-equiv=...></head><body>`` and
  drops a submitted ``<meta charset>``; inserts ``<tbody>`` into a table;
* lowercases tag and attribute names, quotes every attribute value, sorts
  attributes by name, and adds ``data-outlook-trace`` to ``<img>``;
* rewrites ``style`` attributes (``a:b;c:d;`` -> ``a:b; c:d``, ``color: red ;``
  -> ``color:red``) and reflows ``<style>`` blocks inside ``<!-- -->``;
* drops comments; ``<br/>`` -> ``<br>``;
* decodes character references it can represent (``&#8212;`` -> the dash,
  ``&#39;`` -> ``'``) and re-encodes a literal no-break space as ``&nbsp;``;
* drops whitespace-only text between blocks, strips leading whitespace at the
  start of a block, collapses whitespace runs to one space -- except inside
  ``<pre>``, which it keeps verbatim.

THE CANONICAL FORM undoes exactly that class and nothing more: parse, drop
wrapper/meta/tbody tags (children kept), comments, and ``data-outlook-*``
attributes; lowercase names; sort attributes; normalize style declarations;
decode references (the parser does); collapse ASCII whitespace runs outside
``<pre>`` and strip it at block boundaries. Every tag that survives, every
attribute value (an href included), and every non-whitespace character of text
is in the hash, so a one-character content change changes it -- the falsifier
in ``operator/workspace_broker/tests/test_html_canon.py`` pins that against the
calibration fixtures. ``html_canon_v1`` is versioned in name: a change to the
rules is a new function and a new stamp key, never a silent redefinition of an
old one, because rows on the ledger were stamped by the old rules.
"""

from __future__ import annotations

import hashlib
import html
import re
from html.parser import HTMLParser

#: Tags Exchange adds or rewrites around the content; dropped, children kept.
_WRAPPERS = frozenset({"html", "head", "body", "tbody"})
#: Dropped with nothing kept (void).
_DROPPED_VOID = frozenset({"meta"})
_VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
)
#: Elements at whose boundary Exchange strips adjacent whitespace.
_BLOCK = frozenset(
    {
        "address", "article", "aside", "blockquote", "body", "br", "caption", "col", "colgroup",
        "dd", "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form",
        "h1", "h2", "h3", "h4", "h5", "h6", "head", "header", "hr", "html", "li", "main",
        "meta", "nav", "ol", "p", "pre", "section", "style", "table", "tbody", "td",
        "tfoot", "th", "thead", "title", "tr", "ul",
    }
)  # fmt: skip
_ASCII_WS = re.compile(r"[ \t\r\n\f]+")
_BOUNDARY = object()  # a block edge; never serialized


def _style(value: str) -> str:
    out = []
    for decl in value.split(";"):
        prop, sep, val = decl.partition(":")
        prop, val = prop.strip().lower(), _ASCII_WS.sub(" ", val).strip()
        if prop and sep:
            out.append(f"{prop}:{val}")
    return ";".join(out)


class _Canon(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list = []
        self._pre = 0
        self._style = 0

    def _tag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in _WRAPPERS or tag in _DROPPED_VOID:
            self.tokens.append(_BOUNDARY)
            return
        kept = []
        for name, value in attrs:
            name = name.lower()
            if name.startswith("data-outlook"):
                continue
            value = "" if value is None else value
            kept.append((name, _style(value) if name == "style" else value))
        rendered = "".join(f' {n}="{html.escape(v, quote=True)}"' for n, v in sorted(kept))
        self.tokens.append(_BOUNDARY if tag in _BLOCK else None)
        self.tokens.append(f"<{tag}{rendered}>")
        self.tokens.append(_BOUNDARY if tag in _BLOCK else None)
        if tag == "pre":
            self._pre += 1
        elif tag == "style":
            self._style += 1

    def handle_starttag(self, tag, attrs) -> None:
        self._tag(tag, attrs)

    def handle_startendtag(self, tag, attrs) -> None:
        self._tag(tag, attrs)
        if tag.lower() not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag) -> None:
        tag = tag.lower()
        if tag in _VOID:
            return
        if tag in _WRAPPERS:
            self.tokens.append(_BOUNDARY)
            return
        if tag == "pre" and self._pre:
            self._pre -= 1
        elif tag == "style" and self._style:
            self._style -= 1
        edge = _BOUNDARY if tag in _BLOCK else None
        self.tokens += [edge, f"</{tag}>", edge]

    def handle_data(self, data) -> None:
        if self._style:
            data = re.sub(r"\s+", "", data.replace("<!--", "").replace("-->", ""))
            self.tokens.append(("pre", data))
        elif self._pre:
            self.tokens.append(("pre", data.replace("\r\n", "\n")))
        else:
            self.tokens.append(("text", _ASCII_WS.sub(" ", data)))

    # comments, doctype, processing instructions: dropped (Exchange drops them)
    def handle_comment(self, data) -> None:
        pass

    def handle_decl(self, decl) -> None:
        pass

    def handle_pi(self, data) -> None:
        pass


def html_canon_v1(markup: str) -> str:
    """The canonical text form of ``markup`` (see module docstring)."""
    parser = _Canon()
    parser.feed(markup)
    parser.close()
    tokens = [_BOUNDARY, *[t for t in parser.tokens if t is not None], _BOUNDARY]
    # Merge adjacent text so "a" + "b" and "ab" canonicalize alike.
    merged: list = []
    for token in tokens:
        if isinstance(token, tuple) and merged and isinstance(merged[-1], tuple) and merged[-1][0] == token[0]:
            merged[-1] = (token[0], merged[-1][1] + token[1])
        else:
            merged.append(token)
    out: list[str] = []
    for i, token in enumerate(merged):
        if token is _BOUNDARY:
            continue
        if isinstance(token, tuple):
            kind, text = token
            if kind == "text":
                text = _ASCII_WS.sub(" ", text)
                if merged[i - 1] is _BOUNDARY:
                    text = text.lstrip(" ")
                if merged[i + 1] is _BOUNDARY:
                    text = text.rstrip(" ")
            if text:
                out.append(html.escape(text, quote=False))
            continue
        out.append(token)
    return "".join(out)


def html_body_sha256(markup: str) -> str:
    """sha256 (hex) over utf-8 of :func:`html_canon_v1` -- THE stamp function."""
    return hashlib.sha256(html_canon_v1(markup).encode("utf-8")).hexdigest()


__all__ = ["html_body_sha256", "html_canon_v1"]
