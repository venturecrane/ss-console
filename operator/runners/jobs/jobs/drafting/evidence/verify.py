"""Code verifies every fact the model proposed, against the page it names.

For each ``Candidate``:

1. The excerpt is located by its WORDS on the cited page of the cited file.
   Found, the fact's excerpt becomes the page's own characters for those
   words (punctuation included), so a model that typed a semicolon where the
   page prints a comma cannot carry its version into the brief (taxonomy
   item 16). Not on the cited page but elsewhere in the same file, the fact is
   ``relocated`` and its page corrected (the wrong-pin class, 10 of 23).
   Nowhere in the file, ``rejected``: never indexed, never rendered.
2. The excerpt is widened to the sentence it sits in, so a quote cannot stop
   just before the words that change its meaning.
3. A figure's value is parsed by ``howell.money`` from the verified
   characters (a split dollars-and-cents box reads as cents, never 100x); a
   date's by ``howell.parse_date`` (plus month-name forms). A value the
   excerpt does not print is not a figure.
4. Testimony's page and lines are located in code with the gate checker's
   transcript parser. Unlocatable, the p:l is dropped and the fact is cited as
   a file page.

Ids (``F0001`` ...) are assigned here, in corpus order, so the same inputs
give the same ids on a resume.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable, Mapping

from .. import howell
from .shapes import KINDS, ROLES, Candidate, DocPages, Fact, Ledger

#: A widened excerpt longer than this keeps the words the model chose.
WIDEN_MAX = 600
#: Below this many words an excerpt is too short to relocate without
#: ambiguity: it must sit on the page it names.
RELOCATE_MIN_WORDS = 4
_WORD = re.compile(r"[A-Za-z0-9]+")
_SPACER = "·"
_ABBREV = {
    "dr", "mr", "mrs", "ms", "no", "nos", "p", "pp", "vs", "v", "st", "inc", "co", "jr", "sr", "eg", "ie",
    "cal", "app", "etc", "approx", "ave", "dept", "fig", "id", "ibid", "civ", "proc", "evid", "code", "sec",
    "rptr", "supp", "ex", "exh", "dep", "corp", "ltd", "mt", "ft", "hon",
}  # fmt: skip
_MONTH_FORMATS = ("%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b. %d, %Y", "%b %d %Y", "%d %B %Y")
_MONEY = re.compile(r"\(?-?\$?\s?-?\d{1,3}(?:,\d{3})+(?:\.\d{2})?\)?|\(?-?\$\s?-?\d+(?:\.\d{2})?\)?|\b\d+\.\d{2}\b")
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s?(?:%|percent\b)", re.I)


def page_sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# ---- the gate checker (its transcript parser) ------------------------------------------


@lru_cache(maxsize=4)
def _load(path: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location("_evidence_gate_checker", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load the drafting gate checker at {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def checker() -> ModuleType:
    """The seat's gate checker (``record_check.checker_path``), else the
    repo's own copy. One parser of transcript pages and lines, never two."""
    try:
        from smokeball_connector.record_check import checker_path

        p = Path(checker_path())
        if p.is_file():
            return _load(str(p))
    except ImportError:
        pass
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "templates" / "drafting" / "drafting_gate_check.py"
        if cand.is_file():
            return _load(str(cand))
    raise RuntimeError("the drafting gate checker is not on this machine")


# ---- the page's own characters ----------------------------------------------------------


_LABEL = re.compile(r"^(?:BY [A-Z][^:]{0,60}:\s*)?[QA][.:]\s+")


def clean_page(text: str, transcript: bool, chk: ModuleType | None = None) -> str:
    """The page's own characters, one line per printed line: whitespace runs
    inside a line collapsed, a reporter's middle-dot spacers read as spaces,
    and on a transcript the gutter line numbers, ``Page N`` header lines and
    ``Q.`` / ``A.`` labels removed, so an excerpt that runs across transcript
    lines is contiguous by its words. A blank line (``\n\n``) marks a hard
    boundary: each question and each answer starts one."""
    out: list[str] = []
    for line in (text or "").splitlines():
        hard = False
        if transcript and chk is not None:
            if chk._PAGE_RE.match(line):
                continue
            m = chk._GUTTER_RE.match(line)
            if m:
                line = m.group(2)
            line = line.replace(_SPACER, " ").strip()
            lab = _LABEL.match(line)
            if lab:
                line, hard = line[lab.end() :], True
        line = " ".join(line.replace(_SPACER, " ").split())
        if not line:
            continue
        if hard and out:
            out.append("")
        out.append(line)
    return "\n".join(out)


def _boundary(clean: str, i: int) -> bool:
    """Whether the line break at ``clean[i]`` ends a statement: always after a
    blank line; otherwise when the next line opens with a capital or a digit
    and this line does not end mid-phrase (a lower-case word or a comma)."""
    if clean[i] != "\n":
        return False
    if clean[i + 1 : i + 2] == "\n" or clean[i - 1 : i] == "\n":
        return True
    nxt = clean[i + 1 : i + 2]
    prev = clean[i - 1 : i]
    if not (nxt.isupper() or nxt.isdigit()):
        return False
    return not (prev.islower() or prev in ",;:-(")


def _words(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group(0).lower()) for m in _WORD.finditer(text)]


def locate(excerpt: str, clean: str) -> tuple[int, int] | None:
    """(start, end) in ``clean`` of the first run of the excerpt's words, or
    None. Words only: punctuation and case never decide whether the words are
    on the page; the page's characters decide what the excerpt says."""
    want = [w for _a, _b, w in _words(excerpt)]
    if not want:
        return None
    have = _words(clean)
    hay = " " + " ".join(w for _a, _b, w in have) + " "
    pos = hay.find(" " + " ".join(want) + " ")
    if pos < 0:
        return None
    idx = hay.count(" ", 0, pos + 1) - 1  # the index of the first matched word
    first, last = have[idx], have[idx + len(want) - 1]
    return first[0], last[1]


def _is_end(clean: str, i: int) -> bool:
    """Whether ``clean[i]`` ends a sentence."""
    if clean[i] not in ".!?":
        return False
    j = i + 1
    while j < len(clean) and clean[j] in "\"')]’”":
        j += 1
    if j < len(clean) and not clean[j].isspace():
        return False
    if clean[i] == ".":
        k = i
        while k > 0 and clean[k - 1].isalpha():
            k -= 1
        word = clean[k:i].lower()
        if word in _ABBREV or (len(word) == 1 and clean[k:i].isupper()):
            return False
        if i + 1 < len(clean) and clean[i - 1 : i].isdigit() and clean[i + 1 : i + 2].isdigit():
            return False
    return True


def widen(clean: str, a: int, b: int) -> tuple[int, int]:
    """[a, b) widened to the sentence bounds around it, unless that runs past
    ``WIDEN_MAX``."""
    s = a
    while s > 0 and not _is_end(clean, s - 1) and not _boundary(clean, s - 1):
        s -= 1
    while s < a and clean[s].isspace():
        s += 1
    e = b
    while e < len(clean) and not _is_end(clean, e - 1) and not _boundary(clean, e):
        e += 1
    while e < len(clean) and clean[e] in "\"')]’”":
        e += 1
    if " ".join(clean[s:e].split()).__len__() > WIDEN_MAX:
        return a, _trail(clean, b)
    return s, e


def _trail(clean: str, b: int) -> int:
    """The end extended over closing punctuation the words stopped before."""
    while b < len(clean) and clean[b] in ".,;:!?\"')]’”":
        b += 1
    return b


# ---- values -------------------------------------------------------------------------------


def parse_date(raw: str | None) -> date | None:
    s = " ".join(str(raw or "").replace(",", ", ").split()).replace(" ,", ",")
    got = howell.parse_date(s)
    if got:
        return got
    for f in _MONTH_FORMATS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    return None


def _printed(raw: str, excerpt: str) -> bool:
    s = " ".join(raw.split())
    flat = " ".join(excerpt.split())
    if not s:
        return False
    return s in flat or s.replace(",", "") in flat.replace(",", "") or s.lstrip("$").strip() in flat


def figure_value(c: Candidate, excerpt: str) -> tuple[str, str] | None:
    """(value, unit) of a figure, parsed from the verified characters."""
    raw = (c.value or "").strip()
    if raw:
        if not _printed(raw, excerpt):
            return None
        m = _PCT.fullmatch(raw.strip())
        if m:
            return m.group(1), "pct"
        v = howell.money(raw)
        if v is None:
            return None
        dollars = (
            "$" in raw
            or re.search(r"\.\d{2}$", raw) is not None
            or howell._SPLIT_CENTS.fullmatch(raw.strip("()$- ")) is not None
            or c.role in ("billed", "paid", "adjusted")
            or "$" in excerpt
        )
        return (f"{v:.2f}", "usd") if dollars else (str(v), "number")
    hits = _MONEY.findall(excerpt)
    vals = {howell.money(h.strip()) for h in hits} - {None}
    if len(vals) == 1:
        v = vals.pop()
        return f"{v:.2f}", "usd"  # type: ignore[union-attr]
    pct = {m.group(1) for m in _PCT.finditer(excerpt)}
    if not hits and len(pct) == 1:
        return pct.pop(), "pct"
    return None


def date_value(c: Candidate, excerpt: str) -> str | None:
    raw = (c.value or "").strip()
    if not raw or not _printed(raw, excerpt):
        return None
    d = parse_date(raw)
    return d.isoformat() if d else None


# ---- testimony ------------------------------------------------------------------------


class _Transcript:
    def __init__(self, doc: DocPages, chk: ModuleType) -> None:
        text = "\n".join(doc.pages[p] for p in sorted(doc.pages))
        self.src = chk.SourceDoc(Path(doc.name), text)
        self.chk = chk

    @property
    def is_transcript(self) -> bool:
        return bool(self.src.is_transcript)

    def locate(self, excerpt: str) -> tuple[int, int, int, int] | None:
        """(start page, start line, end page, end line) of the excerpt."""
        if not self.src.has_page_line:
            return None
        needle = self.chk.normalize(self.chk.strip_markdown(excerpt))
        view = self.src.views[1]
        at = view.find(needle)
        if at < 0:
            return None
        a, b = view.line_at(at), view.line_at(at + max(len(needle) - 1, 0))
        pa, pb = self.src.page_line.get(a), self.src.page_line.get(b)
        if not pa or not pb:
            return None
        return pa[0], pa[1], pb[0], pb[1]


# ---- the stage ------------------------------------------------------------------------


def resolve(name: str, corpus: Mapping[str, DocPages]) -> str | None:
    """The document a candidate's ``file`` names: exact, else by stem."""
    name = (name or "").strip().removeprefix("FILE:").strip()
    if name in corpus:
        return name
    stem = _stem(name)
    hits = [n for n in corpus if _stem(n) == stem]
    return hits[0] if len(hits) == 1 else None


def _stem(name: str) -> str:
    return re.sub(r"\.(pdf|docx?|msg|txt|rtf|jpe?g|png|tiff?)$", "", name.strip(), flags=re.I).casefold()


class Verifier:
    def __init__(self, corpus: Mapping[str, DocPages], chk: ModuleType | None = None) -> None:
        self.corpus = corpus
        self.chk = chk if chk is not None else checker()
        self._tx: dict[str, _Transcript] = {}
        self._clean: dict[tuple[str, int], str] = {}

    def transcript(self, name: str) -> _Transcript:
        if name not in self._tx:
            self._tx[name] = _Transcript(self.corpus[name], self.chk)
        return self._tx[name]

    def clean(self, name: str, page: int) -> str:
        key = (name, page)
        if key not in self._clean:
            self._clean[key] = clean_page(
                self.corpus[name].pages.get(page, ""), self.transcript(name).is_transcript, self.chk
            )
        return self._clean[key]

    def find(self, c: Candidate, name: str) -> tuple[tuple[int, int] | None, int, str]:
        """(hit in the page's clean text, page, status): the cited page
        first, then (for an excerpt long enough not to be ambiguous) every
        other page of the same file. Moved only to the ONE other page that
        carries the words: words on several pages are ambiguous and rejected,
        never pinned to the lowest (2026-11-04: of 187 page-moved quotes in a
        control set, 21 landed on a wrong page that way; ``pincheck`` keeps
        the same rule)."""
        doc = self.corpus[name]
        if c.page in doc.pages:
            hit = locate(c.excerpt, self.clean(name, c.page))
            if hit is not None:
                return hit, c.page, "ok"
        if len(_words(c.excerpt)) >= RELOCATE_MIN_WORDS:
            found = []
            for p in sorted(doc.pages):
                if p == c.page:
                    continue
                got = locate(c.excerpt, self.clean(name, p))
                if got is not None:
                    found.append((got, p))
            if len(found) == 1:
                return found[0][0], found[0][1], "relocated"
            if found:
                return None, c.page, "ambiguous"
        return None, c.page, "rejected"

    def one(self, c: Candidate) -> tuple[dict[str, Any], tuple[str, int, int]]:
        """The fact's fields (without its id), and its sort key."""
        kind = c.kind if c.kind in KINDS else "fact"
        base: dict[str, Any] = {
            "file": c.file,
            "page": c.page,
            "kind": kind,
            "excerpt": " ".join(c.excerpt.split()),
            "claim": c.claim,
            "role": None,
            "tags": c.tags,
        }
        name = resolve(c.file, self.corpus)
        if name is None:
            return {**base, "status": "rejected", "note": "no such file in the record"}, (c.file, c.page, 0)
        base["file"] = name
        hit, page, status = self.find(c, name)
        if hit is None:
            long = len(_words(c.excerpt)) >= RELOCATE_MIN_WORDS
            why = "excerpt not found on the cited page" + (" or elsewhere in the file" if long else "")
            if status == "ambiguous":
                why = "excerpt not on the cited page, and on more than one other page of the file: ambiguous"
            return {**base, "status": "rejected", "note": why}, (name, c.page, 0)
        doc = self.corpus[name]
        clean = self.clean(name, page)
        a, b = widen(clean, *hit)
        excerpt = " ".join(clean[a:b].split())
        exact = " ".join(clean[hit[0] : _trail(clean, hit[1])].split())
        note = []
        if status == "relocated":
            note.append(f"cited p. {c.page}; the excerpt is on p. {page}")
        if " ".join(c.excerpt.split()).strip(" .,;:") not in exact:
            note.append("excerpt characters taken from the page")
        out: dict[str, Any] = {
            **base,
            "page": page,
            "excerpt": excerpt,
            "status": status,
            "machine": page in doc.machine,
            "page_sha": page_sha(doc.pages.get(page, "")),
        }
        note += self.values(c, name, kind, excerpt, out)
        out["note"] = "; ".join(note)
        return out, (name, page, a)

    def values(self, c: Candidate, name: str, kind: str, excerpt: str, out: dict[str, Any]) -> list[str]:
        """A figure's or date's value, testimony's page and line, set on
        ``out`` from the verified excerpt; the notes it adds."""
        note: list[str] = []
        if kind == "figure":
            got = figure_value(c, excerpt)
            if got is None:
                out["kind"] = "fact"
                note.append("figure value not printed in the excerpt; kept as a fact")
            else:
                out["value"], out["unit"] = got
                out["role"] = c.role if c.role in ROLES else "other"
        elif kind == "date":
            v = date_value(c, excerpt)
            if v is None:
                out["kind"] = "fact"
                note.append("date not printed in the excerpt; kept as a fact")
            else:
                out["value"] = v
        elif kind == "testimony":
            tx = self.transcript(name)
            loc = tx.locate(excerpt) if tx.is_transcript else None
            if loc is None:
                out["kind"] = "quote"
                note.append("transcript page and line not located; cited by file page")
            else:
                pa, la, pb, lb = loc
                out["tpage"], out["lines"] = pa, (la, lb)
                if pb != pa:
                    out["tags"] = tuple(t for t in c.tags if not t.startswith("tpage_end:")) + (f"tpage_end:{pb}",)
                if c.tpage is not None and (c.tpage, c.lines) != (pa, (la, lb)):
                    note.append(f"page and line located at {pa}:{la}")
        return note


def verify(candidates: Iterable[Candidate], corpus: Mapping[str, DocPages], chk: ModuleType | None = None) -> Ledger:
    """Every candidate verified or rejected, de-duplicated, ids assigned in
    corpus order (file order, then page, then position on the page)."""
    v = Verifier(corpus, chk)
    order = {n: i for i, n in enumerate(corpus)}
    seen: set[tuple[Any, ...]] = set()
    rows: list[tuple[tuple[int, int, int, int, str], dict[str, Any]]] = []
    for c in candidates:
        fields, (name, page, pos) = v.one(c)
        key = (fields["file"], fields["page"], fields["kind"], fields["excerpt"], fields.get("value"), fields["status"])
        if key in seen:
            continue
        seen.add(key)
        rejected = 1 if fields["status"] == "rejected" else 0
        rows.append(((rejected, order.get(name, len(order)), page, pos, fields["excerpt"]), fields))
    rows.sort(key=lambda r: r[0])
    facts = [Fact(id=f"F{i:04d}", **fields) for i, (_k, fields) in enumerate(rows, start=1)]
    return Ledger(tuple(facts))


def stats(ledger: Ledger) -> dict[str, int]:
    """Accept, relocate and reject counts, and the kind mix of usable facts."""
    out = {"ok": 0, "relocated": 0, "rejected": 0}
    for f in ledger:
        out[f.status] = out.get(f.status, 0) + 1
        if f.usable:
            out[f"kind:{f.kind}"] = out.get(f"kind:{f.kind}", 0) + 1
    return out
