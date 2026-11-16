"""Every residue report names the volume's stores of transcribed matter text,
each with the command that purges one matter and the probe that proves it
gone (wave 5: the job lanes' page store joined the litigation text cache)."""

from __future__ import annotations

from pathlib import Path

from bin.lib import seat_layout
from bin.lib.decommission_inventory import VOLUME_TEXT_STORES, residue_report


def test_the_residue_report_lists_each_volume_text_store_with_its_probe(tmp_path: Path) -> None:
    report = residue_report("acme", tmp_path, env={})
    stores = {s["path"]: s for s in report["removed_with_volume"]}
    assert len(stores) == len(VOLUME_TEXT_STORES) and all(s["probe"] and s["what"] for s in stores.values())
    page_store = stores[f"{seat_layout.JOBS_DATA}/litigation/textcache/"]
    assert page_store["probe"] == "python -m jobs.textcache probe --matter <id>"
    assert "pages/" in page_store["what"]
