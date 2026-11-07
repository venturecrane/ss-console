"""Score one job folder: its filed Word documents against its record, five tests.

THE INPUTS, all read from disk, none written but the score:

* ``<job>/data/library-resolution.json``: the record (``caller.build_record``);
* ``<job>/data/out/demand/upload_manifest.json``: what was filed, with each
  file's sha256 (both lanes write it; ``demand/deliver.write_manifest``). The
  documents scored are the ``draft`` and ``demand`` roles; the attorney notes
  and reports are not made in a library file. A filed file whose bytes no
  longer match the manifest is never scored as if it were the filed one;
* the file the document was made in, found by its recorded sha256 among
  :func:`places`: the job's own data folder (the accepted marked file and the
  demand reference are written there, ``library-base.docx`` and
  ``library-reference.docx``; a used firm edit as ``library-edit.docx``), the
  seat's kept copies (``copies_dir``), and the lanes' firm inputs (a pinned
  fallback). Bytes are matched by sha256 only, never by name.

A record the toolkit cannot read (``caller.record_problems``) scores every test
``not_recorded``. A ``reader`` key (design piece 7, not yet written by any
caller) is set aside for that check and read by test (c) alone.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from pathlib import Path
from typing import Any

from operator_connector_sdk.library import copies_dir
from operator_connector_sdk.library import roster as roster_mod
from operator_connector_sdk.library.caller import (
    ACCEPTED,
    BY_NAME,
    EDIT_USED,
    FIRM_EDIT as EDIT_KEY,
    PINNED,
    RECORD_NAME,
    record_problems,
)

from . import (
    ATTORNEY,
    FAIL,
    FIRM_EDIT,
    MADE_IN_FILE,
    NOT_APPLICABLE,
    NOT_RECORDED,
    PASS,
    READER,
    RULES,
    SCHEMA_VERSION,
    TESTS,
    attorney,
    measure,
    overall,
    rules,
    verdict,
)

MANIFEST = Path("out") / "demand" / "upload_manifest.json"
SCORED_ROLES = ("draft", "demand")
READER_KEY = "reader"
_SPACING = frozenset({"line_spacing", "line_spacing_pt", "heading_line_spacing", "heading_line_spacing_pt",
                      "heading_space_before_pt", "heading_space_after_pt"})  # fmt: skip


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def places(data: Path, spec_dir: str | Path | None, inputs: list[str | None], extra: list[str | Path]) -> list[Path]:
    """Where a recorded file's bytes may be found, in order."""
    out = [data]
    if spec_dir is not None:
        out.append(copies_dir(spec_dir))
    out += [Path(i) for i in inputs if i] + [Path(e) for e in extra]
    return out


def filed_documents(data: Path) -> list[dict[str, Any]]:
    """The filed documents a library file made: role, sha256, and the bytes when
    the job folder still holds exactly the filed ones."""
    rows = _json(data / MANIFEST)
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("role") not in SCORED_ROLES:
            continue
        blob = None
        for p in (Path(str(row.get("local_path") or "")), data / MANIFEST.parent / str(row.get("name") or "")):
            if p.is_file():
                blob = p.read_bytes()
                break
        ok = blob is not None and hashlib.sha256(blob).hexdigest() == row.get("sha256")
        out.append({"role": row["role"], "sha256": row.get("sha256"), "data": blob if ok else None,
                    "state": "present" if ok else ("changed" if blob is not None else "missing")})  # fmt: skip
    return out


def _spacing_ruled(data: Path) -> bool:
    rows = ((_json(data / "state.json") or {}).get("firm_rules") or {}).get("rows") or []
    return any(r.get("applied") and r.get("key") in _SPACING for r in rows if isinstance(r, dict))


def made_in_file(record: dict[str, Any], filed: bytes, where: list[Path], data: Path) -> dict[str, Any]:
    """Test (a)."""
    base = record.get("base") or {}
    source, sha = base.get("source"), base.get("sha256")
    if source not in (ACCEPTED, PINNED, BY_NAME):
        return verdict(NOT_APPLICABLE, f"made on {source}; no firm file to measure against", {"source": source})
    blob = measure.find_by_sha(str(sha or ""), where)
    if blob is None:
        return verdict(NOT_RECORDED, "the file it was made in is not on the seat", {"source": source, "sha256": sha})
    contract = base.get("contract") or ((record.get("resolution") or {}).get("file") or {}).get("contract") or ""
    got = measure.compare(blob, filed, contract, without_spacing=_spacing_ruled(data))
    got["measured"].update(source=source, sha256=sha, contract=contract)
    if got["differs"]:
        return verdict(
            FAIL, "the filed document does not carry the file's look", {**got["measured"], "differs": got["differs"]}
        )
    return verdict(PASS, "", got["measured"])


def firm_edit(record: dict[str, Any], filed: bytes, where: list[Path], data: Path) -> dict[str, Any]:
    """Test (d)."""
    edit = record.get(EDIT_KEY)
    if not isinstance(edit, dict):
        return verdict(NOT_APPLICABLE, "no firm edit was considered")
    used = edit.get("status") == EDIT_USED
    file = (record.get("resolution") or {}).get("file") or {}
    sha = edit.get("sha256") if used else file.get("sha256")
    blob = measure.find_by_sha(str(sha or ""), where)
    shown = {"edit": edit.get("status"), "code": edit.get("code"), "version": edit.get("version"), "sha256": sha}
    if blob is None:
        return verdict(NOT_RECORDED, "the version the document should measure to is not on the seat", shown)
    got = measure.compare(blob, filed, str(file.get("contract") or ""), without_spacing=_spacing_ruled(data))
    if got["differs"]:
        why = (
            "the firm's edit was used and the filed document does not carry it"
            if used
            else ("the firm's edit was refused and the filed document is not the accepted copy")
        )
        return verdict(FAIL, why, {**shown, "differs": got["differs"]})
    return verdict(PASS, "", {**shown, **got["measured"]})


def reader(record: dict[str, Any]) -> dict[str, Any]:
    """Test (c): recorded, or not yet; never a failure."""
    got = record.get(READER_KEY)
    if isinstance(got, dict) and got.get("reader") and got.get("profile"):
        return verdict(PASS, "", {"reader": got["reader"], "profile": got["profile"]})
    return verdict(NOT_RECORDED, "the record names no reader and profile (design piece 7)")


def _all(value: str, why: str) -> dict[str, Any]:
    return {t: verdict(value, why) for t in TESTS}


def _firm_data(inputs: str | None) -> dict[str, Any] | None:
    if not inputs:
        return None
    from ..drafting import firm as drafting_firm

    try:
        return drafting_firm.load(inputs).data
    except drafting_firm.DraftingConfigError:
        return None


def score_document(
    record: dict[str, Any], doc: dict[str, Any], data: Path, where: list[Path], ctx: dict[str, Any]
) -> dict[str, Any]:
    """The five tests for one filed document."""
    if doc["data"] is None:
        return _all(NOT_RECORDED if doc["state"] == "missing" else FAIL, f"the filed bytes are {doc['state']}")
    filed = doc["data"]
    with tempfile.TemporaryDirectory(prefix="library-score-") as scratch:
        tmp = Path(scratch) / "filed.docx"
        tmp.write_bytes(filed)
        kind = str(record.get("kind") or "")
        paragraphs = measure.paragraph_texts(measure.members(filed))
        firm_data = None if record.get("path") == "demand_job" else ctx["firm_data"]
        return {
            MADE_IN_FILE: made_in_file(record, filed, where, data),
            ATTORNEY: attorney.score(record, data, paragraphs, ctx["roster"], ctx["demand_inputs"]),
            READER: reader(record),
            FIRM_EDIT: firm_edit(record, filed, where, data),
            RULES: rules.score(data, tmp, kind.split(".")[0], firm_data),
        }


def score_job(
    job_dir: str | Path,
    *,
    spec_dir: str | Path | None = None,
    roster: roster_mod.Roster | None = None,
    drafting_inputs: str | None = None,
    demand_inputs: str | None = None,
    extra: list[str | Path] | None = None,
    clock: Any = time.time,
) -> dict[str, Any] | None:
    """The job's score, or None when it filed nothing a library file makes."""
    job = Path(job_dir)
    data = job / "data"
    docs = filed_documents(data)
    if not docs:
        return None
    record = _json(data / RECORD_NAME)
    base = {k: v for k, v in record.items() if k != READER_KEY} if isinstance(record, dict) else record
    problems = record_problems(base) if record is not None else ["there is no record beside the documents"]
    ctx = {
        "roster": roster or roster_mod.load(),
        "demand_inputs": demand_inputs,
        "firm_data": _firm_data(drafting_inputs),
    }
    where = places(data, spec_dir, [drafting_inputs, demand_inputs], list(extra or []))
    per = [
        _all(NOT_RECORDED, f"the record cannot be read ({len(problems)} problem(s))") if problems
        else score_document(record, d, data, where, ctx)
        for d in docs
    ]  # fmt: skip
    tests = {t: {"verdict": overall([p[t] for p in per]), "documents": [p[t] for p in per]} for t in TESTS}
    return {
        "schema_version": SCHEMA_VERSION,
        "job_id": job.name,
        "path": (record or {}).get("path") if isinstance(record, dict) else None,
        "kind": (record or {}).get("kind") if isinstance(record, dict) else None,
        "scored_at": round(float(clock()), 3),
        "documents": [{"role": d["role"], "sha256": d["sha256"], "state": d["state"]} for d in docs],
        "tests": tests,
        "failed": [t for t in TESTS if tests[t]["verdict"] == FAIL],
    }


def write_score(job_dir: str | Path, score: dict[str, Any]) -> Path:
    """The score beside the record, written whole or not at all."""
    from . import SCORE_NAME

    folder = Path(job_dir) / "data"
    dest, tmp = folder / SCORE_NAME, folder / f".{SCORE_NAME}.tmp"
    tmp.write_text(json.dumps(score, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(dest)
    return dest


__all__ = ["MANIFEST", "filed_documents", "places", "score_job", "write_score"]
