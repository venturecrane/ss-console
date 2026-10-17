"""C1 (2026-10-17): a failed or refused vision page on a text-layer PDF.

`_finish` merged the image pages of a text-layer PDF into its text and
returned BEFORE it appended the `ocr_results.jsonl` row, so a page vision
failed or refused on was disclosed in Limitations nowhere, and `merged()`
read the failure marker as a machine transcription, so a resume never asked
for it again."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from medchron.stages import limitations
from medchron.stages import transcript as T
from medchron.stages import vision as V
from medchron_testkit import make_pdf


def _sr(d: Path) -> SimpleNamespace:
    return SimpleNamespace(slug_dir=d, log=lambda _m: None)


def _file(d: Path, done: dict[int, str]) -> dict:
    (d / "text").mkdir(exist_ok=True)
    (d / "text" / "X.txt").write_text("[p.1]\nTyped body of the record, long enough\n[p.2]\n\n")
    return {"rec": {"id": "X", "name": "r.pdf"}, "npages": 2, "done": done, "part": d / "p.jsonl", "only": [2]}


def test_a_failed_image_page_on_a_text_layer_pdf_is_recorded_and_disclosed(tmp_path: Path) -> None:
    log = tmp_path / "ocr_results.jsonl"
    assert V._finish(_sr(tmp_path), _file(tmp_path, {2: V.FAILED}), log)  # type: ignore[arg-type]
    rows = [json.loads(x) for x in log.read_text().splitlines()]
    assert rows == [{"id": "X", "name": "r.pdf", "pages": 2, "pages_out": 1, "failed_pages": 1, "illegible_marks": 0}]
    text = "\n".join(limitations._unread_lines(tmp_path, [], lambda _m: None))
    assert '"r.pdf" - 1 page of 2' in text


def test_a_refused_or_illegible_image_page_is_recorded_too(tmp_path: Path) -> None:
    log = tmp_path / "ocr_results.jsonl"
    V._finish(_sr(tmp_path), _file(tmp_path, {2: V.REFUSED}), log)  # type: ignore[arg-type]
    assert json.loads(log.read_text())["failed_pages"] == 1
    log.unlink()
    V._finish(_sr(tmp_path), _file(tmp_path, {2: "Signed [illegible] on [illegible]"}), log)  # type: ignore[arg-type]
    row = json.loads(log.read_text())
    assert row["failed_pages"] == 0 and row["illegible_marks"] == 2


def test_a_failed_image_page_is_not_read_and_a_resume_asks_again(tmp_path: Path) -> None:
    assert T.FAILED_PAGE == V.FAILED
    tp = tmp_path / "text" / "X.txt"
    V._finish(_sr(tmp_path), _file(tmp_path, {2: V.FAILED}), tmp_path / "ocr_results.jsonl")  # type: ignore[arg-type]
    assert not T.merged(tp, [2], retry_failed=True)  # vision's resume test: asked again
    assert T.merged(tp, [2])  # build_units: disclosed through the row, never a halt
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(make_pdf(["Typed body of the record, long enough", "scanned"]))
    (tmp_path / "partial").mkdir()
    (tmp_path / "partial" / "X.jsonl").write_text(json.dumps({"page": 2, "text": V.FAILED}) + "\n")
    queue = [{"id": "X", "name": "r.pdf", "vision_pages": [2]}]
    files, pending = V._gather(_sr(tmp_path), queue, {"X": str(pdf)}, tmp_path / "ocr_results.jsonl")  # type: ignore[arg-type]
    assert [p for _r, p, _b in pending] == [2] and 2 not in files["X"]["done"]


def test_a_transcribed_image_page_is_not_asked_again(tmp_path: Path) -> None:
    tp = tmp_path / "text" / "X.txt"
    V._finish(_sr(tmp_path), _file(tmp_path, {2: "PROOF OF SERVICE"}), tmp_path / "ocr_results.jsonl")  # type: ignore[arg-type]
    assert T.merged(tp, [2], retry_failed=True)


def test_limitations_reads_the_newest_row_of_a_document(tmp_path: Path) -> None:
    log = tmp_path / "ocr_results.jsonl"
    V._finish(_sr(tmp_path), _file(tmp_path, {2: V.FAILED}), log)  # type: ignore[arg-type]
    V._finish(_sr(tmp_path), _file(tmp_path, {2: "PROOF OF SERVICE"}), log)  # type: ignore[arg-type]
    text = "\n".join(limitations._unread_lines(tmp_path, [], lambda _m: None))
    assert "could not be transcribed" not in text
