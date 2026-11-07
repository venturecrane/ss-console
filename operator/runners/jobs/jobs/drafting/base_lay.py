"""Laying a document out: in the firm's own Word file when its class has a base, else
on ours with the config's layout. Each class that can have a base names its own slot
plan here."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import brief_base, firm_rules, memo_base
from .brief_base import unfit

#: class: (its fill, the format its check reads). The brief's is the default.
PLANS = {
    "mediation_brief": (brief_base.fill, brief_base.checked_format),
    "memo": (memo_base.fill, memo_base.checked_format),
}


def lay_out(
    doc: str,
    dest: Path,
    cls: str,
    fmt: dict[str, Any],
    paper: str,
    held: tuple[Path, dict[str, str]] | None,
    rules: dict[str, Any] | None = None,
    styles: bytes | None = None,
) -> tuple[Path, dict[str, Any], list[str], dict[str, Any]]:
    """The Word file: in the firm's own file when it has a base (``held``), else on
    ours with the config's layout. Returns (path, report, what was laid, the
    format the check reads). ``rules``: the firm's installed layout rules
    (``firm_rules``), already in ``fmt``; on a base they are edited into the
    rendered file in place, proven to touch only their own leaves. ``styles``: the
    accepted styles file the renderer opens in place of ours (``library_pick``)."""
    if held is not None:
        make, checked = PLANS[cls]
        base = brief_base.load_base(*held, cls)
        path, report = brief_base.render_into(doc, dest, base, paper, make)
        lost = firm_rules.onto_base(path, dict(rules or {}))
        if lost:
            path.unlink(missing_ok=True)
            raise unfit("a firm layout rule could not be laid onto the base: " + "; ".join(lost[:3]))
        if rules:
            report["notes"].append("the firm's layout rules laid onto the base: " + ", ".join(sorted(rules)))
        return path, report, [], checked(fmt, cls, base)
    from . import pleading, render

    path, report = render.render(doc, cls, dest, fmt, styles)
    return path, report, pleading.apply_for(path, fmt, cls) if paper == "pleading" else [], fmt
