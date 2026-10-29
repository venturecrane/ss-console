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
   reply runs out is split in half; a single page that still cannot finish
   is held "not reviewed" (nothing unreviewed goes on).
2. FINISH (``update/finisher.py``). A second pass, shown the findings, the
   entries and the pages they rely on, returns edits: any text of an added
   entry (date, provider, body, an omitted paragraph, a duplicate deleted in
   favour of the entry that holds its content). Each edit is checked
   (grounded, never dropping content that exists nowhere else), applied, the
   entries re-sorted, and every new sentence AUDITED against its page; an
   edit the audit does not support is reverted and its finding stays open.
   The audit loop then runs to its gate on what remains.
3. FREEZE, THEN SHRINK (2026-10-15). The loop did not converge on two real
   matters: each fresh review raised mostly NEW objections (one round 2 had 77
   of 85 must-fix new, not unfixed), and duplicates were structurally
   unresolvable (``finisher.check_drop``). So round 1's MUST findings on added
   entries are the FROZEN list, each keyed by what survives anchor
   renumbering (``_key``: the class, the exhibit and page it relies on, and the
   entry's date line). No later review adds a must-fix: a later finding no
   frozen key matches is recorded as advisory and never blocks or reaches
   the finisher. Duplicates are decided in code before the finisher
   (``update/duplicates.py``), never a hold. After the one finish, ONE narrow
   re-check reviews only the entries still-open frozen findings sit on: a
   frozen finding it raises again (same key) is CONFIRMED and gets one more
   finisher attempt; one it does not raise is settled ("not raised again on
   re-check"). No further review. No confirmed finding left open -> clean:
   assemble, proofs and the existing delivery path. Otherwise HOLD
   (``update_held:``, ``smd_finishing``) with only the frozen findings still
   open, listed in plain language in the job log. The list only shrinks, so
   the stage always ends. SHOULD findings are finished when the finisher can;
   they never hold the job. A part the review could not finish is held "not
   reviewed" and its entries are re-read by the re-check, whose must-fix on
   them is their first review (frozen, not advisory).

The delivered entries are never edited. Every call is a ledger row under this
stage with its round in the custom id; the driver's cap binds each call, and
each round logs its calls, tokens and dollars. ``review_repair.json`` is the
record (``review_repair.named_values`` reads it for proof 10); a rerun on the
document this stage already settled does not pay again.
"""

from __future__ import annotations

import json
import os
import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .. import budget, llm, prompts
from ..audit import claims as CL
from ..audit.page_text import exhibit_paths
from ..audit.run import AuditPaths, Round
from ..proof import review as PR
from ..audit import handwritten_evidence as HE
from ..update import body as B, duplicates as DU, entry_edits as EE, finisher as FN, handwritten as H
from ..update import frozen as FZ, illegible as IL
from ..update import review_parts as RP, review_repair as RR
from . import audit_loop, update_review_settle as URS
from .base import StageRun, read_json
from .update_base import headings, update_dir
from .update_review_io import At, Images, _checkpoint, _resume, _sha, already_pages, page_counts
from .update_review_io import _end, _image_source, page_text
from .update_review_io import CHECKPOINT as CHECKPOINT  # the stage's checkpoint file name, read by tests

#: Rounds of the loop: 1 is the full review and its finish; 2 is the narrow
#: re-check of the frozen findings still open and the one more finish of the
#: ones it confirms. There is no round 3 (``_loop``).
REVIEW, RECHECK = 1, 2
SPLIT_DEPTH = 5
MAX_TOKENS = 32_000
RECORD = "review_repair.json"
STAGE = "update_review"
#: The projection's input per page shown (an image plus its label) and the
#: share of round one the rest adds: the finish, the narrow re-check (a subset
#: of round one's pages) and its one finish; kept at a full round, an upper
#: bound now that there is no third review.
PAGE_TOKENS = 1_800
LATER_SHARE = 1.0

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


NOT_REVIEWED = FZ.NOT_REVIEWED


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
    found, settled = FN.settled_as_read(found, ents)
    if settled:
        c.sr.log(
            f"  {settled} finding(s) on rows stated as marked or as written on the page: settled, not raised again"
        )
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


def _closed(f: RR.Finding) -> bool:
    """A finding an applied edit settled, or one the stage settled itself (a
    duplicate pair folded once, a frozen finding the re-check did not raise)."""
    return f.outcome == "applied" or f.outcome.startswith("settled")


def _dedupe(c: Ctx, md: str, todo: list[RR.Finding], base: list[str]) -> tuple[str, list, list]:
    """The document with every decidable duplicate folded or dropped in code
    (``update/duplicates.py``), the edits that made, and the findings left
    for the finisher, as copies on the new document's anchors (a finding on
    an entry that was folded away is not sent: it is re-checked where its
    content went)."""
    keep, heads = _keep(c.sr), headings(c.sr.cfg)
    md_d, dups, moved = DU.resolve(md, todo, keep, heads, base)
    amap = DU.remap(md, md_d, moved, keep, heads) if md_d != md else None
    decided = {e.fid for e in dups if _closed(e)}
    send: list[RR.Finding] = []
    for f in todo:
        a = f.anchor.split(".")[0]
        if f.fid in decided:
            continue
        if a in moved:
            e = copy.copy(f)
            e.outcome = f"its entry {a} was folded into {moved[a]} as a duplicate; checked again there"
            dups.append(e)
            continue
        g = copy.copy(f)
        if amap is not None and a in amap:
            g.anchor = amap[a] + f.anchor[len(a) :]
        send.append(g)
    if dups:
        c.sr.log(
            f"  duplicates decided in code: {sum(e.outcome == 'applied' for e in dups)} folded or dropped, "
            f"{sum(e.outcome.startswith('settled') for e in dups)} settled with their pair, "
            f"{sum(not _closed(e) for e in dups)} left open"
        )
    return md_d, dups, send


def finish(c: Ctx, md: str, found: list[RR.Finding], base: list[str], doc: Path) -> tuple[str, set[int], int, int]:
    """Decide the duplicates in code, then edit, check, apply, audit, revert
    what the audit does not support, and run the audit loop to its gate.
    Returns (document, edited entries, findings settled, the audit loop's
    exit)."""
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
    keep, heads = _keep(c.sr), headings(c.sr.cfg)
    ents0 = RR.anchor_entries(md, keep, heads)
    md_d, dups, send = _dedupe(c, md, todo, base)
    ents = RR.anchor_entries(md_d, keep, heads)
    auto, ask = IL.drop_on_noted(send, ents)  # no model: a drop on a noted handwritten entry
    edits, left = _finish_calls(c, ask, ents, base) if ask else ([], {})
    edits += auto
    new_md, edited = _apply(c, md_d, edits, base)
    # The moved duplicate paragraphs keep their citations and are graded with
    # the finisher's sentences; one the audit does not support is the audit
    # loop's to repair (it is no finisher edit to revert).
    bad, untraced = FN.failed(edits, c.verify(md, new_md)) if new_md != md else ([], 0)
    if bad:
        ids = {id(e) for e in bad}
        for e in edits:
            if id(e) in ids:
                e.outcome = "reverted: the audit did not find the edited sentence on its page"
            elif e.outcome == "applied":
                e.outcome = ""
        new_md, edited = _apply(c, md_d, edits, base)
    settled = FN.settle(todo, edits + dups, left)
    new = {int(r["exhibit"]) for r in _new_records(c.sr)}
    _mark_refiled(c.sr, FN.refiled(dups, ents0, base, new_md, new) | FN.refiled(edits, ents, base, new_md, new))
    c.sr.log(
        f"  finisher: {len(edits)} edit(s), {sum(e.outcome == 'applied' for e in edits)} applied, {len(bad)} reverted; "
        f"{untraced} unsupported sentence(s) no edit wrote, left to the audit loop"
    )
    if new_md == md:
        doc.write_text(md, encoding="utf-8")
        return md, set(), 0, 0
    doc.write_text(new_md, encoding="utf-8")
    rc = audit_loop.run(c.sr)  # every remaining new sentence is audited on its page, to the gate
    keep_illegible(c.sr, B.entries_of(new_md)[1], base, doc)
    return doc.read_text(encoding="utf-8"), edited, settled, rc


def keep_illegible(sr: StageRun, before: list[str], base: list[str], doc: Path) -> None:
    """Each new entry the audit emptied or trimmed on a handwritten page
    carries the firm's illegible note (``update/illegible.py``). The note is
    an [NTD: ...] span, which the audit never grades, so nothing is re-audited."""
    trimmed = IL.trimmed_pages(CL.read_rows(AuditPaths(sr.slug_dir, sr.unit.unit).out / "repair-edits.jsonl"))
    md, done = IL.restore(before, doc.read_text(encoding="utf-8"), base, _handwritten(sr), trimmed)
    if done:
        doc.write_text(md, encoding="utf-8")
        sr.log(f"  {len(done)} entr(ies) the audit emptied or trimmed on a handwritten page carry the illegible note")


def _handwritten(sr: StageRun) -> Callable[[int, int], bool]:
    ev = HE.Evidence(sr.slug_dir, sr.unit.unit, "")
    return lambda ex, p: H.is_handwritten(ev.page(ex, p))


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
def _reuse(sr: StageRun, rec: dict[str, Any], md: str) -> int | None:
    """A rerun on the audit document this stage already settled: not paid again."""
    if rec.get("doc_sha_out") != _sha(md):
        return None
    if rec.get("status") == "clean":
        if rec.get("smd_corrected"):  # ``medchron.update_decide``: SMD corrected what the review held on
            sr.log("update review: SMD corrected the held finding(s) against the pages; not paid again")
        else:
            sr.log("update review: the audit document is the one already reviewed clean; not paid again")
        return 0
    if rec.get("status") == "held":
        if PR.release_requested(sr.job.path.parent) is not None:
            sr.log("update review: released by SMD; the open findings stay in the record")
            return 0
        if not (sr.job.path.parent / PR.REDO_MARKER).is_file():
            # Settled again by today's rules on resume (``update_review_settle``), no model call.
            doc = sr.slug_dir / "runs" / sr.unit.unit / "final-chronology.md"
            return URS.resume_held(sr, rec, update_dir(sr) / RECORD, doc, _sha)
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
    composed = (
        B.split_body((ud / "new_entries.md").read_text(encoding="utf-8"))[1]
        if (ud / "new_entries.md").is_file()
        else []
    )
    keep_illegible(sr, composed, base, doc)
    md = doc.read_text(encoding="utf-8")
    rec: dict[str, Any] = {"model": c.model, "doc_sha_in": _sha(md), "rounds": []}
    return _loop(c, rec, md, base, doc, ud)


def _review_once(
    c: Ctx, rec: dict[str, Any], ud: Path, md: str, base: list[str], targets: set[int] | None, done: int
) -> tuple[list[RR.Finding], int]:
    """This round's review, paid once: a review a killed run already paid for
    (``rec["pending"]``, same round, same document) is read back; a new one is
    checkpointed before anything acts on it."""
    pend = rec.get("pending")
    if isinstance(pend, dict) and pend.get("round") == c.rnd and pend.get("md_sha") == _sha(md):
        c.sr.log(f"update review round {c.rnd}: the review was paid for before the interruption; not paid again")
        return [RR.Finding(**f) for f in pend.get("findings") or []], int(pend.get("entries") or 0)
    found, n = review_round(c, md, base, targets)
    rec["pending"] = {"round": c.rnd, "md_sha": _sha(md), "entries": n, "findings": [f.as_dict() for f in found]}
    _checkpoint(ud, At(done, md, rec))
    return found, n


def _legacy(c: Ctx, rec: dict[str, Any], md: str) -> None:
    """A checkpoint from the loop before the frozen list (2026-10-15): its
    last review's open must-fix become the frozen list, re-anchored onto the
    checkpoint's document by the page each cites (their anchors name the
    document that round reviewed, before its finish, which an old checkpoint
    does not keep); the next step is the re-check."""
    last = (rec.get("rounds") or [{}])[-1]
    must = [RR.Finding(**f) for f in last.get("findings") or [] if isinstance(f, dict)]
    must = [f for f in _must(must) if not _closed(f)]
    rec["frozen"] = FZ.reanchor(must, RR.anchor_entries(md, _keep(c.sr), headings(c.sr.cfg)))
    rec.pop("pending", None)
    c.sr.log(f"update review: a checkpoint from the earlier loop; its {len(must)} open must-fix are the frozen list")


def _loop(c: Ctx, rec: dict[str, Any], md: str, base: list[str], doc: Path, ud: Path, at: At | None = None) -> int:
    """Round 1: the full review, its MUST findings frozen, one finish. Round 2:
    the narrow re-check of the frozen findings still open, one more finish of
    those it confirms. Then clean or held; never a third review."""
    done = at.done if at else 0
    if done >= REVIEW and "frozen" not in rec:
        _legacy(c, rec, md)
    if done < REVIEW:
        c.rnd, c.calls, c.usage = REVIEW, 0, []
        found, n = _review_once(c, rec, ud, md, base, None, 0)
        must = _must(found)
        rec["frozen"] = FZ.freeze(must, RR.anchor_entries(md, _keep(c.sr), headings(c.sr.cfg)))
        settled, rc = 0, 0
        if found:
            try:
                md, _edited, settled, rc = finish(c, md, found, base, doc)
            except GradeFailed as exc:
                doc.write_text(md, encoding="utf-8")  # the round's edits are not kept on a non-answer
                c.sr.log(f"update review: {exc}; nothing is reverted or kept, a rerun grades again")
                return 1
        rec.pop("pending", None)
        for row, f in zip(rec["frozen"], must):
            row["finding"] = f.as_dict()
        left = sum(not _closed(f) for f in must)
        rec["rounds"].append(
            {"round": REVIEW, "kind": "review", "entries": n, "must": len(must), "calls": c.calls, "usage": c.usage}
            | {"left": left, "findings": [f.as_dict() for f in found]}
        )
        c.sr.log(
            f"update review round {REVIEW} (full review): {n} entr(ies) reviewed, {len(must)} must-fix frozen, "
            f"{len(found) - len(must)} other; {settled} settled by the finisher, {left} must-fix left; "
            f"{c.calls} call(s), {_dollars(c.usage)}"
        )
        if rc != 0:
            _save(ud / RECORD, rec, "audit_failed", doc)
            return 3 if rc == 1 else 4
        if not left:
            _save(ud / RECORD, rec, "clean", doc)
            return 0
        _checkpoint(ud, At(REVIEW, md, rec))
    return _recheck(c, rec, md, base, doc, ud)


def _recheck(c: Ctx, rec: dict[str, Any], md: str, base: list[str], doc: Path, ud: Path) -> int:
    """The narrow re-check and the one more finish (``update/frozen.py``)."""
    rows = rec["frozen"]
    frozen = [FZ.finding(r) for r in rows]
    open_ = [i for i, f in enumerate(frozen) if not _closed(f)]
    if not open_:
        _save(ud / RECORD, rec, "clean", doc)
        return 0
    c.rnd, c.calls, c.usage = RECHECK, 0, []
    ents = RR.anchor_entries(md, _keep(c.sr), headings(c.sr.cfg))
    targets = FZ.locate([rows[i] for i in open_], ents)
    found, n = _review_once(c, rec, ud, md, base, targets, REVIEW)
    todo, unreviewed, confirms = FZ.triage(rows, open_, found, ents)
    keys = [FZ.key(f, ents) for f in found]  # before the finish moves any anchor
    settled, rc = 0, 0
    if todo:
        try:
            md, _edited, settled, rc = finish(c, md, todo, base, doc)
        except GradeFailed as exc:
            doc.write_text(md, encoding="utf-8")
            c.sr.log(f"update review: {exc}; nothing is reverted or kept, a rerun grades again")
            return 1
    rec.pop("pending", None)
    for i in open_:
        f = frozen[i]
        if f.klass == FZ.NOT_REVIEWED:
            f.outcome = f.outcome if unreviewed else "settled: its entries were reviewed on the re-check"
        elif i not in confirms:
            f.outcome = FZ.SETTLED
        elif all(_closed(g) for g in confirms[i]):
            f.outcome = "applied"
        else:
            f.outcome = "confirmed on re-check; " + "; ".join(dict.fromkeys(g.outcome for g in confirms[i]))[:400]
        rows[i]["finding"] = f.as_dict()
    still = [f for f in todo if not _closed(f)] + unreviewed
    advisory = sum(f.outcome == FZ.ADVISORY for f in found)
    rec["rounds"].append(
        {"round": RECHECK, "kind": "re-check", "entries": n, "must": len(_must(found)), "calls": c.calls}
        | {"usage": c.usage, "confirmed": len(confirms), "advisory": advisory, "left": len(still)}
        | {"keys": keys, "findings": [f.as_dict() for f in found]}
    )
    c.sr.log(
        f"update review round {RECHECK} (re-check of {len(open_)} open frozen finding(s)): {n} entr(ies) reviewed, "
        f"{len(confirms)} confirmed, {len(open_) - len(confirms)} settled as not raised again, {advisory} advisory; "
        f"{settled} settled by the finisher, {len(still)} must-fix left; {c.calls} call(s), {_dollars(c.usage)}"
    )
    if rc != 0:
        _save(ud / RECORD, rec, "audit_failed", doc)
        return 3 if rc == 1 else 4
    if not still:
        _save(ud / RECORD, rec, "clean", doc)
        return 0
    return _hold(c, rec, still, doc, ud)


def _hold(c: Ctx, rec: dict[str, Any], open_: list[RR.Finding], doc: Path, ud: Path) -> int:
    """Before it holds, the chronology review's $0 rules (``update_review_settle``)."""
    open_ = URS.settle(c.sr, doc, open_, rec)
    if not open_:
        _save(ud / RECORD, rec, "clean", doc)
        return 0
    _save(ud / RECORD, rec, "held", doc, open_)
    for f in open_:
        c.sr.log(f"  OPEN [{f.klass}] {URS.plain(f)}" + (f"; not settled: {f.outcome}" if f.outcome else ""))
    c.sr.log(URS.hold_line(open_))
    return 2


def _save(path: Path, rec: dict[str, Any], status: str, doc: Path, open_: list[RR.Finding] | None = None) -> None:
    rec.pop("pending", None)
    rec["status"] = status
    rec["doc_sha_out"] = _sha(doc.read_text(encoding="utf-8"))
    rec["open"] = [f.as_dict() for f in open_ or []]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    _end(path.parent)  # a settled loop is never resumed


def projected_usd(slug_dir: Path, unit: str, cfg: Any, pricing: budget.Pricing) -> float | None:
    """What this stage is projected to cost, from this matter's own pages:
    every page of the new exhibits shown once to the review, the same again
    for the rest (two finishes and the narrow re-check, at most), priced at the review model's input
    rate (output is bounded by the parts and small beside the pages)."""
    out = slug_dir / "out" / unit
    doc = slug_dir / "runs" / unit / "final-chronology.md"
    rec = read_json(slug_dir / "runs" / unit / "update" / RECORD, {})
    if rec.get("status") == "clean" and doc.is_file() and rec.get("doc_sha_out") == _sha(doc.read_text("utf-8")):
        # ``_reuse`` returns at once on this document (a resume after SMD's
        # correction, ``medchron.update_decide``): projecting a full review
        # here would hold a resumed job on the cap for money it never spends.
        return 0.0
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
