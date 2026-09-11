"""vision, billing_extract and build_units in-process, against a scripted SDK
client (no network) and real PDFs."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from medchron import config as config_mod, decisions, job as job_mod
from medchron.stages import billing as billing_stage, units as units_stage, vision as vision_stage
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
