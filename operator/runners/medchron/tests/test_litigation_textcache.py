"""The cross-job text cache: a paid read is bought once.

A second job over the same files makes no vision call and downloads nothing;
a file whose listing moved is downloaded (and still not re-read when its bytes
are the same); a change to how text is made reads again; purge removes one
matter only; the seed CLI loads a finished job. Every fixture is invented.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import stat
from pathlib import Path

import pytest

from litigation_testkit import M1, M2, LitSeat, ScriptedLit, make_inputs, make_job, matter_docs
from medchron.litigation import textcache
from medchron.litigation.run import LitigationRun
from medchron_testkit import make_pdf

TODAY = dt.date(2026, 10, 7)
SCAN = make_pdf(["", ""])  # two pages, no text layer: both go to vision
#: Built ONCE: make_pdf writes a random trailer /ID, so each call's bytes (and
#: listed size) can differ. Two jobs must list the same files with the same
#: (modified, size), or a cache test measures make_pdf instead of the cache
#: (merge-queue run 40130877069: a regenerated paper's size moved, so it was
#: downloaded again and the test read that as a cache miss).
DOCS = matter_docs()


@pytest.fixture(autouse=True)
def _pricing(tmp_path: Path, pricing_path: Path) -> None:
    (tmp_path / "pricing.json").write_text(pricing_path.read_text())


def _docs(scan_modified: str = "2026-04-12T10:00:00") -> dict[str, list]:
    return {M1: [*DOCS, ("f-s1", "Summons scan.pdf", SCAN, scan_modified)]}


class CountingSeat(LitSeat):
    def __init__(self, per_matter: dict) -> None:
        super().__init__(per_matter)
        self.fetched: list[str] = []

    def fetch(self, url: str, dest: Path, expected_size: int | None) -> int:
        self.fetched.append(url.rsplit("/", 1)[1])
        return super().fetch(url, dest, expected_size)


def _through_extract(tmp_path: Path, job: str, seat: LitSeat, **firm: object) -> tuple[LitigationRun, ScriptedLit]:
    client = ScriptedLit()
    r = LitigationRun(
        make_job(tmp_path / job, job_id=f"01KTJ0BX0000000000000000{job[-2:].upper()}"),
        inputs_dir=str(make_inputs(tmp_path / f"inputs-{job}", **firm)),
        pricing=str(tmp_path / "pricing.json"),
        state_dir=tmp_path / "state",
        seat_factory=lambda: seat,
        client=client,
        log=lambda m: None,
        today=lambda: TODAY,
        readback_pause=0.0,
    )
    for name in ("inventory", "diff", "fetch", "extract"):
        r._stage(name, getattr(r, f"_{name}"))
    return r, client


def _ocr_calls(client: ScriptedLit) -> int:
    return sum(1 for c in client.calls if "Transcribe every word" in json.dumps(c["messages"]))


def _scan_text(r: LitigationRun) -> str:
    return (r.data / "m" / M1 / "txt" / "f-s1.txt").read_text()


def test_a_second_job_makes_no_vision_call_and_downloads_nothing(tmp_path):
    a, ca = _through_extract(tmp_path, "job-aa", CountingSeat(_docs()))
    assert _ocr_calls(ca) == 2 and "OCR TEXT" in _scan_text(a)
    seat = CountingSeat(_docs())
    b, cb = _through_extract(tmp_path, "job-bb", seat)
    assert _ocr_calls(cb) == 0 and seat.fetched == [] and seat.mints == []
    assert _scan_text(b) == _scan_text(a)
    rows = {r["id"]: r for r in map(json.loads, (b.data / "m" / M1 / "pulled.jsonl").read_text().splitlines())}
    assert rows["f-s1"]["cached"] is True and "path" not in rows["f-s1"] and rows["f-s1"]["sha256"]
    ext = json.loads((b.data / "extract.json").read_text())[M1]
    assert ext["failed"] == [] and ext["ok"] == len(rows)
    assert json.loads((b.data / "fetch.json").read_text())[M1]["cached"] == len(rows)


def test_a_moved_listing_downloads_but_the_same_bytes_are_not_read_again(tmp_path):
    _through_extract(tmp_path, "job-aa", CountingSeat(_docs()))
    seat = CountingSeat(_docs(scan_modified="2026-04-20T10:00:00"))
    _b, cb = _through_extract(tmp_path, "job-bb", seat)
    assert seat.fetched == ["f-s1"]  # only the file whose listing moved
    assert _ocr_calls(cb) == 0  # its content (sha256) is the cached one


def test_a_change_to_how_text_is_made_reads_again(tmp_path):
    _through_extract(tmp_path, "job-aa", CountingSeat(_docs()))
    models = {"read": "claude-sonnet-5", "verify": "claude-sonnet-5", "audit": "claude-sonnet-5"}
    seat = CountingSeat(_docs())
    _b, cb = _through_extract(tmp_path, "job-bb", seat, models={**models, "transcription": "claude-opus-5"})
    assert _ocr_calls(cb) == 2 and "f-s1" in seat.fetched


def test_a_page_image_of_a_cached_file_fetches_its_bytes_on_demand(tmp_path):
    _through_extract(tmp_path, "job-aa", CountingSeat(_docs()))
    seat = CountingSeat(_docs())
    b, _cb = _through_extract(tmp_path, "job-bb", seat)
    ctx = b._ctx(M1)
    n = ctx.by_id["f-s1"]
    out = ctx.view_page({"doc": n, "page": 1})
    assert isinstance(out, list) and out[1]["type"] == "image" and seat.fetched == ["f-s1"]
    assert ctx.text(n) == _scan_text(b)  # the text stays the cached one


def test_a_read_that_opens_an_uncandidated_file_uses_the_cache(tmp_path):
    a, _ca = _through_extract(tmp_path, "job-aa", CountingSeat(_docs()))
    ctx = a._ctx(M1)
    ctx.text(ctx.by_id["f-b1"])  # the medical bill: not a candidate, opened on request and cached
    seat = CountingSeat(_docs())
    b, cb = _through_extract(tmp_path, "job-bb", seat)
    (b.data / "m" / M1 / "txt" / "f-b1.txt").unlink(missing_ok=True)
    ctx = b._ctx(M1)
    assert "Exampletown Clinic" in ctx.text(ctx.by_id["f-b1"]) and "f-b1" not in seat.fetched


# ---- the store itself ----------------------------------------------------------------------
def _cache(d: Path, mv: str = "v1", **kw: object) -> textcache.TextCache:
    return textcache.TextCache(d, method_version=mv, job_id="J", log=lambda m: None, **kw)


def test_owner_only_files_and_a_method_mismatch_is_a_miss(tmp_path):
    c = _cache(tmp_path / "tc")
    assert c.put_text("a" * 64, "text one", scanned_pages=1, matter_id=M1)
    assert stat.S_IMODE(os.stat(tmp_path / "tc").st_mode) == 0o700
    assert stat.S_IMODE(os.stat(tmp_path / "tc" / f"{'a' * 64}.txt").st_mode) == 0o600
    assert c.get_text("a" * 64) == "text one" and _cache(tmp_path / "tc", "v2").get_text("a" * 64) is None
    assert c.put_file("f1", {"modified": "2026-01-01T00:00:00", "size": 10}, {"ok": True, "sha256": "a" * 64}, M1)
    assert c.lookup_file("f1", "2026-01-01T00:00:00", 10) == "a" * 64
    assert (
        c.lookup_file("f1", "2026-01-02T00:00:00", 10) is None
        and c.lookup_file("f1", "2026-01-01T00:00:00", 11) is None
    )


def test_a_rehearsal_placeholder_and_a_read_only_cache_never_write(tmp_path):
    c = _cache(tmp_path / "tc")
    assert not c.put_text("b" * 64, textcache.REHEARSAL_MARK + " x]", scanned_pages=1, matter_id=M1)
    ro = _cache(tmp_path / "ro", read_only=True)
    assert not ro.put_text("b" * 64, "t", scanned_pages=0, matter_id=M1) and not (tmp_path / "ro").exists()


def test_an_unwritable_cache_goes_quiet_and_never_fails_the_job(tmp_path):
    (tmp_path / "file").write_text("not a dir")
    c = _cache(tmp_path / "file" / "tc")
    assert not c.put_text("c" * 64, "t", scanned_pages=0, matter_id=M1) and c.quiet


def test_purge_removes_one_matter_and_keeps_a_shared_entry_for_the_other(tmp_path):
    c = _cache(tmp_path / "tc")
    c.put_text("a" * 64, "only m1", scanned_pages=0, matter_id=M1)
    c.put_text("b" * 64, "shared", scanned_pages=0, matter_id=M1)
    c.put_text("b" * 64, "shared", scanned_pages=0, matter_id=M2)
    c.put_text("d" * 64, "only m2", scanned_pages=0, matter_id=M2)
    c.put_file("f1", {"modified": "2026-01-01", "size": 1}, {"ok": True, "sha256": "a" * 64}, M1)
    c.put_file("f2", {"modified": "2026-01-01", "size": 1}, {"ok": True, "sha256": "d" * 64}, M2)
    assert textcache.main(["purge", "--matter", M1, "--dir", str(tmp_path / "tc"), "--dry-run"]) == 0
    assert c.get_text("a" * 64) == "only m1"  # a dry run removes nothing
    out = textcache.purge_matter(tmp_path / "tc", M1)
    assert out == {"entries_deleted": 1, "entries_kept_for_other_matters": 1, "file_rows_deleted": 1}
    assert c.get_text("a" * 64) is None and not (tmp_path / "tc" / f"{'a' * 64}.txt").exists()
    meta = json.loads((tmp_path / "tc" / f"{'b' * 64}.json").read_text())
    assert meta["matter_ids"] == [M2] and c.get_text("d" * 64) == "only m2"
    assert c.lookup_file("f2", "2026-01-01", 1) == "d" * 64 and c.lookup_file("f1", "2026-01-01", 1) is None


# ---- seed ---------------------------------------------------------------------------------
def _job_with_texts(tmp_path: Path) -> Path:
    jd = tmp_path / "donejob"
    m = jd / "data" / "m" / M1
    (m / "txt").mkdir(parents=True)
    rows = [
        {"id": "f1", "ok": True, "sha256": "1" * 64, "path": "/gone/f1.pdf"},
        {"id": "f2", "ok": True, "sha256": "2" * 64, "path": "/gone/f2.pdf"},  # no text saved
        {"id": "f3", "ok": False, "error": "x"},
        {"id": "f4", "ok": True, "sha256": "4" * 64, "path": "/gone/f4.pdf"},  # a rehearsal's placeholder
    ]
    (m / "pulled.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (m / "txt" / "f1.txt").write_text("Complaint text")
    (m / "txt" / "f4.txt").write_text(textcache.REHEARSAL_MARK + " page]")
    (jd / "data" / "files").mkdir()
    listing = [{"id": f"f{i}", "modified": "2026-03-01T00:00:00", "size": 100} for i in range(1, 5)]
    (jd / "data" / "files" / f"{M1}.json").write_text(json.dumps(listing))
    return jd


def test_seed_counts_and_a_second_seed_finds_it_already(tmp_path, capsys):
    jd, d = _job_with_texts(tmp_path), tmp_path / "tc"
    args = ["seed", "--job", str(jd), "--model", "claude-sonnet-5", "--dir", str(d)]
    assert textcache.main([*args, "--dry-run"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["seeded"] == 1 and not d.exists()
    assert textcache.main(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["seeded"], out["skipped_no_text"], out["skipped_no_sha"], out["skipped_rehearsal_text"]) == (1, 1, 1, 1)
    c = _cache(d, textcache.method_version("claude-sonnet-5"))
    assert c.text_for({"id": "f1", "modified": "2026-03-01T00:00:00", "size": 100}) == ("1" * 64, "Complaint text")
    assert textcache.main(args) == 0
    assert json.loads(capsys.readouterr().out)["already"] == 1
