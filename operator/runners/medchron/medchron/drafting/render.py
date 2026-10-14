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
  same responding party in the digest's DISCOVERY-SETS index that a SERVED copy
  in the file bears out (``proof``). The declaration swears under penalty of
  perjury that the count is exceeded, so only a PROVEN count over 35 attaches it:
  a mention (a memo about discovery, the 2026-10-12 incident) never counts, and a
  prior set the file does not bear out, or whose count cannot be read, is named
  for the attorney instead. The proof of service's
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
from typing import Any, Mapping

DISCOVERY = ("discovery_set", "discovery_response")
NOTES_MARK = "=== ATTORNEY NOTES ==="
NEEDS_ATTORNEY = "=== NEEDS THE ATTORNEY ==="
SPECIAL_LIMIT = 35
DECL_LINE = re.compile(r"(?i)declaration for additional discovery|declaration\b.*2030\.050")
POS_LINE = re.compile(r"(?i)^\s*(#{1,6}\s+|\*\*)?\s*proof of service\b")
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
    cite: str = ""
    served: str = ""


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
                served = cells[5] if len(cells) > 5 else ""
                cite = " | ".join(cells[6:]) if len(cells) > 6 else ""
                out.append(PriorSet(cells[0], prop.strip(), resp.strip(), cells[3].casefold(), count, cite, served))
    return out


#: The title a served copy of each set type carries on its own face.
SET_TITLES = {
    "special": re.compile(r"(?i)special(?:ly prepared)?\s+interrogatories"),
    "form": re.compile(r"(?i)form\s+interrogatories"),
    "admission": re.compile(r"(?i)requests?\s+for\s+admissions?"),
    "production": re.compile(r"(?i)requests?\s+for\s+(?:the\s+)?production"),
}
#: How far into a document its own face reaches: the caption, the title and the
#: identification block a served set carries "in the first paragraph
#: immediately below the title of the case" (sections 2030.060(b), 2031.030(b),
#: 2033.060(b)).
FACE_CHARS = 4000
#: The identification block (or a response's own heading) on that face. A memo,
#: a note or an email ABOUT discovery carries the words "special
#: interrogatories" too; it never carries the propounding and responding party
#: lines. On 2026-10-13 a matter memo about a discovery tool read as a possible
#: prior set and left paragraph 4 of a sworn declaration a placeholder.
IDENTIFICATION = re.compile(r"(?i)\b(propounding|responding|asking|answering)\s+party\b")
#: A cited document with less text than this is unread (a scan whose pages
#: did not extract), never "not a set": it cannot be told either way.
MIN_FACE_CHARS = 40
SERVED = re.compile(r"(?i)proof\s+of\s+service|responses?\s+to\s+(?:plaintiff|defendant)\S*\s+.{0,80}?interrogatories")


def _stem(name: str) -> str:
    return re.sub(r"\.(pdf|docx?|msg|txt|rtf|jpe?g|png|tiff?)$", "", name.strip(), flags=re.I).casefold()


def cited_documents(cite: str, corpus: Mapping[str, str]) -> list[str]:
    """The corpus documents a DISCOVERY-SETS line's cite names (by file name or stem)."""
    c = cite.casefold()
    return [n for n in corpus if len(_stem(n)) >= 4 and (_stem(n) in c or n.casefold() in c)]


def kind_of(set_type: str) -> str | None:
    """The SET_TITLES key a DISCOVERY-SETS type names, or None."""
    t = set_type.casefold()
    for key in ("special", "form", "admission", "production"):
        if key in t:
            return key
    return None


def is_the_set(text: str, kind: str) -> bool:
    """The document IS a set of this kind (or the responses to one): its own
    face carries the set's title and the identification block. Never a
    document that only mentions discovery."""
    face = text[:FACE_CHARS]
    return bool(SET_TITLES[kind].search(face) and IDENTIFICATION.search(face))


def proof(s: PriorSet, corpus: Mapping[str, str]) -> tuple[str, str]:
    """How the record bears out a prior set: ``("served", doc)`` when a cited
    document IS that set (its own face: title and identification block) and
    carries a proof of service or is the responding party's responses to it;
    ``("unreadable", why)`` when it is served but its count cannot be read;
    ``("doubtful", why)`` when a cited document is that set but nothing shows
    it was served; ``("mention", why)`` when no cited document is that set at
    all (a memo, a note, an email ABOUT discovery): noise, never a count. A
    doubtful set is never counted and never leaves a sworn count blank: it was
    not shown propounded, and the attorney is told so by name. The 2026-10-12
    and 2026-10-13 incidents: a memo about discovery read as a prior set."""
    kind = kind_of(s.kind)
    if kind is None:
        return "mention", f"{s.title}: not a kind of set this rule reads ({s.kind})"
    cited = cited_documents(s.cite, corpus)
    blank = [d for d in cited if len("".join(corpus[d].split())) < MIN_FACE_CHARS]
    docs = [d for d in cited if is_the_set(corpus[d], kind)]
    if not docs and blank:
        return (
            "unreadable",
            f"{s.title}: {blank[0]} has no readable text, so the file cannot show whether it is the set",
        )
    if not docs:
        return "mention", f"{s.title}: no cited document is the set itself ({s.cite or 'no cite'})"
    served = [d for d in docs if SERVED.search(corpus[d])]
    if not served:
        return "doubtful", f"{s.title}: {docs[0]} is the set but carries no proof of service"
    if s.count is None:
        return "unreadable", f"{s.title}: served ({served[0]}) but its count is not readable"
    return "served", served[0]


@dataclass
class DeclDecision:
    this_set: int
    prior_special: int
    prior_form: int
    unreadable: list[str] = field(default_factory=list)
    #: Prior sets to this party the record does not bear out (attorney's call).
    doubtful: list[str] = field(default_factory=list)
    #: Mentions that are not the set itself (a memo about discovery): not counted.
    ignored: list[str] = field(default_factory=list)
    served: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.this_set + self.prior_special

    @property
    def attach(self) -> bool:
        """Only a PROVEN cumulative count over the limit: the declaration's
        paragraph 3 says so under penalty of perjury."""
        return self.this_set > 0 and self.total > SPECIAL_LIMIT


#: A special interrogatory's own label: the line holds the label and nothing
#: else (a request that begins "Special Interrogatory No. 41 asked..." is not one).
_SPECIAL_LABEL = re.compile(r"(?im)^\W*SPECIAL INTERROGATORY NO\.?\s*(\d+)\W*$")


def special_count(md: str) -> int:
    """The special interrogatories a set itself numbers: its label lines, never
    an interrogatory a request or a definition merely cites ("the response to
    Special Interrogatory No. 41" in a set of admissions counts nothing)."""
    from .sets import LABELS, kinds_in

    doc, _ = split_notes(md)
    alone = set(_SPECIAL_LABEL.findall(doc))
    if kinds_in(doc) != ["special_interrogatories"]:
        return len(alone)
    # A set of interrogatories alone: a label that carries its text on the same
    # line still counts, so an over-35 set is never served without its page.
    return len(alone | {re.findall(r"\d+", x)[-1] for x in LABELS["special_interrogatories"].findall(doc)})


def decl_decision(md: str, digest: str, corpus: Mapping[str, str] | None = None) -> DeclDecision:
    """The cumulative count over SERVED prior sets only, the same rule render,
    format_check and the pre-delivery review apply. ``corpus`` is the matter's
    document texts by name; without it no prior set can be proven."""
    doc, _ = split_notes(md)
    n = special_count(doc)
    resp, prop = party(doc, "RESPONDING PARTY"), party(doc, "PROPOUNDING PARTY")
    d = DeclDecision(this_set=n, prior_special=0, prior_form=0)
    for s in prior_sets(digest):
        if "interrogator" not in s.kind:
            continue
        if resp is None or prop is None:
            d.unreadable.append(f"{s.title} (this set's propounding or responding party is not stated)")
            continue
        if not _same(s.responding, resp) or not _same(s.propounding, prop):
            continue
        how, what = proof(s, corpus or {})
        if how == "mention":
            d.ignored.append(what)
        elif how == "doubtful":
            d.doubtful.append(what)
        elif how == "unreadable":
            d.unreadable.append(s.title)
        elif "special" in s.kind:
            d.prior_special += int(s.count or 0)
            d.served.append(f"{s.title} ({s.count}, {what})")
        else:
            d.prior_form += int(s.count or 0)
            d.served.append(f"{s.title} ({s.count}, {what})")
    return d


@dataclass
class PriorOfKind:
    """Earlier sets of one kind from this propounding party to this responding
    party, as the file bears them out."""

    served: list[str] = field(default_factory=list)
    doubtful: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    stated: bool = True  # False: the set's own parties are not stated


def prior_of_kind(md: str, kind: str, digest: str, corpus: Mapping[str, str] | None = None) -> PriorOfKind:
    """What the file shows of earlier sets of ``kind`` (a SET_TITLES key) to
    this party: the basis for the set number, stated to the attorney as a
    fact of the record rather than assumed."""
    doc, _ = split_notes(md)
    resp, prop = party(doc, "RESPONDING PARTY"), party(doc, "PROPOUNDING PARTY")
    out = PriorOfKind()
    if resp is None or prop is None:
        out.stated = False
        return out
    for s in prior_sets(digest):
        if kind_of(s.kind) != kind or not _same(s.responding, resp) or not _same(s.propounding, prop):
            continue
        how, what = proof(s, corpus or {})
        if how in ("served", "unreadable"):
            out.served.append(f"{s.title} ({what})")
        elif how == "doubtful":
            out.doubtful.append(what)
        else:
            out.ignored.append(what)
    return out


def declarant_name(block_lines: list[str] | None) -> str | None:
    """The attorney's name from the firm's authored attorney block: its first
    line without the State Bar number or the "Esq." (the declaration is the
    attorney's, and the firm authored whose block this set carries)."""
    if not block_lines:
        return None
    name = re.sub(r"\(\s*SBN[^)]*\)", "", block_lines[0])
    name = re.sub(r",?\s*Esq\.?", "", name).strip(" ,`*")
    return name or None


def _fill_decl(text: str, d: DeclDecision, doc: str = "", declarant: str | None = None) -> str:
    """The declaration's code-filled slots: the counts, the two parties as the
    set's own identification block names them (the set took them from the
    operative pleading), and the declarant when the firm authored the
    attorney block. A party the block does not name stays a FILL."""
    text = COMMENT.sub("", text).strip()
    if declarant:
        text = re.sub(r"`?\{\{ATTORNEY: declarant's full name\}\}`?", lambda _m: declarant, text)
    for role, slot in (("PROPOUNDING PARTY", "propounding"), ("RESPONDING PARTY", "responding")):
        who = party(doc, role)
        if who:
            text = re.sub(rf"`?\{{\{{FILL: {slot} party, as captioned[^}}]*\}}\}}`?", lambda _m, w=who: w, text)
    text = re.sub(r"`?\{\{FILL: number of special interrogatories in this set[^}]*\}\}`?", str(d.this_set), text)
    if d.unreadable:
        why = "the file shows a prior set served, but its count is not readable: " + "; ".join(d.unreadable)
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


def sets_kinds(doc: str) -> list[str]:
    from .sets import kinds_in

    return kinds_in(doc)


_KIND_OF_VARIANT = {
    "special_interrogatories": ("special", "special interrogatories"),
    "requests_for_admission": ("admission", "requests for admission"),
    "requests_for_production": ("production", "requests for production"),
}


def prior_set_notes(doc: str, digest: str, corpus: Mapping[str, str] | None) -> list[str]:
    """The set number's basis, stated as what the file shows: the earlier sets
    of this kind from this party to this party that are served, or that the
    file shows none. Never left for the attorney to infer."""
    kinds = sets_kinds(doc)
    if len(kinds) != 1:
        return []
    key, words = _KIND_OF_VARIANT[kinds[0]]
    p = prior_of_kind(doc, key, digest, corpus)
    resp, prop = party(doc, "RESPONDING PARTY"), party(doc, "PROPOUNDING PARTY")
    if not p.stated:
        return [
            f"set number: the set does not state its propounding and responding party, so earlier {words} could not be matched"
        ]
    out = [
        f"set number: earlier {words} from {prop} to {resp} the file shows served: "
        + ("; ".join(p.served) if p.served else "none")
    ]
    if p.doubtful:
        out.append(
            f"set number: the file also holds {len(p.doubtful)} unserved set(s) of this kind, which are not "
            "earlier sets: " + "; ".join(p.doubtful)
        )
    return out


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


def attach(
    doc: str,
    cls: str,
    firm: Any,
    digest: str,
    corpus: Mapping[str, str] | None = None,
    declarant: str | None = None,
) -> tuple[str, list[str]]:
    """The served attachments, appended by the job in the order the firm serves
    them: the document, then the section 2030.050 declaration (a special
    interrogatory set over the PROVEN cumulative limit), then the proof of
    service (every discovery set and response). Authoring comments are
    stripped; every ``{{FILL: ... | at service}}`` slot stays verbatim for the
    person serving. Appended after the audit and the gate: the firm's fixed
    text is never audited. ``corpus`` (document texts by name) is what proves a
    prior set served (``decl_decision``)."""
    if cls not in DISCOVERY:
        return doc, []
    doc, removed = strip_attachments(doc)
    notes = ["the firm's own declaration and proof of service forms are used"] if removed else []
    parts = [doc.rstrip()]
    if cls == "discovery_set" and special_count(doc) > 0:
        d = decl_decision(doc, digest, corpus)
        if d.attach:
            parts.append(_fill_decl(firm.attachment("decl_2030_050"), d, doc, declarant))
            why = (
                f"{d.total} special interrogatories to this party ({d.this_set} in this set, {d.prior_special} "
                f"before in served sets: {'; '.join(d.served) or 'the file shows none served'})"
            )
            if d.unreadable:
                why += "; a served prior set's count is not readable, so paragraph 4 is left to the attorney"
            notes.append(f"section 2030.050 declaration attached: {why}")
        elif d.this_set:
            notes.append(
                f"section 2030.050: {d.total} special interrogatories to this party ({d.this_set} in this set, "
                f"{d.prior_special} in served prior sets); no declaration, the count does not exceed {SPECIAL_LIMIT}"
            )
        notes += [
            f"section 2030.050: not counted, the file does not show it served (if it was, paragraph 4 changes): {x}"
            for x in d.doubtful
        ]
        notes += [f"section 2030.050: {x}; read as a mention, not a prior set" for x in d.ignored]
    if cls == "discovery_set":
        notes += prior_set_notes(doc, digest, corpus)
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

    md = re.sub(r"`+(\{\{[^{}]*\}\})`+", r"\1", md)  # a slot never carries stray backticks
    blob, report = render_document(md, cls, None, house=house_for(fmt, cls))
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(f".{out.name}.tmp")
    part.write_bytes(blob)
    part.replace(out)
    return out, report.to_dict()
