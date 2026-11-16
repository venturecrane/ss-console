"""The store of paid page text (wave 5, ``jobs/matter/store.py``): a page read
the same way is paid for once, on any lane and any matter, and a matter can
be purged from it and proven gone. Every store here is under the test's own
tmp dir (``JOBS_TEXTCACHE_DIR`` set by a fixture, never ``/opt/data``)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from jobs import textcache
from jobs.matter import paid, policy, store as store_mod
from reader_fixtures import FIXTURES
from test_litigation_textcache import CountingSeat, _docs, _ocr_calls, _scan_text, _through_extract

SHA, SHA2 = "a" * 64, "b" * 64
END = "end_turn"
M1, M2 = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def store_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "page-store"
    monkeypatch.setenv(store_mod.ENV, str(d))
    return d


@pytest.fixture(autouse=True)
def _pricing(tmp_path: Path, pricing_path: Path) -> None:
    (tmp_path / "pricing.json").write_text(pricing_path.read_text())


def _mv(model: str = "claude-sonnet-5") -> str:
    return paid.method(policy.LITIGATION, model).version


# ---- the store ---------------------------------------------------------------------------
def test_a_page_put_once_is_served_again_and_a_hit_adds_its_matter(store_dir: Path) -> None:
    s = store_mod.from_env("litigation", log=lambda m: None)
    assert s.get(SHA, 1, _mv(), M1) is None and s.put(SHA, 1, _mv(), "PAGE ONE", M1, stop_reason=END)
    assert s.get(SHA, 1, _mv(), M2) == "PAGE ONE"
    doc = json.loads((store_dir / "pages" / SHA / _mv() / "1.json").read_text())
    assert doc == {"text": "PAGE ONE", "matter_ids": sorted([M1, M2])}
    assert s.summary() == {"store": "on", "hits": 1, "misses": 1, "repeats": 0}
    assert oct((store_dir / "pages" / SHA / _mv() / "1.json").stat().st_mode & 0o777) == "0o600"


def test_another_file_page_or_method_is_a_miss(store_dir: Path) -> None:
    s = store_mod.from_env("litigation", log=lambda m: None)
    s.put(SHA, 1, _mv(), "PAGE ONE", M1, stop_reason=END)
    assert s.get(SHA2, 1, _mv(), M1) is None  # FALSIFIER: never another file's page
    assert s.get(SHA, 2, _mv(), M1) is None
    assert s.get(SHA, 1, _mv("claude-opus-5-5"), M1) is None  # the model is in the method
    m = paid.method(policy.LITIGATION, "claude-sonnet-5")
    for other in (
        paid.Method(m.system, m.prompt + " ", m.model, m.max_tokens, m.render),
        paid.Method(m.system, m.prompt, m.model, m.max_tokens + 1, m.render),
        paid.Method(m.system, m.prompt, m.model, m.max_tokens, policy.RAW_IMAGE),
    ):
        assert other.version != m.version


@pytest.mark.parametrize(
    "text, matter",
    [
        ("text", ""),  # no matter: could never be purged or probed
        ("[page transcription failed]", M1),
        ("[page transcription refused]", M1),
        (textcache.REHEARSAL_MARK + " a page]", M1),
        ("[scanned page, no text layer: not read in this job]", M1),
        ("[not transcribed: the same page image is read elsewhere]", M1),
        ("   ", M1),
    ],
)
def test_what_the_store_refuses(store_dir: Path, text: str, matter: str) -> None:
    s = store_mod.from_env("litigation", log=lambda m: None)
    assert not s.put(SHA, 1, _mv(), text, matter, stop_reason=END)
    assert not (store_dir / "pages").exists() or not store_mod.page_files(store_dir)


def test_off_and_read_only_stores_write_nothing_but_count(tmp_path: Path, store_dir: Path) -> None:
    off = store_mod.PageStore(None, lane="demand", log=lambda m: None, record_to=tmp_path / "j" / "page_store.json")
    assert off.get(SHA, 1, _mv(), M1) is None and not off.put(SHA, 1, _mv(), "x", M1, stop_reason=END)
    off.flush()
    assert json.loads((tmp_path / "j" / "page_store.json").read_text()) == {
        "hits": 0, "lane": "demand", "misses": 1, "repeats": 0, "store": "off",
    }  # fmt: skip
    ro = store_mod.PageStore(store_dir, lane="litigation", read_only=True, log=lambda m: None)
    assert not ro.put(SHA, 1, _mv(), "x", M1, stop_reason=END) and not store_dir.exists()


def test_a_job_that_read_no_page_records_nothing(tmp_path: Path, store_dir: Path) -> None:
    s = store_mod.from_env("litigation", log=lambda m: None, record_to=tmp_path / "page_store.json")
    s.flush()
    assert not (tmp_path / "page_store.json").exists()
    assert store_mod.job_fields(tmp_path) == {}


def test_flush_adds_to_the_job_record_and_the_lane_stats(tmp_path: Path, store_dir: Path) -> None:
    rec = tmp_path / "job" / "page_store.json"
    s = store_mod.from_env("medchron", log=lambda m: None, record_to=rec)
    s.put(SHA, 1, _mv(), "P", M1, stop_reason=END)
    s.get(SHA, 1, _mv(), M1)
    s.get(SHA, 2, _mv(), M1)
    s.flush()
    s.flush()  # a second flush adds nothing it already added
    assert store_mod.job_fields(tmp_path / "job") == {
        "page_store": {"store": "on", "hits": 1, "misses": 1, "repeats": 0}
    }
    assert textcache.stats(store_dir)["by_lane"] == {"medchron": {"hits": 1, "misses": 1}}


def test_the_cap_evicts_the_oldest_touched_pages_down_to_low_water(store_dir: Path) -> None:
    probe = store_mod.PageStore(store_dir / "probe", lane="demand", log=lambda m: None)
    probe.put(SHA, 1, _mv(), "x" * 100, M1, stop_reason=END)
    size = (store_dir / "probe" / "pages" / SHA / _mv() / "1.json").stat().st_size
    # Six pages cross a cap of 5.5; evicting to the cap would drop one, to 90% of it drops two.
    s = store_mod.PageStore(store_dir, lane="demand", log=lambda m: None, cap_bytes=int(5.5 * size))
    for n in range(1, 6):
        s.put(SHA, n, _mv(), "x" * 100, M1, stop_reason=END)
    assert json.loads((store_dir / "pages" / ".bytes").read_text()) == 5 * size  # shared, no rescan yet
    for n, age in ((1, 500), (2, 400), (3, 300), (4, 200), (5, 100)):
        os.utime(store_dir / "pages" / SHA / _mv() / f"{n}.json", (time.time() - age,) * 2)
    s.get(SHA, 1, _mv(), M1)  # touched: now the newest
    s.put(SHA, 6, _mv(), "x" * 100, M1, stop_reason=END)
    assert sorted(int(p.stem) for p in store_mod.page_files(store_dir)) == [1, 4, 5, 6]
    assert json.loads((store_dir / "pages" / ".bytes").read_text()) == 4 * size
    s.put(SHA, 7, _mv(), "x" * 100, M1, stop_reason=END)  # back under the cap: this write evicts nothing
    assert sorted(int(p.stem) for p in store_mod.page_files(store_dir)) == [1, 4, 5, 6, 7] and not s.quiet


def test_a_purge_that_races_a_claim_never_quiets_the_store(store_dir: Path) -> None:
    s = store_mod.from_env("litigation", log=lambda m: None)
    s.put(SHA, 1, _mv(), "PAGE ONE", M1, stop_reason=END)
    page = store_dir / "pages" / SHA / _mv() / "1.json"
    textcache.purge_matter(store_dir, M1)
    assert not page.exists()
    s._claim(page, M2)  # the hit read the page; the purge removed it before the claim
    assert not s.quiet and s.put(SHA2, 1, _mv(), "next", M1, stop_reason=END)


@pytest.mark.parametrize("stop", ["refusal", "max_tokens", "tool_use", "", None])
def test_only_a_finished_reading_is_stored(store_dir: Path, stop: str | None) -> None:
    s = store_mod.from_env("demand", log=lambda m: None)
    assert not s.put(SHA, 1, _mv(), "I can't transcribe this page.", M1, stop_reason=stop)
    assert store_mod.page_files(store_dir) == []
    assert s.put(SHA, 1, _mv(), "PAGE", M1, stop_reason="stop_sequence")


def test_a_stored_page_that_cannot_be_served_is_a_repeat_miss_and_a_new_page_is_not(store_dir: Path) -> None:
    s = store_mod.from_env("medchron", log=lambda m: None)
    s.put(SHA, 1, _mv(), "PAGE ONE", M1, stop_reason=END)
    assert s.get(SHA, 2, _mv(), M1) is None and s.repeats == 0  # a new page of a held file
    (store_dir / "pages" / SHA / _mv() / "1.json").write_text("{not json")
    assert s.get(SHA, 1, _mv(), M1) is None and s.repeats == 1


# ---- purge, probe, stats: gone means gone ------------------------------------------------
def test_purge_by_matter_keeps_a_page_another_matter_uses_and_probe_proves_it_gone(store_dir: Path, capsys) -> None:
    s = store_mod.from_env("litigation", log=lambda m: None)
    s.put(SHA, 1, _mv(), "shared", M1, stop_reason=END)
    s.get(SHA, 1, _mv(), M2)
    s.put(SHA2, 1, _mv(), "only m1", M1, stop_reason=END)
    args = ["--dir", str(store_dir)]
    assert textcache.main(["probe", "--matter", M1, *args]) == 1
    out = textcache.purge_matter(store_dir, M1)
    assert out["pages_deleted"] == 1 and out["pages_kept_for_other_matters"] == 1
    assert textcache.main(["probe", "--matter", M1, *args]) == 0
    assert textcache.main(["probe", "--matter", M2, *args]) == 1
    assert json.loads((store_dir / "pages" / SHA / _mv() / "1.json").read_text())["matter_ids"] == [M2]
    capsys.readouterr()
    assert textcache.main(["stats", *args]) == 0
    assert json.loads(capsys.readouterr().out)["pages"] == 1


def test_purge_by_sha_drops_one_files_pages_and_its_whole_file_entry(store_dir: Path) -> None:
    s = store_mod.from_env("litigation", log=lambda m: None)
    s.put(SHA, 1, _mv(), "wrong transcription", M1, stop_reason=END)
    s.put(SHA2, 1, _mv(), "kept", M1, stop_reason=END)
    tc = textcache.TextCache(store_dir, method_version="v", job_id="J", log=lambda m: None)
    tc.put_text(SHA, "whole", scanned_pages=1, matter_id=M1)
    assert textcache.main(["purge", "--sha", SHA, "--dir", str(store_dir), "--dry-run"]) == 0
    assert store_mod.page_files(store_dir) and (store_dir / f"{SHA}.txt").is_file()
    out = textcache.purge_sha(store_dir, SHA)
    assert out == {"entries_deleted": 1, "file_rows_deleted": 0, "pages_deleted": 1}
    assert [p.parent.parent.name for p in store_mod.page_files(store_dir)] == [SHA2]
    assert not (store_dir / SHA).exists() and not (store_dir / f"{SHA}.txt").exists()


def test_the_old_entry_point_runs_the_same_commands(store_dir: Path) -> None:
    from jobs.litigation import textcache as old

    assert old.main is textcache.main and old.method_version is textcache.method_version
    assert old.ENV == "JOBS_LITIGATION_TEXTCACHE_DIR" and textcache.STORE_ENV == "JOBS_TEXTCACHE_DIR"


# ---- the lanes ------------------------------------------------------------------------------
def test_a_reader_change_no_longer_repays_for_scanned_pages_when_the_store_is_on(
    tmp_path: Path, store_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S3: EXTRACT_VERSION misses the whole-file entry, but each page a model
    read is keyed by its own method, so the second job pays for none."""
    from jobs.litigation import extract as extract_mod

    a, ca = _through_extract(tmp_path, "job-aa", CountingSeat(_docs()))
    assert _ocr_calls(ca) == 2
    monkeypatch.setattr(extract_mod, "EXTRACT_VERSION", extract_mod.EXTRACT_VERSION + 1)
    b, cb = _through_extract(tmp_path, "job-bb", CountingSeat(_docs()))
    assert _ocr_calls(cb) == 0 and _scan_text(b) == _scan_text(a)
    rec = store_mod.read_record(b.job.job_dir)
    assert rec == {"store": "on", "hits": 2, "misses": 0, "repeats": 0}


def test_the_litigation_store_is_off_without_the_seat_switch(tmp_path: Path) -> None:
    from jobs.litigation import extract as extract_mod

    a, ca = _through_extract(tmp_path, "job-aa", CountingSeat(_docs()))
    assert _ocr_calls(ca) == 2 and extract_mod.EXTRACT_VERSION == 1
    assert not (tmp_path / "litigation-textcache" / "pages").exists()
    assert store_mod.read_record(a.job.job_dir) == {"store": "off", "hits": 0, "misses": 2, "repeats": 0}


def _medchron(tmp: Path, mp: pytest.MonkeyPatch) -> dict:
    import reader_lanes

    return reader_lanes.medchron(tmp, mp)


def test_a_second_chronology_over_the_same_files_pays_for_no_page(
    tmp_path: Path, store_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _medchron(tmp_path / "one", monkeypatch)
    second = _medchron(tmp_path / "two", monkeypatch)
    assert first["paid"]["sent"] and second["paid"]["sent"] == []
    assert {k: v["text"] for k, v in second["files"].items()} == {k: v["text"] for k, v in first["files"].items()}


def _demand(data: Path, model_client, matter: str) -> list[str]:
    from jobs.demand import preflight, transcribe
    from jobs.ledger import Ledger
    from jobs.llm import Doorway

    (data / "text").mkdir(parents=True, exist_ok=True)
    pdf = data / "scan.pdf"
    pdf.write_bytes(SCAN2)
    row = {"id": "f1", "name": "Scan.pdf", "folder": "/MEDICAL", "kind": "document", "ext": ".pdf", "path": str(pdf)}
    preflight.write_extracted(data, [preflight.extract_one(row, data / "text")])
    doorway = Doorway(ledger=Ledger(data / "usage-ledger.jsonl"), client=model_client, log=lambda m: None)
    transcribe.run(data, doorway, "claude-sonnet-5", lambda m: None, 1, matter_id=matter, record_to=data / "ps.json")
    return [(data / "text" / "f1.txt").read_text()]


SCAN2 = FIXTURES["scan2"].data  # two scanned pages, each an image


def test_demand_pays_once_for_a_page_the_chronology_method_read(tmp_path: Path, store_dir: Path) -> None:
    import reader_kit as rk

    m1, m2 = rk.Model(), rk.Model()
    a = _demand(tmp_path / "a", m1, M1)
    b = _demand(tmp_path / "b", m2, M2)
    assert len(m1.calls) == 2 and m2.calls == [] and a == b
    assert json.loads((tmp_path / "b" / "ps.json").read_text())["hits"] == 2


class _Batch:
    """The doorway's batch queue answering every page with one stop reason (a
    refusal's text is the model's prose)."""

    batch_stages = ("vision",)

    def __init__(self, stop: str) -> None:
        self.stop = stop

    def batch_call(self, stage, items, landed, **kw):
        from types import SimpleNamespace

        for item in items:
            landed(item, SimpleNamespace(text="I can't help with transcribing this.", stop_reason=self.stop), None)
        return SimpleNamespace(timed_out=[])


@pytest.mark.parametrize("stop, stored", [("refusal", 0), ("max_tokens", 0), ("end_turn", 2)])
def test_a_batch_reading_is_stored_only_when_it_finished(
    tmp_path: Path, store_dir: Path, stop: str, stored: int
) -> None:
    from jobs.demand import preflight, transcribe

    data = tmp_path / "d"
    (data / "text").mkdir(parents=True)
    pdf = data / "scan.pdf"
    pdf.write_bytes(SCAN2)
    row = {"id": "f1", "name": "Scan.pdf", "folder": "/MEDICAL", "kind": "document", "ext": ".pdf", "path": str(pdf)}
    preflight.write_extracted(data, [preflight.extract_one(row, data / "text")])
    transcribe.run(data, _Batch(stop), "claude-sonnet-5", lambda m: None, 1, matter_id=M1)
    assert len(store_mod.page_files(store_dir)) == stored
