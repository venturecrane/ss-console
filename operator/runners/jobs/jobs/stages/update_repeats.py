"""`update_repeats`: an update's added entries read against the other
entries of their date, and a paragraph that only states again what one of
them states removed. Paid (the judgment tier), text only, DELETE-ONLY.

WHY (2026-11-20): ``medchron/update/repeat_read``. The order:

1. CANDIDATES and their keepers (``repeat_read.plan``): an added entry that
   shares its date with another entry; its keepers are the delivered entries
   of the date and the added ones ranked above it. And an added entry citing
   two or more exhibits, read against itself: each paragraph's keepers are
   the paragraphs before it (``repeat_read.within``).
2. Before any call, the read phase is priced and checked by the driver's own
   limits (``entry_audit_io.check_limits``): past the job's cap it is the
   firm's ordinary cap hold (``LimitHold``). The doorway re-checks before
   every call.
3. FIRST READ of each candidate (several requests when its keepers are long,
   ``repeat_read.KEEPER_CHARS``). An unparsed reply is asked once more;
   still unparsed, or refused by the API, the candidate is unread and the
   stage HOLDS (exit 1) with the document untouched. A network failure exits
   75 and the daemon retries.
4. The code legs (``repeat_veto.code_legs``) on every ``repeats``; only a
   paragraph that passes is given the SECOND READ, shown the paragraph and
   its keeper, never the first answer. A second read the API refuses or that
   never parses keeps the paragraph (it is counted, not held: keeping text
   is the safe answer).
5. ``repeat_veto.settle``: the drops both reads agree on, less what the
   chain, heading, entry and exhibit rules keep; the document written once.
6. RECORD ``runs/<unit>/update_repeats.json``: counts only, and the dropped
   paragraphs' sha256, never their text.

Answers are saved before use under ``runs/<unit>/update_repeats/`` and a
resume pays for none twice (``entry_audit_io.Asker``, the entry reader's).
The stage reads the document as it found it on a rerun of itself
(``final-chronology.pre-repeats.md``), so a rerun asks the same questions.

REPLAY ($0), the entry reader's switch: with ``ENTRY_AUDIT_REPLAY=1`` a
missing answer is never called for. Each request is written as
``runs/<unit>/update_repeats/<name>.<kind>.request.json`` (`name` is the
candidate, ``A3`` or ``A3-2`` for its second request, ``W3`` for an entry
read against itself, or ``A3.P2`` / ``W3.P2`` for a paragraph's second read; `kind` is ``read1``/``read2`` or
``confirm1``/``confirm2``; the request's ``system`` and its ``content`` text
blocks are the whole question) and the stage exits 9. Write each answer as
``<name>.<kind>.json``, ``{"hash": <the request's hash>, "text": <the reply
JSON>, "stop_reason": "end_turn", "model": <the request's model>}``, and run
the stage again: the first run writes every first-read request, the second
every second-read request, the third decides, applies and records. Nothing
is paid under the switch.

Exits: 0 settled; 1 held (a candidate not read); 3 the stage crashed outside
a model call (a defect, never a hold); 75 the model could not be reached
(retried); 9 answers pending, under the replay switch only.
"""

from __future__ import annotations

import hashlib
import json
import traceback
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .. import budget, paid
from ..deadline import is_transient
from ..limits import LimitHold
from ..matter.store import write_atomic
from ..medchron.audit.run import run_pool
from ..medchron.config import DEFAULT_AUDIT_WORKERS
from ..medchron.update import final_set_io as FIO, repeat_read as RR, repeat_veto as V
from ..medchron.update.review_pages import CHARS_PER_TOKEN
from . import entry_audit_io as IO
from .base import StageRun, read_json
from .update_base import headings
from .update_final_set import already_sent

STAGE = DIR = "update_repeats"
RECORD = "update_repeats.json"
PRE = "final-chronology.pre-repeats.md"
EXIT_HELD, EXIT_DEFECT, EXIT_INFRA, EXIT_PENDING = 1, 3, 75, 9
#: The exits past 1 (``dag_update`` writes exit 1's reason).
EXITS = {
    EXIT_DEFECT: ("failed", "update_repeats: the stage crashed (a defect)"),
    EXIT_INFRA: (
        "failed",
        "update_repeats incomplete (infrastructure): the model returned no answer; a later rerun reads again",
    ),
    EXIT_PENDING: (
        "failed",
        "update_repeats replay: answers pending (ENTRY_AUDIT_REPLAY is set; a real run never sets it)",
    ),
}
READ_MAX_TOKENS, CONFIRM_MAX_TOKENS = 8_000, 2_000
#: The output a projection prices per call (a verdict per paragraph; one small object).
READ_OUT_EXPECTED, CONFIRM_OUT_EXPECTED = 800, 200
Ask = tuple[str, str, str, list[dict[str, Any]]]  # (name, kind stem, system, content)


@dataclass
class Ctx:
    sr: StageRun
    ask: IO.Asker
    workers: int


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def run(sr: StageRun) -> int:
    """The stage, with every way out mapped: a limit hold goes to the driver,
    the network to 75, any other exception to 3."""
    try:
        return _start(sr)
    except LimitHold:
        raise
    except Exception as exc:  # noqa: BLE001 - a crash is the defect exit, never read as a hold
        if is_transient(exc):
            sr.log(f"repeats read: the model could not be reached ({type(exc).__name__}); a later rerun reads again")
            return EXIT_INFRA
        sr.log(f"repeats read CRASHED ({type(exc).__name__}: {str(exc)[:300]})")
        sr.log(traceback.format_exc()[-1500:])
        return EXIT_DEFECT


def source(rd: Path) -> str:
    """The document as this stage first read it, on a rerun of itself; else as it stands."""
    current = (rd / "final-chronology.md").read_text(encoding="utf-8")
    rec, pre = read_json(rd / RECORD, {}) or {}, rd / PRE
    if pre.is_file() and rec.get("out_sha") == _sha(current):
        kept = pre.read_text(encoding="utf-8")
        if rec.get("in_sha") == _sha(kept):
            return kept
    return current


def _plan(sr: StageRun) -> tuple[Path, str, RR.Plan]:
    rd = sr.slug_dir / "runs" / sr.unit.unit
    src = source(rd)
    base = (rd / "update" / "base.md").read_text(encoding="utf-8")
    return rd, src, RR.plan(src, base, headings(sr.cfg))


def _start(sr: StageRun) -> int:
    if already_sent(sr):
        sr.log("repeats read: already sent: left as filed")
        return 0
    if not FIO.added_entries(sr.slug_dir, sr.unit.unit):
        sr.log("repeats read: no added entries; nothing to do")
        return 0
    rd, src, pl = _plan(sr)
    workers = int(sr.cfg.get("levers", "audit_workers") or DEFAULT_AUDIT_WORKERS)
    c = Ctx(sr, IO.Asker(sr, IO.model(sr.cfg), rd / DIR, IO.replaying(), stage=STAGE), workers)
    sr.log(f"repeats read: {pl.candidates} candidate(s), {len(pl.asked)} with keepers, {len(pl.parts)} request(s)")
    _check_cost(c, [(p.name, "read", *RR.read_request(p, sr.cfg)) for p in pl.parts], READ_OUT_EXPECTED)
    reads = _pool(c, pl.parts, lambda p: _read(c, p))
    if any(r == "pending" for r in reads):
        return _pending(c, "read")
    unread = [p.name for p, r in zip(pl.parts, reads) if not isinstance(r, dict)]
    if unread:
        _record(rd, c, pl, src, src, None, held=f"{len(unread)} candidate request(s) could not be read")
        sr.log(f"repeats read HELD: {len(unread)} request(s) could not be read: {', '.join(unread)}; unchanged")
        return EXIT_HELD
    by_name = {p.name: r for p, r in zip(pl.parts, reads) if isinstance(r, dict)}
    seen, kept, todo = _first(pl, by_name)
    _check_cost(c, [_confirm_ask(d, sr.cfg) for d in todo], CONFIRM_OUT_EXPECTED)
    confirms = _pool(c, todo, lambda d: _confirm(c, d))
    if c.ask.pending:
        return _pending(c, "confirm")
    agreed = []
    for d, got in zip(todo, confirms):
        if got is not None and V.agrees(d.keeper, *got):
            agreed.append(d)
        else:
            kept["second_read"] += 1
    s = V.settle(src, agreed, headings(sr.cfg))
    s.kept.update(kept)
    _write(rd, src, s.md, lambda: _record(rd, c, pl, src, s.md, (seen, s)))
    sr.log(
        f"repeats read: {sum(seen.values())} paragraph(s) judged, {seen['repeats']} called repeats, "
        f"{len(s.dropped)} dropped ({s.entries_removed} entr(ies) removed whole), {seen['conflict']} conflict(s); "
        f"kept by the code {dict(s.kept)}; {c.ask.calls} call(s)"
    )
    return 0


def _confirm_ask(d: V.Drop, cfg: Any) -> Ask:
    return (f"{d.cand.key}.{d.para.label}", "confirm", *RR.confirm_request(d.cand, d.para, d.keeper, cfg))


def _first(pl: RR.Plan, by_name: dict[str, dict[str, RR.Verdict]]) -> tuple[Counter, Counter, list[V.Drop]]:
    """(verdicts counted, code legs that kept a repeat, the drops a second read is asked about)."""
    seen: Counter[str] = Counter()
    kept: Counter[str] = Counter()
    todo: list[V.Drop] = []
    parts: dict[str, list[dict[str, RR.Verdict]]] = {}
    for p in pl.parts:
        parts.setdefault(p.cand.key, []).append(by_name[p.name])
    conflict: set[tuple[int, int]] = set()
    for cand in pl.asked:
        for para in cand.paras:
            v = RR.combine(parts[cand.key], para.label)
            seen[v.verdict] += 1
            conflict |= {(cand.index, para.index)} if v.verdict == "conflict" else set()
            if v.verdict != "repeats":
                continue
            # In an entry, only an EARLIER paragraph keeps: a later one named is no keeper.
            ids = {k.kid: k for k in cand.keepers if k.kind != RR.WITHIN or k.block < para.index}
            keeper = ids.get(v.keeper)
            legs = V.code_legs(para, keeper, v)
            kept.update(legs)
            if not legs and keeper is not None:
                todo.append(V.drop(cand, para, keeper, v.quotes))
    # A conflict keeps the paragraph whichever read found it (one against its
    # date's entries, one against its own entry's earlier paragraphs).
    kept["conflict_elsewhere"] += sum(1 for d in todo if d.at in conflict)
    return seen, kept, [d for d in todo if d.at not in conflict]


def _pool(c: Ctx, items: list[Any], fn: Callable[[Any], Any]) -> list[Any]:
    out: list[Any] = [None] * len(items)

    def task(i: int) -> Callable[[], None]:
        return lambda: out.__setitem__(i, fn(items[i]))

    run_pool([task(i) for i in range(len(items))], c.workers)
    return out


def _pending(c: Ctx, phase: str) -> int:
    c.sr.log(
        f"repeats read REPLAY: {len(c.ask.pending)} {phase} answer(s) pending; requests written under "
        f"runs/{c.sr.unit.unit}/{DIR}/ (*.request.json)"
    )
    return EXIT_PENDING


# ---- the calls ------------------------------------------------------------------------
def _answer(c: Ctx, name: str, stem: str, req: tuple[str, list[dict[str, Any]]], ok: IO.Valid, cap: int) -> Any:
    """A good saved answer for either attempt, else asked (once more when it
    ran out or does not parse): the text, None (refused or never parsed), or
    "pending" under the replay switch. A network failure is raised."""
    system, content = req
    for attempt in (1, 2):
        text = c.ask.peek(name, f"{stem}{attempt}", system, content, ok)
        if text is not None and ok(text):
            return text
    for attempt in (1, 2):
        try:
            text, stop = c.ask.ask(name, f"{stem}{attempt}", system, content, ok, max_tokens=cap)
        except IO.Pending:
            return "pending"
        except IO.ModelError as exc:
            c.sr.log(f"  {name}: the API refused the {stem} ({exc})")
            return None
        if stop != "max_tokens" and ok(text):
            return text
        c.sr.log(f"  {name}: the {stem} reply {'ran out' if stop == 'max_tokens' else 'did not parse'}")
    return None


def _read(c: Ctx, p: RR.Part) -> dict[str, RR.Verdict] | str | None:
    labels = [q.label for q in p.cand.paras]
    got = _answer(c, p.name, "read", RR.read_request(p, c.sr.cfg), RR.parses(labels), READ_MAX_TOKENS)
    return got if got in ("pending", None) else RR.parse_read(got, labels)


def _confirm(c: Ctx, d: V.Drop) -> tuple[bool, list[str]] | None:
    name, stem, system, content = _confirm_ask(d, c.sr.cfg)
    got = _answer(c, name, stem, (system, content), RR.confirm_parses, CONFIRM_MAX_TOKENS)
    return None if got in ("pending", None) else RR.parse_confirm(got)


# ---- cost -----------------------------------------------------------------------------
def _tokens(system: str, content: list[dict[str, Any]]) -> int:
    return (len(system) + sum(len(str(b.get("text", ""))) for b in content)) // CHARS_PER_TOKEN


def _unanswered(ask: IO.Asker, asks: list[Ask]) -> list[Ask]:
    return [a for a in asks if not any(ask.peek(a[0], f"{a[1]}{n}", a[2], a[3], None) for n in (1, 2))]


def _usd(cfg: Any, asks: list[Ask], out_each: int) -> float | None:
    try:
        pricing = paid.load_pricing()
    except (OSError, ValueError, KeyError, budget.BudgetError):
        return None
    return IO.priced(pricing, cfg, sum(_tokens(a[2], a[3]) for a in asks), out_each * len(asks))


def _check_cost(c: Ctx, asks: list[Ask], out_each: int) -> None:
    """The phase's calls not yet answered, priced through the driver's own
    limits (the firm's cap hold past the cap). Not under the $0 replay."""
    todo = _unanswered(c.ask, asks)
    if c.ask.replay or not todo:
        return
    usd = _usd(c.sr.cfg, todo, out_each)
    if usd is not None:
        c.sr.log(f"repeats read: {len(todo)} call(s) to make, ~{usd:.2f} USD")
        IO.check_limits(c.sr, usd, stage=STAGE)


def rehearse(sr: StageRun) -> list[str]:
    """The $0 half for `jobs rehearse`: what the first reads would cost."""
    rd = sr.slug_dir / "runs" / sr.unit.unit
    if not (rd / "final-chronology.md").is_file() or not (rd / "update" / "base.md").is_file():
        return ["no audit document yet; the repeats read is unprojectable"]
    _rd, _src, pl = _plan(sr)
    asks = [(p.name, "read", *RR.read_request(p, sr.cfg)) for p in pl.parts]
    usd = _usd(sr.cfg, asks, READ_OUT_EXPECTED)
    priced = f"~{usd:.2f} USD" if usd is not None else "USD not priced on this host"
    return [
        f"{pl.candidates} added entr(ies) share a date; {len(pl.asked)} have keepers; {len(asks)} first read(s), "
        f"{sum(_tokens(a[2], a[3]) for a in asks)} input tokens, {priced} before any second read"
    ]


# ---- the files ------------------------------------------------------------------------
def _write(rd: Path, src: str, md: str, record: Callable[[], None]) -> None:
    """The input kept, then the record naming both shas, then the document:
    a crash between them leaves the input and a record that does not name
    it, so the rerun reads the document afresh (``final_set_io.write``'s order)."""
    pre, doc = rd / PRE, rd / "final-chronology.md"
    if not pre.is_file() or pre.read_text(encoding="utf-8") != src:
        write_atomic(pre, src)
    record()
    if doc.read_text(encoding="utf-8") != md:
        write_atomic(doc, md)


def _record(rd: Path, c: Ctx, pl: RR.Plan, src: str, md: str, done: Any, held: str = "") -> None:
    rec: dict[str, Any] = {
        "model": c.ask.model,
        "held": held,
        "candidates": pl.candidates,
        "candidates_with_keepers": len(pl.asked) - pl.within,
        "in_entry_candidates": pl.within,
        "requests": len(pl.parts),
        "calls": c.ask.calls,
        "in_sha": _sha(src),
        "out_sha": _sha(md),
    }
    if done is not None:
        seen, s = done
        rec |= {
            "paragraphs_judged": sum(seen.values()),
            "verdicts": {v: seen[v] for v in RR.VERDICTS},
            "conflicts": seen["conflict"],
            "dropped": len(s.dropped),
            "entries_removed": s.entries_removed,
            "citations_merged": s.cites_merged,
            "kept_by": {leg: s.kept[leg] for leg in V.LEGS},
            "dropped_sha256": s.shas(),
        }
    write_atomic(rd / RECORD, json.dumps(rec, indent=1))
