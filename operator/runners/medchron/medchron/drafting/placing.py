"""The placing flow: an attorney's own attached sets, each placed in the
court's caption on the firm's paper, reviewed, then filed (``place.py`` holds
the pure part; this is the job's half). A mixin on ``DraftingRun``, beside
``finish.Finish``: it reuses the unit directories, the paper rule, the review
and the manifest, and never composes, audits or repairs a word of his."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..demand import deliver
from . import format_check, pleading, place, render, review as review_mod
from .finish import Unit
from .outcome import DraftingFailed, DraftingHold, markers

#: Format findings about HIS content (his labels, his Definitions, a declaration
#: he wrote): his to decide, reported in the notes, never a refusal of ours.
HIS = re.compile(r"(?i)^(discovery: no item labels|definitions:|attachments: section 2030\.050|label:|request:|spacing:)")


class Placing:
    def placing(self) -> bool:
        return self.cls == "discovery_set" and bool(self.job.attachments)

    def _theirs(self) -> list[tuple[Unit, place.Theirs]]:
        if getattr(self, "_placed_units", None) is not None:
            return self._placed_units  # type: ignore[has-type]
        read = [place.read(n, b) for n, b in self.job.attachments]
        sets_ = [t for t in read if t.kinds]
        if not sets_:
            raise DraftingHold(
                "request_incomplete: the attached documents carry no discovery requests to place in the caption"
            )
        plan = self._state().get("units")
        if plan is None:
            plan = []
            for i, t in enumerate(sets_, 1):
                label = place.VARIANT_TITLES.get(t.kinds[0], "Discovery").title().replace(" Of ", " of ")
                if len(t.kinds) > 1 or sum(1 for x in sets_ if x.kinds[:1] == t.kinds[:1]) > 1:
                    label = f"{label} {i}"
                plan.append({"key": f"set-{i}", "variant": t.kinds[0], "label": label})
            self._put("units", plan)
        out = []
        for p, t in zip(plan, sets_):
            d = self.data / p["key"]
            d.mkdir(parents=True, exist_ok=True)
            out.append((Unit(p["key"], p["label"], p["variant"], None, d), t))
        self._placed_units = out
        self._units = [u for u, _t in out]
        return out

    def _place_unit(self, u: Unit, t: place.Theirs) -> None:
        if self._is_done(u.stage("placed")):
            return
        cap = self._json("caption.json") if (self.data / "caption.json").is_file() else {}
        fields = cap.get("fields") or {}
        block = (cap.get("attorney_block") or {}).get("lines")
        pos = render.COMMENT.sub("", self.firm.attachment("pos")).strip()
        placed = place.place(t, fields, block, pos)
        out, fmt, paper = deliver.out_dir(self.data), self.firm.data["format"], self.paper()
        path, report = render.render(placed.markdown, self.cls, out / self._names(u)["draft"], fmt)
        laid = pleading.apply(path, pleading.spec_for(fmt)) if paper == "pleading" else []  # type: ignore[arg-type]
        res = format_check.check(path, self.cls, fmt, "", None, paper)
        ours = [f for f in res.fails if not HIS.match(f)]
        if ours:
            raise DraftingFailed(
                "format_check: the format check refused the placed document: " + "; ".join(ours)[:400]
            )
        his = [f for f in res.fails if HIS.match(f)]
        self._uwrite(u, "theirs.json", {"name": t.name, "body": placed.body, "added": placed.added})
        self._uwrite(
            u,
            "rendered.json",
            {
                "path": str(path),
                "paper": paper,
                "markers": markers(placed.markdown),
                "notes": [
                    f"placed {t.name} verbatim; the job added: " + ", ".join(placed.added),
                    *[f"in his text, for his eye only (not changed): {f}" for f in his],
                    *report.get("notes", []),
                    *laid,
                ],
            },
        )
        self._done(u.stage("placed"))

    def _place_notes(self) -> str:
        if (self.data / "notes-final.md").is_file():
            return (self.data / "notes-final.md").read_text(encoding="utf-8")
        cap = self._json("caption.json") if (self.data / "caption.json").is_file() else {}
        lines = [f"# Attorney notes: {self._title} placed in the caption", ""]
        lines += [f"- {{{{{m['kind']}}}}} {m['text']}".rstrip() for m in self._all_markers()] or []
        lines += ["", f"## Caption: source {cap.get('source') or 'none (no court document in the file)'}", ""]
        lines += [
            f'- {x["field"].replace("_", " ")}: the court\'s paper reads "{x["document_value"]}"; the matter record '
            f'reads "{x["record_value"] or "nothing"}"'
            for x in cap.get("discrepancies") or []
        ]
        lines += ["", "## What the job did", ""]
        for u, _t in self._theirs():
            lines += [f"- {u.label}: {n}" for n in self._rendered(u)["notes"]]
        md = "\n".join(lines) + "\n"
        self._atomic("notes-final.md", md)
        return md

    def _place_review(self, notes_md: str) -> dict[str, Any]:
        rec = review_mod.load(self.data)
        if rec is None:
            papers = self._papers()
            found = review_mod.code_findings(
                self.cls, papers, self.job.request_text, "", {}, pleading.spec_for(self.firm.data["format"]) is not None,
                theirs=True,
            )
            for (u, _t), p in zip(self._theirs(), papers):
                body = json.loads((u.data / "theirs.json").read_text(encoding="utf-8"))["body"]
                lost = place.missing(body, p.text)
                if lost:
                    found.append(
                        review_mod.Finding(
                            review_mod.MUST,
                            "2 his text verbatim",
                            f"{u.label}: {len(lost)} paragraph(s) of the attorney's own set are not in the placed "
                            f"document word for word; first: {lost[0]}",
                            u.label,
                            "code",
                        )
                    )
            cap = self._json("caption.json") if (self.data / "caption.json").is_file() else {}
            if not (cap.get("fields") or {}).get("case_number"):
                found.append(
                    review_mod.Finding(
                        review_mod.MUST, "3 caption", "the court's caption could not be read from the file", "caption", "code"
                    )
                )
            user = review_mod.review_user(self.job.request_text, self._context(), papers, notes_md, found, "")
            found += review_mod.run_model(
                self.doorway, self.firm.model("compose"), "(the documents are the attorney's own)", user,
                self.job.job_id[-6:], prompt=review_mod.PLACE_PROMPT,
            )
            rec = review_mod.save(self.data, found)
            self.log(f"  review: {rec['must_fix']} must-fix, {rec['should_fix']} should-fix, {rec['notes']} note(s)")
        if rec["must_fix"]:
            raise DraftingFailed(review_mod.held_reason(rec))
        self._done("review", must_fix=0, should_fix=rec["should_fix"], notes=rec["notes"])
        return rec

    def _place_finish(self) -> None:
        for u, t in self._theirs():
            self._place_unit(u, t)
        notes_md = self._place_notes()
        rec = self._place_review(notes_md)
        if self._is_done("manifest"):
            return
        out, nm = deliver.out_dir(self.data), self._names()
        notes_md = notes_md.rstrip("\n") + "\n" + "\n".join(review_mod.notes_section(rec)) + "\n"
        notes = deliver.render_plain(notes_md, out, nm["notes"], str(self.firm.get("firm", "display_name")))
        files = [("draft", Path(self._rendered(u)["path"])) for u, _t in self._theirs()] + [("attorney_notes", notes)]
        deliver.write_manifest(out, nm["folder"], files)
        self._done("manifest")
        self._done("render", markers=self._all_markers())
