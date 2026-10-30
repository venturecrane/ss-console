"""`medchron litigate --rehearse <report.json> --read-matters <numbers> <job_dir>`
on a discovery12 job: the breakdown's PROOF on named cases (2026-10-29).

The real reads, at the real (batch) price, on the named matters only, then the
gates, the workbook, the print-out and the review, and nothing filed: the seat
is wrapped read-only (a write raises before it is made) and the walk stops
before ``file``. The lane's state is read (the print-out's court values) and
never written. The report JSON carries what the full run is projected from:
the cents by stage and by model, each stage's wall-clock, the batch ids, each
case's chunks and entries, the gate findings, and the files built.

``--reuse-check`` (2026-10-30, ``carry.py``) names no matters and spends
nothing: every case a saved record answers is rebuilt from it through the
gates, the workbook and the print-out; every other case is dropped from scope
and listed with what its read would carry. No batch can be submitted and no
page is transcribed (a page that needs it reads as empty, so its case lands
among those to read). The report's per-case row digest is compared with the
filed breakdown.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from ..outcome import LitigationFailed, LitigationHold, Verdict
from ..rehearse import ReadOnlySeat
from ..run import STAGES
from . import sections
from .run import Discovery12Run

PROOF_STAGES = STAGES[: STAGES.index("file")]
#: A scope no matter id matches: the reuse check with nothing reused builds nothing.
NO_CASE = "(no reused case)"


class Discovery12Proof(Discovery12Run):
    def __init__(
        self, job_dir: Path, *, report_path: Path, seat_factory: Any = None, reuse_check: bool = False, **kw: Any
    ) -> None:
        def read_only() -> Any:
            if seat_factory is not None:
                return ReadOnlySeat(seat_factory())
            from ...seat import open_seat

            return ReadOnlySeat(open_seat(self.job.slug))

        super().__init__(job_dir, seat_factory=read_only, **kw)
        self.reuse_check = reuse_check
        self.ocr_skipped = 0
        if not self.only and not reuse_check:
            raise LitigationFailed("unexpected: a discovery proof names its matters (--read-matters)")
        self.report_path = Path(report_path)
        # A proof runs by hand (as root): what it extracts is not written into
        # the lane's text cache, whose files the lane's own user must own.
        self.cache.read_only = True

    def _stage(self, name: str, fn: Any) -> None:
        """Gates, book and review are RECORDED, not obeyed: the proof is there
        to see what they say."""
        try:
            super()._stage(name, fn)
        except (LitigationHold, LitigationFailed) as exc:
            if name not in ("gates", "book", "review"):
                raise
            self._put(f"{name}_refusal", str(exc))
            self._put(name, {"status": "done", "refused": True})

    def _before_batch(self, stage: str, items: int, chars: int) -> None:
        if self.reuse_check:
            raise LitigationFailed(f"unexpected: the reuse check spends nothing ({stage}: {items} requests)")
        super()._before_batch(stage, items, chars)

    def _ocr(self, png: bytes) -> str:
        if self.reuse_check:
            self.ocr_skipped += 1
            return ""
        return super()._ocr(png)

    def _read1(self) -> None:
        if not self.reuse_check:
            return super()._read1()
        self._plan_reuse()
        reused = self._reused()
        keys = self._json("d12-keys.json")
        would = []
        for m in self._matters():
            if m["id"] in keys and m["id"] not in reused:
                _ctx, parts = self._chunks(m["id"])
                would.append(
                    {"number": m["number"], "chunks": len(parts), "chars": sum(len(t) for p in parts for _n, t in p)}
                )
        self._put("would_read", would)
        # An empty scope reads as "every case" (run._matters), so a check that
        # reuses nothing is held to a scope no matter can match.
        self.only = [m["id"] for m in self._matters() if m["id"] in reused] or [NO_CASE]
        return None

    def _walk(self) -> Verdict:
        for name in PROOF_STAGES:
            self._stage(name, getattr(self, f"_{name}"))
        return Verdict("held", stage="file", reason="rehearsal: discovery proof; nothing filed")

    def run(self) -> Verdict:
        t0 = time.time()
        v = super().run()
        try:
            self.report_path.write_text(json.dumps(self.report(v, time.time() - t0), indent=1), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - the verdict still goes out; the report says why it is thin
            self.report_path.write_text(
                json.dumps({"verdict": json.loads(v.to_json()), "report_error": repr(exc)[:300]})
            )
        return v

    def report(self, v: Verdict, seconds: float) -> dict[str, Any]:
        st = self._state()
        by_model: dict[str, float] = {}
        by_stage: dict[str, float] = {}
        rows = 0
        ledger = self.data / "usage-ledger.jsonl"
        for line in ledger.read_text(encoding="utf-8").splitlines() if ledger.is_file() else []:
            row = json.loads(line)
            usd = self.pricing.price_row(row)
            by_model[str(row.get("model"))] = by_model.get(str(row.get("model")), 0.0) + usd
            by_stage[str(row.get("stage"))] = by_stage.get(str(row.get("stage")), 0.0) + usd
            rows += 1
        finals = self._json("final.json") if (self.data / "final.json").is_file() else []
        cases = []
        for m in finals:
            secs = m.get("sections") or {}
            cases.append(
                {
                    "number": m["number"],
                    "read": m["read"],
                    "chunks": (
                        self._json("d12-read-chunks.json") if (self.data / "d12-read-chunks.json").is_file() else {}
                    ).get(m["matter_id"]),
                    "candidates": len((self._plan().get(m["matter_id"]) or {}).get("candidates") or []),
                    "entries": {k: len(secs.get(k) or []) for k in sections.KEYS},
                    "dated_values": len(sections.dated_values(m)),
                }
            )
        batches = (
            sorted(p.name for p in (self.data / "batches").glob("*.json")) if (self.data / "batches").is_dir() else []
        )
        return {
            "verdict": json.loads(v.to_json()),
            "seconds_total": round(seconds),
            "stage_seconds": {s: (st.get(s) or {}).get("seconds") for s in STAGES},
            "refusals": {k: v for k, v in st.items() if k.endswith("_refusal")},
            "usd_total": round(sum(by_stage.values()), 4),
            "usd_by_stage": {k: round(x, 4) for k, x in by_stage.items()},
            "usd_by_model": {k: round(x, 4) for k, x in by_model.items()},
            "ledger_rows": rows,
            "batch_files": batches,
            "cases": cases,
            "gates": self._json("gates.json") if (self.data / "gates.json").is_file() else None,
            "files_built": [p.name for p in (self._out(), self._pdf()) if p.is_file()],
            "reuse_check": self._reuse_report() if self.reuse_check else None,
        }

    def _reuse_report(self) -> dict[str, Any]:
        """What the reuse check proves: the reused cases, each one's printed
        rows as a digest to compare with the filed breakdown, and what a read
        of every other case would carry."""
        reused = self._reused()
        rows = self._json("final-complete.json") if (self.data / "final-complete.json").is_file() else []
        digest = {
            str(f["number"]): hashlib.sha256(json.dumps(f.get("sections") or {}, sort_keys=True).encode()).hexdigest()
            for f in rows
            if f.get("matter_id") in reused
        }
        would = self._state().get("would_read") or []
        return {
            "reused": len(reused),
            "row_digest": digest,
            "would_read": would,
            "would_read_chars": sum(w["chars"] for w in would),
            "pages_not_transcribed": self.ocr_skipped,
        }
