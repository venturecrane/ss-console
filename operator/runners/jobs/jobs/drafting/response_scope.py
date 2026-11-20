"""What a discovery-response job reads: the parts of the matter file each set needs.

WHY (2026-11-20). A response job built the lane's whole-file digest like any
other class. Measured on three matters (575 to 1,425 pages, 1.4M to 4.4M
characters, 285 to 331 files), two passed the digest's 1.6M-character ceiling
and would have been condensed with paid calls, and every scanned page was
transcribed whether a set needed it or not. Medical records alone ran 1.42M
and 1.73M characters on two of them, and 792 of one matter's 871 medical pages
were scans. A set of form interrogatories on income needs the wage file, not
the radiology films; a request for production needs a list of the file.

So, in code, never a model:

* **Categories.** Every corpus file gets ONE category from the closed list
  ``CATEGORIES``: billing when its file name says so (a bill filed in a
  provider's folder is still a bill), else by its deepest folder name, then its file name, then the
  folders above, against the firm's patterns (``drafting-firm.yaml`` section
  ``response_scope``, checked by ``validate`` from ``firm.py``); a firm that
  authors none uses ``DEFAULT_PATTERNS``, the one generic list. Within one
  name, ``PRECEDENCE`` decides. Then the extension (``EXT_CATEGORY``: a
  ``.msg`` is correspondence), then a folder named for a Medicals-tab provider
  (medical records); nothing matched is ``other``.
* **Needs.** ``NEEDS`` (by set type) and ``FORM_NEEDS`` (by form section
  group) name the categories whose TEXT a unit reads. A request for production
  reads no text: it gets an inventory of every file (``INVENTORY_ONLY``). A
  category with no file (no wage records: the usual case) is normal: that
  unit's pack is simply smaller.
* **Scope** (``scope``): the files in no category any planned unit reads move
  out of ``extracted.jsonl`` into ``unread.json`` with a reason, exactly as the
  privilege wall moves a printed email to ``walled.json``. Of the files kept,
  a scanned page is left queued for transcription only when some unit's
  selection would read it, chosen from what is known before any text exists
  (the page's position, the file's folder and name, the providers): the
  count left untranscribed is in ``scope.json``.
* **Packs** (``pack``): each unit's text in the digest's own framing
  (``=== FILE: name (folder f) ===`` and ``[p.N]``, ``demand/summarize.py``), so
  a cite reads exactly as it does from a digest, capped per unit. Over the cap
  the pages are chosen in a fixed order (``_choose``): every billing page, the
  first two pages of every file, then the rest ranked by hits on the unit's
  terms and the matter's provider names. What was left out is counted in
  ``Pack`` and ``left_out_note`` says it in a sentence for the attorney notes.

``unit_pack`` is the compose seam: a unit directory holding ``pack.md`` is
composed from it instead of the job's text (``compose.Drafter.compose``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

KEY = "response_scope"
CATEGORIES: tuple[str, ...] = (
    "medical_records",
    "billing",
    "incident_report",
    "employment_wage",
    "insurance",
    "pleadings",
    "prior_discovery",
    "correspondence",
    "photographs",
    "other",
)
OTHER = "other"
#: Which category wins when one folder or name matches several. ``other``
#: first: a firm's own cost folders (ACCOUNTING, INVOICES) are never billing.
PRECEDENCE = (
    OTHER,
    "billing",
    "prior_discovery",
    "pleadings",
    "incident_report",
    "employment_wage",
    "insurance",
    "medical_records",
    "correspondence",
    "photographs",
)
#: The one generic list, for a seat whose firm authored no patterns. MEASURED
#: (2026-11-20, three real matters): with these, 2 to 19% of files and 3 to 9%
#: of the text were left in ``other``.
DEFAULT_PATTERNS: dict[str, tuple[str, ...]] = {
    OTHER: (r"\bACCOUNTING\b", r"\bINVOICES\b"),
    "billing": (r"\bBILL", r"\bLEDGER", r"\bSTATEMENT"),
    "prior_discovery": (r"\bDISCOVERY\b", r"\bDEF\b", r"\bDEPOSITION"),
    "pleadings": (r"\bPLEADING", r"\bSUMMONS\b", r"\bCOMPLAINT\b", r"\bANSWER\b", r"\bSERVICE\b"),
    "incident_report": (r"\bPOLICE\b", r"\bINCIDENT\b", r"\bCHP\b"),
    "employment_wage": (r"\bWAGE", r"\bEMPLOY", r"\bPAYROLL\b"),
    "insurance": (
        r"\b1ST\b",
        r"\b3RD\b",
        r"\bINSURANCE\b",
        r"\bPOLICY\b",
        r"\bLIEN",
        r"\bDHCS\b",
        r"\bCMS\b",
        r"\bMEDICARE\b",
    ),  # fmt: skip
    "medical_records": (r"\bMEDICAL\b", r"\bRECORDS?\b"),
    "correspondence": (r"\bCORRESPONDENCE\b", r"\bE-?MAILS?\b"),
    "photographs": (r"\bPHOTO",),
}
#: Decided by the file's extension when no folder or name pattern matched.
EXT_CATEGORY: dict[str, str] = {
    ".msg": "correspondence",
    ".eml": "correspondence",
    **{e: "photographs" for e in (".jpg", ".jpeg", ".png", ".heic", ".gif", ".tif", ".tiff")},
    **{e: OTHER for e in (".download", ".css", ".html", ".htm", ".js")},  # saved web-page assets
}

#: The form interrogatory section groups a form set is answered in.
FORM_GROUPS = ("background", "incident", "injuries_treatment", "income")
FORM = "form_interrogatories"
_ALL_BUT_IMAGES: frozenset[str] = frozenset(c for c in CATEGORIES if c not in ("photographs", OTHER))
#: Categories whose TEXT each kind of set reads.
NEEDS: dict[str, frozenset[str]] = {
    "special_interrogatories": _ALL_BUT_IMAGES,
    "requests_for_admission": _ALL_BUT_IMAGES,
    "requests_for_production": frozenset(),
}
FORM_NEEDS: dict[str, frozenset[str]] = {
    "background": frozenset({"pleadings", "prior_discovery", "insurance", "correspondence"}),
    "incident": frozenset({"incident_report", "pleadings", "prior_discovery", "photographs"}),
    "injuries_treatment": frozenset({"medical_records", "billing", "prior_discovery"}),
    "income": frozenset({"employment_wage", "billing", "prior_discovery"}),
}
#: Each DISC-001 section's group (the section numbers of ``forms/catalog``). A
#: section not named here (15 denials, 16 contentions, 17 admissions, 50
#: contract) reads every group's categories.
SECTION_GROUP: dict[str, str] = {
    **{s: "background" for s in ("1.0", "2.0", "3.0", "4.0", "11.0")},
    **{s: "incident" for s in ("7.0", "12.0", "13.0", "14.0", "20.0")},
    **{s: "injuries_treatment" for s in ("6.0", "9.0", "10.0")},
    "8.0": "income",
}
#: A set answered from an inventory of the file, with no page text.
INVENTORY_ONLY = frozenset({"requests_for_production"})
#: Characters a unit's pack may hold (the house style, skeleton, exemplar and
#: context sit beside it under ``compose.COMPOSE_INPUT_MAX_CHARS``). Decided,
#: not measured: the design's limits (plan section D).
FORM_GROUP_CAP = 400_000
SPECIAL_CAP = 500_000
CAPS: dict[str, int] = {"special_interrogatories": SPECIAL_CAP, "requests_for_admission": SPECIAL_CAP}
#: Terms a unit's over-cap pages are ranked by, beside the matter's providers.
TERMS: dict[str, tuple[str, ...]] = {
    "background": ("residence", "address", "born", "education", "license", "insurance", "policy"),
    "incident": ("collision", "accident", "incident", "scene", "witness", "vehicle", "police", "speed"),
    "injuries_treatment": ("diagnosis", "pain", "injury", "treatment", "impression", "assessment", "plan"),
    "income": ("wage", "salary", "hours", "employer", "earnings", "missed", "work"),
    "special_interrogatories": ("injury", "treatment", "diagnosis", "wage", "witness", "incident", "damages"),
    "requests_for_admission": ("injury", "treatment", "incident", "fault", "damages"),
}
#: A page still to transcribe counts at this size until it is read (the
#: ledger path's figure, ``evidence_cost.TRANSCRIBED_CHARS``; a guess there).
SCAN_PAGE_CHARS = 2_500
UNREAD = "unread.json"
SCOPE = "scope.json"
PACK = "pack.md"
PACK_RECORD = "pack.json"
#: Pages of every file kept before any ranking, over a cap.
FIRST_PAGES = 2
_PAGE = re.compile(r"\[p\.(\d+)\][^\n]*\n?")


# ---- the firm input ------------------------------------------------------------------
def validate(body: object) -> list[str]:
    """``response_scope``'s closed shape: ``{patterns: {<category>: [regex, ...]}}``."""
    if not isinstance(body, dict) or set(body) != {"patterns"}:
        return [f"{KEY}: expected exactly {{patterns: {{<category>: [regex, ...]}}}}"]
    pats = body["patterns"]
    if not isinstance(pats, dict) or not pats:
        return [f"{KEY}.patterns: expected a map of one or more of {list(PRECEDENCE)}"]
    out = [f"{KEY}.patterns.{c}: not one of {list(PRECEDENCE)}" for c in pats if c not in PRECEDENCE]
    for c, ps in pats.items():
        if not isinstance(ps, list) or not ps or not all(isinstance(p, str) and p for p in ps):
            out.append(f"{KEY}.patterns.{c}: expected a list of one or more regexes")
            continue
        for p in ps:
            try:
                re.compile(p)
            except re.error as exc:
                out.append(f"{KEY}.patterns.{c}: invalid regex {p!r} ({exc})")
    return out


Patterns = dict[str, list[re.Pattern[str]]]


def patterns(firm_data: dict[str, Any] | None) -> Patterns:
    """The firm's patterns when it authored them, else ``DEFAULT_PATTERNS``."""
    body = (firm_data or {}).get(KEY)
    raw: Mapping[str, Sequence[str]] = body["patterns"] if body is not None and not validate(body) else DEFAULT_PATTERNS
    return {c: [re.compile(p, re.I) for p in raw.get(c, ())] for c in PRECEDENCE}


def _match(label: str, pats: Patterns) -> str | None:
    return next((c for c in PRECEDENCE if any(p.search(label) for p in pats.get(c, ()))), None)


def _provider_folder(folders: list[str], providers: Sequence[str]) -> bool:
    names = [p.lower() for p in providers if len(p.strip()) >= 4]
    return any(n in f.lower() or f.lower() in n for f in folders if len(f) >= 4 for n in names)


def categorize(row: dict[str, Any], pats: Patterns, providers: Sequence[str] = ()) -> str:
    """One category for one corpus file: the deepest folder name, the file
    name, the folders above it, the extension, a provider's folder."""
    folders = [s for s in re.split(r"[\\/]+", str(row.get("folder") or "")) if s.strip()]
    name = str(row.get("name") or "")
    if _match(name, pats) == "billing":  # a bill filed in its provider's or a medical folder is still a bill
        return "billing"
    for label in (*folders[-1:], name, *reversed(folders[:-1])):
        got = _match(label, pats)
        if got:
            return got
    ext = str(row.get("ext") or Path(str(row.get("name") or "")).suffix).lower()
    if ext in EXT_CATEGORY:
        return EXT_CATEGORY[ext]
    if row.get("kind") == "email_body":
        return "correspondence"
    return "medical_records" if _provider_folder(folders, providers) else OTHER


# ---- which categories a unit reads -----------------------------------------------------
def text_needs(set_type: str | None, group: str | None = None) -> frozenset[str]:
    """The categories whose text a unit reads. A form set with no group reads
    every group's; a set type the table does not name reads every category."""
    if set_type == FORM:
        if group is not None:
            return FORM_NEEDS[group]
        return frozenset().union(*FORM_NEEDS.values())
    return NEEDS.get(str(set_type), frozenset(CATEGORIES))


def group_of_section(section: str) -> str | None:
    """A form section's group (``SECTION_GROUP``); None reads every group's categories."""
    return SECTION_GROUP.get(section)


def readers(plan: Iterable[dict[str, Any]]) -> list[tuple[str, str | None]]:
    """Every (set type, form group) that reads text, a form set once per group."""
    out: list[tuple[str, str | None]] = []
    for p in plan:
        st = str(p.get("variant"))
        for r in [(st, g) for g in FORM_GROUPS] if st == FORM else [(st, None)]:
            if st not in INVENTORY_ONLY and r not in out:
                out.append(r)
    return out


def needed(plan: Iterable[dict[str, Any]]) -> frozenset[str]:
    """Every category some planned unit reads the text of."""
    return frozenset().union(*(text_needs(p.get("variant")) for p in plan))


def cap_for(set_type: str | None, group: str | None = None) -> int:
    return FORM_GROUP_CAP if set_type == FORM else CAPS.get(str(set_type), SPECIAL_CAP)


# ---- pages and the over-cap selection ------------------------------------------------------
def pages_of(text: str) -> list[tuple[int, str]]:
    """``(page, text with its [p.N] line)`` in order; text with no marker is page 1."""
    marks = list(_PAGE.finditer(text))
    if not marks:
        return [(1, text)] if text.strip() else []
    return [
        (int(m.group(1)), text[m.start() : marks[i + 1].start() if i + 1 < len(marks) else len(text)])
        for i, m in enumerate(marks)
    ]


def _blank(page: str) -> bool:
    return not _PAGE.sub("", page, count=1).strip()


def header(name: Any, folder: Any) -> str:
    """The digest's file header (``demand/summarize._pieces``)."""
    return f"=== FILE: {name} (folder {folder or '/'}) ==="


@dataclass
class Doc:
    """One file a unit may read: its rows, category and pages (size, score)."""

    row: dict[str, Any]
    category: str
    pages: list[tuple[int, int, int]]  # (page, characters, rank score)
    text: dict[int, str] = field(default_factory=dict)

    @property
    def head(self) -> int:
        return len(header(self.row.get("name"), self.row.get("folder"))) + 2


def _hits(text: str, words: Sequence[str]) -> int:
    low = text.lower()
    return sum(low.count(w) for w in words)


def _words(set_type: str | None, group: str | None, providers: Sequence[str], terms: Sequence[str]) -> list[str]:
    return [w.lower() for w in (*TERMS.get(group or str(set_type), ()), *terms, *providers) if w and w.strip()]


def _choose(docs: list[Doc], cap: int) -> set[tuple[int, int]]:
    """The pages kept over a cap, in the fixed order: billing pages, every
    file's first pages, then by rank (score, then file and page order)."""
    order = [(di, n, size) for di, d in enumerate(docs) if d.category == "billing" for n, size, _ in d.pages]
    order += [(di, n, size) for di, d in enumerate(docs) for n, size, _ in d.pages[:FIRST_PAGES]]
    rest = [(di, n, size, score) for di, d in enumerate(docs) for n, size, score in d.pages[FIRST_PAGES:]]
    order += [(di, n, size) for di, n, size, _ in sorted(rest, key=lambda x: (-x[3], x[0], x[1]))]
    chosen: set[tuple[int, int]] = set()
    headed: set[int] = set()
    used = 0
    for di, n, size in order:
        if (di, n) in chosen:
            continue
        cost = size + (0 if di in headed else docs[di].head)
        if used + cost > cap:
            continue
        chosen.add((di, n))
        headed.add(di)
        used += cost
    return chosen


def _fits(docs: list[Doc], cap: int) -> bool:
    return sum(d.head + sum(s for _, s, _ in d.pages) for d in docs) <= cap


def corpus_rows(data: Path) -> list[dict[str, Any]]:
    text = (data / "extracted.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line]


def scan_pages(row: dict[str, Any]) -> list[int]:
    """The pages of a row still to be transcribed."""
    t = row.get("transcribe")
    return list(range(1, int(row.get("pages") or 1) + 1)) if t == "all" else [int(p) for p in t or []]


def _text(row: dict[str, Any]) -> str:
    p = row.get("text_path")
    return Path(p).read_text(encoding="utf-8", errors="replace") if p and Path(p).is_file() else ""


# ---- the scope step ---------------------------------------------------------------------
def scope(
    data: Path, firm_data: dict[str, Any] | None, plan: list[dict[str, Any]], providers: Sequence[str] = ()
) -> dict[str, Any]:
    """Move the files no planned unit reads out of the corpus (``unread.json``),
    leave queued only the scanned pages some unit's selection reads, record
    every file's category (``scope.json``) and return that record."""
    from ..demand.preflight import write_extracted

    pats, need = patterns(firm_data), needed(plan)
    keep, unread, listed = [], [], []
    for r in corpus_rows(data):
        c = categorize(r, pats, providers)
        listed.append({"id": r.get("id"), "name": r.get("name"), "folder": r.get("folder"), "category": c,
                       "pages": int(r.get("pages") or 0), "read": c in need})  # fmt: skip
        if c in need:
            keep.append(r)
        else:
            unread.append({"id": r.get("id"), "name": r.get("name"), "category": c,
                           "scanned_pages": len(scan_pages(r)), "reason": f"{c}: no set in this job reads it"})  # fmt: skip
    wanted = _scans_read(keep, {str(f["id"]): f["category"] for f in listed}, plan, providers)
    left = 0
    for r in keep:
        scans = scan_pages(r)
        if scans:
            read = [n for n in scans if (str(r.get("id")), n) in wanted]
            left += len(scans) - len(read)
            r["transcribe"] = read
    rec = {
        "needed": sorted(need),
        "files": listed,
        "unread": len(unread),
        "scan_pages_unread_files": sum(u["scanned_pages"] for u in unread),
        "scan_pages_untranscribed": left,
        "scan_pages_to_read": sum(len(scan_pages(r)) for r in keep),
    }
    (data / SCOPE).write_text(json.dumps(rec, indent=1), encoding="utf-8")
    if unread:
        (data / UNREAD).write_text(json.dumps(unread, indent=1), encoding="utf-8")
    write_extracted(data, keep)
    return rec


def _scans_read(
    rows: list[dict[str, Any]], cats: dict[str, str], plan: list[dict[str, Any]], providers: Sequence[str]
) -> set[tuple[str, int]]:
    """(file id, page) of every scanned page some reader's selection keeps,
    judged before any text: a scanned page counts at ``SCAN_PAGE_CHARS`` and
    ranks by its file's folder and name."""
    out: set[tuple[str, int]] = set()
    texts = {str(r.get("id")): dict(pages_of(_text(r))) for r in rows}
    for st, group in readers(plan):
        want, words = text_needs(st, group), _words(st, group, providers, ())
        docs = []
        for r in rows:
            rid = str(r.get("id"))
            if cats.get(rid) not in want:
                continue
            label = _hits(f"{r.get('folder') or ''} {r.get('name') or ''}", words)
            scans, layer = set(scan_pages(r)), texts[rid]
            n_pages = max([int(r.get("pages") or 0), *layer, *scans]) if (layer or scans) else 0
            pages = [
                (n, SCAN_PAGE_CHARS, label) if n in scans else (n, len(layer.get(n, "")), label + _hits(layer.get(n, ""), words))
                for n in range(1, n_pages + 1)
                if n in scans or n in layer
            ]  # fmt: skip
            docs.append(Doc(r, cats[rid], pages))
        if _fits(docs, cap_for(st, group)):
            out |= {(str(d.row.get("id")), n) for d in docs for n, _, _ in d.pages}
            continue
        chosen = _choose(docs, cap_for(st, group))
        out |= {(str(docs[di].row.get("id")), n) for di, n in chosen}
    return out


def categories(data: Path) -> dict[str, str]:
    """File id -> category, as the scope step recorded it."""
    return {str(f["id"]): str(f["category"]) for f in json.loads((data / SCOPE).read_text(encoding="utf-8"))["files"]}


# ---- packs ------------------------------------------------------------------------------
@dataclass
class Pack:
    text: str
    cap: int
    pages_read: int = 0
    pages_left_out: int = 0
    chars_left_out: int = 0
    files_left_out: int = 0
    #: Pages with no text: scans the scope step left untranscribed, blank pages.
    pages_without_text: int = 0
    left_out_by_category: dict[str, int] = field(default_factory=dict)

    @property
    def chars(self) -> int:
        return len(self.text)

    def record(self) -> dict[str, Any]:
        return {
            "chars": self.chars,
            "cap": self.cap,
            "pages_read": self.pages_read,
            "pages_left_out": self.pages_left_out,
            "chars_left_out": self.chars_left_out,
            "files_left_out": self.files_left_out,
            "pages_without_text": self.pages_without_text,
            "left_out_by_category": self.left_out_by_category,
        }


def pack(
    data: Path,
    set_type: str | None,
    group: str | None = None,
    *,
    providers: Sequence[str] = (),
    terms: Sequence[str] = (),
) -> Pack:
    """One unit's text, under its cap, in the digest's framing."""
    if set_type in INVENTORY_ONLY:
        return Pack(inventory(data), cap=0)
    cats, want = categories(data), text_needs(set_type, group)
    words = _words(set_type, group, providers, terms)
    docs, blank = [], 0
    for r in corpus_rows(data):
        c = cats.get(str(r.get("id")), OTHER)
        if c not in want:
            continue
        label = _hits(f"{r.get('folder') or ''} {r.get('name') or ''}", words)
        pg = pages_of(_text(r))
        blank += sum(1 for _, t in pg if _blank(t))
        full = [(n, t) for n, t in pg if not _blank(t)]
        if full:
            docs.append(Doc(r, c, [(n, len(t), label + _hits(t, words)) for n, t in full], dict(full)))
    cap = cap_for(set_type, group)
    chosen = None if _fits(docs, cap) else _choose(docs, cap)
    out = Pack(_framed(docs, chosen), cap, pages_without_text=blank)
    for di, d in enumerate(docs):
        lost = [n for n, _, _ in d.pages if chosen is not None and (di, n) not in chosen]
        out.pages_read += len(d.pages) - len(lost)
        if lost:
            out.pages_left_out += len(lost)
            out.chars_left_out += sum(len(d.text[n]) for n in lost)
            out.left_out_by_category[d.category] = out.left_out_by_category.get(d.category, 0) + len(lost)
            out.files_left_out += len(lost) == len(d.pages)
    return out


def _framed(docs: list[Doc], chosen: set[tuple[int, int]] | None) -> str:
    parts = []
    for di, d in enumerate(docs):
        kept = [d.text[n] for n, _, _ in d.pages if chosen is None or (di, n) in chosen]
        if kept:
            parts.append(header(d.row.get("name"), d.row.get("folder")) + "\n" + "".join(kept) + "\n")
    return "\n".join(parts)


def inventory(data: Path) -> str:
    """Every file the job may cite, by name, category and pages: no page text."""
    walled_p = data / "walled.json"
    walled = (
        {str(w.get("id")) for w in json.loads(walled_p.read_text(encoding="utf-8"))} if walled_p.is_file() else set()
    )
    rows = [f for f in json.loads((data / SCOPE).read_text(encoding="utf-8"))["files"] if str(f["id"]) not in walled]
    lines = ["THE FILE INVENTORY (every document in the matter file, listed by code; no page text):", ""]
    lines += ["| File | Folder | Category | Pages |", "| --- | --- | --- | --- |"]
    lines += [f"| {f['name']} | {f.get('folder') or '/'} | {f['category']} | {f['pages']} |" for f in rows]
    return "\n".join(lines) + "\n"


def providers_of(facts: dict[str, Any]) -> list[str]:
    """The Medicals tab's providers, the ranking's matter names."""
    return [str(m.get("provider")) for m in facts.get("medicals") or [] if m.get("provider")]


def unit_pack(unit_data: Path) -> str | None:
    """The compose seam: the unit's pack when the scope step wrote one."""
    p = unit_data / PACK
    return p.read_text(encoding="utf-8") if p.is_file() else None


def write_packs(data: Path, plan: list[dict[str, Any]], facts: dict[str, Any]) -> str:
    """Every unit's ``pack.md`` and ``pack.json`` in its directory; returns
    the job's text (each distinct pack once), which the job keeps as
    ``digest.md`` for the notes, the review and the repair checks."""
    prov = providers_of(facts)
    distinct: list[str] = []
    for p in plan:
        d = data / p["key"] if p.get("key") else data
        d.mkdir(parents=True, exist_ok=True)
        got = pack(data, p.get("variant"), providers=prov)
        (d / PACK).write_text(got.text, encoding="utf-8")
        (d / PACK_RECORD).write_text(json.dumps(got.record(), indent=1), encoding="utf-8")
        if got.text not in distinct:
            distinct.append(got.text)
    return "\n\n".join(distinct)


def left_out_note(label: str, rec: dict[str, Any]) -> str | None:
    """The attorney notes' sentence for a pack that left pages out; None when it read everything."""
    if not rec.get("pages_left_out"):
        return None
    cats = ", ".join(f"{c.replace('_', ' ')} {n:,}" for c, n in sorted(rec["left_out_by_category"].items()))
    return (
        f"{label}: drafted from {rec['pages_read']:,} pages of the file; {rec['pages_left_out']:,} pages "
        f"({rec['chars_left_out']:,} characters; by category: {cats}) were over its reading limit and not read."
    )
