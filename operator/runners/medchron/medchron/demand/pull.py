"""`pull`: the matter's documents onto the job's disk, email bodies included, with
the privilege wall applied by sender and recipient before anything is read. $0.

Three things the chronology pull does not do, each from the 2026-09-24 incident:

* **Email bodies.** Carrier letters often sit only in the body of an email
  (``.msg``); the stock pull took attachments and skipped bodies. Every kept
  email becomes a document of its own (``Email: <subject>``), and its
  attachments are documents too, deduplicated by content against the pull.
* **The privilege wall is structural.** Mail between the client and the firm,
  and mail between the firm's own people, is attorney-client communication or
  firm work product. It is identified by ADDRESSES (the firm's authored
  domains, the client's address on the matter), never by a model reading it,
  and none of it reaches a prompt. It is kept on disk as HELD-OUT text so the
  drafting gate can prove the draft does not quote it. The digest prompt's own
  privilege rule stays as a second layer.
* **Everything the firm filed is a candidate.** Correspondence folders are
  pulled (that is where the carrier writes); only the authored folder and
  name exclusions (vendor chronologies, the Operator's own prior output) and
  the wall subtract.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

from ..stages.base import append_jsonl, read_jsonl
from .firm import DemandFirm

BATCH = 8
HTTP_ABSENT = 404
_ADDR = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ATTACH_KEEP = {".pdf", ".docx", ".doc", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}
ATTACH_MIN_BYTES = 20_000  # signature images and logos


class PullError(RuntimeError):
    pass


# ---- selection ----------------------------------------------------------------
def folder_paths(folders: list[dict[str, Any]]) -> dict[str, str]:
    return {str(f.get("id")): str(f.get("path") or "") for f in folders}


def select(docs: list[dict[str, Any]], paths: dict[str, str], firm: DemandFirm) -> list[dict[str, Any]]:
    exts = {e.lower() for e in firm.get("selection", "doc_extensions")}
    folder_pats = [re.compile(p, re.I) for p in firm.get("selection", "exclude_folder_patterns")]
    name_pats = [re.compile(p, re.I) for p in (firm.data["selection"].get("exclude_name_patterns") or [])]
    out = []
    for d in docs:
        if d.get("deleted") or (d.get("ext") or "").lower() not in exts:
            continue
        folder = paths.get(str(d.get("folderId")), "/(root)")
        if any(p.search(seg) for p in folder_pats for seg in folder.split("/") if seg):
            continue
        if any(p.search(str(d.get("name") or "")) for p in name_pats):
            continue
        out.append({**d, "folder": folder})
    return out


# ---- the wall ------------------------------------------------------------------
def addresses(raw: Any) -> list[str]:
    return [a.lower() for a in _ADDR.findall(str(raw or ""))]


def wall_reason(
    sender: str,
    recipients: list[str],
    firm_domains: tuple[str, ...],
    client: set[str],
    consumer_domains: tuple[str, ...] = (),
) -> str | None:
    """Why an email is held out, or None. Decided by addresses alone.

    Measured on a live 108-email matter (2026-10-06, read-only): the first,
    fail-closed version held out 97, including mail with a claims
    administrator, a medical provider and a transport vendor on it, because a
    business party copied on a client's or an Exchange-internal message was
    walled with it. A third party on the thread is not attorney-client
    communication, and carrier and provider mail is the record a demand is
    built from. So:

    * ANY business party (not the firm, not a personal mailbox) on the message:
      KEPT;
    * otherwise, the firm and a personal mailbox (the client's known address,
      or any consumer mailbox) on the same message: walled;
    * otherwise firm-only mail, counting a sender with no parseable address as
      the firm's own (Exchange writes internal senders as a name or an X.500
      path, never an SMTP address): walled as firm internal;
    * nothing parseable at all: walled (it cannot be placed)."""

    def is_firm(a: str) -> bool:
        return a.rsplit("@", 1)[-1] in firm_domains

    def is_personal(a: str) -> bool:
        return a in client or a.rsplit("@", 1)[-1] in consumer_domains

    sender = sender.lower()
    parties = [x.lower() for x in [sender, *recipients] if x]
    if any(not is_firm(x) and not is_personal(x) for x in parties):
        return None
    if not parties:
        return "sender and recipients unreadable"
    firm_side = not sender or any(is_firm(x) for x in parties)
    if firm_side and any(is_personal(x) for x in parties):
        return "between the firm and the client or a personal mailbox"
    if firm_side:
        return "firm internal"
    return None


_HEADER = re.compile(r"(?im)^\s*(from|to|cc|sent|subject)\s*:")


def printed_email_wall(text: str, firm: DemandFirm, client: set[str]) -> str | None:
    """A printed email gets the same wall as a ``.msg``. It is recognized by
    the Outlook header shape (From, Sent, To and Subject lines, all four, in
    its first 3,000 characters) AND an email address on its From or To line.
    A fax cover sheet (TO:/FROM:/RE:) has neither, and a provider's or
    carrier's cover is a record, not correspondence (review of #3074)."""
    head = text[:3000]
    if not {"from", "sent", "to", "subject"} <= {m.group(1).lower() for m in _HEADER.finditer(head)}:
        return None
    if "@" not in _line(head, "from") + _line(head, "to"):
        return None
    sender = next(iter(addresses(_line(head, "from"))), "")
    rcpt = addresses(_line(head, "to")) + addresses(_line(head, "cc"))
    return wall_reason(sender, rcpt, firm.firm_domains, client, firm.consumer_domains)


def _line(text: str, field: str) -> str:
    m = re.search(rf"(?im)^\s*{field}\s*:(.*)$", text)
    return m.group(1) if m else ""


# ---- the pull ------------------------------------------------------------------
def _mint(seat: Any, matter_id: str, ids: list[str], log: Callable[[str], None]) -> dict[str, dict[str, Any]]:
    for attempt in (1, 2):
        try:
            return {m["id"]: m for m in seat.mint(matter_id, ids)}
        except Exception as exc:  # noqa: BLE001 - a mint failure is retried once, then recorded per file
            log(f"mint failed ({str(exc)[:120]}); attempt {attempt} of 2")
            time.sleep(2.0 * attempt)
    return {i: {"id": i, "error": "mint failed twice"} for i in ids}


def _fetch_one(seat: Any, d: dict[str, Any], minted: dict[str, Any], raw: Path, seen: dict[str, str]) -> dict[str, Any]:
    rec = {k: d.get(k) for k in ("id", "name", "ext", "folder")}
    url = minted.get("url")
    if not url:
        return {**rec, "ok": False, "error": minted.get("error", "no url")}
    dest = raw / (str(d["id"]) + (d.get("ext") or ""))
    try:
        seat.fetch(url, dest, d.get("size"))
    except Exception as exc:  # noqa: BLE001 - one file's failure is one row
        dest.unlink(missing_ok=True)
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return {**rec, "ok": False, "error": str(exc)[:200], "http_status": status}
    sha = hashlib.sha256(dest.read_bytes()).hexdigest()
    if sha in seen:
        dest.unlink()
        return {**rec, "ok": True, "sha256": sha, "duplicate_of": seen[sha]}
    seen[sha] = str(d["id"])
    return {**rec, "ok": True, "sha256": sha, "path": str(dest)}


def fetch_all(
    seat: Any, matter_id: str, targets: list[dict[str, Any]], data: Path, log: Callable[[str], None]
) -> list[dict[str, Any]]:
    """Resumable: a row already ``ok`` in ``pulled.jsonl`` is not fetched again."""
    raw = data / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    log_path = data / "pulled.jsonl"
    rows = {r["id"]: r for r in read_jsonl(log_path)}
    seen = {r["sha256"]: r["id"] for r in rows.values() if r.get("ok") and r.get("path")}
    todo = [t for t in targets if not (rows.get(t["id"]) or {}).get("ok")]
    for i in range(0, len(todo), BATCH):
        batch = todo[i : i + BATCH]
        minted = _mint(seat, matter_id, [b["id"] for b in batch], log)
        for b in batch:
            row = _fetch_one(seat, b, minted.get(b["id"]) or {}, raw, seen)
            rows[b["id"]] = row
            append_jsonl(log_path, row)
        log(f"pulled {min(i + BATCH, len(todo))}/{len(todo)}")
    return [rows[t["id"]] for t in targets]


# ---- emails ----------------------------------------------------------------------
def _open_msg(path: Path) -> dict[str, Any] | None:
    """The email's fields, or None when it cannot be read. A body stored in a
    legacy code page (cp1252 curly quotes, read live 2026-10-06 on a trial
    matter) raises inside extract_msg; the second attempt names the encoding."""
    for encoding in (None, "cp1252"):
        try:
            return _read_msg(path, encoding)
        except Exception:  # noqa: BLE001 - an unreadable container is reported by name, never guessed at
            continue
    return None


def _read_msg(path: Path, encoding: str | None) -> dict[str, Any]:
    import extract_msg

    m = extract_msg.Message(str(path), **({"overrideEncoding": encoding} if encoding else {}))
    try:
        atts = []
        for att in m.attachments:
            data = getattr(att, "data", None)
            if isinstance(data, bytes):
                atts.append((att.longFilename or att.shortFilename or "unnamed", data))
        return {
            "subject": str(m.subject or "")[:200],
            "sender": (addresses(m.sender) or [""])[0],
            "recipients": addresses(m.to) + addresses(m.cc) + addresses(getattr(m, "bcc", "")),
            "date": str(m.date or ""),
            "body": str(m.body or ""),
            "attachments": atts,
        }
    finally:
        try:
            m.close()
        except Exception:  # noqa: BLE001 - closing must not mask what was read
            pass


def _email_text(row: dict[str, Any], msg: dict[str, Any]) -> str:
    head = [
        f"Email: {msg['subject'] or row.get('name')}",
        f"From: {msg['sender']}",
        f"To: {', '.join(msg['recipients'])}",
        f"Date: {msg['date']}",
        "",
    ]
    return "\n".join(head) + msg["body"]


def _attachment_rows(
    row: dict[str, Any], msg: dict[str, Any], data: Path, seen: dict[str, str]
) -> list[dict[str, Any]]:
    out = []
    for name, blob in msg["attachments"]:
        ext = os.path.splitext(name)[1].lower()
        if ext not in ATTACH_KEEP or len(blob) < ATTACH_MIN_BYTES:
            continue
        sha = hashlib.sha256(blob).hexdigest()
        if sha in seen:
            continue
        seen[sha] = f"att-{sha[:12]}"
        dest = data / "raw" / f"att-{sha[:12]}{ext}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blob)
        out.append(
            {
                "id": f"att-{sha[:12]}",
                "name": name,
                "ext": ext,
                "folder": row.get("folder"),
                "path": str(dest),
                "from_email": msg["subject"],
                "kind": "attachment",
            }
        )
    return out


def split_emails(rows: list[dict[str, Any]], data: Path, firm: DemandFirm, client: set[str]) -> dict[str, Any]:
    """Every pulled ``.msg``: walled (held out, its text kept for the gate) or
    kept (its body a document, its attachments documents)."""
    seen = {r["sha256"]: r["id"] for r in rows if r.get("ok") and r.get("sha256")}
    bodies = data / "email"
    bodies.mkdir(parents=True, exist_ok=True)
    kept: list[dict[str, Any]] = []
    walled: list[dict[str, Any]] = []
    unreadable: list[str] = []
    for row in rows:
        if not row.get("path") or (row.get("ext") or "").lower() != ".msg":
            continue
        msg = _open_msg(Path(row["path"]))
        if msg is None:
            unreadable.append(str(row.get("name")))
            continue
        text_path = bodies / f"{row['id']}.txt"
        text_path.write_text(_email_text(row, msg), encoding="utf-8")
        reason = wall_reason(msg["sender"], msg["recipients"], firm.firm_domains, client, firm.consumer_domains)
        if reason:
            walled.append(
                {
                    "id": row["id"],
                    "name": row.get("name"),
                    "reason": reason,
                    "text_path": str(text_path),
                    "attachments": len(msg["attachments"]),
                }
            )
            continue
        kept.append(
            {
                "id": row["id"],
                "name": f"Email: {msg['subject'] or row.get('name')}",
                "ext": ".txt",
                "folder": row.get("folder"),
                "text_path": str(text_path),
                "kind": "email_body",
                "subject": msg["subject"],
            }
        )
        kept += _attachment_rows(row, msg, data, seen)
    return {"kept": kept, "walled": walled, "unreadable": unreadable}


def run(
    seat: Any, matter_id: str, firm: DemandFirm, client_emails: set[str], data: Path, log: Callable[[str], None]
) -> dict[str, Any]:
    """The pull, end to end. Returns the corpus rows and the wall's report;
    raises PullError when a file a retry could fix is still not pulled."""
    data.mkdir(parents=True, exist_ok=True)
    docs = seat.list_files(matter_id)
    folders = seat.folder_tree(matter_id)
    (data / "manifest.json").write_text(json.dumps(docs, indent=1), encoding="utf-8")
    targets = select(docs, folder_paths(folders), firm)
    log(f"{len(docs)} documents listed, {len(targets)} selected")
    rows = fetch_all(seat, matter_id, targets, data, log)
    retry = [r for r in rows if not r.get("ok") and r.get("http_status") != HTTP_ABSENT]
    if retry:
        raise PullError(f"{len(retry)} of {len(rows)} documents are not pulled for a reason a retry can fix")
    absent = [str(r.get("name")) for r in rows if not r.get("ok")]
    mail = split_emails(rows, data, firm, client_emails)
    files = [
        {**r, "kind": "file"} for r in rows if r.get("ok") and r.get("path") and (r.get("ext") or "").lower() != ".msg"
    ]
    report = {
        "listed": len(docs),
        "selected": len(targets),
        "absent_from_storage": absent,
        "emails_kept": sum(1 for k in mail["kept"] if k["kind"] == "email_body"),
        "emails_walled": [{"name": w["name"], "reason": w["reason"]} for w in mail["walled"]],
        "emails_unreadable": mail["unreadable"],
    }
    corpus = files + mail["kept"]
    (data / "corpus.json").write_text(json.dumps(corpus, indent=1), encoding="utf-8")
    (data / "walled.json").write_text(json.dumps(mail["walled"], indent=1), encoding="utf-8")
    (data / "pull-report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    log(f"corpus {len(corpus)} documents; {len(mail['walled'])} emails behind the privilege wall")
    return report
