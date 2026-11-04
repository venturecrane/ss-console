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
Then ``prove``: every filed file downloaded by its id and its sha256 compared
with the bytes built, before the job may say delivered (2026-10-13).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from ..stages import upload as upload_stage
from . import format_check, house, house_numbered, plain_docx

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


def front_keys(firm: Any) -> tuple[str, ...]:
    """The selected format's front-matter keys (the firm's house format when
    no variant is selected)."""
    if "_variant" not in firm.data:
        return house.FRONT_KEYS
    return house_numbered.FRONT_KEYS if firm.variant.get("layout") == "numbered" else house.FRONT_KEYS


def render_demand(firm: Any, draft_md: str, out_dir: Path, client_label: str) -> tuple[Path, str, list[str]]:
    """The house file, its attorney notes, and the format check's notes.
    Raises FormatRefused when the file is off-format: nothing is filed.

    The layout is the responsible attorney's format (``firm.variant``). The
    title may name the client (``Attorney for {client}``): it is filled from
    ``client_label``, the matter record's own client name, never the model's."""
    fmt, v, who = firm.data["format"], firm.variant, firm.attorney
    author = firm.get("firm", "display_name")
    title = who["title"].replace("{client}", client_label)
    if v.get("layout") == "numbered":
        path, notes = house_numbered.render(
            draft_md,
            firm.input_path("house_reference"),
            out_dir,
            variant=v,
            signature=fmt["firm_signature"],
            signer=who["signer"],
            signer_title=title,
            author=author,
            client_label=client_label,
            initials=who["initials"],
        )
    else:
        path, notes = house.render(
            draft_md,
            firm.input_path("house_reference"),
            out_dir,
            signature=fmt["firm_signature"],
            signer_title=title,
            author=author,
            client_label=client_label,
            signer=who["signer"],
            initials=who["initials"],
            mail_line=v["mail_line"],
        )
    res = format_check.check(
        path,
        signature=fmt["firm_signature"],
        signer_title=title,
        footer_markers=list(v.get("footer_markers") or fmt["footer_markers"]),
        variant=format_check.Variant.from_config(v),
        signer=who["signer"],
        initials=who["initials"],
    )
    if not res.ok:
        raise FormatRefused(res.fails)
    return path, notes, res.notes


def render_plain(md: str, out_dir: Path, name: str, author: str) -> Path:
    return plain_docx.render(md, out_dir / name, title=name.rsplit(".", 1)[0], author=author)


def write_manifest(out_dir: Path, folder: str, files: list[tuple[str, Path]]) -> list[dict[str, Any]]:
    """``files`` are (role, path). The role (demand, gap_audit, attorney_notes,
    coverage_report) is the code-authored label the wake carries instead of a
    file name."""
    manifest = []
    for role, p in files:
        data = p.read_bytes()
        manifest.append(
            {
                "name": p.name,
                "role": role,
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
    said: list[str] = []

    def _log(line: str) -> None:
        said.append(line)
        log(line)

    sr = SimpleNamespace(
        slug_dir=data,
        unit=SimpleNamespace(unit=UNIT),
        job=SimpleNamespace(matter_id=matter_id),
        seat=seat,
        log=_log,
    )
    code = upload_stage.run(sr, pause=pause)  # type: ignore[arg-type]
    delivery_path = data / "runs" / UNIT / "delivery.json"
    delivery = json.loads(delivery_path.read_text(encoding="utf-8")) if delivery_path.is_file() else {}
    # The upload stage's own last sentence is the reason (every numbered
    # folder name already taken, local bytes changed, no folder id, an empty
    # manifest; a same-name folder it did not create is no longer one, the
    # stage numbers past it): one exit code covers all four, so the caller
    # must not guess which.
    return {"exit": code, "said": said[-1] if said else "", **delivery}


def prove(
    data: Path,
    seat: Any,
    matter_id: str,
    rec: dict[str, Any],
    manifest: list[dict[str, Any]],
    log: Callable[[str], None],
    *,
    pause: float | None = None,
) -> dict[str, Any]:
    """The read-back half of the proof block, by file id, size and sha256
    (2026-10-13: name and size alone read a same-length wrong file as
    delivered). The upload stage is being taught to prove each row itself
    (``proven``, ``file_id``, ``sha256`` on ``delivery.json``); when every
    manifest row already carries that, it is used as it stands. Otherwise this
    downloads and hashes each file here, so the demand and drafting jobs never
    depend on which side landed first. Raises ``ReadbackShort`` (a lag;
    resumable) or ``ReadbackMismatch`` (the bytes there are not ours)."""
    from ..proof import readback

    rows = {str(f.get("name")): f for f in (rec.get("files") or [])}
    folder_id = str(rec.get("folder_id") or "")
    files = []
    for m in manifest:
        r = rows.get(str(m["name"])) or {}
        if not (r.get("proven") is True and r.get("file_id") and r.get("sha256") == m["sha256"]):
            files = []
            break
        files.append({"name": m["name"], "file_id": str(r["file_id"]), "size": int(m["bytes"]), "sha256": m["sha256"]})
    if files:
        log(f"read-back proven by the upload stage: {len(files)} file(s) by id, size and sha256")
        return {"readback": "confirmed", "folder_id": folder_id, "files": files}
    expected = [{"name": m["name"], "sha256": m["sha256"], "bytes": m["bytes"]} for m in manifest]
    kw: dict[str, Any] = {} if pause is None else {"pause": pause}
    return readback.confirm_files(seat, matter_id, folder_id, expected, data / "readback", log=log, **kw)


def out_dir(data: Path) -> Path:
    d = data / "out" / UNIT
    d.mkdir(parents=True, exist_ok=True)
    return d
