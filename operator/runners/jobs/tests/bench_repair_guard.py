"""Peak memory and wall time of the repair guard on a District-sized job.

The 2026-11-05 rehearsal of ``repair_guard`` on the real District discovery
job (524 record text files, 526 extracted rows, about 11 MB) ran past two
minutes and out of memory on a 3.9 GB box; the guard runs inside a live job
on a 2 GB seat. This builds a synthetic job of that shape (524 files, 11 MB,
6 sets of 3 to 5 draft versions each, every name and number invented), then,
in a fresh process, measures the job index build and a full replay.

    python tests/bench_repair_guard.py            # prints the numbers
    python tests/bench_repair_guard.py --check    # exits 1 over the targets

Targets: under 300 MB peak RSS and under 30 s, on a laptop. Not collected by
pytest (it takes seconds and writes 11 MB); ``test_drafting_repair_guard``
holds the rules.
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

FILES, TOTAL_BYTES, SETS = 524, 11 * 1024 * 1024, 6
PEAK_MB, WALL_S = 300, 30
_MONTHS = "January February March April May June July August September October November December".split()


def _vocab(rng: random.Random) -> list[str]:
    letters = "abcdefghijklmnopqrstuvwxyz"
    words = {"".join(rng.choice(letters) for _ in range(rng.randint(3, 11))) for _ in range(60_000)}
    return sorted(words)


def _sentence(rng: random.Random, vocab: list[str], names: list[str]) -> str:
    w = [rng.choice(vocab) for _ in range(rng.randint(8, 22))]
    roll = rng.random()
    if roll < 0.2:
        w.insert(rng.randrange(len(w)), rng.choice(names))
    if roll < 0.12:
        w.insert(rng.randrange(len(w)), f"{rng.randint(1, 12):02d}/{rng.randint(1, 28):02d}/20{rng.randint(10, 26)}")
    if 0.1 < roll < 0.18:
        w.insert(rng.randrange(len(w)), f"{rng.choice(_MONTHS)} {rng.randint(1, 28)}, 20{rng.randint(10, 26)}")
    if 0.15 < roll < 0.25:
        w.insert(rng.randrange(len(w)), f"${rng.randint(10, 99_999):,}.{rng.randint(0, 99):02d}")
    if 0.2 < roll < 0.4:
        w.insert(rng.randrange(len(w)), str(rng.randint(0, 999_999)))
    s = " ".join(w)
    return s[0].upper() + s[1:] + "."


def _draft(rng: random.Random, vocab: list[str], names: list[str], n: int) -> dict[int, str]:
    return {i: "Admit that " + _sentence(rng, vocab, names) for i in range(1, n + 1)}


def _doc(items: dict[int, str]) -> str:
    body = "\n\n".join(f"REQUEST FOR ADMISSION NO. {n}\n\n{t}" for n, t in items.items())
    return f'# DEFINITIONS\n\n1. "PREMISES" means the store.\n\n# REQUESTS FOR ADMISSION\n\n{body}\n'


def build(root: Path, seed: int = 11) -> Path:
    """The synthetic job's copy under ``root``: ``root/copy/data`` (written a file at a time)."""
    rng = random.Random(seed)
    vocab = _vocab(rng)
    names = [rng.choice(vocab).title() + " " + rng.choice(vocab).title() for _ in range(2_000)]
    data = root / "copy" / "data"
    texts = root / "copy" / "text"
    texts.mkdir(parents=True)
    rows = []
    per = TOTAL_BYTES // FILES
    for i in range(FILES):
        out, size = [], 0
        while size < per:
            s = _sentence(rng, vocab, names)
            out.append(s)
            size += len(s) + 1
        p = texts / f"doc-{i:04d}.txt"
        p.write_text("\n".join(out), encoding="utf-8")
        # The seat's path: a copy resolves it by file name.
        rows.append(json.dumps({"name": f"doc {i}", "text_path": f"/opt/data/jobs/J/data/text/{p.name}"}))
    rows += [json.dumps({"name": "scan a", "text_path": ""}), json.dumps({"name": "scan b"})]
    data.mkdir(parents=True)
    (data / "extracted.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (data / "digest.md").write_text(
        "\n".join(f"## RECORD | doc {i} | {_sentence(rng, vocab, names)}" for i in range(400)), encoding="utf-8"
    )
    for k in range(1, SETS + 1):
        u = data / f"set-{k}"
        u.mkdir()
        items = _draft(rng, vocab, names, 60)
        for v in range(1, rng.randint(3, 5) + 1):
            (u / f"draft-v{v}.md").write_text(_doc(items), encoding="utf-8")
            items = dict(items)
            for n in rng.sample(sorted(items), 4):  # a repair rewords four requests
                items[n] = "Admit that " + _sentence(rng, vocab, names + ["Officer Inventedname"])
    return data


_MEASURE = """
import json, resource, sys, time
from pathlib import Path
from jobs.drafting import newfacts, repair_guard
data = Path(sys.argv[1])
t0 = time.perf_counter()
files, listed = repair_guard._record_files(data)
def chunks():
    yield (data / "digest.md").read_text(encoding="utf-8")
    for f in files:
        yield f.read_text(encoding="utf-8", errors="replace")
newfacts.index(chunks, data)
t1 = time.perf_counter()
out = repair_guard.replay(data)
t2 = time.perf_counter()
newfacts._MEMO.clear()
repair_guard.replay(data)
t3 = time.perf_counter()
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
peak_mb = peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024
print(json.dumps({"files": len(files), "listed": listed, "index_s": round(t1 - t0, 2),
    "replay_warm_s": round(t2 - t1, 2), "replay_from_cache_s": round(t3 - t2, 2),
    "peak_rss_mb": round(peak_mb, 1), "rounds": len(out) - 1, "first": out[0]}))
"""


#: The live job's path: the record text is already one string in the job
#: (``finish._corpus_text``), handed to the guard with the digest.
_LIVE = """
import json, resource, sys, time
from pathlib import Path
from jobs.drafting import repair_guard
data = Path(sys.argv[1])
files, _ = repair_guard._record_files(data)
text = (data / "digest.md").read_text() + "\\n" + "\\n\\n".join(f.read_text() for f in files)
base = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
t0 = time.perf_counter()
g = repair_guard.Guard(data / "set-1", text)
v1, v2 = (data / "set-1" / "draft-v1.md").read_text(), (data / "set-1" / "draft-v2.md").read_text()
_, refused = g.check(v1, v2, "", "bench")
t1 = time.perf_counter()
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
mb = (lambda x: x / (1024 * 1024)) if sys.platform == "darwin" else (lambda x: x / 1024)
print(json.dumps({"live_index_and_check_s": round(t1 - t0, 2), "live_peak_rss_mb": round(mb(peak), 1),
    "live_rss_before_guard_mb": round(mb(base), 1), "live_refused": len(refused)}))
"""


def measure(data: Path) -> dict:
    out = {}
    for script in (_MEASURE, _LIVE):
        for f in data.glob("known-index-*"):
            f.unlink()  # each measurement builds the index cold
        argv = [sys.executable, "-c", script, str(data)]  # this module's own script, no shell
        r = subprocess.run(argv, capture_output=True, text=True, check=True)  # noqa: S603 - argv is literal here
        out.update(json.loads(r.stdout.strip().splitlines()[-1]))
    return out


def main(argv: list[str]) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        t = time.perf_counter()
        data = build(Path(tmp))
        size = sum(p.stat().st_size for p in (Path(tmp) / "copy" / "text").iterdir())
        got = {"corpus_bytes": size, "build_corpus_s": round(time.perf_counter() - t, 2), **measure(data)}
    got["total_s"] = round(got["index_s"] + got["replay_warm_s"], 2)
    print(json.dumps(got, indent=1))
    over = max(got["peak_rss_mb"], got["live_peak_rss_mb"]) >= PEAK_MB
    if "--check" in argv and (over or got["total_s"] >= WALL_S or got["live_index_and_check_s"] >= WALL_S):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
