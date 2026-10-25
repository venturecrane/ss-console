"""`download`: pull the extraction set for a matter into `raw/`, verified, deduped
by content, logged to `raw_manifest.jsonl`.

Selection is the authored `include.json` (`decide_selection` writes it from the
firm's rules; nothing here knows a folder name). Presigned URLs are minted in
small batches immediately before each pull because they expire, and the seat
paces itself between mints. A file whose sha256 was already pulled is recorded
with `duplicate_of` and its bytes deleted, so downstream stages read one copy
and pay for one (a delivered matter once carried 10/265 byte-identical files,
all paid for twice).

EXIT_INFRA (75) when a target is still not pulled for a reason a retry could
fix: the frozen script printed the failures and exited 0, which is exactly the
kind of outcome an agent reading stdout would catch and a driver would not.
Every such reason is the vendor or the network (auth, throttling, timeouts,
5xx, a size the vendor reports that the bytes do not match yet, a mint that
failed twice), never the records. Until 2026-10-24 it was exit 1, which the
ledger records as terminal `failed` (09-11, 09-14); now the daemon retries it
after a delay (requeue.py), and the resumed pass pulls only what is missing
(raw_manifest.jsonl rows already `ok` are skipped).

The one exception is a 404 on a freshly minted presigned URL, which is the
vendor's storage saying that object is not there. Minting again mints another
URL to the same absent object, so no retry, no rebuild and no reprovision ever
turns it into bytes -- and halting on it means a firm can never get a chronology
for that matter until their vendor repairs their own storage. Every downstream
stage already skips a row that is not `ok` (coverage, extract, vision,
exhibits), and the delivered document already names each unpulled file, its
folder and its reason under Records Reviewed and Limitations. So an absent
object is a disposition the firm is told about, not a halt. Everything else --
auth, throttling, timeouts, 5xx, a size mismatch, a mint that failed twice --
keeps the hard exit.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from ..seat import SeatError
from .base import StageRun, append_jsonl, read_json, read_jsonl

BATCH = 8
# The vendor's storage does not have this object. Structured off the response
# rather than parsed out of the message, because a presigned URL can itself
# contain the digits.
HTTP_ABSENT = 404
MINT_RETRY_PAUSE_SECONDS = 5.0
BATCH_PAUSE_SECONDS = 1.0
#: The stage's exits (dag.py reads EXITS); 75 is compose.INFRA_EXIT.
#: EXIT_ALL_EXCLUDED (2026-10-25): every named document is excluded by the
#: firm's rules. It used to share EXIT_NAMED and was paged as "not on the
#: matter". On an update it is an answer (``dag_update`` maps it to CURRENT:
#: delivered already current, nothing filed); on an append run it holds with
#: its own true reason.
EXIT_NOTHING, EXIT_NAMED, EXIT_ALL_EXCLUDED, EXIT_INFRA = 1, 2, 3, 75
EXITS = {
    EXIT_NOTHING: ("failed", "download: every target is absent from the vendor's storage; nothing to chronicle"),
    EXIT_NAMED: ("held", "append: a named document id is not on the matter"),
    EXIT_ALL_EXCLUDED: (
        "held",
        "append: every named document is excluded by the firm's rules; nothing is left to read",
    ),
    EXIT_INFRA: (
        "failed",
        "download incomplete (infrastructure): vendor or network errors; a later rerun pulls the rest",
    ),
}


def wanted(
    doc: dict[str, Any],
    *,
    folder_path: str,
    prefixes: list[str],
    excludes: list[str],
    root_pdfs: bool,
    doc_exts: set[str],
) -> bool:
    if doc.get("deleted"):
        return False
    ext = (doc.get("ext") or "").lower()
    if ext not in doc_exts:
        return False
    if any(x in folder_path.upper() for x in excludes):
        return False
    if folder_path == "/(root)":
        return root_pdfs and ext == ".pdf"
    return any(folder_path.startswith(p) for p in prefixes)


def _already(rows: list[dict[str, Any]]) -> tuple[set[str], dict[str, str]]:
    done: set[str] = set()
    seen_sha: dict[str, str] = {}
    for r in rows:
        if r.get("ok"):
            done.add(r["id"])
            if r.get("sha256") and not r.get("duplicate_of"):
                seen_sha.setdefault(r["sha256"], r["id"])
    return done, seen_sha


def run(sr: StageRun) -> int:
    sel = read_json(sr.slug_dir / "include.json", None)
    if sel is None:
        raise SeatError("include.json is missing: decide_selection did not run")
    prefixes = list(sel["include_prefixes"])
    excludes = [x.upper() for x in sel.get("exclude_substrings", [])]
    root_pdfs = bool(sel.get("root_pdfs", True))
    doc_exts = {e.lower() for e in sr.cfg.get("selection", "doc_extensions")}
    fpath = sr.folder_paths()
    raw = sr.slug_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    log_path = sr.slug_dir / "raw_manifest.jsonl"

    targets = [
        d
        for d in sr.manifest()
        if wanted(
            d,
            folder_path=fpath.get(d.get("folderId"), "/(root)"),
            prefixes=prefixes,
            excludes=excludes,
            root_pdfs=root_pdfs,
            doc_exts=doc_exts,
        )
    ]
    if sel.get("include_file_ids"):
        rule = {"prefixes": prefixes, "excludes": excludes, "root_pdfs": root_pdfs, "doc_exts": doc_exts}
        named = _named_targets(sr, sel, fpath, rule)
        if isinstance(named, int):
            return named
        targets = named
    done, seen_sha = _already(read_jsonl(log_path))
    todo = [t for t in targets if t["id"] not in done]
    sr.log(f"{sr.slug}: {len(targets)} targets, {len(done)} already done, {len(todo)} to pull")

    pulled = dupes = 0
    failed: list[dict[str, Any]] = []
    batches = (len(todo) + BATCH - 1) // BATCH
    for i in range(0, len(todo), BATCH):
        batch = todo[i : i + BATCH]
        ids = [b["id"] for b in batch]
        minted = _mint_with_retry(sr, ids)
        byid = {m["id"]: m for m in minted}
        for b in batch:
            m = byid.get(b["id"]) or {}
            rec: dict[str, Any] = {
                "id": b["id"],
                "name": b["name"],
                "ext": b["ext"],
                "folder": fpath.get(b.get("folderId"), "/(root)"),
                "size_expected": b.get("size"),
            }
            url = m.get("url")
            if not url:
                rec.update(ok=False, error=m.get("error", "no url"))
                failed.append(rec)
                append_jsonl(log_path, rec)
                continue
            dest = raw / (b["id"] + (b.get("ext") or ""))
            try:
                got = sr.seat.fetch(url, dest, b.get("size"))
                sha = hashlib.sha256(dest.read_bytes()).hexdigest()
                rec.update(ok=True, path=str(dest), size_got=got, sha256=sha)
                dup = seen_sha.get(sha)
                if dup:
                    dest.unlink()
                    rec.update(duplicate_of=dup, path=None)
                    dupes += 1
                else:
                    seen_sha[sha] = b["id"]
                    pulled += 1
            except Exception as exc:  # noqa: BLE001 - one file's failure is one row
                Path(dest).unlink(missing_ok=True)
                rec.update(ok=False, error=str(exc)[:200])
                rec["http_status"] = getattr(getattr(exc, "response", None), "status_code", None)
                failed.append(rec)
            append_jsonl(log_path, rec)
        sr.log(f"batch {i // BATCH + 1}/{batches} done ({pulled} pulled, {dupes} byte-duplicates skipped)")
        if i + BATCH < len(todo):
            time.sleep(BATCH_PAUSE_SECONDS)
    sr.log(f"DONE {pulled} pulled, {dupes} byte-duplicates skipped, {len(failed)} failed")
    return _settle(sr, failed, n_targets=len(targets), landed=len(done) + pulled)


CONTAINER_EXTS = {".msg", ".rpmsg"}


def _name_rules(sr: StageRun) -> list[tuple[Any, str]]:
    rules = [(rx, why) for rx, why in sr.cfg.compiled("coverage", "exclusions")]
    for pat in sr.cfg.get("units", "exclude_name_patterns") or []:
        rules.append((re.compile(str(pat), re.I), "excluded by the firm's file-name rule"))
    return rules


# A name rule that excludes billing or lien paper, and the words that say a
# file is a medical record all the same. "Medical and Billing Records" is a
# visit record with its bill attached: dropping it by `\bbill` dropped the
# visits (2026-10-13). "Billing Records" alone stays billing.
_BILLING_RULE = re.compile(r"(?i)bill|lien|ledger|invoice|statement")
_BILLING_PHRASE = re.compile(r"(?i)\b(?:billing|bills?|itemi[sz]ed|lien)\s+(?:records?|statements?|notes?)\b")
_MEDICAL_NAME = re.compile(r"(?i)\b(?:medical|chart|clinical|notes?|visits?|treatment|records?)\b")


def medical_overrides(rx: Any, why: str, name: str) -> bool:
    """True when a billing or lien name rule matched a name that also says
    it holds medical records: the file is read, not excluded."""
    if not (_BILLING_RULE.search(why) or _BILLING_RULE.search(str(getattr(rx, "pattern", "")))):
        return False
    return bool(_MEDICAL_NAME.search(_BILLING_PHRASE.sub(" ", name)))


def _uncited_firm_exhibits(sr: StageRun) -> set[str]:
    """Ids of the firm's numbered files in the chronology folder ("Exhibit 6-
    ...") that the delivered Exhibit List does not name: the firm filed a
    record there the delivery never read, so it is a new source. It keeps the
    firm's number and is never filed again (``update_select``)."""
    from ..update.body import delivered_numbers

    out: set[str] = set()
    for p in sr.slug_dir.glob("runs/*/update/update_plan.json"):
        base = p.parent / "base.md"
        if not base.is_file():
            continue
        listed = delivered_numbers(base.read_text(encoding="utf-8"))
        out |= {str(e["id"]) for e in (read_json(p, {}) or {}).get("exhibits") or [] if e["exhibit"] not in listed}
    return out


def _update_folders(sr: StageRun) -> set[str]:
    """The delivered chronology's folder on an update: its files are the
    delivered package, never a new record to read."""
    out = set()
    for p in sr.slug_dir.glob("runs/*/update/update_plan.json"):
        fid = (read_json(p, {}) or {}).get("folder_id")
        if fid:
            out.add(str(fid))
    return out


def _why_not(
    d: dict[str, Any],
    folder: str,
    rule: dict[str, Any],
    names: list,
    chron: set[str],
    firm: frozenset[str] = frozenset(),
) -> str | None:
    """Why a NAMED document is not pulled, or None. The firm's authored rules
    apply to a named id exactly as to a listed one (2026-10-13: an agent that
    names everything must not make the runner read photos and background
    reports); an Outlook container is the email stage's to open. `firm`: the
    uncited firm-numbered files in the chronology folder, read whatever
    folder rule covers that folder (``_uncited_firm_exhibits``)."""
    ext = (d.get("ext") or "").lower()
    is_firm = str(d.get("id")) in firm
    if str(d.get("folderId") or "") in chron and not is_firm:
        return "in the delivered chronology's own folder"
    if ext in CONTAINER_EXTS:  # the email stage opens it; only the folder rules apply
        ok = wanted({**d, "ext": ".pdf"}, folder_path=folder, **{**rule, "doc_exts": {".pdf"}})
        return None if ok else "outside the firm's folder rules (an excluded folder)"
    if d.get("deleted"):
        return "deleted"
    if not (ext in rule["doc_exts"] if is_firm else wanted(d, folder_path=folder, **rule)):
        return "outside the firm's folder rules (an excluded folder or kind)"
    full = f"{d.get('name') or ''}{d.get('ext') or ''}"
    return next((why for rx, why in names if rx.search(full) and not medical_overrides(rx, why, full)), None)


def _named_targets(sr: StageRun, sel: dict[str, Any], fpath: dict[str, str], rule: dict[str, Any]) -> list | int:
    """ss#2616: only the named documents, from the full matter listing. A
    named id the matter does not carry is a HOLD naming it (EXIT_NAMED: a
    silent skip would let an update claim coverage it never pulled); a named
    id the firm's rules exclude is left out and recorded in
    `named_excluded.json`, which the update's coverage record and the
    limitations read. Every named id excluded is EXIT_ALL_EXCLUDED."""
    ids = {str(f) for f in sel["include_file_ids"]}
    by_id = {d["id"]: d for d in sr.manifest()}
    missing = sorted(ids - set(by_id))
    if missing:
        sr.log(f"append: {len(missing)} named document id(s) not on the matter: {', '.join(missing[:5])}")
        return EXIT_NAMED
    names, chron, firm = _name_rules(sr), _update_folders(sr), frozenset(_uncited_firm_exhibits(sr))
    targets, excluded = [], []
    for i in sorted(ids):
        d = by_id[i]
        why = _why_not(d, fpath.get(str(d.get("folderId") or ""), "/(root)"), rule, names, chron, firm)
        if why:
            excluded.append({"id": i, "name": f"{d.get('name') or ''}{d.get('ext') or ''}", "why": why})
        elif (d.get("ext") or "").lower() not in CONTAINER_EXTS:
            targets.append(d)
    (sr.slug_dir / "named_excluded.json").write_text(json.dumps(excluded, indent=1), encoding="utf-8")
    for x in excluded:
        sr.log(f"  named but not read: {x['name']} ({x['why']})")
    containers = len(ids) - len(targets) - len(excluded)
    if not targets and not containers:
        sr.log(f"append: every one of the {len(ids)} named document(s) is excluded by the firm's rules")
        return EXIT_ALL_EXCLUDED
    sr.log(f"append: pull restricted to {len(targets)} named document(s); {len(excluded)} left out by the firm's rules")
    return targets


def _mint_with_retry(sr: StageRun, ids: list[str]) -> list[dict[str, Any]]:
    try:
        return sr.seat.mint(sr.job.matter_id, ids)
    except Exception as exc:  # noqa: BLE001 - a mint failure of any kind is retried once; the second failure is recorded per file id below
        sr.log(f"MINT FAIL ({str(exc)[:120]}); retrying once")
        time.sleep(MINT_RETRY_PAUSE_SECONDS)
        try:
            return sr.seat.mint(sr.job.matter_id, ids)
        except Exception as exc2:  # noqa: BLE001 - the second mint failure is recorded per file id so the download stage reports exactly which files failed
            return [{"id": i, "error": f"mint failed twice: {str(exc2)[:120]}"} for i in ids]


def _settle(sr: StageRun, failed: list[dict[str, Any]], *, n_targets: int, landed: int) -> int:
    """The exit code, decided by what kind of failure is left rather than by how
    many there are. EXIT_INFRA gets the pass repeated by the daemon, so a
    failure a repeat cannot fix must not return it."""
    if not failed:
        return 0
    absent = [r for r in failed if r.get("http_status") == HTTP_ABSENT]
    for r in absent:
        sr.log(f'NOT IN THE VENDOR\'S STORAGE (404): "{r.get("name")}" ({r.get("folder")})')
    retryable = [r for r in failed if r.get("http_status") != HTTP_ABSENT]
    if retryable:
        sr.log(
            f"{len(retryable)} of {n_targets} targets are not pulled for a reason a retry can fix; "
            f"the rows carry the reason"
        )
        return EXIT_INFRA
    if not landed:
        # Disclosure is what makes an absent document safe to continue past, and
        # disclosure needs a document to sit in. With nothing pulled there is no
        # chronology to write, only an empty one to file on the firm's matter.
        sr.log(f"all {n_targets} targets are absent from the vendor's storage; there is nothing to chronicle")
        return EXIT_NOTHING
    sr.log(
        f"{len(absent)} of {n_targets} targets are absent from the vendor's storage and no retry can "
        f"produce them; each is named in the delivered document under Records Reviewed and "
        f"Limitations. Continuing with {landed} documents."
    )
    return 0
