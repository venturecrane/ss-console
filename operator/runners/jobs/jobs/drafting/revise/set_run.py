"""`jobs draft <job_dir>` for a revision of EVERY filed discovery response of a
request: the firm's standing rules made in each filed document, as new versions
of the same files.

WHY (2026-11-21). An attorney replied in a discovery draft's thread "apply all
new rules to the already prepared responses". The request had filed three
documents (form, special, production), later jobs had re-filed into the same
file ids, and the revise runner edited ONE filed draft, so the broker sent the
reply to a fresh draft instead. The broker now names every filed draft of the
request once, by file id, from the newest delivery proof of each
(``revise.units``); this runs today's revise stages once per unit.

The graph::

    destination -> facts (the attorney the rules are scoped by; $0)
      -> revise-rules (the rules in force, frozen; each a checklist item whose
         source is its rule id) -> revise-base (per unit, under
         ``data/units/<k>/``: the file AS IT IS NOW downloaded by id, its version
         pinned, proven to write back element for element, its responses pinned
         to their request numbers, against the served set when one is kept)
      -> revise-estimate (every unit's calls priced under REVISE_CAP_USD before
         the first) -> revise-values (per unit in scope: the model names values,
         never words) -> revise-render (per unit: code writes the firm's exact
         phrases, the edit made IN the file and proven, the format check)
      -> manifest -> file

WHICH DOCUMENTS: the set types the request names, or every one when it names
none (``set_units.in_scope``); which responses and which phrase: the model's
values, checked by code (``rules.check``). Nothing staff wrote changes except
where a rule's value lands; a rule that would contradict a response is a
conflict, named in the job's notes (``data/revise-set-notes.md``), never applied.

ALL OR NOTHING BEFORE FILING: every unit is edited, proven and format-checked
before the first upload; one failure fails the job with nothing filed. A file
staff saved again since its base was read is read again, the same values made
on the new version and proven again, never written over (:meth:`_rebase`).
Once uploads start, ``versions.json`` records each unit done, and a resume
files the rest.

It never pulls, transcribes or digests the matter file.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from operator_connector_sdk.library import readers

from ... import firm_wording, proof_mode
from ...demand import deliver
from ...matter import pull as matter_pull
from .. import review as review_mod
from ..outcome import DraftingFailed, DraftingHold, Verdict
from . import checklist as ck, responses as rs, rules as rules_mod, set_units as su, versions
from .run import ReviseRun

STAGES = (
    "destination",
    "facts",
    "revise-rules",
    "revise-base",
    "revise-estimate",
    "revise-values",
    "revise-render",
    "manifest",
)
NOTES = "revise-set-notes.md"
#: How many times a file staff keeps saving is read again before the job holds.
REBASES = 2


class ReviseSetRun(ReviseRun):
    def __init__(self, job_dir: Path, **kw: Any) -> None:
        super().__init__(job_dir, **kw)
        if "units" not in self.revise:
            raise DraftingFailed("unexpected: ReviseSetRun on a revise block without units")
        self.units: list[dict[str, Any]] = self.revise["units"]

    # ---- plumbing ---------------------------------------------------------------
    def _udir(self, k: int) -> Path:
        d = self.data / "units" / f"{k:02d}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _ujson(self, k: int, name: str) -> Any:
        p = self._udir(k) / name
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None

    def _uwrite(self, k: int, name: str, obj: Any) -> None:
        tmp = self._udir(k) / f".{name}.tmp"
        tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
        tmp.replace(self._udir(k) / name)

    def _rules_in_force(self) -> list[dict[str, Any]]:
        return list(self._json("rules-in-force.json")["rules"])

    def _scoped(self) -> list[int]:
        return [k for k in range(len(self.units)) if (self._ujson(k, "base.json") or {}).get("in_scope")]

    def _doc(self, k: int) -> rs.Document:
        return rs.read((self._udir(k) / "base.md").read_text(encoding="utf-8"))

    def _version(self, file_id: str) -> str:
        return str((self.seat.file_meta(self.job.file_to_id, file_id) or {}).get("versionId") or "")

    # ---- stages -----------------------------------------------------------------
    def _rules(self) -> None:
        """The rules in force for discovery responses, frozen; each one a
        checklist item sourced by its rule id. None the firm wrote words for: held."""
        firm_wording.freeze_or_hold(
            self.data, self.cls, self._json("facts.json"), DraftingHold, readers.reader_for(self.cls)
        )
        p = self.data / firm_wording.FILE
        frozen = list(json.loads(p.read_text(encoding="utf-8")).get("rules") or []) if p.is_file() else []
        can, cannot = rules_mod.writable(frozen)
        self._write("rules-in-force.json", {"rules": can, "without_wording": [r["rule_id"] for r in cannot]})
        self._write("checklist.json", {"items": [i.to_dict() for i in ck.rule_items(can)]})
        if not can:
            raise DraftingHold(
                "revise_unchanged: no new versions were filed, because no standing rule of the firm's for "
                "discovery responses gives the exact wording to use"
                + (f" ({len(cannot)} rule(s) state what to do without the words)" if cannot else "")
            )

    def _base_unit(self, k: int) -> None:
        u = self.units[k]
        d = self._udir(k)
        for _ in range(2):  # the version read on both sides of the download is the one pinned
            before = self._version(u["file_id"])
            url = matter_pull.mint_one(self.seat, self.job.file_to_id, u["file_id"]).get("url")
            if not url:
                raise DraftingFailed(f"stage_unfinished: revise-base: no download could be minted for {u['name']}")
            matter_pull.fetch_one(self.seat, url, d / "filed.docx", None)
            if before and before == self._version(u["file_id"]):
                break
        else:
            raise DraftingFailed(f"stage_unfinished: revise-base: {u['name']} kept changing while it was read")
        try:
            md, doc = su.read_base(d / "filed.docx", d)
        except su.BaseUnproven as exc:
            raise DraftingFailed(f"revise_base_unproven: {u['name']}: {exc}; nothing was edited or filed") from None
        if u["set_type"] and doc.set_type != u["set_type"]:
            raise DraftingFailed(
                f"revise_base_unproven: {u['name']} is headed as {doc.set_type or 'mixed'} responses, "
                f"delivered as {u['set_type']}; nothing was edited or filed"
            )
        served = su.served_numbers(su.prior_records(self.data), u["name"], doc.set_type)
        off = rs.numbered_as_served(doc, served)
        if off:
            raise DraftingFailed(f"revise_base_unproven: {u['name']}: {off[0]}; nothing was edited or filed")
        (d / "base.md").write_text(md, encoding="utf-8")
        sha = hashlib.sha256((d / "filed.docx").read_bytes()).hexdigest()
        prev = self._ujson(k, "base.json") or {}
        self._uwrite(
            k,
            "base.json",
            {
                "file_id": u["file_id"],
                "name": u["name"],
                "version": before,
                "sha256": sha,
                "set_type": doc.set_type,
                "numbers": doc.numbers,
                "served_pinned": served is not None,
                "newer_than_delivery": sha != u["sha256"],
                "in_scope": prev.get("in_scope", True),
                "rebased": int(prev.get("rebased") or 0),
            },
        )

    def _base(self) -> None:
        for k in range(len(self.units)):
            self._base_unit(k)
        kinds = [self._ujson(k, "base.json")["set_type"] for k in range(len(self.units))]
        flags = su.in_scope(self.job.request_text, kinds)
        for k, on in enumerate(flags):
            self._uwrite(k, "base.json", {**self._ujson(k, "base.json"), "in_scope": on})
        if not any(flags):
            raise DraftingHold(
                "revise_unchanged: no new versions were filed, because the request names no kind of response "
                "this request filed"
            )
        self.log(f"  base: {len(self.units)} filed document(s), {sum(flags)} in scope")

    def _estimate_set(self) -> None:
        rules = self._rules_in_force()
        chars = sum(rules_mod.plan_chars(rules, self._doc(k)) for k in self._scoped())
        self._project(chars, "the set revision", calls=2 * len(self._scoped()), documents=len(self._scoped()))

    def _values(self) -> None:
        rules = self._rules_in_force()
        ask = self._ask(self.firm.model("audit"))
        for k in self._scoped():
            if (self._udir(k) / "values.json").is_file():
                continue  # a resume: this document's values are already read
            try:
                values, conflicts = rules_mod.ask_values(rules, self._doc(k), ask)
            except rules_mod.ValuesRefused as exc:
                raise DraftingFailed(
                    f"revise_unchecked: the rule values for {self.units[k]['name']} were refused by code after "
                    f"its retry: {exc}"[:480]
                ) from None
            tmp = self._udir(k) / ".values.json.tmp"
            tmp.write_text(rules_mod.to_json(values, conflicts), encoding="utf-8")
            tmp.replace(self._udir(k) / "values.json")

    def _render_unit(self, k: int) -> dict[str, Any]:
        d = self._udir(k)
        values, _conflicts = rules_mod.from_json((d / "values.json").read_text(encoding="utf-8"))
        try:
            rec = su.revise_one(
                (d / "base.md").read_text(encoding="utf-8"),
                d / "filed.docx",
                values,
                d / "out" / self.units[k]["name"],
                self.cls,
                self._fmt(),
            )
        except su.Refused as exc:
            raise DraftingFailed(f"{exc.code}: {self.units[k]['name']}: {exc.why}; nothing was filed") from None
        if rec["out"]:
            blob = Path(rec["out"]).read_bytes()
            rec.update(sha256=hashlib.sha256(blob).hexdigest(), bytes=len(blob))
        self._uwrite(k, "render.json", rec)
        return rec

    def _render_set(self) -> None:
        recs = {k: self._render_unit(k) for k in self._scoped()}
        self._write_notes()
        if not any(r["out"] for r in recs.values()):
            why = [s for r in recs.values() for s in r["skipped"]] + [
                f"{c.request} ({c.rule}): would contradict the response as written" for c in self._conflicts()
            ]
            raise DraftingHold(
                "revise_unchanged: no new versions were filed, because no filed response would change: "
                + ("; ".join(why) or "no rule applies to any response")[:400]
            )

    def _conflicts(self) -> list[rules_mod.Conflict]:
        out: list[rules_mod.Conflict] = []
        for k in self._scoped():
            p = self._udir(k) / "values.json"
            if p.is_file():
                out += rules_mod.from_json(p.read_text(encoding="utf-8"))[1]
        return out

    def _write_notes(self) -> None:
        """The job's notes: per document what changed, what was left and why."""
        lines = ["# The firm's standing rules, made in the filed responses", ""]
        left = self._json("rules-in-force.json").get("without_wording") or []
        if left:
            lines += [f"Not applied (the rule gives no exact words to write): {', '.join(left)}.", ""]
        for k, u in enumerate(self.units):
            base, rec = self._ujson(k, "base.json") or {}, self._ujson(k, "render.json") or {}
            lines.append(f"## {u['name']}")
            if not base.get("in_scope"):
                lines += ["Not in this request's scope; left as filed.", ""]
                continue
            lines.append(f"Responses changed: {', '.join(rec.get('changed') or []) or 'none'}.")
            if base.get("newer_than_delivery"):
                lines.append("Revised on the version as the firm last saved it, not as delivered.")
            p = self._udir(k) / "values.json"
            for c in rules_mod.from_json(p.read_text(encoding="utf-8"))[1] if p.is_file() else []:
                lines.append(f"- Response {c.request}: rule {c.rule} not applied; it would contradict it ({c.why}).")
            lines += [f"- {s}" for s in rec.get("skipped") or []] + [""]
        self._atomic(NOTES, "\n".join(lines).rstrip() + "\n")

    def _changed_units(self) -> list[int]:
        return [k for k in self._scoped() if (self._ujson(k, "render.json") or {}).get("out")]

    def _manifest_set(self) -> None:
        files = [("draft", Path(self._ujson(k, "render.json")["out"])) for k in self._changed_units()]
        deliver.write_manifest(deliver.out_dir(self.data), "", files)

    # ---- filing ----------------------------------------------------------------
    def _rebase(self, k: int, sent: dict[str, Any]) -> None:
        """A unit staff saved since its base was read, before its upload: read
        again, the same values made on the new version and proven again."""
        for _ in range(REBASES):
            base = self._ujson(k, "base.json")
            if sent.get(f"unit-{k}", {}).get("sent") or self._version(self.units[k]["file_id"]) == base["version"]:
                return
            self.log(f"  {self.units[k]['name']} was saved again since it was read: reading it again")
            self._base_unit(k)
            self._uwrite(k, "base.json", {**self._ujson(k, "base.json"), "rebased": int(base.get("rebased") or 0) + 1})
            self._render_unit(k)
        if self._version(self.units[k]["file_id"]) != self._ujson(k, "base.json")["version"]:
            raise DraftingHold(
                f"filing_refused: {self.units[k]['name']} kept changing while the revision was made; nothing "
                "more was written"
            )

    def _plan(self) -> list[dict[str, Any]]:
        p = self.data / "versions.json"
        sent = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        for k in self._changed_units():
            self._rebase(k, sent)  # every unit before the first upload: all or nothing
        self._write_notes()
        plan = []
        for k in self._changed_units():
            base, rec, u = self._ujson(k, "base.json"), self._ujson(k, "render.json"), self.units[k]
            plan.append(
                {
                    "key": f"unit-{k}",
                    "role": "draft",
                    "file_id": u["file_id"],
                    "folder_id": u["folder_id"],
                    "name": u["name"],
                    "local_path": rec["out"],
                    "sha256": rec["sha256"],
                    "bytes": rec["bytes"],
                    "base_sha256": base["sha256"],
                    "base_version": base["version"],
                }
            )
        return plan

    def _file(self) -> Verdict:
        plan = self._plan()
        files = [{"name": p["name"], "size": p["bytes"], "role": "draft"} for p in plan]
        if proof_mode.active():
            return Verdict(proof_mode.OUTCOME, stage="file", files=files)
        proven: list[dict[str, Any]] = []
        for folder in dict.fromkeys(p["folder_id"] for p in plan):
            group = [p for p in plan if p["folder_id"] == folder]
            proven += versions.file_versions(
                self.seat, self.job.file_to_id, group, self.data, self.log, folder_id=folder
            )
        by_id = {p["file_id"]: p for p in proven}
        proven = [by_id[p["file_id"]] for p in plan]
        proof = {
            "readback": "confirmed",
            "folder_id": self.revise["folder_id"],
            "files": proven,
            "review": "passed",
            "review_model": review_mod.model_for(self.firm),
            "mode": "revise",
            "revise_of": self.revise["prior_job_id"],
            "documents": len(self.units),
        }
        return Verdict("delivered", stage="file", folder_id=self.revise["folder_id"], files=files, proof=proof)

    # ---- the walk ---------------------------------------------------------------
    def _walk(self) -> Verdict:
        if self._is_done("manifest"):
            return self._file()
        self._stage("destination", self._destination)
        self._stage("facts", self._facts)
        self._close_request()
        self._stage("revise-rules", self._rules)
        self._stage("revise-base", self._base)
        self._stage("revise-values", self._values)
        self._stage("revise-estimate", self._estimate_set)
        self._stage("revise-render", self._render_set)
        self._stage("manifest", self._manifest_set)
        return self._file()

    def _current(self) -> str:
        st = self._state()
        return next((s for s in STAGES if st.get(s, {}).get("status") != "done"), "file")


__all__ = ["NOTES", "STAGES", "ReviseSetRun"]
