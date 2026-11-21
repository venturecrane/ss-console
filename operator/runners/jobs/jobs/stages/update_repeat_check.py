"""`update_repeat_check`: every page an update's repeat rules set aside as a
copy, and that is not pixel-equal to the page covering it, is shown beside
that page to one reader; a page the reader does not call the SAME is read.
Paid (the judgment tier, one call per pair).

The rules and their evidence are ``medchron/update/repeat_check``. Here:

1. The pairs are read off ``runs/<unit>/pages.jsonl`` as ``update_select``
   wrote it. A repeat with no recorded covering page is read without a call.
2. Before any call, the pairs not yet answered are priced (two pictures and
   the prompt each, on the seat's rate card) and checked by the driver's own
   limits (``entry_audit_io.check_limits``): past the job's cap it is the
   firm's ordinary cap hold. Every call also passes the doorway's per-call
   check.
3. One call per pair, ``audit_workers`` at once. SAME: the page stays set
   aside. DIFFERENT, a reply that does not parse after one retry, a refusal
   or any model failure: the page is read. A model failure never holds the
   job. Each answer is saved before it is used, keyed by both pages' file
   sha256 and page number, so a resume pays for none twice (a network
   failure is asked again on the next run).
4. When any page is read, ``update_select`` runs again (at $0) reading it,
   so every later consumer (the compose text, the exhibits, the page record
   and the pages charged, Records Reviewed) sees exactly what it would had
   no rule set the page aside. Its new exclusions (a document no longer a
   re-filed copy is decided page by page) are checked the same way, until
   no new page is read.

Exits: 0 settled; 2 the update held (``update_select``'s own hold, on its
rerun); ``outcome.EXIT`` every page is still set aside: the delivered
chronology is current.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import budget, paid
from ..deadline import is_transient
from ..limits import LimitHold
from ..medchron.audit.render import img_block, render
from ..medchron.audit.run import run_pool
from ..medchron.config import DEFAULT_AUDIT_WORKERS
from ..medchron.prompts import load
from ..medchron.update import outcome as UO, repeat_check as RC
from . import entry_audit_io as IO
from .base import StageRun, read_json, read_jsonl
from .update_base import update_dir

STAGE = "update_repeat_check"
MAX_TOKENS = 1_024
#: Rounds of check-then-reselect; each round reads at least one more page,
#: so this is a backstop: past it every pair still open is read unasked.
MAX_ROUNDS = 12
_HELD = 2


@dataclass
class Ctx:
    sr: StageRun
    store: RC.Store
    model: str
    system: str
    sources: dict[str, Path]
    pages_dir: Path
    calls: int = 0
    tally: dict[str, int] = field(default_factory=dict)
    counted: set[str] = field(default_factory=set)


def _sources(sr: StageRun) -> dict[str, Path]:
    """{"exhibit:N" | document id: the PDF (or image) its pages render from}."""
    out: dict[str, Path] = {}
    ud = update_dir(sr)
    for e in (read_json(ud / "update_plan.json", {}) or {}).get("exhibits") or []:
        if isinstance(e, dict) and str(e.get("exhibit", "")).isdigit() and e.get("name"):
            out[f"exhibit:{int(e['exhibit'])}"] = sr.slug_dir / "delivered" / str(e["name"])
    for r in read_jsonl(sr.slug_dir / "raw_manifest.jsonl"):
        if r.get("ok") and r.get("path"):
            out[str(r["id"])] = Path(str(r["path"]))
    return out


def _picture(c: Ctx, label: str, sha: str, page: int) -> dict[str, Any] | None:
    src = c.sources.get(label)
    if src is None or not src.is_file():
        return None
    got = render(src, page, c.pages_dir, sha[:24])
    return img_block(got) if got is not None else None


def request(c: Ctx, p: RC.Pair) -> list[dict[str, Any]] | None:
    """The user content for one pair: PAGE A, then PAGE B (each cover page);
    None when a picture cannot be made."""
    a = _picture(c, p.doc, p.sha, p.page)
    bs = [_picture(c, p.cover, p.cover_sha, n) for n in p.cover_pages]
    if a is None or any(b is None for b in bs):
        return None
    out: list[dict[str, Any]] = [{"type": "text", "text": "PAGE A (the page set aside as a copy):"}, a]
    out.append({"type": "text", "text": f"PAGE B ({len(bs)} page(s), the page(s) that cover it):"})
    return out + [b for b in bs if b is not None]


def _ask(c: Ctx, p: RC.Pair, content: list[dict[str, Any]], attempt: int) -> tuple[str, str]:
    r = c.sr.doorway.call(
        STAGE,
        model=c.model,
        system=c.system,
        messages=[{"role": "user", "content": content}],
        max_tokens=MAX_TOKENS,
        effort="medium",
        custom_id=f"repeat-{p.sha[:12]}-{p.page}-{attempt}"[:64],
    )
    c.calls += 1
    return str(r.text or ""), str(r.stop_reason or "")


def check(c: Ctx, p: RC.Pair) -> str:
    """One pair's verdict, from the store or one call (and one retry when
    the reply does not parse); FAILED on a refusal or any model failure."""
    got = c.store.cached(p.key)
    if got is not None:
        return got
    try:
        content = request(c, p)
        if content is None:
            c.store.put(p, RC.FAILED, "a page picture could not be made")
            return RC.FAILED
        for attempt in (1, 2):
            text, stop = _ask(c, p, content, attempt)
            if stop == "refusal":
                c.store.put(p, RC.FAILED, "refused")
                return RC.FAILED
            v = RC.parse(text)
            if v is not None:
                c.store.put(p, v)
                return v
        c.store.put(p, RC.FAILED, "the reply did not parse twice")
        return RC.FAILED
    except LimitHold:
        raise
    except Exception as exc:  # noqa: BLE001 - a failure to compare reads the page; it never holds the job
        c.store.put(p, RC.FAILED, f"{type(exc).__name__}: {str(exc)[:150]}", transient=is_transient(exc))
        return RC.FAILED


def _priced(sr: StageRun, todo: list[RC.Pair]) -> float | None:
    try:
        pricing = paid.load_pricing()
    except (OSError, ValueError, KeyError, budget.BudgetError):
        return None
    return IO.priced(pricing, sr.cfg, sum(RC.pair_tokens(p) for p in todo), len(todo) * RC.OUT_TOKENS)


def _count(c: Ctx, key: str, n: int = 1) -> None:
    c.tally[key] = c.tally.get(key, 0) + n


def run(sr: StageRun) -> int:
    from . import update_select

    ud = update_dir(sr)
    store = RC.Store(ud)
    c = Ctx(sr, store, IO.model(sr.cfg), load("repeat-check-system", sr.cfg), _sources(sr), ud / "repeat_pages")
    workers = int(sr.cfg.get("levers", "audit_workers") or DEFAULT_AUDIT_WORKERS)
    for rnd in range(1, MAX_ROUNDS + 1):
        pairs, lost = RC.current(sr.slug_dir, sr.unit.unit)
        todo = [p for p in pairs if store.cached(p.key) is None]
        usd = _priced(sr, todo) if todo else None
        sr.log(
            f"repeat check round {rnd}: {len(pairs)} pair(s), {len(todo)} to ask on {c.model}"
            + (f" (~{usd:.2f} USD)" if usd is not None else "")
            + f"; {len(lost)} repeat(s) with no covering page are read"
        )
        if usd is not None:
            IO.check_limits(sr, usd)
        verdicts: dict[str, str] = {}
        if rnd < MAX_ROUNDS:
            run_pool([(lambda p=p: verdicts.__setitem__(p.key, check(c, p))) for p in pairs], workers)
        else:  # the backstop: every pair still open is read, unasked
            verdicts = {p.key: c.store.cached(p.key) or RC.FAILED for p in pairs}
        _tally(c, pairs, lost, verdicts)
        read = {(p.sha, p.page) for p in pairs if verdicts.get(p.key) != RC.SAME}
        read |= {(u.sha, u.page) for u in lost}
        new = read - store.forced
        if not new:
            break
        store.forced |= new
        store.save()
        sr.log(f"repeat check: {len(new)} page(s) set aside as copies are read; selecting again")
        code = update_select.run(sr)
        if code == UO.EXIT:
            continue  # cannot happen with a page newly read; the next round re-reads the record
        if code != 0:
            return _HELD if code == 2 else code
    return _finish(sr, c)


def _tally(c: Ctx, pairs: list[RC.Pair], lost: list[RC.Uncovered], verdicts: dict[str, str]) -> None:
    """Counts for the run record, over every pair this stage settled (a
    pair counted once, whichever round asked it)."""
    for p in pairs:
        if p.key in c.counted:
            continue
        c.counted.add(p.key)
        v = verdicts.get(p.key, RC.FAILED)
        _count(c, "repeat_pairs_checked")
        _count(c, {RC.SAME: "repeat_same", RC.DIFFERENT: "repeat_different"}.get(v, "repeat_flipped_by_failure"))
    for u in lost:
        if f"{u.sha}:{u.page}" not in c.counted:
            c.counted.add(f"{u.sha}:{u.page}")
            _count(c, "repeat_no_cover_read")
    c.store.counts = {**c.tally, "repeat_calls": c.calls}
    c.store.save()


def _finish(sr: StageRun, c: Ctx) -> int:
    n = c.store.counts
    sr.log(
        f"repeat check: {n.get('repeat_pairs_checked', 0)} pair(s): {n.get('repeat_same', 0)} SAME, "
        f"{n.get('repeat_different', 0)} DIFFERENT, {n.get('repeat_flipped_by_failure', 0)} read on a failure, "
        f"{n.get('repeat_no_cover_read', 0)} read with no covering page; {c.calls} call(s)"
    )
    sel = read_json(update_dir(sr) / "selection.json", {}) or {}
    pages = sum(len(r.get("read") or []) for r in sel.get("exhibits") or [] if isinstance(r, dict))
    if not pages:
        sr.log("nothing new to read: every page is set aside; the delivered chronology is current")
        return UO.EXIT
    return 0


# ---- the projection ---------------------------------------------------------
def tokens(slug_dir: Path, unit: str) -> tuple[int, int, int, int]:
    """(pairs, pairs to ask, input tokens, output tokens) of the pairs not
    yet answered, read off the page record as it stands."""
    pairs, _ = RC.current(slug_dir, unit)
    store = RC.Store(slug_dir / "runs" / unit / "update")
    todo = [p for p in pairs if store.cached(p.key) is None]
    return len(pairs), len(todo), sum(RC.pair_tokens(p) for p in todo), len(todo) * RC.OUT_TOKENS


def projected_usd(slug_dir: Path, unit: str, cfg: Any, pricing: budget.Pricing) -> float:
    """What the stage is projected to add, for the driver's limits."""
    _n, _todo, tin, tout = tokens(slug_dir, unit)
    return IO.priced(pricing, cfg, tin, tout)


def rehearse(sr: StageRun) -> list[str]:
    """The $0 half for `jobs rehearse`: the pairs and what asking them costs."""
    from .update_review import _dollars

    n, todo, tin, tout = tokens(sr.slug_dir, sr.unit.unit)
    usd = _dollars([{"model": IO.model(sr.cfg), "in": tin, "out": tout}])
    return [
        f"{n} repeat pair(s) to check, {n - todo} already answered; {tin} input and {tout} output tokens "
        f"(two page pictures each) on {IO.model(sr.cfg)} = ~{usd}"
    ]
