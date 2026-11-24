"""A set revision with the firm's rules applied by the firm-rules stage
(``rulebook``): the path ``ReviseSetRun`` takes when the firm's
``phrase_book`` lists the ``revise`` stage. Off, nothing here runs and the
revision is today's (``rules.py``: quoted words only).

On, per job:

1. the attorney's own instructions read once from his email and checked
   (``instructions.py``); a refusal fails the job, nothing filed;
2. per document in scope, the stage applies the rules in force and those
   instructions (``rulebook.stage.run``), returning the document's markdown and
   which response each rule or instruction was applied to;
3. code then settles the candidate-objection markers a rule or instruction
   names (``markers.py``), writes the facts the attorney stated, per attorney,
   and keeps the Index of Exhibits true to the responses (``index_fix.py``), in
   the document or in its paired index file;
4. each document is edited paragraph by paragraph in the filed file and proven
   (``blocks.prove``): only responses a rule, instruction, marker or fact
   names change, outside them only the index lines;
5. the notes say per document what changed and why, and what was left and
   why (``notes_book.py``), and when the delivery carries attorney notes they
   are filed as a new version too.

Every model answer goes through the job's doorway, so the $0 rehearsal replays
the same calls from recordings.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ...demand import deliver
from ...matter import pull as matter_pull
from ..outcome import DraftingFailed, DraftingHold
from ..rulebook import book as book_mod, hook as rule_hook, reading as rule_reading, stage as rule_stage
from ..rulebook.types import SET_DOC, Applied, Finding, Instruction, Left, StageDoc, StageJob, StageResult
from . import blocks, index_fix, instructions as ins_mod, markers, notes_book, prior as prior_mod
from . import responses as rs, set_units as su

INSTRUCTIONS = "instructions.json"
STAGE_FILE = "stage.json"
INDEX_PLAN = "index-plan.json"
NOTES_DIR = "notes"
#: Why a response changed when the change is a proposed objection taken.
MARKER_SOURCE = "marker"


def on(run: Any) -> bool:
    return rule_hook.enabled(run.firm, "revise")


def book(run: Any) -> Any:
    """The firm's phrase book; a firm that switched the stage on with no
    readable book is our configuration's fault, never a silent old path."""
    try:
        got = book_mod.load(run.firm)
    except (book_mod.BookError, OSError, KeyError) as exc:
        raise DraftingFailed(f"config_missing: the firm's phrase book cannot be read: {exc}"[:300]) from None
    if got is None:
        raise DraftingFailed("config_missing: the revise stage is on and the firm has no phrase book")
    return got


def _job(run: Any) -> StageJob:
    return StageJob(
        data=run.data,
        firm=run.firm,
        ask=run._ask(run.firm.model("audit")),
        log=run.log,
        request_text=str(run.job.request_text or ""),
        mode="revise",
    )


def _docs(run: Any) -> list[ins_mod.Doc]:
    return [
        ins_mod.Doc(f"{k:02d}", run._ujson(k, "base.json")["set_type"], tuple(run._ujson(k, "base.json")["numbers"]))
        for k in run._scoped()
    ]


def _stage_doc(run: Any, k: int, read: ins_mod.Read) -> StageDoc:
    base = run._ujson(k, "base.json")
    return StageDoc(
        key=f"{k:02d}",
        md=(run._udir(k) / "base.md").read_text(encoding="utf-8") + _paired_index(run, k),
        doc_type=base["set_type"],
        hint=run._hint(k),
        instructions=read.instructions.get(f"{k:02d}", ()),
    )


def _paired_index(run: Any, k: int) -> str:
    """The Index of Exhibits of the index file paired with production document
    ``k``, as an index region after its responses, so the stage names exhibit
    letters from it (4 of 7 real requests filed the index as its own file,
    2026-11-23). Only the stage reads it: the filed document is built from its
    own base (``render``), so the region never reaches the file."""
    for j in range(len(run.units)):
        b = run._ujson(j, "base.json") or {}
        if b.get("index") and b.get("paired") == k:
            doc = rs.read((run._udir(j) / "base.md").read_text(encoding="utf-8"))
            if doc.index is not None:
                lines = [doc.blocks[i] for i in doc.index.blocks]
                return "\n" + "\n\n".join(["INDEX OF EXHIBITS", *lines]) + "\n"
    return ""


def _read(run: Any) -> ins_mod.Read:
    return ins_mod.Read.from_dict(run._json(INSTRUCTIONS))


# ---- revise-rules / revise-base / revise-estimate -----------------------------------
def pair_indexes(run: Any) -> None:
    """Each index file paired with the request's one production document."""
    bases = [run._ujson(k, "base.json") for k in range(len(run.units))]
    for k, b in enumerate(bases):
        if not b.get("index"):
            continue
        j, why = index_fix.pair(bases, k)
        if j is None:
            raise DraftingFailed(f"{why}; nothing was filed")
        run._uwrite(k, "base.json", {**b, "paired": j})


def estimate(run: Any) -> int:
    docs = [_stage_doc(run, k, ins_mod.Read({})) for k in run._scoped()]
    asks = ins_mod.plan_chars(str(run.job.request_text or ""), _docs(run), book(run))
    return rule_stage.plan_chars(_job(run), docs) + asks


# ---- revise-values ---------------------------------------------------------------------
def values(run: Any) -> None:
    bk = book(run)
    if not (run.data / INSTRUCTIONS).is_file():
        try:
            got = ins_mod.read(str(run.job.request_text or ""), _docs(run), bk, run._ask(run.firm.model("audit")))
        except ins_mod.InstructionsRefused as exc:
            raise DraftingFailed(
                f"revise_unchecked: the instructions in the request could not be read so that every response it "
                f"names is accounted for: {exc}"[:480]
            ) from None
        run._write(INSTRUCTIONS, got.to_dict())
    read = _read(run)
    for k in run._scoped():
        if (run._udir(k) / STAGE_FILE).is_file():
            continue  # a resume: this document went through the stage already
        res = rule_stage.run(_job(run), _stage_doc(run, k, read))
        run._uwrite(k, STAGE_FILE, _result_to(res))


def _result_to(res: StageResult) -> dict[str, Any]:
    return {
        "md": res.md,
        "applied": [vars(a) for a in res.applied],
        "left": [vars(x) for x in res.left],
        "findings": [vars(f) for f in res.findings],
        "notes": list(res.notes),
    }


def _written_lines(res: StageResult) -> set[str]:
    """The stage's notes lines for an objection written for this request."""
    mark = "an objection was written for this request"
    return {n for n in res.notes if mark in n and mark in rule_stage.NOTES[rule_stage.WRITTEN]}


def _result_from(raw: dict[str, Any]) -> StageResult:
    return StageResult(
        md=raw["md"],
        applied=tuple(Applied(**a) for a in raw.get("applied") or []),
        left=tuple(Left(**x) for x in raw.get("left") or []),
        findings=tuple(Finding(**f) for f in raw.get("findings") or []),
        notes=tuple(raw.get("notes") or []),
    )


# ---- revise-render ---------------------------------------------------------------------
def _stage_bodies(
    doc: rs.Document, res: StageResult, hint: str | None, why: dict[str, list[str]]
) -> dict[str, list[str]]:
    """The responses the stage changed, each only where it applied something."""
    try:
        got = rs.read(res.md, hint)
    except rs.ResponsesUnread as exc:
        raise su.Refused("revise_layout_lost", f"the firm-rules stage returned a document that does not read: {exc}")
    if got.numbers != doc.numbers:
        raise su.Refused("revise_layout_lost", "the firm-rules stage changed which responses the document has")
    applied: dict[str, list[str]] = {}
    for a in res.applied:
        applied.setdefault(str(a.item), []).append(a.source)
    out: dict[str, list[str]] = {}
    gb = got.by_number()
    for r in doc.responses:
        if rs.plain(doc.text_of(r)) == rs.plain(got.text_of(gb[r.number])):
            continue
        if r.number not in applied:
            raise su.Refused("revise_layout_lost", f"response {r.number} changed with nothing applied to it")
        out[r.number] = [got.blocks[i] for i in gb[r.number].editable]
        why.setdefault(r.number, []).extend(applied[r.number])
    return out


def _standing_grounds(run: Any, bk: Any, set_type: str) -> set[str]:
    """The objection keys the firm's STANDING rules ask for in this kind of
    document, from the stage's own reading of them (``rule-read.json``): a
    rule's one-matter segment ("Response #9: add privacy") never reaches
    another response's marker. A rule the stage did not read names none."""
    rules = run._rules_in_force()
    if not rules:
        return set()
    try:
        clauses, grown = rule_reading.read(run.data, rules, bk, None)
    except rule_reading.ReadRefused:
        return set()
    dtype = SET_DOC.get(set_type, "")
    keys: set[str] = set()
    for c in clauses:
        if c.standing and c.act == "add_objection" and dtype in c.doc_types:
            keys |= set(c.keys) | markers.named_by(c.grounds, grown)
    return keys


def _allowed_keys(run: Any, bk: Any, ins: tuple[Instruction, ...], number: str, standing: set[str]) -> set[str]:
    keys = set(standing)
    for i in ins:
        if number in i.numbers and i.act == "add_objection":
            keys |= set(i.keys)
    return keys


def _settle(run: Any, k: int, doc: rs.Document, bodies: dict[str, list[str]], plan: dict[str, Any]) -> None:
    bk, st = book(run), run._ujson(k, "base.json")["set_type"]
    ins = _read(run).instructions.get(f"{k:02d}", ())
    standing = _standing_grounds(run, bk, st)
    for r in doc.responses:
        cur = bodies.get(r.number) or [doc.blocks[i] for i in r.editable]
        if not any(markers.spans(b) for b in cur):
            continue
        new, done = markers.settle(r.number, cur, bk, st, _allowed_keys(run, bk, ins, r.number, standing))
        for s in done:
            if s.left:
                plan["left"].append([s.number, s.left, s.ground[:80]])
        if new != cur:
            bodies[r.number] = new
            plan["why_changed"].setdefault(r.number, []).append(MARKER_SOURCE)


def _facts(run: Any, k: int, doc: rs.Document, bodies: dict[str, list[str]], plan: dict[str, Any]) -> None:
    by = doc.by_number()
    for f in _read(run).facts:
        if f.unit != f"{k:02d}" or f.number not in by:
            continue
        cur = bodies.get(f.number) or [doc.blocks[i] for i in by[f.number].editable]
        if not cur or rs.plain(f.text) in rs.plain(" ".join(cur)):
            continue
        bodies[f.number] = [*cur[:-1], f"{cur[-1]} {f.text}"]
        plan["facts"].append({"number": f.number, "text": f.text})
        plan["why_changed"].setdefault(f.number, []).append("email")


def _index_target(run: Any, k: int, doc: rs.Document) -> tuple[int, rs.Document] | None:
    if doc.index is not None:
        return k, doc
    for j in range(len(run.units)):
        b = run._ujson(j, "base.json") or {}
        if b.get("index") and b.get("paired") == k:
            return j, rs.read((run._udir(j) / "base.md").read_text(encoding="utf-8"))
    return None


def render(run: Any, k: int) -> dict[str, Any]:
    """Unit ``k`` revised and proven; its plan and record in ``render.json``."""
    d, hint = run._udir(k), run._hint(k)
    base_md = (d / "base.md").read_text(encoding="utf-8")
    doc = rs.read(base_md, hint)
    res = _result_from(run._ujson(k, STAGE_FILE))
    plan: dict[str, Any] = {"why_changed": {}, "facts": [], "left": [], "index_did": []}
    plan["left"] += [[x.item, x.reason, x.source] for x in res.left]
    # An objection the firm's book has no sentence for, written for this request (S amendment): named as such.
    plan["written"] = [n for n in res.notes if n in _written_lines(res)]
    try:
        bodies = _stage_bodies(doc, res, hint, plan["why_changed"])
        _settle(run, k, doc, bodies, plan)
        _facts(run, k, doc, bodies, plan)
        after = rs.read(blocks.build(doc, bodies), hint)
        target = _index_target(run, k, doc)
        edits: dict[int, str] = {}
        if target is not None:
            change = index_fix.recompute(target[1], index_fix.produced(doc), index_fix.produced(after))
            plan["index_did"], plan["left"] = list(change.did), plan["left"] + [list(x) for x in change.left]
            if target[0] == k:
                edits = change.edits
            else:
                run._uwrite(target[0], INDEX_PLAN, {"edits": {str(i): t for i, t in change.edits.items()}})
        new_md = blocks.build(doc, bodies, edits)
        rec = blocks.prove(
            base_md, d / "filed.docx", new_md, d / "out" / run.units[k]["name"], set(bodies),
            index_ok=bool(edits), cls=run.cls, fmt=run._fmt(), set_type=hint,
        )  # fmt: skip
    except su.Refused as exc:
        raise DraftingFailed(f"{exc.code}: {run.units[k]['name']}: {exc.why}; nothing was filed") from None
    except ValueError as exc:  # the revised paragraphs no longer read as the same numbered responses
        raise DraftingFailed(f"revise_layout_lost: {run.units[k]['name']}: {exc}; nothing was filed"[:480]) from None
    return _finish(run, k, {**plan, **rec})


def render_index(run: Any, k: int) -> dict[str, Any]:
    """An index file: its lines as its paired production document's plan says."""
    d = run._udir(k)
    p = d / INDEX_PLAN
    edits = {int(i): t for i, t in json.loads(p.read_text(encoding="utf-8"))["edits"].items()} if p.is_file() else {}
    base_md = (d / "base.md").read_text(encoding="utf-8")
    try:
        new_md = blocks.build(rs.read(base_md), {}, edits)
        rec = blocks.prove(
            base_md, d / "filed.docx", new_md, d / "out" / run.units[k]["name"], set(),
            index_ok=True, cls=run.cls, fmt=run._fmt(),
        )  # fmt: skip
    except su.Refused as exc:
        raise DraftingFailed(f"{exc.code}: {run.units[k]['name']}: {exc.why}; nothing was filed") from None
    did = [f"{len(edits)} line(s) brought into line with the production responses"] if edits else []
    return _finish(run, k, {"why_changed": {}, "facts": [], "left": [], "index_did": did, **rec})


def _finish(run: Any, k: int, rec: dict[str, Any]) -> dict[str, Any]:
    rec["skipped"] = []
    if rec.get("out"):
        blob = Path(rec["out"]).read_bytes()
        rec.update(sha256=hashlib.sha256(blob).hexdigest(), bytes=len(blob))
    run._uwrite(k, "render.json", rec)
    return rec


def render_set(run: Any) -> dict[int, dict[str, Any]]:
    """Every document in scope, then every index file its production document planned."""
    recs = {k: render(run, k) for k in run._scoped()}
    for k in range(len(run.units)):
        if (run._ujson(k, "base.json") or {}).get("index"):
            recs[k] = render_index(run, k)
    return recs


# ---- notes ---------------------------------------------------------------------------
def notes(run: Any) -> str:
    units = []
    for k, u in enumerate(run.units):
        base = run._ujson(k, "base.json") or {}
        rec: dict[str, Any] = dict(run._ujson(k, "render.json") or {})
        if not base.get("in_scope") and not base.get("index"):
            rec = {"kept": "Not in this request's scope; left as filed."}
        rec["newer_than_delivery"] = bool(base.get("newer_than_delivery"))
        units.append((u["name"], rec))
    rules = run._rules_in_force()
    return notes_book.write("The firm's rules and your instructions, made in the filed responses", units, rules)


def notes_base(run: Any) -> None:
    """The delivery's attorney notes as filed, when the broker names them."""
    n = run.revise.get("notes")
    if not n:
        return
    d = run.data / NOTES_DIR
    d.mkdir(parents=True, exist_ok=True)
    before = run._version(n["file_id"])
    url = matter_pull.mint_one(run.seat, run.job.file_to_id, n["file_id"]).get("url")
    if not url:
        raise DraftingFailed("stage_unfinished: revise-base: no download could be minted for the attorney notes")
    matter_pull.fetch_one(run.seat, url, d / "filed.docx", None)
    (d / "prior.md").write_text(prior_mod.notes_md(d / "filed.docx"), encoding="utf-8")
    sha = prior_mod.sha(d / "filed.docx")
    (d / "base.json").write_text(json.dumps({"version": before, "sha256": sha}), encoding="utf-8")


def notes_plan(run: Any, changes: str) -> dict[str, Any] | None:
    """The attorney notes' new version, ready to file after the documents."""
    n = run.revise.get("notes")
    if not n:
        return None
    d = run.data / NOTES_DIR
    base = json.loads((d / "base.json").read_text(encoding="utf-8"))
    p = run.data / "versions.json"
    sent = (json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}).get("notes", {}).get("sent")
    if not sent and run._version(n["file_id"]) != base["version"]:
        # Checked before the first upload, as the documents are (``set_run._rebase``): a
        # staff edit to the notes must never leave the drafts filed and the notes refused.
        raise DraftingHold(
            f"filing_refused: {n['name']} was saved again while the revision was made; nothing was written"
        )
    md = notes_book.with_prior(changes, (d / "prior.md").read_text(encoding="utf-8"))
    out = deliver.render_plain(md, d / "out", n["name"], str(run.firm.get("firm", "display_name")))
    blob = out.read_bytes()
    return {
        "key": "notes", "role": "attorney_notes", "file_id": n["file_id"], "folder_id": n["folder_id"],
        "name": n["name"], "local_path": str(out), "sha256": hashlib.sha256(blob).hexdigest(), "bytes": len(blob),
        "base_sha256": base["sha256"], "base_version": base["version"],
    }  # fmt: skip


def hold_rebase(run: Any, k: int) -> None:
    raise DraftingHold(
        f"filing_refused: {run.units[k]['name']} was saved again while the revision was made; nothing more was written"
    )


__all__ = ["estimate", "hold_rebase", "notes", "notes_base", "notes_plan", "on", "pair_indexes", "render_set", "values"]
