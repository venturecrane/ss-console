"""`render` and `file`: the deliverables as the firm's files, checked, then onto
the matter in a dated folder of their own, read back by name and size.

The demand is rendered into the firm's own house file and ``format_check``
must pass before anything is written to the matter (the laptop's filing script
refused the same way, engagements #159). The upload is the chronology's own
stage (``stages/upload.py``) driven through a small adapter, so the demand job
inherits every lesson it paid for: the folder id recorded before the first
file, a send recorded as it happens so a lagging vendor list can never cause a
second copy, a folder of the same name that this job did not create refused,
and the read-back that composes the vendor's name and extension (ss#2914).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from ..stages import upload as upload_stage
from . import format_check, house, plain_docx

UNIT = "demand"


class FormatRefused(RuntimeError):
    def __init__(self, fails: list[str]) -> None:
        self.fails = fails
        super().__init__("; ".join(fails))


def names(firm: Any, job: Any, date_stamp: str) -> dict[str, str]:
    ctx = {"date": date_stamp, "matter_number": job.matter_number, "job": job.job_id[-6:]}
    d = firm.data["delivery"]
    return {
        "folder": d["folder_template"].format(**ctx),
        "gap_audit": d["gap_audit_name_template"].format(**ctx),
        "coverage": d["coverage_report_name_template"].format(**ctx),
    }


def render_demand(firm: Any, draft_md: str, out_dir: Path) -> tuple[Path, str, list[str]]:
    """The house file, its attorney notes, and the format check's notes.
    Raises FormatRefused when the file is off-format: nothing is filed."""
    fmt = firm.data["format"]
    author = firm.get("firm", "display_name")
    path, notes = house.render(
        draft_md,
        firm.input_path("house_reference"),
        out_dir,
        signature=fmt["firm_signature"],
        signer_title=fmt["signer_title"],
        author=author,
    )
    res = format_check.check(
        path,
        signature=fmt["firm_signature"],
        signer_title=fmt["signer_title"],
        footer_markers=list(fmt["footer_markers"]),
    )
    if not res.ok:
        raise FormatRefused(res.fails)
    return path, notes, res.notes


def render_plain(md: str, out_dir: Path, name: str, author: str) -> Path:
    return plain_docx.render(md, out_dir / name, title=name.rsplit(".", 1)[0], author=author)


def write_manifest(out_dir: Path, folder: str, files: list[Path]) -> list[dict[str, Any]]:
    manifest = []
    for p in files:
        data = p.read_bytes()
        manifest.append(
            {
                "name": p.name,
                "folder": folder,
                "local_path": str(p),
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )
    (out_dir / "upload_manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def file_to_matter(
    data: Path,
    seat: Any,
    matter_id: str,
    log: Callable[[str], None],
    *,
    pause: float = upload_stage.READBACK_PAUSE_SECONDS,
) -> dict[str, Any]:
    """Run the chronology's upload stage over ``data/out/demand``. Returns the
    delivery record; ``exit`` 0 is read back complete, 2 is short after the
    retries (the files may still be materializing), 1 is a refusal."""
    sr = SimpleNamespace(
        slug_dir=data,
        unit=SimpleNamespace(unit=UNIT),
        job=SimpleNamespace(matter_id=matter_id),
        seat=seat,
        log=log,
    )
    code = upload_stage.run(sr, pause=pause)  # type: ignore[arg-type]
    delivery_path = data / "runs" / UNIT / "delivery.json"
    delivery = json.loads(delivery_path.read_text(encoding="utf-8")) if delivery_path.is_file() else {}
    return {"exit": code, **delivery}


def out_dir(data: Path) -> Path:
    d = data / "out" / UNIT
    d.mkdir(parents=True, exist_ok=True)
    return d
