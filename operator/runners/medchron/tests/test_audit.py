"""The citation audit in-process: claims, anchors, the page index, renders,
verdicts through the doorway, one round with controls, the coverage gate,
the repair, and the loop end to end. Scripted client, real PDFs."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS


from medchron import config as config_mod, job as job_mod
from medchron.audit import anchors as AN, claims as CL, coverage, diag, page_text, render, repair, verify as VF
from medchron.audit.run import AuditPaths, Round
from medchron.stages import audit_loop
from medchron.stages.base import StageRun
from medchron_testkit import FakeSeat, make_pdf

PROSE = ("Patient seen on 01/02/2026 for neck pain after the collision. Blood pressure 120/80. "
         "Ibuprofen 400 mg prescribed. Riverside Imaging ordered an MRI. ") * 4


class Usage:
    input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens = 100, 20, 0, 0


def tool_msg(verdict: dict) -> NS:
    return NS(content=[NS(type="tool_use", input=verdict)], stop_reason="tool_use", usage=Usage())


def text_msg(text: str) -> NS:
    return NS(content=[NS(type="text", text=text)], stop_reason="end_turn", usage=Usage())


class Scripted:
    """reply(params, n) -> message. Records every request."""

    def __init__(self, reply):
        self.calls: list[dict] = []
        self._reply = reply
        self.messages = NS(create=self._create, stream=None, batches=None)

    def _create(self, **params):
        self.calls.append(params)
        return self._reply(params, len(self.calls))


def _sr(job_dir: Path, firm_config_path: Path, data_root: Path, client, log: list[str] | None = None) -> StageRun:
    job = job_mod.load(job_dir)
    cfg = config_mod.load(str(firm_config_path))
    lines = log if log is not None else []
    return StageRun(job=job, cfg=cfg, unit=job.units[0], slug_dir=data_root / "example-matter", decided={},
                    log=lines.append, seat_factory=lambda: FakeSeat([], [], {}), client_factory=lambda: client)


SUPPORTED = {"verdict": "SUPPORTED", "unsupported_assertions": [], "contradictions": [], "note": "the page says so"}
UNSUPPORTED = {"verdict": "UNSUPPORTED", "unsupported_assertions": ["blood pressure 120/80"], "contradictions": [],
               "note": "no vitals on the page"}


def _is_control(params: dict) -> bool:
    """A control re-runs a claim against a DIFFERENT exhibit's first page."""
    return "cited to Exhibit 2 p.1)" in params["messages"][0]["content"][-1]["text"]


def _claim_of(params: dict) -> str:
    text = params["messages"][0]["content"][-1]["text"]
    return text.split('"""')[1].strip()


# ---- claims and keys -------------------------------------------------------------
DOC = """# Chronology
## Medical Chronology

01/02/2026
Example Clinic | Patient Complaints & Limitations

The patient reports neck pain rated 6 of 10 since the Subject Incident. (Exhibit 1 - p. 1)

Blood pressure was recorded as 120/80 at this visit and the patient was given ibuprofen. (Exhibit 1 - p. 2)

[NTD: A portion of the records were handwritten and illegible.] (Exhibit 1 - p. 2)

MRI of the cervical spine shows a disc bulge at C5-6 per the radiologist. (Exhibit 2 - p. 1-2, machine transcription)

A citation into thin air that a reader could not follow at all. (Exhibit 1 - p. 9)

## Exhibit List
Exhibit 1 - Example Clinic
"""


def test_claims_extract_keys_skip_ntd_and_flag_out_of_range() -> None:
    body = CL.body_of(DOC)
    claims = CL.extract_claims(body, {1, 2})
    assert [c["page_spec"] for c in claims] == ["1", "2", "1-2", "9"]
    assert all(not c["claim"].startswith("[NTD") for c in claims)
    assert claims[2]["cite"].endswith("machine transcription)") and CL.parse_pages("1-2") == [1, 2]
    assert CL.parse_pages("5-1") == [5, 1] and CL.parse_pages("3, 7") == [3, 7]
    assert claims[0]["key"] == CL.claim_key(1, "1", claims[0]["claim"]) and len(claims[0]["key"]) == 16
    assert CL.extract_claims(body, {1}) and len(CL.extract_claims(body, {1})) == 3


def test_anchors_find_dates_numbers_drugs_and_propers() -> None:
    found = AN.find_anchors("On April 3, 2021 the patient took ibuprofen 400 mg; BP 120/80 at Riverside Imaging.")
    assert "date:2021-04-03" in found and "num:400mg" in found and "num:120/80" in found
    assert "drug:ibuprofen" in found and "proper:riverside" in found and "proper:imaging" not in found
    assert AN.found_on(["date:2021-04-03", "num:400mg"], "Visit 04/03/2021, ibuprofen 400 mg") == ["date:2021-04-03", "num:400mg"]


# ---- render and the page index --------------------------------------------------------
def test_render_is_cached_and_capped(tmp_path: Path) -> None:
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(make_pdf([PROSE, ""]))
    out = render.render(pdf, 1, tmp_path / "pages", "ex1")
    assert out is not None and out.stat().st_size > 1024 and out.name == "ex1_p1.png"
    assert render.render(pdf, 1, tmp_path / "pages", "ex1") == out and render.render(pdf, 3, tmp_path / "pages", "ex1") is None
    assert render.img_block(out)["source"]["media_type"] == "image/png"


def _exhibit_set(sr: StageRun, pages: list[str]) -> Path:
    """One exhibit PDF from one unit file, with the map, page_map and text
    the index needs to call its pages native."""
    d = sr.slug_dir
    out = d / "out" / "alpha"
    out.mkdir(parents=True, exist_ok=True)
    (d / "units").mkdir(exist_ok=True)
    (d / "text").mkdir(exist_ok=True)
    data = make_pdf(pages)
    (out / "Exhibit 1 - Example Clinic - 01-02-2026 (Medical Records).pdf").write_bytes(data)
    (d / "units" / "alpha.json").write_text(json.dumps([{"id": "f1", "name": "clinic note", "ext": ".pdf"}]))
    (d / "extracted.jsonl").write_text(json.dumps({"id": "f1", "name": "clinic note", "pages": len(pages), "chars": 100}) + "\n")
    (d / "text" / "f1.txt").write_text("".join(f"[p.{i}]\n{t}\n" for i, t in enumerate(pages, 1)))
    (out / "page_map.json").write_text(json.dumps([{"exhibit": 1, "total_pages": len(pages), "files": [
        {"file": "clinic note.pdf", "old_exhibit": 1, "start_page": 1, "pages": len(pages)}]}]))
    return out


def test_page_index_calls_a_native_text_page_eligible_and_a_blank_one_not(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    sr = _sr(job_dir, firm_config_path, data_root, None)
    _exhibit_set(sr, [PROSE, ""])
    ix = page_text.PageIndex(sr.slug_dir, "alpha")
    assert ix.npages(1) == 2 and ix.resolve(1, 2) == ("f1", 2)
    assert ix.classify(1, 1)[0] == "native" and ix.eligible(1, 1)
    assert ix.classify(1, 2)[0] == "short" and ix.classify(1, 3)[0] == "unmapped"
    assert "===== Exhibit 1 p.1 =====" in ix.window_text(1, [1, 2])
    assert page_text.content_agrees("Information25 N Via MonteWalnut", "Information 25 N Via Monte Walnut")
    ix.close()


# ---- verify through the doorway -----------------------------------------------------------
def test_verify_parses_the_tool_result_and_never_assumes_its_shape() -> None:
    assert VF.normalize({"verdict": "SUPPORTED"})["note"] == "(no note returned)"
    assert VF.normalize({"verdict": "MAYBE", "unsupported_assertions": "x"})["verdict"] == "PAGE_UNREADABLE"
    assert VF.text_verdict({"verdict": "SUPPORTED", "supporting_pages": [4]}, [2])["verdict"] == "SUPPORTED_WIDENED"
    assert VF.text_verdict({"verdict": "SUPPORTED", "supporting_pages": [2, 4]}, [2])["verdict"] == "SUPPORTED"


# ---- one round, image mode, with controls -------------------------------------------------------
def _write_doc(sr: StageRun, body_entries: str) -> None:
    rd = sr.slug_dir / "runs" / "alpha"
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "final-chronology.md").write_text("# Chronology\n## Medical Chronology\n\n" + body_entries + "\n## Exhibit List\n")


def test_round_records_verdicts_controls_and_out_of_range(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    def reply(p, n):
        return tool_msg(UNSUPPORTED if _is_control(p) else SUPPORTED)

    client = Scripted(reply)
    log: list[str] = []
    sr = _sr(job_dir, firm_config_path, data_root, client, log=log)
    out = _exhibit_set(sr, [PROSE, PROSE])
    (out / "Exhibit 2 - Riverside Imaging - 01-09-2026 (Medical Records).pdf").write_bytes(make_pdf([PROSE]))
    entries = "\n\n".join(f"01/0{i}/2026\nExample Clinic | Medical Diagnoses\n\nA supported claim number {i} about the neck. (Exhibit 1 - p. 1)"
                          for i in range(1, 9))
    entries += "\n\n01/09/2026\nExample Clinic | Medical Diagnoses\n\nA claim citing a page that does not exist. (Exhibit 1 - p. 7)"
    _write_doc(sr, entries)
    paths = AuditPaths(sr.slug_dir, "alpha")
    rc = Round(sr.doorway, "claude-sonnet-5", paths, log.append, mode="image", workers=2).execute()
    assert rc == 1                                          # the out-of-range claim is a problem
    rows = CL.read_rows(paths.results)
    real = [r for r in rows if r["kind"] == "real"]
    assert len(real) == 9 and sum(1 for r in real if r["verdict"] == "SUPPORTED") == 8
    assert [r for r in real if r["verdict"] == "PAGE_OUT_OF_RANGE"][0]["bad_pages"] == [7]
    ctl = [r for r in rows if r["kind"].startswith("control")]
    assert len(ctl) == 1 and ctl[0]["verdict"] == "UNSUPPORTED" and "vs Ex2" in ctl[0]["kind"]
    assert any("DISCRIMINATES" in line for line in log)
    # a rubber-stamping verifier makes the round INVALID
    client2 = Scripted(lambda p, n: tool_msg(SUPPORTED))
    sr2 = _sr(job_dir, firm_config_path, data_root, client2)
    paths.results.unlink()
    assert Round(sr2.doorway, "claude-sonnet-5", paths, lambda *_: None, mode="image", workers=1).execute() == 2
    # resume: nothing is re-billed for keys already on disk
    n_before = len(client.calls)
    Round(sr.doorway, "claude-sonnet-5", paths, log.append, mode="image", workers=1).execute()
    assert len(client.calls) == n_before


def test_double_sweep_guard_refuses_orphaned_keys(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    sr = _sr(job_dir, firm_config_path, data_root, Scripted(lambda p, n: tool_msg(SUPPORTED)))
    _exhibit_set(sr, [PROSE])
    _write_doc(sr, "01/02/2026\nExample Clinic | Medical Diagnoses\n\nA claim about the neck that is long enough. (Exhibit 1 - p. 1)")
    paths = AuditPaths(sr.slug_dir, "alpha")
    body = CL.body_of(paths.doc.read_text())
    CL.append_row(paths.results, {"key": "deadbeefdeadbeef", "kind": "real", "doc_sha": CL.doc_sha_of(body), "verdict": "SUPPORTED"})
    assert Round(sr.doorway, "claude-sonnet-5", paths, lambda *_: None).execute() == 3
    assert Round(sr.doorway, "claude-sonnet-5", paths, lambda *_: None, force=True).execute() == 0


# ---- text mode ------------------------------------------------------------------------------
def test_text_mode_audits_native_pages_against_a_cached_window(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    def reply(p, n):
        tools = p.get("tools") or []
        if tools and "supporting_pages" in tools[0]["input_schema"]["properties"]:
            return tool_msg({**SUPPORTED, "supporting_pages": [1]})
        return tool_msg(SUPPORTED)

    client = Scripted(reply)
    log: list[str] = []
    sr = _sr(job_dir, firm_config_path, data_root, client, log=log)
    _exhibit_set(sr, [PROSE, PROSE, PROSE])
    _write_doc(sr, "01/02/2026\nExample Clinic | Medical Diagnoses\n\nBlood pressure 120/80 and ibuprofen 400 mg on 01/02/2026. (Exhibit 1 - p. 1)")
    paths = AuditPaths(sr.slug_dir, "alpha")
    assert Round(sr.doorway, "claude-sonnet-5", paths, log.append, mode="text", workers=1).execute() == 0
    row = [r for r in CL.read_rows(paths.results) if r["kind"] == "real"][0]
    # cited p.1, its eligible neighbour p.2, and p.3 two away because it carries two of the claim's anchors
    assert row["mode"] == "text" and row["window"] == [1, 2, 3] and row["text_then_image"] is False
    assert "num:120/80" in row["anchors"] and "num:120/80" in row["anchors_found"]
    p = client.calls[0]
    assert p["messages"][0]["content"][0].get("cache_control") and "===== Exhibit 1 p.1 =====" in p["messages"][0]["content"][0]["text"]
    assert p["system"] and p["tool_choice"]["name"] == "record_verdict"


# ---- coverage, repair, the loop ----------------------------------------------------------------
def test_repair_helpers() -> None:
    assert repair.compress([1, 2, 3, 7]) == "1-3, 7"
    assert repair.widen_cite("(Exhibit 3 - p. 4)", [3, 4, 5]) == "(Exhibit 3 - p. 3-5)"
    assert repair.widen_cite("(Exhibit 3 - p. 9, 14, machine transcription)", [9, 10]) == "(Exhibit 3 - p. 9-10, machine transcription)"
    assert repair.widen_cite("(Exhibit 3)", [2]) == "(Exhibit 3 - p. 2)"
    assert repair.replace_in("a\nb\n", "a", "") == ("b\n", True) and repair.replace_in("x", "q", "y") == ("x", False)


def test_loop_repairs_then_passes_the_gate(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    """Round 1: one claim UNSUPPORTED. Repair weakens it. Round 2: the new
    key is audited and SUPPORTED. The gate passes."""
    state = {"repaired": False}

    def reply(p, n):
        if p.get("system") and "correct one sentence-group" in p["system"][0]["text"]:
            state["repaired"] = True
            return text_msg("Blood pressure was recorded at this visit. (Exhibit 1 - p. 2)")
        if _is_control(p):
            return tool_msg(UNSUPPORTED)
        if "120/80" in _claim_of(p) and not state["repaired"]:
            return tool_msg(UNSUPPORTED)
        return tool_msg(SUPPORTED)

    client = Scripted(reply)
    log: list[str] = []
    sr = _sr(job_dir, firm_config_path, data_root, client, log=log)
    _exhibit_set(sr, [PROSE, PROSE])
    entries = ("01/02/2026\nExample Clinic | Medical Diagnoses\n\n"
               "The patient reports neck pain rated 6 of 10 since the incident. (Exhibit 1 - p. 1)\n\n"
               "Blood pressure was recorded as 120/80 at this visit. (Exhibit 1 - p. 2)")
    rd = sr.slug_dir / "runs" / "alpha"
    _write_doc(sr, entries)
    (rd / "entries_scoped_final.md").write_text(entries)
    assert audit_loop.run(sr) == 0
    doc = (rd / "final-chronology.md").read_text()
    assert "120/80" not in doc and "Blood pressure was recorded at this visit. (Exhibit 1 - p. 2)" in doc
    assert "120/80" not in (rd / "entries_scoped_final.md").read_text()
    edits = CL.read_rows(sr.slug_dir / "out" / "alpha" / "repair-edits.jsonl")
    assert edits[-1]["action"] == "repair" and "120/80" in edits[-1]["old"]
    ok, summary = coverage.check(AuditPaths(sr.slug_dir, "alpha"), log.append)
    assert ok and summary["live"] == 2 and summary["never"] == 0
    assert any("GATE PASS" in line for line in log)


def test_loop_drops_residual_at_the_cap_and_holds_on_a_never_supported_claim(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    def reply(p, n):
        if p.get("system") and "correct one sentence-group" in p["system"][0]["text"]:
            return text_msg("Still says 120/80 which the page does not. (Exhibit 1 - p. 2)")
        if _is_control(p):
            return tool_msg(UNSUPPORTED)
        return tool_msg(UNSUPPORTED if "120/80" in _claim_of(p) else SUPPORTED)

    sr = _sr(job_dir, firm_config_path, data_root, Scripted(reply))
    _exhibit_set(sr, [PROSE, PROSE])
    entries = ("01/02/2026\nExample Clinic | Medical Diagnoses\n\n"
               "The patient reports neck pain rated 6 of 10 since the incident. (Exhibit 1 - p. 1)\n\n"
               "Blood pressure was recorded as 120/80 at this visit. (Exhibit 1 - p. 2)")
    _write_doc(sr, entries)
    assert audit_loop.run(sr) == 0            # residual dropped at the cap; the survivor passes the gate
    doc = (sr.slug_dir / "runs" / "alpha" / "final-chronology.md").read_text()
    assert "120/80" not in doc and "neck pain rated 6 of 10" in doc
    edits = CL.read_rows(sr.slug_dir / "out" / "alpha" / "repair-edits.jsonl")
    assert edits[-1]["action"] == "drop-residual"


def test_diag_page_remap_and_rekey(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    sr = _sr(job_dir, firm_config_path, data_root, None)
    out = _exhibit_set(sr, [PROSE, PROSE, PROSE])
    pdf = next(out.glob("Exhibit 1 - *.pdf"))
    Path(str(pdf) + ".orig").write_bytes(pdf.read_bytes())
    (sr.slug_dir / "strip_result-alpha.json").write_text(json.dumps({"drops": {"1": [2]}}))
    remap = diag.page_remap(sr.slug_dir, "alpha")
    assert remap == {1: {1: 1, 3: 2}}
    assert diag.remap_pages([3], remap, 1) == [2] and diag.remap_pages([2], remap, 1) is None
    old = {"key": CL.claim_key(1, "3", "same words"), "kind": "real", "exhibit": 1, "page_spec": "3", "pages": [3],
           "claim": "same words", "verdict": "SUPPORTED"}
    live = [{"key": CL.claim_key(1, "2", "same words"), "exhibit": 1, "page_spec": "2", "pages": [2], "claim": "same words"}]
    results = sr.slug_dir / "out" / "alpha" / "audit-results.jsonl"
    CL.append_row(results, old)
    assert diag.rekey_rows(results, CL.read_rows(results), live, remap, doc_sha="abc") == 1
    rows = CL.read_rows(results)
    assert rows[-1]["key"] == live[0]["key"] and rows[-1]["rekeyed_from"] == old["key"] and rows[-1]["doc_sha"] == "abc"
