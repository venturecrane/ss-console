"""`medchron litigate --rehearse <report.json>`: the real job on the real seat,
minus every effect.

What a rehearsal guarantees, each pinned by a test:

* **No write to the firm's system.** The seat is wrapped read-only: a
  ``create_folder`` or ``add_file`` raises before any request is made, and the
  walk stops before ``file`` (and ``report``) in any case.
* **No broker call.** The runner never speaks to the broker; a rehearsal job
  dir that has no ``job.json`` gets a synthetic one (scope all, nothing to
  file to, this month's spend 0), so the lane is not involved at all.
* **No touch of the lane's state.** The state dir is ``--state-dir`` or, when
  none is given, ``<job_dir>/rehearsal-state``; never the volume's. It is
  seeded ONCE from ``<inputs>/baseline/`` (never overwritten), and nothing is
  committed to it: ``report`` does not run.
* **Bounded spend.** ``--read-limit N`` reads N matters (the named ones first,
  then those with the most candidate files); inventory, manifest and diff
  still cover every matter, so the counts and the projection are whole. Fetch
  and extract cover every matter the unlimited run would read, too, so the
  OCR count below is the whole run's: a limited rehearsal now DOWNLOADS what
  the full run would (2026-10-24; $0, but disk and time). A fetch that would
  not fit on the volume (``disk_short``) is recorded, not raised, like a gate.
* **No vision call, and an exact OCR count** (2026-10-24). ``_ocr`` is a
  counting stub: each scanned page extract would send to vision is recorded
  (``ocr-needed.jsonl``: file id, page, model) and answered with a marked
  placeholder,
  so ``--read-limit 0`` finishes extract at $0 and ``counts.ocr_pages_needed``
  is the pages the real run would pay for (text-cache misses only).
* **No write to the text cache.** It is opened read-only: its hits are
  counted as hits, and nothing a rehearsal read (placeholders included)
  reaches it.

Gates, parity and the workbook's leak scan run and are RECORDED, not obeyed:
a rehearsal exists to see what they would say. The verdict is always
``held`` with a ``rehearsal: ...`` reason, and the report JSON carries the
measurements (``REPORT_KEYS``).
"""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from typing import Any, Callable

from ..seat import SeatError
from ..stages.base import append_jsonl
from . import extract as extract_mod, manifest, textcache, vocab
from .outcome import LitigationFailed, LitigationHold, Verdict
from .rehearse_report import OCR_LOG, _reads, build_report, write_report
from .rehearse_report import REHEARSAL_STAGES, REPORT_KEYS  # noqa: F401 - re-exported: callers and tests read them here
from .run import LitigationRun
from .tools import dump

SYNTHETIC_JOB_ID = "01REHEARSA0000000000000000"
NOWHERE = "00000000-0000-4000-8000-000000000000"
OCR_STUB = textcache.REHEARSAL_MARK + " a scanned page this rehearsal counted and did not transcribe]"


class RehearsalError(RuntimeError):
    pass


class OfflineSeat:
    """No seat at all (a laptop replay of a copied job dir): every listing,
    mint and write raises, so a replay reads only what the job dir holds."""

    writes_refused = 0

    def __getattr__(self, name: str) -> Any:
        def refuse(*_a: Any, **_k: Any) -> Any:
            raise SeatError("offline: no seat in this run")

        return refuse

    def mint(self, matter_id: str, file_ids: list[str]) -> list[dict[str, Any]]:
        return [{"id": f, "error": "offline: no seat in this run"} for f in file_ids]


class ReadOnlySeat:
    """Every read passes through; every write raises before it is made."""

    def __init__(self, seat: Any) -> None:
        self._seat = seat
        self.writes_refused = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._seat, name)

    def create_folder(self, matter_id: str, name: str) -> dict[str, Any]:
        self.writes_refused += 1
        raise SeatError("rehearsal: the seat is read-only")

    def add_file(self, matter_id: str, folder_id: str, name: str, data: bytes) -> dict[str, Any]:
        self.writes_refused += 1
        raise SeatError("rehearsal: the seat is read-only")


def ensure_job(job_dir: Path, slug: str, staff_ids: list[str]) -> None:
    """A synthetic job.json when the rehearsal dir has none (never replaced)."""
    path = job_dir / "job.json"
    if path.is_file():
        return
    job_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        "kind": "litigation",
        "job_id": SYNTHETIC_JOB_ID,
        "trigger": "request",
        "requester": "rehearsal@rehearsal.invalid",
        "message_ref": "rehearsal",
        "request_text": "rehearsal",
        "scope": {"attorney_staff_ids": staff_ids} if staff_ids else {"all": True},
        "file_to_matter_id": NOWHERE,
        "file_to_matter_number": "REHEARSAL",
        "folder_name": "Rehearsal",
        "slug": slug,
        "month_cents_used": 0,
    }
    path.write_text(json.dumps(doc, indent=1), encoding="utf-8")


def seed_state(state: Path, inputs_dir: Path) -> bool:
    """Copy ``<inputs>/baseline/`` into the state dir once. True if seeded now."""
    src = inputs_dir / "baseline"
    if (state / "baseline.json").is_file() or not src.is_dir():
        state.mkdir(parents=True, exist_ok=True)
        return False
    shutil.copytree(src, state, dirs_exist_ok=True)
    return True


def _limit(p: dict[str, Any]) -> dict[str, Any]:
    """A plan a limited rehearsal does not read: what it would have read is
    kept in ``limited_out`` (the widest seen, when limited twice)."""
    lo = p.get("limited_out") or {"read_groups": [], "audit": "none"}
    groups = [g for g in vocab.GROUPS if g in {*lo["read_groups"], *p["read_groups"]}]
    audit = "all" if "all" in (lo["audit"], p["audit"]) else p["audit"] if p["audit"] != "none" else lo["audit"]
    return {**p, "limited_out": {"read_groups": groups, "audit": audit}, "read_groups": [], "audit": "none"}


def pick(plan: dict[str, Any], limit: int, named: list[str]) -> list[str]:
    """The matters a limited rehearsal reads: named first, then the most candidates."""
    want = [mid for mid, p in plan.items() if _reads(p)]
    first = [m for m in named if m in plan]
    rest = sorted((m for m in want if m not in first), key=lambda m: (-len(plan[m]["candidates"]), m))
    return (first + rest)[:limit]


class RehearsalRun(LitigationRun):
    def __init__(
        self,
        job_dir: Path,
        *,
        report_path: Path,
        read_limit: int | None = None,
        read_matters: list[str] | None = None,
        seat_factory: Callable[[], Any] | None = None,
        offline: bool = False,
        **kw: Any,
    ) -> None:
        self.offline = offline

        def read_only() -> Any:
            if offline:
                return OfflineSeat()
            if seat_factory is not None:
                return ReadOnlySeat(seat_factory())
            from ..seat import open_seat

            return ReadOnlySeat(open_seat(self.job.slug))

        super().__init__(job_dir, seat_factory=read_only, **kw)
        if self.state.resolve() == manifest.state_dir().resolve():
            raise RehearsalError("rehearsal: refused; the state dir is the lane's own (pass --state-dir)")
        self.report_path = Path(report_path)
        self.read_limit = read_limit
        self.read_matters = list(read_matters or [])
        self.seeded = seed_state(self.state, self.firm.root)
        self.cache.read_only = True
        self._ocr_lock = threading.Lock()

    def _ocr(self, png: bytes) -> str:
        """The counting stub: never a paid call, never a cached text."""
        cur = extract_mod.CURRENT
        row = {"file_id": getattr(cur, "file", None), "page": getattr(cur, "page", None)}
        with self._ocr_lock:
            append_jsonl(self.data / OCR_LOG, {**row, "model": self.firm.model("transcription"), "bytes": len(png)})
        if self._progress is not None:
            self._progress.add("vision pages counted")
        return OCR_STUB

    def _active(self) -> list[str]:
        """The matters a limited rehearsal fetches and extracts: every one the
        unlimited run would read, so the OCR count is whole."""
        if self.read_limit is None:
            return super()._active()
        act = set(super()._active())
        court_only = self._court_only()

        def would(p: dict[str, Any]) -> bool:
            lo = dict(p.get("limited_out") or p)
            if court_only:  # a court-only list never fetches or counts discovery
                lo["read_groups"] = [g for g in lo["read_groups"] if g != vocab.GROUP_DISCOVERY]
            return _reads(lo)

        return [m for m, p in self._plan().items() if m in act or would(p)]

    def _stage(self, name: str, fn: Callable[[], Any]) -> None:
        before = self.budget.refresh()
        try:
            super()._stage(name, fn)
        except (LitigationHold, LitigationFailed) as exc:
            disk = name == "fetch" and str(exc).startswith("disk_short")
            if (
                name not in ("gates", "parity", "book", "review")
                and not (self.offline and name == "fetch")
                and not disk
            ):
                raise
            self._put(f"{name}_refusal", str(exc))
            self._put(name, {"status": "done", "refused": True})
        st = self._state().get(name) or {}
        if "cents" not in st:
            self._put(name, {**st, "cents": round((self.budget.refresh() - before) * 100, 2)})

    def _diff(self) -> None:
        super()._diff()
        if self.read_limit is None:
            return
        plan = self._plan()
        keep = set(pick(plan, self.read_limit, self.read_matters))
        dump(self.data / "plan.json", {mid: p if mid in keep else _limit(p) for mid, p in plan.items()})

    def _screen(self) -> None:
        """The screen settles seed gaps and can hand a limited-out matter its
        read groups back (2026-10-24: ``--read-limit 0`` still read one
        matter); the limit is applied again after it."""
        super()._screen()
        if self.read_limit is None:
            return
        plan = self._plan()
        dump(self.data / "plan.json", {mid: _limit(p) if "limited_out" in p else p for mid, p in plan.items()})

    def run(self) -> Verdict:
        """The report is written on EVERY exit path, a failed run included:
        a rehearsal that dies without one has measured nothing."""
        v = super().run()
        try:
            write_report(self, verdict=v)
        except Exception as exc:  # noqa: BLE001 - the verdict still goes out; the report says why it is thin
            self.report_path.write_text(
                json.dumps({"rehearsal": {"verdict": json.loads(v.to_json()), "report_error": repr(exc)[:300]}}),
                encoding="utf-8",
            )
        return v

    def _walk(self) -> Verdict:
        for name in REHEARSAL_STAGES:
            self._stage(name, getattr(self, f"_{name}"))
            if name == "inventory" and not self._matters():
                raise LitigationHold("scope_empty: no open litigation matters are in this job's scope")
        report = build_report(self)
        c, g, par = report["counts"], report["gates"], report["parity"]
        return Verdict(
            "held",
            stage="book",
            reason=(
                f"rehearsal: {c.get('matters_inventoried')} matters inventoried, {c.get('matters_read')} read, "
                f"{len(c.get('matters_unread') or [])} unread, "
                f"{sum((g.get('by_rule') or {}).values())} gate findings, "
                f"{par.get('unexplained')} unexplained changes; nothing filed"
            ),
            flags_new=0,
        )
