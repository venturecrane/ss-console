"""`jobs draft <job_dir>` for a job whose envelope carries ``revise``: the
attorney's changes to a brief the Operator already filed, as new versions of
the same files.

The graph::

    destination -> revise-base (the filed files by id; the markdown that prints
      them, proven) -> revise-estimate ($0: the projection under the cap)
      -> revise-checklist (and the estimate again, from its section plan)
      -> revise-edit (code edits, section edits, code checks, the narrow
      reading, one re-edit) -> revise-render (the firm's renderer with the
      FILED brief's layout carried onto it: its paper, first page, signature
      and charts, the attorney's formatting asks over them; the layout
      compared back to the filed file; the format check; formatting checked
      in the Word XML) -> revise-notes -> manifest -> file (new versions in
      place, the brief first and its notes only after it, compare-and-stop,
      read back)

It never pulls, transcribes or digests the matter: the brief is the input, and
the attorney's words are the instruction. Spend is capped at ``REVISE_CAP_USD``
or the firm's per-job cap, whichever is lower, and priced before any call.
"""

from __future__ import annotations

import copy
import dataclasses
import json
from pathlib import Path
from typing import Any

from ... import proof_mode
from ...demand import deliver
from .. import format_check, pleading, render
from .. import review as review_mod
from ..outcome import DraftingFailed, Verdict, markers
from ..run import DraftingRun
from . import checklist as ck, checks, core, layout as lay, prior as prior_mod, sections as sec, versions

#: The ceiling on one revision's model spend, whatever the firm's per-job cap.
REVISE_CAP_USD = 10.0
#: Before the checklist: its two reads of the request and the brief. After it,
#: the projection counts the section plan itself (``core.plan_reads``).
CHECKLIST_READS = 2
FIXED_USD = 0.5
STAGES = (
    "destination",
    "revise-base",
    "revise-estimate",
    "revise-checklist",
    "revise-edit",
    "revise-render",
    "revise-notes",
    "manifest",
)


class ReviseRun(DraftingRun):
    def __init__(self, job_dir: Path, **kw: Any) -> None:
        super().__init__(job_dir, **kw)
        if not self.job.revise:
            raise DraftingFailed("unexpected: ReviseRun on a job without a revise block")
        self.revise: dict[str, Any] = self.job.revise
        cap = min(float(self.limits.cap_usd), REVISE_CAP_USD)
        self.limits = dataclasses.replace(self.limits, cap_usd=cap)
        self.doorway.before_request = lambda stage: self.limits.check_each_call(self.budget.refresh(), stage)
        self._filed_cache: dict[str, Any] | None = None

    # ---- plumbing ---------------------------------------------------------------
    def _ask(self, model: str):
        def ask(stage: str, system: str, user: str, max_tokens: int) -> str:
            r = self.doorway.call(
                stage, model=model, system=system, messages=[{"role": "user", "content": user}], max_tokens=max_tokens
            )
            return r.text

        return ask

    def _file_entry(self, role: str) -> dict[str, Any] | None:
        return next((f for f in self.revise["files"] if f["role"] == role), None)

    @property
    def _base_dir(self) -> Path:
        d = self.data / "base"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _fmt(self, layout: dict[str, Any] | None = None) -> dict[str, Any]:
        fmt = copy.deepcopy(self.firm.data["format"])
        if layout:
            fmt.setdefault("layout", {}).setdefault(self.cls, {}).update(layout)
        return fmt

    def _render_to(self, md: str, out: Path, layout: dict[str, Any] | None = None) -> Path:
        return render.render(md, self.cls, out, self._fmt(layout))[0]

    @property
    def _filed_path(self) -> Path:
        return self._base_dir / "draft.docx"

    def _filed_layout(self) -> dict[str, Any]:
        if self._filed_cache is None:
            self._filed_cache = lay.read(self._filed_path)
        return self._filed_cache

    def _laid(self, md: str, out: Path, layout: dict[str, Any] | None = None) -> tuple[Path, dict[str, Any], str]:
        """``md`` rendered as the filed brief is laid: its first page and
        signature (inferred from the filed file), its paper, its charts'
        borders and fills, with ``layout`` (the attorney's asks) over them.
        Returns (path, the format the file was rendered with, its paper)."""
        filed = self._filed_layout()
        import docx

        natural = out.with_name(f".natural.{out.name}")
        render.render(md, self.cls, natural, lay.without_front(self._fmt(), self.cls))

        opts = lay.options(filed, lay.front_options(docx.Document(str(natural))), dict(layout or {}))
        natural.unlink()
        fmt = self._fmt(opts)
        path = render.render(md, self.cls, out, fmt)[0]
        paper = lay.paper(filed, self.job.request_text)
        if paper == "pleading":
            if pleading.spec_for(fmt) is None:
                raise DraftingFailed(
                    "revise_layout_lost: the filed brief is on pleading paper and the firm has none authored; "
                    "nothing was edited or filed"
                )
            pleading.apply_for(path, fmt, self.cls)
        lay.carry_tables(path, self._filed_path, dict(layout or {}))
        return path, fmt, paper

    # ---- stages -----------------------------------------------------------------
    def _download(self, role: str) -> Path | None:
        f = self._file_entry(role)
        if f is None:
            return None
        minted = self.seat.mint(self.job.file_to_id, [f["file_id"]])
        url = (minted[0] if minted else {}).get("url")
        if not url:
            raise DraftingFailed(f"stage_unfinished: revise-base: no download could be minted for the filed {role}")
        dest = self._base_dir / f"{role}.docx"
        self.seat.fetch(url, dest, None)
        return dest

    def _base(self) -> None:
        draft = self._download("draft")
        assert draft is not None
        notes = self._download("attorney_notes")
        candidates: list[tuple[str, str]] = []
        prior_final = self.job.job_dir.parent / self.revise["prior_job_id"] / "data" / "draft-final.md"
        if prior_final.is_file():
            doc = render.split_notes(prior_final.read_text(encoding="utf-8"))[0]
            candidates.append(("prior_job", render.reserve_judgment(doc, self.cls)[0]))
        rec = prior_mod.resolve(draft, candidates, lambda md, out: self._laid(md, out)[0], self._base_dir)
        filed = {"draft": prior_mod.sha(draft)}
        if notes is not None:
            filed["attorney_notes"] = prior_mod.sha(notes)
            (self._base_dir / "notes.md").write_text(prior_mod.docx_to_md(notes), encoding="utf-8")
        rec["filed"] = filed
        # The version each file was at when the revision read it: the filing's
        # compare-and-stop (versions.py) refuses a file that moved since.
        rec["versions"] = {
            role: str(
                (self.seat.file_meta(self.job.file_to_id, (self._file_entry(role) or {})["file_id"]) or {}).get(
                    "versionId"
                )
                or ""
            )
            for role in filed
        }
        rec["newer_than_delivery"] = filed["draft"] != (self._file_entry("draft") or {}).get("sha256")
        tmp = self._base_dir / ".base.json.tmp"
        tmp.write_text(json.dumps(rec, indent=1), encoding="utf-8")
        tmp.replace(self._base_dir / "base.json")
        self.log(
            f"  base: {rec['source']}; the filed brief is {'newer than' if rec['newer_than_delivery'] else 'the'} delivery"
        )

    def _base_md(self) -> str:
        return (self._base_dir / "base.md").read_text(encoding="utf-8")

    def _project(self, chars: int, stage: str, **rec: Any) -> None:
        rate = float(self.firm.data["budget"]["usd_per_million_chars"])
        projected = FIXED_USD + chars / 1_000_000 * rate
        self.limits.check_before_paid(projected_usd=projected, spent_usd=self.budget.refresh(), stage=stage)
        self._write("revise-estimate.json", {**rec, "characters": chars, "projected_usd": round(projected, 4)})

    def _estimate_revise(self) -> None:
        chars = CHECKLIST_READS * (len(self._base_md()) + len(self.job.request_text))
        self._project(chars, "the revision's checklist", calls=CHECKLIST_READS)

    def _checklist(self) -> None:
        prior = sec.split(self._base_md())
        try:
            items = core.make_checklist(self.job.request_text, prior, self._ask(self.firm.model("audit")))
        except ck.ChecklistError as exc:
            raise DraftingFailed(
                f"revise_unchecked: the request could not be turned into a checklist code accepts: {exc}"[:480]
            ) from None
        self._write("checklist.json", {"items": [i.to_dict() for i in items]})
        # The section plan is known now: price every call it makes before any.
        plan = core.plan_reads(self._base_md(), self.job.request_text, items)
        self._project(plan["characters"], "the revision", calls=plan["calls"], sections=plan["sections"])

    def _items(self) -> list[ck.Item]:
        return [ck.Item(**i) for i in self._json("checklist.json")["items"]]

    def _edit(self) -> None:
        out = core.revise(
            self._base_md(),
            self.job.request_text,
            checklist_ask=self._ask(self.firm.model("audit")),
            edit_ask=self._ask(self.firm.model("repair")),
            read_ask=self._ask(review_mod.model_for(self.firm)),
            items=self._items(),
        )
        self._write("revision.json", out.to_dict())
        blocking = out.blocking()
        if blocking:
            raise DraftingFailed(
                f"revise_unchecked: {len(blocking)} check(s) refused the revision after {out.rounds} round(s); "
                "see revision.json; nothing was filed"
            )
        self._atomic("revised.md", out.md)

    def _layout(self) -> dict[str, Any]:
        layout: dict[str, Any] = {}
        for i in self._items():
            if i.kind == "formatting":
                layout.update(i.params["layout"])
        return layout

    def _layout_lost(self, path: Path, layout: dict[str, Any]) -> list[str]:
        """What of the filed layout the rendered revision does not carry, past
        what the request asks to change. Headings are the edit's (the checks
        keep every filed heading line); the count only never drops."""
        filed, ours = self._filed_layout(), lay.read(path)
        ignore = {"headings", "roles"} | lay.changed_by(layout, self.job.request_text)
        if checks._about_tables(self._items()) or len(filed["tables"]) != len(ours["tables"]):
            ignore.add("tables")  # an item about a table may reshape it; the look is still carried
        lost = lay.compare(filed, ours, ignore)
        if len(ours["headings"]) < len(filed["headings"]):
            lost.append(f"headings: {len(filed['headings'])} filed, {len(ours['headings'])} in the revision")
        return lost

    def _render_revise(self) -> None:
        revised = (self.data / "revised.md").read_text(encoding="utf-8")
        md = core.reserve(revised, self._base_md(), self.cls)
        name = (self._file_entry("draft") or {})["name"]
        layout = self._layout()
        path, fmt, paper = self._laid(md, deliver.out_dir(self.data) / name, layout)
        lost = self._layout_lost(path, layout)
        if lost:
            raise DraftingFailed(
                "revise_layout_lost: the revision does not carry the filed brief's " + "; ".join(lost)[:400]
            )
        # The filed brief is the attorney's document as he has it: what the
        # format check says of THE FILED FILE ITSELF is not this revision's to
        # fix. Only a failure the revision brings in stops the filing.
        filed_fmt = self._fmt(lay.options(self._filed_layout(), {}, {}))
        filed_paper = str(self._filed_layout()["paper"])
        before = format_check.check(self._filed_path, self.cls, filed_fmt, paper=filed_paper)
        res = format_check.check(path, self.cls, fmt, paper=paper)
        new = [f for f in res.fails if f not in before.fails]
        self._write("revise-format.json", {"filed_brief": before.fails, "revision": res.fails, "new": new})
        if new:
            raise DraftingFailed("format_check: the revision brings in: " + "; ".join(new)[:400])
        rev = self._json("revision.json")
        skip = len(sec.tables(sec.split(md)[0].body))
        size = float(self._fmt().get("size_pt") or 12)
        for i, st in zip(self._items(), rev["statuses"]):
            if i.kind != "formatting":
                continue
            probs = checks.check_docx(path, i.params["layout"], size, skip, self.cls)
            st.update(
                status="not_met" if probs else "met", how="code", detail="; ".join(probs) or "checked in the Word file"
            )
        self._write("revision.json", rev)
        bad = [s for s in rev["statuses"] if s["status"] == "not_met" and s["how"] == "code"]
        if bad:
            raise DraftingFailed(
                f"revise_unchecked: {len(bad)} formatting check(s) refused the rendered brief; see revision.json"
            )
        self._done("render", markers=markers(md))

    def _revise_notes(self) -> None:
        from . import notes as notes_mod

        md = notes_mod.write(
            title=self._title,
            job_tail=self.job.job_id[-6:],
            requester=self.job.requester,
            items=self._items(),
            revision=self._json("revision.json"),
            markers=list(self._state().get("render", {}).get("markers") or []),
            prior_notes=(self._base_dir / "notes.md").read_text(encoding="utf-8")
            if (self._base_dir / "notes.md").is_file()
            else "",
        )
        self._atomic("notes-final.md", md)

    def _manifest(self) -> None:
        out = deliver.out_dir(self.data)
        files = [("draft", out / (self._file_entry("draft") or {})["name"])]
        notes_entry = self._file_entry("attorney_notes")
        if notes_entry is not None:
            md = (self.data / "notes-final.md").read_text(encoding="utf-8")
            notes = deliver.render_plain(md, out, notes_entry["name"], str(self.firm.get("firm", "display_name")))
            files.append(("attorney_notes", notes))
        deliver.write_manifest(out, "", files)

    def _file(self) -> Verdict:
        manifest = json.loads((deliver.out_dir(self.data) / "upload_manifest.json").read_text(encoding="utf-8"))
        files = [{"name": m["name"], "size": m["bytes"], "role": m.get("role")} for m in manifest]
        if proof_mode.active():
            return Verdict(proof_mode.OUTCOME, stage="file", files=files)
        base = self._json("base/base.json")
        plan = []
        for m in manifest:
            entry = self._file_entry(m["role"]) or {}
            plan.append(
                {
                    **m,
                    "file_id": entry["file_id"],
                    "base_sha256": base["filed"][m["role"]],
                    "base_version": base["versions"][m["role"]],
                }
            )
        proven = versions.file_versions(
            self.seat, self.job.file_to_id, plan, self.data, self.log, folder_id=self.revise["folder_id"]
        )
        model = review_mod.model_for(self.firm)
        proof = {
            "readback": "confirmed",
            "folder_id": self.revise["folder_id"],
            "files": proven,
            "review": "passed",
            "review_model": model,
            "mode": "revise",
            "revise_of": self.revise["prior_job_id"],
        }
        return Verdict("delivered", stage="file", folder_id=self.revise["folder_id"], files=files, proof=proof)

    # ---- the walk ---------------------------------------------------------------
    def _walk(self) -> Verdict:
        if self._is_done("manifest"):
            return self._file()
        self._stage("destination", self._destination)
        self._stage("revise-base", self._base)
        self._stage("revise-estimate", self._estimate_revise)
        self._stage("revise-checklist", self._checklist)
        self._stage("revise-edit", self._edit)
        self._stage("revise-render", self._render_revise)
        self._stage("revise-notes", self._revise_notes)
        self._stage("manifest", self._manifest)
        return self._file()

    def _current(self) -> str:
        st = self._state()
        return next((s for s in STAGES if st.get(s, {}).get("status") != "done"), "file")
