"""Stops that fired on legitimate records, each now decided by a rule and
disclosed (the 2026-10-24 stop-path audit, rows 6-10, 16 and the email
attachment note). Every test reproduces the legitimate-record trigger: on the
code before this change each one stopped the job.

Policy: a page the map never read may be NAMED as unread; a record the map
read is never dropped into a disclosure sentence; no content is invented."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import yaml

from medchron import config as config_mod, job as job_mod
from medchron.stages import build_doc as build_doc_stage, compose as compose_stage, coverage
from medchron.stages import exhibits as exhibits_stage, group as group_stage, limitations, msg as msg_stage
from medchron.stages import repair as repair_stage, summarize as summarize_stage
from medchron.stages.base import StageRun
from medchron_testkit import FIRM_CONFIG, FakeSeat, make_pdf

COMPOSITION, JUDGMENT = "claude-sonnet-5", "claude-opus-5-5"


class Usage:
    def __init__(self, i: int = 100, o: int = 50) -> None:
        self.input_tokens, self.output_tokens = i, o
        self.cache_read_input_tokens = self.cache_creation_input_tokens = 0


def _msg(text: str, stop: str = "end_turn") -> NS:
    return NS(content=[NS(type="text", text=text)], stop_reason=stop, usage=Usage())


class Stream:
    def __init__(self, msg: NS) -> None:
        self.msg = msg

    def __enter__(self) -> "Stream":
        return self

    def __exit__(self, *a: object) -> bool:
        return False

    def get_final_message(self) -> NS:
        return self.msg


class Scripted:
    def __init__(self, reply) -> None:
        self.calls: list[dict] = []
        self._reply = reply
        self.messages = NS(create=self._create, stream=self._stream, batches=None)

    def _create(self, **params):
        self.calls.append(params)
        return self._reply(params, len(self.calls))

    def _stream(self, **params):
        self.calls.append(params)
        return Stream(self._reply(params, len(self.calls)))


def _firm(tmp_path: Path, policy: str = "include") -> Path:
    cfg = json.loads(json.dumps(FIRM_CONFIG))
    cfg["models"]["tiers"]["composition"] = COMPOSITION
    cfg["models"]["tiers"]["judgment"] = JUDGMENT
    cfg["chronology"]["pre_incident_history"] = policy
    cfg["providers"]["aliases"] = [{"match": r"example clinic", "label": "Example Clinic"}]
    p = tmp_path / f"firm-{policy}.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return p


def _sr(job_dir: Path, firm: Path, data_root: Path, client=None, log: list[str] | None = None) -> StageRun:
    job = job_mod.load(job_dir)
    lines = log if log is not None else []
    return StageRun(
        job=job,
        cfg=config_mod.load(str(firm)),
        unit=job.units[0],
        slug_dir=data_root / "example-matter",
        decided={},
        log=lines.append,
        seat_factory=lambda: FakeSeat([], [], {}),
        client_factory=lambda: client,
    )


def _content(p: dict) -> str:
    c = p["messages"][0]["content"]
    return c if isinstance(c, str) else json.dumps(c)


def _map_for(name: str, fid: str) -> str:
    return (
        "## ENTRIES\n01/02/2026\nExample Clinic | Medical Diagnoses\n\nCervical strain. "
        f"(FILE: {name}, p. 1)\n\n## INDEX\n2026-01-02 | Example Clinic | S13.4 | {name}\n\n"
        "## BILLING-DATES\nnone in this chunk\n\n## CONFLICTS / REFERENCED-BUT-ABSENT\nnone observed\n\n"
        f"## FILES-SEEN\n=== FILE: {name} (fileId {fid}) === entries: 1\n"
    )


def _unit_files(sr: StageRun, texts: dict[str, str]) -> None:
    (sr.slug_dir / "text").mkdir(parents=True, exist_ok=True)
    (sr.slug_dir / "units").mkdir(parents=True, exist_ok=True)
    rows = []
    for name, text in texts.items():
        fid = name.replace(" ", "_")
        tp = sr.slug_dir / "text" / f"{fid}.txt"
        tp.write_text(text)
        rows.append({"id": fid, "name": name, "ext": ".pdf", "text_path": str(tp), "chars": len(text)})
    (sr.slug_dir / "units" / "alpha.json").write_text(json.dumps(rows))


# ---- row 6: a chunk the map declines -----------------------------------------------
SANE = "[p.1] Sexual assault nurse examiner report, ASSAULT-EXAM\n[p.2] ASSAULT-EXAM findings\n"
CLINIC = "[p.1] Cervical strain diagnosed today.\n"


def _declines_the_exam(p: dict, n: int) -> NS:
    if "ASSAULT-EXAM" in _content(p):
        return _msg("", stop="refusal")
    return _msg(_map_for("clinic note.pdf", "clinic_note"))


def test_a_chunk_refused_everywhere_is_retried_on_judgment_then_split_and_its_pages_named_unread(
    job_dir: Path, tmp_path: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A SANE exam in a chunk: the composition tier refuses three times. The
    stage asks the judgment tier, then sends each file alone, so the clinic
    note is composed and only the exam's pages, which nothing read, are named
    as unread. The map exits 0 and the gate downstream has the file's account."""
    client = Scripted(_declines_the_exam)
    sr = _sr(job_dir, _firm(tmp_path), data_root, client)
    _unit_files(sr, {"clinic note": CLINIC, "sane exam": SANE})
    monkeypatch.setattr(compose_stage.time, "sleep", lambda *_: None)
    assert compose_stage.run(sr) == 0
    d = sr.slug_dir / "runs" / "alpha"
    models = [c["model"] for c in client.calls]
    # whole: 3 composition + 1 judgment; clinic alone: 1; exam alone: 3 + 1;
    # each exam page alone: 1 + 1 (both pages carry the declined text).
    assert models[:4] == [COMPOSITION] * 3 + [JUDGMENT]
    assert len(client.calls) == 13 and models.count(JUDGMENT) == 4
    unread = json.loads((d / "unread_pages.json").read_text())
    assert sorted((u["file"], u["pages"], u["reason"]) for u in unread) == [
        ("sane exam.pdf", [1], "refused"),
        ("sane exam.pdf", [2], "refused"),
    ]
    composed = "".join(p.read_text() for p in sorted(d.glob("map-01-*.md")))
    assert "Cervical strain" in composed  # the clinic note's record is kept, never disclosed away
    files = json.loads((sr.slug_dir / "units" / "alpha.json").read_text())
    said = coverage.composer_dispositions(d, files)
    assert "sane exam.pdf" in said and "clinic note.pdf" not in said
    text = "\n".join(limitations.section(sr.slug_dir, "alpha", lambda _m: None))
    assert '"sane exam.pdf" - pp. 1-2' in text and "clinic note" not in text


def test_a_refusal_the_judgment_tier_reads_is_composed_with_nothing_unread(
    job_dir: Path, tmp_path: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reply(p: dict, n: int) -> NS:
        if p["model"] == COMPOSITION:
            return _msg("", stop="refusal")
        return _msg(_map_for("clinic note.pdf", "clinic_note"))

    client = Scripted(reply)
    sr = _sr(job_dir, _firm(tmp_path), data_root, client)
    _unit_files(sr, {"clinic note": CLINIC})
    monkeypatch.setattr(compose_stage.time, "sleep", lambda *_: None)
    assert compose_stage.run(sr) == 0
    d = sr.slug_dir / "runs" / "alpha"
    assert "Cervical strain" in (d / "map-01.md").read_text()
    assert not (d / "unread_pages.json").exists() and len(client.calls) == 4


def test_a_half_that_stays_empty_is_sent_by_file_and_only_the_silent_file_is_named(
    job_dir: Path, tmp_path: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty after the split used to leave the chunk missing and fail the job."""

    def reply(p: dict, n: int) -> NS:
        c = _content(p)
        if "MUTE" in c:
            return _msg("x")  # nothing, in no shape, every time
        if "clinic note" in c and "MUTE" not in c:
            return _msg(_map_for("clinic note.pdf", "clinic_note"))
        return _msg("x")

    body = "".join(f"[p.{k}] MUTE fax cover\n" for k in range(1, 4))
    clinic = "".join(f"[p.{k}] " + "record text " * 40 + "\n" for k in range(1, 30))
    client = Scripted(reply)
    sr = _sr(job_dir, _firm(tmp_path), data_root, client)
    _unit_files(sr, {"clinic note": clinic, "fax covers": body, "fax covers two": body})
    monkeypatch.setattr(compose_stage.time, "sleep", lambda *_: None)
    assert compose_stage.run(sr) == 0
    d = sr.slug_dir / "runs" / "alpha"
    unread = json.loads((d / "unread_pages.json").read_text())
    assert sorted({u["file"] for u in unread}) == ["fax covers two.pdf", "fax covers.pdf"]
    assert sorted(p for u in unread if u["file"] == "fax covers.pdf" for p in u["pages"]) == [1, 2, 3]
    assert {u["reason"] for u in unread} == {"empty"}


def test_a_piece_that_errors_every_time_is_never_called_produced_on_a_rerun(
    job_dir: Path, tmp_path: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A half that composed beside a half that errored three times: the chunk
    is incomplete, and a rerun must not take the surviving half for the
    whole (an infrastructure error is never disclosed as unread)."""

    def reply(p: dict, n: int) -> NS:
        if n == 1:
            return _msg("x")  # the whole chunk empties: split it
        if "BROKEN" in _content(p):
            raise RuntimeError("upstream connect error")
        return _msg(_map_for("clinic note.pdf", "clinic_note"))

    clinic = "".join(f"[p.{k}] " + "record text " * 40 + "\n" for k in range(1, 30))
    client = Scripted(reply)
    sr = _sr(job_dir, _firm(tmp_path), data_root, client)
    _unit_files(sr, {"clinic note": clinic, "broken": "[p.1] BROKEN\n" * 30})
    monkeypatch.setattr(compose_stage.time, "sleep", lambda *_: None)
    sr.doorway.retry_budget_s, sr.doorway.sleep = 0.0, lambda *_: None  # the doorway's network retries, in no time
    assert compose_stage.run(sr) == compose_stage.INFRA_EXIT == 75  # infrastructure, never a defect's 1
    d = sr.slug_dir / "runs" / "alpha"
    assert not (d / "unread_pages.json").exists()
    assert not compose_stage.produced(d, 1)


# ---- rows 7 and 8: exhibits from a word file and from an encrypted PDF --------------
def _seed_exhibits(sr: StageRun, files: list[dict], raw_rows: list[dict], cites: str) -> Path:
    d = sr.slug_dir
    (d / "units").mkdir(parents=True, exist_ok=True)
    (d / "units" / "alpha.json").write_text(json.dumps(files))
    (d / "raw_manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in raw_rows))
    rd = d / "runs" / "alpha"
    rd.mkdir(parents=True, exist_ok=True)
    idx = "\n".join(f"2026-01-0{k + 2} | Example Clinic | -- | {f['name']}{f['ext']}" for k, f in enumerate(files))
    (rd / "map-01.md").write_text("## ENTRIES\nnone in this chunk\n\n## INDEX\n" + idx + "\n\n## FILES-SEEN\n")
    (rd / "exhibit_map.json").write_text(json.dumps({f["name"] + f["ext"]: k + 1 for k, f in enumerate(files)}))
    (rd / "entries_scoped.md").write_text(cites)
    return rd


def test_a_cited_word_report_becomes_exhibit_pages_rendered_from_its_text_and_is_disclosed(
    job_dir: Path, tmp_path: Path, data_root: Path
) -> None:
    """A narrative report filed as .docx: extract reads it and the map cites
    it, but the exhibit step could only page a PDF or an image, so the
    citation had nothing to land on and the job refused."""
    import pymupdf

    sr = _sr(job_dir, _firm(tmp_path), data_root)
    d = sr.slug_dir
    raw = d / "raw"
    raw.mkdir(parents=True)
    (raw / "a.pdf").write_bytes(make_pdf(["", ""]))
    (raw / "r.docx").write_bytes(b"not read here: the exhibit is rendered from the extracted text")
    (d / "text").mkdir()
    tp = d / "text" / "r.txt"
    tp.write_text("NARRATIVE REPORT\nThe patient was seen for neck pain after the collision.\n" * 3)
    rd = _seed_exhibits(
        sr,
        [
            {"id": "a", "name": "clinic note", "ext": ".pdf", "folder": "/MEDICAL"},
            {"id": "r", "name": "narrative report", "ext": ".docx", "folder": "/MEDICAL", "text_path": str(tp)},
        ],
        [{"id": "a", "ok": True, "path": str(raw / "a.pdf")}, {"id": "r", "ok": True, "path": str(raw / "r.docx")}],
        "01/02/2026\nExample Clinic | Medical Diagnoses\n\nStrain. (Exhibit 1 - p. 2)\n\n"
        "01/03/2026\nExample Clinic | Medical Diagnoses\n\nNarrative. (Exhibit 2 - p. 1)\n",
    )
    assert group_stage.run(sr) == 0
    assert exhibits_stage.run(sr) == 0
    pm = json.loads((d / "out" / "alpha" / "page_map.json").read_text())
    assert len(pm) == 1 and pm[0]["total_pages"] >= 3
    word = [f for f in pm[0]["files"] if f["file"] == "narrative report.docx"][0]
    assert word["rendered_from_text"] is True and word["start_page"] == 3
    assert "(Exhibit 1 - p. 3)" in (rd / "entries_final.md").read_text()
    pdf = next((d / "out" / "alpha").glob("Exhibit 1 - *.pdf"))
    with pymupdf.open(pdf) as doc:
        assert "NARRATIVE REPORT" in doc[2].get_text()
    text = "\n".join(limitations.section(d, "alpha", lambda _m: None))
    assert '"narrative report.docx"' in text and "rendered from the document's text" in text


class _LazyFailingReader:
    """pypdf as it met an encrypted Kaiser PDF (Martinez): the reader opens,
    and the first touch of `.pages` raises. pymupdf read the same file."""

    def __init__(self, real, path: object) -> None:
        self._real, self._path = real, str(path)

    @property
    def pages(self):
        if self._path.endswith("e.pdf"):
            from pypdf.errors import FileNotDecryptedError

            raise FileNotDecryptedError("File has not been decrypted")
        return self._real.pages


def test_an_encrypted_pdf_pypdf_cannot_page_is_read_another_way_not_a_crash(
    job_dir: Path, tmp_path: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pypdf opens an encrypted PDF lazily and raises on `.pages`, which sat
    outside the try: the stage crashed (failed, terminal) on a file pymupdf
    reads without a password."""
    import pypdf

    sr = _sr(job_dir, _firm(tmp_path), data_root)
    d = sr.slug_dir
    raw = d / "raw"
    raw.mkdir(parents=True)
    (raw / "e.pdf").write_bytes(make_pdf(["page one", "page two"]))
    real = pypdf.PdfReader
    monkeypatch.setattr(pypdf, "PdfReader", lambda p, *a, **k: _LazyFailingReader(real(p, *a, **k), p))
    rd = _seed_exhibits(
        sr,
        [{"id": "e", "name": "kaiser records", "ext": ".pdf", "folder": "/MEDICAL"}],
        [{"id": "e", "ok": True, "path": str(raw / "e.pdf")}],
        "01/02/2026\nExample Clinic | Medical Diagnoses\n\nStrain. (Exhibit 1 - p. 2)\n",
    )
    assert group_stage.run(sr) == 0
    assert exhibits_stage.run(sr) == 0
    pm = json.loads((d / "out" / "alpha" / "page_map.json").read_text())
    assert pm[0]["total_pages"] == 2
    assert "(Exhibit 1 - p. 2)" in (rd / "entries_final.md").read_text()


def test_a_file_no_reader_can_page_is_recorded_as_its_error_not_a_crash(
    job_dir: Path, tmp_path: Path, data_root: Path
) -> None:
    sr = _sr(job_dir, _firm(tmp_path), data_root)
    d = sr.slug_dir
    raw = d / "raw"
    raw.mkdir(parents=True)
    (raw / "a.pdf").write_bytes(make_pdf(["", ""]))
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "locked")
    doc.save(str(raw / "bad.pdf"), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    doc.close()
    _seed_exhibits(
        sr,
        [
            {"id": "a", "name": "clinic note", "ext": ".pdf", "folder": "/MEDICAL"},
            {"id": "b", "name": "broken scan", "ext": ".pdf", "folder": "/MEDICAL"},
        ],
        [{"id": "a", "ok": True, "path": str(raw / "a.pdf")}, {"id": "b", "ok": True, "path": str(raw / "bad.pdf")}],
        "01/02/2026\nExample Clinic | Medical Diagnoses\n\nStrain. (Exhibit 1 - p. 2)\n",
    )
    assert group_stage.run(sr) == 0
    assert exhibits_stage.run(sr) == 0
    pm = json.loads((d / "out" / "alpha" / "page_map.json").read_text())
    errs = [f for f in pm[0]["files"] if f.get("error")]
    assert [f["file"] for f in errs] == ["broken scan.pdf"]


# ---- rows 9 and 10: the Prior Medical History block --------------------------------
E_PRE = "09/01/2025\nExample Clinic | Medical Diagnoses\n\nChronic neck pain. (Exhibit 1 - p. 3, 5)\n"
E_PRE2 = "10/01/2025\nExample Clinic | Medical Diagnoses\n\nLow back pain. (Exhibit 1 - p. 7)\n"
E_POST = "01/20/2026\nExample Clinic | Patient Complaints & Limitations\n\nNeck pain since. (Exhibit 1 - p. 9)\n"
MERGED = "Prior Medical History\n\nNeck and back pain predate the incident. (Exhibit 1 - p. 3-7)"
GOOD = "Prior Medical History\n\nChronic neck pain. (Exhibit 1 - p. 3, 5) Low back pain. (Exhibit 1 - p. 7)"


def _seed_entries(sr: StageRun) -> Path:
    d = sr.slug_dir / "runs" / "alpha"
    d.mkdir(parents=True)
    (d / "entries_condensed.md").write_text("\n\n".join([E_PRE, E_PRE2, E_POST]))
    return d


def test_a_summary_that_merges_ranges_is_retried_with_what_it_reached_and_accepted_clean(
    job_dir: Path, tmp_path: Path, data_root: Path
) -> None:
    """The summarizer wrote "pp. 3-7" for "pp. 3, 5" and "p. 7": a page the
    source never cited. One retry carries exactly what it reached."""
    client = Scripted(lambda p, n: _msg(MERGED if n == 1 else GOOD))
    sr = _sr(job_dir, _firm(tmp_path), data_root, client)
    d = _seed_entries(sr)
    assert summarize_stage.run(sr) == 0
    assert len(client.calls) == 2
    feedback = client.calls[1]["messages"][-1]["content"]
    assert "Exhibit 1 pages [4, 6]" in feedback
    assert (d / "entries_scoped_final.md").read_text() == GOOD + "\n\n" + E_POST.strip()


def test_a_summary_still_beyond_the_source_under_itemize_all_is_omitted_and_every_entry_stays(
    job_dir: Path, tmp_path: Path, data_root: Path
) -> None:
    client = Scripted(lambda p, n: _msg(MERGED))
    sr = _sr(job_dir, _firm(tmp_path, "itemize_all"), data_root, client)
    d = _seed_entries(sr)
    assert summarize_stage.run(sr) == 0
    out = (d / "entries_scoped_final.md").read_text()
    assert out == "\n\n".join(e.strip() for e in [E_PRE, E_PRE2, E_POST])
    assert "Prior Medical History" not in out and len(client.calls) == 2
    text = "\n".join(limitations.section(sr.slug_dir, "alpha", lambda _m: None))
    assert "itemized entry by entry" in text


def test_a_summary_still_beyond_the_source_otherwise_itemizes_the_pre_incident_entries(
    job_dir: Path, tmp_path: Path, data_root: Path
) -> None:
    client = Scripted(lambda p, n: _msg(MERGED))
    sr = _sr(job_dir, _firm(tmp_path, "include"), data_root, client)
    d = _seed_entries(sr)
    assert summarize_stage.run(sr) == 0
    out = (d / "entries_scoped_final.md").read_text()
    assert out == "\n\n".join(e.strip() for e in [E_PRE, E_PRE2, E_POST])
    text = "\n".join(limitations.section(sr.slug_dir, "alpha", lambda _m: None))
    assert "itemized entry by entry" in text


@pytest.mark.parametrize(
    "heading", ["**PRIOR MEDICAL HISTORY:**", "## Pre-Incident Medical History", "Prior Medical History (summary)"]
)
def test_the_heading_is_set_in_code_so_build_doc_never_refuses_on_its_wording(
    job_dir: Path, tmp_path: Path, data_root: Path, heading: str
) -> None:
    body = GOOD.split("\n", 1)[1]
    client = Scripted(lambda p, n: _msg(heading + "\n" + body))
    sr = _sr(job_dir, _firm(tmp_path), data_root, client)
    d = _seed_entries(sr)
    assert summarize_stage.run(sr) == 0
    out = (d / "entries_scoped_final.md").read_text()
    assert out.startswith("Prior Medical History\n") and heading not in out
    prior, _rest = build_doc_stage.split_prior(out)
    assert prior.startswith("Prior Medical History") and "Chronic neck pain" in prior


def test_a_summary_with_no_heading_at_all_gets_one(job_dir: Path, tmp_path: Path, data_root: Path) -> None:
    body = GOOD.split("\n", 1)[1].strip()
    client = Scripted(lambda p, n: _msg(body))
    sr = _sr(job_dir, _firm(tmp_path), data_root, client)
    d = _seed_entries(sr)
    assert summarize_stage.run(sr) == 0
    out = (d / "entries_scoped_final.md").read_text()
    assert out.startswith("Prior Medical History\n\nChronic neck pain.")


# ---- row 16: repair accepts an honestly empty part ---------------------------------
def test_repair_accepts_a_low_yield_part_that_answers_in_the_house_shape(
    job_dir: Path, tmp_path: Path, data_root: Path
) -> None:
    """A truncated chunk of mostly fax covers and legal papers: one half
    honestly yields almost nothing. Below MIN_YIELD it was 'implausibly
    empty' at every split and the stage failed."""
    short = (
        "## ENTRIES\nnone in this chunk\n\n## INDEX\n\n## BILLING-DATES\nnone in this chunk\n\n"
        "## CONFLICTS / REFERENCED-BUT-ABSENT\nnone observed\n\n## FILES-SEEN\n"
        "=== FILE: f0.pdf (fileId f0) === nothing extractable: fax cover sheets\n"
    )
    client = Scripted(lambda p, n: _msg(short if "f0.pdf" in _content(p) else _map_for("f1.pdf", "f1")))
    sr = _sr(job_dir, _firm(tmp_path), data_root, client)
    d = sr.slug_dir / "runs" / "alpha"
    d.mkdir(parents=True)
    text = "".join(f"=== FILE: f{k}.pdf (fileId f{k}) ===\n[p.1] " + "fax cover " * 4000 + "\n" for k in range(2))
    (d / "chunk-01.txt").write_text(text)
    (d / "map-01.md").write_text("partial")
    (d / "usage.jsonl").write_text(json.dumps({"chunk": 1, "stop": "max_tokens"}) + "\n")
    assert repair_stage.run(sr) == 0
    assert len(client.calls) == 2 and (d / "map-01-1.md").read_text() == short


def test_a_compose_half_that_truncates_is_repaired_and_assembles(
    job_dir: Path, tmp_path: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An emptied chunk is halved by compose; one half then truncates. Repair
    looked only at whole-chunk labels, so the half "1.1" was never repaired
    and assemble refused over it."""
    from medchron.stages import assemble as assemble_stage

    phase = {"repair": False}

    def reply(p: dict, n: int) -> NS:
        c = _content(p)
        if n == 1:
            return _msg("x")  # the whole chunk empties: compose halves it
        if "ALPHA" in c and not phase["repair"]:
            return _msg(_map_for("alpha.pdf", "alpha"), stop="max_tokens")
        if "ALPHA" in c:
            return _msg(_map_for("alpha.pdf", "alpha"))
        return _msg(_map_for("beta.pdf", "beta"))

    client = Scripted(reply)
    sr = _sr(job_dir, _firm(tmp_path), data_root, client)
    page = "record text " * 40
    _unit_files(
        sr,
        {
            "alpha": "".join(f"[p.{k}] ALPHA {page}\n" for k in range(1, 9)),
            "beta": "".join(f"[p.{k}] BETA {page}\n" for k in range(1, 9)),
        },
    )
    monkeypatch.setattr(compose_stage.time, "sleep", lambda *_: None)
    assert compose_stage.run(sr) == 0
    d = sr.slug_dir / "runs" / "alpha"
    assert (d / "map-01-1.md").is_file() and (d / "map-01-2.md").is_file()
    phase["repair"] = True
    assert repair_stage.run(sr) == 0
    assert (d / "map-01-1.md.truncated").is_file() and not (d / "map-01-1.md").exists()
    assert (d / "map-01-2.md").is_file(), "the sibling half compose wrote is never touched"
    assert (d / "map-01-3.md").is_file() and (d / "map-01-4.md").is_file()
    assert assemble_stage.run(sr) == 0
    client.calls.clear()
    assert repair_stage.run(sr) == 0 and client.calls == []  # nothing left to repair


def _truncated_chunk(sr: StageRun, text: str) -> Path:
    d = sr.slug_dir / "runs" / "alpha"
    d.mkdir(parents=True)
    (d / "chunk-01.txt").write_text(text)
    (d / "map-01.md").write_text("partial")
    (d / "usage.jsonl").write_text(json.dumps({"chunk": 1, "stop": "max_tokens"}) + "\n")
    return d


def test_repair_names_a_single_page_that_still_truncates_unread_and_keeps_the_rest(
    job_dir: Path, tmp_path: Path, data_root: Path
) -> None:
    """Truncation is narrowed to one page; only a page that still truncates
    alone is named unread (never the pages around it), and the stage exits 0."""

    def reply(p: dict, n: int) -> NS:
        c = _content(p)
        if "DENSE" in c:
            return _msg(_map_for("f2.pdf", "f2"), stop="max_tokens")
        return _msg(_map_for("f1.pdf", "f1"))

    client = Scripted(reply)
    sr = _sr(job_dir, _firm(tmp_path), data_root, client)
    text = "=== FILE: f1.pdf (fileId f1) ===\n" + "".join(
        f"[p.{k}] {'DENSE table' if k == 3 else 'note'}\n" for k in range(1, 7)
    )
    d = _truncated_chunk(sr, text)
    assert repair_stage.run(sr) == 0
    unread = json.loads((d / "unread_pages.json").read_text())
    assert [(u["file"], u["pages"], u["reason"]) for u in unread] == [("f1.pdf", [3], "truncated")]
    assert (d / "map-01.md.truncated").is_file()
    text_out = "\n".join(limitations.section(sr.slug_dir, "alpha", lambda _m: None))
    assert '"f1.pdf" - p. 3' in text_out and "too dense" in text_out


def test_repair_names_shapeless_pages_unread_like_a_refusal(job_dir: Path, tmp_path: Path, data_root: Path) -> None:
    """A part that answers in no shape at every split is a refusal by
    another name: narrowed to its pages, those still shapeless named unread."""

    def reply(p: dict, n: int) -> NS:
        return _msg("x") if "MUTE" in _content(p) else _msg(_map_for("f1.pdf", "f1"))

    client = Scripted(reply)
    sr = _sr(job_dir, _firm(tmp_path), data_root, client)
    text = "".join(
        f"=== FILE: f{k}.pdf (fileId f{k}) ===\n[p.1] {'MUTE ' if k == 4 else ''}" + "text " * 200 + "\n"
        for k in range(6)
    )
    d = _truncated_chunk(sr, text)
    assert repair_stage.run(sr) == 0
    unread = json.loads((d / "unread_pages.json").read_text())
    assert [(u["file"], u["pages"], u["reason"]) for u in unread] == [("f4.pdf", [1], "empty")]


def test_repair_with_an_api_error_exits_the_infrastructure_code_and_discloses_nothing(
    job_dir: Path, tmp_path: Path, data_root: Path
) -> None:
    def reply(p: dict, n: int) -> NS:
        if "BROKEN" in _content(p):
            raise RuntimeError("upstream connect error")
        return _msg(_map_for("f1.pdf", "f1"))

    client = Scripted(reply)
    sr = _sr(job_dir, _firm(tmp_path), data_root, client)
    sr.doorway.retry_budget_s, sr.doorway.sleep = 0.0, lambda *_: None
    text = "".join(f"=== FILE: f{k}.pdf (fileId f{k}) ===\n[p.1] {'BROKEN' if k == 1 else 'ok'}\n" for k in range(3))
    d = _truncated_chunk(sr, text)
    assert repair_stage.run(sr) == compose_stage.INFRA_EXIT == 75
    assert not (d / "unread_pages.json").exists() and (d / "map-01.md").is_file()


def test_a_refused_single_file_is_halved_by_page_and_only_the_refused_page_is_unread(
    job_dir: Path, tmp_path: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One 6-page file whose page 3 is declined: the whole file is no longer
    named unread, only page 3, and the other five pages are composed."""

    def reply(p: dict, n: int) -> NS:
        if "ASSAULT-EXAM" in _content(p):
            return _msg("", stop="refusal")
        return _msg(_map_for("records.pdf", "records"))

    client = Scripted(reply)
    sr = _sr(job_dir, _firm(tmp_path), data_root, client)
    body = "".join(f"[p.{k}] {'ASSAULT-EXAM' if k == 3 else 'office visit'}\n" for k in range(1, 7))
    _unit_files(sr, {"records": body})
    monkeypatch.setattr(compose_stage.time, "sleep", lambda *_: None)
    assert compose_stage.run(sr) == 0
    d = sr.slug_dir / "runs" / "alpha"
    unread = json.loads((d / "unread_pages.json").read_text())
    assert [(u["file"], u["pages"], u["reason"]) for u in unread] == [("records.pdf", [3], "refused")]
    composed = [p for p in d.glob("map-01-*.md") if "Cervical strain" in p.read_text()]
    assert len(composed) >= 2  # the pages around page 3 were read
    # every page-half header names the file once, so coverage reads the name
    for p in d.glob("chunk-01-*.txt"):
        assert "[continued] [continued]" not in p.read_text()


# ---- email attachments that were found and not opened ------------------------------
def test_an_archive_attached_to_an_email_is_named_in_limitations(data_root: Path) -> None:
    """A .zip attached to an email: kind 'other', dropped from the fold and
    disclosed nowhere. Nothing inside it was read, and the firm is told so."""
    d = data_root / "example-matter"
    ix = msg_stage._Index(d, have={})
    ix.add_attachment("Records from Example Clinic", "records.zip", b"PK" + b"\0" * 30_000, container="email.msg")
    ix.add_attachment("Records from Example Clinic", "logo.gif", b"G" * 100, container="email.msg")
    msg_stage._write_report(d, True, [], ix.counts, [], 1, [], ix.dropped)
    text = "\n".join(limitations.section(d, None, lambda _m: None))
    assert '"records.zip"' in text and "Records from Example Clinic" in text
    assert "logo.gif" not in text  # never-a-record kinds stay out of the firm's list


# ---- billing disclosures written by the billing workstream -------------------------
def test_billing_gaps_and_dropped_rows_are_disclosed_without_a_figure(data_root: Path) -> None:
    d = data_root / "example-matter"
    (d / "billing_gaps.json").write_text(
        json.dumps([{"file": "ledger.pdf", "page": 4, "reason": "handwritten; every read failed"}])
    )
    (d / "billing_dropped_rows.json").write_text(
        json.dumps(
            [{"file": "ledger.pdf", "page": 2, "amount_text": "$612,000.00", "reason": "amount not printed on page"}]
        )
    )
    text = "\n".join(limitations.section(d, None, lambda _m: None))
    assert '"ledger.pdf" - p. 4' in text and "handwritten; every read failed" in text
    assert '"ledger.pdf" - p. 2' in text and "amount not printed on page" in text
    assert "612" not in text  # an unconfirmed amount is never shown as a figure
    (d / "billing_gaps.json").unlink()
    (d / "billing_dropped_rows.json").unlink()
    assert "billing" not in "\n".join(limitations.section(d, None, lambda _m: None)).lower()


LEFT_OUT = {
    "unit": "alpha",
    "entries": [
        {
            "date": "03/04/2026",
            "provider": "Example Clinic",
            "exhibit": 2,
            "pages": [5, 6],
            "reason": "names a person other than the client, so it was left out of this chronology",
        },
        {
            "date": "03/09/2026",
            "provider": "Example Imaging",
            "exhibit": None,
            "pages": [],
            "reason": "was read by the review as about a person other than the client, and its page does not "
            "identify the client, so it was left out of this chronology",
        },
    ],
}


def test_entries_the_review_left_out_are_named_by_date_provider_exhibit_and_pages(data_root: Path) -> None:
    d = data_root / "example-matter"
    (d / "out" / "alpha").mkdir(parents=True)
    (d / "out" / "alpha" / "review-left-out.json").write_text(json.dumps(LEFT_OUT))
    text = "\n".join(limitations.section(d, "alpha", lambda _m: None))
    assert (
        "* The entry dated 03/04/2026 (Example Clinic, Exhibit 2, pp. 5-6) names a person other than the client, "
        "so it was left out of this chronology." in text
    )
    assert "* The entry dated 03/09/2026 (Example Imaging) was read by the review" in text
    (d / "out" / "alpha" / "review-left-out.json").unlink()
    assert "left out of this chronology before delivery" not in "\n".join(
        limitations.section(d, "alpha", lambda _m: None)
    )


def test_refresh_puts_the_reviews_disclosure_into_the_built_document(data_root: Path) -> None:
    """build_doc writes the section before the review; the review leaves an
    entry out afterwards and re-renders from final-chronology.md, so the
    section is rewritten from the artifacts as they stand then."""
    d = data_root / "example-matter"
    rd = d / "runs" / "alpha"
    rd.mkdir(parents=True)
    doc = rd / "final-chronology.md"
    body = (
        "Client - Medical Chronology\n\n## Medical Chronology\n\n01/02/2026\nExample Clinic | x\n\nKept. (Exhibit 1)\n"
    )
    doc.write_text(body + "\n".join(limitations.section(d, "alpha", lambda _m: None)))
    assert "03/04/2026" not in doc.read_text()
    (d / "out" / "alpha").mkdir(parents=True)
    (d / "out" / "alpha" / "review-left-out.json").write_text(json.dumps(LEFT_OUT))
    assert limitations.refresh(doc, d, "alpha", lambda _m: None) is True
    out = doc.read_text()
    assert out.startswith(body) and out.count("## Records Reviewed and Limitations") == 1
    assert "The entry dated 03/04/2026 (Example Clinic, Exhibit 2, pp. 5-6)" in out
    assert limitations.refresh(doc, d, "alpha", lambda _m: None) is False  # idempotent


def test_a_joint_unit_reads_its_own_billing_files(data_root: Path) -> None:
    """billing_chart's suffix: on a joint matter each unit has its own file."""
    d = data_root / "example-matter"
    (d / "billing_dropped_rows-alpha.json").write_text(
        json.dumps([{"file": "a.pdf", "page": 1, "amount_text": "$1.00", "reason": "r-alpha"}])
    )
    (d / "billing_dropped_rows-beta.json").write_text(
        json.dumps([{"file": "b.pdf", "page": 1, "amount_text": "$1.00", "reason": "r-beta"}])
    )
    text = "\n".join(limitations.section(d, "alpha", lambda _m: None))
    assert "r-alpha" in text and "r-beta" not in text
