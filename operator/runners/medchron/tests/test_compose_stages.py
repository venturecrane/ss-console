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


def test_assemble_resolves_a_citation_carrying_a_file_id_to_that_file_not_its_namesake(
    job_dir: Path, firm_headings: Path, data_root: Path
) -> None:
    """Two email attachments named image001.jpg: the model cites the second as
    `image001.jpg [fileId msgatt-2]`. Name matching gave both one exhibit
    number and the second's citation landed on the first's page."""
    sr = _sr(job_dir, firm_headings, data_root)
    text = MAP_OUT.replace("(FILE: mri report.pdf, p. 1, p. 3)", "(FILE: image001.jpg [fileId msgatt-2], p. 1)")
    d = _seed_map(sr, text=text, usage=[{"chunk": 1, "stop": "end_turn"}])
    (sr.slug_dir / "units" / "alpha.json").write_text(
        json.dumps(
            [
                {"id": "a", "name": "clinic note", "ext": ".pdf"},
                {"id": "msgatt-1", "name": "image001", "ext": ".jpg"},
                {"id": "msgatt-2", "name": "image001 (2)", "ext": ".jpg"},
            ]
        )
    )
    assert assemble_stage.run(sr) == 0
    assert json.loads((d / "exhibit_map.json").read_text()) == {"clinic note.pdf": 1, "image001 (2).jpg": 2}
    assert "(Exhibit 2 - p. 1)" in (d / "entries.md").read_text()


def test_assemble_resolves_a_bare_citation_of_a_shared_name_to_the_copy_the_chunk_held(
    job_dir: Path, firm_headings: Path, data_root: Path
) -> None:
    """Two copies of a 51-page record with one name, each composed in its own
    chunk (live 2026-09-15: 149 + 152 bare citations). The chunk's own
    `=== FILE:` header says which copy it held; the model never saw the other."""
    sr = _sr(job_dir, firm_headings, data_root)
    d = _seed_map(sr, usage=[{"chunk": 1, "stop": "end_turn"}])
    (sr.slug_dir / "units" / "alpha.json").write_text(
        json.dumps(
            [
                {"id": "clinic_note", "name": "clinic note", "ext": ".pdf"},
                {"id": "first_copy", "name": "mri report", "ext": ".pdf"},
                {"id": "mri_report", "name": "mri report (2)", "ext": ".pdf"},
            ]
        )
    )
    assert assemble_stage.run(sr) == 0
    # MAP_OUT's FILES-SEEN carries `=== FILE: mri report.pdf (fileId mri_report) ===`
    assert json.loads((d / "exhibit_map.json").read_text()) == {"clinic note.pdf": 1, "mri report (2).pdf": 2}


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


REWORDED = """##### CLUSTER 2023-05-10 | examplechiropractic (2 fragments)
05/10/2023
Example Chiropractic | Patient Complaints & Limitations

Palpation of the cervical, thoracic, and lumbar spines revealed pain, tenderness, and trigger points with multiple fixations. (Exhibit 2 - p. 14)
---FRAGMENT-BREAK---
05/10/2023
Example Chiropractic | Patient Complaints & Limitations

Palpation revealed pain, tenderness and trigger points of the cervical, thoracic and lumbar spines with multiple fixations. (Exhibit 2 - p. 14)

"""


def _no_model() -> Scripted:
    return Scripted(lambda p, n: (_ for _ in ()).throw(AssertionError("the model must not be called")))


def test_a_same_citation_rewording_collapses_in_code_by_the_shared_rule(
    job_dir: Path, firm_headings: Path, data_root: Path
) -> None:
    """The pair is a real shape from a client matter (2026-09-15): the same
    chiropractic finding composed twice with its commas moved, J=0.86, same
    page, no numbers. Before this rule the router sent it to the model, the
    prompt told the model to collapse it, and the falsifier refused the
    collapse -- 29 clusters, $62, no chronology. Now code collapses it by
    `merge_falsify.yields_to`, the falsifier credits that same function, and
    the model is never asked.

    Falsifier: restoring the reworded-pair route in merge_cluster makes
    `routed` non-empty and this goes red.
    """
    sr = _sr(job_dir, firm_headings, data_root, client=_no_model())
    d = sr.slug_dir / "runs" / "alpha"
    d.mkdir(parents=True)
    (d / "clusters.md").write_text(REWORDED)
    assert merge_stage.run(sr) == 0
    merged = (d / "merged.md").read_text()
    assert merged.count("Palpation") == 1, merged
    assert "Palpation of the cervical, thoracic, and lumbar spines" in merged, "the longer wording is the one kept"
    route = json.loads((d / "merge_route.json").read_text())
    assert route["code"] == [1] and route["routed"] == []


def test_a_rewording_with_a_differing_number_is_kept_and_routed_to_be_marked(
    job_dir: Path, firm_headings: Path, data_root: Path
) -> None:
    """The number-set guard is what makes code collapse defensible on a legal
    record. Same words, same page, but 6 of 10 against 8 of 10: not the same
    fact. Code keeps both and routes the pair; the model marks the
    disagreement and keeps both; the count is preserved and the falsifier
    passes.

    Falsifier: dropping the number-set comparison from `same_fact` collapses
    this pair in code before the router sees it, `routed` is empty, and the
    merged entry carries one paragraph where the record carries two.
    """
    cluster = REWORDED.replace(
        "trigger points with multiple fixations. (Exhibit 2 - p. 14)\n---FRAGMENT",
        "trigger points with multiple fixations, pain rated 6 of 10. (Exhibit 2 - p. 14)\n---FRAGMENT",
    ).replace(
        "spines with multiple fixations. (Exhibit 2 - p. 14)",
        "spines with multiple fixations, pain rated 8 of 10. (Exhibit 2 - p. 14)",
    )
    assert cluster.count("of 10") == 2
    marked = (
        "05/10/2023\nExample Chiropractic | Patient Complaints & Limitations\n\n"
        "Palpation of the cervical, thoracic, and lumbar spines revealed pain, tenderness, and trigger points "
        "with multiple fixations, pain rated 6 of 10. (Exhibit 2 - p. 14)\n\n"
        "Palpation revealed pain, tenderness and trigger points of the cervical, thoracic and lumbar spines "
        "with multiple fixations, pain rated 8 of 10. The records differ on this point. (Exhibit 2 - p. 14)\n"
    )
    client = Scripted(lambda p, n: _msg(marked))
    log: list[str] = []
    sr = _sr(job_dir, firm_headings, data_root, client, log=log)
    d = sr.slug_dir / "runs" / "alpha"
    d.mkdir(parents=True)
    (d / "clusters.md").write_text(cluster)
    assert merge_stage.run(sr) == 0
    route = json.loads((d / "merge_route.json").read_text())
    assert len(route["routed"]) == 1 and "near-duplicate" in route["routed"][0]["reasons"][0]
    assert len(client.calls) == 1
    merged = (d / "merged.md").read_text()
    assert merged.count("of 10") == 2 and "The records differ on this point." in merged


def _pair_cluster(a: str, b: str, cite: str = "(Exhibit 2 - p. 14)") -> str:
    """Two fragments, same date/provider/heading, one cited sentence each."""
    head = "05/10/2023\nExample Chiropractic | Patient Complaints & Limitations\n\n"
    return (
        "##### CLUSTER 2023-05-10 | examplechiropractic (2 fragments)\n"
        + head
        + f"{a} {cite}\n---FRAGMENT-BREAK---\n"
        + head
        + f"{b} {cite}\n\n"
    )


def test_a_content_word_difference_is_never_collapsed_in_code(firm_headings: Path) -> None:
    """The review of this change caught the first draft: a rule that guarded on
    NUMBERS alone would have collapsed "active" range of motion into "passive"
    (measured: 44 of 47 same-citation pairs on a real matter differed by a
    content word -- active/passive, positive, bilateral -- and only 3 were one
    sentence with its commas moved). Laterality and negation are the same
    class. Code may only claim two sentences are one when their content words
    are identical; a word not in FUNCTION_WORDS keeps both.

    Falsifier: replacing the content-word test in `same_fact` with a token
    Jaccard >= 0.8 collapses every pair below and this goes red.
    """
    hd = mf.Headings.from_config(config_mod.load(str(firm_headings)))
    base = (
        "Range of motion of the cervical, thoracic, and lumbar spine was measured and recorded as restricted with pain"
    )
    distinct = [
        (
            base.replace("Range of motion", "Active range of motion"),
            base.replace("Range of motion", "Passive range of motion"),
        ),
        (
            "Tenderness was noted over the left knee on palpation.",
            "Tenderness was noted over the right knee on palpation.",
        ),
        (
            "Straight leg raise was positive on the left at 45 degrees.",
            "Straight leg raise was negative on the left at 45 degrees.",
        ),
        (
            "There was no swelling of the ankle on examination today.",
            "There was swelling of the ankle on examination today.",
        ),
    ]
    for a, b in distinct:
        na, nb = mf.norm_text(a), mf.norm_text(b)
        assert mf.jaccard(mf.tokens(na), mf.tokens(nb)) >= 0.8, "the fixture must be the case the weak rule got wrong"
        assert not mf.same_fact(na, nb) and not mf.yields_to(na, nb) and not mf.yields_to(nb, na), (a, b)
        text, reasons = merge_stage.merge_cluster(mf.parse_clusters(_pair_cluster(a, b))[0], hd)
        assert text is not None and reasons == [], reasons
        assert a in text and b in text, "both findings survive, each with its citation"
    # and the one shape code MAY collapse: identical content words, only connectives moved
    same = (
        "Palpation of the cervical, thoracic, and lumbar spines revealed pain, tenderness, and trigger points.",
        "Palpation revealed pain, tenderness and trigger points of the cervical, thoracic and lumbar spines.",
    )
    assert mf.same_fact(mf.norm_text(same[0]), mf.norm_text(same[1]))


def test_a_one_sided_number_difference_keeps_both_and_routes_nothing(firm_headings: Path) -> None:
    """A same-citation pair where one sentence carries a number the other lacks
    (no two-sided conflict) is neither `same_fact` (number sets differ) nor a
    near-duplicate route (`conflict` needs a difference on BOTH sides). Before
    this change that pair hit the reworded route and the model was told to
    collapse it. Now it is kept as two paragraphs, unmarked: nothing is lost
    and nothing is adjudicated. Pinned so the next reader knows it is a
    decision, not a gap.
    """
    hd = mf.Headings.from_config(config_mod.load(str(firm_headings)))
    a = "Cervical flexion was measured and found to be restricted with pain at end range."
    b = "Cervical flexion was measured at 30 degrees and found to be restricted with pain at end range."
    text, reasons = merge_stage.merge_cluster(mf.parse_clusters(_pair_cluster(a, b))[0], hd)
    assert reasons == [], "not routed: no two-sided number conflict"
    assert text is not None and a in text and b in text and "differ on this point" not in text


def test_the_falsifier_credits_exactly_what_code_collapses(firm_headings: Path) -> None:
    """One rule, two readers. For a cluster holding a same_fact pair AND a
    containment pair, the floor the falsifier computes equals the paragraph
    count the code merge emits, so the code merge passes its own falsifier
    with nothing to spare.

    Falsifier: making `containment_collapses` credit only `t in o` (the
    2026-09-15 defect) raises the floor by one and `check` returns 4. The
    exact mutation: replace `any(yields_to(t, o) for o in texts)` with
    `any(o != t and t in o for o in texts)`.
    """
    hd = mf.Headings.from_config(config_mod.load(str(firm_headings)))
    cluster = REWORDED.replace(
        "spines with multiple fixations. (Exhibit 2 - p. 14)\n",
        "spines with multiple fixations. (Exhibit 2 - p. 14)\n\n"
        "Medical Diagnoses\n\nCervical strain. (Exhibit 2 - p. 15)\n\n"
        "Cervical strain with radiculopathy. (Exhibit 2 - p. 15)\n",
    )
    parsed = mf.parse_clusters(cluster)
    assert len(parsed) == 1
    text, reasons = merge_stage.merge_cluster(parsed[0], hd)
    assert text is not None and reasons == [], reasons
    rc, rep = mf.check(cluster, text, hd)
    assert rc == 0, rep
    line = next(ln for ln in rep if ln.startswith("paragraphs:"))
    assert "4 distinct in, 2 same-cite containment collapse(s) allowed, floor 2, 2 out" in line, line


def test_a_model_that_fuses_two_paragraphs_is_refused(job_dir: Path, firm_headings: Path, data_root: Path) -> None:
    """The prompt now forbids fusion as well as collapse, because joining two
    cited sentences into one paragraph drops the distinct count exactly as a
    deletion does. A cluster routed for a PARSE reason (an unknown heading)
    still reaches the model; a model that fuses is refused, bisected to one
    cluster, cannot split, and the stage exits 1 rather than ship it.

    Falsifier: removing the paragraph-count check from `mf.check` lets the
    fused answer through and the run returns 0.
    """
    routed = CLUSTER.replace("Treatment Recommendations", "Discontinuation in Care")
    assert routed != CLUSTER
    fused = (
        "01/02/2026\nExample Clinic | Patient Complaints & Limitations\n\n"
        "The patient reports neck pain rated 6 of 10. (Exhibit 1 - p. 1)\n\nMedical Diagnoses\n\n"
        "Cervical strain. Physical therapy twice weekly. (Exhibit 1 - p. 2)\n"
    )
    client = Scripted(lambda p, n: _msg(fused))
    log: list[str] = []
    sr = _sr(job_dir, firm_headings, data_root, client, log=log)
    d = sr.slug_dir / "runs" / "alpha"
    d.mkdir(parents=True)
    (d / "clusters.md").write_text(routed)
    assert merge_stage.run(sr) == 1
    assert any("unknown heading" in line for line in log), "it was routed for the parse reason, not a rewording"
    assert any("LOST 1 paragraph" in line for line in log), log
    assert not (d / "merged.md").exists()


def test_the_prompt_forbids_rewording_collapse_and_fusion(firm_headings: Path) -> None:
    """Prompt and falsifier cannot share code, so they share words, pinned
    here. The 2026-09-15 line licensed collapsing "the same fact from the
    SAME source file and pages" -- the collapse the falsifier cannot credit.

    Falsifier: restoring that sentence goes red on the third assertion.
    """
    text = prompts.load("merge-system", config_mod.load(str(firm_headings)))
    assert "Drop nothing" in text, "the model never deletes; what may be collapsed is decided in code"
    assert "A rewording is never a duplicate" in text
    assert "its own cited paragraph" in text and "Never join two cited sentences" in text
    assert "same fact" not in text.lower() and "Drop a sentence" not in text


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
