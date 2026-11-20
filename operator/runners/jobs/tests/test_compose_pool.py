"""The writer composes several chunks at once (``stages/compose_pool``) and
writes exactly what one-at-a-time wrote: the same maps, part records and
unread pages, byte for byte, through the real composer and doorway. A kill or
a limit mid-fan-out leaves a queue that resumes only the unanswered pieces.
All names and text are invented."""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

import pytest

from jobs.limits import LimitHold
from jobs.stages import chunking, compose as compose_stage, compose_pool
from test_compose_stages import MAP_OUT, Scripted, _msg, _sr, _unit_files


def _pages(n: int, word: str) -> str:
    return "".join(f"[p.{k}] " + f"{word} record text " * 40 + "\n" for k in range(1, n + 1))


#: Four files: one cut into three windows, one whose whole window comes back
#: emptied (split in two), one declined on every attempt (named unread), one plain.
FILES = {
    "alpha clinic": _pages(30, "alpha"),
    "bulky report": _pages(12, "bulky"),
    "declined note": "[p.1] declined page text\n",
    "delta imaging": _pages(12, "delta"),
}


def _reply(text: str):
    """Answers keyed by the request text, never by arrival order."""
    if "declined page text" in text:
        return _msg("no", stop="refusal")
    if "bulky record" in text and "[p.1]" in text and "[p.12]" in text:
        return _msg("x")  # the whole window emptied: split
    return _msg(MAP_OUT + f"\n<!-- {chunking.sha(text)[:12]} -->\n")


class Slow(Scripted):
    """Scripted, slow enough that workers overlap; records the peak."""

    def __init__(self, reply=None) -> None:
        super().__init__(reply or (lambda p, n: _reply(p["messages"][0]["content"])))
        self.live = self.peak = 0
        self.lock = threading.Lock()

    def _stream(self, **params):
        with self.lock:
            self.live += 1
            self.peak = max(self.peak, self.live)
        threading.Event().wait(0.02)  # time.sleep is stubbed for the backoff
        try:
            return super()._stream(**params)
        finally:
            with self.lock:
                self.live -= 1


def _run(tmp: Path, job_dir: Path, firm: Path, data_root: Path, workers: int, mp: pytest.MonkeyPatch, client=None):
    mp.setattr(compose_pool, "COMPOSE_WORKERS", workers)
    mp.setattr(compose_pool.time, "sleep", lambda *_: None)
    client = client or Slow()
    sr = _sr(job_dir, firm, data_root, client)
    _unit_files(sr, FILES)
    rc = compose_stage.run(sr)
    d = sr.slug_dir / "runs" / "alpha"
    out = tmp / f"n{workers}"
    shutil.copytree(d, out)
    shutil.rmtree(sr.slug_dir)
    return rc, client, out


def _same(a: Path, b: Path) -> None:
    names = sorted(p.name for p in a.iterdir() if p.name not in ("usage.jsonl", "usage-ledger.jsonl"))
    assert names == sorted(p.name for p in b.iterdir() if p.name not in ("usage.jsonl", "usage-ledger.jsonl"))
    for n in names:
        assert (a / n).read_bytes() == (b / n).read_bytes(), n
    rows = [sorted((a / "usage.jsonl").read_text().splitlines()), sorted((b / "usage.jsonl").read_text().splitlines())]
    assert rows[0] == rows[1], "the usage rows differ beyond their order"


def test_one_worker_asks_in_the_serial_order(
    job_dir: Path, firm_config_path: Path, data_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rc, client, d = _run(tmp_path, job_dir, firm_config_path, data_root, 1, monkeypatch)
    assert rc == 0 and client.peak == 1
    asked = [c["messages"][0]["content"] for c in client.calls]
    n = len(list(d.glob("chunk-??.txt")))
    want: list[str] = []
    for i in range(1, n + 1):
        whole = (d / f"chunk-{i:02d}.txt").read_text()
        if "declined page text" in whole:
            want += [whole] * 4  # three composition attempts, then the judgment tier
        elif "bulky record" in whole:
            want += [whole] + [(d / f"chunk-{i:02d}-{k}.txt").read_text() for k in (1, 2)]
        else:
            want.append(whole)
    assert asked == want


def test_eight_workers_write_what_one_wrote(
    job_dir: Path, firm_config_path: Path, data_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rc1, c1, one = _run(tmp_path, job_dir, firm_config_path, data_root, 1, monkeypatch)
    rc8, c8, eight = _run(tmp_path, job_dir, firm_config_path, data_root, 8, monkeypatch)
    assert rc1 == rc8 == 0
    assert c8.peak > 1, "the chunks were composed one after another"
    assert sorted(json.dumps(c, sort_keys=True) for c in c1.calls) == sorted(
        json.dumps(c, sort_keys=True) for c in c8.calls
    ), "the requests changed"
    assert (eight / "unread_pages.json").is_file() and (eight / "chunk-04.parts").is_file()
    _same(one, eight)


def _killed_reply(state: dict):
    def reply(p, n):
        text = p["messages"][0]["content"]
        if n > 1 and "delta record" in text and not state.get("killed"):
            state["killed"] = True
            raise SystemExit("runner killed")
        return _reply(text)

    return reply


def test_a_kill_mid_fan_out_resumes_only_the_pieces_with_no_map(
    job_dir: Path, firm_config_path: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(compose_pool.time, "sleep", lambda *_: None)
    sr = _sr(job_dir, firm_config_path, data_root, Slow(_killed_reply({})))
    _unit_files(sr, FILES)
    d = sr.slug_dir / "runs" / "alpha"
    with pytest.raises(SystemExit):
        compose_stage.run(sr)
    queued = compose_stage.read_queue(d)
    assert queued, "the queue names the open pieces"
    for r in queued:
        name = f"chunk-{r['i']:02d}-{r['part']}.txt" if r["part"] else f"chunk-{r['i']:02d}.txt"
        stem = name[len("chunk-") : -len(".txt")]
        assert not (d / f"map-{stem}.md").exists(), f"{stem} has a map yet is queued"
    unmapped = {i for i in range(1, len(list(d.glob("chunk-??.txt"))) + 1) if not compose_stage.produced(d, i)}
    assert unmapped == {r["i"] for r in queued}
    answered = {(d / f"chunk-{p.name[4:-3]}.txt").read_text() for p in d.glob("map-*.md")}

    client = Slow()
    assert compose_stage.run(_sr(job_dir, firm_config_path, data_root, client)) == 0
    asked = {c["messages"][0]["content"] for c in client.calls}
    for r in queued:
        name = f"chunk-{r['i']:02d}-{r['part']}.txt" if r["part"] else f"chunk-{r['i']:02d}.txt"
        assert (d / name).read_text() in asked, f"{name} was open and not asked again"
    assert not asked & answered, "a piece with a map was paid for twice"


def test_a_limit_stops_every_worker_and_leaves_attempts_unchanged(
    job_dir: Path, firm_config_path: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(compose_pool.time, "sleep", lambda *_: None)
    seen = {"n": 0, "after": 0}
    lock = threading.Lock()

    def hook(stage: str) -> None:
        with lock:
            seen["n"] += 1
            if seen["n"] >= 3:
                seen["after"] += 1
                raise LimitHold("cap_usd", "cap_usd: reached")

    client = Slow()
    sr = _sr(job_dir, firm_config_path, data_root, client)
    sr.before_request = hook
    _unit_files(sr, FILES)
    d = sr.slug_dir / "runs" / "alpha"
    with pytest.raises(LimitHold):
        compose_stage.run(sr)
    queued = compose_stage.read_queue(d)
    assert queued and all(r["attempts"] == 0 for r in queued), "a held piece kept its attempt count"
    assert len(client.calls) == 2, "no call reached the model after the hold"
    assert seen["after"] <= compose_pool.COMPOSE_WORKERS, "workers kept asking after the halt"
    assert {r["i"] for r in queued} == {
        i for i in range(1, len(list(d.glob("chunk-??.txt"))) + 1) if not compose_stage.produced(d, i)
    }


def test_a_machine_stop_starts_no_new_piece_and_keeps_the_queue(
    job_dir: Path, firm_config_path: Path, data_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jobs import stop_request

    monkeypatch.setattr(compose_pool.time, "sleep", lambda *_: None)
    flag = tmp_path / stop_request.NAME
    monkeypatch.setattr(stop_request._Armed, "path", flag)

    asked: list[str] = []

    def hook(stage: str) -> None:
        # The stop lands before the fan-out's first call is sent (call 1 is
        # the warm-up chunk), so each worker starts at most the one piece whose
        # stop check it had already passed.
        asked.append(stage)
        if len(asked) == 2:
            flag.write_text("stop")

    client = Slow()
    sr = _sr(job_dir, firm_config_path, data_root, client)
    sr.before_request = hook
    _unit_files(sr, FILES)
    d = sr.slug_dir / "runs" / "alpha"
    with pytest.raises(stop_request.StopRequested):
        compose_stage.run(sr)
    assert len(client.calls) <= 1 + compose_pool.COMPOSE_WORKERS, "pieces kept starting after the stop"
    queued = compose_stage.read_queue(d)
    assert {r["i"] for r in queued} == {
        i for i in range(1, len(list(d.glob("chunk-??.txt"))) + 1) if not compose_stage.produced(d, i)
    }
    tries: dict[str, int] = {}
    for row in compose_stage.read_usage(d):
        tries[str(row["chunk"])] = tries.get(str(row["chunk"]), 0) + 1
    for r in queued:
        label = f"{r['i']}.{r['part']}" if r["part"] else str(r["i"])
        assert r["attempts"] == tries.get(label, 0), f"{label}: attempts changed without a recorded call"


def test_the_replay_harness_reproduces_a_saved_run_at_eight_workers(
    job_dir: Path, firm_config_path: Path, data_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import replay_writer

    monkeypatch.setattr(compose_pool, "COMPOSE_WORKERS", 1)
    monkeypatch.setattr(compose_pool.time, "sleep", lambda *_: None)
    sr = _sr(job_dir, firm_config_path, data_root, Slow())
    _unit_files(sr, FILES)
    assert compose_stage.run(sr) == 0
    out = tmp_path / "replay"
    args = ["--slug-dir", str(sr.slug_dir), "--unit", "alpha", "--job-dir", str(job_dir)]
    assert replay_writer.main([*args, "--firm", str(firm_config_path), "--workers", "8", "--out", str(out)]) == 0
    _same(sr.slug_dir / "runs" / "alpha", out / "runs" / "alpha")
