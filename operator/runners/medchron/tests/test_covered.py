"""What a delivery COVERED, in document ids (`medchron/covered.py`).

The record an UPDATE reads its delta from. Every assertion here fails if the
sets were taken from the download log's `ok` flag instead of the coverage
gate's accounting, which is the whole point: `ok` means bytes arrived, and a
contentless scan, an orphan and a name-excluded form are all `ok`.
"""

from __future__ import annotations

import json
from pathlib import Path

from medchron import config as config_mod, job as job_mod
from medchron.covered import covered_payload, covered_sets, merge_covered


def _cfg(firm_config_path: Path):
    return config_mod.load(str(firm_config_path))


def _seed(d: Path) -> None:
    """One unit, five documents, each a different disposition:

    clinic note   cited in the delivered document        -> covered
    bill          in the authored billing-chart set       -> covered
    dupe          byte-duplicate of the clinic note       -> covered
    junk scan     contentless, `compose_skip` on the unit -> UNCOVERED
    torn page     retrieval failed                        -> UNCOVERED
    """
    (d / "units").mkdir(parents=True, exist_ok=True)
    (d / "units" / "alpha.json").write_text(
        json.dumps(
            [
                {"id": "a", "name": "clinic note", "ext": ".pdf"},
                {"id": "b", "name": "bill", "ext": ".pdf"},
                {"id": "c", "name": "junk scan", "ext": ".pdf", "compose_skip": "no text layer"},
            ]
        )
    )
    (d / "raw_manifest.jsonl").write_text(
        "".join(
            json.dumps(r) + "\n"
            for r in [
                {"id": "a", "name": "clinic note", "ext": ".pdf", "ok": True, "sha256": "x"},
                {"id": "b", "name": "bill", "ext": ".pdf", "ok": True},
                {"id": "c", "name": "junk scan", "ext": ".pdf", "ok": True},
                {"id": "d", "name": "dupe", "ext": ".pdf", "ok": True, "duplicate_of": "a"},
                {"id": "e", "name": "torn page", "ext": ".pdf", "ok": False, "error": "no url"},
            ]
        )
    )
    (d / "billing_docs.json").write_text(json.dumps({"docs": [{"name": "bill", "path": "x", "pages": 1}]}))
    rd = d / "runs" / "alpha"
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "entries_final.md").write_text("01/02/2026\nExample Clinic\n\nStrain. (Exhibit 1 - p. 1)\n")
    (d / "out" / "alpha").mkdir(parents=True, exist_ok=True)
    (d / "out" / "alpha" / "page_map.json").write_text(
        json.dumps([{"exhibit": 1, "files": [{"file": "clinic note.pdf"}]}])
    )


def test_coverage_follows_the_document_not_the_download(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    job = job_mod.load(job_dir)
    d = Path(job.data_root) / job.slug
    d.mkdir(parents=True, exist_ok=True)
    _seed(d)

    sets = covered_sets(d, "alpha", _cfg(firm_config_path))

    # The cited document, its byte-duplicate, and the billing-chart document are
    # accounted for in the delivery. Sourcing from `ok` would have added the junk
    # scan here and dropped it from every later update.
    assert sets["covered"] == ["a", "b", "d"]
    # The contentless scan and the failed retrieval are NOT in the delivered
    # record, so an update reads them again.
    assert sets["uncovered"] == ["c", "e"]
    # Every pulled document is in exactly one set: the broker refuses a payload
    # where they do not sum, because that is a stage having dropped rows.
    assert sets["pulled"] == 5
    assert not set(sets["covered"]) & set(sets["uncovered"])


def test_no_coverage_artifacts_reports_no_record_rather_than_an_empty_one(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """A run that left nothing to read must not claim it covered nothing:
    "nothing was covered" and "nobody recorded what was covered" send an update
    in opposite directions."""
    job = job_mod.load(job_dir)
    d = Path(job.data_root) / job.slug
    d.mkdir(parents=True, exist_ok=True)
    assert covered_payload(d, ["alpha"], _cfg(firm_config_path)) is None


def test_the_union_across_units_keeps_the_uncovered_reading(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    merged = merge_covered(
        [
            {"outcome": "delivered", "covered": {"covered": ["a", "b"], "uncovered": ["c"]}},
            # The same document, read for another unit, came back contentless.
            {"outcome": "delivered", "covered": {"covered": ["d"], "uncovered": ["b"]}},
        ]
    )
    assert merged == {"covered": ["a", "d"], "uncovered": ["b", "c"], "pulled": 4}
    assert merge_covered([{"outcome": "delivered"}]) is None


def _seed_joint(d: Path, *, units_spec: bool = True) -> None:
    """A joint matter: two clients, one manifest, one unit per client.

    alpha cites its own file a1; beta cites its own file b1; o1 belongs to no
    unit (a documented orphan) and f1 never arrived.
    """
    (d / "units").mkdir(parents=True, exist_ok=True)
    if units_spec:
        (d / "units.json").write_text(json.dumps({"alpha": {"name_token": "Alpha"}, "beta": {"name_token": "Beta"}}))
    (d / "units" / "alpha.json").write_text(json.dumps([{"id": "a1", "name": "alpha clinic", "ext": ".pdf"}]))
    (d / "units" / "beta.json").write_text(json.dumps([{"id": "b1", "name": "beta hospital", "ext": ".pdf"}]))
    (d / "raw_manifest.jsonl").write_text(
        "".join(
            json.dumps(r) + "\n"
            for r in [
                {"id": "a1", "name": "alpha clinic", "ext": ".pdf", "ok": True},
                {"id": "b1", "name": "beta hospital", "ext": ".pdf", "ok": True},
                {"id": "o1", "name": "loose page", "ext": ".pdf", "ok": True},
                {"id": "f1", "name": "torn page", "ext": ".pdf", "ok": False, "error": "no url"},
            ]
        )
    )
    (d / "orphans.json").write_text(json.dumps({"orphans": [{"name": "loose page.pdf", "reason": "no folder"}]}))
    for unit, fname in (("alpha", "alpha clinic.pdf"), ("beta", "beta hospital.pdf")):
        rd = d / "runs" / unit
        rd.mkdir(parents=True, exist_ok=True)
        (rd / "entries_final.md").write_text("01/02/2026\nExample Clinic\n\nStrain. (Exhibit 1 - p. 1)\n")
        (d / "out" / unit).mkdir(parents=True, exist_ok=True)
        (d / "out" / unit / "page_map.json").write_text(json.dumps([{"exhibit": 1, "files": [{"file": fname}]}]))


def _matter_dir(job_dir: Path) -> Path:
    job = job_mod.load(job_dir)
    d = Path(job.data_root) / job.slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_a_joint_matter_records_every_clients_cited_files_covered(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """2026-10-28: each unit judged the WHOLE manifest, so each client's cited
    files read NOT EXPLAINED in the other client's record and the union made
    every cited exhibit of both clients uncovered; the next update re-read and
    re-filed exhibits the firm already had."""
    d = _matter_dir(job_dir)
    _seed_joint(d)
    cfg = _cfg(firm_config_path)

    alpha = covered_sets(d, "alpha", cfg)
    beta = covered_sets(d, "beta", cfg)
    # Each unit judges only its own files plus the rows no unit adopted.
    assert "b1" not in alpha["covered"] + alpha["uncovered"]
    assert "a1" not in beta["covered"] + beta["uncovered"]

    payload = covered_payload(d, ["alpha", "beta"], cfg)
    assert payload is not None
    assert payload["covered"] == ["a1", "b1"]
    # The orphan and the failed retrieval stay uncovered: an update reads them.
    assert payload["uncovered"] == ["f1", "o1"]
    assert payload["pulled"] == 4
    # The daemon's path (one record per outcome) unions the same way.
    assert merge_covered([{"covered": alpha}, {"covered": beta}]) == payload


def test_a_joint_matter_without_a_units_spec_reads_the_unit_files_present(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    d = _matter_dir(job_dir)
    _seed_joint(d, units_spec=False)
    payload = covered_payload(d, ["alpha", "beta"], _cfg(firm_config_path))
    assert payload is not None and payload["covered"] == ["a1", "b1"]


def test_a_unit_file_outside_the_jobs_spec_is_not_read_as_this_jobs(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """A unit file left by a different job on the matter adopts nothing here:
    skipping its ids would drop them from both sets."""
    d = _matter_dir(job_dir)
    _seed(d)
    (d / "units.json").write_text(json.dumps({"alpha": {}}))
    (d / "units" / "stale.json").write_text(json.dumps([{"id": "c"}, {"id": "e"}]))
    sets = covered_sets(d, "alpha", _cfg(firm_config_path))
    assert sets["covered"] == ["a", "b", "d"]
    assert sets["uncovered"] == ["c", "e"]
