"""`medchron litigate --rehearse <report.json> --read-matters <numbers> <job_dir>`
on a discovery12 job: the breakdown's PROOF on named cases (2026-10-29).

The real reads, at the real (batch) price, on the named matters only, then the
gates, the workbook, the print-out and the review, and nothing filed: the seat
is wrapped read-only (a write raises before it is made) and the walk stops
before ``file``. The lane's state is read (the print-out's court values) and
never written. The report JSON carries what the full run is projected from:
the cents by stage and by model, each stage's wall-clock, the batch ids, each
case's chunks and entries, the gate findings, and the files built.
"""

from __future__ import annotations

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


class Discovery12Proof(Discovery12Run):
    def __init__(self, job_dir: Path, *, report_path: Path, seat_factory: Any = None, **kw: Any) -> None:
        def read_only() -> Any:
            if seat_factory is not None:
                return ReadOnlySeat(seat_factory())
            from ...seat import open_seat

            return ReadOnlySeat(open_seat(self.job.slug))

        super().__init__(job_dir, seat_factory=read_only, **kw)
        if not self.only:
            raise LitigationFailed("unexpected: a discovery proof names its matters (--read-matters)")
        self.report_path = Path(report_path)

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
        }
