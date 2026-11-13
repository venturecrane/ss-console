"""One scene per filing lane, for the filing goldens (``test_filing_goldens.py``).

A scene is a matter on a ``filing_kit.RecordingSeat`` and the lane's own filing
call, set up for one ENDING. Each lane's call is the entry point the lane
itself makes, with the arguments it makes it with:

* ``chronology``: ``stages.upload.run`` (a full build's dated folder);
* ``update``: ``stages.update_upload.run`` (the update beside the delivered
  chronology, then its retirement by id);
* ``update_review``: ``stages.update_review_file.file`` (an already-current
  update's records review, filed beside it);
* ``demand`` and ``drafting``: ``demand.deliver.file_to_matter`` then
  ``demand.deliver.prove``, as ``demand/run.py`` and ``drafting/run.py`` call
  them (each lane's own file names);
* ``litigation``: ``litigation.deliver.file_workbook``, the list and then its
  per-case copy under its own record (``litigation/bookstages.py``);
* ``casefile``: the same call, a matter's File Status into its matter and the
  caseload workbook into the library matter (``casefile/deliver.py``);
* ``revise``: ``drafting.revise.versions.file_versions``;
* ``library``: ``library_setup.file.file_library`` through ``WriteLimited``.

What a lane does with the result (hold, fail, the next stage) is the lane's,
not the filing's, and is held by the lane endings set instead.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from filing_kit import LibraryClient, RecordingSeat
from jobs_testkit import FIRM_CONFIG, doc_row, job_yaml

MATTER = "00000000-0000-4000-8000-000000000001"
LIB = "00000000-0000-4000-8000-00000000a11b"
UPPER, NO_EXT, WHOLE = "shape_upper_ext", "shape_no_ext", "shape_ext_on_name"


@dataclass
class Scene:
    seat: RecordingSeat
    run: Callable[[], Any]
    #: The name the fault and the corrupt bytes target: the first file sent.
    first: str
    root: Path
    #: Rebuilds the first file with other bytes (and its manifest row), as a
    #: lane re-run between two attempts would; None where nothing is rebuilt.
    rebuild: Callable[[], None] | None = None
    #: Renders the first file again on a later day (another dated name, other
    #: bytes) and files that instead; None where no lane does.
    later: Callable[[], None] | None = None


REBUILT = b" (rebuilt)"


def rebuild_row(manifest_path: Path, name: str) -> Callable[[], None]:
    """Rewrite the manifest row ``name`` and its local file with other bytes."""

    def go() -> None:
        rows = json.loads(manifest_path.read_text(encoding="utf-8"))
        for m in rows:
            if m["name"] == name:
                local = Path(m["local_path"])
                local.write_bytes(local.read_bytes() + REBUILT)
                data = local.read_bytes()
                m.update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
        manifest_path.write_text(json.dumps(rows, indent=1), encoding="utf-8")

    return go


def rebuild_file(path: Path) -> Callable[[], None]:
    return lambda: path.write_bytes(path.read_bytes() + REBUILT)


def shaped(name: str, ending: str) -> str:
    """The first file's name in the ending's shape."""
    stem, _dot, ext = name.rpartition(".")
    if ending == UPPER:
        return f"{stem}.{ext.upper()}"
    if ending == NO_EXT:
        return stem
    return name


def _seat(root: Path, ending: str, watch: list[Path]) -> RecordingSeat:
    seat = RecordingSeat()
    seat.root, seat.watch, seat.whole_names = root, watch, ending == WHOLE
    return seat


def _firm_row(seat: RecordingSeat, fid: str, name: str, folder: str, data: bytes) -> None:
    """A file the firm (not this run) put in ``folder``: other bytes, same size."""
    seat.docs.append(doc_row(fid, name, folder, len(data)))
    seat.blobs[fid] = bytes(b ^ 0x55 for b in data)


def _manifest(out: Path, folder: str, files: list[tuple[str, bytes]]) -> list[dict[str, Any]]:
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, data in files:
        (out / name).write_bytes(data)
        rows.append(
            {
                "name": name,
                "folder": folder,
                "local_path": str(out / name),
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )
    (out / "upload_manifest.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return rows


def stage_run(root: Path, seat: RecordingSeat) -> Any:
    """A real ``StageRun`` over the synthetic firm and job (``jobs_testkit``)."""
    from jobs.medchron import config as config_mod, job as job_mod
    from jobs.stages.base import StageRun

    data_root = root / "data"
    (data_root / "example-matter").mkdir(parents=True, exist_ok=True)
    jd = root / "job"
    jd.mkdir(parents=True, exist_ok=True)
    (jd / "job.yaml").write_text(job_yaml(data_root), encoding="utf-8")
    firm = root / "firm.yaml"
    firm.write_text(yaml.safe_dump(FIRM_CONFIG, sort_keys=False), encoding="utf-8")
    job = job_mod.load(jd)
    return StageRun(
        job=job,
        cfg=config_mod.load(str(firm)),
        unit=job.units[0],
        slug_dir=data_root / "example-matter",
        decided={},
        log=seat.log,
        seat_factory=lambda: seat,
        date_stamp="08-29-26",
    )


# ---- a new dated folder ------------------------------------------------------------
CHRON_FOLDER = "MEDICAL CHRONOLOGY - Alpha Example 08-29-26"
CHRON_FILES = [
    ("Alpha Example - Medical Chronology 08-29-26.docx", b"chronology document bytes"),
    ("Exhibit 1 - Example Clinic - 01-20-2026 (Medical Records).pdf", b"%PDF-1 first exhibit"),
    ("Exhibit 2 - Example Imaging - 02-10-2026 (Medical Records).pdf", b"%PDF-1 second exhibit"),
]


def _dated(seat: RecordingSeat, record: Path, folder: str, first: tuple[str, bytes], ending: str) -> None:
    """The folder and name collisions a new dated folder can meet."""
    if ending == "folder_taken":
        seat.folders.append({"id": "fold-firm", "name": folder, "parentId": None, "path": f"/{folder}"})
    if ending == "name_taken":
        # A resume into the folder this run created, which now also holds a
        # file of the first name, at its size, that this run never sent.
        seat.folders.append({"id": "fold-ours", "name": folder, "parentId": None, "path": f"/{folder}"})
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps({"folder": folder, "folder_id": "fold-ours", "files": []}), encoding="utf-8")
        _firm_row(seat, "firm-1", first[0], "fold-ours", first[1])


def chronology(root: Path, ending: str, _mp: Any) -> Scene:
    from jobs.stages import upload

    record = root / "data" / "example-matter" / "runs" / "alpha" / "delivery.json"
    seat = _seat(root, ending, [record])
    sr = stage_run(root, seat)
    files = [(shaped(CHRON_FILES[0][0], ending), CHRON_FILES[0][1]), *CHRON_FILES[1:]]
    _manifest(sr.slug_dir / "out" / "alpha", CHRON_FOLDER, files)
    (sr.slug_dir / "runs" / "alpha").mkdir(parents=True, exist_ok=True)
    _dated(seat, record, CHRON_FOLDER, files[0], ending)
    mpath = sr.slug_dir / "out" / "alpha" / "upload_manifest.json"
    return Scene(seat, lambda: upload.run(sr, pause=0), files[0][0], root, rebuild_row(mpath, files[0][0]))


def _deliver(root: Path, ending: str, folder: str, files: list[tuple[str, str, bytes]]) -> Scene:
    from jobs.demand import deliver

    data = root / "job-data"
    record = data / "runs" / deliver.UNIT / "delivery.json"
    seat = _seat(root, ending, [record])
    out = deliver.out_dir(data)
    files = [(files[0][0], shaped(files[0][1], ending), files[0][2]), *files[1:]]
    paths = []
    for role, name, body in files:
        (out / name).write_bytes(body)
        paths.append((role, out / name))
    manifest = deliver.write_manifest(out, folder, paths)
    _dated(seat, record, folder, (files[0][1], files[0][2]), ending)

    def run() -> dict[str, Any]:
        rec = deliver.file_to_matter(data, seat, MATTER, seat.log, pause=0)
        if rec["exit"] != 0:
            return {"rec": rec}
        return {"rec": rec, "proof": deliver.prove(data, seat, MATTER, rec, manifest, seat.log, pause=0)}

    return Scene(seat, run, files[0][1], root, rebuild_row(out / "upload_manifest.json", files[0][1]))


def demand(root: Path, ending: str, _mp: Any) -> Scene:
    return _deliver(
        root,
        ending,
        "Demand 2099-EX-0001 11-13-26 (Operator 0000a1)",
        [
            ("demand", "Demand - Alpha Example 11-13-26.docx", b"demand letter bytes"),
            ("gap_audit", "Gap audit 11-13-26.docx", b"gap audit bytes"),
            ("attorney_notes", "Demand - Alpha Example 11-13-26 - attorney notes.docx", b"attorney notes bytes"),
        ],
    )


def drafting(root: Path, ending: str, _mp: Any) -> Scene:
    return _deliver(
        root,
        ending,
        "Draft 2099-EX-0001 11-13-26 (Operator 0000b2)",
        [
            ("draft", "Memo - Example points 11-13-26.docx", b"drafted memo bytes"),
            ("attorney_notes", "Memo - Example points 11-13-26 - attorney notes.docx", b"memo notes bytes"),
        ],
    )


# ---- the update into the delivered chronology's folder ------------------------------
UP_FOLDER = "fold-chron"
UP_OLD = "Alpha Example - Medical Chronology 09-01-26.docx"
UP_NEW = "Alpha Example - Medical Chronology 10-13-26.docx"
UP_EX2 = "Exhibit 2 - Example Imaging - 02-10-2026 (Medical Records).pdf"
UP_REVIEW = "Alpha Example - Records Review 10-13-26.docx"


def _update_scene(root: Path, ending: str, first: str) -> tuple[RecordingSeat, Any, Path]:
    sd = root / "data" / "example-matter"
    record = sd / "runs" / "alpha" / "delivery.json"
    seat = _seat(root, ending, [record, sd / "out" / "alpha" / "upload_manifest.json"])
    sr = stage_run(root, seat)
    ud = sd / "runs" / "alpha" / "update"
    ud.mkdir(parents=True, exist_ok=True)
    plan = {
        "chronology": {"id": "c1", "name": UP_OLD},
        "folder_id": UP_FOLDER,
        "folder_name": "CHRON",
        "exhibits": [{"exhibit": 1, "id": "e1", "name": "Exhibit 1 - x.pdf"}],
        "others": ["Attorney notes.docx"],
    }
    (ud / "update_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    seat.folders.append({"id": UP_FOLDER, "name": "CHRON", "parentId": None, "path": "/CHRON"})
    for fid, name, body in (("c1", UP_OLD, b"delivered chron"), ("e1", "Exhibit 1 - x.pdf", b"%PDF exhibit 1")):
        seat.docs.append(doc_row(fid, name, UP_FOLDER, len(body)))
        seat.blobs[fid] = body
    _firm_row(seat, "a1", "Attorney notes.docx", UP_FOLDER, b"the firm's own notes")
    if ending == "name_taken":
        _firm_row(seat, "firm-2", first, UP_FOLDER, b"a firm file of that name")
    (sd / "out" / "alpha").mkdir(parents=True, exist_ok=True)
    return seat, sr, sd / "out" / "alpha"


def update(root: Path, ending: str, _mp: Any) -> Scene:
    from jobs.stages import update_upload

    first = shaped(UP_NEW, ending)
    seat, sr, out = _update_scene(root, ending, first)
    _manifest(out, "CHRON", [(first, b"new chronology bytes"), (UP_EX2, b"%PDF new exhibit")])
    run = lambda: update_upload.run(sr, pause=0, tries=3)  # noqa: E731 - one call, named for the scene
    return Scene(seat, run, first, root, rebuild_row(out / "upload_manifest.json", first))


def update_review(root: Path, ending: str, _mp: Any) -> Scene:
    from jobs.stages import update_review_file

    first = shaped(UP_REVIEW, ending)
    seat, sr, out = _update_scene(root, ending, first)
    doc = out / first
    doc.write_bytes(b"records review bytes")
    # The driver renders the review again on every ending (outcome.end); the
    # scene files whatever document it rendered last.
    held = {"doc": doc}

    def later() -> None:
        held["doc"] = out / UP_REVIEW.replace("10-13-26", "10-14-26")
        held["doc"].write_bytes(b"records review bytes, rendered a day later")

    run = lambda: update_review_file.file(sr, held["doc"], pause=0, tries=3)  # noqa: E731 - one call, named for the scene
    return Scene(seat, run, first, root, rebuild_file(doc), later)


# ---- a standing folder ------------------------------------------------------------------
def _standing(root: Path, ending: str, calls: list[tuple[str, str, str, bytes, str]]) -> Scene:
    """``calls``: (matter, folder name, file name, bytes, record) in the lane's order."""
    from jobs.litigation import deliver

    data = root / "job-data"
    data.mkdir(parents=True, exist_ok=True)
    seat = _seat(root, ending, [data / c[4] for c in calls])
    first = shaped(calls[0][2], ending)
    calls = [(calls[0][0], calls[0][1], first, calls[0][3], calls[0][4]), *calls[1:]]
    if ending in ("folder_taken", "name_taken"):
        seat.folders.append({"id": "fold-standing", "name": calls[0][1], "parentId": None, "path": "/x"})
    if ending == "name_taken":
        _firm_row(seat, "firm-3", first, "fold-standing", calls[0][3])
    for _m, _f, name, body, _r in calls:
        (data / "out").mkdir(exist_ok=True)
        (data / "out" / name).write_bytes(body)

    def run() -> list[dict[str, Any]]:
        return [
            deliver.file_workbook(seat, m, f, data / "out" / n, data, seat.log, pause=0, record=r)
            for m, f, n, _b, r in calls
        ]

    return Scene(seat, run, first, root, rebuild_file(data / "out" / first))


def litigation(root: Path, ending: str, _mp: Any) -> Scene:
    return _standing(
        root,
        ending,
        [
            (
                LIB,
                "Litigation Status",
                "Litigation Status 2026-11-13 (Operator 0000c3).xlsx",
                b"PK list",
                "delivery.json",
            ),
            (
                LIB,
                "Litigation Status",
                "Litigation Status 2026-11-13 by case.xlsx",
                b"PK per case",
                "delivery-percase.json",
            ),
        ],
    )


def casefile(root: Path, ending: str, _mp: Any) -> Scene:
    return _standing(
        root,
        ending,
        [
            (MATTER, "File Status", "File Status 2099-EX-0001 2026-11-13.pdf", b"%PDF file status", "delivery.json"),
            (
                LIB,
                "Case File Manager",
                "Case File Manager 2026-11-13 (Operator 0000d4).xlsx",
                b"PK caseload",
                "wb.json",
            ),
        ],
    )


# ---- new versions by id, and the library -------------------------------------------------
def revise(root: Path, ending: str, _mp: Any) -> Scene:
    from jobs.drafting.revise import versions

    data = root / "job-data"
    data.mkdir(parents=True, exist_ok=True)
    seat = _seat(root, ending, [data / "versions.json"])
    ext = {UPPER: ".DOCX", NO_EXT: ""}.get(ending, ".docx")
    plan = []
    for role, fid, stem in (("draft", "draft-1", "Brief"), ("attorney_notes", "notes-1", "Brief - attorney notes")):
        base, new = f"filed {role}".encode(), f"revised {role}".encode()
        seat.docs.append(doc_row(fid, (stem + ext) if ending == WHOLE else stem, "fold-r", len(base), ext=ext))
        seat.blobs[fid] = base
        (data / f"new-{role}").write_bytes(new)
        plan.append(
            {
                "role": role,
                "file_id": fid,
                "name": stem + ext,
                "base_sha256": hashlib.sha256(base).hexdigest(),
                "base_version": f"{fid}-v1",
                "local_path": str(data / f"new-{role}"),
                "sha256": hashlib.sha256(new).hexdigest(),
                "bytes": len(new),
            }
        )
    first = f"{seat.docs[0]['name']}{seat.docs[0]['ext']}"
    run = lambda: versions.file_versions(seat, MATTER, plan, data, seat.log, folder_id="fold-r", pause=0)  # noqa: E731 - one call, named for the scene
    return Scene(seat, run, first, root)


def library(root: Path, ending: str, mp: Any) -> Scene:
    import library_setup_testkit as lk
    from jobs.library_setup import file as file_mod

    root.mkdir(parents=True, exist_ok=True)
    lk.customer_yaml(root / "customer.yaml", mp)
    seat = _seat(root, ending, [])
    client = LibraryClient(seat, lk.LIB_MATTER, lk.LIB_NUMBER, lk.FOLDER, "Document Library")
    name = "Template - Memo.docx"
    if ending == "name_taken":
        seat.docs.append(doc_row("lib-7", name, lk.FOLDER, len(b"the old memo")))
        seat.blobs["lib-7"] = b"the old memo"
    made = b"the made memo template"

    def run() -> Any:
        return file_mod.file_library(file_mod.WriteLimited(client), "memo", None, made, pause=lambda _s: None)

    return Scene(seat, run, name, root)


LANES: dict[str, Callable[[Path, str, Any], Scene]] = {
    "chronology": chronology,
    "update": update,
    "update_review": update_review,
    "demand": demand,
    "drafting": drafting,
    "litigation": litigation,
    "casefile": casefile,
    "revise": revise,
    "library": library,
}

__all__ = ["LANES", "MATTER", "Scene", "shaped", "stage_run"]
