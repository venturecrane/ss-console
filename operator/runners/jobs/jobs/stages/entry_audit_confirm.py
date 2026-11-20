"""The UPDATE lane's second reader on every finding (``stages/entry_audit.py`` is
the stage; ``entry_audit_update`` the rule). A finding with a proposed edit
has the verify; every other one a confirm-only call: the line as it stands,
the first reader's class and reason, the pages as pictures, and one answer,
wrong or not wrong (``entry_confirm.confirm_request``). Answers are saved
before use as ``<item name>.confirm1.json`` / ``confirm2`` beside the read's,
so a resume pays for none twice and ``ENTRY_AUDIT_REPLAY=1`` answers at $0.
"""

from __future__ import annotations

from typing import Any

from ..medchron.update import entry_confirm as EC, entry_read as ER
from . import entry_audit_apply as AP, entry_audit_io as IO, entry_audit_update as EU


def open_items(reads: list[IO.Read]) -> list[AP.Item]:
    """A finding for each line the read marked a problem with no finding kept
    (``entry_read.Parsed.open_lines``), at the page the line cites first."""
    out: list[AP.Item] = []
    for r in reads:
        for name, unit, p in r.got:
            for j, (label, klass) in enumerate(sorted(p.open_lines.items())):
                pages = EC._line_pages(unit.lines[label], unit) & set(unit.pages) or set(unit.cited or unit.pages)
                ex, pg = min(pages) if pages else (0, 0)
                why = "the first reader marked this line a problem; its finding could not be kept"
                f = ER.ReadFinding(klass, unit.key, unit.anchor, ex, pg, "", "none", "", why, unit.record_only, label)
                out.append((f"{name}.o{j}", unit, f))
    return out


def needs_confirm(unit: ER.Unit, proposal: EC.Proposal | str) -> bool:
    """A finding with no edit to verify, outside a name-claim unit and SMD's own sentences."""
    return isinstance(proposal, str) and proposal not in EU.SETTLED and not unit.anchor.startswith(ER.NAME)


def confirm(
    ask: IO.Asker, name: str, unit: ER.Unit, f: ER.ReadFinding, cfg: Any, client: str, images: Any
) -> bool | None:
    """Is the line wrong: True, False, or None when no picture could be shown,
    the API refused twice, or no answer parsed. Raises ``IO.Pending`` under
    the replay switch; a network failure is raised (the stage exits 75)."""
    system, content, shown = EC.confirm_request(f, unit, cfg, client, images)
    if not shown:
        return None

    def ok(t: str) -> bool:
        return EC.parse_confirm(t) is not None

    for attempt in (1, 2):
        text = ask.peek(name, f"confirm{attempt}", system, content, ok)
        if text is not None and ok(text):
            return EC.parse_confirm(text)
    for attempt in (1, 2):
        try:
            text, _stop = ask.ask(name, f"confirm{attempt}", system, content, ok, max_tokens=IO.VERIFY_MAX_TOKENS)
        except IO.ModelError:
            continue
        got = EC.parse_confirm(text)
        if got is not None:
            return got
    return None


def wrong(unit: ER.Unit, proposal: EC.Proposal | str, second: Any) -> Any:
    """The second reader's answer for one finding, as ``entry_audit_update.verdict`` reads it."""
    if isinstance(proposal, EC.Proposal):
        return second.original_wrong if isinstance(second, EC.Verify) else None
    if not needs_confirm(unit, proposal):
        return EU.SKIP
    return second if isinstance(second, bool) else None


def smd_settled(sr: Any, items: list[AP.Item], proposals: list[EC.Proposal | str]) -> list[EC.Proposal | str]:
    """Each finding on a line SMD kept as written (``medchron/update_settle``)
    recorded ``smd_settled``: no edit, no second reader, no hold."""
    from ..medchron import update_settle as US

    kept = US.settled(sr.slug_dir, sr.unit.unit)
    if not kept:
        return proposals
    out = [
        EU.SMD_SETTLED if US.line_sha(u.lines.get(f.line, "")) in kept else p for (_n, u, f), p in zip(items, proposals)
    ]
    n = sum(1 for p in out if p == EU.SMD_SETTLED)
    if n:
        sr.log(f"entry read: {n} finding(s) on line(s) SMD kept as written; recorded, never held or edited")
    return out


def spellings(items: list[AP.Item], delivered: dict[str, list[str]]) -> dict[str, str]:
    """{name claim sha: delivered spelling} for each name-claim finding that spells it as delivered."""
    out: dict[str, str] = {}
    for _name, unit, f in items:
        got = EU.spelling(unit, f, delivered) if unit.anchor.startswith(ER.NAME) else None
        if got is not None:
            out[got[0]] = got[1]
    return out
