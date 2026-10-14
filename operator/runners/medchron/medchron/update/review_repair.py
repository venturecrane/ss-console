"""The update's review-and-repair findings: anchored, parsed, grounded, applied.

WHY (2026-10-14). Independent attorney-style reviewers given only the seat's
update output and the page images found 13 and 15 MUST-FIX defects on two real
matters (wrong visit dates the typed statement settles, one-sided findings
reported as both sides, a positive exam reported negative, truncated entries,
duplicates, wrong values) and could have corrected every one from the pages.
The pre-delivery review only HELD; nothing fixed. This module is the $0 half
of the loop that fixes (``stages/update_review.py`` is the paid half):

* the ADDED entries of the audit document get anchors (``N<k>`` per entry,
  ``N<k>.c<j>`` per claim, the audit's own claim spans);
* the reviewer's reply is ONE JSON object, parsed field by field, never cast;
  an item that does not parse is kept as an open finding, never dropped;
* a correction applies only when it is GROUNDED: a replaced claim ends with a
  citation to the very exhibit and page the finding names, is long enough for
  the audit to audit it, and stays one paragraph; a redate names a page the
  entry already cites AND that page's text states the corrected date
  (``Evidence``: a model never rewrites a date on its reading alone), and the
  redated entry moves to its place in date order; an entry drop never leaves
  a new exhibit uncited;
* an ``add`` puts ONE grounded paragraph into the entry (under the heading
  it names, when the entry has it or the heading is a known one), citing a
  page the review was SHOWN as an uncited page of the entry's own document:
  what the document records and the entry omits;
* a finding about a DELIVERED entry (``D<k>``) is reported, never applied: the
  update never edits the firm's delivered text;
* diagnosis and provider claims (the preamble grammars of ``update/icd.py``
  and ``update/providers.py``) are never edited, except that a redate carries
  the date of a diagnosis claim the redated entry recorded, in its grammar.

Each corrected sentence is a new claim (a new audit key); the stage re-runs
the audit loop, which audits it against the page.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Callable

from ..audit import claims as CL
from . import body as B, handwritten as H, icd
from .values import lost, numbers

MUST, SHOULD = "MUST", "SHOULD"
ACTIONS = ("replace", "drop", "redate", "drop_entry", "add", "none")
MIN_CLAIM_CHARS = 30  # shorter claims are never audited (``claims._walk``)
_MDY = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
_ANCHOR = re.compile(r"^([ND])(\d+)(?:\.c(\d+))?$")
#: (exhibit, page) -> the page's text, or None when there is none to read.
Evidence = Callable[[int, int], str | None]


@dataclass
class Finding:
    severity: str
    klass: str
    anchor: str
    exhibit: int | None
    page: int | None
    problem: str
    action: str
    corrected: str
    reason: str
    #: why it was not applied ("" when applied or not yet tried)
    outcome: str = ""
    #: the values an applied correction took out of the document, named so
    #: proof 10 reads them as named by the review, not silently dropped
    removed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Claim:
    anchor: str
    head: str  # a subsection heading the span opens with, kept on any edit
    text: str  # the claim through its citation, the part a finding edits
    exhibit: int
    pages: list[int]


@dataclass
class Entry:
    anchor: str
    text: str
    claims: list[Claim] = field(default_factory=list)

    def cites(self) -> set[tuple[int, int]]:
        return {(c.exhibit, p) for c in self.claims for p in c.pages}


def _split_head(span: str, headings: set[str]) -> tuple[str, str]:
    first, _, rest = span.partition("\n")
    if rest and first.strip() in headings:
        return first + "\n", rest
    return "", span


def anchor_entries(md: str, keep: set[int], headings: set[str]) -> list[Entry]:
    """Every ADDED entry of the audit document, anchored ``N1``... in order."""
    _, ents = B.entries_of(md)
    out = []
    for i, e in enumerate(ents, 1):
        ent = Entry(f"N{i}", e)
        for j, (c, span) in enumerate(_walk(e, keep), 1):
            head, text = _split_head(span, headings)
            ent.claims.append(Claim(f"N{i}.c{j}", head, text, c["exhibit"], CL.parse_pages(c["page_spec"]) or [1]))
        out.append(ent)
    return out


def _walk(text: str, keep: set[int]) -> list[tuple[dict[str, Any], str]]:
    spans = CL.claim_spans(text, keep)
    return [(c, spans[c["key"]]) for c in CL.extract_claims(text, keep)]


# ---- parse ------------------------------------------------------------------
def _int(v: object) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


def _str(v: object) -> str:
    return v.strip() if isinstance(v, str) else ""


def _severity(v: object) -> str | None:
    s = _str(v).upper().replace("-FIX", "")
    return s if s in (MUST, SHOULD) else None


def _one(item: object) -> Finding:
    """One reply item as a Finding; an item that does not parse is an OPEN
    MUST finding (``outcome`` says why), never a silently dropped one."""
    if not isinstance(item, dict):
        return Finding(MUST, "unparsed", "", None, None, str(item)[:200], "none", "", "", "not an object")
    sev = _severity(item.get("severity"))
    action = _str(item.get("action")).lower()
    f = Finding(
        sev or MUST,
        _str(item.get("class")),
        _str(item.get("anchor")),
        _int(item.get("exhibit")),
        _int(item.get("page")),
        _str(item.get("problem")),
        action if action in ACTIONS else "none",
        _str(item.get("corrected")),
        _str(item.get("reason")),
    )
    if sev is None:
        f.outcome = "severity not MUST or SHOULD; held as MUST"
    elif action not in ACTIONS:
        f.outcome = f"unknown action {action!r}"
    return f


class ReplyUnparsed(ValueError):
    """The reply is not one complete JSON object: the part is reviewed again."""


def parse_reply(text: str) -> list[Finding]:
    """The reviewer's reply: ONE JSON object ``{"findings": [...], "complete":
    true}``. Anything else raises ReplyUnparsed (an incomplete review is never
    read as a clean one)."""
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b < a:
        raise ReplyUnparsed("no JSON object in the reply")
    try:
        obj = json.loads(text[a : b + 1])
    except json.JSONDecodeError as exc:
        raise ReplyUnparsed(f"the reply is not JSON: {exc.msg}") from exc
    if not isinstance(obj, dict) or obj.get("complete") is not True:
        raise ReplyUnparsed("the reply does not say the part was reviewed completely")
    items = obj.get("findings")
    if not isinstance(items, list):
        raise ReplyUnparsed("the reply has no findings list")
    return [_one(x) for x in items]


# ---- ground and apply -----------------------------------------------------------
def _valid_mdy(s: str) -> bool:
    m = _MDY.match(s)
    if not m:
        return False
    try:
        date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return False
    return True


def grounded_text(f: Finding, keep: set[int]) -> str:
    """'' when the corrected claim is grounded, else why not."""
    t = f.corrected
    cites = list(CL.CITE.finditer(t))
    if len(cites) != 1 or t[cites[0].end() :].strip():
        return "the corrected text does not end with exactly one citation"
    n, pages = int(cites[0].group(1)), CL.parse_pages(cites[0].group(2))
    if n != f.exhibit or f.page not in pages:
        return f"the corrected text does not cite the page the finding names (Exhibit {f.exhibit} p. {f.page})"
    if n not in keep:
        return f"Exhibit {n} is not an added exhibit"
    if "\n\n" in t.strip() or len(CL.NTD.sub("", t[: cites[0].start()]).strip()) < MIN_CLAIM_CHARS:
        return "the corrected text is not one auditable paragraph"
    if icd.CLAIM.match(t.strip()):
        return "a diagnosis claim is never rewritten by the review"
    return ""


def _by_anchor(ents: list[Entry]) -> tuple[dict[str, Entry], dict[str, tuple[Entry, Claim]]]:
    return {e.anchor: e for e in ents}, {c.anchor: (e, c) for e in ents for c in e.claims}


def _date_on_page(f: Finding, entry: Entry, evidence: Evidence | None) -> str:
    """'' when the cited page's text states the corrected date, else why not.
    A handwritten ledger row carries no year: its month and day count when
    the year is the entry's own (``handwritten.on_page``)."""
    m = _MDY.match(f.corrected)
    text = evidence(f.exhibit, f.page) if evidence and m and f.exhibit and f.page else None
    if m is None or text is None:
        return f"no page text to check the corrected date against (Exhibit {f.exhibit} p. {f.page})"
    d = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    cur = H.reading_of(entry.text)
    if not H.on_page(d, text, cur.year if cur else None):
        return (
            f"Exhibit {f.exhibit} p. {f.page} does not state {f.corrected}; the date is not changed on a reading alone"
        )
    return ""


def _split_add(t: str, headings: set[str]) -> tuple[str, str]:
    """(the heading an add names, or "", the paragraph)."""
    first, _, rest = t.strip().partition("\n")
    return (first.strip(), rest.strip()) if rest and first.strip() in headings else ("", t.strip())


def _add_ok(f: Finding, e: Entry, keep: set[int], headings: set[str], shown: dict[str, list[tuple[int, int]]]) -> str:
    """'' when an add is grounded: one auditable paragraph citing the page the
    finding names, a page shown to the review as an uncited page of this
    entry's own document."""
    if (f.exhibit, f.page) not in (shown.get(e.anchor) or []):
        return f"Exhibit {f.exhibit} p. {f.page} is not an uncited page of the entry's document shown to the review"
    _, para = _split_add(f.corrected, headings)
    return grounded_text(Finding(**{**f.as_dict(), "corrected": para}), keep)


def _add(text: str, corrected: str, headings: set[str]) -> str:
    """The entry with the added paragraph at the end of the subsection its
    heading names (or at the entry's end, under that heading when given)."""
    head, para = _split_add(corrected, headings)
    blocks = text.split("\n\n")
    at = [i for i, b in enumerate(blocks) if head and b.split("\n", 1)[0].strip() == head]
    if at:
        j = at[0] + 1
        while j < len(blocks) and blocks[j].split("\n", 1)[0].strip() not in headings:
            j += 1
        blocks[j - 1] += "\n" + para
        return "\n\n".join(blocks)
    return text.rstrip("\n") + "\n\n" + (f"{head}\n{para}" if head else para)


def check(
    f: Finding,
    ents: list[Entry],
    keep: set[int],
    evidence: Evidence | None = None,
    headings: set[str] | None = None,
    shown: dict[str, list[tuple[int, int]]] | None = None,
) -> str:
    """'' when the finding may be applied, else why it may not."""
    m = _ANCHOR.match(f.anchor)
    if f.outcome:
        return f.outcome
    if m is None:
        return f"anchor {f.anchor!r} is not an entry or claim of this part"
    if m.group(1) == "D":
        return "carried: a delivered entry is never edited by an update"
    if f.action == "none":
        return "no action given"
    by_e, by_c = _by_anchor(ents)
    want_claim = f.action in ("replace", "drop")  # an add names its entry
    if want_claim and f.anchor not in by_c:
        return f"{f.anchor} is not a claim of an added entry"
    if not want_claim and f.anchor not in by_e:
        return f"{f.anchor} is not an added entry"
    if f.exhibit is None or f.page is None:
        return "the finding names no exhibit and page"
    if f.action == "replace":
        return grounded_text(f, keep)
    if f.action == "add":
        return _add_ok(f, by_e[f.anchor], keep, headings or set(), shown or {})
    if f.action == "redate":
        if not _valid_mdy(f.corrected):
            return "the corrected date is not MM/DD/YYYY"
        if (f.exhibit, f.page) not in by_e[f.anchor].cites():
            return "the redate is grounded on a page the entry does not cite"
        return _date_on_page(f, by_e[f.anchor], evidence)
    if not f.reason:
        return "a drop gives no reason"
    return ""


def _drop_claim(text: str, c: Claim, headings: set[str]) -> str:
    """The claim out of its entry. Its subsection heading goes with it unless
    a paragraph of the same subsection follows (then the heading stays)."""
    span = c.head + c.text
    if not c.head or text.count(span) != 1:
        return text.replace(c.text, "", 1)
    after = text[text.index(span) + len(span) :].lstrip("\n")
    nxt = after.split("\n", 1)[0].strip()
    keeps = bool(nxt) and nxt not in headings and not B.DATED.match(nxt[:10])
    return text.replace(span, c.head.rstrip("\n") if keeps else "", 1)


def _tidy(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text).strip("\n")


def _redate_diag(pre: str, old: str, new: str, cites: set[tuple[int, int]]) -> str:
    """A diagnosis claim the redated entry recorded (same date, a page the
    entry cites) carries the new date, in its grammar."""
    out = []
    for para in re.split(r"(\n\s*\n)", pre):
        m = icd.CLAIM.match(para.strip())
        ref = CL.CITE.search(f"({m.group(5)})") if m else None
        if m and ref and f"{m.group(2)}/{m.group(3)}/{m.group(4)}" == old:
            ex, pages = int(ref.group(1)), CL.parse_pages(ref.group(2)) or [1]
            if any((ex, p) in cites for p in pages):
                para = para.replace(f"recorded on {old}.", f"recorded on {new}.", 1)
        out.append(para)
    return "".join(out)


def apply(
    md: str,
    findings: list[Finding],
    keep: set[int],
    headings: set[str],
    evidence: Evidence | None = None,
    shown: dict[str, list[tuple[int, int]]] | None = None,
) -> tuple[str, set[int]]:
    """Apply every grounded finding to the audit document. MUST findings are
    applied; SHOULD findings only as a ``replace`` or ``redate`` (confined to
    one entry). A redate applies only when the cited page's text states the
    date (`evidence`); without page text it is not applied. An add applies
    only on a page `shown` to the review for that entry. Returns the new
    document and the 0-based indexes, in the new order, of the entries it
    edited (a dropped entry names its neighbours). Each finding's ``outcome``
    is set: "applied" or why not."""
    ents = anchor_entries(md, keep, headings)
    by_e, by_c = _by_anchor(ents)
    texts = {e.anchor: e.text for e in ents}
    dropped: set[str] = set()
    redates: list[tuple[str, str, set[tuple[int, int]]]] = []
    for f in findings:
        why = check(f, ents, keep, evidence, headings, shown)
        if not why and f.severity == SHOULD and f.action not in ("replace", "redate"):
            why = "a SHOULD finding is applied only as a replace or redate; recorded"
        if why:
            f.outcome = why
            continue
        if f.action in ("replace", "drop"):
            e, c = by_c[f.anchor]
            if texts[e.anchor].count(c.text) != 1:
                f.outcome = "the claim is not found once in its entry"
                continue
            if f.action == "replace":
                texts[e.anchor] = texts[e.anchor].replace(c.text, f.corrected, 1)
                f.removed = lost(c.text, f.corrected)
            else:
                texts[e.anchor] = _drop_claim(texts[e.anchor], c, headings)
                f.removed = sorted(numbers(c.text))
        elif f.action == "add":
            texts[f.anchor] = _add(texts[f.anchor], f.corrected, headings)
        elif f.action == "redate":
            # Every value of the entry moves to the corrected date: named, so
            # proof 10 does not read the move as a loss on the old date.
            head = texts[f.anchor][:10]
            texts[f.anchor] = f.corrected + texts[f.anchor][10:]
            redates.append((head, f.corrected, by_e[f.anchor].cites()))
            f.removed = sorted(numbers(texts[f.anchor]))
        else:
            dropped.add(f.anchor)
            f.removed = sorted(numbers(by_e[f.anchor].text))
        f.outcome = "applied"
    for f in [x for x in findings if x.action == "drop_entry" and x.outcome == "applied"]:
        left = {n for a, t in texts.items() if a not in dropped for n in B.cited(t)}
        orphans = sorted(B.cited(by_e[f.anchor].text) - left)
        if orphans:
            dropped.discard(f.anchor)
            f.removed = []
            f.outcome = f"dropping the entry would leave Exhibit {orphans[0]} uncited"
    return _rebuild(md, ents, texts, dropped, redates)


def _rebuild(
    md: str,
    ents: list[Entry],
    texts: dict[str, str],
    dropped: set[str],
    redates: list[tuple[str, str, set[tuple[int, int]]]],
) -> tuple[str, set[int]]:
    """The audit document with its entries replaced in place: the preamble and
    every other section byte for byte, except a redated diagnosis claim."""
    body = B.sections(md)["## Medical Chronology"]
    at = md.index(body, md.index("## Medical Chronology"))
    parts = B.ENTRY.split(body)
    pre = parts[0]
    for old, new, cites in redates:
        pre = _redate_diag(pre, old, new, cites)
    edited, gone, live = set(), set(), []
    for k, e in enumerate(ents):
        if e.anchor in dropped:
            gone.add(k)
            continue
        t = _tidy(texts[e.anchor])
        if t != e.text:
            edited.add(k)
        live.append((k, t))
    for k in sorted(gone):
        edited |= {k - 1, k + 1}
    # A redate moves its entry: the entries stay in date order (stable).
    order = sorted(live, key=lambda kt: B.iso(kt[1]))
    seps = [p[len(p.rstrip("\n")) :] for k, p in enumerate(parts[1:]) if k not in gone]
    out = md[:at] + pre + "".join(t + sep for (_, t), sep in zip(order, seps)) + md[at + len(body) :]
    pos = {k: i for i, (k, _) in enumerate(order)}
    return out, {pos[k] for k in edited if k in pos}


def named_values(record: dict[str, Any]) -> set[str]:
    """Every value the review's applied corrections took out of the document
    (``review_repair.json``): named by the review, as the audit names the
    values it finds unsupported."""
    out: set[str] = set()
    for row in record.get("rounds") or []:
        for f in row.get("findings") or []:
            if isinstance(f, dict) and f.get("outcome") == "applied":
                out |= {str(v) for v in f.get("removed") or [] if isinstance(v, str)}
    return out
