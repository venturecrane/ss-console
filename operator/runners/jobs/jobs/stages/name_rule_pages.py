"""`name_rule_pages`: a file the firm's billing NAME rule left out is read
page by page before the name decides. $0, no model.

WHY (2026-11-16, 2026-11-17). An update named "Dr. Example Bill and
Report.pdf": a billing statement, then a signed follow-up examination report
on pages 2 to 4. The billing name rule dropped it at the pull, and #4089's
rescue reads the NAME only ("Report" saved that one), so a file named like a
bill whose later pages are a clinical report is still dropped by a word.

WHAT IT DOES. On an update, it first pulls each named file a billing rule
excluded (``named_excluded.json``), within this client's billing scope
(``billing_update.scope``: another client's file is never pulled, and on a
matter whose other clients are unknown none is), into ``billing/raw/``
(``billing/raw_manifest.jsonl``), outside the read set. The pull is here, not
in ``download``, so nothing is pulled until this stage is wired: it is an
EXTERNAL stage (it touches the seat), and ``jobs rehearse`` never runs it.
Then it reads each pulled file's text layer page by page:

* ``billing``: a claim form, statement or ledger with no clinical narrative
  (``billing_page.is_billing``);
* ``clinical``: a clinical-note heading (``billing_page._NARRATIVE``);
* ``cannot_tell``: fewer than ``select.MIN_CHARS`` characters (a scan with no
  text layer, a blank page);
* ``other``: text that is neither (a cover letter): it promotes nothing alone.

ON AN UPDATE, any clinical or cannot-tell page PROMOTES the file: it moves
into ``raw/`` and ``raw_manifest.jsonl`` as an ordinary pulled document and
leaves ``named_excluded.json``, so extract, vision and ``update_select``
read it, and its billing pages are then set aside page by page by the
existing rule (``select._decide``, "billing page"). A file every page of
which is billing stays out, as its name said.

ON A FRESH RUN this stage only records (``FRESH_POLICY``): the run lane
pulls such a file anyway and its name rule is applied later, by the
coverage record; no real fresh job has been rehearsed on this, so it changes
nothing a fresh run reads. Either way ``billing/name_rule_pages.json`` holds
the per-page classes. The stage never stops a run.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from ..medchron.update.billing_page import _NARRATIVE, is_billing
from ..medchron.update.select import MIN_CHARS
from .base import StageRun, append_jsonl, read_json, read_jsonl

BILLING_RAW = "billing/raw"
BILLING_MANIFEST = "billing/raw_manifest.jsonl"
RECORD = "billing/name_rule_pages.json"
BILLING, CLINICAL, CANNOT_TELL, OTHER = "billing", "clinical", "cannot_tell", "other"
PROMOTES = frozenset({CLINICAL, CANNOT_TELL})
#: What a fresh run does with a promotion: record it, never act on it.
RECORD_ONLY, ACT = "record_only", "act"
FRESH_POLICY = RECORD_ONLY


def page_class(text: str) -> str:
    t = (text or "").strip()
    if len(t) < MIN_CHARS:
        return CANNOT_TELL
    if _NARRATIVE.search(t):
        return CLINICAL
    return BILLING if is_billing(t) else OTHER


def classes(path: Path) -> list[str]:
    """Each page's class by its text layer; an unopenable file is one cannot-tell page."""
    from ..matter import pdf as matter_pdf

    try:
        return [page_class(t) for t in matter_pdf.page_texts(path)]
    except Exception:  # noqa: BLE001 - a file this cannot open is a file nobody can call billing by its pages
        return [CANNOT_TELL]


def promotes(cls: list[str]) -> bool:
    return any(c in PROMOTES for c in cls)


def _promote(sr: StageRun, row: dict[str, Any]) -> bool:
    """Move one billing-named file into the read set; False when its bytes are gone."""
    src = Path(str(row.get("path") or ""))
    if not src.is_file():
        return False
    dest = sr.slug_dir / "raw" / src.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    append_jsonl(sr.slug_dir / "raw_manifest.jsonl", {**row, "path": str(dest), "promoted_from": "billing_name_rule"})
    named = sr.slug_dir / "named_excluded.json"
    if named.is_file():
        keep = [x for x in read_json(named, []) or [] if str(x.get("id")) != str(row["id"])]
        named.write_text(json.dumps(keep, indent=1), encoding="utf-8")
    return True


def _fresh_rows(sr: StageRun) -> list[dict[str, Any]]:
    """A fresh run's pulled files a billing name rule names (the coverage record applies it later)."""
    from ..filing import full_name
    from .download import _name_rules
    from .name_rules import _BILLING_RULE, excluded_by_name

    rules = _name_rules(sr)
    out = []
    for r in read_jsonl(sr.slug_dir / "raw_manifest.jsonl"):
        why = excluded_by_name(full_name(r), rules) if r.get("ok") else None
        if why and _BILLING_RULE.search(why) and r.get("path"):
            out.append(r)
    return out


def pull_aside(sr: StageRun) -> int:
    """Each named file a BILLING name rule left out, pulled into ``billing/raw/``
    outside the read set; returns how many were pulled. Best effort: a file
    that does not land is recorded by its row and stays excluded, as its
    name said."""
    from ..matter import pull as matter_pull
    from .download import BATCH_PAUSE_SECONDS, _already, _pull_one
    from .name_rules import _BILLING_RULE

    named = read_json(sr.slug_dir / "named_excluded.json", []) or []
    ids = {str(x["id"]) for x in named if isinstance(x, dict) and _BILLING_RULE.search(str(x.get("why") or ""))}
    if not ids:
        return 0
    log_path = sr.slug_dir / BILLING_MANIFEST
    done, seen_sha = _already(read_jsonl(log_path))
    if not ids - done:
        return 0
    todo = _this_clients(sr, [d for d in sr.manifest() if str(d.get("id")) in ids - done])
    if not todo:
        return 0
    raw = sr.slug_dir / BILLING_RAW
    raw.mkdir(parents=True, exist_ok=True)
    fpath = sr.folder_paths()

    def one(b: dict[str, Any], m: dict[str, Any]) -> None:
        append_jsonl(log_path, _pull_one(sr, b, m, fpath, raw, seen_sha))

    matter_pull.pull(
        sr.seat, sr.job.matter_id, todo, one, log=sr.log, how=matter_pull.STAGES, pause=BATCH_PAUSE_SECONDS
    )
    sr.log(f"name_rule_pages: {len(todo)} billing-named file(s) pulled aside for a page-by-page read")
    return len(todo)


def _this_clients(sr: StageRun, docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The `docs` this client's billing scope reads (``billing_update.scope``,
    the billing refresh's own rule): another client's file is never pulled,
    and on a matter whose other clients are unknown none is."""
    from ..medchron import billing_plan as BP
    from .billing_update import scope

    if not docs:
        return []
    try:
        scope(sr, sr.manifest(), sr.cfg.get("billing", "name_patterns") or [])  # the refresh's own unknown-clients test
        read, _aside = scope(sr, docs, [r".*"])
    except BP.Unknown:
        sr.log("name_rule_pages: nothing pulled aside: other clients on this matter are unknown")
        return []
    own = {r["id"] for r in read}
    return [d for d in docs if str(d.get("id")) in own]


def run(sr: StageRun) -> int:
    update = sr.job.update is not None
    if update:
        pull_aside(sr)
    aside = sr.slug_dir / BILLING_MANIFEST
    rows = [r for r in read_jsonl(aside) if r.get("ok") and r.get("path")] if update else _fresh_rows(sr)
    act = update or FRESH_POLICY == ACT
    record: dict[str, Any] = {"policy": "act" if act else RECORD_ONLY, "files": {}}
    promoted = 0
    for r in rows:
        cls = classes(Path(str(r["path"])))
        want = promotes(cls)
        done = want and act and _promote(sr, r)
        promoted += bool(done)
        record["files"][str(r["id"])] = {"pages": cls, "promotes": want, "promoted": bool(done)}
    (sr.slug_dir / RECORD).parent.mkdir(parents=True, exist_ok=True)
    (sr.slug_dir / RECORD).write_text(json.dumps(record, indent=1), encoding="utf-8")
    sr.log(
        f"name_rule_pages: {len(rows)} file(s) a billing name rule names; "
        f"{sum(v['promotes'] for v in record['files'].values())} hold a clinical or unreadable page; "
        f"{promoted} promoted to be read" + ("" if act else " (record only on a fresh run)")
    )
    return 0
