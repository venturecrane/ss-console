"""`render`: the finished markdown into the class's Word file, in code.

Before the renderer runs, deterministic settlements, each listed for the
attorney:

* the split: the model writes the document, then a line exactly
  ``=== ATTORNEY NOTES ===``, then its end tables; only the document is
  rendered, the tables go to the attorney notes file;
* the served attachments (a discovery set or response), appended by the job
  in serving order: the document, then the CCP section 2030.050 declaration,
  then the firm's proof of service. Any declaration or proof of service the
  model wrote is removed first. The declaration is attached when the count is
  CUMULATIVE over 35 (the statute: "propounding or has propounded more than 35
  specially prepared interrogatories to any other party"): this set's labeled
  special interrogatories plus every prior special interrogatory set to the
  same responding party in the digest's DISCOVERY-SETS index. A prior count the
  record cannot read never counts as zero: the declaration is attached and its
  paragraph-4 counts are left to the attorney. The proof of service's
  ``{{FILL: ... | at service}}`` slots stay verbatim for the person serving;
* reserved judgment: in a mediation brief, the case-value section opens with an
  ``{{ATTORNEY}}`` marker, and a paragraph about settlement authority, the
  target figure or the bracket carries no dollar figure. The valuation
  argument is drafted from the record; the numbers that are the attorney's
  call are never the Operator's.

The typography is ``smokeball_connector.docx_format.render_document`` on the
starter base, with the firm's authored house style (``house.py``, from the
``format`` block) passed as its override and enforced in-class.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DISCOVERY = ("discovery_set", "discovery_response")
NOTES_MARK = "=== ATTORNEY NOTES ==="
NEEDS_ATTORNEY = "=== NEEDS THE ATTORNEY ==="
SPECIAL_LIMIT = 35
DECL_LINE = re.compile(r"(?i)declaration for additional discovery|declaration\b.*2030\.050")
POS_LINE = re.compile(r"(?i)^\s*(#{1,6}\s+|\*\*)?\s*proof of service\b")
SPECIAL = re.compile(r"(?i)(?<!response to )special interrogator(?:y|ies)\s+no\.?\s*(\d+)")
RESERVED = re.compile(
    r"(?i)\b(settlement\s+authority|authority\s+to\s+settle|settlement\s+target|target\s+(?:figure|number|amount)|"
    r"bracket|settlement\s+range)\b"
)
FIGURE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:k|m|million|thousand))?", re.I)
MARKER = re.compile(r"\{\{[^{}]*\}\}")
ATTORNEY_MARKER = "{{ATTORNEY: settlement authority, the target figure, and any bracket}}"
COMMENT = re.compile(r"<!--.*?-->", re.S)
PARTY = re.compile(r"(?i)\b{}\s*:\s*\|?\s*`?([^|\n`]+)")


def split_notes(md: str) -> tuple[str, str]:
    doc, _, notes = md.partition(NOTES_MARK)
    return doc.rstrip() + "\n", notes.strip()


def needs_attorney(md: str) -> str | None:
    """The compose's own refusal (the request does not name the variant, the
    responding party, the deponent, or the served set cannot be read): the
    sentence after the sentinel, or None."""
    i = md.find(NEEDS_ATTORNEY)
    if i < 0:
        return None
    return (
        " ".join(MARKER.sub("", md[i + len(NEEDS_ATTORNEY) :]).split())[:400] or "the request is missing what it needs"
    )


def _heading(line: str) -> int:
    m = re.match(r"^(#{1,6}) ", line)
    return len(m.group(1)) if m else 0


# ---- the section 2030.050 declaration ---------------------------------------------------


def _norm(s: str) -> str:
    s = re.sub(r"(?i)\b(defendants?|plaintiffs?|an individual|inc|llc|the)\b", " ", s)
    return " ".join(re.findall(r"[a-z0-9]+", s.lower()))


def _same(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    return bool(a and b) and (a == b or a in b or b in a)


def party(md: str, role: str) -> str | None:
    """The PROPOUNDING / RESPONDING PARTY the set's identification block names;
    None when it is a marker or absent."""
    m = re.search(PARTY.pattern.format(re.escape(role)), md)
    v = m.group(1).strip() if m else ""
    return None if not v or "{{" in v or "}}" in v else v


@dataclass
class PriorSet:
    title: str
    propounding: str
    responding: str
    kind: str
    count: int | None  # None: "not readable"


def prior_sets(digest: str) -> list[PriorSet]:
    """Every line of every DISCOVERY-SETS block in the digest, deduplicated:
    ``<title> | <set> | <prop> -> <resp> | <type> | <count or "not readable"> | <date> | <cite>``."""
    out, seen = [], set()
    for block in re.split(r"(?m)^## DISCOVERY-SETS\s*$", digest)[1:]:
        for ln in block.split("\n## ", 1)[0].splitlines():
            cells = [c.strip() for c in ln.strip().lstrip("-* ").split("|")]
            if len(cells) < 5 or "->" not in cells[2]:
                continue
            prop, _, resp = cells[2].partition("->")
            raw = cells[4].replace(",", "")
            count = int(raw) if raw.isdigit() else None
            key = (cells[0].casefold(), cells[1].casefold(), _norm(prop), _norm(resp), cells[3].casefold())
            if key not in seen:
                seen.add(key)
                out.append(PriorSet(cells[0], prop.strip(), resp.strip(), cells[3].casefold(), count))
    return out


@dataclass
class DeclDecision:
    this_set: int
    prior_special: int
    prior_form: int
    unreadable: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.this_set + self.prior_special

    @property
    def attach(self) -> bool:
        return self.this_set > 0 and (self.total > SPECIAL_LIMIT or bool(self.unreadable))


def decl_decision(md: str, digest: str) -> DeclDecision:
    """The cumulative count, the same rule render and format_check apply."""
    doc, _ = split_notes(md)
    n = len({int(x) for x in SPECIAL.findall(doc)})
    resp, prop = party(doc, "RESPONDING PARTY"), party(doc, "PROPOUNDING PARTY")
    d = DeclDecision(this_set=n, prior_special=0, prior_form=0)
    for s in prior_sets(digest):
        if "interrogator" not in s.kind:
            continue
        if resp is None:  # the set's own responding party is unread: any prior could be to them
            d.unreadable.append(f"{s.title} (this set's responding party is not stated)")
            continue
        if not _same(s.responding, resp) or (prop and not _same(s.propounding, prop)):
            continue
        if s.count is None:
            d.unreadable.append(s.title)
        elif "special" in s.kind:
            d.prior_special += s.count
        else:
            d.prior_form += s.count
    return d


def _fill_decl(text: str, d: DeclDecision, doc: str = "") -> str:
    """The declaration's code-filled slots: the counts, and the two parties as
    the set's own identification block names them (the set took them from the
    operative pleading). A party the block does not name stays a FILL."""
    text = COMMENT.sub("", text).strip()
    for role, slot in (("PROPOUNDING PARTY", "propounding"), ("RESPONDING PARTY", "responding")):
        who = party(doc, role)
        if who:
            text = re.sub(rf"`?\{{\{{FILL: {slot} party, as captioned[^}}]*\}}\}}`?", lambda _m, w=who: w, text)
    text = re.sub(r"`?\{\{FILL: number of special interrogatories in this set[^}]*\}\}`?", str(d.this_set), text)
    if d.unreadable:
        why = "a prior set's count is not readable in the record: " + "; ".join(d.unreadable)
        text = re.sub(
            r"`?\{\{FILL: number of (?:interrogatories previously|those that were not)[^}]*\}\}`?",
            f"{{{{ATTORNEY: {why}}}}}",
            text,
        )
    else:
        text = re.sub(
            r"`?\{\{FILL: number of interrogatories previously[^}]*\}\}`?", str(d.prior_special + d.prior_form), text
        )
        text = re.sub(r"`?\{\{FILL: number of those that were not[^}]*\}\}`?", str(d.prior_special), text)
    return text


def strip_attachments(doc: str) -> tuple[str, bool]:
    """Remove a model-written declaration or proof of service: from the first
    title line of either (a heading or a bold-only line) to the end of the
    document, where the job's own attachments go. The drafter's output ends at
    the signature block (a set) or the verification (responses)."""
    lines = doc.splitlines()
    for i, ln in enumerate(lines):
        titled = _heading(ln) or ln.strip().startswith("**")
        if titled and (DECL_LINE.search(ln) or POS_LINE.search(ln)):
            return "\n".join(lines[:i]).rstrip() + "\n", True
    return doc, False


def attach(doc: str, cls: str, firm: Any, digest: str) -> tuple[str, list[str]]:
    """The served attachments, appended by the job in the order the firm serves
    them: the document, then the section 2030.050 declaration (a special
    interrogatory set over the cumulative limit), then the proof of service
    (every discovery set and response). Authoring comments are stripped; every
    ``{{FILL: ... | at service}}`` slot stays verbatim for the person serving.
    Appended after the audit and the gate: the firm's fixed text is never
    audited."""
    if cls not in DISCOVERY:
        return doc, []
    doc, removed = strip_attachments(doc)
    notes = ["removed a model-written declaration or proof of service; the job attaches the firm's"] if removed else []
    parts = [doc.rstrip()]
    if cls == "discovery_set":
        d = decl_decision(doc, digest)
        if d.attach:
            parts.append(_fill_decl(firm.attachment("decl_2030_050"), d, doc))
            why = (
                f"{d.total} special interrogatories to this party ({d.this_set} in this set, {d.prior_special} before)"
            )
            if d.unreadable:
                why += "; a prior count is not readable, so paragraph 4 is left to the attorney"
            notes.append(f"section 2030.050 declaration attached: {why}")
        elif d.this_set:
            notes.append(
                f"section 2030.050: {d.total} special interrogatories to this party ({d.this_set} in this set); no declaration"
            )
    parts.append(COMMENT.sub("", firm.attachment("pos")).strip())
    notes.append("the firm's proof of service is attached; its at-service slots are for the person serving")
    return "\n\n".join(parts) + "\n", notes


# ---- reserved judgment ------------------------------------------------------------------


def _outside_markers(text: str) -> str:
    return MARKER.sub(" ", text)


def reserve_judgment(md: str, cls: str) -> tuple[str, list[str]]:
    if cls != "mediation_brief":
        return md, []
    paras, notes = md.split("\n\n"), []
    for i, p in enumerate(paras):
        if _heading(p) or not RESERVED.search(_outside_markers(p)):
            continue
        figures = FIGURE.findall(_outside_markers(p))
        if figures:
            pieces, marks = MARKER.split(p), MARKER.findall(p)
            pieces = [FIGURE.sub("{{ATTORNEY: figure reserved for the attorney}}", s) for s in pieces]
            p = "".join(x for pair in zip(pieces, [*marks, ""]) for x in pair)
            notes.append(f"removed {len(figures)} settlement figure(s) the draft stated; the attorney sets them")
        if "{{ATTORNEY" not in p:
            p = p.rstrip() + " " + ATTORNEY_MARKER
        paras[i] = p
    md = "\n\n".join(paras)
    m = re.search(r"(?im)^#\s+.*case value.*$", md)
    if m:
        nxt = re.search(r"(?m)^#\s", md[m.end() :])
        section = md[m.end() : m.end() + nxt.start()] if nxt else md[m.end() :]
        if "{{ATTORNEY" not in section:
            md = md[: m.end()] + "\n\n" + ATTORNEY_MARKER + "\n" + md[m.end() :]
            notes.append("the case-value section's settlement authority, target and bracket marker was restored")
    return md, notes


def render(md: str, cls: str, out: Path, fmt: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    """The class's Word file on the starter base, with the firm's authored
    house style (``fmt``, the ``format`` block) as the renderer's override."""
    from smokeball_connector.docx_format import render_document

    from .house import house_for

    blob, report = render_document(md, cls, None, house=house_for(fmt, cls))
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(f".{out.name}.tmp")
    part.write_bytes(blob)
    part.replace(out)
    return out, report.to_dict()
