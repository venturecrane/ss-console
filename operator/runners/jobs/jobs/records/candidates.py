"""Which of the matter's files and emails a record is looked for in.

Split out of ``run.py`` (its size ceiling). Two cases were added on 2026-11-24,
after an attorney asked for "every parent statement in the file, attached" and
the statements sat in 262 emails the job never looked at:

* **Every document of a kind** (``rec["every"]``, from the parse): a file is a
  candidate only when its name carries every word of the record, never one word
  of it; each candidate is looked in (``search.locate`` does not stop at the
  first found), and a record with more candidates than one request carries
  (:data:`MAX_EVERY_CANDIDATES`) holds at resolve rather than sending some of
  them as if they were all.
* **Emails** (``.msg``) are matter files: found by their subject and body at
  $0 (``rank.text_matches``), read for an ``every`` record always, and for any
  other record only when no file name and no already-read PDF answers for it;
  each email that is a candidate becomes a PDF of its own (``emails.py``).

The cap of :data:`MAX_CANDIDATES` priced candidates for an ordinary record
stays: each is pages the page finder is paid to read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import emails, free as free_mod, rank, search
from .outcome import RecordsHold

#: Priced candidate files per ordinary record, at most.
MAX_CANDIDATES = 3
#: Candidates for a record asked for as every document of a kind: the files one
#: request carries (records_contract batches: 3 replies of 3).
MAX_EVERY_CANDIDATES = 9
#: Emails one job reads at $0 to find a record by its subject and body. Past it,
#: an ``every`` record holds (a partial search is never reported as complete);
#: any other record is looked for in the matter's files only.
MAX_EMAILS_SEARCHED = 600


def email_raw(run: Any, mid: str, file_id: str) -> Path:
    """The ``.msg`` itself, downloaded once to ``data/emails/``."""
    from smokeball_connector import matter_files as mf

    raw = run.data / "emails" / f"{file_id}.msg"
    raw.parent.mkdir(parents=True, exist_ok=True)
    if not raw.is_file():
        tmp = raw.with_suffix(".part")
        mf._download_to(run.client, mid, file_id, tmp, emails.MAX_EMAIL_BYTES)
        tmp.replace(raw)
    return raw


def email_pdf(run: Any, mid: str, file_id: str, dest: Path) -> None:
    """The email as a PDF at ``dest``, or ``StageRefused`` with the reason."""
    from smokeball_connector import matter_files as mf

    try:
        msg = emails.read(email_raw(run, mid, file_id))
    except emails.EmailUnreadable as exc:
        raise mf.StageRefused(str(exc)) from exc
    emails.to_pdf(msg, dest)


def email_hits(run: Any, mid: str, rows: list[dict[str, Any]], rec: dict[str, Any]) -> list[tuple[str, int]]:
    """$0: the matter's emails whose subject and body carry every word of the
    record, best first. Over :data:`MAX_EMAILS_SEARCHED` emails an ``every``
    record holds and any other record reads none."""
    from smokeball_connector import matter_files as mf

    msgs = [r for r in rows if rank.extension(r) == "msg" and r.get("id")]
    if len(msgs) > MAX_EMAILS_SEARCHED:
        if rec.get("every"):
            raise RecordsHold(
                f"scope_unresolved: the matter has {len(msgs)} emails, more than the "
                f"{MAX_EMAILS_SEARCHED} one records job reads"
            )
        return []
    hits: list[tuple[str, int]] = []
    for row in msgs:
        fid = str(row["id"])
        try:
            msg = emails.read(email_raw(run, mid, fid))
        except (emails.EmailUnreadable, mf.StageRefused, OSError):
            continue  # an email that cannot be read is not a match; it is never guessed at
        sc = rank.text_matches(emails.search_text(msg), rec)
        if sc > 0:
            hits.append((fid, sc))
    return sorted(hits, key=lambda h: (-h[1], h[0]))


def ranked(run: Any, mid: str, rec: dict[str, Any], files: dict[str, Any]) -> list[tuple[str, int]]:
    """The record's candidates, in the order the page finder tries them: free
    files (a complete index), then for an ordinary record the PDFs already read
    whole (``free.py``) and the best :data:`MAX_CANDIDATES` priced ones; for an
    ``every`` record, every candidate. ``[]`` when nothing scores for it."""
    from smokeball_connector import matter_files as mf

    try:
        rows, _complete = mf._all_files(run.client, mid)
    except Exception as exc:  # could not look is never "no file is named for it"
        raise RecordsHold("finder_failed: the matter's files could not be listed") from exc
    every = bool(rec.get("every"))
    named = dict(rank.rank(rows, rec, top=len(rows) if every else rank.TOP))
    if every:
        names = {str(r.get("id")): r.get("name") for r in rows}
        named = {fid: sc for fid, sc in named.items() if rank.text_matches(names.get(fid), rec)}
        named.update({fid: sc for fid, sc in email_hits(run, mid, rows, rec) if fid not in named})
    got = [(fid, sc) for fid, sc in named.items() if run._fetch(mid, fid, files)]
    free = [fs for fs in got if search.unread(files[fs[0]]) == 0]
    priced = [fs for fs in got if search.unread(files[fs[0]]) > 0]
    priced.sort(key=lambda fs: (-fs[1], search.read_none(files[fs[0]]), int(files[fs[0]]["page_count"]), fs[0]))
    if every:
        if len(got) > MAX_EVERY_CANDIDATES:
            raise RecordsHold(
                f"cut_refused: record {rec['n']} asks for every document of a kind and {len(got)} files may "
                f"hold one, more than the {MAX_EVERY_CANDIDATES} one request reads and carries"
            )
        return free + priced
    extras = free_mod.extras(run, mid, rows, files, dict(got))
    if not got and not extras:
        # Nothing on the matter is named for it: its emails, by subject and body.
        got = [(fid, sc) for fid, sc in email_hits(run, mid, rows, rec) if run._fetch(mid, fid, files)]
        free = [fs for fs in got if search.unread(files[fs[0]]) == 0]
        priced = sorted((fs for fs in got if search.unread(files[fs[0]]) > 0), key=lambda fs: (-fs[1], fs[0]))
    return free + extras + priced[:MAX_CANDIDATES]


def pdf_name(meta: dict[str, Any]) -> str:
    """The attachment's name: the matter file's, an email's without its ``.msg``."""
    name = str(meta.get("name") or "")
    return name[:-4] if meta.get("email") and name.lower().endswith(".msg") else name


__all__ = [
    "MAX_CANDIDATES",
    "MAX_EMAILS_SEARCHED",
    "MAX_EVERY_CANDIDATES",
    "email_hits",
    "email_pdf",
    "email_raw",
    "pdf_name",
    "ranked",
]
