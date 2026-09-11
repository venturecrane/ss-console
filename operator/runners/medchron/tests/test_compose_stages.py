"""map (compose), repair_truncated, assemble and merge in-process, against a
scripted SDK client. The synthetic map output below is in the house format
the prompt asks for, so assemble and the merge falsifier read it for real."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import yaml

from medchron import config as config_mod, job as job_mod, prompts
from medchron.stages import assemble as assemble_stage, chunking, compose as compose_stage, merge as merge_stage
from medchron.stages import merge_falsify as mf, repair as repair_stage
from medchron.stages.base import StageRun
from medchron_testkit import FIRM_CONFIG, FakeSeat

HEADINGS = [
    "Patient Complaints & Limitations",
    "HPI & Prior Medical History",
    "Medical Diagnoses",
    "Treatment Recommendations",
    "All Other Information",
]


class Usage:
    def __init__(self, i=100, o=50):
        self.input_tokens, self.output_tokens = i, o
        self.cache_read_input_tokens = self.cache_creation_input_tokens = 0


def _msg(text: str, stop: str = "end_turn") -> NS:
    return NS(content=[NS(type="text", text=text)], stop_reason=stop, usage=Usage())


class Stream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


class Scripted:
    def __init__(self, reply):
        self.calls: list[dict] = []
        self._reply = reply
        self.messages = NS(create=self._create, stream=self._stream, batches=None)

    def _create(self, **params):
        self.calls.append(params)
        return self._reply(params, len(self.calls))

    def _stream(self, **params):
        self.calls.append(params)
        return Stream(self._reply(params, len(self.calls)))


@pytest.fixture
def firm_headings(tmp_path: Path) -> Path:
    cfg = json.loads(json.dumps(FIRM_CONFIG))
    cfg["format"]["subsections"] = HEADINGS
    p = tmp_path / "firm-headings.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return p


def _sr(job_dir: Path, firm: Path, data_root: Path, client=None, log: list[str] | None = None) -> StageRun:
    job = job_mod.load(job_dir)
    cfg = config_mod.load(str(firm))
    lines = log if log is not None else []
    return StageRun(
        job=job,
        cfg=cfg,
        unit=job.units[0],
        slug_dir=data_root / "example-matter",
        decided={},
        log=lines.append,
        seat_factory=lambda: FakeSeat([], [], {}),
        client_factory=lambda: client,
    )


def _unit_files(sr: StageRun, texts: dict[str, str]) -> None:
    (sr.slug_dir / "text").mkdir(parents=True, exist_ok=True)
    (sr.slug_dir / "units").mkdir(parents=True, exist_ok=True)
    rows = []
    for name, text in texts.items():
        fid = name.replace(" ", "_")
        tp = sr.slug_dir / "text" / f"{fid}.txt"
        tp.write_text(text)
        rows.append(
            {"id": fid, "name": name, "ext": ".pdf", "folder": "/MEDICAL", "text_path": str(tp), "chars": len(text)}
        )
    (sr.slug_dir / "units" / "alpha.json").write_text(json.dumps(rows))


MAP_OUT = """## ENTRIES
01/02/2026
Example Clinic | Patient Complaints & Limitations

The patient reports neck pain rated 6 of 10 since the Subject Incident. (FILE: clinic note.pdf, p. 1)

Medical Diagnoses

Cervical strain. (FILE: clinic note.pdf, p. 2)

01/09/2026
Example Imaging | Medical Diagnoses

MRI of the cervical spine shows a disc bulge at C5-6. (FILE: mri report.pdf, p. 1, p. 3)

## INDEX
2026-01-02 | Example Clinic | S13.4 | clinic note.pdf
2026-01-09 | Example Imaging | -- | mri report.pdf

## BILLING-DATES
none in this chunk

## CONFLICTS / REFERENCED-BUT-ABSENT
none observed

## FILES-SEEN
=== FILE: clinic note.pdf (fileId clinic_note) === entries: 1
=== FILE: mri report.pdf (fileId mri_report) === entries: 1
"""


# ---- chunking ------------------------------------------------------------------
def test_chunk_size_derives_from_the_window_and_build_chunks_splits_between_files(tmp_path: Path) -> None:
    assert chunking.chunk_size(128000) == 240_000
    assert chunking.chunk_size(32000) == int(32000 * 4 * 0.8 / 0.85)
    files = []
    for i in range(3):
        p = tmp_path / f"f{i}.txt"
        p.write_text("[p.1]\n" + "x" * 500)
        files.append({"id": f"f{i}", "name": f"f{i}", "ext": ".pdf", "text_path": str(p)})
    chunks = chunking.build_chunks(files, chunk=1200)
    assert len(chunks) == 2 and chunks[0].count("=== FILE:") == 2 and chunks[1].count("=== FILE:") == 1
    big = tmp_path / "big.txt"
    big.write_text("".join(f"[p.{n}]\n" + "y" * 400 + "\n" for n in range(1, 8)))
    parts = chunking.build_chunks([{"id": "b", "name": "big", "ext": ".pdf", "text_path": str(big)}], chunk=1000)
    assert all("[part " in c for c in parts) and len(parts) >= 3


def test_split_chunk_carries_the_governing_header_into_a_mid_file_part() -> None:
    text = "=== FILE: a.pdf (fileId a) ===\n" + "".join(f"[p.{n}] text\n" for n in range(1, 21))
    parts = chunking.split_chunk(text, 2)
    assert len(parts) == 2 and parts[1].startswith("=== FILE: a.pdf (fileId a) [continued] ===")


# ---- compose ---------------------------------------------------------------------
def test_compose_streams_resumes_by_hash_and_records_usage(job_dir: Path, firm_headings: Path, data_root: Path) -> None:
    client = Scripted(lambda p, n: _msg(MAP_OUT))
    sr = _sr(job_dir, firm_headings, data_root, client)
    _unit_files(sr, {"clinic note": "[p.1] neck pain\n[p.2] strain", "mri report": "[p.1] MRI\n[p.3] bulge"})
    assert compose_stage.run(sr) == 0
    d = sr.slug_dir / "runs" / "alpha"
    assert (d / "map-01.md").read_text() == MAP_OUT and (d / "chunk-01.sha").is_file()
    rows = compose_stage.read_usage(d)
    assert rows[-1]["chunk"] == 1 and rows[-1]["stop"] == "end_turn" and rows[-1]["max_tokens"] == 128000
    assert "stream" in str(client.messages.stream) and client.calls[0]["max_tokens"] == 128000
    assert "Patient Complaints & Limitations / HPI" in client.calls[0]["system"][0]["text"]
    # the same input is not recomposed; a changed input is
    client.calls.clear()
    assert compose_stage.run(sr) == 0 and client.calls == []
    _unit_files(sr, {"clinic note": "[p.1] neck pain CHANGED\n[p.2] strain", "mri report": "[p.1] MRI\n[p.3] bulge"})
    assert compose_stage.run(sr) == 0 and len(client.calls) == 1


def test_compose_retries_a_refusal_then_gives_up_and_exits_1(
    job_dir: Path, firm_headings: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = Scripted(lambda p, n: _msg("nope", stop="refusal"))
    log: list[str] = []
    sr = _sr(job_dir, firm_headings, data_root, client, log=log)
    _unit_files(sr, {"clinic note": "[p.1] text"})
    monkeypatch.setattr(compose_stage.time, "sleep", lambda *_: None)
    assert compose_stage.run(sr) == 1
    assert len(client.calls) == 3
    assert (sr.slug_dir / "runs" / "alpha" / "map-01.md").read_text() == "## REFUSED\n"
    assert any("REFUSED after 3 attempts" in line for line in log)


def test_compose_splits_an_emptied_chunk_once(
    job_dir: Path, firm_headings: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reply(p, n):
        return _msg("x") if n == 1 else _msg(MAP_OUT)  # 1 byte for the whole chunk, then fine per half

    client = Scripted(reply)
    sr = _sr(job_dir, firm_headings, data_root, client)
    body = "".join(f"[p.{k}] " + "record text " * 40 + "\n" for k in range(1, 30))
    _unit_files(sr, {"clinic note": body, "mri report": body})
    monkeypatch.setattr(compose_stage.time, "sleep", lambda *_: None)
    assert compose_stage.run(sr) == 0
    d = sr.slug_dir / "runs" / "alpha"
    assert (d / "map-01-1.md").is_file() and (d / "map-01-2.md").is_file() and not (d / "map-01.md").exists()
    assert len(client.calls) == 3
    assert any(r.get("empty") for r in compose_stage.read_usage(d))


# ---- repair ----------------------------------------------------------------------
def test_repair_rewrites_a_truncated_chunk_as_parts_and_sets_the_original_aside(
    job_dir: Path, firm_headings: Path, data_root: Path
) -> None:
    client = Scripted(lambda p, n: _msg(MAP_OUT))
    sr = _sr(job_dir, firm_headings, data_root, client)
    d = sr.slug_dir / "runs" / "alpha"
    d.mkdir(parents=True)
    text = "=== FILE: a.pdf (fileId a) ===\n" + "".join(f"[p.{n}] text\n" for n in range(1, 21))
    (d / "chunk-01.txt").write_text(text)
    (d / "map-01.md").write_text(MAP_OUT[:200])
    (d / "usage.jsonl").write_text(json.dumps({"chunk": 1, "stop": "max_tokens", "in": 1, "out": 1}) + "\n")
    assert repair_stage.run(sr) == 0
    assert (d / "map-01.md.truncated").is_file() and (d / "map-01-1.md").is_file() and (d / "map-01-2.md").is_file()
    assert len(client.calls) == 2
    # the safety net is $0 when nothing is truncated
    client.calls.clear()
    assert repair_stage.run(sr) == 0 and client.calls == []


def test_repair_escalates_the_split_when_a_part_is_emptied(job_dir: Path, firm_headings: Path, data_root: Path) -> None:
    def reply(p, n):
        return _msg("x") if n <= 2 else _msg(MAP_OUT)  # both halves empty, thirds fine

    client = Scripted(reply)
    sr = _sr(job_dir, firm_headings, data_root, client)
    d = sr.slug_dir / "runs" / "alpha"
    d.mkdir(parents=True)
    text = "".join(f"=== FILE: f{k}.pdf (fileId f{k}) ===\n[p.1] " + "text " * 200 + "\n" for k in range(6))
    (d / "chunk-02.txt").write_text(text)
    (d / "map-02.md").write_text("partial")
    (d / "usage.jsonl").write_text(json.dumps({"chunk": 2, "stop": "max_tokens"}) + "\n")
    assert repair_stage.run(sr) == 0
    assert len(client.calls) == 5 and (d / "map-02-3.md").is_file()


# ---- assemble --------------------------------------------------------------------
def _seed_map(sr: StageRun, text: str = MAP_OUT, name: str = "map-01.md", usage: list | None = None) -> Path:
    d = sr.slug_dir / "runs" / "alpha"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text)
    if usage is not None:
        (d / "usage.jsonl").write_text("".join(json.dumps(u) + "\n" for u in usage))
    (sr.slug_dir / "units").mkdir(exist_ok=True)
    (sr.slug_dir / "units" / "alpha.json").write_text(
        json.dumps(
            [{"id": "a", "name": "clinic note", "ext": ".pdf"}, {"id": "b", "name": "mri report", "ext": ".pdf"}]
        )
    )
    return d


def test_assemble_numbers_exhibits_substitutes_citations_and_keeps_both_page_groups(
    job_dir: Path, firm_headings: Path, data_root: Path
) -> None:
    sr = _sr(job_dir, firm_headings, data_root)
    d = _seed_map(sr, usage=[{"chunk": 1, "stop": "end_turn"}])
    assert assemble_stage.run(sr) == 0
    assert json.loads((d / "exhibit_map.json").read_text()) == {"clinic note.pdf": 1, "mri report.pdf": 2}
    entries = (d / "entries.md").read_text()
    assert "(Exhibit 1 - p. 1)" in entries and "(Exhibit 1 - p. 2)" in entries
    assert "(Exhibit 2 - p. 1, 3)" in entries  # the second page group is not swallowed
    assert (d / "clusters.md").read_text() == "" and "none observed" not in (d / "conflicts.md").read_text()


def test_assemble_clusters_same_date_same_provider_and_refuses_over_truncation(
    job_dir: Path, firm_headings: Path, data_root: Path
) -> None:
    sr = _sr(job_dir, firm_headings, data_root)
    second = MAP_OUT.replace("neck pain rated 6 of 10", "headache")
    d = _seed_map(sr, usage=[{"chunk": 1, "stop": "end_turn"}, {"chunk": 2, "stop": "end_turn"}])
    (d / "map-02.md").write_text(second)
    assert assemble_stage.run(sr) == 0
    clusters = (d / "clusters.md").read_text()
    assert clusters.count("##### CLUSTER") == 2 and "---FRAGMENT-BREAK---" in clusters
    (d / "usage.jsonl").write_text(json.dumps({"chunk": 2, "stop": "max_tokens"}) + "\n")
    assert assemble_stage.run(sr) == 1
    (d / "usage.jsonl").write_text(json.dumps({"chunk": 2, "stop": "end_turn"}) + "\n")
    (d / "map-02.md").write_text("## REFUSED\n")
    assert assemble_stage.run(sr) == 1


# ---- merge -----------------------------------------------------------------------
CLUSTER = """##### CLUSTER 2026-01-02 | exampleclinic (2 fragments)
01/02/2026
Example Clinic | Patient Complaints & Limitations

The patient reports neck pain rated 6 of 10. (Exhibit 1 - p. 1)

Medical Diagnoses

Cervical strain. (Exhibit 1 - p. 2)
---FRAGMENT-BREAK---
01/02/2026
Example Clinic | Medical Diagnoses

Cervical strain. (Exhibit 1 - p. 2)

Treatment Recommendations

Physical therapy twice weekly. (Exhibit 1 - p. 2)

"""


def test_merge_in_code_unions_and_orders_and_the_falsifier_passes(
    job_dir: Path, firm_headings: Path, data_root: Path
) -> None:
    sr = _sr(
        job_dir,
        firm_headings,
        data_root,
        client=Scripted(lambda p, n: (_ for _ in ()).throw(AssertionError("no model"))),
    )
    d = sr.slug_dir / "runs" / "alpha"
    d.mkdir(parents=True)
    (d / "clusters.md").write_text(CLUSTER)
    assert merge_stage.run(sr) == 0
    merged = (d / "merged.md").read_text()
    assert merged.startswith("01/02/2026\nExample Clinic | Patient Complaints & Limitations\n")
    assert merged.index("Medical Diagnoses") < merged.index("Treatment Recommendations")
    assert merged.count("Cervical strain.") == 1 and "Physical therapy twice weekly." in merged
    route = json.loads((d / "merge_route.json").read_text())
    assert route["code"] == [1] and route["routed"] == []


def test_merge_routes_a_disagreement_to_the_model_and_falsifies_its_answer(
    job_dir: Path, firm_headings: Path, data_root: Path
) -> None:
    # fragment 2 restates the complaint under the SAME subsection with a different number
    disagree = CLUSTER.replace(
        "Example Clinic | Medical Diagnoses\n\nCervical strain. (Exhibit 1 - p. 2)\n\nTreatment Recommendations\n\n"
        "Physical therapy twice weekly. (Exhibit 1 - p. 2)",
        "Example Clinic | Patient Complaints & Limitations\n\n"
        "The patient reports neck pain rated 8 of 10. (Exhibit 1 - p. 3)\n\nMedical Diagnoses\n\n"
        "Cervical strain. (Exhibit 1 - p. 2)",
    )
    assert disagree != CLUSTER
    good = (
        "01/02/2026\nExample Clinic | Patient Complaints & Limitations\n\n"
        "The patient reports neck pain rated 6 of 10. (Exhibit 1 - p. 1)\n\n"
        "The patient reports neck pain rated 8 of 10. The records differ on this point. (Exhibit 1 - p. 3)\n\n"
        "Medical Diagnoses\n\nCervical strain. (Exhibit 1 - p. 2)\n"
    )
    client = Scripted(lambda p, n: _msg(good))
    log: list[str] = []
    sr = _sr(job_dir, firm_headings, data_root, client, log=log)
    d = sr.slug_dir / "runs" / "alpha"
    d.mkdir(parents=True)
    (d / "clusters.md").write_text(disagree)
    assert merge_stage.run(sr) == 0
    assert len(client.calls) == 1 and any("near-duplicate" in line for line in log)
    assert "The records differ on this point." in (d / "merged.md").read_text()
    # a model answer that drops a citation is falsified and split until it cannot be: exit 1
    bad = good.replace(" (Exhibit 1 - p. 3)", "")
    client2 = Scripted(lambda p, n: _msg(bad))
    sr2 = _sr(job_dir, firm_headings, data_root, client2)
    (d / "clusters.md").write_text(disagree)
    assert merge_stage.run(sr2) == 1


def test_falsifier_exit_codes(firm_headings: Path) -> None:
    hd = mf.Headings.from_config(config_mod.load(str(firm_headings)))
    merged_ok = (
        "01/02/2026\nExample Clinic | Patient Complaints & Limitations\n\n"
        "The patient reports neck pain rated 6 of 10. (Exhibit 1 - p. 1)\n\nMedical Diagnoses\n\n"
        "Cervical strain. (Exhibit 1 - p. 2)\n\nTreatment Recommendations\n\n"
        "Physical therapy twice weekly. (Exhibit 1 - p. 2)\n"
    )
    assert mf.check(CLUSTER, merged_ok, hd)[0] == 0
    assert mf.check(CLUSTER, merged_ok.replace("(Exhibit 1 - p. 1)", "(Exhibit 1 - p. 9)"), hd)[0] == 3
    assert mf.check(CLUSTER, merged_ok.replace("Physical therapy twice weekly. (Exhibit 1 - p. 2)\n", ""), hd)[0] == 4
    two_entries = merged_ok + "\n\n" + merged_ok.replace("01/02/2026", "01/03/2026")
    assert mf.check(CLUSTER, two_entries, hd)[0] == 5


def test_prompt_menu_comes_from_the_firm_config(firm_headings: Path) -> None:
    cfg = config_mod.load(str(firm_headings))
    assert " / ".join(HEADINGS) in prompts.load("map-system", cfg)
    assert "{{" not in prompts.load("merge-system", cfg)
