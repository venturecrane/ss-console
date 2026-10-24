"""The text cache's edges, from the #3473 review: a reader change is a miss,
a hit adds its matter (so purge is right both ways), a storage error page or a
file that changed mid-fetch is never kept, a fill keeps the content dedupe, a
lost text is healed instead of becoming a failure, a failed re-download never
shadows the good row, a cache that cannot write is recorded, and the lane
hands the child the cache dir. Every fixture is invented.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from litigation_testkit import M1, M2, LitSeat
from medchron.demand import pull as pull_mod
from medchron.litigation import extract as extract_mod, fetch as fetch_mod, textcache
from medchron_testkit import make_pdf
from test_litigation_textcache import SCAN, CountingSeat, _docs, _ocr_calls, _through_extract

XML = '<?xml version="1.0"?><Error><Code>NoSuchKey</Code></Error>'


@pytest.fixture(autouse=True)
def _pricing(tmp_path: Path, pricing_path: Path) -> None:
    (tmp_path / "pricing.json").write_text(pricing_path.read_text())


def _cache(d: Path, mv: str = "v1") -> textcache.TextCache:
    return textcache.TextCache(d, method_version=mv, job_id="J", log=lambda m: None)


def _meta(cache_dir: Path, sha: str) -> dict:
    return json.loads((cache_dir / f"{sha}.json").read_text())


def test_a_reader_change_bumps_the_version_and_reads_again(tmp_path, monkeypatch):
    _through_extract(tmp_path, "job-aa", CountingSeat(_docs()))
    before = textcache.method_version("claude-sonnet-5")
    monkeypatch.setattr(extract_mod, "EXTRACT_VERSION", extract_mod.EXTRACT_VERSION + 1)
    assert textcache.method_version("claude-sonnet-5") != before
    _b, cb = _through_extract(tmp_path, "job-bb", CountingSeat(_docs()))
    assert _ocr_calls(cb) == 2


def _two_matters() -> dict[str, list]:
    return {**_docs(), M2: [("m2-s1", "Summons scan.pdf", SCAN, "2026-04-12T10:00:00")]}


def test_a_hit_on_a_second_matter_adds_it_and_purge_is_right_both_ways(tmp_path, monkeypatch):
    d = tmp_path / "tc"
    monkeypatch.setenv(textcache.ENV, str(d))
    _through_extract(tmp_path, "job-aa", CountingSeat(_docs()))
    _b, cb = _through_extract(tmp_path, "job-bb", CountingSeat(_two_matters()))
    assert _ocr_calls(cb) == 0  # the second matter's copy of the same scan is a hit
    sha = next(p.stem for p in d.glob("*.json") if "OCR TEXT" in (d / f"{p.stem}.txt").read_text())
    assert _meta(d, sha)["matter_ids"] == sorted([M1, M2])
    shutil.copytree(d, tmp_path / "tc2")
    textcache.purge_matter(d, M2)  # purging the later matter removes only its id
    assert _meta(d, sha)["matter_ids"] == [M1] and (d / f"{sha}.txt").is_file()
    textcache.purge_matter(tmp_path / "tc2", M1)  # purging the first keeps the text the other uses
    assert _meta(tmp_path / "tc2", sha)["matter_ids"] == [M2] and (tmp_path / "tc2" / f"{sha}.txt").is_file()


def _matter_with(tmp_path: Path, rows: list[dict]) -> Path:
    data = tmp_path / "data"
    m = fetch_mod.matter_dir(data, M2)
    (m / "pulled.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return data


def test_a_fill_adds_its_matter_and_keeps_the_content_dedupe(tmp_path):
    c = _cache(tmp_path / "tc")
    c.put_text("a" * 64, "shared text", scanned_pages=0, matter_id=M1)
    for fid in ("f1", "f2"):
        c.put_file(fid, {"modified": "2026-01-01", "size": 5}, {"ok": True, "sha256": "a" * 64}, M1)
    data = _matter_with(tmp_path, [])
    f1, f2 = ({"id": i, "name": i, "ext": ".pdf", "modified": "2026-01-01", "size": 5} for i in ("f1", "f2"))
    assert fetch_mod.fill_one(c, data, M2, f1) and fetch_mod.fill_one(c, data, M2, f2)
    rows = fetch_mod.pulled(data, M2)
    assert rows["f2"]["duplicate_of"] == "f1" and not (data / "m" / M2 / "txt" / "f2.txt").exists()
    assert fetch_mod.cached_text(data, M2, "f2") == "shared text"
    assert not fetch_mod.fill_one(c, data, M2, f2)  # a duplicate_of row is never replaced
    assert _meta(tmp_path / "tc", "a" * 64)["matter_ids"] == sorted([M1, M2])


def test_a_storage_error_page_and_a_changed_file_are_never_kept(tmp_path):
    c = _cache(tmp_path / "tc")
    assert not c.put_text("b" * 64, XML, scanned_pages=0, matter_id=M1)
    (tmp_path / "x.pdf").write_text(XML)
    f = {"modified": "2026-01-01", "size": 5}
    assert not c.put_file("f1", f, {"ok": True, "sha256": "b" * 64, "path": str(tmp_path / "x.pdf")}, M1)
    assert not c.put_file("f2", f, {"ok": True, "sha256": "b" * 64, "size_changed": {"listed": 5}}, M1)
    assert not c.put_file("f3", f, {"ok": False, "sha256": "b" * 64}, M1)
    (tmp_path / "e.txt").write_text(XML)
    rec = extract_mod.extract_cached(
        tmp_path / "m", {"id": "e", "path": str(tmp_path / "e.txt"), "ext": ".txt", "sha256": "e" * 64}, None, c
    )
    assert rec["ok"] and c.get_text("e" * 64) is None
    assert not list((tmp_path / "tc" / "files").glob("*.json"))


class FlakySeat(LitSeat):
    def __init__(self, per: dict, fail_first: int) -> None:
        super().__init__(per)
        self.fail_left = fail_first

    def mint(self, matter_id: str, file_ids: list[str]) -> list[dict]:
        if self.fail_left > 0:
            self.fail_left -= 1
            return [{"id": f, "error": "temporarily unavailable"} for f in file_ids]
        return super().mint(matter_id, file_ids)


def test_a_cached_rows_download_retries_and_a_failure_never_shadows_it(tmp_path, monkeypatch):
    monkeypatch.setattr(pull_mod, "RETRY_PAUSE_SECONDS", 0.0)
    _through_extract(tmp_path, "job-aa", CountingSeat(_docs()))
    b, _cb = _through_extract(tmp_path, "job-bb", CountingSeat(_docs()))
    f = next(x for x in json.loads((b.data / "files" / f"{M1}.json").read_text()) if x["id"] == "f-s1")
    dead = FlakySeat(_docs(), fail_first=99)
    row = fetch_mod.fetch_one(dead, M1, f, b.data, lambda m: None)
    assert not row["ok"] and fetch_mod.pulled(b.data, M1)["f-s1"]["cached"] is True
    flaky = FlakySeat(_docs(), fail_first=1)
    row = fetch_mod.fetch_one(flaky, M1, f, b.data, lambda m: None)
    assert row["ok"] and fetch_mod.local_path(b.data, M1, "f-s1") is not None


def test_a_lost_cached_text_is_healed_never_recorded_as_unreadable(tmp_path):
    _through_extract(tmp_path, "job-aa", CountingSeat(_docs()))
    seat = CountingSeat(_docs())
    b, _cb = _through_extract(tmp_path, "job-bb", seat)
    m = b.data / "m" / M1
    (m / "txt" / "f-s1.txt").unlink()
    (m / "extracted.jsonl").unlink()
    b._put("extract", {"status": "reopened"})
    b._stage("extract", b._extract)
    assert json.loads((b.data / "extract.json").read_text())[M1]["failed"] == []
    assert "OCR TEXT" in (m / "txt" / "f-s1.txt").read_text() and seat.fetched == []  # filled again
    shutil.rmtree(tmp_path / "litigation-textcache")
    (m / "txt" / "f-s1.txt").unlink()
    (m / "extracted.jsonl").unlink()
    b._put("extract", {"status": "reopened"})
    b._stage("extract", b._extract)
    assert "f-s1" in seat.fetched  # nothing cached: downloaded and read again
    assert json.loads((b.data / "extract.json").read_text())[M1]["failed"] == []


def test_a_cache_that_cannot_write_is_recorded_in_the_job_state(tmp_path, monkeypatch):
    (tmp_path / "afile").write_text("not a dir")
    monkeypatch.setenv(textcache.ENV, str(tmp_path / "afile" / "tc"))
    a, _ca = _through_extract(tmp_path, "job-aa", CountingSeat(_docs()))
    assert a.cache.quiet and a._state()["cache_disabled"]


def test_the_lane_hands_the_child_the_cache_dir(tmp_path):
    from test_litigation_lane import _lane

    from medchron.litigation_lane import TEXTCACHE_ENV

    lane, _client = _lane(tmp_path)
    lane.textcache_dir = str(tmp_path / "cache")
    assert lane._child_env("J1", tmp_path)[TEXTCACHE_ENV] == str(tmp_path / "cache")


def test_a_document_read_by_vision_is_recorded_with_its_file_and_page(tmp_path):
    seen = []

    def ocr(_png: bytes) -> str:
        seen.append((extract_mod.CURRENT.file, extract_mod.CURRENT.page))
        return "x" * 40

    (tmp_path / "s.pdf").write_bytes(make_pdf(["", "A printed page with enough text to carry a text layer.", ""]))
    extract_mod.extract_file(tmp_path / "s.pdf", ".pdf", tmp_path / "f-9.txt", ocr)
    assert seen == [("f-9", 1), ("f-9", 3)]
