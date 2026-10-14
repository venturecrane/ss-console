"""`update_review`: the update's INDEPENDENT REVIEW and FINISHER loop, the
gate before the update is assembled, proven and filed. Paid (the review tier
for both passes, plus the audit of every sentence the finisher writes).

WHY (2026-10-14). The laptop's two chronology updates were delivered clean;
the seat's update lane failed for a day on the same files. The difference
was the laptop's last step: after the pipeline an Opus agent ran an
independent review against the page images, then corrected every MUST-FIX
finding by hand, and looped until the reviewer found nothing must-fix. The
lane had encoded each such judgment as a rule (and its own review saw only
cited pages and could repair only claim anchors), so every real file exposed
a new case. This stage is the laptop's step, in the lane, as one loop:

1. REVIEW (``update/review_parts.py``). A fresh pass with no access to
   compose's reasoning reads every added entry against EVERY page of the new
   documents it cites, in windows sized to finish, plus a text-only duplicate
   part per long document. It returns findings only: severity, class, where
   (anchor), the page it relies on and what that page says. A part whose
   reply runs out is split in half; a single page that cannot finish fails
   the stage resumably (nothing unreviewed goes on).
2. FINISH (``update/finisher.py``). A second pass, shown the findings, the
   entries and the pages they rely on, returns edits: any text of an added
   entry (date, provider, body, an omitted paragraph, a duplicate deleted in
   favour of the entry that holds its content). Each edit is checked
   (grounded, never dropping content that exists nowhere else), applied, the
   entries re-sorted, and every new sentence AUDITED against its page; an
   edit the audit does not support is reverted and its finding stays open.
   The audit loop then runs to its gate on what remains.
3. LOOP. The edited entries, the entries still carrying a MUST, and their
   neighbours are reviewed again: ROUNDS reviews, up to MAX_ROUNDS while the
   loop converges. A round that settles nothing, or a review that finds no
   fewer MUST-FIX than the last, ends it; the last review's findings are then
   marked "not finished" with why, and a finding the finisher will not edit is
   "declined by the finisher" with its reason, so the hold names each one.
   0 MUST -> assemble, proofs and the existing delivery path. Otherwise HOLD
   (``update_held:``, ``smd_finishing``) with the open findings listed in
   plain language in the job log and the reason. SHOULD findings are finished
   when the finisher can; they never hold the job.

The delivered entries are never edited. Every call is a ledger row under this
stage with its round in the custom id; the driver's cap binds each call, and
each round logs its calls, tokens and dollars. ``review_repair.json`` is the
record (``review_repair.named_values`` reads it for proof 10); a rerun on the
document this stage already settled does not pay again.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .. import budget, llm, prompts
from ..audit import claims as CL
from ..audit.page_text import exhibit_paths
from ..audit.render import img_block, render
from ..audit.run import AuditPaths, Round
from ..proof import review as PR
from ..update import body as B, entry_edits as EE, finisher as FN, review_parts as RP, review_repair as RR, select as S
from . import audit_loop
from .base import StageRun, read_json
from .update_base import headings, update_dir

ROUNDS = 4  # reviews: up to three finishes, each verified by the next review
#: Past ROUNDS a loop goes on only while it is converging: the last finish
#: settled a finding and the must-fix count is still falling (a real update
#: was cut off at round 4 with 52 open after 123, 98, 54).
MAX_ROUNDS = 6
SPLIT_DEPTH = 5
MAX_TOKENS = 32_000
RECORD = "review_repair.json"
STAGE = "update_review"
#: The projection's input per page shown (an image plus its label) and the
#: share of round one the later rounds and the finisher add.
PAGE_TOKENS = 1_800
LATER_SHARE = 1.0

Images = Callable[[int, int], dict[str, Any] | None]
#: (document before, document after) -> the new claims the audit did not support
Verify = Callable[[str, str], list[dict[str, Any]]]


@dataclass
class Ctx:
    sr: StageRun
    model: str
    system: str
    fsystem: str
    images: Images
    evidence: RR.Evidence
    npages: Callable[[int], int]
    verify: Verify
    already: RP.Already = field(default_factory=dict)
    rnd: int = 0
    calls: int = 0
    usage: list[dict[str, Any]] = field(default_factory=list)


def _model(cfg: Any) -> str:
    tiers = cfg.get("models", "tiers") or {}
    return str(tiers.get("review") or llm.model_for(cfg, "judgment"))


def _image_source(sr: StageRun) -> Images:
    out = sr.slug_dir / "out" / sr.unit.unit
    pdfs = exhibit_paths(out)

    def get(ex: int, page: int) -> dict[str, Any] | None:
        if ex not in pdfs:
            return None
        p = render(pdfs[ex], page, out / "auditpages", f"ex{ex}")
        return img_block(p) if p else None

    return get


def page_counts(out: Path) -> Callable[[int], int]:
    from pypdf import PdfReader

    pdfs, cache = exhibit_paths(out), {}

    def n(ex: int) -> int:
        if ex not in cache:
            try:
                cache[ex] = len(PdfReader(str(pdfs[ex])).pages) if ex in pdfs else 0
            except Exception:  # noqa: BLE001 - an unreadable exhibit shows no pages; its claims still audit
                cache[ex] = 0
        return cache[ex]

    return n


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


def already_pages(sr: StageRun) -> RP.Already:
    """exhibit -> {page: the delivered page it is a copy of}, from the page
    identity the update decided before vision (``update/pageid``): a new
    exhibit's page the delivery already read."""
    ud = update_dir(sr)
    ident = (read_json(ud / "page_identity.json", {}) or {}).get("pages") or {}
    out: RP.Already = {}
    for r in read_json(ud / "new_exhibits.final.json", []):
        ex, doc = (r.get("exhibit"), r.get("id")) if isinstance(r, dict) else (None, None)
        rows = ident.get(doc) if isinstance(doc, str) and isinstance(ident, dict) else None
        if not isinstance(ex, int) or not isinstance(rows, dict):
            continue
        for page, v in rows.items():
            if isinstance(v, list) and len(v) >= 3 and v[0] == "already" and str(page).isdigit():
                home = str(v[1]).replace("exhibit:", "Exhibit ")
                out.setdefault(ex, {})[int(page)] = f"{home} p. {v[2]}"
    return out


class GradeFailed(RuntimeError):
    """The grader of the finisher's sentences returned no verdict (an API
    refusal): the stage fails resumably; nothing is reverted on a non-answer."""


def verify_model(cfg: Any) -> str:
    """The grader of the finisher's sentences: the firm's ``verify`` tier,
    else the audit tier. It is the audit's own verdict call (a forced tool),
    so it can never be a model that refuses one (``config.NO_FORCED_TOOL_MODELS``):
    F5 graded with the review tier, claude-opus-5-5, which answers every forced
    tool_choice with HTTP 400, and all 46 of a round's edits were reverted on
    ERROR verdicts (2026-10-14)."""
    tiers = cfg.get("models", "tiers") or {}
    return str(tiers.get("verify") or llm.model_for(cfg, "audit"))


def _audit_verify(sr: StageRun) -> Verify:
    """One audit round over the edited document, on the page IMAGE, by the
    verify tier: the new claims (keys the document before did not have)
    whose verdict is not SUPPORTED. A verdict that is not a verdict (ERROR:
    the call failed) raises GradeFailed: a refusal is never read as
    "unsupported", which would revert every edit of the round."""

    def verify(before: str, after: str) -> list[dict[str, Any]]:
        paths = AuditPaths(sr.slug_dir, sr.unit.unit)
        keep = _keep(sr)
        old = {c["key"] for c in CL.extract_claims(CL.body_of(before), keep)}
        new = [c for c in CL.extract_claims(CL.body_of(after), keep) if c["key"] not in old]
        if not new:
            return []
        paths.doc.write_text(after, encoding="utf-8")
        # A sentence an earlier run already wrote and got NO verdict for (an
        # ERROR row: a refused call) is graded again, never resumed: the audit
        # resumes any key with a row, so a stale refusal from a run before the
        # verify tier existed failed this one (2026-10-14, every call opus-5).
        prior = CL.latest_real(CL.read_rows(paths.results), {c["key"] for c in new})
        for k, r in prior.items():
            if r.get("verdict") == "ERROR":
                CL.append_row(paths.results, {"key": k, "kind": CL.REAUDIT, "why": "no verdict on file"})
        Round(sr.doorway, verify_model(sr.cfg), paths, sr.log, mode="image").execute()
        got = CL.latest_real(CL.read_rows(paths.results), {c["key"] for c in new})
        errors = [r for r in got.values() if r.get("verdict") == "ERROR"]
        if errors:
            raise GradeFailed(
                f"{len(errors)} of the finisher's sentences got no verdict: {str(errors[0].get('note'))[:200]}"
            )
        return [c for c in new if (got.get(c["key"]) or {}).get("verdict") != "SUPPORTED"]

    return verify


def _keep(sr: StageRun) -> set[int]:
    return set(exhibit_paths(sr.slug_dir / "out" / sr.unit.unit))


def _client(sr: StageRun) -> str:
    u = sr.unit
    return f"CLIENT: {u.client_name}; date of birth {u.dob or 'not known'}; incident {sr.job.incident_date}"


# ---- the paid calls ---------------------------------------------------------------
def _call(c: Ctx, system: str, content: list[dict[str, Any]], key: str) -> Any:
    r = c.sr.doorway.call(
        STAGE,
        model=c.model,
        system=system,
        messages=[{"role": "user", "content": content}],
        max_tokens=MAX_TOKENS,
        stream=True,
        effort="high",
        custom_id=f"{STAGE}-r{c.rnd}-{key}",
    )
    c.calls += 1
    c.usage.append({"model": c.model, **_usage(r.usage)})
    return r


def _usage(usage: Any) -> dict[str, int]:
    from ..ledger import usage_dict

    try:
        return usage_dict(usage)
    except (AttributeError, TypeError):
        return {}


def _halves(p: RP.Part) -> list[RP.Part] | None:
    if len(p.pages) > 1:
        h = len(p.pages) // 2
        return [RP.Part(p.kind, p.entries, p.exhibit, p.pages[:h]), RP.Part(p.kind, p.entries, p.exhibit, p.pages[h:])]
    if len(p.entries) > 1:
        h = len(p.entries) // 2
        return [RP.Part(p.kind, p.entries[:h], p.exhibit, p.pages), RP.Part(p.kind, p.entries[h:], p.exhibit, p.pages)]
    return None


NOT_REVIEWED = "not reviewed"


def _not_reviewed(p: RP.Part) -> RR.Finding:
    """A part whose review never finished, as an open MUST finding on its
    first entry: held for SMD with its pages named, reviewed again next round
    (its entries are targets), never sent to the finisher, and never a failure
    of the whole stage (a real update died on one part's seven tries)."""
    where = f"Exhibit {p.exhibit} pp. {p.pages[0]}-{p.pages[-1]}" if p.pages else "its text"
    why = f"the review of entries {', '.join(e.anchor for e in p.entries)} ({where}) did not finish"
    f = RR.Finding(
        RR.MUST,
        NOT_REVIEWED,
        p.entries[0].anchor,
        p.exhibit or None,
        p.pages[0] if p.pages else None,
        why,
        "none",
        "",
        "",
    )
    f.outcome = f"{NOT_REVIEWED}: {why}"
    return f


def _review_part(c: Ctx, p: RP.Part, ents: list[RR.Entry], base: list[str], key: str, depth: int = 0) -> list:
    """One part's findings. A reply that runs out or does not parse is split
    in half (by pages, then entries) and reviewed again. At the last split a
    reply with findings but no completeness line is read as reviewed with
    them; one that still does not parse is a NOT_REVIEWED finding."""
    leaf = depth >= SPLIT_DEPTH or _halves(p) is None
    content = RP.review_content(p, _client(c.sr), c.images, ents, base, c.already)
    for attempt in (1, 2):
        r = _call(c, c.system, content, f"{key}-{attempt}")
        if r.stop_reason != "max_tokens":
            try:
                return RR.parse_reply(r.text, lenient=leaf)
            except RR.ReplyUnparsed as exc:
                c.sr.log(f"  {key}: {exc}")
        halves = None if leaf else _halves(p)
        if halves:
            return [
                f for i, h in enumerate(halves) for f in _review_part(c, h, ents, base, f"{key}{'ab'[i]}", depth + 1)
            ]
    c.sr.log(f"  {key}: not reviewed; held for SMD and reviewed again next round")
    return [_not_reviewed(p)]


def review_round(c: Ctx, md: str, base: list[str], targets: set[int] | None) -> tuple[list[RR.Finding], int]:
    """The independent review of the targeted added entries (all when None):
    deduplicated findings and the number of entries reviewed."""
    ents = RR.anchor_entries(md, _keep(c.sr), headings(c.sr.cfg))
    pick = [e for i, e in enumerate(ents) if targets is None or i in targets]
    found: list[RR.Finding] = []
    seen: set[tuple] = set()
    for i, part in enumerate(RP.plan(pick, _keep(c.sr), c.npages, c.already), 1):
        for f in _review_part(c, part, ents, base, f"p{i}"):
            k = (f.anchor, f.klass, f.exhibit, f.page, f.problem[:80])
            if k not in seen:
                seen.add(k)
                found.append(f)
    return found, len(pick)


def _must(found: list[RR.Finding]) -> list[RR.Finding]:
    """MUST findings about the added text (a delivered entry's is reported only)."""
    return [f for f in found if f.severity == RR.MUST and not f.anchor.startswith("D")]


def _finish_calls(c: Ctx, todo: list[RR.Finding], ents: list[RR.Entry], base: list[str]) -> tuple[list, dict]:
    """The finisher's edits for `todo`, part by part. A part whose reply does
    not finish is split by entries; one that still does not leaves its
    findings to the next review (never a guess)."""
    edits: list[RR.Finding] = []
    left: dict[str, str] = {}
    queue = [(es, fs, f"f{i}") for i, (es, fs) in enumerate(RP.finish_parts(todo, ents), 1)]
    while queue:
        es, fs, key = queue.pop(0)
        content = RP.finish_content(es, fs, _client(c.sr), c.images, ents, base)
        r = _call(c, c.fsystem, content, key)
        try:
            if r.stop_reason == "max_tokens":
                raise FN.EditsUnparsed("the reply ran out")
            got, why = FN.parse_edits(r.text, {f.fid: f for f in fs})
            edits += got
            left |= why
        except FN.EditsUnparsed as exc:
            c.sr.log(f"  finisher {key}: {exc}")
            if len(es) > 1:
                h = len(es) // 2
                for j, sub in enumerate((es[:h], es[h:])):
                    names = {e.anchor for e in sub}
                    queue.append((sub, [f for f in fs if f.anchor.split(".")[0] in names], f"{key}{'ab'[j]}"))
    return edits, left


def _apply(c: Ctx, md: str, edits: list[RR.Finding], base: list[str]) -> tuple[str, set[int]]:
    keep, heads = _keep(c.sr), headings(c.sr.cfg)
    ents = RR.anchor_entries(md, keep, heads)
    # The pages of each entry's own documents: the review read them for it, so
    # a correction grounded on any of them is a correction on its page.
    shown = {
        e.anchor: [(x, p) for x in sorted({x for x, _ in e.cites()}) for p in range(1, c.npages(x) + 1)] for e in ents
    }
    FN.precheck(edits, ents, base, c.evidence, shown, keep, heads)
    md1 = EE.apply_folds(FN.set_provider(md, edits, ents), edits, ents, base, heads)
    rest = [e for e in edits if e.action not in ("provider", "append_to_delivered", "new_entry")]
    md2, edited = RR.apply(md1, rest, keep, heads, c.evidence, shown)
    md2 = EE.add_entries(md2, edits)  # after the anchored edits: a new entry moves no anchor
    old = set(B.entries_of(md)[1])
    return md2, edited | {i for i, t in enumerate(B.entries_of(md2)[1]) if t not in old}


def finish(c: Ctx, md: str, found: list[RR.Finding], base: list[str], doc: Path) -> tuple[str, set[int], int, int]:
    """Edit, check, apply, audit, revert what the audit does not support, and
    run the audit loop to its gate. Returns (document, edited entries,
    findings settled, the audit loop's exit)."""
    for f in found:
        if f.anchor.startswith("D"):
            f.outcome = "carried: a delivered entry is never edited by an update"
    todo = [
        f
        for f in found
        if not f.anchor.startswith("D") and f.severity in (RR.MUST, RR.SHOULD) and f.klass != NOT_REVIEWED
    ]
    for i, f in enumerate(todo, 1):
        f.fid = f"F{i}"
    ents = RR.anchor_entries(md, _keep(c.sr), headings(c.sr.cfg))
    edits, left = _finish_calls(c, todo, ents, base)
    new_md, edited = _apply(c, md, edits, base)
    bad, untraced = FN.failed(edits, c.verify(md, new_md)) if new_md != md else ([], 0)
    if bad:
        ids = {id(e) for e in bad}
        for e in edits:
            if id(e) in ids:
                e.outcome = "reverted: the audit did not find the edited sentence on its page"
            elif e.outcome == "applied":
                e.outcome = ""
        new_md, edited = _apply(c, md, edits, base)
    settled = FN.settle(todo, edits, left)
    _mark_refiled(c.sr, FN.refiled(edits, ents, base, new_md, {int(r["exhibit"]) for r in _new_records(c.sr)}))
    c.sr.log(
        f"  finisher: {len(edits)} edit(s), {sum(e.outcome == 'applied' for e in edits)} applied, {len(bad)} reverted; "
        f"{untraced} unsupported sentence(s) no edit wrote, left to the audit loop"
    )
    if new_md == md:
        doc.write_text(md, encoding="utf-8")
        return md, set(), 0, 0
    doc.write_text(new_md, encoding="utf-8")
    rc = audit_loop.run(c.sr)  # every remaining new sentence is audited on its page, to the gate
    return doc.read_text(encoding="utf-8"), edited, settled, rc


def _new_records(sr: StageRun) -> list[dict[str, Any]]:
    rows = read_json(update_dir(sr) / "new_exhibits.final.json", [])
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("exhibit"), int)]


def _mark_refiled(sr: StageRun, homes: dict[int, str]) -> None:
    """Each new exhibit the finisher left uncited by dropping a copy of a
    delivered visit, recorded as a re-filed copy of that delivered exhibit:
    its number is kept, its Exhibit List row says so, and it is not filed
    again (``update_finish``)."""
    if not homes:
        return
    rows = _new_records(sr)
    for r in rows:
        if r["exhibit"] in homes:
            r["refiled"] = homes[r["exhibit"]]
            sr.log(f"  Exhibit {r['exhibit']}: a re-filed copy of delivered {homes[r['exhibit']]}; not filed again")
    (update_dir(sr) / "new_exhibits.final.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


# ---- the loop ---------------------------------------------------------------------
def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _plain(f: RR.Finding) -> str:
    where = f" (Exhibit {f.exhibit}, page {f.page})" if f.exhibit and f.page else ""
    return f"entry {f.anchor or 'unnamed'}{where}: {f.problem or f.klass}"


def _hold_line(open_: list[RR.Finding]) -> str:
    first = "; ".join(_plain(f) for f in open_[:4])
    return (
        f"update_held: the independent review of the update left {len(open_)} must-fix finding(s) the finisher "
        f"could not correct against the pages; SMD finishes it: {first}"
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


def _dollars(usage: list[dict[str, Any]]) -> str:
    """The round's dollars from the pricing table the driver reads: the env
    override, the image's path, else the source tree's own copy."""
    tree = Path(__file__).resolve().parents[4] / "adapter" / "cost_telemetry" / "anthropic_pricing.json"
    for path in (os.environ.get(budget.PRICING_ENV), budget.PRICING_DEFAULT, str(tree)):
        try:
            p = budget.Pricing.load(Path(path)) if path else None
            if p is not None:
                return f"{sum(p.price_row(u) for u in usage):.2f} USD"
        except (OSError, ValueError, KeyError, budget.BudgetError):
            continue
    return "not priced here"


def _ctx(sr: StageRun, images: Images | None, evidence: RR.Evidence | None, verify: Verify | None) -> Ctx:
    return Ctx(
        sr,
        _model(sr.cfg),
        prompts.load("update-review-system", sr.cfg),
        prompts.load("update-finish-system", sr.cfg),
        images or _image_source(sr),
        evidence or page_text(sr),
        page_counts(sr.slug_dir / "out" / sr.unit.unit),
        verify or _audit_verify(sr),
    )


def run(
    sr: StageRun,
    *,
    images: Images | None = None,
    evidence: RR.Evidence | None = None,
    verify: Verify | None = None,
    npages: Callable[[int], int] | None = None,
) -> int:
    ud, doc = update_dir(sr), sr.slug_dir / "runs" / sr.unit.unit / "final-chronology.md"
    md = doc.read_text(encoding="utf-8")
    again = _reuse(sr, read_json(ud / RECORD, {}), md)
    if again is not None:
        return again
    base = FN.delivered_of((ud / "base.md").read_text(encoding="utf-8"))
    c = _ctx(sr, images, evidence, verify)
    c.npages, c.already = npages or c.npages, already_pages(sr)
    at = _resume(sr, ud)
    if at is not None:
        doc.write_text(at.md, encoding="utf-8")
        return _loop(c, at.rec, at.md, base, doc, ud, at)
    rec: dict[str, Any] = {"model": c.model, "doc_sha_in": _sha(md), "rounds": []}
    return _loop(c, rec, md, base, doc, ud)


# ---- the checkpoint: a completed round is never paid for twice ---------------------
CHECKPOINT = "review_checkpoint.json"


@dataclass
class At:
    """Where a resumed loop starts: after round `done`, with that round's
    document, record, next review targets and must-fix count."""

    done: int
    md: str
    rec: dict[str, Any]
    targets: set[int] | None
    last_must: int | None
    last_settled: int = 0


def compose_sha(ud: Path) -> str:
    """The identity of what the loop reviews: the composed new entries and
    the delivered document (``update_collect``, which also clears the
    checkpoint: a new collect is a new review)."""
    parts = [(ud / n).read_text(encoding="utf-8") if (ud / n).is_file() else "" for n in ("new_entries.md", "base.md")]
    return _sha("\0".join(parts))


def _checkpoint(ud: Path, at: At) -> None:
    row = {
        "compose_sha": compose_sha(ud),
        "done": at.done,
        "md": at.md,
        "rec": at.rec,
        "targets": None if at.targets is None else sorted(at.targets),
        "last_must": at.last_must,
        "last_settled": at.last_settled,
    }
    (ud / CHECKPOINT).write_text(json.dumps(row), encoding="utf-8")


def _resume(sr: StageRun, ud: Path) -> At | None:
    """The last completed round of an interrupted loop over the same composed
    entries (a connection failure, a requeue), or None."""
    row = read_json(ud / CHECKPOINT, {})
    if not isinstance(row, dict) or not row.get("md") or row.get("compose_sha") != compose_sha(ud):
        return None
    done, t, lm = row.get("done"), row.get("targets"), row.get("last_must")
    if not isinstance(done, int) or not isinstance(row.get("rec"), dict):
        return None
    sr.log(f"update review: resuming after round {done}; rounds 1 to {done} are not paid again")
    targets = {int(x) for x in t if isinstance(x, int)} if isinstance(t, list) else None
    ls = row.get("last_settled")
    return At(
        done, str(row["md"]), row["rec"], targets, lm if isinstance(lm, int) else None, ls if isinstance(ls, int) else 0
    )


def _end(ud: Path) -> None:
    (ud / CHECKPOINT).unlink(missing_ok=True)


def _stop(rnd: int, left: int, last_left: int | None, settled: int) -> str:
    """Why the loop ends after this round's finish, or "". `left` is the
    must-fix the finisher did not settle this round. A review that finds new
    must-fix after edits is not a stall: progress is the finisher settling
    one, or fewer left than after the last round (2026-10-14: the loop stopped
    on a review count before the finisher had acted, 59 open)."""
    falling = last_left is not None and left < last_left
    if settled == 0 and not falling:
        return f"no progress: the finisher settled nothing and {left} must-fix remain, no fewer than the last round"
    if rnd >= MAX_ROUNDS or (rnd >= ROUNDS and not (falling and settled > 0)):
        return f"the review limit was reached at round {rnd}"
    return ""


def _loop(c: Ctx, rec: dict[str, Any], md: str, base: list[str], doc: Path, ud: Path, at: At | None = None) -> int:
    targets: set[int] | None = at.targets if at else None
    found: list[RR.Finding] = []
    last_left: int | None = at.last_must if at else None
    for rnd in range((at.done if at else 0) + 1, MAX_ROUNDS + 1):
        c.rnd, c.calls, c.usage = rnd, 0, []
        found, n = review_round(c, md, base, targets)
        must = _must(found)
        row: dict[str, Any] = {"round": rnd, "entries": n, "must": len(must)}
        rec["rounds"].append(row)
        settled, rc = 0, 0
        if found:  # the finisher always acts on the latest review
            try:
                md, edited, settled, rc = finish(c, md, found, base, doc)
            except GradeFailed as exc:
                doc.write_text(md, encoding="utf-8")  # the round's edits are not kept on a non-answer
                c.sr.log(f"update review: {exc}; nothing is reverted or kept, a rerun grades again")
                return 1
            targets = _targets(md, c.sr, edited, found)
        left = sum(f.outcome != "applied" for f in must)
        row.update(calls=c.calls, usage=c.usage, left=left, findings=[f.as_dict() for f in found])
        c.sr.log(
            f"update review round {rnd}: {n} entr(ies) reviewed, {len(must)} must-fix, "
            f"{len(found) - len(must)} other; {settled} settled by the finisher, {left} must-fix left; "
            f"{c.calls} call(s), {_dollars(c.usage)}"
        )
        if rc != 0:
            _save(ud / RECORD, rec, "audit_failed", doc)
            return 3 if rc == 1 else 4
        if not must:
            _save(ud / RECORD, rec, "clean", doc)
            return 0
        why = _stop(rnd, left, last_left, sum(f.outcome == "applied" for f in must))
        if why:
            c.sr.log(f"update review: {why}")
            break
        last_left = left
        _checkpoint(ud, At(rnd, md, rec, targets, last_left, settled))
    return _hold(c, rec, found, doc, ud)


def _hold(c: Ctx, rec: dict[str, Any], found: list[RR.Finding], doc: Path, ud: Path) -> int:
    open_ = [f for f in _must(found) if f.outcome != "applied"]
    if not open_:  # every one settled, yet the loop ended before a review confirmed it
        open_ = _must(found)
        for f in open_:
            f.outcome = "settled by the finisher, but the loop ended before a review read the edit"
    _save(ud / RECORD, rec, "held", doc, open_)
    for f in open_:
        c.sr.log(f"  OPEN [{f.klass}] {_plain(f)}" + (f"; not settled: {f.outcome}" if f.outcome else ""))
    c.sr.log(_hold_line(open_))
    return 2


def _save(path: Path, rec: dict[str, Any], status: str, doc: Path, open_: list[RR.Finding] | None = None) -> None:
    rec["status"] = status
    rec["doc_sha_out"] = _sha(doc.read_text(encoding="utf-8"))
    rec["open"] = [f.as_dict() for f in open_ or []]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    _end(path.parent)  # a settled loop is never resumed


def projected_usd(slug_dir: Path, unit: str, cfg: Any, pricing: budget.Pricing) -> float | None:
    """What this stage is projected to cost, from this matter's own pages:
    every page of the new exhibits shown once to the review, the same again
    for the later rounds and the finisher, priced at the review model's input
    rate (output is bounded by the parts and small beside the pages)."""
    out = slug_dir / "out" / unit
    new = [r.get("exhibit") for r in read_json(slug_dir / "runs" / unit / "update" / "new_exhibits.final.json", [])]
    exs = [x for x in new if isinstance(x, int)]
    if not exs:
        return None
    n = page_counts(out)
    pages = sum(n(x) for x in exs)
    rate = pricing.rate_for(_model(cfg))
    tokens_in = pages * PAGE_TOKENS * (1 + LATER_SHARE)
    parts = max(1, pages // RP.WINDOW_PAGES + 1)
    return (tokens_in * rate.input_per_million_cents + parts * 4_000 * rate.output_per_million_cents) / 1e8
