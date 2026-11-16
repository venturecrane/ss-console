"""How every lane reads a firm's file, downloads it, and names it, recorded from today's code.

Wave 5 puts every jobs lane on one reader (``jobs/matter/``), one downloader and
one store of paid page text, moves the connector to reading page by page, and
de-doubles the file names a firm and a model are shown. This family is the
instrument that proves each of those moves changed only what it names. It
records at the process edges only (``reader_kit``: the model client, the seat,
sleep, the file tree), so a PR that moves code between modules changes no cell
and needs no instrument entry. The cells:

* ``lanes/<lane>``: every fixture (``reader_fixtures``) through the lane's own
  entry point (``reader_lanes``): its text page by page, its record, the pages
  sent to paid reading (page, render size, request shape), the deferred pages
  and failure records as the lane writes them, and the store the run leaves;
  litigation also pins ``textcache.method_version`` for three model ids.
* ``pulls/<site>/<scenario>``: each download site (``reader_pulls``) through
  each scenario: the seat's mint and fetch calls in order, every sleep, and the
  rows the site wrote.
* ``connector/<mode>``: the connector's ``extract_text_ex`` per fixture: text,
  method, reason, and the pages sent to vision (``reader_connector``).
* ``names/<site>``: every display or key join of a file's name and extension,
  the string it produced for each fixture row (``reader_names``).

What a refactor keeps, or names. The instrument files are pinned by sha256
(``golden_manifest.READER_FROZEN``); a change to one is a ``<normaliser>`` entry
in ``bin/goldens/allowed-instrument-changes.json``. So the names the cells call
stay importable with the call shapes they use: each module's docstring lists
its own (``reader_lanes``, ``reader_pulls``, ``reader_names``,
``reader_connector``). What is recorded at the edges: every
``messages.create`` request (model, max tokens, a sha256 of its system prompt
and of its text blocks, its keys, its block types, and each page's identity and
render size); every seat call in order with its arguments and answer; every
``time.sleep`` (looked up on the ``time`` module at call time); the return or
the exception; and every file under each store root and each site's own dir.

``goldens/reader/<cell>.json`` holds each one. A difference fails, except the
dotted paths ``allowed-changes.json`` names for that cell (``golden-integrity.py``
regenerates every cell from the merge-base tree). ``JOBS_WRITE_KIT_GOLDENS=1``
rewrites them (then read the diff).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest

import goldens_kit as gk
import reader_connector
import reader_lanes
import reader_names
import reader_pulls

FAMILY = "reader"
ROOT = gk.GOLDENS / FAMILY
ALLOWED = gk.allowed(FAMILY)
#: Every store a reader may write through an environment variable, pointed at
#: the test's own directory (never ``/opt/data``). ``JOBS_TEXTCACHE_DIR`` is the
#: one store of paid page text wave 5 adds: it is set now, so the cell that
#: first sees it written records it (``env_stores``).
STORE_ENV = {
    "JOBS_DATA_DIR": "volume",
    "JOBS_TEXTCACHE_DIR": "page-store",
    "JOBS_LITIGATION_TEXTCACHE_DIR": "litigation-textcache",
    "SMOKEBALL_EXTRACT_CACHE_DIR": "connector-cache",
    "SMD_POST_JOURNAL_DIR": "post-journal",
}


@pytest.fixture(autouse=True)
def _stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for var, sub in STORE_ENV.items():
        monkeypatch.setenv(var, str(tmp_path / sub))


def _cells() -> dict[str, Callable[[Path, Any], Any]]:
    out: dict[str, Callable[[Path, Any], Any]] = {}
    for lane, fn in reader_lanes.LANES.items():
        out[f"lanes/{lane}"] = fn
    for site in reader_pulls.SITES:
        for scenario in reader_pulls.SCENARIOS:
            out[f"pulls/{site}/{scenario}"] = reader_pulls.cell(site, scenario)
    for mode, fn in reader_connector.MODES.items():
        out[f"connector/{mode}"] = fn
    for site, fn in reader_names.SITES.items():
        out[f"names/{site}"] = fn
    return out


RUNS = _cells()
#: Every cell this family records (``golden_manifest.py`` pins the list, so a
#: dropped cell is a named instrument change).
CELLS: tuple[str, ...] = tuple(sorted(RUNS))
#: Cells declared not applicable, each with its reason (pinned the same way).
NOT_APPLICABLE: dict[str, str] = {
    **reader_pulls.NOT_APPLICABLE,
    "fixtures/msg_container": (
        "a real Outlook .msg needs an OLE compound-file writer no test dependency has; every lane opens .msg through "
        "extract_msg.Message, which reader_lanes.FakeMsg replaces at that library edge"
    ),
}


def test_every_golden_cell_is_declared_and_recorded() -> None:
    on_disk = {
        p.relative_to(ROOT).with_suffix("").as_posix() for p in ROOT.rglob("*.json") if p.name != "allowed-changes.json"
    }
    assert on_disk == set(CELLS), "a golden without a cell, or a cell without a golden"
    assert set(ALLOWED) - {"_why"} <= set(CELLS), "an allowed entry names no cell"
    assert all(len(why) > 40 for why in NOT_APPLICABLE.values()), "each not-applicable cell says why"


@pytest.mark.parametrize("cell", CELLS)
def test_a_reader_cell_records_what_its_golden_holds(
    cell: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import reader_kit as rk

    got = RUNS[cell](tmp_path, monkeypatch)
    got = {"cell": got, "env_stores": {v: rk.tree(tmp_path / sub, tmp_path) for v, sub in STORE_ENV.items()}}
    bad = gk.write_or_compare(ROOT / f"{cell}.json", got, ALLOWED.get(cell, []))
    if gk.writing():
        _prettier(ROOT / f"{cell}.json")
    assert not bad, f"{cell}: differs at {bad[:20]}"


REPO = Path(__file__).resolve().parents[4]


def _prettier(path: Path) -> None:
    """A rewritten golden in the repo's own JSON style, so the format check
    (``npm run format:check``) passes on the file as written and a rewrite of
    unchanged behaviour is byte for byte the committed file. The comparison
    reads JSON, so a tree without node (the integrity job's base) compares the
    same; there the file is left as ``goldens_kit.dump`` wrote it."""
    import shutil
    import subprocess

    exe = REPO / "node_modules" / ".bin" / "prettier"
    if not exe.exists() or shutil.which("node") is None:
        return
    subprocess.run([str(exe), "--write", "--log-level", "warn", str(path)], cwd=REPO, check=True)  # noqa: S603 - the repo's own pinned formatter on a file this test just wrote
