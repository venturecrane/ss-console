"""`jobs draft <job_dir>` for a revision of the propounded sets a request
filed (``discovery_set`` with ``revise.units``): the attorney's define, add and
cut made in each filed set, as new versions of the same files.

The graph::

    destination -> revise-base (per unit, under ``data/units/<k>/``: the file
      AS IT IS NOW downloaded by id, its version pinned, proven to write back
      element for element, read as a set: its type, party and count)
      -> revise-estimate (every call a job can make priced under
      REVISE_CAP_USD before the first) -> revise-plan (one call: the email's
      ops in its own words, each scoped by code; anything else, or a scope or
      number code cannot settle, held for SMD before any other call)
      -> revise-edit (per set in scope: the model's definition, requests and
      ranking, each kept on disk; code makes every change, ``propounded.py``,
      IN the filed file, proven, format-checked) -> manifest -> file

The plumbing is the response revision's (``set_run.ReviseSetRun``): the
version pin, all or nothing before the first upload, the rebase of a file
staff saved meanwhile, ``versions.json`` on resume. A rebase here re-makes the
same values only when the file reads exactly as before; otherwise it holds,
never writes over the staff's words.

It never pulls, transcribes or digests the matter file. Reached only when the
broker names a propounded request's filed sets as ``units`` (a root
``revise_of`` today; the broker's switch is its own change).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..outcome import DraftingFailed, DraftingHold, Verdict
from . import book_run as br, prior, propounded as pp, propounded_ask as pa, propounded_set as ps, set_units as su
from .set_run import NOTES, REBASES, ReviseSetRun

STAGES = ("destination", "revise-base", "revise-estimate", "revise-plan", "revise-edit", "manifest")
PLAN = "propounded-plan.json"
ANSWERS = "propounded-answers.json"
RESULT = "result.json"
REVISED = "revised.md"


class PropoundedSetRun(ReviseSetRun):
    # ---- the filed sets ----------------------------------------------------------------
    def _hint(self, k: int) -> str:
        return str(self.units[k].get("set_type") or "")

    def _base_unit(self, k: int) -> None:
        u, d = self.units[k], self._udir(k)
        before = self._fetch(k)
        md = prior.docx_to_md(d / "filed.docx")
        ed, diffs = su.edit_file(d / "filed.docx", md, d / "base-roundtrip.docx")
        if diffs or ed.touched or ed.removed:
            raise DraftingFailed(
                f"revise_base_unproven: {u['name']} does not read back element for element ("
                + "; ".join(diffs or [f"{len(ed.touched)} element(s) moved"])[:300]
                + "); nothing was edited or filed"
            )
        try:
            unit = pp.read_unit(u["name"], self._hint(k), md)
        except pp.Refused as exc:
            raise DraftingFailed(f"{exc.code}: {exc.why}; nothing was edited or filed") from None
        (d / "base.md").write_text(md, encoding="utf-8")
        sha = hashlib.sha256((d / "filed.docx").read_bytes()).hexdigest()
        prev = self._ujson(k, "base.json") or {}
        self._uwrite(
            k,
            "base.json",
            {
                "file_id": u["file_id"], "name": u["name"], "version": before, "sha256": sha,
                "set_type": unit.set_type, "party": unit.party, "count": unit.count, "supported": unit.supported,
                "newer_than_delivery": sha != u["sha256"], "rebased": int(prev.get("rebased") or 0),
            },
        )  # fmt: skip

    def _sets(self) -> list[pp.Unit]:
        out = []
        for k in range(len(self.units)):
            b = self._ujson(k, "base.json")
            out.append(pp.Unit(b["name"], b["set_type"], b["party"], int(b["count"]), bool(b["supported"])))
        return out

    def _md(self, k: int) -> str:
        return (self._udir(k) / "base.md").read_text(encoding="utf-8")

    def _base(self) -> None:
        for k in range(len(self.units)):
            self._base_unit(k)
        br.notes_base(self)  # the delivery's attorney notes, when the broker names them
        sets = self._sets()
        if not any(s.supported for s in sets):
            raise DraftingHold(
                "revise_set_unsupported: the filed sets are form interrogatories, which this revision does not "
                "edit; nothing was filed"
            )
        self.log(f"  base: {len(sets)} filed set(s), {sum(s.supported for s in sets)} this revision edits")

    # ---- the estimate and the plan ----------------------------------------------------------
    def _estimate(self) -> None:
        mds = [self._md(k) for k in range(len(self.units))]
        self._project(pa.plan_chars(self.job.request_text, mds), "the set revision", documents=len(mds))

    def _plan_ops(self) -> None:
        email, sets = self.job.request_text, self._sets()
        try:
            ops, scoped = pa.settle_plan(email, sets, self._ask(self.firm.model("audit")))
        except pp.Held as exc:
            raise DraftingHold(str(exc)[:480]) from None
        except pp.Refused as exc:
            raise DraftingFailed(str(exc)[:480]) from None
        # Each term, and each add and cut on each set, priced before the first write call.
        mds = [self._md(k) for k in range(len(self.units))]
        self._project(pa.edit_chars(email, mds, ops, scoped), "the set revision's edits", ops=len(ops))
        self._write(PLAN, {"ops": [o.to_dict() for o in ops], "scope": scoped})

    # ---- the edit ----------------------------------------------------------------------------
    def _oracle(self) -> pa.ModelOracle:
        p = self.data / ANSWERS
        kept = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        return pa.ModelOracle(
            self._ask(self.firm.model("audit")), self.job.request_text, kept, lambda a: self._write(ANSWERS, a)
        )

    def _revise(self) -> dict[int, ps.Result]:
        """Every set's ops and every citation they move, made in memory by code."""
        plan = self._json(PLAN)
        ops = [pp.Op(o["op"], o["quote"], tuple(o["set_types"]), o["party_quote"], o["term"], o["number_quote"])
               for o in plan["ops"]]  # fmt: skip
        try:
            return ps.revise_all(
                self._sets(), [self._md(k) for k in range(len(self.units))], ops, plan["scope"], self._oracle()
            )
        except pp.Held as exc:
            raise DraftingHold(str(exc)[:480]) from None
        except pp.Refused as exc:
            raise DraftingFailed(f"{exc.code}: {exc.why}"[:480]) from None

    def _render_unit(self, k: int) -> dict[str, Any]:
        """Unit ``k``'s revised text (``revised.md``) made in its filed file, proven."""
        d = self._udir(k)
        rec: dict[str, Any] = {"out": None}
        if (d / REVISED).is_file():
            md = (d / REVISED).read_text(encoding="utf-8")
            out = d / "out" / self.units[k]["name"]
            ed, diffs = su.edit_file(d / "filed.docx", md, out)
            if diffs:
                raise DraftingFailed(
                    f"revise_layout_lost: {self.units[k]['name']}: the revision changes what no ask names: "
                    + "; ".join(diffs)[:300]
                )
            new = su.new_findings(d / "filed.docx", out, self.cls, self._fmt())
            if new:
                raise DraftingFailed(
                    f"format_check: {self.units[k]['name']}: the revision brings in: " + "; ".join(new)[:300]
                )
            blob = out.read_bytes()
            rec.update(out=str(out), sha256=hashlib.sha256(blob).hexdigest(), bytes=len(blob), touched=len(ed.touched))
        self._uwrite(k, "render.json", rec)
        return rec

    def _edit(self) -> None:
        results = self._revise()
        for k, res in results.items():
            self._uwrite(k, RESULT, res.to_dict())
            if res.md != self._md(k):
                (self._udir(k) / REVISED).write_text(res.md, encoding="utf-8")
        for k in results:
            self._render_unit(k)
        self._write_notes()
        if not any((self._ujson(k, "render.json") or {}).get("out") for k in results):
            left = [w for k in results for w in (self._ujson(k, RESULT) or {}).get("left") or []]
            raise DraftingHold(
                "revise_unchanged: no new versions were filed, because no filed set would change: "
                + ("; ".join(left) or "nothing the email asks changes them")[:400]
            )

    # ---- the reply and the notes ----------------------------------------------------------
    def reply_lines(self) -> list[str]:
        """What changed in each file and what was left and why, written by code."""
        scoped = {k for ks in self._json(PLAN)["scope"] for k in ks} if (self.data / PLAN).is_file() else set()
        out: list[str] = []
        for k, u in enumerate(self.units):
            base, raw = self._ujson(k, "base.json") or {}, self._ujson(k, RESULT)
            if raw is not None:
                res = ps.Result("", int(raw["before"]), int(raw["after"]), list(raw["lines"]), list(raw["left"]))
                out += ps.summary(u["name"], res, base["set_type"])
                if res.changed and base.get("newer_than_delivery"):
                    out.append("- Revised on the version as the firm last saved it, not as delivered.")
            elif base and not base.get("supported", True):
                out.append(f"- {u['name']}: left as filed; form interrogatories are not edited by this revision.")
            elif k not in scoped:
                out.append(f"- {u['name']}: left as filed; the email does not ask to change it.")
        return out

    def _write_notes(self) -> None:
        lines = ["# Your changes, made in the filed sets", "", *self.reply_lines()]
        self._atomic(NOTES, "\n".join(lines).rstrip() + "\n")

    # ---- filing --------------------------------------------------------------------------------
    def _rebase(self, k: int, sent: dict[str, Any]) -> None:
        """A set staff saved since its base was read: read again, and the same
        values made on the new version only when it reads exactly as before."""
        for _ in range(REBASES):
            base = self._ujson(k, "base.json")
            if sent.get(f"unit-{k}", {}).get("sent") or self._version(self.units[k]["file_id"]) == base["version"]:
                return
            was = self._md(k)
            self.log(f"  {self.units[k]['name']} was saved again since it was read: reading it again")
            self._base_unit(k)
            if self._md(k) != was:
                raise DraftingHold(
                    f"filing_refused: {self.units[k]['name']} was changed while the revision was made; nothing "
                    "more was written"
                )
            self._uwrite(k, "base.json", {**self._ujson(k, "base.json"), "rebased": int(base.get("rebased") or 0) + 1})
            self._render_unit(k)
        if self._version(self.units[k]["file_id"]) != self._ujson(k, "base.json")["version"]:
            raise DraftingHold(
                f"filing_refused: {self.units[k]['name']} kept changing while the revision was made; nothing "
                "more was written"
            )

    def _plan(self) -> list[dict[str, Any]]:
        plan = super()._plan()
        if plan and self.revise.get("notes") and not any(p.get("key") == "notes" for p in plan):
            notes = br.notes_plan(self, (self.data / NOTES).read_text(encoding="utf-8"))
            if notes:
                plan.append(notes)
        return plan

    def _file(self) -> Verdict:
        v = super()._file()
        if v.proof is not None:
            v.proof["changes"] = self.reply_lines()
        return v

    # ---- the walk -------------------------------------------------------------------------------
    def _walk(self) -> Verdict:
        if self._is_done("manifest"):
            return self._file()
        self._stage("destination", self._destination)
        self._close_request()
        self._stage("revise-base", self._base)
        self._stage("revise-estimate", self._estimate)
        self._stage("revise-plan", self._plan_ops)
        self._stage("revise-edit", self._edit)
        self._stage("manifest", self._manifest_set)
        return self._file()

    def _current(self) -> str:
        st = self._state()
        return next((s for s in STAGES if st.get(s, {}).get("status") != "done"), "file")


def runner_for(revise: dict[str, Any] | None, cls: str) -> type:
    """The runner class a job's revise block and class name (``__main__``)."""
    from ..run import DraftingRun
    from .run import ReviseRun

    if not revise:
        return DraftingRun
    if "units" not in revise:
        return ReviseRun
    return PropoundedSetRun if cls == "discovery_set" else ReviseSetRun


__all__ = ["ANSWERS", "PLAN", "STAGES", "PropoundedSetRun", "runner_for"]
