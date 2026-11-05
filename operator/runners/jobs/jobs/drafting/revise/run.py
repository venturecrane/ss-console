"""`jobs draft <job_dir>` for a job whose envelope carries ``revise``: the
attorney's changes to a brief the Operator already filed, as new versions of
the same files.

The graph::

    destination -> revise-base (the filed files by id; the filed brief read
      as markdown, proven to map element for element onto the file)
      -> revise-estimate ($0: the projection under the cap) -> revise-checklist
      (and the estimate again, from its section plan) -> revise-edit (code
      edits, section edits, code checks, the narrow reading, one re-edit)
      -> revise-render (the edits made IN the filed .docx, ``inplace.py``:
      content in the paragraphs it names, layout asks on the elements they
      name; the XML proven unchanged everywhere else; the format check;
      formatting checked in the Word XML) -> revise-notes -> manifest -> file
      (new versions in place, the brief first and its notes only after it,
      compare-and-stop, read back)

It never re-renders the brief: what the attorney accepted and no ask names is
his file's own XML afterwards.

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
from .. import format_check
from .. import review as review_mod
from ..outcome import DraftingFailed, DraftingHold, Verdict, markers
from ..run import DraftingRun
from . import checklist as ck, checks, core, filed as filed_mod, inplace, layout as lay, prior as prior_mod
from . import sections as sec, versions

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
        self._verdicts: dict[str, tuple[str, str, list[str]]] | None = None

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

    @property
    def _filed_path(self) -> Path:
        return self._base_dir / "draft.docx"

    def _filed_layout(self) -> dict[str, Any]:
        if self._filed_cache is None:
            self._filed_cache = lay.read(self._filed_path)
        return self._filed_cache

    def _edit_in_place(self, md: str, layout: dict[str, Any], out: Path) -> tuple[inplace.Edit, list[str]]:
        """``md`` and ``layout`` made in the filed file, saved to ``out``:
        (the edit, where the result differs from the filed XML beyond it)."""
        ed = inplace.open_filed(self._filed_path)
        before = inplace.snapshot(ed)
        from ..house import rules_for

        inplace.edit_content(ed, md, rules_for(self._fmt(), self.cls))
        inplace.edit_layout(ed, layout)
        blob = inplace.save(ed, out)
        return ed, inplace.prove(ed, before, self._filed_path.read_bytes(), blob, md)

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
        # The base is the filed file read as markdown, and only that: the
        # revision is made IN this file, block for element, so the proof is
        # that reading it and writing it back unchanged touches nothing.
        md = prior_mod.docx_to_md(draft)
        ed, diffs = self._edit_in_place(md, {}, self._base_dir / "base-roundtrip.docx")
        if diffs or ed.touched or ed.removed:
            raise DraftingFailed(
                "revise_base_unproven: the filed brief does not read back element for element ("
                + "; ".join(diffs or [f"{len(ed.touched)} element(s) moved on a round trip"])[:360]
                + "); nothing was edited or filed"
            )
        (self._base_dir / "base.md").write_text(md, encoding="utf-8")
        rec: dict[str, Any] = {"source": "filed", "filed_sha256": prior_mod.sha(draft), "tried": []}
        filed = {"draft": prior_mod.sha(draft)}
        if notes is not None:
            filed["attorney_notes"] = prior_mod.sha(notes)
            (self._base_dir / "notes.md").write_text(prior_mod.notes_md(notes), encoding="utf-8")
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
        if all(i.kind == "formatting" for i in items):
            self._hold_if_unchanged(text_changed=False)  # $0: no edit can change the words
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
        """The formatting asks as layout keys, less those the filed brief
        already meets and a relative ask's that would not go beyond it."""
        layout: dict[str, Any] = {}
        verdicts = self._formatting_verdicts()
        for i in self._items():
            if i.kind == "formatting":
                drop = verdicts[i.id][2]
                layout.update({k: v for k, v in i.params["layout"].items() if k not in drop})
        return layout

    def _formatting_verdicts(self) -> dict[str, tuple[str, str, list[str]]]:
        """Per formatting item, against the FILED file: ("already" | "short" |
        "change", why, the keys not to lay: a relative key that does not go
        beyond the filed value, or a key the filed brief already meets, whose
        elements stay exactly as filed)."""
        if self._verdicts is not None:
            return self._verdicts
        out: dict[str, tuple[str, str, list[str]]] = {}
        size = float(self._fmt().get("size_pt") or 12)
        skip = len(sec.tables(sec.split(self._base_md())[0].body))
        for i in self._items():
            if i.kind != "formatting":
                continue
            asked: dict[str, Any] = i.params["layout"]
            short, have = self._short_keys(asked, i.params.get("relative"))
            rest = {k: v for k, v in asked.items() if k not in short}
            met = [k for k, v in rest.items() if not checks.check_docx(self._filed_path, {k: v}, size, skip, self.cls)]
            drop = [*short, *met]
            if len(drop) < len(asked):
                out[i.id] = ("change", "", drop)
            elif short:
                way = i.params["relative"]
                said = ", ".join(f"{_label(k)} {have[k]:g}" for k in short)
                why = f'"{i.quote}": the filed brief already has {said}; this would not make it {way}'
                out[i.id] = ("short", why, drop)
            else:
                out[i.id] = ("already", f'"{i.quote}" is already how the filed brief reads', drop)
        self._verdicts = out
        return out

    def _short_keys(self, asked: dict[str, Any], way: Any) -> tuple[list[str], dict[str, float]]:
        """The keys of a relative ask whose value does not go beyond the filed one."""
        if way not in ck.RELATIVE:
            return [], {}
        drop, have = [], {}
        for k, v in asked.items():
            cur = filed_mod.value(self._filed_path, k)
            if cur is None or isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            if not (float(v) > cur if way == "more" else float(v) < cur):
                drop.append(k)
                have[k] = cur
        return drop, have

    def _hold_if_unchanged(self, text_changed: bool, diff_empty: bool = False) -> None:
        """A revision that changes nothing is never filed as a new version:
        held, and the attorney is told why, item by item. ``diff_empty``: the
        edit made in the file touched no element at all."""
        verdicts = self._formatting_verdicts()
        if not diff_empty and (text_changed or any(v[0] == "change" for v in verdicts.values())):
            return
        rev = self._json("revision.json") if (self.data / "revision.json").is_file() else {"statuses": []}
        status = {s["id"]: s for s in rev["statuses"]}
        whys = []
        for i in self._items():
            if i.kind == "formatting":
                whys.append(verdicts[i.id][1])
            elif (status.get(i.id) or {}).get("status") == "met":
                whys.append(f'"{i.quote}" is already how the filed brief reads')
            else:
                whys.append(f'"{i.quote}" could not be made from what the brief already says')
        raise DraftingHold(
            "revise_unchanged: no new version was filed, because nothing in the brief would change: "
            + "; ".join(whys)[:420]
        )

    def _render_revise(self) -> None:
        revised = (self.data / "revised.md").read_text(encoding="utf-8")
        self._hold_if_unchanged(text_changed=revised != self._base_md())
        md = core.reserve(revised, self._base_md(), self.cls)
        name = (self._file_entry("draft") or {})["name"]
        layout = self._layout()
        path = deliver.out_dir(self.data) / name
        ed, lost = self._edit_in_place(md, layout, path)
        if not ed.touched and not ed.removed:
            self._hold_if_unchanged(text_changed=False, diff_empty=True)
        if lost:
            raise DraftingFailed("revise_layout_lost: the revision changes what no ask names: " + "; ".join(lost)[:400])
        # The filed brief is the attorney's document as he has it: what the
        # format check says of THE FILED FILE ITSELF is not this revision's to
        # fix. Only a failure the revision brings in stops the filing.
        paper = str(self._filed_layout()["paper"])
        before = format_check.check(self._filed_path, self.cls, self._fmt(), paper=paper)
        res = format_check.check(path, self.cls, self._fmt(layout), paper=paper)
        new = [f for f in res.fails if f not in before.fails]
        self._write("revise-format.json", {"filed_brief": before.fails, "revision": res.fails, "new": new})
        if new:
            raise DraftingFailed("format_check: the revision brings in: " + "; ".join(new)[:400])
        rev = self._json("revision.json")
        skip = len(sec.tables(sec.split(md)[0].body))
        size = float(self._fmt().get("size_pt") or 12)
        verdicts = self._formatting_verdicts()
        for i, st in zip(self._items(), rev["statuses"]):
            if i.kind != "formatting":
                continue
            kind, why, drop = verdicts[i.id]
            if kind == "short":  # not made, and the attorney is told why; never a stop
                st.update(status="not_met", how="filed", detail=why)
                continue
            if kind == "already":
                st.update(status="met", how="code", detail="already how the filed brief reads; left as filed")
                continue
            asked = {k: v for k, v in i.params["layout"].items() if k not in drop}
            probs = checks.check_docx(path, asked, size, skip, self.cls)
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


_LABELS = {
    "line_spacing": "line spacing",
    "line_spacing_pt": "line spacing of",
    "heading_line_spacing": "heading line spacing",
    "heading_line_spacing_pt": "heading line spacing of",
    "heading_space_before_pt": "space before the headings of",
    "heading_space_after_pt": "space after the headings of",
    "table_font_pt": "a chart font of",
    "table_border_pt": "chart borders of",
}


def _label(key: str) -> str:
    if key.endswith("_gap_lines"):
        return "a count of blank lines of"
    return _LABELS.get(key, key.replace("_", " "))
