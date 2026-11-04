"""The source check over the drafter's MARKED-UP text: the judgment errors
code cannot rule out by construction.

Code already owns every quote, figure, date and pin (``evidence.render``).
What it cannot own is whether the sentence AROUND them says what the record
says, whether the record carries something that cuts against it, and whether
an authority holds what the brief says. Per section (``view.sections``):

* **characterization**: each sentence that names fact ids, shown as it will
  print (``view.preview``), against those facts' EXCERPTS and the pages around
  them (``evidence_for``). Never ``Fact.claim``: the claim is the extraction
  model's words, and checking prose against a model's words is the digest
  check that passed 23 wrong items (2026-11-04).
* **omission**: code builds contrary candidates from the FULL ledger, the
  facts the index trimmed included (``contrary``): same document and nearby
  page, same date, shared tags, cited nowhere in the brief. The model says
  which cut against the sentence; those are ``disclosed`` in the attorney
  notes. Never blocking: the attorney weighs them.
* **authority**: each ``{{L:slug}}`` sentence against the store's primary
  text only. ``{{L:?|...}}`` or a slug the store lacks is ``could_not_verify``
  without a model call, and lands in ``authorities-wanted.json``.
* **adversary**: the whole document read as opposing counsel, then as the
  mediator; a contradiction is ``wrong``, an overstatement or omission
  ``framing``.

Every ``wrong`` is settled two-of-two (``adjudicate``). Planted controls run
inside the real batches (``controls_adapter``); INVALID fails the job. Each
graded claim is cached by sha(sentence + ids + page text) (``items.Cache``).
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Iterable

from ..evidence.shapes import Fact, Ledger
from . import adjudicate as adj
from . import authorities as auth
from . import controls_adapter as ctl
from . import prompts, view
from .items import Ask, Block, Cache, Item, Report, apply_grade, item_key, parse_json
from .sources import Sources, norm, sha

REPORT_FILE = "check.json"
#: Items per characterization call, and the evidence one call carries.
BATCH_ITEMS = 12
BATCH_CHARS = 200_000
#: Fact pages shown per item (the cited pages and either side), at most.
ITEM_PAGES = 6
#: Contrary candidates per sentence.
CANDIDATES = 5
EXCERPT_SHOWN = 600


def evidence_for(ids: Iterable[str], ledger: Ledger, sources: Sources) -> list[Block]:
    """What a sentence's ids rest on: each fact's excerpt (the page's own
    characters) and the text of the pages around it. Nothing else."""
    out: list[Block] = []
    refs: list[tuple[str, int]] = []
    for fid in ids:
        f = ledger.get(fid)
        if f is None or not f.usable:
            continue
        where = f"{f.file} p.{f.page}" + (f" (transcript {f.tpage}:{f.lines[0]})" if f.tpage and f.lines else "")
        reading = f"\nIMAGE READING OF THE PAGE: {f.image_reading}" if f.image_reading else ""
        out.append(Block(f"{fid} excerpt", f"{fid}, {where}, the page's own words: «{f.excerpt}»{reading}"))
        name = sources.resolve(f.file)
        if name:
            refs += [(name, p) for p in sources.window(name, [f.page]) if (name, p) not in refs]
    for name, p in refs[:ITEM_PAGES]:
        out.append(Block(f"{name} p.{p}", sources.page_block(name, p)))
    return out


def page_sha(ids: Iterable[str], ledger: Ledger, sources: Sources) -> str:
    parts = []
    for fid in ids:
        f = ledger.get(fid)
        if f is None:
            parts.append(f"{fid}|missing")
            continue
        name = sources.resolve(f.file) or f.file
        parts.append(
            f"{fid}|{f.status}|{sha(f.excerpt)}|{sources.pages_sha((name, p) for p in sources.window(name, [f.page]))}"
        )
    return sha("\n".join(parts))


def contrary(ids: tuple[str, ...], ledger: Ledger, cited_anywhere: set[str], limit: int = CANDIDATES) -> list[Fact]:
    """Facts the brief cites nowhere that share a document and nearby page, a
    date, or tags with the sentence's facts: where a contrary record sits."""
    mine = [f for f in (ledger.get(i) for i in ids) if f is not None and f.usable]
    if not mine:
        return []
    scored: list[tuple[int, str, Fact]] = []
    for c in ledger.usable():
        if c.id in cited_anywhere or c.id in ids:
            continue
        score = 0
        for f in mine:
            if c.file == f.file:
                score += 2 if abs(c.page - f.page) <= 2 else 1
            if c.kind == "date" and f.kind == "date" and c.value and c.value == f.value:
                score += 2
            score += len(set(c.tags) & set(f.tags))
        if score >= 2:
            scored.append((-score, c.id, c))
    return [c for _s, _i, c in sorted(scored)[:limit]]


class Check:
    def __init__(
        self,
        ledger: Ledger,
        sources: Sources,
        ask: Ask,
        *,
        store: auth.Store | None = None,
        second: Ask | None = None,
        cache_dir: Path | None = None,
        parallelism: int = 1,
        controls: bool = True,
        seed: int = 0,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.ledger, self.sources, self.ask = ledger, sources, ask
        self.second = second or ask
        self.store = store if store is not None else auth.Store()
        self.cache = Cache(cache_dir)
        self.parallelism = max(1, parallelism)
        self.controls_on, self.seed = controls, seed
        self.log = log or (lambda _m: None)
        self.calls = 0

    # ---- calls ---------------------------------------------------------------------------
    def _call(self, system: str, user: str, second: bool = False) -> dict[str, Any] | None:
        self.calls += 1
        return parse_json((self.second if second else self.ask)(system, user))

    def _map(self, fn: Callable[[Any], None], work: list[Any]) -> None:
        with ThreadPoolExecutor(max_workers=self.parallelism) as pool:
            list(pool.map(fn, work))

    # ---- items ---------------------------------------------------------------------------
    def items(self, markup: str) -> list[Item]:
        out: list[Item] = []
        for heading, body in view.sections(markup):
            for s in view.sentences(body):
                ids = view.fact_ids(s)
                refs = view.authority_refs(s)
                pv = view.preview(s, self.ledger, self.store)
                if ids:
                    key = item_key("characterization", s, ids, page_sha(ids, self.ledger, self.sources))
                    out.append(Item(key, "characterization", heading, s, pv, ids))
                if refs:
                    slugs = tuple(r[0] or "?" for r in refs)
                    texts = "|".join(sha(self.store.passage(r[0], [], 10**9)) for r in refs)
                    out.append(
                        Item(item_key("authority", s, slugs, sha(texts)), "authority", heading, s, pv, (), slugs)
                    )
        return out

    def _cached(self, items: list[Item]) -> list[Item]:
        todo = []
        for i in items:
            hit = None if i.control else self.cache.get(i.key)
            if hit is not None:
                i.take(hit)
                i.cached = True
            else:
                todo.append(i)
        return todo

    # ---- characterization ----------------------------------------------------------------
    def characterize(self, items: list[Item]) -> None:
        for i in items:
            i.blocks = evidence_for(i.ids, self.ledger, self.sources)
        batches = _batches(self._cached(items))
        self._map(self._grade_batch, batches)

    def _grade_batch(self, batch: list[Item]) -> None:
        shown_all: list[dict[str, str]] = []
        lines = []
        k = 0
        for n, i in enumerate(batch, 1):
            shown: dict[str, str] = {}
            lines.append(f"## ITEM {n}\nSECTION: {i.section or 'opening'}\nSENTENCE: {i.preview}\nEVIDENCE:")
            for b in i.blocks:
                k += 1
                shown[f"E{k}"] = b.ref
                lines.append(f"[E{k}] {b.text}")
            if not i.blocks:
                lines.append("(none: the cited facts are not in the record's verified facts)")
            shown_all.append(shown)
        got = self._call(prompts.CHARACTERIZE_SYSTEM, "\n".join(lines))
        self._apply(batch, shown_all, got)

    def _apply(self, batch: list[Item], shown_all: list[dict[str, str]], got: dict[str, Any] | None) -> None:
        by = _by_n(got, "grades")
        for n, (i, shown) in enumerate(zip(batch, shown_all), 1):
            g = by.get(n)
            if g is None:
                i.grade, i.note = "could_not_verify", "the checker gave this item no grade"
                continue
            apply_grade(i, g, shown)
            if not i.control:
                self.cache.put(i.key, i.graded())

    # ---- authority -----------------------------------------------------------------------
    def authority(self, items: list[Item]) -> None:
        todo = []
        for i in self._cached(items):
            if any(s == "?" or s not in self.store for s in i.slugs):
                i.grade = "could_not_verify"
                i.note = "no primary text of this authority is on file"
                continue
            todo.append(i)
        self._map(self._grade_authority, [todo[x : x + 6] for x in range(0, len(todo), 6)])

    def _grade_authority(self, batch: list[Item]) -> None:
        shown_all, lines, k = [], [], 0
        for n, i in enumerate(batch, 1):
            shown: dict[str, str] = {}
            lines.append(f"## ITEM {n}\nSENTENCE: {i.preview}\nPRIMARY TEXT:")
            for s in i.slugs:
                k += 1
                shown[f"P{k}"] = f"P: {s}"
                e = self.store.get(s)
                lines.append(f"[P{k}] {e.citation if e else s}:\n{self.store.passage(s, view.terms(i.sentence))}")
            shown_all.append(shown)
        self._apply(batch, shown_all, self._call(prompts.AUTHORITY_SYSTEM, "\n".join(lines)))

    # ---- omission ------------------------------------------------------------------------
    def omissions(self, char_items: list[Item], cited: set[str]) -> list[Item]:
        out = []
        for i in char_items:
            cands = contrary(i.ids, self.ledger, cited)
            if not cands:
                continue
            extra = ",".join(f"{c.id}:{sha(c.excerpt)}" for c in cands)
            o = Item(
                item_key("omission", i.sentence, i.ids, i.key, extra),
                "omission",
                i.section,
                i.sentence,
                i.preview,
                i.ids,
            )
            o.blocks = [Block(c.id, f"{c.id}, {c.file} p.{c.page}: «{c.excerpt[:EXCERPT_SHOWN]}»") for c in cands]
            hit = self.cache.get(o.key)
            if hit is not None:
                o.take(hit)
                o.cached = True
            out.append(o)
        todo = [o for o in out if not o.cached]
        self._map(self._omission_batch, [todo[x : x + 10] for x in range(0, len(todo), 10)])
        return [o for o in out if o.grade == "disclosed"]

    def _omission_batch(self, batch: list[Item]) -> None:
        lines, maps = [], []
        for n, o in enumerate(batch, 1):
            cited = "\n".join(
                b.text for b in evidence_for(o.ids, self.ledger, self.sources) if b.ref.endswith("excerpt")
            )
            lines.append(f"## SENTENCE {n}\n{o.preview}\nIT CITES:\n{cited}\nCANDIDATES:")
            m = {}
            for k, b in enumerate(o.blocks, 1):
                m[f"C{k}"] = b.ref
                lines.append(f"[C{k}] {b.text}")
            maps.append(m)
        by = _by_n(self._call(prompts.OMISSION_SYSTEM, "\n".join(lines)), "omissions")
        for n, (o, m) in enumerate(zip(batch, maps), 1):
            g = by.get(n)
            against = tuple(m[c] for c in (g or {}).get("against") or [] if isinstance(c, str) and c in m)
            if g is not None and against:
                o.grade, o.against = "disclosed", against
                facts = [self.ledger.get(a) for a in against]
                where = "; ".join(f"FILE: {f.file}, p. {f.page}" for f in facts if f is not None)
                o.note = " ".join(str(g.get("note") or "").split())[:400] + (f" ({where})" if where else "")
            else:
                o.grade = "confirmed"
            o.evidence = against
            self.cache.put(o.key, o.graded())

    # ---- adversary -----------------------------------------------------------------------
    def adversary(self, markup: str, items: list[Item]) -> list[Item]:
        whole = "\n\n".join(
            (f"## {h}\n" if h else "") + " ".join(view.preview(s, self.ledger, self.store) for s in view.sentences(b))
            for h, b in view.sections(markup)
        )
        key = item_key("adversary", whole, (), "")
        hit = self.cache.get(key)
        if hit is None:
            got = self._call(prompts.ADVERSARY_SYSTEM, whole) or {}
            hit = {"findings": [f for f in got.get("findings") or [] if isinstance(f, dict)]}
            self.cache.put(key, hit)
        out = []
        for n, f in enumerate(hit["findings"]):
            text = " ".join(str(f.get("text") or "").split())
            src = _match(text, items)
            kind = str(f.get("kind") or "").lower()
            a = Item(
                key=sha(f"{key}|{n}"),
                part="adversary",
                section=src.section if src else "",
                sentence=src.sentence if src else text,
                preview=src.preview if src else text,
                ids=src.ids if src else (),
                grade="wrong" if kind == "contradiction" else "framing",
                severity=(str(f.get("severity") or "minor").lower() if kind == "contradiction" else ""),
                note=" ".join(str(f.get("note") or "").split())[:400],
                fix=" ".join(str(f.get("fix") or "").split())[:400],
                against=(" ".join(str(f.get("against") or "").split()),) if f.get("against") else (),
            )
            if a.severity not in ("serious", "minor") and a.grade == "wrong":
                a.severity = "minor"
            out.append(a)
        return out

    # ---- the second read's source ----------------------------------------------------------
    def source_for(self, i: Item) -> str:
        """What the second reader sees beside the claim: only the source the
        claim cites (the first reader's note, grade and fix never)."""
        if i.part == "authority":
            return "\n\n".join(
                f"{self.store.citation(s) or s}:\n{self.store.passage(s, view.terms(i.sentence))}" for s in i.slugs
            )
        parts = [b.text for b in (i.blocks or evidence_for(i.ids, self.ledger, self.sources))]
        if i.part == "adversary" and i.against:
            parts.insert(0, "THE OTHER SENTENCE OF THE SAME DOCUMENT: " + i.against[0])
        return "\n\n".join(parts)

    # ---- the run -------------------------------------------------------------------------
    def run(self, markup: str, out_dir: Path | None = None, job: dict[str, Any] | None = None) -> Report:
        items = self.items(markup)
        chars = [i for i in items if i.part == "characterization"]
        plants = ctl.plant(chars, self.ledger, self.seed) if self.controls_on else []
        self.characterize(_interleave(chars, plants))
        retests = [
            r
            for r in (
                ctl.retest(c, self.ledger, self.seed)
                for c in plants
                if c.control == "control" and c.grade == "confirmed"
            )
            if r
        ]
        if retests:
            self.characterize(retests)
        self.authority([i for i in items if i.part == "authority"])
        cited = {fid for i in chars for fid in i.ids}
        disclosed = self.omissions(chars, cited)
        adv = self.adversary(markup, chars)
        real = items + disclosed + adv
        adj.adjudicate(real, self.source_for, self.second, self.cache, self.parallelism, self.log)
        rev = (
            [
                (i, adj.second_read(i.preview, self.source_for(i), self.second))
                for i in ctl.reverse_sample(chars, self.seed)
            ]
            if self.controls_on
            else []
        )
        rows = ctl.rows(plants + retests) + ctl.reverse_rows(rev)
        wants = auth.wanted(
            (r for i in items if i.part == "authority" for r in view.authority_refs(i.sentence)), self.store
        )
        report = Report(
            real + plants + retests,
            wanted=[w.__dict__ for w in wants],
            calls=self.calls + len(rev),
            refused_sources=list(self.sources.refused),
        )
        if out_dir is not None:
            auth.write_wanted(out_dir / auth.WANTED_FILE, wants, job)
        try:
            report.controls = ctl.assess(rows, out_dir, self.log) if rows else {"controls": 0, "invalid": False}
        finally:
            if out_dir is not None:
                (out_dir / REPORT_FILE).write_text(json.dumps(report.to_json(), indent=2) + "\n", encoding="utf-8")
        self.log(f"CHECK: {report.counts()} in {report.calls} calls")
        return report


def run(markup: str, ledger: Ledger, sources: Sources, ask: Ask, **kw: Any) -> Report:
    """One check of ``markup``. Raises ``controls_adapter.ControlsInvalid``
    when the planted controls say the checker did not discriminate."""
    out_dir = kw.pop("out_dir", None)
    job = kw.pop("job", None)
    return Check(ledger, sources, ask, **kw).run(markup, out_dir, job)


# ---- helpers ---------------------------------------------------------------------------------


def _by_n(got: dict[str, Any] | None, key: str) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for g in (got or {}).get(key) or []:
        if isinstance(g, dict) and isinstance(g.get("n"), int):
            out.setdefault(g["n"], g)
    return out


def _batches(items: list[Item]) -> list[list[Item]]:
    out: list[list[Item]] = []
    cur: list[Item] = []
    size = 0
    for i in items:
        n = sum(len(b.text) for b in i.blocks) + len(i.preview)
        if cur and (len(cur) >= BATCH_ITEMS or size + n > BATCH_CHARS):
            out.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += n
    if cur:
        out.append(cur)
    return out


def _interleave(real: list[Item], plants: list[Item]) -> list[Item]:
    """Controls spread through the real items, so no batch is all controls."""
    if not plants:
        return list(real)
    out: list[Item] = []
    step = max(1, len(real) // (len(plants) + 1))
    queue = list(plants)
    for n, i in enumerate(real, 1):
        out.append(i)
        if queue and n % step == 0:
            out.append(queue.pop(0))
    return out + queue


def _match(text: str, items: list[Item]) -> Item | None:
    t = norm(text)
    if not t:
        return None
    for i in items:
        p = norm(i.preview)
        if t in p or (p and p in t):
            return i
    return None
