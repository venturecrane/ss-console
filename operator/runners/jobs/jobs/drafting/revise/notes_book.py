"""The changes section of a revision's notes, written from what was done,
never from a model's words: per document, each response changed and the
firm rule (with the date it was stated) or the attorney's own instruction
behind it, and each response left as filed with its reason from a table.

The same text is the job's own notes (``revise-set-notes.md``) and, when the
delivery carries attorney notes, the top of their new version, above the notes
as filed (``book_run``)."""

from __future__ import annotations

from typing import Any

from .. import wording
from ..endings import FIRM_MESSAGES
from ..outcome import DraftingFailed

#: Why a response or a line was left as filed, per reason code. A code the
#: endings table words for the firm (``FIRM_MESSAGES``) is worded from there.
REASONS: dict[str, str] = {
    "ground_not_named": "{item}: the proposed objection ({what}) is left marked for you; no firm rule and nothing "
    "in your request asks for it",
    "no_objection_sentence": "{item}: the proposed objection is left marked for you; it holds no objection "
    "sentence to take as written",
    "index_which_files": "{item}: it now produces {what}; add its number to the index lines of the files that answer it",
    "index_line_kept": "{item}: the index line ({what}) is kept as filed; no request would still produce it",
    "already": "{item}: already reads as the firm's wording; left as filed",
    "conflict": "{item}: left as filed; the firm's wording would contradict it ({what})",
    "out_of_scope": "{item}: left as filed",
}


def left_line(number: str, code: str, what: str) -> str:
    """A response left as filed: the firm-rules stage's own line for a code it
    left (``rulebook.stage.NOTES``, ``what`` its ``rule:<id>``), else ours."""
    from ..rulebook.stage import ASKED_BY, NOTES

    if (what.startswith("rule:") or what in ASKED_BY) and code in NOTES:
        by = ASKED_BY.get(what) or f"by rule {what.removeprefix('rule:')}"
        return NOTES[code].format(item=number, by=by, rule=what.removeprefix("rule:"))
    return reason(code, f"Response {number}", what)


def reason(code: str, item: str, what: str = "") -> str:
    if code in FIRM_MESSAGES and FIRM_MESSAGES[code]:
        line = FIRM_MESSAGES[code].replace("{item}", item)
        return f"{line} ({what})" if what else line
    tpl = REASONS.get(code, "{item}: left as filed ({what})")
    return tpl.replace("{item}", item).replace("{what}", what or code.replace("_", " ")).replace(" ()", "")


def source_label(source: str, rules: dict[str, dict[str, Any]]) -> str:
    """'the firm's rule of 2026-11-20' for ``rule:<id>``; 'your request' for the email."""
    if source in ("rule:email", "email"):  # the stage names an instruction by its source
        return "your request"
    if source in ("rule:letter", "letter"):
        return "the other side's letter"
    if source.startswith("rule:"):
        rid = source.split(":", 1)[1]
        at = str((rules.get(rid) or {}).get("at") or "")[:10]
        return f"the firm's rule {rid}" + (f" of {at}" if at else "")
    if source in ("email", "attorney"):
        return "your request"
    if source == "marker":
        return "the objection proposed in the draft, which the firm's rules ask for"
    return source


def unit_lines(name: str, rec: dict[str, Any], rules: dict[str, dict[str, Any]]) -> list[str]:
    """One document's section: ``rec`` is its render record (``book_run``)."""
    lines = [f"## {name}"]
    if rec.get("kept"):
        return [*lines, rec["kept"], ""]
    changed = rec.get("why_changed") or {}
    if not changed and not rec.get("index_did"):
        lines.append("No response changed.")
    for number, sources in changed.items():
        said = "; ".join(dict.fromkeys(source_label(s, rules) for s in sources))
        lines.append(f"- Response {number}: changed, per {said}.")
    for fact in rec.get("facts") or []:
        lines.append(f'- Response {fact["number"]}: added "{fact["text"]}" (per attorney, from your request).')
    for d in rec.get("index_did") or []:
        lines.append(f"- Index of Exhibits: {d}.")
    lines += [f"- {n}" for n in rec.get("written") or []]
    for number, code, what in rec.get("left") or []:
        lines.append("- " + left_line(str(number), code, str(what)))
    if rec.get("newer_than_delivery"):
        lines.append("Revised on the version as the firm last saved it, not as delivered.")
    return [*lines, ""]


def write(title: str, units: list[tuple[str, dict[str, Any]]], rules: list[dict[str, Any]]) -> str:
    by = {str(r["rule_id"]): r for r in rules}
    lines = [f"# {title}", ""]
    for name, rec in units:
        lines += unit_lines(name, rec, by)
    md = "\n".join(lines).rstrip() + "\n"
    problems = wording.notes_findings(md)
    if problems:
        raise DraftingFailed(
            f"notes_unclean: the revision notes carry {len(problems)} problem(s): " + wording.summary(md)
        )
    return md


def with_prior(changes: str, prior_notes: str) -> str:
    """The attorney notes' new version: the changes, then the notes as filed."""
    if not prior_notes.strip():
        return changes
    return changes.rstrip() + "\n\n## Notes as filed before this revision\n\n" + prior_notes.strip() + "\n"


__all__ = ["REASONS", "reason", "source_label", "with_prior", "write"]
