"""Deterministic checks over a rendered form interrogatory response body.

Run before filing, on the body as it will be filed (the markdown or plain text
of ``render_body``, or a body read back from the Word file), against the
checked list, the validated answers and the firm's policy. Each failure is a
``Finding`` naming the number:

* ``unchecked``: a number that was not checked appears;
* ``missing``: a checked number is not answered;
* ``order``: the numbers are out of the form's order, or one appears twice;
* ``no_response``: a restated interrogatory has no response label of its own;
* ``restated_text``: the restated text is not the official text (after
  whitespace normalisation);
* ``restated_layout``: the stem and each lettered subpart are not each on a
  line of their own;
* ``fixed_sentence``: a none / no information / not applicable response is not
  the firm's sentence for it;
* ``item_count`` / ``item_name``: the item blocks are not the answers' items;
* ``subparts``: an item block does not carry every lettered subpart, each on
  its own line, in the form's order;
* ``subpart_inline``: a subpart label sits inside another line;
* ``uncited_value``: an answered value is not a cited value in the answers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .answers import Answer, Item
from .catalog import Catalogue, load, ws
from .render_body import FIXED_KINDS, LABEL, RESPONSE_LABEL, FormPolicy

_SUB = re.compile(r"^\(([a-z])\)\s+(.*)$")
_INLINE = re.compile(r"\s\(([a-z])\)\s")


@dataclass(frozen=True)
class Finding:
    number: str
    code: str
    detail: str


@dataclass
class _Entry:
    number: str
    restated: list[str] = field(default_factory=list)
    response: list[str] | None = None


def _label_re(template: str) -> re.Pattern[str]:
    return re.compile("^" + re.escape(template).replace(re.escape("{n}"), r"(\d{1,3}\.\d{1,2})") + "$")


def _lines(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("**") and s.endswith("**") and len(s) > 4:
            s = s[2:-2].strip()
        if s:
            out.append(s)
    return out


def parse(text: str, policy: FormPolicy) -> tuple[list[_Entry], list[Finding]]:
    return _parse(text, policy.label, policy.response_label)


def _parse(text: str, label_t: str, response_t: str) -> tuple[list[_Entry], list[Finding]]:
    label, response = _label_re(label_t), _label_re(response_t)
    entries: list[_Entry] = []
    findings: list[Finding] = []
    for ln in _lines(text):
        r, q = response.match(ln), label.match(ln)
        if r:
            if not entries or entries[-1].number != r.group(1) or entries[-1].response is not None:
                findings.append(
                    Finding(r.group(1), "no_response", "a response label without its own restated interrogatory")
                )
                continue
            entries[-1].response = []
        elif q:
            entries.append(_Entry(q.group(1)))
        elif entries:
            target = entries[-1].response if entries[-1].response is not None else entries[-1].restated
            target.append(ln)
    return entries, findings


def _numbers(entries: list[_Entry], checked: list[str], cat: Catalogue) -> list[Finding]:
    got = [e.number for e in entries]
    out = [Finding(n, "unchecked", "appears but was not checked") for n in got if n not in checked]
    out += [Finding(n, "missing", "checked but not answered") for n in checked if n not in got]
    seen = [n for n in got if n in checked and cat.has(n)]
    dupes = sorted({n for n in seen if seen.count(n) > 1}, key=cat.position)
    out += [Finding(n, "order", "appears more than once") for n in dupes]
    if not dupes and seen != sorted(seen, key=cat.position):
        first = next(a for a, b in zip(seen, sorted(seen, key=cat.position)) if a != b)
        out.append(Finding(first, "order", "the numbers are not in the form's order"))
    return out


def _restated(e: _Entry, cat: Catalogue) -> list[Finding]:
    it = cat.get(e.number)
    out: list[Finding] = []
    if ws(" ".join(e.restated)) != it.text:
        out.append(Finding(e.number, "restated_text", "the restated text is not the official text"))
    want = [it.stem, *(f"({k}) {t}" for k, t in it.subparts)]
    if [ws(x) for x in e.restated] != want and not out:
        out.append(Finding(e.number, "restated_layout", "the stem and each subpart are not each on their own line"))
    return out


def _blocks(lines: list[str]) -> list[tuple[str | None, list[str]]]:
    """Response lines of a lettered interrogatory into (item name or None,
    its subpart lines): a line that is not a subpart starts a named item."""
    blocks: list[tuple[str | None, list[str]]] = []
    for ln in lines:
        if not _SUB.match(ln):
            blocks.append((ln, []))
        elif not blocks:
            blocks.append((None, [ln]))
        else:
            blocks[-1][1].append(ln)
    return blocks


def _value_set(item: Item) -> dict[str, tuple[str, int]]:
    return {k: (v.text, len(v.cites)) for k, v in item.parts.items()}


def _answered(e: _Entry, a: Answer, cat: Catalogue, policy: FormPolicy) -> list[Finding]:
    n, letters = e.number, cat.get(e.number).letters
    lines = e.response or []
    out = [
        Finding(n, "subpart_inline", f"a subpart label inside a line: {ln[:60]!r}")
        for ln in lines
        if _INLINE.search(ln) and letters
    ]
    blocks = _blocks(lines) if letters else _plain_blocks(lines, a)
    if len(blocks) != len(a.items):
        return out + [Finding(n, "item_count", f"{len(blocks)} item blocks for {len(a.items)} items")]
    no_info = policy.sentence(n, "no_information")
    for (name, body), item in zip(blocks, a.items):
        if (name or None) != item.name:
            out.append(Finding(n, "item_name", f"item {name!r} is not {item.name!r}"))
        values = _value_set(item)
        if not letters:
            text = body[0] if body else ""
            if len(body) != 1 or values.get("", ("", 0))[0] != text or values[""][1] < 1:
                out.append(Finding(n, "uncited_value", f"{text[:60]!r} is not a cited value in the answers"))
            continue
        subs = [_SUB.match(x) for x in body]
        if any(m is None for m in subs) or [m.group(1) for m in subs if m] != list(letters):
            out.append(
                Finding(n, "subparts", f"item {item.name!r} does not carry {list(letters)} each on its own line")
            )
            continue
        for m in subs:
            if m is None:
                continue
            k, text = m.group(1), m.group(2)
            if k in values:
                if values[k][0] != text or values[k][1] < 1:
                    out.append(Finding(n, "uncited_value", f"({k}) {text[:60]!r} is not a cited value in the answers"))
            elif text != no_info:
                out.append(
                    Finding(
                        n, "uncited_value", f"({k}) has no value in the answers but is not the no-information sentence"
                    )
                )
    return out


def _plain_blocks(lines: list[str], a: Answer) -> list[tuple[str | None, list[str]]]:
    """For an interrogatory without subparts: [name,] value per item, as the answers name them."""
    out: list[tuple[str | None, list[str]]] = []
    i = 0
    for item in a.items:
        if item.name:
            out.append((lines[i] if i < len(lines) else None, lines[i + 1 : i + 2]))
            i += 2
        else:
            out.append((None, lines[i : i + 1]))
            i += 1
    if i < len(lines):
        out.append((None, lines[i:]))
    return out


def _item_layout(e: _Entry, cat: Catalogue) -> list[Finding]:
    """Without the answers record: a lettered number's response is either one
    line (a fixed sentence) or item blocks, each carrying every lettered
    subpart on its own line in the form's order."""
    n, letters, lines = e.number, cat.get(e.number).letters, e.response or []
    if not letters or len(lines) <= 1:
        return []
    out = [
        Finding(n, "subpart_inline", f"a subpart label inside a line: {ln[:60]!r}")
        for ln in lines
        if _INLINE.search(ln)
    ]
    for name, body in _blocks(lines):
        got = [m.group(1) for m in (_SUB.match(x) for x in body) if m]
        if got != list(letters):
            out.append(Finding(n, "subparts", f"item {name!r} does not carry {list(letters)} each on its own line"))
    return out


def structure(
    text: str,
    checked: list[str] | tuple[str, ...],
    cat: Catalogue | None = None,
    label: str = LABEL,
    response_label: str = RESPONSE_LABEL,
) -> list[Finding]:
    """The checks a body passes before filing that need no answers record (the
    review's form check, ``review.response_findings``): exactly the checked
    numbers in the form's order and none unchecked, each restated as the
    official text with its stem and subparts on lines of their own, each with
    its own response, and every lettered subpart present per item."""
    cat = cat or load()
    entries, out = _parse(text, label, response_label)
    out += _numbers(entries, list(checked), cat)
    for e in entries:
        if e.number not in checked or not cat.has(e.number):
            continue
        out += _restated(e, cat)
        if e.response is None:
            out.append(Finding(e.number, "no_response", "no response label follows the restated interrogatory"))
        else:
            out += _item_layout(e, cat)
    return out


def check(
    text: str,
    checked: list[str] | tuple[str, ...],
    answers: list[Answer] | tuple[Answer, ...],
    policy: FormPolicy,
    cat: Catalogue | None = None,
) -> list[Finding]:
    cat = cat or load()
    entries, out = parse(text, policy)
    out += _numbers(entries, list(checked), cat)
    by = {a.number: a for a in answers}
    for e in entries:
        if e.number not in checked or not cat.has(e.number):
            continue
        out += _restated(e, cat)
        if e.response is None:
            out.append(Finding(e.number, "no_response", "no response label follows the restated interrogatory"))
            continue
        a = by.get(e.number)
        if a is None:
            out.append(Finding(e.number, "missing", "no answer in the answers record"))
        elif a.kind in FIXED_KINDS:
            if e.response != [policy.sentence(e.number, a.kind)]:
                out.append(Finding(e.number, "fixed_sentence", f"the response is not the firm's {a.kind} sentence"))
        else:
            out += _answered(e, a, cat, policy)
    return out
