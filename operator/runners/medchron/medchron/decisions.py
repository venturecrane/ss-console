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

from . import unopened
from .config import FirmConfig
from .gates import foreign_identity as FI
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

    A unit that names a `folder_prefix` is scoped to it, plus the shared
    classes. The sixteen delivered matters had sixteen folder vocabularies, so
    the rule is subtraction with disclosure, not an allowlist of medical folder
    names. A top-level folder that matches an exclude class is named in
    `excluded` (the limitations section prints it); nothing is silently left
    out.

    The scoping branch keys on a folder_prefix being PRESENT, never on
    ``job.joint`` (which is only ``len(units) > 1``). Found live 2026-09-22 on
    a multi-client matter where the firm asked for a chronology on ONE of its
    clients: a single unit made ``joint`` False, the submitted folder_prefix
    was never consulted, and the run pulled every top-level folder -- roughly
    a third of the documents it read belonged to the co-client, and were
    already extracted into the chronology corpus before it was stopped by
    hand. The number of units in a job says nothing about how many clients'
    folders the matter holds.

    Prefixes are normalised to a single leading slash because
    ``download.wanted`` matches ``folder_path.startswith(p)`` against paths
    like ``/<folder>/MEDICAL``. The submitting skill sends the bare folder
    name; the test fixtures happened to send ``/Alpha_Example``. Without
    normalisation this branch would match nothing and the run would look
    correctly scoped while reading only shared classes and root PDFs.
    """
    folders = _read_json(slug_dir / "folders.json", [])
    tops = sorted({f["path"].split("/")[1] for f in folders if f.get("path", "").count("/") >= 1})
    exclude = _compile(cfg.get("selection", "exclude_folder_classes") or [])
    shared = _compile(cfg.get("selection", "shared_folder_classes") or [])
    holds: list[str] = []
    notes: list[str] = []
    excluded = [t for t in tops if any(rx.search(t) for rx in exclude)]
    prefixes = [f"/{u.folder_prefix.strip('/')}" for u in job.units if u.folder_prefix]
    if prefixes:
        missing = [p for p in prefixes if p.lstrip("/") not in tops]
        if missing:
            holds.append(f"unit folder(s) not found at top level: {missing}")
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
    withheld = FI.withheld(slug_dir)
    keep: list[str] = []
    for a in items or []:
        sha = str(a.get("sha256") or "")[:12]
        ext = ("." + str(a.get("local") or "").rsplit(".", 1)[-1]).lower() if "." in str(a.get("local") or "") else ""
        if a.get("already_pulled_as") or not sha:
            continue
        if ext in SKIP_EXT or ext == ".rpmsg":
            continue
        if FI.matches(sha, withheld):
            continue  # the firm said to leave it out: never folded, never read
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
    foreign = _foreign_attachments(job, slug_dir, items or [], keep)
    if foreign:
        # 2026-10-13: never fold an attachment whose own text names a different
        # person. An attachment has no folder to say whose it is; its text is
        # the only evidence, and a co-plaintiff's records inside a client's
        # chronology is the defect this lane must not repeat (ss#2893).
        #
        # 2026-10-24 (the Captain): it is left out BY RULE and the run goes on.
        # It used to hold here and ask the firm whose it was; a chronology now
        # reaches the firm only when it is delivered, and the delivered
        # package's Records Reviewed and Limitations section names the file
        # and says it named someone other than the client (limitations.py).
        # What it says about the other person is SMD's detail and stays in
        # identity_holds.json on the seat.
        ids = [str(h["id"]) for h in foreign]
        _write(slug_dir / IDENTITY_HOLDS_FILE, foreign, dry_run=dry_run)
        if not dry_run:
            FI.withhold_by_rule(slug_dir, ids, ground=FI.RULE_ANOTHER_PERSON)
        payload["fold"] = [sha for sha in keep if sha not in ids]
        d.notes.append(
            f"{len(foreign)} emailed attachment(s) name a different person than the client; left out by rule "
            f"and disclosed: {'; '.join(_named(h) for h in foreign[:5])}"
        )
    _write(d.artifact, payload, dry_run=dry_run)
    return d


#: SMD's detail of an identity hold (what was found), kept on the seat beside
#: the job; the same file the identity stage writes (stages/identity.HOLDS_FILE).
IDENTITY_HOLDS_FILE = "identity_holds.json"


def _named(hold: dict[str, Any]) -> str:
    """One held document as a hold line names it: its id, its file name, and
    the email it came from, as far as the index knows them. Never the finding."""
    out = str(hold["id"])
    if hold.get("name"):
        out += f' "{hold["name"]}"'
    if hold.get("container"):
        out += f' attached to the email "{hold["container"]}"'
    return out


def _foreign_attachments(
    job: Job, slug_dir: Path, items: list[dict[str, Any]], keep: list[str]
) -> list[dict[str, Any]]:
    """Each attachment to fold whose text names someone else: its sha12, its
    file name and email, and what was found (SMD's detail). Text that names
    no one (an image, a scan) passes here and is checked again after vision
    by the `identity` stage."""
    surnames, dobs = FI.identities(job.units)
    free = FI.released(slug_dir) | FI.withheld(slug_dir)
    by12 = {str(a.get("sha256") or "")[:12]: a for a in items}
    out: list[dict[str, Any]] = []
    for sha in keep:
        a = by12.get(sha) or {}
        text = FI.pdf_text(slug_dir / "msg_pdfs" / str(a.get("local") or ""))
        found = FI.findings(text, surnames, dobs, FI.first_names(job.units))
        if found and not FI.matches(sha, free):
            out.append(
                {
                    "id": sha,
                    "name": str((a.get("names") or [""])[0] or "") or None,
                    "container": str((a.get("containers") or [""])[0] or "") or None,
                    "found": found,
                }
            )
    return out


# ---- orphans -----------------------------------------------------------------
TINY_IMAGE_BYTES = 8_000
WITHHELD_REASON = FI.WITHHELD_REASON
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp"}


def _tiny_image_reason(row: dict[str, Any]) -> str | None:
    """A measured fact, not a guess: an image under 8 KB pulled from a matter
    is an email signature or spacer graphic; it cannot carry a record page."""
    ext = str(row.get("ext") or "").lower()
    size = int(row.get("size_got") or row.get("size") or 0)
    if ext in IMAGE_EXTS and 0 < size < TINY_IMAGE_BYTES:
        return f"email signature or spacer graphic ({size} bytes {ext}); no clinical content possible at that size"
    return None


#: A file the routing left out because its name matches the firm config's
#: ``units.exclude_name_patterns`` and no coverage exclusion class names why.
NAME_RULE_REASON = "excluded by the file-name rule for documents that are not chronology sources"


def _unreadable_reason(row: dict[str, Any]) -> str:
    """What the extract stage measured about a file it could not read: the
    type it does not read, or that the file would not open. A fact about the
    file, never a guess at its content."""
    if str(row.get("error") or "") == "unhandled ext":
        return f"a {row.get('ext') or 'file'} file, a type this review does not read"
    return "the file could not be opened, or no text could be read from it"


def orphans(job: Job, cfg: FirmConfig, slug_dir: Path, unit: Unit, *, dry_run: bool) -> Decision:
    """`orphans.json`: every pulled file no unit owns, with the factual reason
    it is not a source, for the coverage gate and the Records Reviewed and
    Limitations section. The reason is the config's exclusion class, the
    firm's own leave-it-out (``WITHHELD_REASON``), the identity rule's
    leave-it-out (``FI.RULE_REASONS``), a measured fact about the
    file (password, size, a type or file that would not read), or the
    firm config's name rule; never invented.

    Since 2026-10-23 this decision does not hold for a readable file: a file
    with text that no unit owns is decided at build_units, after vision, where
    it joins the client's unit or is asked of the firm (stages/units
    ``_resolve_orphans``). Curtis held here, after every paid stage, on the
    residue class. What still holds is an integrity stop: a file the pull
    received that never reached extraction, or that has text and still no
    owner, which means an earlier stage did not do its job."""
    exclusions = cfg.compiled("coverage", "exclusions")
    name_rule = [re.compile(str(p)) for p in (cfg.get("units", "exclude_name_patterns") or [])]
    extracted = {str(r.get("id")): r for r in _read_jsonl(slug_dir / "extracted.jsonl") if r.get("id")}
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
    withheld = FI.withheld(slug_dir)
    locked = unopened.password_protected(slug_dir)
    for r in unowned:
        name = str(r.get("name") or "")
        # The entry is keyed the way its two readers key a pulled row: the
        # coverage gate and the coverage record both look it up by name plus
        # extension (stages/coverage.file_key). Keyed by the bare name, an
        # orphan whose extension the pull keeps apart was never matched, so the
        # gate called it dropped and the record called it unread (2026-10-22).
        key = name + str(r.get("ext") or "")
        if FI.matches(str(r.get("id") or ""), withheld):
            # The firm's direction, or the rule's own ground (a record that
            # names someone other than the client, 2026-10-24).
            explained.append({"name": key, "reason": FI.withheld_reason(slug_dir, str(r.get("id") or ""))})
            continue
        if str(r.get("id") or "") in locked:
            explained.append({"name": key, "reason": unopened.REASON})
            continue
        reason = next((why for rx, why in exclusions if rx.search(name)), None)
        if reason is None:
            reason = _tiny_image_reason(r)
        ex = extracted.get(str(r.get("id") or ""))
        if reason is None and any(rx.search(name) for rx in name_rule):
            reason = NAME_RULE_REASON
        if reason is None and ex is not None and ex.get("error") and not ex.get("text_path") and not ex.get("scan"):
            reason = _unreadable_reason({**r, **ex})
        if reason:
            explained.append({"name": key, "reason": reason})
        else:
            residue.append(name)
    payload = {"orphans": explained, "_decided": {"by": "medchron.decisions.orphans"}}
    d = Decision("orphans", slug_dir / "orphans.json", payload)
    if residue:
        d.holds.append(
            f"orphans (integrity stop): {len(residue)} pulled file(s) were never extracted, or have text and no "
            f"owner after build_units: {residue[:12]}"
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
    density: dict[str, float] = {}
    for row in _read_jsonl(slug_dir / "extracted.jsonl"):
        pages, chars = int(row.get("pages") or 0), int(row.get("chars") or 0)
        if pages >= 1 and chars:
            density[str(row.get("name") or "")] = chars / pages
    # (dense, chars per page, exhibit, page): the dense rule first, then the
    # best text page anywhere in the exhibits.
    best: tuple[bool, float, int, int] | None = None
    for ex in page_map if isinstance(page_map, list) else []:
        for f in ex.get("files") or []:
            name = str(f.get("file") or "")
            dens = density.get(name.rsplit(".", 1)[0]) or density.get(name)
            pages = int(f.get("pages") or 0)
            if not dens or pages < 1:
                continue
            start = int(f.get("start_page") or 1)
            cand = (pages >= 2 and dens >= 800, dens, int(ex.get("exhibit") or 0), start + 1 if pages >= 2 else start)
            if best is None or cand[:2] > best[:2]:
                best = cand
    d = Decision(
        "control", slug_dir / (f"record_control-{unit.unit}.json" if job.joint else "record_control.json"), None
    )
    if best is None:
        # 2026-10-23: the one control hold left, and an integrity stop. With
        # no native text anywhere in the exhibits there is no page that is a
        # record by construction, so the scanned-page classifier would have
        # nothing to be falsified against.
        d.holds.append(
            "record control (integrity stop): no exhibited file carries native text, so the scanned-page "
            "classifier has no record page to be checked against"
        )
        return d
    if not best[0]:
        # Before 2026-10-23 a matter with no file of 800+ chars a page over
        # two pages held here for SMD; the best text page is still a record
        # page the classifier must call a record, so it serves.
        d.notes.append(
            f"no exhibited file has dense native text; the record control is the best text page "
            f"({round(best[1])} chars a page)"
        )
    d.payload = {
        "exhibit": best[2],
        "page": best[3],
        "_decided": {"by": "medchron.decisions.control", "chars_per_page": round(best[1]), "dense": best[0]},
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
