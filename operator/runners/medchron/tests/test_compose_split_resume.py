"""A split chunk is composed only when EVERY part it recorded has a map, and a
rerun resumes the open parts exactly where they stopped (2026-10-27 review,
MS-1).

Before this, `produced` counted a split chunk as composed when the part maps
that existed were non-trivial. A part whose batch was still processing (the
stage exits infra and a rerun is the remedy), or whose turn never came because
the runner was killed between parts, left the chunk looking whole: the rerun
skipped it forever, the claimed batch was never collected, and that part's
pages were never composed. A file spanning both parts passed coverage, so the
loss was not disclosed."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import yaml

from medchron import llm
from medchron.stages import compose as compose_stage
from medchron_testkit import FIRM_CONFIG
from test_compose_stages import MAP_OUT, Scripted, _msg, _sr, _unit_files

BODY = "".join(f"[p.{k}] " + "record text " * 40 + "\n" for k in range(1, 30))


def _batch_firm(tmp_path: Path) -> Path:
    cfg = json.loads(json.dumps(FIRM_CONFIG))
    cfg["levers"]["batch_stages"] = ["compose"]
    p = tmp_path / "firm-batch-compose.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return p


class Batches:
    """A scripted Batch API: the whole chunk comes back emptied (so it is split
    in two), each part composes cleanly; `ended` says which batches finished."""

    def __init__(self, ended: set[str]) -> None:
        self.created: list[list[dict]] = []
        self.ended = ended

    def create(self, requests):
        self.created.append(requests)
        return NS(id=f"b{len(self.created)}")

    def retrieve(self, bid):
        return NS(processing_status="ended" if bid in self.ended else "in_progress")

    def results(self, bid):
        out = []
        for r in self.created[int(bid[1:]) - 1]:
            cid = r["custom_id"]
            whole = cid.split("-")[2] not in ("1", "2")  # map-01-<sha>-a1 vs map-01-<k>-<sha>-a1
            out.append(NS(custom_id=cid, result=NS(type="succeeded", message=_msg("x" if whole else MAP_OUT))))
        return out


def test_a_part_whose_batch_is_still_processing_is_resumed_not_skipped(
    job_dir: Path, data_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # one request per batch, so part 1 and part 2 land in separate batches
    monkeypatch.setattr(llm, "_split_requests", lambda reqs, *a, **k: [[r] for r in reqs])
    b = Batches(ended={"b1", "b2"})  # b3 (part 2) is still processing
    log: list[str] = []
    sr = _sr(job_dir, _batch_firm(tmp_path), data_root, NS(messages=NS(create=None, batches=b)), log=log)
    sr.doorway.max_wait_s = 0
    sr.doorway.poll_s = 0
    _unit_files(sr, {"clinic note": BODY, "mri report": BODY})
    d = sr.slug_dir / "runs" / "alpha"

    assert compose_stage.run(sr) == compose_stage.INFRA_EXIT, "a part still processing is not a chunk composed"
    assert (d / "map-01-1.md").is_file() and not (d / "map-01-2.md").exists()
    assert not compose_stage.produced(d, 1)
    assert json.loads((d / "chunk-01.parts").read_text())["expected"] == [1, 2]
    part2_id = b.created[2][0]["custom_id"]
    assert len(b.created) == 3

    # the batch finishes; the rerun collects the claimed batch, submits nothing
    b.ended.add("b3")
    log.clear()
    sr2 = _sr(job_dir, _batch_firm(tmp_path), data_root, NS(messages=NS(create=None, batches=b)), log=log)
    sr2.doorway.max_wait_s = 0
    sr2.doorway.poll_s = 0
    assert compose_stage.run(sr2) == 0
    assert len(b.created) == 3, "the still-processing part is collected, never resubmitted and paid twice"
    assert any("resuming ['1.2']" in line for line in log)
    assert (d / "map-01-2.md").read_text() == MAP_OUT
    assert compose_stage.produced(d, 1) and not (d / compose_stage.QUEUE).exists()
    assert part2_id.startswith("map-01-2-")


def test_a_runner_killed_between_parts_composes_the_missing_part_on_rerun(
    job_dir: Path, data_root: Path, firm_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(compose_stage.time, "sleep", lambda *_: None)

    def reply(p, n):
        if n == 1:
            return _msg("x")  # the whole chunk empties: split in two
        if n == 2:
            return _msg(MAP_OUT)  # part 1 composes
        raise SystemExit("runner killed")  # before part 2 comes back

    sr = _sr(job_dir, firm_config_path, data_root, Scripted(reply))
    _unit_files(sr, {"clinic note": BODY, "mri report": BODY})
    d = sr.slug_dir / "runs" / "alpha"
    with pytest.raises(SystemExit):
        compose_stage.run(sr)
    assert (d / "map-01-1.md").is_file() and not compose_stage.produced(d, 1)

    client = Scripted(lambda p, n: _msg(MAP_OUT))
    sr2 = _sr(job_dir, firm_config_path, data_root, client)
    assert compose_stage.run(sr2) == 0
    assert len(client.calls) == 1, "only the missing part is composed; part 1 is not paid for twice"
    assert client.calls[0]["messages"][0]["content"] == (d / "chunk-01-2.txt").read_text()
    assert (d / "map-01-2.md").read_text() == MAP_OUT and compose_stage.produced(d, 1)


def test_produced_needs_a_map_for_every_recorded_part_and_honours_repair(tmp_path: Path) -> None:
    d = tmp_path
    big = "y" * 200
    (d / "map-03-1.md").write_text(big)
    assert not compose_stage.produced(d, 3), "part maps with no split record prove nothing"
    (d / "chunk-03.parts").write_text(json.dumps({"expected": [1, 2], "next": 3}))
    assert not compose_stage.produced(d, 3)
    (d / "map-03-2.md").write_text(big)
    assert compose_stage.produced(d, 3)
    # repair set part 2's truncated map aside and recorded parts 3 and 4
    (d / "map-03-2.md").rename(d / "map-03-2.md.truncated")
    (d / "repair-03-2.json").write_text("[3, 4]")
    (d / "map-03-3.md").write_text(big)
    assert not compose_stage.produced(d, 3)
    (d / "map-03-4.md").write_text(big)
    assert compose_stage.produced(d, 3)
    # a whole chunk repair split into parts
    (d / "map-05.md.truncated").write_text(big)
    (d / "repair-05.json").write_text("[1, 2]")
    (d / "map-05-1.md").write_text(big)
    assert not compose_stage.produced(d, 5)
    (d / "map-05-2.md").write_text(big)
    assert compose_stage.produced(d, 5)
