"""Replay a saved run's writer (``map``) at $0, at any worker count, and diff.

Usage (from operator/, with the jobs package installed; nothing is paid):
    python runners/jobs/tests/replay_writer.py --slug-dir <saved slug dir> --unit <unit> \\
        --job-dir <dir with job.yaml> --firm <firm config> --workers 8 --out <scratch dir> [--pace]
    run it again with --workers 1 and another --out, then diff the two runs/<unit> dirs
    (maps, .parts, unread_pages.json; usage.jsonl compares as sorted lines).
--pace sleeps each reply out-tokens/130 seconds, so the wall time printed is the real writer's.

The fake client goes in through ``StageRun.client_factory``. Each writer
request is answered by the saved ``map-*.md`` whose chunk sha ``usage.jsonl``
recorded (``compose._Composer.handle``); a sha the saved run recorded as
emptied is answered emptied, so the split repeats; any other request is
refused, so a piece the saved run named unread walks the same ladder. The
saved run's chunk-XX.txt/.sha are copied in first, so the same cut is used.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

from jobs.medchron import config as config_mod, job as job_mod
from jobs.stages import chunking, compose, compose_pool
from jobs.stages.base import StageRun

PACE_TOKENS_PER_S = 130.0


def _map_name(label: Any) -> str:
    s = str(label)
    i, _, part = s.partition(".")
    return f"map-{int(i):02d}-{part}.md" if part else f"map-{int(i):02d}.md"


def answers(run_dir: Path) -> dict[str, tuple[str, str, int, int]]:
    """sha -> (reply text, stop reason, in tokens, out tokens) from a saved run."""
    out: dict[str, tuple[str, str, int, int]] = {}
    for row in compose.read_usage(run_dir):
        h = row.get("sha")
        if not h:
            continue
        if row.get("empty"):
            out.setdefault(h, ("x", "end_turn", int(row.get("in") or 0), int(row.get("out") or 1)))
            continue
        p = run_dir / _map_name(row.get("chunk"))
        p = p if p.is_file() else p.with_name(p.name + ".truncated")
        if p.is_file():
            reply, stop = p.read_text(encoding="utf-8"), str(row.get("stop") or "end_turn")
            out[h] = (reply, stop, int(row.get("in") or 0), int(row.get("out") or 0))
    return out


class Replay:
    """An SDK client that answers the writer from a saved run."""

    def __init__(self, saved: dict[str, tuple[str, str, int, int]], pace: bool) -> None:
        self.saved, self.pace = saved, pace
        self.calls = 0
        self.lock = threading.Lock()
        self.messages = NS(create=self._create, stream=self._stream, batches=None)

    def _reply(self, params: dict[str, Any]) -> NS:
        with self.lock:
            self.calls += 1
        text = params["messages"][0]["content"]
        got = self.saved.get(chunking.sha(text))
        reply, stop, tin, out = got if got else ("", "refusal", 0, 0)
        if self.pace and out:
            time.sleep(out / PACE_TOKENS_PER_S)
        usage = NS(input_tokens=tin, output_tokens=out)
        usage.cache_read_input_tokens = usage.cache_creation_input_tokens = 0
        return NS(content=[NS(type="text", text=reply)], stop_reason=stop, usage=usage)

    def _create(self, **params: Any) -> NS:
        return self._reply(params)

    def _stream(self, **params: Any) -> Any:
        msg = self._reply(params)

        class _S:
            def __enter__(self) -> "_S":
                return self

            def __exit__(self, *a: Any) -> bool:
                return False

            def get_final_message(self) -> NS:
                return msg

        return _S()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for a in ("--slug-dir", "--unit", "--job-dir", "--firm", "--out"):
        ap.add_argument(a, required=True)
    ap.add_argument("--workers", type=int, default=compose_pool.COMPOSE_WORKERS)
    ap.add_argument("--pace", action="store_true")
    a = ap.parse_args(argv)
    src, out = Path(a.slug_dir), Path(a.out)
    saved_run = src / "runs" / a.unit
    (out / "units").mkdir(parents=True, exist_ok=True)
    shutil.copy2(src / "units" / f"{a.unit}.json", out / "units" / f"{a.unit}.json")
    run_dir = out / "runs" / a.unit
    run_dir.mkdir(parents=True, exist_ok=True)
    for p in list(saved_run.glob("chunk-??.txt")) + list(saved_run.glob("chunk-??.sha")):
        shutil.copy2(p, run_dir / p.name)
    client = Replay(answers(saved_run), a.pace)
    job = job_mod.load(Path(a.job_dir))
    unit = next(u for u in job.units if u.unit == a.unit)
    sr = StageRun(
        job=job,
        cfg=config_mod.load(a.firm),
        unit=unit,
        slug_dir=out,
        decided={},
        log=lambda m: print(m, flush=True),
        seat_factory=lambda: (_ for _ in ()).throw(RuntimeError("the writer replay touches no seat")),
        client_factory=lambda: client,
    )
    compose_pool.COMPOSE_WORKERS = a.workers
    t0 = time.monotonic()
    rc = compose.run(sr)
    print(f"REPLAY workers={a.workers} rc={rc} calls={client.calls} wall_s={time.monotonic() - t0:.1f}")
    print(json.dumps({"run_dir": str(run_dir)}))
    return rc


if __name__ == "__main__":
    sys.exit(main())
