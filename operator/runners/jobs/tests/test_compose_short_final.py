"""A short compose reply that accounts for every file is final (2026-11-17).

WHY. A whole 198K-character chunk drew a correct "none in this chunk" reply,
short but in the house shape. The runner logged it EMPTY, split the chunk and
paid for both halves, which gave the same answer: half the job's spend repeated
a correct "nothing". Only a part (already split) could be short. Now a whole
chunk's short reply is final when it carries every block heading AND its
FILES-SEEN names every file the chunk holds; one that leaves a file out still
splits, so a reader that skipped a file is still caught. Every name is synthetic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jobs.stages import chunking, compose as compose_stage
from test_compose_stages import Scripted, _msg, _sr, _unit_files, cut_before_windows, firm_headings  # noqa: F401 - firm_headings is a pytest fixture the tests request by name

BODY = "".join(f"[p.{k}] " + "inspection text " * 40 + "\n" for k in range(1, 30))


def _short(prompt: dict, keep: int | None = None) -> str:
    """A complete "nothing here" reply naming the first `keep` files of the
    piece it was sent (all when None), in FILES-SEEN."""
    text = prompt["messages"][0]["content"]
    heads = [p["header"] for p in chunking.pieces_pages(text)][:keep]
    seen = "\n".join(f"{h} | nothing extractable: a property inspection, no medical encounter" for h in heads)
    return (
        "## ENTRIES\nnone in this chunk\n\n## INDEX\n\n## BILLING-DATES\nnone in this chunk\n\n"
        f"## CONFLICTS / REFERENCED-BUT-ABSENT\nnone observed\n\n## FILES-SEEN\n{seen}\n"
    )


def test_a_whole_chunks_short_reply_naming_every_file_is_kept_and_paid_once(
    job_dir: Path, firm_headings: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = Scripted(lambda p, n: _msg(_short(p)))
    log: list[str] = []
    sr = _sr(job_dir, firm_headings, data_root, client, log=log)
    _unit_files(sr, {"inspection report": BODY, "survey report": BODY})
    cut_before_windows(sr)  # a whole chunk of two files (a unit cut before the writer's windows)
    monkeypatch.setattr(compose_stage.time, "sleep", lambda *_: None)
    assert compose_stage.run(sr) == 0
    d = sr.slug_dir / "runs" / "alpha"
    assert len(client.calls) == 1  # never split, never paid twice
    assert (d / "map-01.md").is_file() and not list(d.glob("map-01-*.md"))
    assert any("short" in m and "but complete; kept" in m for m in log)
    assert not any(r.get("empty") for r in compose_stage.read_usage(d))


def test_falsifier_a_whole_chunks_short_reply_that_skips_a_file_still_splits(
    job_dir: Path, firm_headings: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = Scripted(lambda p, n: _msg(_short(p, keep=1 if n == 1 else None)))
    log: list[str] = []
    sr = _sr(job_dir, firm_headings, data_root, client, log=log)
    _unit_files(sr, {"inspection report": BODY, "survey report": BODY})
    cut_before_windows(sr)
    monkeypatch.setattr(compose_stage.time, "sleep", lambda *_: None)
    assert compose_stage.run(sr) == 0
    d = sr.slug_dir / "runs" / "alpha"
    assert len(client.calls) == 3 and not (d / "map-01.md").exists()
    assert (d / "map-01-1.md").is_file() and (d / "map-01-2.md").is_file()
    assert any("EMPTY" in m and "leaves 1 file(s) unaccounted for" in m for m in log)


def test_a_window_whose_short_reply_names_its_file_is_kept_and_paid_once(
    job_dir: Path, firm_headings: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same rule on a writer window (``medchron/windows``): one file, one
    request; a short reply naming that file is its answer, and one naming no
    file still splits."""
    window = "".join(f"[p.{k}] " + "inspection text " * 120 + "\n" for k in range(1, 9))
    client = Scripted(lambda p, n: _msg(_short(p)))
    sr = _sr(job_dir, firm_headings, data_root, client)
    _unit_files(sr, {"inspection report": window})
    monkeypatch.setattr(compose_stage.time, "sleep", lambda *_: None)
    assert compose_stage.run(sr) == 0 and len(client.calls) == 1
    client2 = Scripted(lambda p, n: _msg(_short(p, keep=0 if n == 1 else None)))
    sr2 = _sr(job_dir, firm_headings, data_root, client2)
    for f in (sr.slug_dir / "runs" / "alpha").glob("*"):
        f.unlink()
    assert compose_stage.run(sr2) == 0 and len(client2.calls) == 3


def test_files_seen_is_read_by_id_or_by_name() -> None:
    chunk = "=== FILE: a.pdf (fileId f-1, 2 pages) ===\n[p.1] x\n=== FILE: b.pdf (fileId f2) ===\n[p.1] y\n"
    by_id = "## FILES-SEEN\n=== FILE: renamed.pdf (fileId f-1) === | entries: 0, billing-dates: 3\n"
    assert compose_stage.unaccounted(chunk, by_id) == ["b.pdf"]
    both = by_id + "=== FILE: b.pdf === nothing extractable: a cover letter\n"
    assert compose_stage.unaccounted(chunk, both) == []
