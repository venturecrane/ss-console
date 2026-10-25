"""content_dupes (2026-10-25, the Pamplona job): a pulled document whose every
page another pulled document holds is read once, before vision pays for it
twice, and Records Reviewed and Limitations says so in words that are true of
a file whose bytes differ. Plus the orphan reasons Limitations re-derives from
the firm's current exclusion rules.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import yaml

from medchron import config as config_mod, job as job_mod
from medchron.gates import foreign_identity as FI
from medchron.stages import content_dupes as CD, extract, limitations
from medchron.stages.base import StageRun
from medchron_testkit import FIRM_CONFIG, FakeSeat, job_yaml
from test_exhibit_merge import PAGES, T, pdf

DOCS = {
    "f1": ("kaiser er", PAGES[2], {}),
    "f2": ("kaiser er copy", PAGES[2], {"title": "saved again"}),
    "f3": ("ucd export", PAGES[4], {}),
    "f4": ("ucd export 2", PAGES[5], {"at": (110, 140)}),
    "f5": ("other", PAGES[6], {}),
    "f6": ("kaiser partial", [T("K", 1), T("K", 2), T("X", 9)], {}),
    "f7": ("fax scan", [1, 2, 3], {}),
    "f8": ("fax scan again", [1, 2, 3], {"title": "rescanned name"}),
}


def _sr(job_dir: Path, firm: Path, data_root: Path, cfg=None) -> tuple[StageRun, list[str]]:
    job = job_mod.load(job_dir)
    log: list[str] = []
    sr = StageRun(
        job=job,
        cfg=cfg or config_mod.load(str(firm)),
        unit=job.units[0],
        slug_dir=data_root / "example-matter",
        decided={},
        log=log.append,
        seat_factory=lambda: FakeSeat([], [], {}),
    )
    return sr, log


def _pulled(
    job_dir: Path, firm: Path, data_root: Path, folders: dict[str, str] | None = None
) -> tuple[StageRun, list[str]]:
    sr, log = _sr(job_dir, firm, data_root)
    sd = sr.slug_dir
    (sd / "raw").mkdir()
    rows = []
    for fid, (name, pages, kw) in DOCS.items():
        p = sd / "raw" / f"{fid}.pdf"
        p.write_bytes(pdf(pages, kw.get("at", (72, 72)), title=kw.get("title", "")))
        folder = (folders or {}).get(fid, "/MEDICAL")
        rows.append({"id": fid, "name": name, "ext": ".pdf", "folder": folder, "ok": True, "path": str(p)})
    (sd / "raw_manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert extract.run(sr) == 0
    return sr, log


def _rows(sd: Path) -> list[dict]:
    return [json.loads(x) for x in (sd / "raw_manifest.jsonl").read_text().splitlines()]


def test_a_copy_in_other_bytes_is_marked_on_its_own_row_and_read_once(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sr, log = _pulled(job_dir, firm_config_path, data_root)
    sd = sr.slug_dir
    assert (sd / "raw" / "f1.pdf").read_bytes() != (sd / "raw" / "f2.pdf").read_bytes()
    assert CD.run(sr) == 0, log
    rows = _rows(sd)
    assert len(rows) == len(DOCS)  # rewritten in place, never a second row
    marked = {r["id"]: (r["duplicate_of"], r["duplicate_kind"]) for r in rows if r.get("duplicate_of")}
    assert marked == {"f2": ("f1", "content"), "f4": ("f3", "content"), "f8": ("f7", "content")}
    assert all(r["path"] for r in rows)  # the copy's bytes stay
    left = {json.loads(x)["id"] for x in (sd / "extracted.jsonl").read_text().splitlines()}
    assert left == {"f1", "f3", "f5", "f6", "f7"}  # the partial overlap is read
    assert all(x["id"] not in marked for x in json.loads((sd / "scan_queue.json").read_text()))
    assert extract.live_rows(rows)[0].keys() == left  # extract would retire them the same way
    assert json.loads((sd / "text_duplicates.json").read_text()) == [
        {"kept": "kaiser er.pdf", "dropped": "kaiser er copy.pdf", "kind": "content"},
        {"kept": "ucd export.pdf", "dropped": "ucd export 2.pdf", "kind": "content"},
        {"kept": "fax scan.pdf", "dropped": "fax scan again.pdf", "kind": "content"},
    ]
    text = "\n".join(limitations.section(sd, "alpha", lambda _m: None))
    assert "contain the same pages although the files themselves differ. One copy was read" in text
    assert '* "kaiser er copy.pdf" contains the same pages as "kaiser er.pdf"' in text
    assert "byte-identical" not in text and "exact copy" not in text  # never called a byte copy
    assert "prepared from 5 documents" in text
    # a second run finds nothing new and changes nothing
    snap = {p: p.read_bytes() for p in sd.rglob("*") if p.is_file()}
    assert CD.run(sr) == 0
    assert {p: p.read_bytes() for p in sd.rglob("*") if p.is_file()} == snap


def test_nothing_is_marked_once_exhibits_are_built(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    sr, log = _pulled(job_dir, firm_config_path, data_root)
    sd = sr.slug_dir
    (sd / "out" / "alpha").mkdir(parents=True)
    (sd / "out" / "alpha" / "page_map.json").write_text("[]")
    snap = {p: p.read_bytes() for p in sd.rglob("*") if p.is_file()}
    assert CD.run(sr) == 0
    assert {p: p.read_bytes() for p in sd.rglob("*") if p.is_file()} == snap
    assert "exhibit_merge's to settle" in log[-1]


def test_a_job_resumed_past_routing_marks_nothing(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    sr, log = _pulled(job_dir, firm_config_path, data_root)
    (sr.slug_dir / "units").mkdir()
    snap = {p: p.read_bytes() for p in sr.slug_dir.rglob("*") if p.is_file()}
    assert CD.run(sr) == 0
    assert {p: p.read_bytes() for p in sr.slug_dir.rglob("*") if p.is_file()} == snap
    assert "already routed" in log[-1]


def test_on_a_joint_matter_each_clients_copy_is_that_clients_record(
    tmp_path: Path, firm_config_path: Path, data_root: Path
) -> None:
    jd = tmp_path / "joint"
    jd.mkdir()
    (jd / "job.yaml").write_text(job_yaml(data_root, joint=True))
    folders = {"f1": "/Alpha_Example/MED", "f2": "/Beta_Example/MED", "f3": "/Alpha_Example", "f4": "/Alpha_Example/x"}
    sr, log = _pulled(jd, firm_config_path, data_root, folders)
    assert CD.run(sr) == 0, log
    marked = {r["id"] for r in _rows(sr.slug_dir) if r.get("duplicate_of")}
    assert "f2" not in marked and "f4" in marked


def test_template_pages_that_pair_shifted_are_never_marked(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sr, log = _sr(job_dir, firm_config_path, data_root)
    sd = sr.slug_dir
    (sd / "raw").mkdir()
    rows = []
    for fid, title in (("t1", ""), ("t2", "again")):
        p = sd / "raw" / f"{fid}.pdf"
        p.write_bytes(pdf([T("Template", 0)] * 5, title=title))
        rows.append({"id": fid, "name": f"form {fid}", "ext": ".pdf", "folder": "/M", "ok": True, "path": str(p)})
    (sd / "raw_manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert extract.run(sr) == 0
    assert CD.run(sr) == 0
    assert not any(r.get("duplicate_of") for r in _rows(sd))
    assert any("CANNOT FAIL" in line for line in log), log


def test_a_byte_copy_keeps_its_own_wording(data_root: Path) -> None:
    sd = data_root / "example-matter"
    rows = [
        {"id": "a", "name": "note", "ext": ".pdf", "folder": "/M", "ok": True},
        {"id": "b", "name": "note dup", "ext": ".pdf", "folder": "/M", "ok": True, "duplicate_of": "a"},
    ]
    (sd / "raw_manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    text = "\n".join(limitations.section(sd, "alpha", lambda _m: None))
    assert '"note dup" (/M) is an exact copy of "note" (/M)' in text


# ---- orphan reasons from the current rules ---------------------------------------------------
def test_an_orphan_reason_follows_the_current_rules_but_a_withheld_one_never_moves(
    tmp_path: Path, job_dir: Path, data_root: Path
) -> None:
    firm = copy.deepcopy(FIRM_CONFIG)
    firm["coverage"]["exclusions"] = [
        {"match": r"\blien", "reason": "lien paperwork, not a treatment record"},
        {"match": r"\bphotos?\b", "reason": "a photograph, not a treatment record"},
    ]
    p = tmp_path / "firm2.yaml"
    p.write_text(yaml.safe_dump(firm, sort_keys=False))
    cfg = config_mod.load(str(p))
    sd = data_root / "example-matter"
    rows = [
        {"id": "o1", "name": "Photo of client route", "ext": ".docx", "ok": True},
        {"id": "o2", "name": "Photo withheld", "ext": ".pdf", "ok": True},
        {"id": "o3", "name": "Unrelated letter", "ext": ".pdf", "ok": True},
    ]
    (sd / "raw_manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (sd / "orphans.json").write_text(
        json.dumps(
            {
                "orphans": [
                    {"name": "Photo of client route.docx", "reason": "excluded by the file-name rule"},
                    {"name": "Photo withheld.pdf", "reason": FI.WITHHELD_REASON},
                    {"name": "Unrelated letter.pdf", "reason": "correspondence, not a treatment record"},
                ]
            }
        )
    )
    text = "\n".join(limitations.section(sd, "alpha", lambda _m: None, cfg))
    assert '* "Photo of client route.docx" - a photograph, not a treatment record' in text
    assert f'* "Photo withheld.pdf" - {FI.WITHHELD_REASON}' in text
    assert '* "Unrelated letter.pdf" - correspondence, not a treatment record' in text
    # the falsifier: without the config the stored reason stands
    text = "\n".join(limitations.section(sd, "alpha", lambda _m: None))
    assert '* "Photo of client route.docx" - excluded by the file-name rule' in text
