"""Render a demand in the firm's CRABTREE layout (``layout: crabtree``).

On 2026-11-10 Craig Ashton sent the firm two letters as "best practice" for
the Operator to learn from; the firm's office manager relayed that every demand
the Operator writes should look like the first, the S. Crabtree letter (a
time-limited policy-limits demand, built on Doug Mead's Word template and
finished by Craig). That letter is this layout:

* the letterhead (first page only; its San Francisco office is one authored
  value, ``sf_office``, written into the reference's ``{sf_office}`` slot), a
  centered date, the bold mail line, an "Attn:" address block;
* the RE block as tab-aligned lines with bold labels: Our Client, Claim Number,
  Date of Loss, Place of Loss, Your Insured, Insured Driver (a row the letter
  has no value for is left out);
* a red banner box: the demand's title, the statute line, and the expiry over
  two lines. Code writes it, never the model: a POLICY-LIMITS title and the
  statute line, or, when the request named a figure (``terms.demand_figure``),
  "$75,000 DEMAND - SEVENTY-FIVE THOUSAND DOLLARS" and no statute line;
* "Dear ...:", the centered PREAMBLE heading and the blue C.C.P. 999.1 box
  (the letter's date and its deadline, both code-written);
* the preamble's lettered sub-sections, then the Roman-numbered sections
  (``## Liability`` -> "I. LIABILITY"), numbered and lettered by the renderer
  so a model cannot misnumber them;
* "Very truly yours," / the firm / a script signature / "<signer>, (SBN n)" /
  the title / "Attorneys for Claimant <client>" / the typist's initials /
  "Enclosures: Exhibits 1-N as indexed";
* the INDEX OF EXHIBITS on its own page as "Exhibit n:" paragraphs (no table);
* the footer from the reference (the settlement-communication line over the
  firm and "Page X of Y").

The demand figure: the letter's DEMAND section writes the token
``[DEMAND FIGURE]`` where the amount goes, and the renderer writes "FULL
AVAILABLE POLICY LIMITS" or the figure ("$75,000.00") in its place. A letter
with no token is refused: the amount is code's, so a letter that wrote its
own could contradict the banner.

The deadline: thirty days after the letter's date, on a business day, computed
here. A different date the drafter wrote for it is replaced everywhere it
appears, and the attorney notes say so.

The mechanism is ``house.py``'s: the reference is the firm's own letter with
every word of client content removed and one prototype of each kind tagged
``PROTO:<kind>``, cloned so the firm's fonts, colors, borders and letterhead
arrive as the firm uses them. Photographs are not part of the format: the
attorney adds them.
"""

from __future__ import annotations

import copy
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import terms
from .house import HouseWriter, RenderError, _keep_next, _qn, _first_rpr, _run, out_name, parse
from .house_numbered import _flags, _force, roman

KINDS = (
    "date",
    "mail",
    "addr",
    "re_first",
    "re_line",
    "banner",
    "salute",
    "lead",
    "box999",
    "subhead",
    "minorhead",
    "section",
    "body",
    "bullet",
    "quote",
    "note",
    "providers",
    "specials",
    "future",
    "valediction",
    "signfirm",
    "signscript",
    "signname",
    "initials",
    "enclosures",
    "exh_head",
    "exh_item",
)
#: Kinds a reference may add; absent, the renderer falls back to the named kind.
OPTIONAL_KINDS = {"studyhead": "minorhead", "pquote": "quote", "blank": ""}
#: A table's last row is its total when its first cell starts with one of these.
TOTAL_LEADS = ("TOTAL", "PROJECTED")
#: Required front matter. ``place`` and ``driver`` are optional rows.
FRONT_KEYS = ("date", "attn", "client", "claim", "dol", "insured", "salutation")
OPTIONAL_FRONT = ("place", "driver")
#: The RE rows as the firm prints them: the label, its colon in a column of
#: its own (so the colons align), then the value.
RE_ROWS = (
    ("Our Client", "client"),
    ("Claim Number", "claim"),
    ("Date of Loss", "dol"),
    ("Place of Loss", "place"),
    ("Your Insured", "insured"),
    ("Insured Driver", "driver"),
)
FIGURE_TOKEN = "[DEMAND FIGURE]"
POLICY_LIMITS = "FULL AVAILABLE POLICY LIMITS"
INDEX = "index of exhibits"
TABLES_HELP = (
    "the format's tables are: Provider | Treatment and Dates of Service | Billed (last row TOTAL); "
    "a two-column specials table (description | amount, last row TOTAL); "
    "Anticipated Future Care | Estimated Cost (last row TOTAL)"
)
_EXHIBIT = re.compile(r"^\W*Exhibit\s+(\d+)\b", re.I)
_EXHIBIT_ITEM = re.compile(r"^\**Exhibit\s+(\d+)\s*:\**\s*(.*)$", re.I)
#: Tables the firm prints with no header row (its special-damages table).
HEADERLESS = ("specials",)


def fold_dashes(text: str) -> str:
    """A heading compared with its authored form: em and en dashes, hyphens
    and spacing are the same mark."""
    return " ".join(re.sub(r"\s*[\u2014\u2013-]\s*", " - ", text).split()).upper()


_LETTERED = re.compile(r"^[A-Z][.)]\s+")


_MARKS = re.compile(r"(\*\*__.+?__\*\*|__\*\*.+?\*\*__|\*\*.+?\*\*|__.+?__|\*.+?\*)")


def set_runs(p_el: Any, text: str) -> Any:
    """``house.set_runs`` plus underline: ``__text__`` underlines, and
    ``**__text__**`` is bold and underlined (the firm underlines key terms)."""
    rpr = _first_rpr(p_el)
    for r in list(p_el):
        if r.tag != _qn("w:pPr"):
            p_el.remove(r)
    for seg in _MARKS.split(text):
        if not seg:
            continue
        bold = under = italic = False
        t = seg
        for _ in range(2):
            if len(t) > 4 and t.startswith("**") and t.endswith("**"):
                bold, t = True, t[2:-2]
            elif len(t) > 4 and t.startswith("__") and t.endswith("__"):
                under, t = True, t[2:-2]
        if not (bold or under) and len(t) > 2 and t.startswith("*") and t.endswith("*"):
            italic, t = True, t[1:-1]
        for j, piece in enumerate(t.split("\t")):
            if j:
                tab = p_el.makeelement(_qn("w:r"), {})
                tab.append(tab.makeelement(_qn("w:tab"), {}))
                p_el.append(tab)
            if piece:
                r = _run(p_el, rpr, piece, bold, italic)
                if under:
                    rp = r.find(_qn("w:rPr"))
                    if rp.find(_qn("w:u")) is None:
                        rp.append(rp.makeelement(_qn("w:u"), {_qn("w:val"): "single"}))
                p_el.append(r)
    return p_el


def table_kind(header: list[str]) -> str:
    h = [c.strip().strip("*").strip().lower() for c in header]
    if len(h) == 3 and h[0] == "provider":
        return "providers"
    if len(h) == 2 and h[0].startswith("anticipated"):
        return "future"
    if len(h) == 2:
        return "specials"
    raise RenderError(f"no table in this format for header {header}: {TABLES_HELP}")


# ---- dates ------------------------------------------------------------------------
def _parse_date(raw: str) -> date | None:
    raw = re.sub(r"^\w+day,\s+", "", raw.strip())
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def deadline(letter: date, days: int = 30) -> date:
    """``days`` after the letter, moved off a weekend (public holidays are the
    attorney's to see; the note says so)."""
    d = letter + timedelta(days=days)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def long_date(d: date) -> str:
    return f"{d:%A}, {d:%B} {d.day}, {d.year}"


def settle_expiry(md: str, days: int = 30) -> tuple[str, list[str]]:
    """The deadline is code's: ``days`` after the front matter's date, on a
    business day. Any other deadline the drafter wrote in ``expires:`` is
    replaced through the letter. Returns the letter and attorney-notes lines."""
    try:
        front, _b, _n = parse(md, ("date",))
    except RenderError:
        return md, []
    letter = _parse_date(front["date"])
    if letter is None:
        raise RenderError(f"the letter's date {front['date']!r} is not a date (Month D, YYYY)")
    due = deadline(letter, days)
    notes = []
    if due != letter + timedelta(days=days):
        notes.append(
            f"The deadline, {days} days after the letter, fell on a weekend; it is written as {long_date(due)}, "
            "the next business day. Public holidays are not checked: move it again if that day is one."
        )
    m = re.search(r"(?m)^expires:\s*(.*)$", md)
    said = _parse_date(m.group(1)) if m else None
    if said is not None and said != due:
        for form in (long_date(said), f"{said:%B} {said.day}, {said.year}"):
            md = md.replace(form, long_date(due) if form == long_date(said) else f"{due:%B} {due.day}, {due.year}")
        notes.append(f"The letter's deadline was written as {long_date(said)}; it is {long_date(due)}.")
    if m:
        md = md[: m.start(1)] + long_date(due) + md[m.end(1) :]
    else:
        md = re.sub(r"(?m)^(date:.*)$", lambda x: x.group(1) + f"\nexpires: {long_date(due)}", md, count=1)
    return md, notes


# ---- the writer -------------------------------------------------------------------
class CrabtreeWriter(HouseWriter):
    def __init__(self, reference: Path) -> None:
        super().__init__(reference, KINDS)
        self.base = {k: _flags(v) for k, v in self.proto.items()}

    def para(self, kind: str, text: str = "") -> Any:
        if kind == "blank":
            return super().para("blank") if "blank" in self.proto else None
        if kind not in self.proto and kind in OPTIONAL_KINDS:
            kind = OPTIONAL_KINDS[kind]
        el = copy.deepcopy(self.proto[kind])
        set_runs(el, text)
        _force(el, *self.base[kind])
        self._insert(el)
        return el

    def box(self, kind: str, lines: list[tuple[int, str]]) -> None:
        """A one-cell box (``banner``, ``box999``): each line is (the index of
        the prototype paragraph in the cell whose style it takes, its text)."""
        t = copy.deepcopy(self.proto[kind])
        tc = t.find(".//" + _qn("w:tc"))
        protos = [copy.deepcopy(p) for p in tc.findall(_qn("w:p"))]
        for p in tc.findall(_qn("w:p")):
            tc.remove(p)
        for idx, text in lines:
            p = copy.deepcopy(protos[min(idx, len(protos) - 1)])
            base = _flags(p)
            set_runs(p, text)
            _force(p, *base)
            tc.append(p)
        self._insert(t)
        self.para("blank")

    def _fill(self, tc: Any, text: str) -> None:
        ps = tc.findall(_qn("w:p"))
        for extra in ps[1:]:
            tc.remove(extra)
        base = _flags(ps[0])
        set_runs(ps[0], text)
        _force(ps[0], *base)

    def table(self, kind: str, header: list[str], rows: list[list[str]], total_last: bool = False) -> None:
        t = copy.deepcopy(self.proto[kind])
        trs = t.findall(_qn("w:tr"))
        for tr in trs:
            t.remove(tr)
        prev = self.body[len(self.body) - 2] if len(self.body) >= 2 else None
        if prev is not None and prev.tag == _qn("w:p"):
            _keep_next(prev)
        head = [] if kind in HEADERLESS else [(trs[0], header)]
        # Every prototype row between the header and the total is a body style,
        # used in turn: a reference with two gives the firm's banded rows.
        bodies = trs[1:-1] or [trs[min(1, len(trs) - 1)]]
        for n, (template, row) in enumerate(
            head
            + [
                (trs[-1] if total_last and i == len(rows) - 1 else bodies[i % len(bodies)], r)
                for i, r in enumerate(rows)
            ]
        ):
            tr = copy.deepcopy(template)
            cells = tr.findall(_qn("w:tc"))
            if len(cells) != len(row):
                raise RenderError(f"a {kind} table row has {len(row)} cells; the format's table has {len(cells)}")
            for tc, val in zip(cells, row):
                self._fill(tc, val)
            if n == 0 and head:
                for p_el in tr.iter(_qn("w:p")):
                    _keep_next(p_el)
            t.append(tr)
        self._insert(t)
        self.para("blank")

    def slots(self, values: dict[str, str]) -> None:
        """Fill ``{slot}`` text in every header and footer the reference defines."""
        for section in self.doc.sections:
            for part in (
                section.header,
                section.first_page_header,
                section.even_page_header,
                section.footer,
                section.first_page_footer,
                section.even_page_footer,
            ):
                if part.is_linked_to_previous:
                    continue
                for t in part._element.iter(_qn("w:t")):
                    for slot, value in values.items():
                        if t.text and "{" + slot + "}" in t.text:
                            t.text = t.text.replace("{" + slot + "}", value)


def banner_lines(variant: dict[str, Any], figure: int | None, expires: date) -> list[tuple[int, str]]:
    """The banner box's lines: (prototype paragraph index, text)."""
    exp = [(2, "THIS DEMAND EXPIRES AT 5:00 P.M. PACIFIC TIME"), (3, f"ON {long_date(expires).upper()}")]
    if figure is None:
        return [(0, str(variant["banner_title"])), (1, str(variant["banner_statute"])), *exp]
    return [(0, f"{terms.money(figure)} DEMAND - {terms.words(figure)}"), *exp]


def _fill(text: str, ctx: dict[str, str]) -> str:
    for k, v in ctx.items():
        text = text.replace("{" + k + "}", v)
    return text


def _header(w: CrabtreeWriter, front: dict[str, str], variant: dict[str, Any], figure: int | None) -> None:
    letter = _parse_date(front["date"])
    if letter is None:
        raise RenderError(f"the letter's date {front['date']!r} is not a date (Month D, YYYY)")
    due = deadline(letter, int(variant.get("response_days") or 30))
    w.para("date", front["date"])
    if variant.get("mail_line"):
        w.para("mail", str(variant["mail_line"]))
    lines = [x.strip() for x in front["attn"].split("|") if x.strip()]
    if lines and not lines[0].lower().startswith("attn"):
        lines[0] = f"Attn: {lines[0]}"
    for line in lines:
        w.para("addr", line)
    rows = [(label, front.get(key, "").strip()) for label, key in RE_ROWS]
    rows = [(label, v) for label, v in rows if v]
    for n, (label, value) in enumerate(rows):
        if n == 0:
            w.para("re_first", f"**RE:**\t**{label}**\t:\t{value}")
        else:
            w.para("re_line", f"\t**{label}**\t:\t{value}")
    w.box("banner", banner_lines(variant, figure, due))
    w.para("salute", front["salutation"])
    w.para("lead", str(variant["lead_heading"]))
    ctx = {"date": front["date"], "expires": long_date(due), "days": str(int(variant.get("response_days") or 30))}
    box = [(0, _fill(str(variant["box_title"]), ctx))]
    box += [(1, _fill(str(x), ctx)) for x in variant.get("box_lines") or []]
    box.append((2, _fill(str(variant["box_deadline"]), ctx)))
    w.box("box999", box)


class _Body:
    """The letter body, line by line, into the writer."""

    def __init__(self, w: CrabtreeWriter, sign: dict[str, str], headings: list[str] | None = None) -> None:
        self.w, self.sig = w, sign
        #: A section title is written in its authored form when it is one.
        self.canon = {fold_dashes(h): h for h in headings or []}
        self.sections = self.letter = 0
        self.in_exhibits = self.signed = False
        self.exhibits = 0

    def sign(self) -> None:
        w, s = self.w, self.sig
        w.para("valediction", s["valediction"])
        w.para("signfirm", s["firm"])
        w.para("signscript", s.get("script") or s["signer"])
        w.para("signname", f"{s['signer']}, (SBN {s['bar']})" if s.get("bar") else s["signer"])
        w.para("signname", s["title"])
        if s.get("for"):
            w.para("signname", s["for"])
        if s.get("initials"):
            w.para("initials", s["initials"])
        if self.exhibits:
            w.para("enclosures", f"Enclosures: Exhibits 1\u2013{self.exhibits} as indexed")
        self.signed = True

    def heading(self, line: str) -> None:
        level = len(line) - len(line.lstrip("#"))
        title = line[level:].strip()
        if level == 2:
            self.letter = 0
            if title.lower().startswith(INDEX):
                if not self.signed:
                    self.sign()
                self.w.para("exh_head", "INDEX OF EXHIBITS")
                self.in_exhibits = True
            else:
                self.sections += 1
                title = self.canon.get(fold_dashes(title), title)
                self.w.para("section", f"{roman(self.sections)}. {title.upper()}")
        elif level == 3:
            self.letter += 1
            self.w.para("subhead", f"{chr(64 + self.letter)}.  {_LETTERED.sub('', title)}")
        else:
            # A region head in the preamble ("Lumbar Spine - <date>"); a study
            # head inside a section ("MRI of the Lumbar Spine").
            self.w.para("minorhead" if self.sections == 0 else "studyhead", title)

    def table(self, block: list[list[str]]) -> None:
        block = [r for r in block if not all(re.fullmatch(r":?-{2,}:?", c) for c in r)]
        if self.in_exhibits:
            raise RenderError("the index of exhibits is paragraphs ('Exhibit n: ...'), not a table")
        kind = table_kind(block[0])
        rows = block[1:]
        total = bool(rows) and rows[-1][0].strip("* ").upper().startswith(TOTAL_LEADS)
        if total:
            rows[-1] = [c.replace("**", "") for c in rows[-1]]
        self.w.table(kind, block[0], rows, total_last=total)

    def paragraph(self, text: str) -> None:
        if not self.in_exhibits:
            self.w.para("body", text)
            return
        m = _EXHIBIT_ITEM.match(text)
        self.w.para("exh_item", f"**Exhibit {m.group(1)}:**\t{m.group(2)}" if m else text)

    def feed(self, lines: list[str]) -> None:
        self.exhibits = _count_exhibits(lines)
        i = 0
        item = re.compile(r"\s*(- |>> |> |\^ )")
        while i < len(lines):
            line = lines[i].rstrip()
            s = line.strip()
            if not s:
                i += 1
            elif line.startswith(("## ", "### ", "#### ")):
                self.heading(line)
                i += 1
            elif s.startswith("|"):
                block = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    block.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                    i += 1
                self.table(block)
            elif item.match(line):
                if s.startswith(">> "):  # a physician's own words, quoted
                    self.w.para("pquote", s[3:].strip())
                else:
                    kind = {"- ": "bullet", "> ": "quote", "^ ": "note"}[s[:2]]
                    self.w.para(kind, s[2:].strip())
                i += 1
            else:
                buf = [s]
                i += 1
                while (
                    i < len(lines)
                    and lines[i].strip()
                    and not re.match(r"(#{2,4} |\s*\||" + item.pattern + ")", lines[i])
                    and not (self.in_exhibits and _EXHIBIT.match(lines[i].strip()))
                ):
                    buf.append(lines[i].strip())
                    i += 1
                self.paragraph(" ".join(buf))
        if not self.signed:
            self.sign()


def _count_exhibits(lines: list[str]) -> int:
    seen = False
    nums = []
    for ln in lines:
        if ln.startswith("## "):
            seen = ln[3:].strip().lower().startswith(INDEX)
        elif seen:
            m = _EXHIBIT.match(ln.strip())
            if m:
                nums.append(int(m.group(1)))
    return max(nums) if nums else 0


def place_figure(body: str, figure: int | None) -> str:
    """The demand amount, code's: the token the letter carries becomes the
    policy limits or the request's figure. No token is a refusal."""
    if FIGURE_TOKEN not in body:
        raise RenderError(f"the DEMAND section does not carry {FIGURE_TOKEN} where the amount goes")
    return body.replace(FIGURE_TOKEN, figure_text(figure))


def figure_text(figure: int | None) -> str:
    """How the letter's DEMAND section writes the amount: ``FULL AVAILABLE
    POLICY LIMITS`` or ``$75,000.00`` (the firm writes the cents)."""
    if figure is None:
        return POLICY_LIMITS
    dollars, cents = divmod(int(figure), 100)
    return f"${dollars:,}.{cents:02d}"


def render(
    md: str,
    reference: Path,
    out_dir: Path,
    *,
    variant: dict[str, Any],
    signature: str,
    signer: str,
    signer_title: str,
    author: str,
    client_label: str | None = None,
    initials: str = "",
    bar_number: str = "",
    figure: int | None = None,
    script: str = "",
) -> tuple[Path, str]:
    """``(the demand file, the attorney notes text)``; as ``house.render``.
    ``figure``: the request's demand figure in cents (``terms.demand_figure``),
    None for a policy-limits demand. ``script``: the name the script
    signature line prints (the attorney's own short form), else ``signer``."""
    front, body, notes = parse(md, FRONT_KEYS)
    body = place_figure(body, figure)
    w = CrabtreeWriter(reference)
    w.slots({"sf_office": str(variant["sf_office"])})
    _header(w, front, variant, figure)
    client = client_label or front["client"]
    sign = {
        "valediction": str(variant["valediction"]),
        "firm": signature,
        "signer": signer,
        "script": script,
        "bar": bar_number,
        "title": signer_title,
        "for": str(variant.get("sign_for") or "").replace("{client}", client),
        "initials": initials,
    }
    _Body(w, sign, list(variant.get("headings") or [])).feed(body.strip("\n").splitlines())
    out_dir.mkdir(parents=True, exist_ok=True)
    name = out_name(client)
    out = out_dir / name
    w.save(out, title=name[:-5], author=author)
    return out, notes
