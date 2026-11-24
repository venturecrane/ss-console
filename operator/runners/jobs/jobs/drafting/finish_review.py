"""The second half of ``Finish``: the attorney notes written from the final
documents, then the independent pre-delivery review over the documents and
the notes as they would be filed (``review.py``). Split from ``finish.py`` at
the module-size ceiling; ``Finish`` inherits it, so every caller is unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from . import grounding, notes as notes_mod, pleading, render, review as review_mod, set_package, wording
from .forms import lane_hook as form_hook
from .outcome import DraftingFailed

if TYPE_CHECKING:
    from .finish import Unit

FINAL = "draft-final.md"


class FinishReview:
    """Mixin of ``Finish``: what ``Finish`` and ``DraftingRun`` supply is below."""

    if TYPE_CHECKING:
        data: Path
        job: Any
        firm: Any
        cls: str
        doorway: Any
        log: Callable[[str], None]

        @property
        def _title(self) -> str: ...
        def units(self) -> list[Unit]: ...
        def corpus(self) -> dict[str, str]: ...
        def _rendered(self, u: Unit) -> dict[str, Any]: ...
        def _papers(self) -> list[review_mod.Paper]: ...
        def _done(self, stage: str, **info: Any) -> None: ...
        def _is_done(self, stage: str) -> bool: ...
        def _json(self, name: str) -> Any: ...
        def _atomic(self, name: str, text: str) -> None: ...
        def _context(self) -> list[str]: ...
        def served_review(self) -> dict[str, dict[str, Any]] | None: ...
        def _state(self) -> dict[str, Any]: ...

    def _all_markers(self) -> list[dict[str, str]]:
        seen, out = set(), []
        for u in self.units():
            for m in self._rendered(u)["markers"]:
                k = (m["kind"], m["text"])
                if k not in seen:
                    seen.add(k)
                    out.append(m)
        return out

    def _located_markers(self) -> list[dict[str, str]]:
        """Every marker once per document and place, named by both (notes)."""
        seen, out = set(), []
        for u in self.units():
            r = self._rendered(u)
            for m in r.get("located") or [{**x, "where": ""} for x in r["markers"]]:
                where = ", ".join(x for x in ((u.label if u.key else ""), m.get("where") or "") if x)
                k = (m["kind"], m["text"], where)
                if k not in seen:
                    seen.add(k)
                    out.append({"kind": m["kind"], "text": m["text"], "where": where})
        return out

    # ---- the notes from the final documents, then the review, then the files -----------
    def _notes(self, digest: str, review_findings: list[str] | None = None) -> str:
        """``review_findings``: what a held review found wrong in the notes
        only; the tables are written again, told exactly that (``_review``)."""
        if (self.data / "notes-final.md").is_file() and not review_findings:
            return (self.data / "notes-final.md").read_text(encoding="utf-8")
        tables, open_items = [], []
        for u in self.units():
            end = render.split_notes((u.data / FINAL).read_text(encoding="utf-8"))[1]
            if end:
                tables.append((f"### {u.label}\n\n" if u.key else "") + end)
            fp = json.loads((u.data / "final-pass.json").read_text(encoding="utf-8"))
            open_items += [(f"{u.label}: " if u.key else "") + x for x in fp["drifts"]]
        papers = self._papers()
        facts = self._request_facts()

        def tables_for(refused: list[str] | None) -> str:
            raw = review_mod.final_notes(
                self.doorway,
                self.firm.model("audit"),
                digest,
                papers,
                "\n\n".join(tables),
                open_items,
                self.job.job_id[-6:],
                facts=facts,
                refused=refused,
            )
            # Rows, checked word for word against the file; only what verifies
            # is rendered (grounding.py).
            g = grounding.verify(grounding.parse(raw), self.corpus())
            self._atomic("notes-basis.json", json.dumps({"kept": len(g.rows), "dropped": g.dropped}, indent=1))
            return grounding.render_table(g, grounding.documents(papers))

        end_tables = tables_for(review_findings or None)
        problems = wording.notes_findings(end_tables)
        if problems:
            self.log(f"  attorney notes: {len(problems)} wording problem(s); one rewrite")
            end_tables = tables_for([*(review_findings or []), *problems])
            problems = wording.notes_findings(end_tables)
            if problems:
                # Counts and kinds only: a reason reaches SMD's alerts.
                raise DraftingFailed(
                    f"notes_unclean: the attorney notes still carry {len(problems)} problem(s) after a rewrite: "
                    + wording.summary(end_tables)
                )
        render_notes = []
        for u in self.units():
            render_notes += [(f"{u.label}: " if u.key else "") + n for n in self._rendered(u)["notes"]]
        units = [(u.label, u.data) for u in self.units()] if any(u.key for u in self.units()) else None
        basis = set_package.note(self._state().get(set_package.BASIS_KEY), [str(u.variant) for u in self.units()])
        render_notes = [self._block_note(), *([basis] if basis else []), *render_notes]
        md = notes_mod.attorney_notes(self.data, self._title, self._located_markers(), render_notes, end_tables, units)
        md = form_hook.with_cite_tables(self, md)  # each form set's cite table, by code
        # SMD's hand-written sections of the notes this replaces, carried by
        # code: never sent to the model above, kept byte for byte (2026-11-05).
        prior = self.data / "notes-final.md"
        md = notes_mod.carry_smd(prior.read_text(encoding="utf-8") if prior.is_file() else None, md)
        self._atomic("notes-final.md", md)
        return md

    def _block_note(self) -> str:
        """The attorney block, said to the attorney (the facts line is for the
        notes model: "the firm's authored block" is our word, not his)."""
        cp = self.data / "caption.json"
        cap = json.loads(cp.read_text(encoding="utf-8")) if cp.is_file() else {}
        who = str((cap.get("attorney_block") or {}).get("email") or "").lower()
        if not who:
            return "Attorney block: taken from the operative pleading (no standard block is on file for you)"
        if who == str(self.job.requester or "").strip().lower():
            return "Attorney block: your standard block, as the firm keeps it"
        return f"Attorney block: the standard block of {who}, the attorney the matter record names"

    def _request_facts(self) -> list[str]:
        """What the request and the matter record settle about who: the
        requester, the responsible attorney, and whose authored attorney block
        the documents carry (the LAST line, which the notes also print). Facts,
        so the notes never hedge on them (2026-10-13)."""
        f = self._json("facts.json")
        cp = self.data / "caption.json"
        cap = json.loads(cp.read_text(encoding="utf-8")) if cp.is_file() else {}
        resp = str(f.get("responsible_attorney") or "").strip()
        req = str(self.job.requester or "").strip().lower()
        out = [
            f"the documents were requested by {req}; what the request states about the file or about who handles "
            "or signs it is adopted as stated, never questioned",
            f"the matter record names {resp} as the responsible attorney"
            if resp
            else "the matter record names no responsible attorney",
        ]
        chosen = cap.get("attorney_block") or {}
        who = str(chosen.get("email") or "").lower()
        if not who:
            out.append("the firm has no authored attorney block for the requesting or the responsible attorney")
        elif who == req:
            out.append(f"the attorney block is the firm's authored block for {who}, who requested the documents")
        else:
            # Chosen by the matter record's attorney email (attorneys.pick):
            # say that, never a name the code did not compare.
            out.append(
                f"the attorney block is the firm's authored block for {who}, the attorney the matter record names"
            )
        return out

    def _fresh_review(self, digest: str, notes_md: str) -> tuple[list[review_mod.Finding], str]:
        papers = self._papers()
        found = review_mod.code_findings(
            self.cls,
            papers,
            self.job.request_text,
            digest,
            self.corpus(),
            pleading.spec_for(self.firm.data["format"]) is not None,
            notes_md=notes_md,
            served=self.served_review(),
        )
        served = (
            review_mod.discovery_papers(self.corpus()) if self.cls in render.DISCOVERY else "(not a discovery document)"
        )
        user = review_mod.review_user(self.job.request_text, self._context(), papers, notes_md, found, served)
        # The review's own model, else the auditor's: never compose, the
        # drafter grading its own document (2026-10-13).
        model = review_mod.model_for(self.firm)
        return found + review_mod.run_model(self.doorway, model, digest, user, self.job.job_id[-6:]), model

    def _notes_only_hold(self) -> list[str]:
        """The held review's MUST-FIX findings when every one is about the
        attorney notes (check 6) and none about a document; else []."""
        rec = review_mod.load(self.data) or {}
        must = [f for f in rec.get("findings") or [] if f.get("grade") == review_mod.MUST]
        if not must or rec.get("released"):
            return []
        if not all(str(f.get("check") or "").strip().startswith("6") for f in must):
            return []
        return [f"the review found in the notes: {f['finding']} ({f.get('cite') or ''})" for f in must]

    def _review(self, digest: str, notes_md: str) -> dict[str, Any]:
        """The independent review (``review.py``): a hold raises ``review_held``
        before anything is filed; SMD's redo and release arrive as markers in
        the job dir and are applied here."""
        try:
            rec = review_mod.decide(self.data, self.job.job_dir, lambda: self._fresh_review(digest, notes_md), self.log)
        except DraftingFailed:
            notes_only = self._notes_only_hold()
            if not notes_only or self._is_done("notes_rewrite"):
                raise
            # The notes tables are written by a model each run, and a cite in
            # them can slip (2026-10-14, run 8: a recipient's address cited to
            # responses that do not say it). When the review holds on the
            # notes alone, the documents are right: write the tables once more,
            # told exactly what was wrong, and review again. Once.
            self.log(f"  review: {len(notes_only)} notes-only must-fix finding(s); one notes rewrite")
            held = self.data / "review.json"
            held.replace(self.data / "review-held-notes.json")
            notes_md = self._notes(digest, notes_only)
            self._done("notes_rewrite", findings=len(notes_only))
            rec = review_mod.decide(self.data, self.job.job_dir, lambda: self._fresh_review(digest, notes_md), self.log)
        self._done(
            "review",
            review="released" if rec.get("released") else "passed",
            model=str(rec.get("model") or ""),
            must_fix=rec["must_fix"],
            should_fix=rec["should_fix"],
            notes=rec["notes"],
        )
        return rec
