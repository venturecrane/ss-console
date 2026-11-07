"""The read-back scores a filed firm document against its own record, and each of
its five tests can fail (design 2026-11-06, piece 10; ``jobs/library_readback``).

EVERY JOB HERE IS A REAL JOB: the drafting and demand kits run end to end
(``test_drafting_library_pick._run``, ``test_demand_library_pick._run``,
``test_drafting_attorneys._run``) and the scorer reads the folder they leave.
A correct document passes each test; then ONE defect is planted per test,
either in the filed document (its bytes rewritten and the manifest's sha256 with
them, as if the job had filed that) or in what the record says was used, and
that test, and no other, fails:

* (a) the document made in another file than the record names;
* (b) a signature block that is not the roster's for the recorded attorney;
* (c) never fails: not recorded until a record names a reader;
* (d) a firm's edit the record says was used, not carried by the document;
* (e) a forbidden citation in the filed document, and a layout rule in force
  the document does not hold.

Every firm, person, file and word is made up.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import zipfile
from pathlib import Path

import docx
import pytest

import library_testkit as lt
import test_attorney_roster as tar
from drafting_testkit import firm_data
from jobs.library_readback import CODE, FAIL, NOT_APPLICABLE, NOT_RECORDED, PASS, SCORE_NAME, TESTS
from jobs.library_readback import cli, lane as rb_lane, measure
from jobs.library_readback.score import MANIFEST, score_job
from operator_connector_sdk.library import MARKED, copies_dir, roster as roster_mod
from operator_connector_sdk.library.caller import record_problems
from test_drafting_brief_base import _job_brief, accepted, tmpl  # noqa: F401 - pytest fixtures used by name
from test_drafting_firm_rules import _install as install_layout_rule
from test_drafting_lane import _lane, _submit
from test_drafting_library_pick import _accept, _env, _run, _styles_file, spec  # noqa: F401 - fixtures used by name
from test_library_edit import word_save

ABSENT = roster_mod.Roster(roster_mod.ABSENT)
CITES = {"mediation_brief": {"allow": ["deposition-quote", "legal-authority"]}}


def _verdicts(score: dict) -> dict[str, str]:
    return {t: score["tests"][t]["verdict"] for t in TESTS}


def _measured(score: dict, test: str) -> dict:
    return score["tests"][test]["documents"][0]["measured"]


def _filed_path(data: Path) -> Path:
    rows = json.loads((data / MANIFEST).read_text())
    return Path(next(r["local_path"] for r in rows if r["role"] in ("draft", "demand")))


def _refile(data: Path, blob: bytes) -> None:
    """Plant a defect in the FILED document: its bytes, and the manifest's sha256 with them."""
    rows = json.loads((data / MANIFEST).read_text())
    for r in rows:
        if r["role"] in ("draft", "demand"):
            Path(r["local_path"]).write_bytes(blob)
            r["sha256"] = hashlib.sha256(blob).hexdigest()
    (data / MANIFEST).write_text(json.dumps(rows))


def _with_paragraph(blob: bytes, text: str) -> bytes:
    """``blob`` with one more body paragraph carrying ``text``."""
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(blob)) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "word/document.xml":
                i = data.rfind(b"<w:sectPr")
                data = data[:i] + f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>".encode() + data[i:]
            out.writestr(info, data)
    return buf.getvalue()


def _record(data: Path, change) -> None:
    rec = lt.record(data)
    change(rec)
    assert record_problems(rec) == [], "a planted record must still be a record the toolkit reads"
    (data / "library-resolution.json").write_text(json.dumps(rec))


def _install_copy(spec_dir: Path, blob: bytes) -> str:
    sha = hashlib.sha256(blob).hexdigest()
    (copies_dir(spec_dir) / f"{sha}.docx").write_bytes(blob)
    return sha


@pytest.fixture
def styles_job(spec, tmp_path, monkeypatch):
    f = _styles_file("ACCEPTED EXAMPLE LAW")
    _accept(monkeypatch, spec, f)
    r, v, log = _run(tmp_path / "styles")
    assert v.outcome == "delivered", (v.stage, v.reason, log[-5:])
    return r.data, f


# ---- (a) made in the recorded file --------------------------------------------------------


def test_a_brief_made_in_its_accepted_styles_file_passes_and_nothing_is_made_up(styles_job, spec):
    data, _f = styles_job
    s = score_job(data.parent, spec_dir=spec, roster=ABSENT)
    assert _verdicts(s) == {
        "a_made_in_file": PASS,
        "b_attorney": NOT_APPLICABLE,  # the kit's matter names no attorney block
        "c_reader": NOT_RECORDED,
        "d_firm_edit": NOT_APPLICABLE,
        "e_rules": NOT_APPLICABLE,
    }
    assert s["failed"] == [] and _measured(s, "a_made_in_file")["contract"] == "styles"


def test_a_brief_made_in_another_file_than_its_record_names_fails_a_only(styles_job, spec):
    data, _f = styles_job
    other = _install_copy(spec, _styles_file("ANOTHER EXAMPLE FIRM"))
    _record(data, lambda r: (r["base"].update(sha256=other), r["resolution"]["file"].update(sha256=other)))
    s = score_job(data.parent, spec_dir=spec, roster=ABSENT)
    assert s["failed"] == ["a_made_in_file"]
    assert any(d.startswith("header_footer_changed:") for d in _measured(s, "a_made_in_file")["differs"])


def test_the_file_it_was_made_in_missing_from_the_seat_is_not_recorded_never_a_pass(styles_job, spec):
    data, f = styles_job
    (copies_dir(spec) / f"{hashlib.sha256(f).hexdigest()}.docx").unlink()
    s = score_job(data.parent, spec_dir=spec, roster=ABSENT)
    assert _verdicts(s)["a_made_in_file"] == NOT_RECORDED


def test_a_brief_made_in_its_accepted_marked_file_passes_and_an_unfilled_place_fails(spec, tmp_path, monkeypatch, tmpl):
    _accept(monkeypatch, spec, tmpl.bytes, contract=MARKED)
    r, v, log = _run(tmp_path / "marked", base=tmpl.bytes, draft=_job_brief())
    assert v.outcome == "delivered", (v.stage, v.reason, log[-5:])
    s = score_job(r.data.parent, spec_dir=spec, roster=ABSENT)
    assert _verdicts(s)["a_made_in_file"] == PASS
    assert _measured(s, "a_made_in_file")["marks_left"] == 0 == _measured(s, "a_made_in_file")["fixed_missing"]
    _refile(r.data, _with_paragraph(_filed_path(r.data).read_bytes(), "[[body.p]]"))
    s = score_job(r.data.parent, spec_dir=spec, roster=ABSENT)
    assert s["failed"] == ["a_made_in_file"] and "marks_left" in _measured(s, "a_made_in_file")["differs"]


def test_a_filed_file_changed_after_filing_is_never_scored_as_the_filed_one(styles_job, spec):
    data, _f = styles_job
    _filed_path(data).write_bytes(b"not what was filed")
    s = score_job(data.parent, spec_dir=spec, roster=ABSENT)
    assert set(_verdicts(s).values()) == {FAIL} and s["documents"][0]["state"] == "changed"


# ---- (b) the attorney --------------------------------------------------------------------


def _drafting_with_attorney(tmp_path: Path):
    """A brief whose matter names the kit's lead attorney, drafted (scripted) with
    the authored block where the court's paper put an attorney block."""
    from drafting_testkit import BRIEF, ScriptedClient, make_inputs, make_job, seat_with, standard_docs
    from jobs.drafting.run import DraftingRun
    from jobs_testkit import PRICING

    root = tmp_path / "atty"
    root.mkdir()
    (root / "pricing.json").write_text(json.dumps(PRICING), encoding="utf-8")
    inputs = make_inputs(root / "inputs", attorneys=[{"email": "lead@firm.example", "block": tar.LEAD_BLOCK}])
    seat = seat_with(standard_docs())
    seat.record = {**seat.record, "attorney_email": "lead@firm.example"}
    draft = BRIEF.replace("Alpha Example, Esq.\nAttorneys for Plaintiff", "\n".join(tar.LEAD_BLOCK))
    r = DraftingRun(make_job(root / "job"), inputs_dir=str(inputs), pricing=str(root / "pricing.json"),
                    seat_factory=lambda: seat, client=ScriptedClient(draft=draft), log=lambda m: None,
                    readback_pause=0.0)  # fmt: skip
    v = r.run()
    assert v.outcome == "delivered", (v.stage, v.reason)
    return r


def _roster(doc: dict) -> roster_mod.Roster:
    return roster_mod.parse(json.dumps(doc))


def test_a_brief_signed_with_the_rosters_block_passes_and_another_block_fails(tmp_path, monkeypatch):
    monkeypatch.setenv(roster_mod.DIR_ENV, str(tmp_path / "no-roster"))
    r = _drafting_with_attorney(tmp_path)
    ok = score_job(r.data.parent, roster=_roster(tar.roster_doc()))
    assert _verdicts(ok)["b_attorney"] == PASS
    assert _measured(ok, "b_attorney") == {
        "lines": len(tar.LEAD_BLOCK), "found": len(tar.LEAD_BLOCK), "missing": [], "source": "roster caption_block",
        "tie": "roster",
    }  # fmt: skip
    moved = [*tar.LEAD_BLOCK[:1], "ANOTHER EXAMPLE FIRM, LLP", *tar.LEAD_BLOCK[2:]]
    bad = score_job(r.data.parent, roster=_roster(tar.roster_doc(caption_block=moved)))
    assert bad["failed"] == ["b_attorney"] and _measured(bad, "b_attorney")["missing"] == [2]


def test_with_no_roster_the_block_the_job_chose_is_measured_and_says_it_cannot_be_tied(tmp_path, monkeypatch):
    monkeypatch.setenv(roster_mod.DIR_ENV, str(tmp_path / "no-roster"))
    r = _drafting_with_attorney(tmp_path)
    s = score_job(r.data.parent, roster=ABSENT)
    assert _verdicts(s)["b_attorney"] == PASS
    assert _measured(s, "b_attorney")["tie"] == "not checkable without a roster"


def test_a_demand_signed_as_the_roster_says_passes_and_a_changed_title_fails(tmp_path, monkeypatch):
    from test_demand_library_pick import _run as run_demand

    monkeypatch.setenv(roster_mod.DIR_ENV, str(tmp_path / "no-roster"))
    r, v, log, _client = run_demand(tmp_path / "demand")
    assert v.outcome == "delivered", (v.reason, log[-5:])
    inputs = str(tmp_path / "demand" / "inputs")
    ok = score_job(r.data.parent, roster=_roster(tar.roster_doc()), demand_inputs=inputs)
    assert _verdicts(ok)["b_attorney"] == PASS and _verdicts(ok)["a_made_in_file"] == PASS
    retitled = tar.roster_doc(signs={"demand": {**tar.DEMAND_SIG, "title": "Senior Trial Counsel"}})
    bad = score_job(r.data.parent, roster=_roster(retitled), demand_inputs=inputs)
    assert bad["failed"] == ["b_attorney"] and _measured(bad, "b_attorney")["missing"] == [2]


# ---- (c) the reader: recorded or not, never failed ----------------------------------------


def test_the_reader_is_not_recorded_until_a_record_names_one_and_never_fails(styles_job, spec):
    data, _f = styles_job
    assert _verdicts(score_job(data.parent, spec_dir=spec, roster=ABSENT))["c_reader"] == NOT_RECORDED
    rec = lt.record(data)
    rec["reader"] = {"reader": "mediator", "profile": "court"}
    (data / "library-resolution.json").write_text(json.dumps(rec))
    s = score_job(data.parent, spec_dir=spec, roster=ABSENT)
    assert _verdicts(s)["c_reader"] == PASS and _measured(s, "c_reader") == {"reader": "mediator", "profile": "court"}


# ---- (d) the firm's own edit -------------------------------------------------------------


def test_a_firms_edit_used_in_the_draft_passes_and_one_not_carried_fails(spec, tmp_path, monkeypatch, styles_job):
    plain_data, _plain_f = styles_job
    f = _styles_file("EDITED EXAMPLE LAW")
    lib = _accept(monkeypatch, spec, f)
    lib.save("acc-1", word_save(f, left="2333"))
    r, v, log = _run(tmp_path / "edited")
    assert v.outcome == "delivered", (v.stage, v.reason, log[-5:])
    assert lt.record(r.data)["firm_edit"]["status"] == "used"
    ok = score_job(r.data.parent, spec_dir=spec, roster=ABSENT)
    assert _verdicts(ok)["d_firm_edit"] == PASS == _verdicts(ok)["a_made_in_file"]
    # The planted defect: a brief made as if the firm's save were not there (its margins).
    blob = docx.Document(io.BytesIO(_filed_path(plain_data).read_bytes()))
    blob.sections[0].header.paragraphs[0].text = "EDITED EXAMPLE LAW"
    out = io.BytesIO()
    blob.save(out)
    _refile(r.data, out.getvalue())
    bad = score_job(r.data.parent, spec_dir=spec, roster=ABSENT)
    assert "d_firm_edit" in bad["failed"] and "page_margins" in _measured(bad, "d_firm_edit")["differs"]


# ---- (e) the rules in force ----------------------------------------------------------------


def test_a_forbidden_citation_in_the_filed_brief_fails_e_and_its_absence_passes(styles_job, spec, tmp_path):
    from jobs.drafting import firm as drafting_firm

    data, _f = styles_job
    inputs = tmp_path / "cite-inputs"
    from drafting_testkit import make_inputs

    make_inputs(inputs, citations=CITES)
    assert drafting_firm.load(str(inputs)).data["citations"] == CITES
    ok = score_job(data.parent, spec_dir=spec, roster=ABSENT, drafting_inputs=str(inputs))
    assert _verdicts(ok)["e_rules"] == PASS
    _refile(
        data, _with_paragraph(_filed_path(data).read_bytes(), "The visit is noted (FILE: ER record 1-15-26.pdf, p. 1).")
    )
    bad = score_job(data.parent, spec_dir=spec, roster=ABSENT, drafting_inputs=str(inputs))
    assert bad["failed"] == ["e_rules"]
    (check,) = _measured(bad, "e_rules")["checked"]
    assert check["rule_id"] == "citations" and not check["holds"] and check["problems"] == 1


def test_a_layout_rule_in_force_is_read_back_and_a_document_not_holding_it_fails(spec, tmp_path, monkeypatch):
    install_layout_rule(spec, "Two blank lines before the court name.")
    r, v, log = _run(tmp_path / "ruled")
    assert v.outcome == "delivered", (v.stage, v.reason, log[-5:])
    ok = score_job(r.data.parent, spec_dir=spec, roster=ABSENT)
    (check,) = _measured(ok, "e_rules")["checked"]
    assert check["key"] == "front_court_gap_lines" and check["holds"], ok["tests"]["e_rules"]
    state = json.loads((r.data / "state.json").read_text())
    state["firm_rules"]["rows"][0]["value"] = 4  # the rule says four; the document was laid with two
    (r.data / "state.json").write_text(json.dumps(state))
    bad = score_job(r.data.parent, spec_dir=spec, roster=ABSENT)
    assert bad["failed"] == ["e_rules"]


def test_a_wording_rule_is_listed_by_id_and_never_scored(styles_job, spec):
    data, _f = styles_job
    (data / "wording-rules.json").write_text(json.dumps({"kind": "mediation_brief", "attorney": None,
                                                         "rules": [{"rule_id": "0000000a", "wording": ["x"]}]}))  # fmt: skip
    s = score_job(data.parent, spec_dir=spec, roster=ABSENT)
    assert _verdicts(s)["e_rules"] == NOT_APPLICABLE
    assert [x["rule_id"] for x in _measured(s, "e_rules")["not_scored"] if x["kind"] == "wording"] == ["0000000a"]


# ---- run by itself, after a delivered job; and read per seat --------------------------------


def _lane_job(tmp_path: Path, data: Path, job_id: str):
    lane, client = _lane(tmp_path / "lane")
    dest = lane.job_dir(job_id)
    dest.mkdir(parents=True)
    shutil.copytree(data, dest / "data")
    rows = json.loads((dest / "data" / MANIFEST).read_text())
    for row in rows:
        row["local_path"] = str(dest / "data" / MANIFEST.parent / row["name"])
    (dest / "data" / MANIFEST).write_text(json.dumps(rows))
    return lane, client


def _alerts(client) -> list[dict]:
    return [q for q in client.requests if q["action"] == "delivery_proof_alert"]


@pytest.mark.parametrize("mode,pages", [(None, False), ("observe", False), ("enforce", True)])
def test_the_daemon_scores_a_delivered_job_and_pages_only_when_enforcing(
    styles_job, spec, tmp_path, monkeypatch, mode, pages
):
    data, _f = styles_job
    other = _install_copy(spec, _styles_file("ANOTHER EXAMPLE FIRM"))
    _record(data, lambda r: (r["base"].update(sha256=other), r["resolution"]["file"].update(sha256=other)))
    cfg = tmp_path / "customer.yaml"
    cfg.write_text(f"logic_in_code:\n  library_readback: {mode}\n" if mode else "customer_id: example\n")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(cfg))
    monkeypatch.setenv(roster_mod.DIR_ENV, str(tmp_path / "no-roster"))
    job = "01READBACK0000000000000001"
    lane, client = _lane_job(tmp_path, data, job)
    assert rb_lane.after(lane, job, "delivered") == ("paged" if pages else "failed_observed")
    score = json.loads((lane.job_dir(job) / "data" / SCORE_NAME).read_text())
    assert score["failed"] == ["a_made_in_file"] and score["mode"] == (mode or "observe")
    assert [a["code"] for a in _alerts(client)] == ([CODE] if pages else [])
    assert rb_lane.after(lane, job, "held") == "" and rb_lane.after(lane, job, "failed") == ""
    line = json.loads((lane.run_dir / rb_lane.LOG).read_text().splitlines()[0])
    assert line["job_id"] == job and line["verdicts"]["a_made_in_file"] == FAIL


def test_a_real_drafting_lane_tick_scores_the_job_it_delivered(styles_job, spec, tmp_path, monkeypatch):
    """The hook is wired: the lane's own tick, its fake child delivering, leaves the score."""
    data, _f = styles_job
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(tmp_path / "absent.yaml"))
    monkeypatch.setenv(roster_mod.DIR_ENV, str(tmp_path / "no-roster"))
    lane, client = _lane(tmp_path / "ticked")
    jid = _submit(lane, client)
    shutil.copytree(data, lane.job_dir(jid) / "data")
    assert lane.tick() == "delivered"
    score = json.loads((lane.job_dir(jid) / "data" / SCORE_NAME).read_text())
    assert score["job_id"] == jid and score["mode"] == "observe" and _alerts(client) == []


def test_the_status_reads_every_scored_job_by_id_even_after_its_folder_is_wiped(
    styles_job, spec, tmp_path, monkeypatch, capsys
):
    data, _f = styles_job
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(tmp_path / "absent.yaml"))
    monkeypatch.setenv(roster_mod.DIR_ENV, str(tmp_path / "no-roster"))
    lane, _client = _lane_job(tmp_path, data, "01READBACK0000000000000002")
    assert rb_lane.after(lane, "01READBACK0000000000000002", "delivered") == "scored"
    shutil.rmtree(lane.job_dir("01READBACK0000000000000002"))  # the daemon's 72-hour wipe
    args = type("A", (), {"run_dir": str(lane.run_dir), "json": True, "action": "status"})()
    assert cli.status(args) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["jobs"] == 1
    assert got["tests"]["a_made_in_file"] == {
        "counts": {PASS: 1, FAIL: 0, NOT_APPLICABLE: 0, NOT_RECORDED: 0},
        "newest": {PASS: "01READBACK0000000000000002"},
    }


def test_replay_scores_a_saved_job_folder_and_writes_nothing(styles_job, spec, tmp_path, capsys):
    data, _f = styles_job
    before = sorted(p.name for p in data.iterdir())
    import argparse

    p = argparse.ArgumentParser()
    cli.add(p.add_subparsers(dest="cmd"))
    args = p.parse_args(["library-readback", "replay", str(data.parent), "--spec", str(spec)])
    assert args.fn(args) == 0
    assert json.loads(capsys.readouterr().out)["tests"]["a_made_in_file"]["verdict"] == PASS
    assert sorted(p.name for p in data.iterdir()) == before


def test_the_package_measure_names_parts_and_styles_never_words():
    a = _styles_file("FIRST EXAMPLE FIRM")
    b = word_save(_styles_file("SECOND EXAMPLE FIRM"), left="2333", font="Example Serif")
    got = measure.compare(a, b, "styles")
    assert "page_margins" in got["differs"]
    assert not any(re.search(r"EXAMPLE|FIRM", d) for d in got["differs"])
    assert firm_data()  # the kit loads: the fixtures above are its made-up firm
