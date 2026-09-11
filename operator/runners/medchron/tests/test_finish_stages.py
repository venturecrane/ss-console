"""group, filter (scope), exhibits, condense and summarize in-process, against
a scripted SDK client and real PDFs."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import yaml

from medchron import config as config_mod, job as job_mod
from medchron.stages import condense as condense_stage, exhibits as exhibits_stage, group as group_stage
from medchron.stages import scope as scope_stage, summarize as summarize_stage
from medchron.stages.base import StageRun
from medchron_testkit import FIRM_CONFIG, FakeSeat, make_pdf


class Usage:
    input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens = 100, 20, 0, 0


def _msg(text: str) -> NS:
    return NS(content=[NS(type="text", text=text)], stop_reason="end_turn", usage=Usage())


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
def firm(tmp_path: Path) -> Path:
    cfg = json.loads(json.dumps(FIRM_CONFIG))
    cfg["providers"]["aliases"] = [
        {"match": r"example clinic|ex clinic", "label": "Example Clinic"},
        {"match": r"example health|exh\b", "label": "Example Health System"},
    ]
    cfg["format"]["subsections"] = [
        "Patient Complaints & Limitations",
        "HPI & Prior Medical History",
        "Medical Diagnoses",
        "Treatments Administered During This Visit",
        "Treatment Recommendations",
        "All Other Information",
    ]
    p = tmp_path / "firm.yaml"
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


# ---- group ---------------------------------------------------------------------
def test_canon_collapses_clinicians_and_brands_decide_first(firm: Path) -> None:
    canon = group_stage.Canon(config_mod.load(str(firm)))
    assert canon("Internal Medicine, Example Health System Fairfield") == "Example Health System"
    assert canon("EX CLINIC (Jane Doe, MD)") == "Example Clinic"
    assert canon("Riverside Imaging, John Q Public, M.D.") == "Riverside Imaging"
    assert (
        canon("Riverside Imaging, Sacramento Emergency Department")
        == "Riverside Imaging, Sacramento Emergency Department"
    )
    assert canon("Philip Q. Sample, M.D., Inc.") == "Philip Q. Sample, M.D., Inc."
    assert canon("Riverside Chiropractic - NEED UPDATED RECS") == "Riverside Chiropractic"


def _seed_unit(sr: StageRun, files: list[dict], index_lines: list[str]) -> None:
    (sr.slug_dir / "units").mkdir(parents=True, exist_ok=True)
    (sr.slug_dir / "units" / "alpha.json").write_text(json.dumps(files))
    rd = sr.slug_dir / "runs" / "alpha"
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "map-01.md").write_text(
        "## ENTRIES\nnone in this chunk\n\n## INDEX\n" + "\n".join(index_lines) + "\n\n## FILES-SEEN\n"
    )


def test_group_attributes_by_index_then_folder_then_sentinel(job_dir: Path, firm: Path, data_root: Path) -> None:
    sr = _sr(job_dir, firm, data_root)
    _seed_unit(
        sr,
        [
            {"id": "a", "name": "clinic note", "ext": ".pdf", "folder": "/MEDICAL/Riverside Imaging"},
            {"id": "b", "name": "bill", "ext": ".pdf", "folder": "/MEDICAL/Riverside Imaging - NEED"},
            {"id": "c", "name": "EXH statement", "ext": ".pdf", "folder": "/(root)"},
            {"id": "d", "name": "Invoice 12345", "ext": ".pdf", "folder": "/(root)"},
            {"id": "e", "name": "eob", "ext": ".pdf", "folder": "/Carrier (Policy) 250/500"},
        ],
        ["2026-01-02 | Example Clinic (Jane Doe, MD) | S13.4 | clinic note.pdf"],
    )
    assert group_stage.run(sr) == 0
    groups = {g["provider"]: g for g in json.loads((sr.slug_dir / "groups" / "alpha.json").read_text())}
    assert groups["Example Clinic"]["file_ids"] == ["a"] and groups["Example Clinic"]["exhibit"] is True
    assert groups["Riverside Imaging"]["file_ids"] == ["b"] and groups["Riverside Imaging"]["exhibit"] is False
    assert groups["Example Health System"]["file_ids"] == ["c"]
    assert groups["(unattributed)"]["file_ids"] == ["d"]  # a filename is not a provider
    assert groups["Carrier (Policy) 250"]["file_ids"] == ["e"]  # the numeric leaf "500" is skipped


# ---- scope (filter) ------------------------------------------------------------------
E_PRE_ROUTINE = "06/01/2025\nExample Clinic | Medical Diagnoses\n\nSeasonal allergies. (Exhibit 1 - p. 1)\n"
E_PRE_MATERIAL = "09/01/2025\nExample Clinic | Medical Diagnoses\n\nChronic neck pain. (Exhibit 1 - p. 2)\n"
E_POST = (
    "01/20/2026\nExample Clinic | Patient Complaints & Limitations\n\n"
    "Neck pain since the Subject Incident. (Exhibit 1 - p. 3)\n"
)


def test_scope_omits_with_disclosure_and_keeps_unclassified(job_dir: Path, firm: Path, data_root: Path) -> None:
    client = Scripted(lambda p, n: _msg("1 | OMIT | unrelated allergy visit\n"))  # entry 2 unclassified -> kept
    sr = _sr(job_dir, firm, data_root, client)
    d = sr.slug_dir / "runs" / "alpha"
    d.mkdir(parents=True)
    (d / "entries.md").write_text("\n\n".join([E_PRE_ROUTINE, E_PRE_MATERIAL, E_POST]))
    assert scope_stage.run(sr) == 0
    scoped = (d / "entries_scoped.md").read_text()
    assert "Seasonal allergies" not in scoped and "Chronic neck pain" in scoped and "Subject Incident" in scoped
    omitted = json.loads((d / "omitted_preincident.json").read_text())
    assert omitted == [
        {"date": "2025-06-01", "reason": "unrelated allergy visit", "head": "Example Clinic | Medical Diagnoses"}
    ]
    assert (
        "1 additional pre-incident encounters dated 2025-06-01 to 2025-06-01"
        in (d / "preincident_note.txt").read_text()
    )
    payload = client.calls[0]["messages"][0]["content"]
    assert (
        payload.startswith("CLAIM INJURIES: example injury")
        and "1. 2025-06-01" in payload
        and "2. 2025-09-01" in payload
    )
    assert (d / "entries_full.md").is_file()


def test_scope_refuses_when_clusters_have_no_merge(job_dir: Path, firm: Path, data_root: Path) -> None:
    sr = _sr(job_dir, firm, data_root, Scripted(lambda p, n: _msg("")))
    d = sr.slug_dir / "runs" / "alpha"
    d.mkdir(parents=True)
    (d / "entries.md").write_text(E_POST)
    (d / "clusters.md").write_text("##### CLUSTER 2026-01-02 | x (2 fragments)\n")
    assert scope_stage.run(sr) == 1


# ---- exhibits ----------------------------------------------------------------------
def test_exhibits_merges_a_providers_files_and_remaps_citations(job_dir: Path, firm: Path, data_root: Path) -> None:
    sr = _sr(job_dir, firm, data_root)
    d = sr.slug_dir
    raw = d / "raw"
    raw.mkdir(parents=True)
    (raw / "a.pdf").write_bytes(make_pdf(["", "", ""]))
    (raw / "b.pdf").write_bytes(make_pdf(["", ""]))
    (d / "raw_manifest.jsonl").write_text(
        json.dumps({"id": "a", "ok": True, "path": str(raw / "a.pdf")})
        + "\n"
        + json.dumps({"id": "b", "ok": True, "path": str(raw / "b.pdf")})
        + "\n"
    )
    _seed_unit(
        sr,
        [
            {"id": "a", "name": "clinic note", "ext": ".pdf", "folder": "/MEDICAL"},
            {"id": "b", "name": "clinic bill", "ext": ".pdf", "folder": "/MEDICAL"},
        ],
        ["2026-01-02 | Example Clinic | -- | clinic note.pdf", "2026-01-09 | Example Clinic | -- | clinic bill.pdf"],
    )
    rd = d / "runs" / "alpha"
    (rd / "exhibit_map.json").write_text(json.dumps({"clinic note.pdf": 1, "clinic bill.pdf": 2}))
    (rd / "entries_scoped.md").write_text(
        "01/02/2026\nExample Clinic | Medical Diagnoses\n\nStrain. (Exhibit 1 - p. 2)\n\n"
        "01/09/2026\nExample Clinic | Medical Diagnoses\n\nBilled. (Exhibit 2 - p. 1-2)\n"
    )
    assert group_stage.run(sr) == 0
    assert exhibits_stage.run(sr) == 0
    page_map = json.loads((d / "out" / "alpha" / "page_map.json").read_text())
    assert (
        len(page_map) == 1
        and page_map[0]["total_pages"] == 5
        and page_map[0]["record_type"] == "Medical Records & Bills"
    )
    assert page_map[0]["title"] == "Exhibit 1 - Example Clinic - 01-02-2026 - 01-09-2026 (Medical Records & Bills)"
    final = (rd / "entries_final.md").read_text()
    assert "(Exhibit 1 - p. 2)" in final and "(Exhibit 1 - p. 4-5)" in final
    assert [p.name for p in (d / "out" / "alpha").glob("Exhibit *.pdf")] == [page_map[0]["title"] + ".pdf"]


def test_exhibits_refuses_a_citation_it_cannot_remap(job_dir: Path, firm: Path, data_root: Path) -> None:
    sr = _sr(job_dir, firm, data_root)
    d = sr.slug_dir
    (d / "raw_manifest.jsonl").parent.mkdir(parents=True, exist_ok=True)
    (d / "raw_manifest.jsonl").write_text("")
    _seed_unit(sr, [{"id": "a", "name": "clinic note", "ext": ".pdf", "folder": "/MEDICAL"}], [])
    rd = d / "runs" / "alpha"
    (rd / "exhibit_map.json").write_text(json.dumps({"clinic note.pdf": 1}))
    (rd / "entries.md").write_text("01/02/2026\nExample Clinic | Medical Diagnoses\n\nStrain. (Exhibit 7 - p. 2)\n")
    (d / "groups").mkdir()
    (d / "groups" / "alpha.json").write_text(
        json.dumps(
            [
                {
                    "provider": "Example Clinic",
                    "file_ids": ["a"],
                    "first": "2026-01-02",
                    "last": "2026-01-02",
                    "dated_files": 1,
                    "exhibit": True,
                }
            ]
        )
    )
    assert exhibits_stage.run(sr) == 1


# ---- condense ------------------------------------------------------------------------
LONG_PT = (
    "02/03/2026\nExample Physical Therapy | Patient Complaints & Limitations\n\n"
    + "The patient reports continued neck pain and stiffness with difficulty turning the head while driving and "
    "limited tolerance for sitting at a desk for more than thirty minutes at a time. (Exhibit 2 - p. 4)\n\n"
    "Treatments Administered During This Visit\n\n"
    + "Therapeutic exercise, manual therapy to the cervical paraspinals, and moist heat were administered for a "
    "total of forty five minutes with the patient tolerating all interventions well and without adverse "
    "response, and vital signs were within normal limits and unchanged from the prior visit. (Exhibit 2 - p. 4)\n\n"
    "All Other Information\n\n"
    + "The plan of care was reviewed and the patient was instructed to continue the home exercise program as "
    "previously issued and to return in two days for the next scheduled session. (Exhibit 2 - p. 5)\n"
)
SHORT_PT = (
    "02/03/2026\nExample Physical Therapy | Patient Complaints & Limitations\n\n"
    "Continued neck pain and stiffness limiting driving and desk work. (Exhibit 2 - p. 4)\n\n"
    "Treatments Administered During This Visit\n\n"
    "Therapeutic exercise, manual therapy and moist heat, tolerated well. (Exhibit 2 - p. 4)\n"
)
MAJOR = (
    "01/20/2026\nExample Health System Emergency Department | Patient Complaints & Limitations\n\n"
    + "Neck pain after the collision. " * 20
    + "(Exhibit 1 - p. 3)\n"
)


def test_condense_shortens_routine_entries_and_rejects_invented_citations(
    job_dir: Path, firm: Path, data_root: Path
) -> None:
    def reply(p, n):
        return _msg(SHORT_PT if n == 1 else SHORT_PT.replace("(Exhibit 2 - p. 4)", "(Exhibit 9 - p. 4)", 1))

    client = Scripted(reply)
    log: list[str] = []
    sr = _sr(job_dir, firm, data_root, client, log=log)
    d = sr.slug_dir / "runs" / "alpha"
    d.mkdir(parents=True)
    pre = "Prior Medical History\n\nOld history. (Exhibit 1 - p. 1)\n\n"
    second = LONG_PT.replace("02/03/2026", "02/05/2026")
    (d / "entries_scoped_final.md").write_text(pre + "\n\n".join([MAJOR, LONG_PT, second]))
    monkey = condense_stage.PAUSE_SECONDS
    condense_stage.PAUSE_SECONDS = 0
    try:
        assert condense_stage.run(sr) == 0
    finally:
        condense_stage.PAUSE_SECONDS = monkey
    body = (d / "entries_condensed.md").read_text()
    assert body.startswith("Prior Medical History")  # the preamble survives
    assert "Neck pain after the collision." in body  # the major entry is untouched
    assert SHORT_PT.strip() in body and second.strip() in body  # one condensed, one kept long
    assert len(client.calls) == 2 and any("kept long (invented citation)" in line for line in log)


def test_condense_never_touches_pre_incident_routine_entries(job_dir: Path, firm: Path, data_root: Path) -> None:
    client = Scripted(lambda p, n: (_ for _ in ()).throw(AssertionError("no call expected")))
    sr = _sr(job_dir, firm, data_root, client)
    d = sr.slug_dir / "runs" / "alpha"
    d.mkdir(parents=True)
    (d / "entries_final.md").write_text(LONG_PT.replace("02/03/2026", "12/03/2025"))
    assert condense_stage.run(sr) == 0
    assert (d / "entries_condensed.md").read_text() == LONG_PT.replace("02/03/2026", "12/03/2025")


# ---- summarize ------------------------------------------------------------------------
def test_summarize_collapses_pre_incident_and_rejects_a_reach_beyond_source(
    job_dir: Path, firm: Path, data_root: Path
) -> None:
    block = (
        "Prior Medical History\n\nThe patient carried a diagnosis of chronic neck pain before the incident. "
        "(Exhibit 1 - p. 2)"
    )
    client = Scripted(lambda p, n: _msg(block))
    sr = _sr(job_dir, firm, data_root, client)
    d = sr.slug_dir / "runs" / "alpha"
    d.mkdir(parents=True)
    (d / "entries_condensed.md").write_text("\n\n".join([E_PRE_MATERIAL, E_POST]))
    assert summarize_stage.run(sr) == 0
    out = (d / "entries_scoped_final.md").read_text()
    assert out.startswith("Prior Medical History") and "Subject Incident" in out and "Chronic neck pain." not in out
    assert client.calls[0]["messages"][0]["content"] == E_PRE_MATERIAL.strip()
    client2 = Scripted(lambda p, n: _msg(block.replace("p. 2", "p. 7")))
    sr2 = _sr(job_dir, firm, data_root, client2)
    assert summarize_stage.run(sr2) == 1
    assert summarize_stage.beyond_source("(Exhibit 1 - p. 2-4)", "(Exhibit 1 - p. 3) (Exhibit 2 - p. 1)") == [
        "Exhibit 2 never cited in source"
    ]


def test_summarize_with_no_pre_incident_is_free(job_dir: Path, firm: Path, data_root: Path) -> None:
    client = Scripted(lambda p, n: (_ for _ in ()).throw(AssertionError("no call expected")))
    sr = _sr(job_dir, firm, data_root, client)
    d = sr.slug_dir / "runs" / "alpha"
    d.mkdir(parents=True)
    (d / "entries_final.md").write_text(E_POST)
    assert summarize_stage.run(sr) == 0 and (d / "entries_scoped_final.md").read_text() == E_POST.strip()
