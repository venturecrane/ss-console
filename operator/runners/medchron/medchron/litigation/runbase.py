"""The plumbing every litigation run shares: the staged state file, the
per-matter artifacts, the progress line, and the per-call hook. Split from
``run.py`` (the stages) to keep each module under the size ceiling."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from . import extract as extract_mod, fetch as fetch_mod
from .progress import Progress
from .tools import dump


class RunBase:
    data: Path
    log: Callable[[str], None]
    limits: Any
    budget: Any
    doorway: Any
    firm: Any
    _progress: Progress | None = None

    def _plan(self) -> dict[str, Any]:
        raise NotImplementedError

    def _before_call(self, stage: str) -> None:
        """Every paid call: a quiet stage speaks (counts only), then the limits."""
        if self._progress is not None:
            self._progress.beat()
        self.limits.check_each_call(self.budget.refresh(), stage)

    def _state(self) -> dict[str, Any]:
        p = self.data / "state.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}

    def _put(self, key: str, value: Any) -> None:
        st = self._state()
        st[key] = value
        dump(self.data / "state.json", st)

    def _is_done(self, stage: str) -> bool:
        return self._state().get(stage, {}).get("status") == "done"

    def reopen(self, stages: list[str]) -> None:
        """Reopen the named stages AND every stage after the earliest of them
        (a re-read must be re-gated and rebuilt). A read stage's per-matter
        artifacts go too, unread markers included, or the stage would skip
        every matter it already did."""
        from .run import STAGES

        known = [s for s in stages if s in STAGES]
        if not known:
            return
        first = min(STAGES.index(s) for s in known)
        for s in STAGES[first:]:
            if s in self._state():
                self._put(s, {"status": "reopened"})
        reads = [s for s in STAGES[first:] if s in ("read1", "read2", "read3")]
        root = self.data / "m"
        for mdir in root.iterdir() if root.is_dir() else []:
            for s in reads:
                (mdir / f"{s}.json").unlink(missing_ok=True)
            if reads:
                (mdir / "unread.json").unlink(missing_ok=True)

    def _stage(self, name: str, fn: Callable[[], Any]) -> None:
        if self._is_done(name):
            return
        t0 = time.time()
        self.log(f"[{name}] start")
        fn()
        self._put(
            name, {"status": "done", "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "seconds": round(time.time() - t0, 1)}
        )

    def _json(self, name: str) -> Any:
        return json.loads((self.data / name).read_text(encoding="utf-8"))

    def _mfile(self, mid: str, name: str) -> Path:
        return fetch_mod.matter_dir(self.data, mid) / name

    def _mjson(self, mid: str, name: str) -> Any:
        p = self._mfile(mid, name)
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None

    def _ocr(self, png: bytes) -> str:
        if self._progress is not None:
            self._progress.add("vision pages")
        import base64

        img = {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": base64.standard_b64encode(png).decode()},
        }
        r = self.doorway.call(
            "litigation_ocr",
            model=self.firm.model("transcription"),
            messages=[{"role": "user", "content": [img, {"type": "text", "text": extract_mod.OCR_PROMPT}]}],
            max_tokens=8000,
            cache_blocks=(),
        )
        return r.text

    def _reading(self, stage: str, audit: bool = False) -> Progress:
        n = sum(1 for p in self._plan().values() if p["read_groups"] or (audit and p["audit"] == "all"))
        self._progress = Progress(self.log, stage, n, "matters started", every=1)
        return self._progress
