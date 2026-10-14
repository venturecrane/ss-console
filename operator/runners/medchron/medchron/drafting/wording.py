"""What never reaches an attorney, checked in code: an unfilled slot, the
system's own vocabulary, a hedge where the record states a fact.

WHY (2026-10-13). A proof run of three propounded sets was held by its own
pre-delivery review with, among its findings: ``{{ATTORNEY}}`` and
``{{NOT IN RECORD}}`` slots standing in the served papers, the pipeline's
vocabulary in the attorney notes, a caption note that said nothing, and notes
that hedged on whether the requesting attorney was the handling attorney. The
prompts already forbade each of these. A rule a model is asked to keep is a
hope; these are the checks that make it a rule:

* ``slots``: a delivered DOCUMENT carries no unfilled slot. A ``{{FILL}}``
  survives only when it is completed "at service" by the person serving; a
  served paper (``STRICT_CLASSES``) carries no ``{{NOT IN RECORD}}`` (a
  request resting on what the file lacks is not a request); no marker is
  empty or of an unknown kind; no skeleton placeholder (``<NAME>``), guidance
  comment or system sentinel survives. The format check reports each as a
  content finding: one repair pass, then the job fails (``format_check``).
* ``vocabulary``: the system's words (the job, the auditor, the drafter, the
  final pass, the digest, code) in a document or the notes.
* ``hedges``: the notes state what the record shows or say it is not in the
  record; they never hedge (``finish._notes`` asks once more, then fails).
* ``empty_lines``: a notes line that names a thing and says nothing about it.
"""

from __future__ import annotations

import re

#: The served papers this module checks (a propounded set): a NOT IN RECORD
#: slot in one is unfilled, not a marker. Responses and briefs are not checked
#: here yet: no proof run has measured these rules against them.
SERVED_PAPERS = ("discovery_set",)
MARKER = re.compile(r"\{\{\s*([^{}:|]*?)\s*(?::\s*([^{}]*))?\}\}")
KINDS = ("ATTORNEY", "NOT IN RECORD", "CLIENT", "FILL")
#: A skeleton's own placeholder (``<NAME>``, ``<number in words>``), never prose.
PLACEHOLDER = re.compile(r"<\s*(?:[A-Z][A-Z ]{2,}|name|county|number in words|number|date)\s*>")
SENTINEL = re.compile(r"(?m)^\W*===\s*[A-Z][A-Z ]+[^=\n]*===\s*$")
GUIDANCE = re.compile(r"(?i)<!--|\bGUIDANCE\s*(?:\(|:)")
#: The system's own words that can never be a served paper's prose (the
#: format check reads documents for these): an RFP asks for "the audit trail",
#: a wage claim says "this job", a product case names "the pipeline".
DOCUMENT_VOCABULARY = re.compile(
    r"(?i)\b(job id|record digest|drafting system|drafting gate|language model|audit finding|final audit|"
    r"compose stage|repair pass|final pass|=== attorney notes ===)"
)
#: The notes add the words that are the system's in a note about the papers.
#: Phrases, not bare nouns: "on the job" is an injury at work, "the gate" a
#: premises fact, "the model" a vehicle's, "audit trail" a record.
VOCABULARY = re.compile(
    r"(?i)\b(job rule|under the job|job id|pipeline stage|the auditor|final audit|audit finding|audit flagged|"
    r"open items from the audit|the drafter|drafting system|drafting gate|final pass|compose stage|repair pass|"
    r"record digest|the digest|digest line|counted by code|code counts|by code(?!\W*(?:of|section|sec|civ|§|\d))|"
    r"language model|a rule of the system)"
)
#: Hedges on a fact the record states (or does not).
#: Narrow on purpose: a record's own "possibly" or "appears" is quoted, and
#: quotations are skipped; these are the notes' own hedges on what the request
#: or the file settles (who asked, who handles it, what was served).
HEDGE = re.compile(
    r"(?i)\b(on either reading|(?:it is )?unclear whether|may or may not|presumably|we assume|it is assumed|"
    r"if (?:\S+\s+){0,6}is the (?:handling|responsible|requesting) attorney|"
    r"whether (?:\S+\s+){0,8}(?:is|was) the (?:handling|responsible|requesting) attorney|"
    r"(?:may|might) (?:not )?have been served|"
    r"confirm (?:\S+\s+){0,6}(?:is|as) the (?:handling|signing|responsible|requesting) attorney)\b"
)
_QUOTED = re.compile(r"\"[^\"\n]*\"|“[^”\n]*”")
_EMPTY = re.compile(r'^\s*[-*]\s+[^:\n]{1,80}:\s*(?:""|“”|\(\s*\)|none\s*\(\s*\))?\s*\.?\s*$', re.I)


def _at_service(body: str) -> bool:
    return re.search(r"\|\s*at service\s*$", body.strip(), re.I) is not None


def slots(cls: str, text: str) -> list[str]:
    """The unfilled slots a delivered document carries, one line per kind
    (never the slot's own words: a finding can reach SMD's alerts)."""
    out: list[str] = []
    for m in MARKER.finditer(text):
        kind, body = m.group(1).strip().upper(), (m.group(2) or "").strip()
        if kind not in KINDS:
            out.append("slot: a marker of an unknown kind")
        elif kind == "FILL" and not _at_service(body):
            out.append("slot: an unfilled {{FILL}} (only an at-service FILL may stay)")
        elif kind == "NOT IN RECORD" and cls in SERVED_PAPERS:
            out.append("slot: a {{NOT IN RECORD}} marker in a served paper")
        elif kind != "FILL" and not body:
            out.append(f"slot: an empty {kind} marker")
    leftovers = (
        (PLACEHOLDER, "a skeleton placeholder"),
        (SENTINEL, "a system sentinel"),
        (GUIDANCE, "a guidance comment"),
    )
    for rx, what in leftovers:
        m = rx.search(text)
        if m:
            out.append(f"slot: {what}")
    return list(dict.fromkeys(out))


def _unquoted(text: str) -> str:
    return _QUOTED.sub(" ", text)


def vocabulary(text: str, rx: re.Pattern[str] = VOCABULARY) -> list[str]:
    return list(dict.fromkeys(m.group(0) for m in rx.finditer(_unquoted(text))))


def document_vocabulary(text: str) -> list[str]:
    return vocabulary(text, DOCUMENT_VOCABULARY)


def hedges(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0) for m in HEDGE.finditer(_unquoted(text))))


def empty_lines(text: str) -> list[str]:
    """A line that names a thing and says nothing, unless indented lines
    under it carry what it names."""
    lines = text.splitlines()
    out = []
    for i, ln in enumerate(lines):
        if not _EMPTY.match(ln):
            continue
        nxt = next((x for x in lines[i + 1 :] if x.strip()), "")
        if len(nxt) - len(nxt.lstrip()) > len(ln) - len(ln.lstrip()):
            continue
        out.append(ln.strip())
    return out


def notes_findings(text: str) -> list[str]:
    """Everything the notes may not carry: the system's words, a hedge, a line
    that says nothing, a raw marker."""
    out = [f'the system\'s word "{w}"' for w in vocabulary(text)]
    out += [f'a hedge "{h}"' for h in hedges(text)]
    out += [f"a line that says nothing: {ln[:80]!r}" for ln in empty_lines(text)]
    if "{{" in text or "}}" in text:
        out.append("a raw {{marker}}")
    return out


def summary(text: str) -> str:
    """The notes' problems for a reason line: the system's words themselves
    (never client text), and counts of the rest (a hedge can name a person)."""
    parts = [f"the system's words {', '.join(repr(w) for w in vocabulary(text))}"] if vocabulary(text) else []
    for n, what in ((len(hedges(text)), "hedge(s)"), (len(empty_lines(text)), "empty line(s)")):
        if n:
            parts.append(f"{n} {what}")
    if "{{" in text or "}}" in text:
        parts.append("a raw marker")
    return "; ".join(parts)[:240]
