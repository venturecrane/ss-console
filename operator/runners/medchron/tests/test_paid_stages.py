"""vision, billing_extract and build_units in-process, against a scripted SDK
client (no network) and real PDFs."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from medchron import config as config_mod, decisions, job as job_mod
from medchron.stages import billing as billing_stage, units as units_stage, vision as vision_stage
from medchron.stages import transcript
from medchron.stages.base import StageRun
from medchron_testkit import FakeSeat, make_pdf

PROSE = "Patient seen in clinic today for follow up of neck pain after the collision. " * 8


class Usage:
    input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens = 100, 20, 0, 0


def _msg(text: str, stop: str = "end_turn") -> NS:
    return NS(content=[NS(type="text", text=text)], stop_reason=stop, usage=Usage())


class Scripted:
    """messages.create answers from `reply(params)`; batches never used unless
    the levers name the stage."""

    def __init__(self, reply):
        self.calls: list[dict] = []
        self._reply = reply
        self.messages = NS(create=self._create, batches=None)

    def _create(self, **params):
        self.calls.append(params)
        return self._reply(params)


def _sr(job_dir: Path, firm_config_path: Path, data_root: Path, client, log: list[str] | None = None) -> StageRun:
    job = job_mod.load(job_dir)
    cfg = config_mod.load(str(firm_config_path))
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


def _seed_scan(sr: StageRun, files: dict[str, tuple[str, bytes]]) -> None:
    raw = sr.slug_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    with (sr.slug_dir / "raw_manifest.jsonl").open("w") as fh:
        for fid, (name, data) in files.items():
            p = raw / f"{fid}.pdf"
            p.write_bytes(data)
            fh.write(
                json.dumps(
                    {
                        "id": fid,
                        "name": name,
                        "ext": ".pdf",
                        "folder": "/MEDICAL",
                        "ok": True,
                        "path": str(p),
                        "sha256": fid * 8,
                    }
                )
                + "\n"
            )
    (sr.slug_dir / "scan_queue.json").write_text(
        json.dumps(
            [
                {"id": fid, "name": name, "folder": "/MEDICAL", "ext": ".pdf", "scan": True}
                for fid, (name, _) in files.items()
            ]
        )
    )


# ---- vision --------------------------------------------------------------------
def test_vision_transcribes_every_page_checkpoints_and_ledgers_pages(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    client = Scripted(lambda p: _msg("Dr. Example saw the patient on 01/02/2026. [illegible] signature."))
    sr = _sr(job_dir, firm_config_path, data_root, client)
    _seed_scan(sr, {"s1": ("scanned fax.pdf", make_pdf(["", "", ""]))})
    assert vision_stage.run(sr) == 0
    text = (sr.slug_dir / "text" / "s1.txt").read_text()
    assert text.count("(machine transcription)") == 3 and "[p.3]" in text
    partial = [json.loads(line) for line in (sr.slug_dir / "partial" / "s1.jsonl").read_text().splitlines()]
    assert [r["page"] for r in partial] == [1, 2, 3]
    row = json.loads((sr.slug_dir / "ocr_results.jsonl").read_text().splitlines()[-1])
    assert row == {
        "id": "s1",
        "name": "scanned fax.pdf",
        "pages": 3,
        "pages_out": 3,
        "failed_pages": 0,
        "illegible_marks": 3,
    }
    ledger = [
        json.loads(line) for line in (sr.slug_dir / "runs" / "alpha" / "usage-ledger.jsonl").read_text().splitlines()
    ]
    assert len(ledger) == 3 and all(
        r["stage"] == "vision" and r["pages"] == 1 and r["model"] == "claude-sonnet-5" for r in ledger
    )
    assert ledger[0]["custom_id"] == "s1-p1"
    # the request shape: system marked, one image + the instruction, no effort field
    p = client.calls[0]
    assert p["system"][0].get("cache_control") and "output_config" not in p
    assert [b["type"] for b in p["messages"][0]["content"]] == ["image", "text"]
    # a second run has nothing to do
    client.calls.clear()
    assert vision_stage.run(sr) == 0 and client.calls == []


def test_vision_resumes_from_the_checkpoint_and_marks_refusals(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    def reply(p):
        return _msg("nope", stop="refusal")

    client = Scripted(reply)
    sr = _sr(job_dir, firm_config_path, data_root, client)
    _seed_scan(sr, {"s1": ("fax.pdf", make_pdf(["", ""]))})
    (sr.slug_dir / "partial").mkdir(parents=True)
    (sr.slug_dir / "partial" / "s1.jsonl").write_text(json.dumps({"page": 1, "text": "page one done earlier"}) + "\n")
    assert vision_stage.run(sr) == 0
    assert len(client.calls) == 1  # only page 2 was sent
    text = (sr.slug_dir / "text" / "s1.txt").read_text()
    assert "page one done earlier" in text and vision_stage.REFUSED in text
    row = json.loads((sr.slug_dir / "ocr_results.jsonl").read_text().splitlines()[-1])
    assert row["failed_pages"] == 1 and row["pages_out"] == 2


def test_vision_exits_1_when_a_batch_is_still_processing(
    job_dir: Path, firm_config_path: Path, data_root: Path, tmp_path: Path
) -> None:
    """The frozen script printed 'never returned' under VISION DONE and exited
    0; the driver would have marched on to build_units over a missing file."""

    class Batches:
        def __init__(self):
            self.created = []

        def create(self, requests):
            self.created.append(requests)
            return NS(id="b1")

        def retrieve(self, bid):
            return NS(processing_status="in_progress")

        def results(self, bid):
            return []

    b = Batches()
    client = NS(messages=NS(create=None, batches=b))
    cfg = json.loads(json.dumps(__import__("medchron_testkit").FIRM_CONFIG))
    cfg["levers"]["batch_stages"] = ["vision"]
    fc = tmp_path / "firm-batch.yaml"
    import yaml

    fc.write_text(yaml.safe_dump(cfg))
    log: list[str] = []
    sr = _sr(job_dir, fc, data_root, client, log=log)
    sr.doorway.max_wait_s = 0
    sr.doorway.poll_s = 0
    _seed_scan(sr, {"s1": ("fax.pdf", make_pdf(["", ""]))})
    assert vision_stage.run(sr) == 1
    assert len(b.created) == 1 and not (sr.slug_dir / "text" / "s1.txt").exists()
    assert (sr.slug_dir / "batch" / "batch-vision-0.json").is_file()
    assert any("still processing; rerun resumes it" in line for line in log)


# ---- billing_extract --------------------------------------------------------------
BILL = {
    "doc_type": "MEDICAL_BILL",
    "provider": "Example Clinic",
    "patient": "Alpha Example",
    "date_first": "01/02/2026",
    "date_last": "01/09/2026",
    "printed_totals": [{"label": "Total charges", "amount": "$1,800.00", "page": 1}],
    "line_items": [{"date": "01/02/2026", "description": "visit", "charge": "$900.00", "page": 1}],
    "notes": "",
}


def _billing_docs(sr: StageRun, docs: dict[str, bytes]) -> None:
    raw = sr.slug_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, data in docs.items():
        p = raw / f"{name}.pdf"
        p.write_bytes(data)
        rows.append({"name": name, "path": str(p), "pages": 1})
    (sr.slug_dir / "billing_docs.json").write_text(json.dumps({"docs": rows}))


def test_billing_transcribes_ranges_and_splits_a_range_that_will_not_parse(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    def reply(p):
        pages = sum(1 for b in p["messages"][0]["content"] if b["type"] == "image")
        if pages > 1:
            return _msg("not json at all")  # the 3-page range fails; halves succeed
        return _msg("```json\n" + json.dumps(BILL) + "\n```")

    client = Scripted(reply)
    log: list[str] = []
    sr = _sr(job_dir, firm_config_path, data_root, client, log=log)
    _billing_docs(sr, {"Example Clinic bill": make_pdf(["", "", ""])})
    assert billing_stage.run(sr) == 0
    rec = json.loads((sr.slug_dir / "billing_extract.jsonl").read_text().splitlines()[-1])
    assert rec["file"] == "Example Clinic bill" and rec["pages"] == 3 and rec["failures"] == []
    # 1-3 fails, halves to 1-2 / 3-3; 1-2 fails again, halves to 1-1 / 2-2: three single-page chunks
    assert len(rec["chunks"]) == 3 and all(c["doc_type"] == "MEDICAL_BILL" for c in rec["chunks"])
    assert any("retrying 1-3 as 1-2 / 3-3" in line for line in log)
    assert any("retrying 1-2 as 1-1 / 2-2" in line for line in log)
    # a rerun skips the done document
    client.calls.clear()
    assert billing_stage.run(sr) == 0 and client.calls == []


def test_billing_exits_1_over_a_page_that_never_parses(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    client = Scripted(lambda p: _msg("garbage"))
    sr = _sr(job_dir, firm_config_path, data_root, client)
    _billing_docs(sr, {"dense lien": make_pdf([""])})
    assert billing_stage.run(sr) == 1
    rec = json.loads((sr.slug_dir / "billing_extract.jsonl").read_text().splitlines()[-1])
    assert rec["failures"] == [1] and rec["chunks"] == [{"FAILED_PAGE": 1}]
    caps = [p["max_tokens"] for p in client.calls]
    assert caps == [8000, 32000, 8000]  # first, the large budget, then totals-only
    assert "line_items_omitted" in client.calls[-1]["messages"][0]["content"][0]["text"]


# ---- build_units --------------------------------------------------------------------
def _extracted(sr: StageRun, rows: list[dict]) -> None:
    sr.slug_dir.mkdir(parents=True, exist_ok=True)
    (sr.slug_dir / "text").mkdir(exist_ok=True)
    with (sr.slug_dir / "extracted.jsonl").open("w") as fh:
        for r in rows:
            if r.get("text"):
                tp = sr.slug_dir / "text" / f"{r['id']}.txt"
                tp.write_text(r.pop("text"))
                r.setdefault("text_path", str(tp))
                r.setdefault("chars", tp.stat().st_size)
            fh.write(json.dumps(r) + "\n")


def test_build_units_routes_by_folder_then_token_and_excludes_by_config(
    tmp_path: Path, firm_config_path: Path, data_root: Path
) -> None:
    from medchron_testkit import job_yaml

    jd = tmp_path / "job"
    jd.mkdir()
    (jd / "job.yaml").write_text(job_yaml(data_root, joint=True))
    sr = _sr(jd, firm_config_path, data_root, None)
    decisions.units(sr.job, sr.cfg, sr.slug_dir, dry_run=False)
    _extracted(
        sr,
        [
            {"id": "a1", "name": "clinic note", "folder": "/Alpha_Example/MEDICAL", "ext": ".pdf", "text": PROSE},
            {
                "id": "b1",
                "name": "2099_000001_Beta_Example_-_Example_Health",
                "folder": "/Shared",
                "ext": ".pdf",
                "text": PROSE,
            },
            {"id": "x1", "name": "Retainer signed", "folder": "/Alpha_Example", "ext": ".pdf", "text": PROSE},
            {"id": "u1", "name": "Example Health Records 6.22.26", "folder": "/Shared", "ext": ".pdf", "text": PROSE},
            {"id": "s1", "name": "scan done by vision", "folder": "/Beta_Example", "ext": ".pdf", "scan": True},
        ],
    )
    (sr.slug_dir / "text" / "s1.txt").write_text("[p.1] (machine transcription)\n" + PROSE)
    assert units_stage.run(sr) == 0
    alpha = json.loads((sr.slug_dir / "units" / "alpha.json").read_text())
    beta = json.loads((sr.slug_dir / "units" / "beta.json").read_text())
    assert [r["id"] for r in alpha] == ["a1"]
    assert sorted(r["id"] for r in beta) == ["b1", "s1"]  # token through underscores; the scan via disk
    assert all(r["text_path"] for r in beta)
    unassigned = json.loads((sr.slug_dir / "units" / "_unassigned.json").read_text())
    assert [r["id"] for r in unassigned] == ["u1"]


def test_build_units_gives_two_files_with_one_name_two_names(
    tmp_path: Path, firm_config_path: Path, data_root: Path
) -> None:
    """Four email attachments named image001.jpg (live 2026-09-15): every later
    stage keys on the name, so the later file becomes `image001 (2)`; ids stay."""
    from medchron_testkit import job_yaml

    jd = tmp_path / "job"
    jd.mkdir()
    (jd / "job.yaml").write_text(job_yaml(data_root, joint=True))
    log: list[str] = []
    sr = _sr(jd, firm_config_path, data_root, None, log)
    decisions.units(sr.job, sr.cfg, sr.slug_dir, dry_run=False)
    _extracted(
        sr,
        [
            {"id": "msgatt-1", "name": "image001", "folder": "/Alpha_Example/MEDICAL", "ext": ".jpg", "text": PROSE},
            {"id": "a1", "name": "clinic note", "folder": "/Alpha_Example/MEDICAL", "ext": ".pdf", "text": PROSE},
            {"id": "msgatt-2", "name": "image001", "folder": "/Alpha_Example/MEDICAL", "ext": ".jpg", "text": PROSE},
            {"id": "msgatt-3", "name": "image001", "folder": "/Alpha_Example/MEDICAL", "ext": ".jpg", "text": PROSE},
        ],
    )
    assert units_stage.run(sr) == 0
    alpha = json.loads((sr.slug_dir / "units" / "alpha.json").read_text())
    assert [(r["id"], r["name"]) for r in alpha] == [
        ("msgatt-1", "image001"),
        ("a1", "clinic note"),
        ("msgatt-2", "image001 (2)"),
        ("msgatt-3", "image001 (3)"),
    ]
    assert sum("two files named 'image001.jpg'" in line for line in log) == 2


def test_build_units_refuses_on_an_untranscribed_scan_and_on_missing_billing_extract(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sr = _sr(job_dir, firm_config_path, data_root, None)
    _extracted(sr, [{"id": "s1", "name": "fax", "folder": "/MEDICAL", "ext": ".pdf", "scan": True}])
    assert units_stage.run(sr) == 2
    _extracted(sr, [{"id": "a1", "name": "bill", "folder": "/MEDICAL", "ext": ".pdf", "text": PROSE}])
    (sr.slug_dir / "billing_docs.json").write_text(json.dumps({"docs": [{"name": "bill", "path": "x", "pages": 1}]}))
    assert units_stage.run(sr) == 2


def test_build_units_marks_compose_skips_only_when_every_page_is_evidenced(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sr = _sr(job_dir, firm_config_path, data_root, None)
    decisions.units(sr.job, sr.cfg, sr.slug_dir, dry_run=False)
    _extracted(
        sr,
        [
            {"id": "a1", "name": "full bill", "folder": "/MEDICAL", "ext": ".pdf", "text": PROSE, "pages": 2},
            {
                "id": "a2",
                "name": "mixed report and bill",
                "folder": "/MEDICAL",
                "ext": ".pdf",
                "text": PROSE,
                "pages": 3,
            },
        ],
    )
    (sr.slug_dir / "billing_docs.json").write_text(json.dumps({"docs": []}))
    full = {
        "file": "full bill",
        "pages": 2,
        "failures": [],
        "chunks": [{"doc_type": "LEDGER", "line_items": [{"page": 1}, {"page": 2}], "printed_totals": []}],
    }
    mixed = {
        "file": "mixed report and bill",
        "pages": 3,
        "failures": [],
        "chunks": [{"doc_type": "MEDICAL_BILL", "line_items": [{"page": 1}], "printed_totals": []}],
    }
    (sr.slug_dir / "billing_extract.jsonl").write_text(json.dumps(full) + "\n" + json.dumps(mixed) + "\n")
    assert units_stage.run(sr) == 0
    unit = {r["name"]: r for r in json.loads((sr.slug_dir / "units" / "alpha.json").read_text())}
    assert unit["full bill"]["compose"] is False and "billing-only source (LEDGER)" in unit["full bill"]["compose_skip"]
    assert "compose" not in unit["mixed report and bill"]  # pages 2-3 may be the clinical report


def test_token_hit_survives_underscore_joins() -> None:
    assert units_stage.token_hit("Beta", "2099_000001_Beta_Example_-_Example_Health.pdf")
    assert not units_stage.token_hit("Beta", "Betamax records.pdf")


@pytest.mark.parametrize("dpi", [RENDER := billing_stage.RENDER_DPI])
def test_render_doc_returns_base64_per_page(tmp_path: Path, dpi: int) -> None:
    p = tmp_path / "x.pdf"
    p.write_bytes(make_pdf(["", ""]))
    imgs, n = billing_stage.render_doc(str(p))
    assert n == 2 and set(imgs) == {1, 2} and all(isinstance(v, str) and len(v) > 100 for v in imgs.values())


# -- read, but carrying nothing -----------------------------------------------
#
# On 2026-09-11 a firm's chronology refused over four phone screenshots the
# Operator had read correctly and in full: `[illegible]`, `View motion photo`
# twice, and a phone status bar. Each transcription was 41-47 bytes, the gate
# tested `> 50`, and the refusal said "NO transcription yet" about documents
# that were transcribed. The run stopped for four days and two sessions went
# looking for an image-rendering fault that did not exist.


def _scan_rec(rec_id: str = "s1", name: str = "IMG_0001") -> dict:
    return {"id": rec_id, "name": name, "folder": "/Beta_Example", "ext": ".jpg", "scan": True}


def _recorded(sr, rec_id: str = "s1", name: str = "IMG_0001", **over) -> None:
    """The row vision writes when it completes a document."""
    row = {"id": rec_id, "name": name, "pages": 1, "pages_out": 1, "failed_pages": 0, "illegible_marks": 0}
    row.update(over)
    with (sr.slug_dir / "ocr_results.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def test_a_read_but_contentless_scan_is_carried_with_a_reason_not_refused(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """The regression this file exists for. A screenshot with nothing on it is
    a DISPOSITION, not a halt: it rides into the composition set carrying the
    reason the coverage gate needs."""
    sr = _sr(job_dir, firm_config_path, data_root, None)
    _extracted(sr, [_scan_rec()])
    (sr.slug_dir / "text" / "s1.txt").write_text("[p.1] (machine transcription)\nView motion photo")
    _recorded(sr)

    assert units_stage.run(sr) == 0, "a document that was read must not refuse the run"
    rows = [
        r
        for f in sorted((sr.slug_dir / "units").glob("*.json"))
        for r in json.loads(f.read_text())
        if isinstance(r, dict) and r.get("id") == "s1"
    ]
    assert rows, "the document was read, so it must appear in the composition set somewhere"
    row = rows[0]
    assert row["text_path"], "it was read; it belongs in the set"
    assert row["compose_skip"], "and it must carry the reason the coverage gate asks for"
    assert "nothing in it was cited" in row["compose_skip"], "measured, never inferred"
    assert "3 words of text" in row["compose_skip"], "the reason states what was measured (View motion photo = 3)"
    assert row["chars"] > 3, "chars stays CHARACTERS (budget reads it); the word count is a separate measure"


def test_a_contentless_disposition_survives_the_billing_pass(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """The test above ran the whole stage and still missed this, because
    `mark_compose_skips` returns at its first line when `billing_docs.json` is
    absent -- so the branch that clears marks never executed. On 2026-09-15 a
    live chronology reached `build_units` with billing authored, the four
    screenshots got their disposition in `_attach_transcripts`, and the billing
    pass popped it eleven lines later. The coverage gate then has nothing to
    read and holds the run on documents that were read correctly.

    The falsifier: without billing_docs.json + billing_extract.jsonl present,
    this test passes against the bug. Both are written below for that reason.
    """
    sr = _sr(job_dir, firm_config_path, data_root, None)
    decisions.units(sr.job, sr.cfg, sr.slug_dir, dry_run=False)
    _extracted(
        sr,
        [
            _scan_rec(),
            {"id": "a1", "name": "full bill", "folder": "/MEDICAL", "ext": ".pdf", "text": PROSE, "pages": 1},
        ],
    )
    (sr.slug_dir / "text" / "s1.txt").write_text("[p.1] (machine transcription)\nView motion photo")
    _recorded(sr)
    # Billing authored AND extracted: this is what makes the clearing branch run.
    (sr.slug_dir / "billing_docs.json").write_text(json.dumps({"docs": []}))
    (sr.slug_dir / "billing_extract.jsonl").write_text(
        json.dumps(
            {
                "file": "full bill",
                "pages": 1,
                "failures": [],
                "chunks": [{"doc_type": "LEDGER", "line_items": [{"page": 1}], "printed_totals": []}],
            }
        )
        + "\n"
    )

    assert units_stage.run(sr) == 0
    rows = {
        r["id"]: r
        for f in sorted((sr.slug_dir / "units").glob("*.json"))
        if not f.name.startswith("_")
        for r in json.loads(f.read_text())
        if isinstance(r, dict)
    }
    assert rows["a1"]["compose"] is False, "the billing pass still marks a billing-only source"
    assert rows["s1"].get("compose_skip"), (
        "the contentless scan must STILL carry its reason after the billing pass; "
        "without it the coverage gate holds the run on a document that was read"
    )
    assert "nothing in it was cited" in rows["s1"]["compose_skip"]
    assert "compose" not in rows["s1"], "it is composed as usual; only the explanation rides along"


def test_a_stale_billing_mark_is_cleared_when_the_evidence_stops_supporting_it(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """The clearing branch's OWN job, which had no test of its own: narrowing it
    to protect another stage's disposition must not stop it clearing a mark it
    wrote on an earlier pass. Found by mutation on 2026-09-15 -- deleting the
    two pops left the whole file green, so nothing was watching this.

    A file marked billing-only must lose the mark once billing_extract no longer
    evidences every page; otherwise it silently stays out of the chronology.
    """
    sr = _sr(job_dir, firm_config_path, data_root, None)
    d = sr.slug_dir
    (d / "billing_docs.json").write_text(json.dumps({"docs": []}))
    # A failed page: the billing chart no longer carries the whole file, so
    # composition must take it back and the mark must go.
    (d / "billing_extract.jsonl").write_text(
        json.dumps(
            {
                "file": "full bill",
                "pages": 1,
                "failures": [1],
                "chunks": [{"doc_type": "LEDGER", "line_items": [{"page": 1}], "printed_totals": []}],
            }
        )
        + "\n"
    )
    # `run` rebuilds every record from extracted.jsonl, so a mark can only reach
    # this branch through a direct call -- which is where the contract lives.
    billing_marked = {"id": "a1", "name": "full bill", "ext": ".pdf", "compose": False, "compose_skip": "stale reason"}
    contentless = {"id": "s1", "name": "IMG_0001", "ext": ".jpg", "compose_skip": "read in full; 41 bytes of text"}
    units = {"alpha": [billing_marked, contentless]}
    assert units_stage.mark_compose_skips(d, units, lambda _m: None) == []
    assert "compose" not in billing_marked, "a mark whose evidence is gone must be cleared"
    assert "compose_skip" not in billing_marked, "and its reason with it"
    assert contentless["compose_skip"] == "read in full; 41 bytes of text", (
        "another stage's disposition is not this pass's to drop"
    )


def test_a_transcript_that_varies_by_bytes_keeps_its_disposition(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """Transcription is not byte-identical between runs. The SAME phone
    screenshot came back at 44, 53 and 57 bytes on three runs of one client matter
    (2026-09-15) against a byte threshold of 50: disposed of once, unexplained
    twice, and the second and third runs held at the coverage gate on a
    document that had been read correctly. Words move by ones where bytes
    move by tens, and on 82 real scans the contentless cluster sat at 0-3
    words against a next value of 25.

    Falsifier: counting bytes instead of words turns the 53B and 57B variants
    PRESENT and this goes red.
    """
    from medchron.stages.transcript import CONTENTLESS, PRESENT, transcript_state

    sr = _sr(job_dir, firm_config_path, data_root, None)
    variants = {
        "v44": "[p.1] (machine transcription)\n11:56 M M 75%",
        "v53": "[p.1] (machine transcription)\n11:56 [illegible] MM \u00b7",
        "v57": "[p.1] (machine transcription)\n11:56 M M | 75% | [illegible]",
        "photo": "[p.1] (machine transcription)\n[illegible] The image shows a photograph of a garage floor with "
        "an epoxy spill near the entry, a visible trip hazard along the seam, and standing water at the drain.",
    }
    (sr.slug_dir / "text").mkdir(parents=True, exist_ok=True)
    for rid, body in variants.items():
        (sr.slug_dir / "text" / f"{rid}.txt").write_text(body)
        _recorded(sr, rid, rid)
    states = {rid: transcript_state(sr.slug_dir, rid)[0] for rid in variants}
    assert states == {"v44": CONTENTLESS, "v53": CONTENTLESS, "v57": CONTENTLESS, "photo": PRESENT}, states
    sizes = {rid: len(variants[rid].encode()) for rid in variants}
    assert sizes["v44"] < 50 < sizes["v53"] < sizes["v57"], "the fixture straddles the old byte cutoff on purpose"


def test_a_scan_with_no_transcription_still_refuses(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    """The case the gate was written for, which must survive the fix."""
    sr = _sr(job_dir, firm_config_path, data_root, None)
    _extracted(sr, [_scan_rec()])
    assert units_stage.run(sr) == 2


def test_a_truncated_transcription_refuses_rather_than_passing_as_read(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """A kill mid-write used to leave a zero-length file. Bare existence would
    accept it and the document would enter the chronology empty."""
    sr = _sr(job_dir, firm_config_path, data_root, None)
    _extracted(sr, [_scan_rec()])
    (sr.slug_dir / "text" / "s1.txt").write_text("")
    _recorded(sr)
    assert units_stage.run(sr) == 2


def test_a_transcription_the_reader_never_recorded_refuses(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """Text on disk with no ocr_results row: the pass died between the two
    writes. A resume completes it cheaply; treating it as read does not."""
    sr = _sr(job_dir, firm_config_path, data_root, None)
    _extracted(sr, [_scan_rec(), _scan_rec("s2", "other")])
    (sr.slug_dir / "text" / "s1.txt").write_text("[p.1] (machine transcription)\n" + PROSE)
    (sr.slug_dir / "text" / "s2.txt").write_text("[p.1] (machine transcription)\n" + PROSE)
    _recorded(sr, "s2", "other")  # s1's row is missing
    assert units_stage.run(sr) == 2


def test_the_refusal_states_what_it_observed(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    """The old wording asserted a conclusion ("NO transcription yet") that was
    false, and it cost four days. It must report the state it measured."""
    lines: list[str] = []
    sr = _sr(job_dir, firm_config_path, data_root, None)
    sr.log = lines.append
    _extracted(sr, [_scan_rec()])
    assert units_stage.run(sr) == 2
    blob = " ".join(lines)
    assert "absent" in blob and "s1.txt" in blob
    assert "NO transcription yet" not in blob


def test_vision_does_not_re_transcribe_a_contentless_page(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """The same constant made vision re-read, and re-PAY for, every short
    transcription on every resume."""
    sr = _sr(job_dir, firm_config_path, data_root, None)
    _extracted(sr, [_scan_rec()])
    (sr.slug_dir / "text" / "s1.txt").write_text("[p.1] (machine transcription)\nView motion photo")
    _recorded(sr)
    state, _ = transcript.transcript_state(sr.slug_dir, "s1", transcript.recorded_ids(sr.slug_dir))
    assert state not in transcript.UNREAD, "vision must treat this as already read"
