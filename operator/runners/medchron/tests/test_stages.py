"""The three $0 stages and the seat seam, against a fake seat and real
documents (a real PDF from pymupdf, a faked Outlook container)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from medchron import config as config_mod, decisions, job as job_mod, seat as seat_mod
from medchron.stages import download as download_stage, extract as extract_stage, listing, msg as msg_stage
from medchron.stages.base import StageRefusal, StageRun
from medchron_testkit import FakeSeat, doc_row, make_pdf, seed_seat_files

PROSE = (
    "Patient seen in clinic today for follow up of neck pain after the collision. "
    "The patient reports that the pain is improving with therapy and has no new complaints. "
    "Exam is unchanged from the prior visit and the plan is to continue the current care. "
) * 6
MEDICAL, PHOTOS = "fold-med", "fold-photo"
FOLDERS = [
    {"id": MEDICAL, "name": "MEDICAL", "parentId": None, "path": "/MEDICAL"},
    {"id": PHOTOS, "name": "PHOTOS", "parentId": None, "path": "/PHOTOS"},
]


@pytest.fixture
def corpus() -> dict[str, bytes]:
    return {
        "f1": make_pdf([PROSE] * 40),  # over MIN_BYTES, so the email copy is a kept kind
        "f2": make_pdf(["", ""]),  # no text layer -> scan queue
        "f3": make_pdf([PROSE, PROSE, PROSE]),  # distinct bytes until a test says otherwise
        "f4": b"\x89PNG not really",
        "f5": make_pdf([PROSE]),
        "m1": b"OLE2 pretend container",
    }


@pytest.fixture
def seat(corpus: dict[str, bytes]) -> FakeSeat:
    docs = [
        doc_row("f1", "clinic note.pdf", MEDICAL, len(corpus["f1"])),
        doc_row("f2", "scanned fax.pdf", MEDICAL, len(corpus["f2"])),
        doc_row("f3", "clinic note copy.pdf", MEDICAL, len(corpus["f3"])),
        doc_row("f4", "photo.png", PHOTOS, len(corpus["f4"])),
        doc_row("f5", "root letter.pdf", None, len(corpus["f5"])),
        doc_row("m1", "RE: records.msg", MEDICAL, len(corpus["m1"])),
        {**doc_row("f6", "deleted.pdf", MEDICAL, 10), "deleted": True},
    ]
    return FakeSeat(docs, FOLDERS, corpus)


def _sr(
    job_dir: Path,
    firm_config_path: Path,
    data_root: Path,
    seat: FakeSeat,
    decided: dict | None = None,
    log: list[str] | None = None,
) -> StageRun:
    job = job_mod.load(job_dir)
    cfg = config_mod.load(str(firm_config_path))
    lines = log if log is not None else []
    return StageRun(
        job=job,
        cfg=cfg,
        unit=job.units[0],
        slug_dir=data_root / "example-matter",
        decided=decided or {},
        log=lines.append,
        seat_factory=lambda: seat,
    )


# ---- list_matter --------------------------------------------------------------
def test_list_matter_writes_the_manifest_and_tree(
    job_dir: Path, firm_config_path: Path, data_root: Path, seat: FakeSeat
) -> None:
    sr = _sr(job_dir, firm_config_path, data_root, seat)
    assert listing.run(sr) == 0
    man = json.loads((sr.slug_dir / "manifest.json").read_text())
    assert man["count"] == 7 and man["documents"][0]["id"] == "f1"
    assert [f["path"] for f in json.loads((sr.slug_dir / "folders.json").read_text())] == ["/MEDICAL", "/PHOTOS"]
    # decide_selection reads exactly this tree
    d = decisions.selection(sr.job, sr.cfg, sr.slug_dir, dry_run=False)
    assert d.payload["include_prefixes"] == ["/MEDICAL"]


# ---- download -----------------------------------------------------------------
def test_download_pulls_the_selection_dedupes_by_content_and_verifies_size(
    job_dir: Path, firm_config_path: Path, data_root: Path, seat: FakeSeat, corpus: dict[str, bytes]
) -> None:
    corpus["f3"] = corpus["f1"]  # a byte-identical second copy
    seat.docs[2]["size"] = len(corpus["f1"])
    seed_seat_files(data_root, seat)
    sr = _sr(job_dir, firm_config_path, data_root, seat)
    decisions.selection(sr.job, sr.cfg, sr.slug_dir, dry_run=False)
    assert download_stage.run(sr) == 0
    rows = {r["id"]: r for r in map(json.loads, (sr.slug_dir / "raw_manifest.jsonl").read_text().splitlines())}
    assert set(rows) == {"f1", "f2", "f3", "f5"}  # PHOTOS excluded, .msg not a doc kind, deleted skipped
    assert rows["f3"]["duplicate_of"] == "f1" and rows["f3"]["path"] is None
    assert (sr.slug_dir / "raw" / "f1.pdf").is_file() and not (sr.slug_dir / "raw" / "f3.pdf").exists()
    assert rows["f5"]["folder"] == "/(root)"
    # a second run pulls nothing: the manifest is the resume record
    seat.mints.clear()
    assert download_stage.run(sr) == 0 and seat.mints == []


def test_download_exits_1_when_a_target_is_still_not_pulled(
    job_dir: Path, firm_config_path: Path, data_root: Path, seat: FakeSeat
) -> None:
    seat.fail_mint.add("f2")
    seed_seat_files(data_root, seat)
    log: list[str] = []
    sr = _sr(job_dir, firm_config_path, data_root, seat, log=log)
    decisions.selection(sr.job, sr.cfg, sr.slug_dir, dry_run=False)
    assert download_stage.run(sr) == 1
    rows = [json.loads(line) for line in (sr.slug_dir / "raw_manifest.jsonl").read_text().splitlines()]
    bad = [r for r in rows if not r["ok"]]
    assert [r["id"] for r in bad] == ["f2"] and "not found" in bad[0]["error"]
    assert any("1 of 4 targets are not pulled" in line for line in log)


def test_download_size_mismatch_is_a_failed_row_not_a_silent_file(
    job_dir: Path, firm_config_path: Path, data_root: Path, seat: FakeSeat
) -> None:
    seat.docs[0]["size"] = 12345  # the listing lies about f1's size
    seed_seat_files(data_root, seat)
    sr = _sr(job_dir, firm_config_path, data_root, seat)
    decisions.selection(sr.job, sr.cfg, sr.slug_dir, dry_run=False)
    assert download_stage.run(sr) == 1
    rows = {r["id"]: r for r in map(json.loads, (sr.slug_dir / "raw_manifest.jsonl").read_text().splitlines())}
    assert not rows["f1"]["ok"] and "size mismatch" in rows["f1"]["error"]
    assert not (sr.slug_dir / "raw" / "f1.pdf").exists()


# ---- extract ------------------------------------------------------------------
def test_extract_splits_text_from_scans_and_marks_pages(
    job_dir: Path, firm_config_path: Path, data_root: Path, seat: FakeSeat
) -> None:
    seed_seat_files(data_root, seat)
    sr = _sr(job_dir, firm_config_path, data_root, seat)
    decisions.selection(sr.job, sr.cfg, sr.slug_dir, dry_run=False)
    download_stage.run(sr)
    assert extract_stage.run(sr) == 0
    rows = {r["id"]: r for r in map(json.loads, (sr.slug_dir / "extracted.jsonl").read_text().splitlines())}
    assert (
        rows["f1"]["pages"] == 40
        and rows["f1"]["chars"] > 10000
        and "[p.2]" in Path(rows["f1"]["text_path"]).read_text()
    )
    assert rows["f2"]["scan"] is True and "text_path" not in rows["f2"]
    scans = json.loads((sr.slug_dir / "scan_queue.json").read_text())
    assert [s["id"] for s in scans] == ["f2"]


def test_extract_detectors_are_calibrated() -> None:
    assert extract_stage.glyph_junk("/0/1/2/3 /4/5/6/7 " * 20 + "x")
    assert not extract_stage.glyph_junk(PROSE)
    assert extract_stage.not_english("6HH$GGHQGXP%HORZ PHGLFDO UHFRUG " * 30)
    assert not extract_stage.not_english(PROSE)


# ---- index_msg + fold -----------------------------------------------------------
class _FakeAttachment:
    def __init__(self, name: str, data: bytes) -> None:
        self.longFilename, self.shortFilename, self.data = name, name, data


class _FakeMessage:
    registry: dict[str, list[tuple[str, bytes]]] = {}

    def __init__(self, path: str) -> None:
        self.subject = "RE: records for the file"
        self.attachments = [_FakeAttachment(n, d) for n, d in self.registry[Path(path).name]]

    def close(self) -> None:
        pass


def test_index_msg_hashes_everything_and_fold_takes_only_the_decision(
    job_dir: Path,
    firm_config_path: Path,
    data_root: Path,
    seat: FakeSeat,
    corpus: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hidden = make_pdf([PROSE] * 40)
    _FakeMessage.registry = {
        "m1.msg": [
            ("hidden report.pdf", hidden),  # new to the corpus
            ("clinic note.pdf", corpus["f1"]),  # already pulled: never folded
            ("image001.png", b"tiny"),  # skipped-tiny, still hashed and named
            ("calendar.ics", b"x" * 30_000),  # skipped-kind
            ("secure.rpmsg", b"y" * 30_000),  # encrypted: disclosed
        ]
    }
    monkeypatch.setitem(__import__("sys").modules, "extract_msg", SimpleNamespace(Message=_FakeMessage))
    seed_seat_files(data_root, seat)
    sr = _sr(job_dir, firm_config_path, data_root, seat)
    decisions.selection(sr.job, sr.cfg, sr.slug_dir, dry_run=False)
    download_stage.run(sr)
    assert msg_stage.run_index(sr) == 0
    report = json.loads((sr.slug_dir / "msg_attachments.json").read_text())
    assert report["comparable"] and report["emails_opened"] == 1
    assert report["distinct_attachments"] == 2 and report["new_to_the_corpus"] == 1
    assert [d["kind"] for d in report["dropped_unkept"]] == ["skipped-tiny", "skipped-kind", "encrypted"]
    assert report["encrypted"][0]["attachment"] == "secure.rpmsg"
    # the fold hook reads this report, keeps the new PDF, and discloses the encrypted one
    d = decisions.fold(sr.job, sr.cfg, sr.slug_dir, dry_run=False)
    new_sha12 = next(a["sha256"][:12] for a in report["attachments"] if not a["already_pulled_as"])
    assert d.payload["fold"] == [new_sha12]
    assert d.payload["_disclosed_encrypted"] == ["secure.rpmsg"]
    sr.decided["fold"] = d.payload["fold"]
    assert msg_stage.run_fold(sr) == 0
    rows = [json.loads(line) for line in (sr.slug_dir / "raw_manifest.jsonl").read_text().splitlines()]
    folded = [r for r in rows if r["id"].startswith("msgatt-")]
    assert len(folded) == 1 and folded[0]["from_email"] == "RE: records for the file"
    assert (sr.slug_dir / "raw" / f"msgatt-{new_sha12}.pdf").read_bytes() == hidden
    # extract after fold reads the folded document like any other
    assert extract_stage.run(sr) == 0
    assert any(
        r["id"] == f"msgatt-{new_sha12}"
        for r in map(json.loads, (sr.slug_dir / "extracted.jsonl").read_text().splitlines())
    )
    # folding an attachment the pull already holds is refused
    already = next(a["sha256"][:12] for a in report["attachments"] if a["already_pulled_as"])
    sr.decided["fold"] = [already]
    with pytest.raises(StageRefusal, match="already in the pull"):
        msg_stage.run_fold(sr)


def test_fold_refuses_without_a_baseline_and_is_a_noop_with_nothing_new(
    job_dir: Path, firm_config_path: Path, data_root: Path, seat: FakeSeat
) -> None:
    sr = _sr(job_dir, firm_config_path, data_root, seat)
    sr.slug_dir.mkdir(parents=True, exist_ok=True)
    (sr.slug_dir / "msg_attachments.json").write_text(json.dumps({"comparable": False, "attachments": []}))
    with pytest.raises(StageRefusal, match="no raw_manifest.jsonl baseline"):
        msg_stage.run_fold(sr)
    (sr.slug_dir / "msg_attachments.json").write_text(json.dumps({"comparable": True, "attachments": []}))
    assert msg_stage.run_fold(sr) == 0


def test_classify_keeps_images_and_names_the_rest() -> None:
    assert msg_stage.classify("fax.tif", 60_000) == "image"
    assert msg_stage.classify("logo.png", 4_000) == "skipped-tiny"
    assert msg_stage.classify("letter.doc", 100) == "doc"
    assert msg_stage.classify("message_v2.rpmsg", 9) == "encrypted"
    assert msg_stage.classify("bill.pdf", 30_000) == "pdf"
    assert msg_stage.classify("mystery.zip", 30_000) == "other"


# ---- the seat seam ------------------------------------------------------------
def test_client_seat_normalizes_pages_and_walks_the_tree_per_folder() -> None:
    calls: list[str] = []

    class Client:
        def get(self, path: str, **params):
            calls.append(path)
            if path.endswith("/files"):
                if params["Offset"] == 0:
                    return {
                        "value": [
                            {
                                "id": f"d{i}",
                                "name": f"d{i}.pdf",
                                "sizeBytes": 1,
                                "fileExtension": ".pdf",
                                "folder": {"id": "x"},
                            }
                            for i in range(500)
                        ]
                    }
                return {"value": [{"id": "d500", "name": "last.pdf", "sizeBytes": 2, "fileExtension": ".pdf"}]}
            if path.endswith("/folders"):
                return {"value": [{"folders": [{"id": "a", "name": "A"}]}]}
            if path.endswith("/folders/a"):
                return {"value": [{"folders": [{"id": "b", "name": "B"}]}]}
            return {"value": [{"folders": []}]}

        def request(self, method: str, path: str, **kw):
            return {"downloadUrl": "https://example.invalid/x", "sizeBytes": 3, "name": "n", "fileExtension": ".pdf"}

    import time as _time

    s = seat_mod.ClientSeat(Client())
    orig = _time.sleep
    _time.sleep = lambda *_: None
    try:
        docs = s.list_files("M")
        tree = s.folder_tree("M")
        minted = s.mint("M", ["d1"])
    finally:
        _time.sleep = orig
    assert len(docs) == 501 and docs[0]["folderId"] == "x" and docs[-1]["size"] == 2
    assert [(f["path"], f["parentId"]) for f in tree] == [("/A", None), ("/A/B", "a")]
    assert minted[0]["url"].startswith("https://") and minted[0]["size"] == 3


def test_fetch_refuses_non_https(tmp_path: Path) -> None:
    with pytest.raises(seat_mod.SeatError, match="non-https"):
        seat_mod.fetch_https("file:///etc/hosts", tmp_path / "x", None)


def test_open_seat_names_its_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(seat_mod.SEAT_ENV, "carrier-pigeon")
    with pytest.raises(seat_mod.SeatError, match="not a seat backend"):
        seat_mod.open_seat("example-firm")
    monkeypatch.setenv(seat_mod.SEAT_ENV, "ssh")
    monkeypatch.delenv(seat_mod.SEAT_PROBE_ENV, raising=False)
    with pytest.raises(seat_mod.SeatError, match="seat-probe.sh"):
        seat_mod.open_seat("example-firm")
