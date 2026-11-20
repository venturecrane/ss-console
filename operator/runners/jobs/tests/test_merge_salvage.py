"""A routed cluster the model cannot merge is merged in code ALONE: every other
cluster keeps the model's entry, a cluster too long for one reply is never
sent, and the falsifier passes over the mixed result with every citation in.
Through the real merge stage and doorway (2026-11-19: the saved 3,821-page
run discarded 23 clusters' accepted model results on one failed batch). All
names and text are invented."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from jobs.stages import merge as merge_stage, merge_falsify as mf, merge_model as mm
from test_compose_stages import Scripted, _msg, _sr, firm_headings  # noqa: F401 - firm_headings is a fixture

CITE = re.compile(r"\(Exhibit \d+ - p\. \d+\)")


def _cluster(day: int, filler: int = 0) -> tuple[str, str]:
    """A cluster code routes (same subsection, different numbers), and the
    model's correct answer for it, marking the disagreement."""
    date = f"01/{day:02d}/2026"
    head = f"{date}\nExample Clinic | Patient Complaints & Limitations\n\n"
    a = f"The patient reports neck pain rated 6 of 10 on visit {day}. (Exhibit 1 - p. {day})"
    b = f"The patient reports neck pain rated 8 of 10 on visit {day}. (Exhibit 1 - p. {day + 40})"
    long = " ".join(f"word{j}" for j in range(40))
    pad = "".join(f"\n\nHistory line {k}: {long}. (Exhibit 2 - p. {k})" for k in range(1, filler + 1))
    src = (
        f"##### CLUSTER 2026-01-{day:02d} | exampleclinic (2 fragments)\n"
        f"{head}{a}{pad}\n---FRAGMENT-BREAK---\n{head}{b}\n\n"
    )
    good = f"{head}{a}{pad}\n\n{b.replace('. (Exhibit', '. The records differ on this point. (Exhibit')}\n"
    return src, good


def _answer(bad_day: int | None):
    def reply(p, n):
        text = p["messages"][0]["content"]
        out = []
        for block in [b for b in mf.CLUSTER_SPLIT.split(text) if b.strip()]:
            day = int(re.search(r"CLUSTER 2026-01-(\d\d)", block).group(1))
            _, good = _cluster(day)
            if day == bad_day:
                good = re.sub(r" \(Exhibit 1 - p\. \d+\)\n$", "\n", good)  # drops a citation
            out.append(good)
        return _msg("\n\n".join(out))

    return reply


def _seed(sr, days: list[int], big: dict[int, int] | None = None) -> Path:
    d = sr.slug_dir / "runs" / "alpha"
    d.mkdir(parents=True)
    (d / "clusters.md").write_text("".join(_cluster(k, (big or {}).get(k, 0))[0] for k in days))
    return d


def test_a_falsified_cluster_is_merged_in_code_and_the_others_keep_the_model_text(
    job_dir: Path, firm_headings: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mm.time, "sleep", lambda *_: None)
    log: list[str] = []
    sr = _sr(job_dir, firm_headings, data_root, Scripted(_answer(bad_day=3)), log=log)
    d = _seed(sr, [1, 2, 3, 4])
    src = (d / "clusters.md").read_text()
    assert merge_stage.run(sr) == 0, "the falsifier passes over the mixed result"
    merged = (d / "merged.md").read_text()
    entries = mf.parse_entries(merged)
    assert [e["date_line"] for e in entries] == [f"01/0{k}/2026" for k in (1, 2, 3, 4)]
    by_day = {int(e["date_line"][3:5]): e["body"] for e in entries}
    for k in (1, 2, 4):
        assert "The records differ on this point." in by_day[k], f"cluster {k} lost its model entry"
    assert "The records differ on this point." not in by_day[3], "the falsified cluster is merged in code"
    assert sorted(CITE.findall(merged)) == sorted(CITE.findall(src)), "citations in equal citations out"
    assert any("salvaging 1 of 4" in line for line in log)


def test_a_cluster_too_long_for_one_reply_is_never_sent(
    job_dir: Path, firm_headings: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mm.time, "sleep", lambda *_: None)
    client = Scripted(_answer(bad_day=None))
    sr = _sr(job_dir, firm_headings, data_root, client)
    d = _seed(sr, [1, 2], big={2: 250})
    src = (d / "clusters.md").read_text()
    big = [b for b in mf.CLUSTER_SPLIT.split(src) if "2026-01-02" in b][0]
    assert len(big) > mm.SINGLE_MAX_CHARS
    assert merge_stage.run(sr) == 0
    sent = "".join(c["messages"][0]["content"] for c in client.calls)
    assert "2026-01-01" in sent and "2026-01-02" not in sent
    merged = (d / "merged.md").read_text()
    assert sorted(CITE.findall(merged)) == sorted(CITE.findall(src))
    route = json.loads((d / "merge_route.json").read_text())
    assert len(route["routed"]) == 2


def test_batches_are_sized_so_the_first_call_need_not_truncate() -> None:
    assert mm.BATCH_CHARS == 60_000 and mm.BATCH_CHARS < mm.SINGLE_MAX_CHARS
    blocks = ["x" * 25_000] * 5
    batches, oversize = mm._batches(blocks)
    assert oversize == [] and [len(b) for b in batches] == [2, 2, 1]
