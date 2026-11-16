"""Wave 5 PR P: one downloader for every lane, and displayed names never doubled.

Three things this file holds, each with the falsifier that turns it red:

* **The mint policies are declared, not merged.** The chronology retries once
  after five seconds and puts the second failure's reason on every row; the
  lanes pause two then four seconds. FALSIFIER: give ``STAGES`` the lanes'
  pauses and the first test sees ``[5.0]`` become ``[2.0, 4.0]``.
* **The chronology takes a file edited since the listing** (P1), as the demand
  pull already did, and still refuses the storage service's error document.
  FALSIFIER: drop ``refuse_error_body`` in ``fetch_one`` and the NoSuchKey body
  is returned as the file.
* **Keys stay, display changes** (D1). The two files that collide once
  de-doubled (``x.pdf`` emailed, keyed ``x.pdf.pdf``; ``x`` + ``.pdf`` filed,
  keyed ``x.pdf``) stay two keys everywhere a file is matched: the coverage
  gate, the chunk header the composer echoes back, and the citation resolver.
  Only what a firm reads (a hold line, Records Reviewed, the build log) is
  shown de-doubled. FALSIFIER: route ``chunking.build_chunks``'s header through
  ``filing.shown`` and the collision test sees one name for two files.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jobs import filing
from jobs.matter import pull as matter_pull

EMAILED = {"id": "n-emailed", "name": "x.pdf", "ext": ".pdf"}
FILED = {"id": "n-filed", "name": "x", "ext": ".pdf"}
NOSUCHKEY = b'<?xml version="1.0" encoding="UTF-8"?>\n<Error><Code>NoSuchKey</Code></Error>'


class _Seat:
    """A seat whose mint fails ``mint_fails`` times and whose fetch serves ``body``."""

    def __init__(self, body: bytes = b"x" * 10, mint_fails: int = 0) -> None:
        self.body, self.mint_fails = body, mint_fails
        self.fetches: list[Any] = []

    def mint(self, matter_id: str, ids: list[str]) -> list[dict[str, Any]]:
        if self.mint_fails:
            self.mint_fails -= 1
            raise RuntimeError("boom")
        return [{"id": i, "url": f"https://fake.invalid/{i}"} for i in ids]

    def fetch(self, url: str, dest: Path, expected: int | None) -> int:
        self.fetches.append(expected)
        dest.write_bytes(self.body)
        if expected is not None and expected != len(self.body):
            raise RuntimeError(f"size mismatch: got {len(self.body)}, expected {expected}; re-pull")
        return len(self.body)


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    out: list[float] = []
    monkeypatch.setattr(matter_pull.time, "sleep", out.append)
    return out


def test_each_lane_mints_by_its_own_declared_policy(sleeps: list[float]) -> None:
    said: list[str] = []
    got = matter_pull.mint(_Seat(mint_fails=2), "m", ["a"], said.append, matter_pull.STAGES)
    assert got == {"a": {"id": "a", "error": "mint failed twice: boom"}}
    assert sleeps == [5.0] and said == ["MINT FAIL (boom); retrying once"]
    sleeps.clear()
    said.clear()
    got = matter_pull.mint(_Seat(mint_fails=2), "m", ["a"], said.append, matter_pull.LANES)
    assert got == {"a": {"id": "a", "error": "mint failed twice"}}
    assert sleeps == [2.0, 4.0] and len(said) == 2
    assert matter_pull.mint(_Seat(mint_fails=1), "m", ["a"], said.append)["a"]["url"]


def test_a_file_edited_since_listing_is_taken_and_an_error_body_is_not(tmp_path: Path) -> None:
    seat = _Seat(b"y" * 50)
    got, changed = matter_pull.fetch_one(seat, "u", tmp_path / "a.pdf", 40, refuse_error_body=True)
    assert (got, changed) == (50, {"size_changed": {"listed": 40, "fetched": 50}})
    assert seat.fetches == [40, None]
    seat = _Seat(NOSUCHKEY)
    with pytest.raises(RuntimeError, match="size mismatch"):
        matter_pull.fetch_one(seat, "u", tmp_path / "b.pdf", 40, refuse_error_body=True)
    # Litigation keeps the body: it records the file as missing in Smokeball.
    assert matter_pull.fetch_one(_Seat(NOSUCHKEY), "u", tmp_path / "c.pdf", 40)[1]["size_changed"]


def test_the_chronology_download_records_the_edited_file(tmp_path: Path, sleeps: list[float]) -> None:
    from types import SimpleNamespace

    from jobs.stages import download

    sr = SimpleNamespace(seat=_Seat(b"z" * 30), job=SimpleNamespace(matter_id="m"))
    b = {"id": "a", "name": "A", "ext": ".pdf", "size": 20, "folderId": "f"}
    rec = download._pull_one(sr, b, {"url": "u"}, {"f": "/MEDICAL"}, tmp_path, {})  # type: ignore[arg-type]
    assert rec["ok"] and rec["size_got"] == 30 and rec["size_changed"] == {"listed": 20, "fetched": 30}


@pytest.mark.parametrize(
    ("joined", "shown"),
    [
        ("x.pdf.pdf", "x.pdf"),
        ("Scan.PDF.PDF", "Scan.PDF"),
        ("x.PDF.pdf", "x.PDF"),
        ("x.pdf", "x.pdf"),
        ("notes.txt.pdf", "notes.txt.pdf"),
        ("README", "README"),
    ],
)
def test_one_display_rule(joined: str, shown: str) -> None:
    assert filing.shown(joined) == shown


def test_the_display_rule_agrees_with_full_name_on_every_pulled_row() -> None:
    for row in (EMAILED, FILED, {"name": "Scan.PDF", "ext": ".PDF"}, {"name": "x.PDF", "ext": ".pdf"}):
        assert filing.shown(f"{row['name']}{row['ext']}") == filing.full_name(row)


def test_a_hold_line_names_the_email_as_the_firm_sees_it() -> None:
    from jobs.medchron import decisions
    from jobs.stages import identity

    hold = {"id": "msgatt-1", "name": "scan.pdf", "container": "Records.msg.msg"}
    for named in (decisions._named, identity._named):
        assert named(hold) == 'msgatt-1 "scan.pdf" attached to the email "Records.msg"'


def test_the_two_files_that_collide_once_shown_stay_two_keys(tmp_path: Path) -> None:
    from jobs.stages import assemble, chunking, coverage

    rows = [dict(EMAILED), dict(FILED)]
    keys = [coverage.file_key(r) for r in rows]
    assert keys == ["x.pdf.pdf", "x.pdf"]
    assert filing.shown(keys[0]) == filing.shown(keys[1])  # the collision display would make
    for r in rows:
        p = tmp_path / f"{r['id']}.txt"
        p.write_text(f"[p.1]\nthe text of {r['id']}", encoding="utf-8")
        r["text_path"] = str(p)
    heads = [line for c in chunking.build_chunks(rows, 10_000) for line in c.splitlines() if "=== FILE:" in line]
    assert heads == [
        "=== FILE: x.pdf.pdf (fileId n-emailed) ===",
        "=== FILE: x.pdf (fileId n-filed) ===",
    ]
    resolve = assemble.Resolver(keys, {r["id"]: k for r, k in zip(rows, keys)})
    assert [resolve("x.pdf.pdf"), resolve("x.pdf")] == keys
