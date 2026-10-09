"""`medchron litigate <job_dir>`: one litigation status job, resumable.

    inventory -> diff -> fetch -> extract -> read1 -> read2 -> read3
      -> gates -> parity -> book -> file -> report

Every stage records itself in ``data/state.json``; a resume skips finished
stages, and the per-matter reads resume per matter (``data/m/<id>/readN.json``).
The run's "today" is frozen on the first attempt, so a resume judges dates the
way the first attempt did. The lane's state on the volume (``manifest.py``) is
written only by ``report``, after the workbook is read back.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import traceback
from pathlib import Path
from typing import Any, Callable

from .. import budget as budget_mod, limits as limits_mod
from ..ledger import Ledger
from ..llm import Doorway
from . import (
    book,
    deliver,
    extract as extract_mod,
    fetch as fetch_mod,
    firm as firm_mod,
    gates,
    inventory,
    job as job_mod,
)
from . import manifest, parity, passes, read, update
from .progress import Progress
from .runbase import RunBase
from .outcome import REASON, LitigationFailed, LitigationHold, Verdict
from .tools import MatterContext, ReadIncomplete, dump

STAGES = (
    "inventory",
    "diff",
    "fetch",
    "extract",
    "screen",
    "read1",
    "read2",
    "read3",
    "gates",
    "parity",
    "book",
    "file",
    "report",
)


class LitigationRun(RunBase):
    def __init__(
        self,
        job_dir: Path,
        *,
        inputs_dir: str | None = None,
        pricing: str | None = None,
        state_dir: str | Path | None = None,
        seat_factory: Callable[[], Any] | None = None,
        client: Any = None,
        log: Callable[[str], None] = print,
        today: Callable[[], dt.date] | None = None,
        readback_pause: float | None = None,
    ) -> None:
        self.job = job_mod.load(job_dir)
        self.firm = firm_mod.load(inputs_dir)
        self.data = self.job.data
        self.data.mkdir(parents=True, exist_ok=True)
        self.state = manifest.state_dir(state_dir)
        self.log = log
        cap = self.job.per_job_cap_usd or float(self.firm.data["per_job_cap_usd"])
        monthly = self.job.monthly_budget_usd or float(self.firm.data["monthly_budget_usd"])
        self.pricing = budget_mod.Pricing.load(
            Path(pricing or os.environ.get(budget_mod.PRICING_ENV) or budget_mod.PRICING_DEFAULT)
        )
        self.budget = budget_mod.Budget(self.pricing, cap, [self.data / "usage-ledger.jsonl"], 1.0)
        self.limits = limits_mod.Limits(
            cap_usd=cap,
            monthly_budget_usd=monthly,
            usd_per_scanned_page=0.0,
            usd_per_audit_claim=0.0,
            month_cents_used=self.job.month_cents_used,
        )
        self.doorway = Doorway(
            ledger=Ledger(self.data / "usage-ledger.jsonl"),
            client=client,
            log=log,
            before_request=self._before_call,
        )
        self._seat_factory, self._seat = seat_factory, None
        self.today = self._frozen_today(today)
        self.readback_pause = readback_pause

    # ---- plumbing -------------------------------------------------------------------
    @property
    def seat(self) -> Any:
        if self._seat is None:
            if self._seat_factory is None:
                from ..seat import open_seat

                self._seat = open_seat(self.job.slug)
            else:
                self._seat = self._seat_factory()
        return self._seat

    def _frozen_today(self, today: Callable[[], dt.date] | None) -> dt.date:
        stamp = self._state().get("today")
        if not stamp:
            if today is not None:
                d = today()
            else:
                from zoneinfo import ZoneInfo

                d = dt.datetime.now(ZoneInfo(str(self.firm.get("timezone")))).date()
            stamp = d.isoformat()
            self._put("today", stamp)
        return dt.date.fromisoformat(stamp)

    # ---- stages -----------------------------------------------------------------------
    def _inventory(self) -> None:
        inv = inventory.build(self.seat, self.firm, self.job, manifest.tracked(self.state), self.data, self.log)
        dump(self.data / "inventory.json", inv)

    def _matters(self) -> list[dict[str, Any]]:
        return list(self._json("inventory.json")["matters"])

    def _diff(self) -> None:
        plan = {}
        for m in self._matters():
            mid = m["id"]
            files = inventory.files_of(self.data, mid)
            p = manifest.plan_matter(
                files,
                manifest.load_prior(self.state, mid),
                manifest.load_manifest(self.state, mid),
                self.firm,
                self.today,
            )
            moved = manifest.diff(manifest.current_manifest(files), manifest.load_manifest(self.state, mid))
            plan[mid] = {
                **p,
                "moved": moved["new"] + moved["changed"],
                "newest": manifest.newest_emails(files, int(self.firm.get("email_recent_n"))),
            }
        dump(self.data / "plan.json", plan)

    def _plan(self) -> dict[str, Any]:
        return self._json("plan.json")

    def _active(self) -> list[str]:
        """Matters this run opens documents for: a read, an audit, or new mail
        the screen must read first (an update with only unscreened emails has
        no read groups yet; skipping it left the screen nothing to read)."""
        return [
            mid for mid, p in self._plan().items() if p["read_groups"] or p["audit"] == "all" or p.get("unscreened")
        ]

    def _fetch(self) -> None:
        report, retry = {}, []
        for mid in self._active():
            p = self._plan()[mid]
            r = fetch_mod.fetch_matter(
                self.seat, mid, inventory.files_of(self.data, mid), p["candidates"], self.data, self.log
            )
            report[mid] = r
            retry += r["retry"]
        fetch_mod.write_report(self.data, report)
        if retry:
            raise LitigationFailed(f"fetch_unfinished: {len(retry)} files are not fetched for a reason a retry can fix")

    def _extract(self) -> None:
        summary = {}
        rows_by = {mid: list(fetch_mod.pulled(self.data, mid).values()) for mid in self._active()}
        total = sum(1 for rows in rows_by.values() for r in rows if r.get("ok") and r.get("path"))
        self._progress = Progress(self.log, "extract", total, "docs", every=20)
        for mid, rows in rows_by.items():
            recs = extract_mod.extract_matter(
                fetch_mod.matter_dir(self.data, mid),
                rows,
                self._ocr,
                self._progress,
                concurrency=int(self.firm.get("concurrency")),
            )
            summary[mid] = {
                "ok": sum(1 for r in recs if r.get("ok")),
                "failed": [r["file_id"] for r in recs if not r.get("ok")],
            }
        extract_mod.write_summary(self.data, summary)
        self._progress.finish()

    def _ctx(self, mid: str) -> MatterContext:
        integ = {r["file_id"]: r["problem"] for r in (self._fetch_report().get(mid) or {}).get("integrity") or []}
        return MatterContext(
            mid,
            inventory.files_of(self.data, mid),
            self.data,
            int(self.firm.get("chunk_chars")),
            seat=self.seat,
            ocr=self._ocr,
            log=self.log,
            integrity=integ,
        )

    def _fetch_report(self) -> dict[str, Any]:
        p = self.data / "fetch.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}

    def _header(self, m: dict[str, Any]) -> str:
        return f"{m['number']} {m['title']}".strip()

    def _listing(self, ctx: MatterContext, mid: str) -> str:
        p = self._plan()[mid]
        return read.file_list(
            ctx,
            p["candidates"],
            p["newest"],
            int(self.firm.get("context_chars")),
            int(self.firm.get("doc_context_chars")),
        )

    def _screen(self) -> None:
        update.screen(self, lambda mid: passes.texts(self, mid))

    def _read1(self) -> None:
        passes.read1(self)

    def _read2(self) -> None:
        passes.read2(self)

    def _read3(self) -> None:
        passes.read3(self)

    # ---- assemble, gate, parity -------------------------------------------------------------
    def _integrity(self, mid: str) -> list[dict[str, Any]]:
        names = {str(f["id"]): manifest.full_name(f) for f in inventory.files_of(self.data, mid)}
        recs = [dict(r) for r in (self._fetch_report().get(mid) or {}).get("integrity") or []]
        for r in extract_mod.failures(fetch_mod.matter_dir(self.data, mid)):
            recs.append(
                {
                    "file_id": r["file_id"],
                    "name": names.get(r["file_id"], r.get("name")),
                    "problem": str(r.get("problem")).split(":")[0],
                }
            )
        for r in recs:
            r["name"] = names.get(str(r["file_id"]), r.get("name"))
        return list({r["file_id"]: r for r in recs}.values())

    @staticmethod
    def integrity_flag(rec: dict[str, Any]) -> str:
        if rec.get("problem") == fetch_mod.MISSING:
            return f"The file entry '{rec['name']}' exists in Smokeball but its content is missing; it could not be checked."
        if rec.get("problem") == fetch_mod.REFUSED:
            return f"Smokeball would not open the file '{rec['name']}' (deleted or restricted since it was listed); it could not be checked."
        return f"The file '{rec['name']}' could not be read; it could not be checked."

    def final(self, m: dict[str, Any]) -> dict[str, Any] | None:
        mid = m["id"]
        r3 = self._mjson(mid, "read3.json")
        result, log = (r3["result"], r3["overturns"]) if r3 else passes.after_read2(self, mid)
        if result is None:
            return None
        out = {
            **result,
            "matter_id": mid,
            "number": m["number"],
            "title": m["title"],
            "responsible": m.get("responsible") or "",
        }
        out["integrity"] = self._integrity(mid)
        flags = list(out.get("matter_flags") or [])
        flags += [self.integrity_flag(r) for r in out["integrity"] if self.integrity_flag(r) not in flags]
        out["matter_flags"] = flags
        out["settlement_reviewed"] = (r3 or {}).get("settlement_reviewed") or []
        out["overturns"] = log
        return out

    def _finals(self) -> list[dict[str, Any]]:
        return [f for f in (self.final(m) for m in self._matters()) if f is not None]

    def _text_of(self, mid: str, fid: str) -> str | None:
        return fetch_mod.cached_text(self.data, mid, fid)

    def _gates(self) -> None:
        ms = self._finals()
        dump(self.data / "final.json", ms)
        files, saved, hits = {}, {}, {}
        for m in self._matters():
            fs = inventory.files_of(self.data, m["id"])
            files[m["id"]] = {str(f["id"]) for f in fs}
            saved[m["id"]] = {str(f["id"]): {str(f.get(k) or "")[:10] for k in ("created", "modified")} for f in fs}
            hits[m["id"]] = list((self._mjson(m["id"], "read3.json") or {}).get("hits_asked") or [])
        g = gates.run(ms, today=self.today, files=files, saved=saved, text_of=self._text_of, hits=hits)
        unread = passes.unread_matters(self)
        if unread:  # every other matter was read; the list holds until these are
            g["issues"]["matter could not be read"] = [(u["number"], u["stage"], u["reason"][:120]) for u in unread]
            g["passed"] = False
        dump(self.data / "gates.json", g)
        if not g["passed"]:
            raise LitigationHold(
                "gate_refused: the gates refused the list: "
                + "; ".join(f"{k} ({len(v)})" for k, v in g["issues"].items())[:400]
            )

    def _parity(self) -> None:
        changes, held = {}, []
        for m in self._json("final.json"):
            mid = m["matter_id"]
            p = self._plan()[mid]
            if not p["read_groups"] and p["audit"] != "all":
                continue
            cs = parity.compare(
                manifest.load_prior(self.state, mid),
                m,
                moved_files=set(p["moved"]),
                overturns=m.get("overturns") or [],
                today=self.today,
            )
            changes[mid] = cs
            held += [(m["number"], c["path"]) for c in parity.unexplained(cs)]
        dump(self.data / "changes.json", changes)
        if held:
            raise LitigationHold(
                f"parity_hold: {len(held)} values changed from the last list with no document cited: "
                + "; ".join(f"{n} {p}" for n, p in held)[:350]
            )

    # ---- build, file, commit ----------------------------------------------------------------
    def _out(self) -> Path:
        return self.data / "out" / f"Litigation status {self.today.isoformat()} (Operator {self.job.job_id[-6:]}).xlsx"

    def _book(self) -> None:
        stats = book.build(self._json("final.json"), self._json("changes.json"), self._out(), self.today)
        leaks = gates.leak_scan(self._out())
        dump(self.data / "book.json", {**stats, "leaks": leaks})
        if leaks:
            self._out().unlink(missing_ok=True)
            raise LitigationFailed(
                f"leak_found: the built workbook carries internal text in {len(leaks)} cells: "
                + "; ".join(f"{c} {w}" for c, w, _ in leaks)[:300]
            )

    def _file(self) -> None:
        pause = deliver.READBACK_PAUSE_SECONDS if self.readback_pause is None else self.readback_pause
        rec = deliver.file_workbook(
            self.seat, self.job.file_to_id, self.job.folder_name, self._out(), self.data, self.log, pause=pause
        )
        dump(self.data / "filed.json", rec)

    def _flags_new(self) -> int:
        n = 0
        for m in self._json("final.json"):
            prior = manifest.load_prior(self.state, m["matter_id"]) or {}
            old = set(prior.get("matter_flags") or []) | {
                f for d in prior.get("defendants") or [] for f in d.get("flags") or []
            }
            new = set(m.get("matter_flags") or []) | {
                f for d in m.get("defendants") or [] for f in d.get("flags") or []
            }
            n += len(new - old)
        return n

    def _report(self) -> None:
        flags = self._flags_new()
        for m in self._json("final.json"):
            mid = m["matter_id"]
            keep = {k: v for k, v in m.items() if k not in ("overturns", "responsible", "integrity")}
            keep["provenance"] = {
                "job_id": self.job.job_id,
                "read_at": self.today.isoformat(),
                "two_pass": False,
                "read": (self._plan().get(mid) or {}).get("mode") or "full",
            }
            manifest.commit(self.state, mid, keep, inventory.files_of(self.data, mid))
        self._put("flags_new", flags)

    def _walk(self) -> Verdict:
        for name, fn in zip(
            STAGES,
            (
                self._inventory,
                self._diff,
                self._fetch,
                self._extract,
                self._screen,
                self._read1,
                self._read2,
                self._read3,
                self._gates,
                self._parity,
                self._book,
                self._file,
                self._report,
            ),
        ):
            self._stage(name, fn)
            if name == "inventory" and not self._matters():
                raise LitigationHold("scope_empty: no open litigation matters are in this job's scope")
        filed = self._json("filed.json")
        return Verdict(
            "delivered",
            stage="report",
            files=[
                {
                    "role": "workbook",
                    "name": filed["name"],
                    "size": filed["size"],
                    "sha256": filed["sha256"],
                    "file_id": filed["file_id"],
                }
            ],
            folder_id=filed["folder_id"],
            flags_new=int(self._state().get("flags_new") or 0),
        )

    def _current(self) -> str:
        st = self._state()
        return next((s for s in STAGES if st.get(s, {}).get("status") != "done"), "report")

    def run(self) -> Verdict:
        try:
            v = self._walk()
        except limits_mod.LimitHold as hold:
            v = Verdict("failed", stage=self._current(), reason=f"limit: {hold.reason}")
        except LitigationHold as h:
            v = Verdict("held", stage=self._current(), reason=str(h))
        except LitigationFailed as exc:
            v = Verdict("failed", stage=self._current(), reason=str(exc))
        except ReadIncomplete as exc:
            v = Verdict("failed", stage=self._current(), reason=f"read_incomplete: {exc}")
        except inventory.InventoryError as exc:
            v = Verdict("failed", stage=self._current(), reason=f"inventory_unreadable: {exc}")
        except Exception as exc:  # noqa: BLE001 - the verdict carries a sentence; the trace goes to the log
            self.log(traceback.format_exc())
            v = Verdict(
                "failed",
                stage=self._current(),
                reason=f"unexpected: {type(exc).__name__} at {self._current()}: {str(exc)[:300]}",
            )
        if v.reason and not REASON.match(v.reason):
            v.reason = f"unexpected: {v.reason}"
        v.cents = int(round(self.budget.refresh() * 100))
        if (self.data / "inventory.json").is_file():
            v.matters_total = len(self._matters())
        if (self.data / "plan.json").is_file():
            v.matters_reread = sum(1 for p in self._plan().values() if p["read_groups"])
        return v
