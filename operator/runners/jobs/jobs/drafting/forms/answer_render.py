"""The served paper and the attorney's cite table, from verified answers, in code.

``body`` lays the answers out with ``render_body.render`` (each lettered
subpart on its own line, per item), runs ``checks.check`` over that body
before any marker is added, then appends the firm's client-confirm marker
(``{{CLIENT: ...}}``, the lane's marker: highlighted in the Word file and
listed in the attorney notes, ``outcome.MARKERS``) to:

* every no-information value (a whole number, or a subpart with no value);
* every value and sentence of a number the firm's policy always confirms;
* every answered value whose only support is a single page.

Model text can never carry a marker (``answers`` refuses ``{{`` in a value).
``paper`` wraps the body in the firm's form-response shell, caption filled by
code (``caption_stage.fill_skeleton``), with the firm's preamble and closing.
Page cites never reach the served paper: ``cite_table`` is the attorney's.
"""

from __future__ import annotations

import re
from typing import Any

from .answers import Answer, Value
from .catalog import Catalogue
from .checks import Finding, check
from .policy import BODY_SLOT, ResponsePolicy
from .render_body import Block, FormPolicy, render, to_markdown

_COMMENT = re.compile(r"<!--.*?-->", re.S)


class BodyCheckFailed(ValueError):
    def __init__(self, findings: list[Finding]) -> None:
        super().__init__("; ".join(f"{f.number} {f.code}: {f.detail}" for f in findings)[:400])
        self.findings = findings


def _slots(a: Answer, cat: Catalogue) -> list[Value | None]:
    """The value behind each value/fixed line of an answered number, in render order."""
    letters = cat.get(a.number).letters
    return [it.parts.get(k) for it in a.items for k in (letters or ("",))]


#: A value cited to the attorney's own request (a revise, ``revise_stage``): his statement, not the file's.
PER_ATTORNEY = "ATTORNEY"


def _single_page(v: Value) -> bool:
    if all(c.file == PER_ATTORNEY for c in v.cites):
        return False  # the attorney stated it: the notes say "per attorney", no client marker
    return len({(c.file, c.page) for c in v.cites}) == 1


def marked(blocks: list[Block], answers: tuple[Answer, ...], policy: ResponsePolicy, cat: Catalogue) -> list[Block]:
    by = {a.number: a for a in answers}
    pending: dict[str, list[Value | None]] = {n: _slots(a, cat) for n, a in by.items() if a.kind == "answered"}
    out: list[Block] = []
    for b in blocks:
        a = by.get(b.number)
        mark = False
        if a is not None and b.role in ("value", "fixed"):
            if a.kind == "answered":
                v = pending[b.number].pop(0)
                mark = v is None or _single_page(v)
            else:
                mark = a.kind == "no_information"
            mark = mark or b.number in policy.client_confirm
        out.append(Block(b.role, f"{b.text} {policy.marker}", b.number, b.letter) if mark else b)
    return out


def body(
    answers: tuple[Answer, ...], checked: tuple[str, ...], policy: ResponsePolicy, form: FormPolicy, cat: Catalogue
) -> str:
    """The response body as markdown, checked, then marked."""
    blocks = render(checked, answers, form, cat)
    findings = check(to_markdown(blocks), checked, answers, form, cat)
    if findings:
        raise BodyCheckFailed(findings)
    return to_markdown(marked(blocks, answers, policy, cat))


def paper(shell: str, cap: dict[str, Any] | None, policy: ResponsePolicy, body_md: str) -> str:
    """The firm's shell, caption filled by code, its one body slot replaced by
    the preamble, the body and the closing."""
    from ..caption_stage import fill_skeleton

    filled = _COMMENT.sub("", fill_skeleton(shell, cap))
    if filled.count(BODY_SLOT) != 1:
        raise ValueError(f"the form-response shell must carry {BODY_SLOT} exactly once")
    middle = "\n\n".join([*policy.preamble, body_md.strip(), *policy.closing])
    return filled.replace(BODY_SLOT, middle).rstrip() + "\n"


def cite_table(label: str, cites: tuple[tuple[str, str, str, int], ...]) -> str:
    """The attorney's table of where each answer came from (never in the served paper)."""
    if not cites:
        return ""
    rows = [
        f"| {n} | {('(' + k + ')') if k else ''} | "
        + ("per attorney | |" if f == PER_ATTORNEY else f"{f.replace('|', '/')} | {p} |")
        for n, k, f, p in cites
    ]
    return "\n".join(
        [
            f"### Where each answer comes from: {label}",
            "",
            "| Interrogatory | Subpart | File | Page |",
            "|---|---|---|---|",
            *rows,
        ]
    )
