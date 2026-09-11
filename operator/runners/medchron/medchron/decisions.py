"""The decisions a person used to make, written as rules that surface and refuse.

Each hook writes the authored artifact the frozen pipeline expects
(`include.json`, `units.json`, `billing_docs.json`, `msg_fold.json`,
`orphans.json`, `record_control[-unit].json`) from the job envelope, the firm
config, and the run's own $0 artifacts. A hook that cannot decide returns a
HOLD naming what it could not place; it never guesses and never widens a rule
to make the number come out.

`--dry-run` runs every hook without writing and reports what it would author
and where it would hold, so a rule's hold rate can be measured on delivered
matters before it ships.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import FirmConfig
from .job import Job, Unit


@dataclass
class Decision:
    hook: str
    artifact: Path | None
    payload: Any
    holds: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def held(self) -> bool:
        return bool(self.holds)


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _write(path: Path, payload: Any, *, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    tmp.replace(path)


def _compile(patterns: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(p, re.I) for p in patterns]


# ---- selection ---------------------------------------------------------------
def selection(job: Job, cfg: FirmConfig, slug_dir: Path, *, dry_run: bool) -> Decision:
    """`include.json`: every top-level folder except the excluded classes.

    Joint matters: each unit's folder_prefix plus the shared classes. The
    sixteen delivered matters had sixteen folder vocabularies, so the rule is
    subtraction with disclosure, not an allowlist of medical folder names. A
    top-level folder that matches an exclude class is named in `excluded`
    (the limitations section prints it); nothing is silently left out.
    """
    folders = _read_json(slug_dir / "folders.json", [])
    tops = sorted({f["path"].split("/")[1] for f in folders if f.get("path", "").count("/") >= 1})
    exclude = _compile(cfg.get("selection", "exclude_folder_classes") or [])
    shared = _compile(cfg.get("selection", "shared_folder_classes") or [])
    holds: list[str] = []
    notes: list[str] = []
    excluded = [t for t in tops if any(rx.search(t) for rx in exclude)]
    if job.joint:
        prefixes = [u.folder_prefix for u in job.units if u.folder_prefix]
        missing = [p for p in prefixes if p.lstrip("/") not in tops]
        if missing:
            holds.append(f"joint matter: unit folder(s) not found at top level: {missing}")
        include = prefixes + [f"/{t}" for t in tops if any(rx.search(t) for rx in shared) and t not in excluded]
        others = [t for t in tops if f"/{t}" not in include and t not in excluded]
        if others:
            notes.append(f"top-level folders outside the unit folders and shared classes, not pulled: {others}")
    else:
        include = [f"/{t}" for t in tops if t not in excluded]
    overrides = job.selection_overrides
    payload = {
        "include_prefixes": overrides.get("include_prefixes") or include,
        "exclude_substrings": overrides.get("exclude_substrings") or [],
        "root_pdfs": bool(cfg.get("selection", "root_pdfs")),
        "_decided": {"excluded_top_level": excluded, "by": "medchron.decisions.selection"},
    }
    # ss#2616 append runs: the requesting skill names exactly the new document
    # ids; the pull is restricted to them (download HOLDs on an id the matter
    # does not carry, never silently skips it).
    if overrides.get("include_file_ids"):
        payload["include_file_ids"] = [str(f) for f in overrides["include_file_ids"]]
        notes.append(f"append run: pull restricted to {len(payload['include_file_ids'])} named document id(s)")
    if not payload["include_prefixes"]:
        holds.append("no folder survived selection; nothing to pull")
    d = Decision("selection", slug_dir / "include.json", payload, holds, notes)
    if not d.held:
        _write(d.artifact, payload, dry_run=dry_run)
    return d


# ---- units -------------------------------------------------------------------
def units(job: Job, cfg: FirmConfig, slug_dir: Path, *, dry_run: bool) -> Decision:
    """`units.json` from the envelope's clients. The pipeline reads
    folder_prefix, name_token, surname, dob per unit; single-client matters
    get one unit keyed by the slug with no prefix."""
    payload: dict[str, Any] = {}
    for u in job.units:
        entry: dict[str, Any] = {"name_token": u.name_token, "surname": u.surname, "dob": u.dob}
        if u.folder_prefix:
            entry["folder_prefix"] = u.folder_prefix
        payload[u.unit] = entry
    d = Decision("units", slug_dir / "units.json", payload)
    _write(d.artifact, payload, dry_run=dry_run)
    return d


# ---- billing documents --------------------------------------------------------
def billing_docs(job: Job, cfg: FirmConfig, slug_dir: Path, *, dry_run: bool) -> Decision:
    """`billing_docs.json`: the pulled files whose names match the firm's
    billing patterns. Recomputed whenever the unit set changes (an authored
    artifact that does not regenerate describes the old matter).

    Each row is `{id, name, path}` straight off the pull's manifest row:
    `billing_extract` opens `path` (the pulled bytes under raw/) and counts the
    pages itself when it renders. The manifest carries no page count, so none
    is invented here; a row without a real path would KeyError the paid stage
    on its first document (the 2026-09-04 finding)."""
    patterns = _compile(cfg.get("billing", "name_patterns") or [])
    rows = _read_jsonl(slug_dir / "raw_manifest.jsonl")
    picked = []
    pathless: list[str] = []
    for r in rows:
        if not r.get("ok") or r.get("duplicate_of"):
            continue
        name = str(r.get("name") or "")
        if not any(rx.search(name) for rx in patterns):
            continue
        if not r.get("path"):
            pathless.append(name)
            continue
        picked.append({"id": r.get("id"), "name": name, "path": str(r["path"])})
    payload = {"docs": picked, "_decided": {"by": "medchron.decisions.billing_docs", "patterns": len(patterns)}}
    d = Decision("billing_docs", slug_dir / "billing_docs.json", payload)
    if pathless:
        d.holds.append(
            f"{len(pathless)} billing document(s) matched by name but the pull recorded no local path: {pathless[:12]}"
        )
        return d
    if not picked:
        d.notes.append("no billing documents matched by name; the worksheet will state that no ledger is on file")
    _write(d.artifact, payload, dry_run=dry_run)
    return d


# ---- email attachment fold ---------------------------------------------------
SKIP_EXT = {".ics", ".vcf", ".htm", ".html", ".txt", ".eml", ".rpmsg", ".zip", ".exe", ".dll"}


def fold(job: Job, cfg: FirmConfig, slug_dir: Path, *, dry_run: bool) -> Decision:
    """`msg_fold.json`: fold every byte-new attachment whose extension is not a
    skip type. No byte floor and no aspect window: both were measured to
    mis-sort in both directions (a photographed bill page dropped, a banner
    admitted); dedup and the vision classifier reject banners downstream, and a
    dropped record is undetectable. `.rpmsg` is disclosed, never folded."""
    # The report shape is stages/msg.py's `msg_attachments.json`: each kept
    # attachment carries `sha256`, `local` (its file name, so its extension),
    # and `already_pulled_as` (the pull's name when the bytes are already in
    # the corpus). Encrypted and skipped kinds never reach `attachments`; they
    # are named in `dropped_unkept` and `encrypted`, and the disclosure reads
    # from there.
    index = _read_json(slug_dir / "msg_attachments.json", {})
    items = index.get("attachments") if isinstance(index, dict) else index
    keep: list[str] = []
    for a in items or []:
        sha = str(a.get("sha256") or "")[:12]
        ext = ("." + str(a.get("local") or "").rsplit(".", 1)[-1]).lower() if "." in str(a.get("local") or "") else ""
        if a.get("already_pulled_as") or not sha:
            continue
        if ext in SKIP_EXT or ext == ".rpmsg":
            continue
        keep.append(sha)
    disclosed = [
        str(e.get("attachment") or "") for e in (index.get("encrypted") or [] if isinstance(index, dict) else [])
    ]
    payload = {
        "fold": keep,
        "_note": "folded by medchron.decisions.fold: every byte-new attachment "
        "not in SKIP_EXT; encrypted containers disclosed, not folded",
        "_disclosed_encrypted": disclosed,
    }
    d = Decision("fold", slug_dir / "msg_fold.json", payload)
    if disclosed:
        d.notes.append(f"{len(disclosed)} encrypted attachment(s) cannot be opened; disclosed")
    _write(d.artifact, payload, dry_run=dry_run)
    return d


# ---- orphans -----------------------------------------------------------------
TINY_IMAGE_BYTES = 8_000
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp"}


def _tiny_image_reason(row: dict[str, Any]) -> str | None:
    """A measured fact, not a guess: an image under 8 KB pulled from a matter
    is an email signature or spacer graphic; it cannot carry a record page."""
    ext = str(row.get("ext") or "").lower()
    size = int(row.get("size_got") or row.get("size") or 0)
    if ext in IMAGE_EXTS and 0 < size < TINY_IMAGE_BYTES:
        return f"email signature or spacer graphic ({size} bytes {ext}); no clinical content possible at that size"
    return None


def orphans(job: Job, cfg: FirmConfig, slug_dir: Path, unit: Unit, *, dry_run: bool) -> Decision:
    """`orphans.json`: a pulled file no unit owns gets a machine-written reason
    when its name matches a coverage exclusion class; any other residue is a
    HOLD listing the files. The reason text is the config's, never invented."""
    exclusions = cfg.compiled("coverage", "exclusions")
    rows = _read_jsonl(slug_dir / "raw_manifest.jsonl")
    owned: set[str] = set()
    for f in (slug_dir / "units").glob("*.json"):
        if f.name.startswith("_"):
            continue
        # A unit file is a list of the records build_units assigned to it.
        body = _read_json(f, [])
        records = body if isinstance(body, list) else body.get("files", [])
        for rec in records:
            if isinstance(rec, dict) and rec.get("id"):
                owned.add(str(rec["id"]))
    unowned = [r for r in rows if r.get("ok") and not r.get("duplicate_of") and str(r.get("id")) not in owned]
    explained: list[dict[str, str]] = []
    residue: list[str] = []
    for r in unowned:
        name = str(r.get("name") or "")
        reason = next((why for rx, why in exclusions if rx.search(name)), None)
        if reason is None:
            reason = _tiny_image_reason(r)
        if reason:
            explained.append({"name": name, "reason": reason})
        else:
            residue.append(name)
    payload = {"orphans": explained, "_decided": {"by": "medchron.decisions.orphans"}}
    d = Decision("orphans", slug_dir / "orphans.json", payload)
    if residue:
        d.holds.append(
            f"{len(residue)} pulled file(s) owned by no unit and matching no exclusion class: {residue[:12]}"
        )
    else:
        _write(d.artifact, payload, dry_run=dry_run)
    return d


# ---- control page ------------------------------------------------------------
def control(job: Job, cfg: FirmConfig, slug_dir: Path, unit: Unit, *, dry_run: bool) -> Decision:
    """`record_control[-unit].json`: a page that is a record by construction,
    the planted control the scanned-page classifier is falsified against.

    `page_map.json` is the exhibit list with each source file's page range;
    `extracted.jsonl` carries native-text chars and pages per file. The control
    is the second page of the exhibited file with the densest native text (at
    least 800 chars a page across two or more pages): dense native text is a
    record, and the second page skips a cover sheet."""
    page_map = _read_json(slug_dir / "out" / unit.unit / "page_map.json", [])
    density: dict[str, tuple[float, int]] = {}
    for row in _read_jsonl(slug_dir / "extracted.jsonl"):
        pages, chars = int(row.get("pages") or 0), int(row.get("chars") or 0)
        if pages >= 2 and chars:
            density[str(row.get("name") or "")] = (chars / pages, pages)
    best: tuple[float, int, int] | None = None  # (chars per page, exhibit, page)
    for ex in page_map if isinstance(page_map, list) else []:
        for f in ex.get("files") or []:
            name = str(f.get("file") or "")
            stem = name.rsplit(".", 1)[0]
            dens = density.get(stem) or density.get(name)
            if not dens or int(f.get("pages") or 0) < 2:
                continue
            if best is None or dens[0] > best[0]:
                best = (dens[0], int(ex.get("exhibit") or 0), int(f.get("start_page") or 1) + 1)
    d = Decision(
        "control", slug_dir / (f"record_control-{unit.unit}.json" if job.joint else "record_control.json"), None
    )
    if best is None or best[0] < 800:
        d.holds.append("no exhibited file with dense native text to serve as the record control")
        return d
    d.payload = {
        "exhibit": best[1],
        "page": best[2],
        "_decided": {"by": "medchron.decisions.control", "chars_per_page": round(best[0])},
    }
    _write(d.artifact, d.payload, dry_run=dry_run)
    return d


HOOKS = {
    "selection": selection,
    "units": units,
    "billing_docs": billing_docs,
    "fold": fold,
}
UNIT_HOOKS = {
    "orphans": orphans,
    "control": control,
}
