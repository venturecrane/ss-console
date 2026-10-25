"""`exhibit_merge`: deliver a record set once when two exhibits hold the same
pages. $0, after ``strip_apply`` and before the gates.

WHY (2026-10-25, the Pamplona job). After the paid audit a chronology was
about to ship with duplicate exhibits. Exhibit 4 was page for page Exhibit 3:
the firm had saved one 558-page Kaiser ER PDF twice under two names, and the
two files differed by 8 bytes, so the pull's sha256 dedupe (stages/download)
kept both. Exhibit 7 held the same 547 pages as Exhibit 6 in another order:
two overlapping UC Davis exports, the same text drawn with different pixels.
The Captain decided: merge before delivery, renumber, disclose. Nothing in the
package may be invented, so a merge only re-points a citation to an identical
page, deletes a paragraph that became an exact duplicate, and discloses the
merge in Records Reviewed and Limitations.

THE RULE, measured on the real stripped exhibits. A page's signature is its
whitespace-normalized text when it carries at least ``MIN_TEXT`` characters,
else the hash of its 72-dpi grayscale rendering. Exhibit B (the higher number)
merges into an earlier A only when EVERY page of B pairs one to one with an
unused page of A of the same signature (Pamplona: 558/558 and 547/547, 69
low-text pages paired by pixels). Partial containment never merges; it is
logged. Pixel identity is not asked of a text page: the UC Davis copies carry
the same words in different pixels, and the words are the record.

THE FALSIFIER RIDES IN THE STAGE. A pairing that also holds with B shifted by
one page proves nothing (a run of near-identical template pages): for a
merging pair, the mapping shifted by one B page must be identical on fewer
than ``FALSIFY_MAX`` of its pages (Pamplona: 31/558 and 0/547), or the stage
refuses (``EXIT_BLIND``). A one-page exhibit cannot be shifted and is never
merged. A citation to a page of B outside B cannot be re-pointed, so that pair
is left unmerged and logged.

THE APPLY TOUCHES A CLOSED LIST (``ARTEFACTS``), and every other file that
carries an exhibit number is named in ``NOT_REWRITTEN`` with the reason it is
not; tests/test_exhibit_merge.py enumerates a real run's tree against both.
The audit's verdicts carry across: each real verdict whose claim is still in
the document is copied under its new key (``rekeyed_from``), so the audit that
runs after this spends nothing on claims it already verified
(``uncached_claims``). An interrupted apply leaves ``exhibit_merges.json.part``
and the next attempt refuses rather than guess which files moved.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..audit import claims as CL
from . import exhibit_merge_io as IO, limitations
from .base import StageRefusal, StageRun, read_json
from .build_doc import ENTRY
from .classify import resolve_exhibit_files
from .exhibit_merge_io import (  # noqa: F401 - re-exported: the stage's public names live here
    ARTEFACTS,
    NOT_REWRITTEN,
    copy_verdicts,
    uncached_claims,
)
from .limitations_rules import MERGES_FILE
from .strip import CITE, PAGESPEC, compress, expand

MIN_TEXT = 40
DPI = 72
FALSIFY_MAX = 0.10
#: The shifted mapping is as identical as the real one: the check cannot fail.
EXIT_BLIND = 1
PART = MERGES_FILE + ".part"


# ---- signatures --------------------------------------------------------------
def norm(text: str) -> str:
    return " ".join((text or "").split())


def text_sig(text: str) -> str | None:
    """The signature of a page with text, or None below ``MIN_TEXT``."""
    t = norm(text)
    return "t:" + hashlib.sha256(t.encode()).hexdigest() if len(t) >= MIN_TEXT else None


def pixel_sig(page: Any) -> str:
    import pymupdf

    pix = page.get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY)
    h = hashlib.sha256(f"{pix.width}x{pix.height}:".encode())
    h.update(pix.samples)
    return "p:" + h.hexdigest()


class Sigs:
    """One file's page signatures, text first and pixels only when asked:
    rendering every low-text page of a 4,000-page matter is the cost this
    keeps to candidate pairs. ``texts`` (one per page) replaces the PDF's own
    text layer; ``path`` None means a file with no pages to render."""

    def __init__(self, path: Path | None, texts: list[str] | None = None) -> None:
        self.path = path
        if texts is None:
            import pymupdf

            with pymupdf.open(str(path)) as doc:
                texts = [p.get_text() for p in doc]
        self.text = [text_sig(t) for t in texts]
        self._full: list[str] | None = None

    def __len__(self) -> int:
        return len(self.text)

    def full(self) -> list[str] | None:
        """Every page's signature, or None when a low-text page cannot be drawn."""
        if self._full is None:
            if None not in self.text:
                self._full = [s for s in self.text if s]
            elif self.path is None:
                return None
            else:
                import pymupdf

                with pymupdf.open(str(self.path)) as doc:
                    if len(doc) != len(self.text):
                        return None
                    self._full = [s or pixel_sig(doc[i]) for i, s in enumerate(self.text)]
        return self._full


def maybe_contained(b: Sigs, a: Sigs) -> bool:
    """The cheap test: B's text signatures are a sub-multiset of A's, and A has
    at least as many low-text pages."""
    if not len(b) or len(b) > len(a):
        return False
    tb, ta = Counter(s for s in b.text if s), Counter(s for s in a.text if s)
    return all(ta[k] >= n for k, n in tb.items()) and b.text.count(None) <= a.text.count(None)


def one_to_one(b: list[str], a: list[str]) -> list[int] | None:
    """Each B page (in order) paired with the first unused A page of the same
    signature: the A index per B page, or None when a B page has no partner."""
    free: dict[str, list[int]] = {}
    for i, s in enumerate(a):
        free.setdefault(s, []).append(i)
    out: list[int] = []
    for s in b:
        slots = free.get(s)
        if not slots:
            return None
        out.append(slots.pop(0))
    return out


def falsified(b: list[str], a: list[str], mapping: list[int]) -> tuple[bool | None, str]:
    """Shift the mapping by one B page (B page i+1 against the A page B page i
    paired with): True when that is identical on fewer than ``FALSIFY_MAX`` of
    the pages compared, False when the check cannot fail, None with one page."""
    n = len(b) - 1
    if n < 1:
        return None, "one page: nothing to shift"
    hits = sum(1 for i in range(n) if b[i + 1] == a[mapping[i]])
    ok = hits < FALSIFY_MAX * n
    return ok, f"shifted by one page, {hits}/{n} identical ({'discriminates' if ok else 'CANNOT FAIL'})"


class Blind(RuntimeError):
    """The falsifier: a shifted mapping is as identical as the real one."""


def contained(b: Sigs, a: Sigs) -> tuple[list[int] | None, str]:
    """(B page -> A index, why) when every page of B pairs one to one with A
    and the falsifier discriminates; (None, why) otherwise. Raises ``Blind``
    when B pairs but the shifted mapping pairs too."""
    if not maybe_contained(b, a):
        tb, ta = Counter(s for s in b.text if s), Counter(s for s in a.text if s)
        shared = sum(min(n, ta[k]) for k, n in tb.items())
        return None, (f"partial: {shared} of its {len(b)} pages are also there" if 2 * shared >= len(b) > 0 else "no")
    fb, fa = b.full(), a.full()
    if fb is None or fa is None:
        return None, "a low-text page could not be drawn"
    mapping = one_to_one(fb, fa)
    if mapping is None:
        return None, "its pages overlap but not all of them pair"
    ok, why = falsified(fb, fa, mapping)
    if ok is None:
        return None, why
    if not ok:
        raise Blind(why)
    return mapping, why


# ---- detection ---------------------------------------------------------------
@dataclass
class Merge:
    kept: int  # A, old number
    dropped: int  # B, old number
    pages: list[int]  # B page i+1 (post-strip) -> A page pages[i] (post-strip)


def detect(paths: dict[int, Path], log: Callable[[str], None]) -> list[Merge]:
    """Every B that merges into an earlier A. A dropped exhibit is never a
    keeper, so merges do not chain. Raises ``Blind``."""
    sigs = {ex: Sigs(p) for ex, p in sorted(paths.items())}
    merges: list[Merge] = []
    for b in sorted(sigs):
        for a in sorted(x for x in sigs if x < b and x not in {m.dropped for m in merges}):
            try:
                mapping, why = contained(sigs[b], sigs[a])
            except Blind as exc:
                raise Blind(f"Exhibit {b} pairs with Exhibit {a} page for page, but {exc}") from None
            if mapping is None:
                if why != "no":
                    log(f"Exhibit {b} against Exhibit {a}: {why}; not merged")
                continue
            log(f"Exhibit {b}: all {len(mapping)} page(s) pair one to one with Exhibit {a}; {why}")
            merges.append(Merge(a, b, [i + 1 for i in mapping]))
            break
    return merges


# ---- numbering -----------------------------------------------------------------
@dataclass
class Plan:
    merges: list[Merge]
    kept_pre: dict[int, list[int]] = field(default_factory=dict)  # post page i+1 -> pre-strip page

    @property
    def dropped(self) -> dict[int, Merge]:
        return {m.dropped: m for m in self.merges}

    def new(self, ex: int) -> int:
        """The new number of an exhibit (a dropped one: its keeper's)."""
        m = self.dropped.get(ex)
        if m is not None:
            ex = m.kept
        return ex - sum(1 for d in self.dropped if d < ex)

    def page(self, ex: int, p: int) -> int | None:
        """A post-strip page of ``ex`` as the page it is in its new exhibit."""
        m = self.dropped.get(ex)
        if m is None:
            return p
        return m.pages[p - 1] if 1 <= p <= len(m.pages) else None

    def pre_page(self, ex: int, p: int) -> int | None:
        """A PRE-strip page of ``ex`` (the entries files) as its keeper's."""
        m = self.dropped.get(ex)
        if m is None:
            return p
        kb, ka = self.kept_pre.get(ex) or [], self.kept_pre.get(m.kept) or []
        post = self.page(ex, kb.index(p) + 1) if p in kb else None
        return ka[post - 1] if post and 1 <= post <= len(ka) else None


def _strip_result_path(sr: StageRun) -> Path:
    return sr.slug_dir / f"strip_result{_sfx(sr)}.json"


def _sfx(sr: StageRun) -> str:
    return f"-{sr.unit.unit}" if sr.job.joint else ""


def kept_pre(sr: StageRun, pm: list[dict[str, Any]], counts: dict[int, int]) -> dict[int, list[int]]:
    """Per exhibit, the pre-strip page behind each current page (the strip's
    drops, applied only where the exhibit was replaced: ``audit/diag``'s rule).
    An exhibit whose count does not reconcile is left out, and the entries
    files then renumber its citations by number only."""
    out_dir = sr.slug_dir / "out" / sr.unit.unit
    result = read_json(_strip_result_path(sr), {}) or {}
    origs = [p.name for p in out_dir.iterdir() if p.name.endswith(".pdf.orig")]
    out: dict[int, list[int]] = {}
    for e in pm:
        ex, total = int(e["exhibit"]), int(e.get("total_pages") or 0)
        stripped = any(o.startswith(f"Exhibit {ex} - ") for o in origs)
        drops = set((result.get("drops") or {}).get(str(ex)) or []) if stripped else set()
        kept = [p for p in range(1, total + 1) if p not in drops]
        if len(kept) == counts.get(ex):
            out[ex] = kept
    return out


# ---- the documents ---------------------------------------------------------------
TABLE_REF = re.compile(r"(?<=\|)(\s*)Exhibit (\d+)(?: - p\. (" + PAGESPEC + r"))?(?=[^|\n]*\|)")


def rewrite(text: str, plan: Plan, page_fn: Callable[[int, int], int | None]) -> tuple[str, list[str]]:
    """(text with every prose citation and table Reference cell renumbered,
    a dropped exhibit's citations re-pointed through ``page_fn``; the pages
    that could not be mapped, which keep their number). A citation to a kept
    exhibit keeps its page text exactly."""
    misses: list[str] = []

    def pages(ex: int, spec: str) -> str:
        new: list[int] = []
        for p in expand(spec):
            q = page_fn(ex, p)
            if q is None:
                misses.append(f"Exhibit {ex} p.{p}")
            new.append(p if q is None else q)
        return compress(new)

    def prose(m: re.Match) -> str:
        ex, spec, tail = int(m.group(1)), m.group(2), m.group(3)
        if ex not in plan.dropped or not spec:
            return f"(Exhibit {plan.new(ex)}" + m.group(0)[len(f"(Exhibit {ex}") :]
        return f"(Exhibit {plan.new(ex)} - p. {pages(ex, spec)}{tail})"

    def cell(m: re.Match) -> str:
        lead, ex, spec = m.group(1), int(m.group(2)), m.group(3)
        if ex not in plan.dropped or not spec:
            return f"{lead}Exhibit {plan.new(ex)}" + (f" - p. {spec}" if spec else "")
        return f"{lead}Exhibit {plan.new(ex)} - p. {pages(ex, spec)}"

    out = CITE.sub(prose, text)
    return "\n".join(TABLE_REF.sub(cell, ln) if ln.lstrip().startswith("|") else ln for ln in out.split("\n")), misses


def cited_pages(text: str) -> dict[int, set[int]]:
    """Every page each exhibit is cited at, in prose and in table cells."""
    out: dict[int, set[int]] = {}
    for m in CITE.finditer(text):
        if m.group(2):
            out.setdefault(int(m.group(1)), set()).update(expand(m.group(2)))
    for ln in text.split("\n"):
        if ln.lstrip().startswith("|"):
            for m in TABLE_REF.finditer(ln):
                if m.group(3):
                    out.setdefault(int(m.group(2)), set()).update(expand(m.group(3)))
    return out


def dedupe(text: str) -> tuple[str, int]:
    """(text, paragraphs deleted). Within each entry block (and the block ahead
    of the first entry), a cited paragraph identical, whitespace aside, to an
    earlier one of the same block is deleted; so is a heading paragraph that
    repeats an earlier one once every cited paragraph under it is deleted.
    Deletion only: nothing is reworded."""
    out: list[str] = []
    gone = 0
    for block in ENTRY.split(text):
        pars = block.split("\n\n")
        keep = [True] * len(pars)
        seen: set[str] = set()
        heads: set[str] = set()
        head: tuple[int, bool] | None = None  # (index, repeats an earlier heading)
        under: list[int] = []

        def close() -> None:
            if head is not None and head[1] and under and not any(keep[i] for i in under):
                keep[head[0]] = False

        for i, par in enumerate(pars):
            key = norm(par)
            if not key:
                continue
            if CITE.search(par) is None:
                close()
                head, under = (i, key in heads), []
                heads.add(key)
                continue
            under.append(i)
            if key in seen:
                keep[i] = False
            seen.add(key)
        close()
        gone += keep.count(False)
        out.append("\n\n".join(p for p, k in zip(pars, keep) if k))
    return "".join(out), gone


def exhibit_list(md: str, pm: list[dict[str, Any]]) -> str:
    """The Exhibit List section rebuilt from the page map, in build_doc's form."""
    at = md.find("## Exhibit List")
    if at < 0:
        return md
    end = md.find("\n" + limitations.HEADING, at)
    rows = "".join(f"| {e['exhibit']} | {e['title'].split(' - ', 1)[1]} |\n" for e in pm)
    return md[:at] + "## Exhibit List\n\n| Exhibit No. | Description |\n" + rows + (md[end:] if end >= 0 else "")


def chronology(md: str, plan: Plan) -> tuple[str, list[str], int]:
    """(the document re-pointed and deduplicated up to its Exhibit List,
    unmapped pages, paragraphs deleted). The Exhibit List and Limitations are
    rebuilt by the caller from the artefacts."""
    at = md.find("## Exhibit List")
    head, tail = (md[:at], md[at:]) if at >= 0 else (md, "")
    head, misses = rewrite(head, plan, plan.page)
    gone = 0
    if CL.BODY_START in head:
        top, body = head.split(CL.BODY_START, 1)
        body, gone = dedupe(body)
        head = top + CL.BODY_START + body
    return head + tail, misses, gone


# ---- the stage ---------------------------------------------------------------------
def _cites_fit(m: Merge, cited: dict[int, set[int]], log: Callable[[str], None]) -> bool:
    """Every page of B the document cites has a partner in A. A citation past
    B's last page cannot be re-pointed (the audit grades it out of range)."""
    bad = sorted(p for p in cited.get(m.dropped, set()) if not 1 <= p <= len(m.pages))
    if bad:
        log(f"Exhibit {m.dropped}: cited at page(s) {compress(bad)} outside its {len(m.pages)} pages; not merged")
    return not bad


def _entries(rd: Path, plan: Plan, log: Callable[[str], None]) -> None:
    """The assembled bodies the gates read (pre-strip pages): renumbered, B's
    pages mapped through the strip into A's, the same duplicates deleted."""
    for name in ("entries_scoped_final.md", "entries_final.md"):
        p = rd / name
        if not p.is_file():
            continue
        text, misses = rewrite(p.read_text(encoding="utf-8"), plan, plan.pre_page)
        if misses:
            log(f"  {name}: {len(misses)} page(s) not mapped through the strip; renumbered by exhibit only")
        text, gone = dedupe(text)
        IO.write_text(p, text)
        log(f"  {name}: renumbered, {gone} duplicate paragraph(s) deleted")


def apply(sr: StageRun, plan: Plan, pm: list[dict[str, Any]], md: str, before: set[int]) -> None:
    """Every artefact on ``exhibit_merge_io.ARTEFACTS``; the part marker is
    removed only when all of it is written."""
    d, unit = sr.slug_dir, sr.unit.unit
    out, rd = d / "out" / unit, d / "runs" / unit
    cpath = rd / "final-chronology.md"
    IO.write_json(rd / PART, {"merges": [m.__dict__ for m in plan.merges]})
    new_md, _misses, gone = chronology(md, plan)
    pm_new = IO.page_map(pm, plan)
    _entries(rd, plan, sr.log)
    IO.write_json(out / "page_map.json", pm_new)
    IO.slug_files(sr, plan)
    IO.rename_pdfs(out, plan, sr.log)
    n = IO.clear_cache(out)
    new_md = exhibit_list(new_md, pm_new)
    IO.write_text(cpath, new_md)
    results = out / "audit-results.jsonl"
    keymap: dict[str, str] = {}
    if results.is_file():
        keymap = IO.copy_verdicts(results, md, new_md, plan, (before, {int(e["exhibit"]) for e in pm_new}))
    IO.ledgers(out, plan, keymap)
    IO.record(rd, plan, pm, pm_new)
    limitations.refresh(cpath, d, unit, sr.log, sr.cfg)
    (rd / PART).unlink()
    for m in plan.merges:
        sr.log(f"merged Exhibit {m.dropped} into Exhibit {m.kept}, now Exhibit {plan.new(m.kept)} ({len(m.pages)} pp.)")
    sr.log(
        f"{len(pm)} -> {len(pm_new)} exhibits; {gone} duplicate paragraph(s) deleted; {len(keymap)} claim(s) "
        f"mapped to their new keys; {n} cached page render(s) cleared"
    )


def run(sr: StageRun) -> int:
    import pymupdf

    out, rd = sr.slug_dir / "out" / sr.unit.unit, sr.slug_dir / "runs" / sr.unit.unit
    if (rd / PART).exists():
        raise StageRefusal(
            f"exhibit_merge: an earlier merge stopped part way ({PART} is on file), so the exhibits and the "
            "chronology may disagree; nothing is merged again until a person has looked"
        )
    pm = read_json(out / "page_map.json", None)
    if not isinstance(pm, list) or len(pm) < 2:
        sr.log("fewer than two exhibits; nothing to compare")
        return 0
    paths = resolve_exhibit_files(out)
    try:
        merges = detect(paths, sr.log)
    except Blind as exc:
        sr.log(f"FALSIFIER: {exc}; a pairing that cannot fail proves nothing, so nothing is merged")
        return EXIT_BLIND
    md = (rd / "final-chronology.md").read_text(encoding="utf-8")
    cited = cited_pages(md.split("## Exhibit List", 1)[0])
    merges = [m for m in merges if _cites_fit(m, cited, sr.log)]
    if not merges:
        sr.log(f"{len(paths)} exhibits: none holds the same pages as another")
        return 0
    counts: dict[int, int] = {}
    for ex, p in paths.items():
        with pymupdf.open(str(p)) as doc:
            counts[ex] = len(doc)
    apply(sr, Plan(merges, kept_pre(sr, pm, counts)), pm, md, set(paths))
    return 0
