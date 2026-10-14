"""`update_review`: review the update's added entries against their pages and
REPAIR what the review finds, before the document is assembled and proven.
Paid (the strongest tier, plus the audit loop's re-audit of what changed).

WHY (2026-10-14). The pre-delivery review (``stages/review.py``) only holds.
On two real matters independent reviewers, reading only the update and the
page images, found 13 and 15 MUST-FIX defects and could have corrected each
one from the page: what made the laptop's chronologies deliverable was exactly
that loop, review then fix against the images, until clean. This stage is that
loop, in the update lane:

1. REVIEW. The ``review`` tier (else ``judgment``) reads the added entries in
   parts sized to finish, each entry WITH the page images it cites, plus the
   delivered entries that share its date. A part whose reply runs out is split
   in half and reviewed again (at most SPLIT_DEPTH times); a single entry that
   cannot finish fails the stage resumably: nothing unreviewed goes on.
2. REPAIR. MUST findings are applied to the audit document as targeted edits
   to the anchored entries (``update/review_repair.py``: grounded or not
   applied; delivered entries never edited). SHOULD findings apply only as a
   grounded edit confined to one entry; otherwise they are recorded. A
   REDATE is the one edit to a date line: it applies only when the cited
   page's text states the corrected date (``page_text``), and the entry moves
   to its place in date order. The review reads the image; the page's text
   is the second, independent read a date is never changed without.
   UNCITED PAGES. Each added entry is shown, besides the pages it cites, the
   pages of the same new exhibit that follow its last cited page, up to the
   next page any added entry cites (or the exhibit's end), at most
   EXTRA_PAGES: on a real update a 6-page report reached compose as its first
   2 pages, the review saw only those, agreed with them, and passed an entry
   missing its exam, diagnoses, plan, causation opinion and signer. A finding
   that such a page records what the entry omits is repaired by an ``add``
   (``update/review_repair.py``) citing that page, then re-audited.
3. RE-AUDIT. The audit loop (``stages/audit_loop.py``) runs again: every
   corrected sentence is a new claim and is audited against its page.
4. LOOP. Only the edited entries and their neighbours are reviewed again, at
   most ROUNDS reviews. Exit when a review returns 0 MUST. Otherwise HOLD
   (``update_held:``, an SMD-finishing class in ``proof/holds.py``) listing the
   open findings for SMD. Never assembled, proven or filed with a MUST open.

Every call is a ledger row under stage ``update_review`` with the round in its
custom id; the driver's job cap binds each call (``before_request``), a
runaway guard. ``review_repair.json`` is the record: a rerun on the same
audit document does not pay again.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .. import llm, prompts
from ..audit.page_text import exhibit_paths
from ..audit.render import img_block, render
from ..proof import review as PR
from ..update import body as B, review_repair as RR, select as S
from . import audit_loop
from .base import StageRun, read_json
from .update_base import headings, update_dir

ROUNDS = 4  # reviews: up to three repairs, each verified by the next review
SPLIT_DEPTH = 4
PART_MAX_ENTRIES = 6
PART_MAX_IMAGES = 24
EXTRA_PAGES = 6  # uncited pages of the same document shown per entry, counted in the part
MAX_TOKENS = 32_000
RECORD = "review_repair.json"
STAGE = "update_review"

Images = Callable[[int, int], dict[str, Any] | None]


class ReviewIncomplete(RuntimeError):
    """A single entry's review did not finish: the stage fails resumably."""


@dataclass
class Ctx:
    sr: StageRun
    model: str
    system: str
    images: Images
    evidence: RR.Evidence | None = None
    #: anchor -> the uncited pages of its exhibits shown with it (``_extra``)
    extra: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    rnd: int = 0
    calls: int = 0
    tokens: int = 0


def _model(sr: StageRun) -> str:
    tiers = sr.cfg.get("models", "tiers") or {}
    return str(tiers.get("review") or llm.model_for(sr.cfg, "judgment"))


def _image_source(sr: StageRun) -> Images:
    out = sr.slug_dir / "out" / sr.unit.unit
    pdfs = exhibit_paths(out)

    def get(ex: int, page: int) -> dict[str, Any] | None:
        if ex not in pdfs:
            return None
        p = render(pdfs[ex], page, out / "auditpages", f"ex{ex}")
        return img_block(p) if p else None

    return get


def page_text(sr: StageRun) -> RR.Evidence:
    """(exhibit, page) -> the text the update read off that page of an added
    exhibit (``text/<id>.txt``, in the exhibit's final number), or None."""
    cache: dict[int, dict[int, str]] = {}
    for r in read_json(update_dir(sr) / "new_exhibits.final.json", []):
        ex, doc = (r.get("exhibit"), r.get("id")) if isinstance(r, dict) else (None, None)
        tp = sr.slug_dir / "text" / f"{doc}.txt"
        if isinstance(ex, int) and isinstance(doc, str) and tp.is_file():
            cache[ex] = S.pages_of_text(tp.read_text(encoding="utf-8", errors="replace"))

    def get(ex: int, page: int) -> str | None:
        return cache.get(ex, {}).get(page)

    return get


def _extra(c: Ctx, e: RR.Entry, cited: set[tuple[int, int]], keep: set[int]) -> list[tuple[int, int]]:
    """The pages of each added exhibit `e` cites that follow its last cited
    page there, up to the next page any added entry cites or the exhibit's
    end, at most EXTRA_PAGES in all: what the entry's document says that the
    entry may have left out."""
    out: list[tuple[int, int]] = []
    for ex in sorted({x for x, _ in e.cites() if x in keep}):
        p = max(pg for x, pg in e.cites() if x == ex) + 1
        while len(out) < EXTRA_PAGES and (ex, p) not in cited and c.images(ex, p) is not None:
            out.append((ex, p))
            p += 1
    return out


def _same_date(base_md: str, ents: list[RR.Entry]) -> list[tuple[str, str]]:
    want = {B.iso(e.text) for e in ents}
    _, delivered = B.entries_of(base_md)
    return [(f"D{i}", d) for i, d in enumerate(delivered, 1) if B.iso(d) in want]


def _content(c: Ctx, ents: list[RR.Entry], delivered: list[tuple[str, str]]) -> list[dict[str, Any]]:
    u = c.sr.unit
    out: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": f"CLIENT: {u.client_name}; date of birth {u.dob or 'not known'}; "
            f"incident {c.sr.job.incident_date}\n\nADDED ENTRIES IN THIS PART: {', '.join(e.anchor for e in ents)}",
        }
    ]
    shown = 0
    for e in ents:
        claims = "\n".join(f"{x.anchor}: {x.text}" for x in e.claims) or "(no cited claim)"
        out.append({"type": "text", "text": f"=== {e.anchor} (ADDED) ===\n{e.text}\n\nCLAIMS:\n{claims}"})
        extra = c.extra.get(e.anchor) or []
        if extra:
            out.append({"type": "text", "text": f"UNCITED PAGES OF THE SAME DOCUMENT follow {e.anchor}'s cited pages."})
        for (ex, pg), label in [(x, "") for x in sorted(e.cites())] + [(x, " (uncited)") for x in extra]:
            if shown >= PART_MAX_IMAGES:
                out.append({"type": "text", "text": f"Exhibit {ex} p. {pg}{label}: not shown in this part"})
                continue
            img = c.images(ex, pg)
            if img is not None:
                out += [{"type": "text", "text": f"Exhibit {ex} p. {pg}{label}:"}, img]
                shown += 1
    if delivered:
        out.append(
            {
                "type": "text",
                "text": "DELIVERED ENTRIES ON THE SAME DATES (read only):\n\n"
                + "\n\n".join(f"=== {a} (DELIVERED) ===\n{t}" for a, t in delivered),
            }
        )
    return out


def _parts(ents: list[RR.Entry], extra: dict[str, list[tuple[int, int]]] | None = None) -> list[list[RR.Entry]]:
    """Added entries in parts of at most PART_MAX_ENTRIES entries and about
    PART_MAX_IMAGES pages shown (cited and uncited): sized to finish."""
    out: list[list[RR.Entry]] = []
    cur: list[RR.Entry] = []
    pages = 0
    for e in ents:
        n = len(e.cites()) + len((extra or {}).get(e.anchor) or [])
        if cur and (len(cur) >= PART_MAX_ENTRIES or pages + n > PART_MAX_IMAGES):
            out.append(cur)
            cur, pages = [], 0
        cur.append(e)
        pages += n
    return [*out, cur] if cur else out


def _review_part(c: Ctx, ents: list[RR.Entry], base_md: str, key: str, depth: int = 0) -> list[RR.Finding]:
    """One part's findings. A reply that runs out or does not parse is split
    in half and reviewed again (the part is too big to finish); a single entry
    that still does not finish raises ReviewIncomplete."""
    for attempt in (1, 2):
        r = c.sr.doorway.call(
            STAGE,
            model=c.model,
            system=c.system,
            messages=[{"role": "user", "content": _content(c, ents, _same_date(base_md, ents))}],
            max_tokens=MAX_TOKENS,
            stream=True,
            effort="high",
            custom_id=f"{STAGE}-r{c.rnd}-{key}-{attempt}",
        )
        c.calls += 1
        c.tokens += sum(llm_usage(r.usage).values())
        if r.stop_reason != "max_tokens":
            try:
                return RR.parse_reply(r.text)
            except RR.ReplyUnparsed as exc:
                c.sr.log(f"  {key}: {exc}")
        if len(ents) > 1 and depth < SPLIT_DEPTH:
            h = len(ents) // 2
            return _review_part(c, ents[:h], base_md, f"{key}a", depth + 1) + _review_part(
                c, ents[h:], base_md, f"{key}b", depth + 1
            )
    raise ReviewIncomplete(f"the review of {ents[0].anchor} did not finish")


def llm_usage(usage: Any) -> dict[str, int]:
    from ..ledger import usage_dict

    try:
        return usage_dict(usage)
    except (AttributeError, TypeError):
        return {}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _keep(sr: StageRun) -> set[int]:
    return set(exhibit_paths(sr.slug_dir / "out" / sr.unit.unit))


def review_round(c: Ctx, md: str, base_md: str, targets: set[int] | None) -> tuple[list[RR.Finding], int]:
    """Review the targeted added entries (all when None). Returns the findings
    and the number of entries reviewed."""
    keep = _keep(c.sr)
    ents = RR.anchor_entries(md, keep, headings(c.sr.cfg))
    cited = {x for e in ents for x in e.cites()}
    pick = [e for i, e in enumerate(ents) if targets is None or i in targets]
    c.extra = {e.anchor: _extra(c, e, cited, keep) for e in pick}
    found: list[RR.Finding] = []
    for i, part in enumerate(_parts(pick, c.extra), 1):
        found += _review_part(c, part, base_md, f"p{i}")
    return found, len(pick)


def _must(found: list[RR.Finding]) -> list[RR.Finding]:
    """MUST findings about the added text (a delivered entry's is reported only)."""
    return [f for f in found if f.severity == RR.MUST and not f.anchor.startswith("D")]


def _hold_line(open_: list[RR.Finding]) -> str:
    first = "; ".join(f"{f.anchor} {f.klass}: {f.problem}" for f in open_[:4])
    return (
        f"update_held: the review of the update left {len(open_)} must-fix finding(s) its repair rounds could "
        f"not settle against the pages; SMD finishes it: {first}"
    )[:480]


def _targets(md: str, sr: StageRun, edited: set[int], found: list[RR.Finding]) -> set[int]:
    """The edited entries, every entry a MUST finding was left open on, and
    their neighbours: what the next review reads."""
    ents = RR.anchor_entries(md, _keep(sr), headings(sr.cfg))
    left = {f.anchor.split(".")[0] for f in _must(found) if f.outcome != "applied"}
    hit = edited | {i for i, e in enumerate(ents) if e.anchor in left}
    return {j for i in hit for j in (i - 1, i, i + 1) if 0 <= j < len(ents)}


def _reuse(sr: StageRun, rec: dict[str, Any], md: str) -> int | None:
    """A rerun on the audit document this stage already settled: not paid again."""
    if rec.get("doc_sha_out") != _sha(md):
        return None
    if rec.get("status") == "clean":
        sr.log("update review: the audit document is the one already reviewed clean; not paid again")
        return 0
    if rec.get("status") == "held":
        if PR.release_requested(sr.job.path.parent) is not None:
            sr.log("update review: released by SMD; the open findings stay in the record")
            return 0
        if not (sr.job.path.parent / PR.REDO_MARKER).is_file():
            sr.log(_hold_line([RR.Finding(**f) for f in rec.get("open") or []]))
            return 2
    return None


def _repair(c: Ctx, md: str, found: list[RR.Finding], doc: Path) -> tuple[str, set[int], int]:
    """Apply the round's grounded findings and re-audit what changed. Returns
    the document, the edited entries and the audit loop's exit (0 when clean)."""
    new_md, edited = RR.apply(md, found, _keep(c.sr), headings(c.sr.cfg), c.evidence, c.extra)
    for f in found:
        if f.action == "redate":
            c.sr.log(f"  redate {f.anchor} to {f.corrected} (Exhibit {f.exhibit} p. {f.page}): {f.outcome}")
    if new_md == md:
        return md, edited, 0
    doc.write_text(new_md, encoding="utf-8")
    rc = audit_loop.run(c.sr)  # every corrected sentence is a new claim, audited on its page
    return doc.read_text(encoding="utf-8"), edited, rc


def run(sr: StageRun, *, images: Images | None = None, evidence: RR.Evidence | None = None) -> int:
    ud, doc = update_dir(sr), sr.slug_dir / "runs" / sr.unit.unit / "final-chronology.md"
    md = doc.read_text(encoding="utf-8")
    again = _reuse(sr, read_json(ud / RECORD, {}), md)
    if again is not None:
        return again
    base_md = (ud / "base.md").read_text(encoding="utf-8")
    c = Ctx(sr, _model(sr), prompts.load("update-review-system", sr.cfg), images or _image_source(sr))
    c.evidence = evidence or page_text(sr)
    rec: dict[str, Any] = {"model": c.model, "doc_sha_in": _sha(md), "rounds": []}
    targets: set[int] | None = None
    found: list[RR.Finding] = []
    for rnd in range(1, ROUNDS + 1):
        c.rnd, c.calls, c.tokens = rnd, 0, 0
        try:
            found, n = review_round(c, md, base_md, targets)
        except ReviewIncomplete as exc:
            sr.log(f"update review: {exc}; nothing goes on, a rerun reviews again")
            return 1
        must = _must(found)
        row: dict[str, Any] = {"round": rnd, "entries": n}
        rec["rounds"].append(row)
        if not must or rnd == ROUNDS:
            # Clean, or the last review: nothing is applied that no later review would read.
            row.update(calls=c.calls, tokens=c.tokens, findings=[f.as_dict() for f in found])
            sr.log(f"update review round {rnd}/{ROUNDS}: {n} entr(ies), {len(must)} must-fix; {c.calls} call(s)")
            if not must:
                _save(ud / RECORD, rec, "clean", doc)
                return 0
            break
        md, edited, rc = _repair(c, md, found, doc)
        row.update(calls=c.calls, tokens=c.tokens, findings=[f.as_dict() for f in found])
        sr.log(
            f"update review round {rnd}/{ROUNDS}: {n} entr(ies), {len(must)} must-fix, "
            f"{sum(f.outcome == 'applied' for f in found)} correction(s) applied; {c.calls} call(s), {c.tokens} token(s)"
        )
        if rc != 0:
            _save(ud / RECORD, rec, "audit_failed", doc)
            return 3 if rc == 1 else 4
        targets = _targets(md, sr, edited, found)
    open_ = _must(found)
    _save(ud / RECORD, rec, "held", doc, open_)
    for f in open_:
        sr.log(f"  OPEN {f.anchor} [{f.klass}] Exhibit {f.exhibit} p. {f.page}: {f.problem}")
    sr.log(_hold_line(open_))
    return 2


def _save(path: Path, rec: dict[str, Any], status: str, doc: Path, open_: list[RR.Finding] | None = None) -> None:
    rec["status"] = status
    rec["doc_sha_out"] = _sha(doc.read_text(encoding="utf-8"))
    rec["open"] = [f.as_dict() for f in open_ or []]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, indent=1), encoding="utf-8")
