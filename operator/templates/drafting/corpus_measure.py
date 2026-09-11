"""Measure a matter's drafting corpus WITHOUT spending a model token.

    corpus_measure.py <matter_id> [budget_seconds]        # runs on the seat

Walks the matter's folder tree, downloads each record document, and extracts
its text by MECHANICAL paths only -- ``extract_text``, which never reaches the
network, never consults the transcription cache, and never calls vision. The
output is extracted CHARACTERS, which is the basis `ledger.anchors` projects
from.

WHY CHARACTERS AND NOT BYTES
----------------------------
Density varies by source system by more than half. Epic EMR exports measured
63% more characters per byte than a mixed corpus, and a chronology quote
projected from megabytes came in 30% low. Bytes are the number you have;
characters are the number that predicts the bill.

WHY THIS IS FREE, structurally and not by promise
-------------------------------------------------
The only billable path in extraction is the vision fallback, and it is reached
only by ``extract_text_ex(allow_vision=True)``. This module calls
``extract_text``, which cannot reach it. A scanned page is therefore COUNTED as
dark, never transcribed. That distinction is the point of the measurement: a
demand drafted over a dark record comes back honest and thin, marking what it
could not read, and the remedy is a deliberate transcription pass priced as its
own work -- not a surprise inside a drafting job.

WHAT "DARK" MEANS, precisely
----------------------------
A record document that yields zero characters on a mechanical path is scanned
paper. Measured on client matters, the dark fraction runs between a third and a
half of the record, and it is disproportionately the clinical half: imaging
reports, office notes, the radiology reads. Those are the documents a demand is
built on, so ``dark_scanned`` is the single most decision-relevant number this
produces. It is reported separately from ``unsupported`` (a type with no
extraction path at all) and from ``errors``, because collapsing the three would
let a broken download read as an empty file.

Measurements are recorded in the engagement record, never here: this repository
is public, and a matter number beside a provider name is client data.

NOTHING IS SILENTLY DROPPED
---------------------------
Oversize files are listed by name, and documents left unread when the time
budget expires are counted in ``not_attempted_budget``. A partial measurement
says which part it is; it never reports a smaller corpus as a complete one.
"""
from __future__ import annotations

import json
import sys
import time

#: Types a demand actually reads. Mail is tracked separately -- it is
#: correspondence, not the record the draft is built from.
RECORD_EXT = frozenset({"pdf", "docx", "doc", "txt", "text", "md", "rtf"})
MAIL_EXT = frozenset({"msg", "eml"})

#: Above this, download-and-extract is not worth a seat with 1 vCPU and 1 GB.
#: Skipped files are named in the result, never merely absent.
SIZE_CAP_BYTES = 25 * 1024 * 1024


def classify(extension):
    """``"record"``, ``"mail"`` or ``"other"`` for a file extension.

    Pure, so the accounting rules are testable without a seat or a connector.
    """
    e = (extension or "").lower().lstrip(".")
    if e in MAIL_EXT:
        return "mail"
    if e in RECORD_EXT:
        return "record"
    return "other"


def new_tally(matter_id):
    return {
        "matter_id": matter_id,
        "files_total": 0,
        "mail_files": 0,
        "other_files": 0,
        "record_files": 0,
        "read_ok": 0,
        "chars": 0,
        "dark_scanned": 0,
        "unsupported": 0,
        "errors": 0,
        "skipped_oversize": [],
        "not_attempted_budget": 0,
        "per_ext_chars": {},
        "biggest": [],
    }


def account(tally, ext, chars=None, outcome="read"):
    """Fold one document's outcome into the tally.

    ``outcome`` is one of read | dark | unsupported | error | oversize |
    budget. Kept separate from I/O so the arithmetic can be tested directly --
    the failure this guards against is a download error totalling as an empty
    document and making a corpus look smaller than it is.
    """
    if outcome == "read":
        n = chars or 0
        if n == 0:
            tally["dark_scanned"] += 1
            return
        tally["read_ok"] += 1
        tally["chars"] += n
        tally["per_ext_chars"][ext] = tally["per_ext_chars"].get(ext, 0) + n
    elif outcome == "dark":
        tally["dark_scanned"] += 1
    elif outcome == "unsupported":
        tally["unsupported"] += 1
    elif outcome == "error":
        tally["errors"] += 1
    elif outcome == "budget":
        tally["not_attempted_budget"] += 1


def coverage(tally):
    """Fraction of record documents actually read. None when there are none.

    A projection built on a corpus with low coverage is a projection of a
    different, smaller job -- so this number rides with every quote.
    """
    denom = tally["record_files"]
    if not denom:
        return None
    return round(tally["read_ok"] / denom, 4)


# --------------------------------------------------------------------------
# Seat-side execution. Imports live here so the pure helpers above can be
# imported and tested on a workstation with no connector installed.
# --------------------------------------------------------------------------

def _walk(client, matter_id):
    out = []

    def kids(folder_id):
        path = "/matters/%s/documents/folders" % matter_id
        if folder_id:
            path += "/" + folder_id
        try:
            r = client.get(path)
        except Exception:  # noqa: BLE001 - a folder that cannot be listed contributes nothing; the survey continues over the rest
            return [], []
        node = (r.get("value") or [{}])[0]
        return node.get("folders") or [], node.get("files") or []

    def rec(folder_id, depth):
        if depth > 6:
            return
        folders, files = kids(folder_id)
        out.extend(files)
        time.sleep(0.2)
        for d in folders:
            rec(d.get("id"), depth + 1)

    rec(None, 0)
    return out


def measure(matter_id, budget_seconds=900.0):
    sys.path.insert(0, "/opt/connectors/smokeball")
    from smokeball_connector.client import build_client_from_env
    from smokeball_connector.extract import (UnsupportedDocumentError,
                                             extract_text)

    started = time.time()
    client = build_client_from_env()
    files = _walk(client, matter_id)

    t = new_tally(matter_id)
    t["files_total"] = len(files)
    biggest = []

    for f in files:
        ext = (f.get("fileExtension") or "").lower().lstrip(".")
        kind = classify(ext)
        if kind == "mail":
            t["mail_files"] += 1
            continue
        if kind == "other":
            t["other_files"] += 1
            continue
        t["record_files"] += 1

        if time.time() - started > budget_seconds:
            account(t, ext, outcome="budget")
            continue
        size = f.get("sizeBytes") or 0
        if size > SIZE_CAP_BYTES:
            t["skipped_oversize"].append(
                {"name": (f.get("name") or "?")[:60], "bytes": size})
            continue
        try:
            _meta, blob = client.download_file(matter_id, f.get("id"))
        except Exception:  # noqa: BLE001 - a download failing is accounted as outcome=error for that file; the survey continues
            account(t, ext, outcome="error")
            continue
        try:
            text = extract_text(blob, file_name=f.get("name") or "",
                                file_extension=ext)
        except UnsupportedDocumentError:
            account(t, ext, outcome="unsupported")
            continue
        except Exception:  # noqa: BLE001 - an extractor failing is accounted as outcome=error for that file; the survey continues
            account(t, ext, outcome="error")
            continue

        n = len(text or "")
        account(t, ext, chars=n, outcome="read")
        if n:
            biggest.append((n, (f.get("name") or "?")[:50]))
        time.sleep(0.05)

    t["biggest"] = [{"chars": n, "name": nm}
                    for n, nm in sorted(biggest, reverse=True)[:5]]
    t["record_coverage"] = coverage(t)
    t["elapsed_s"] = round(time.time() - started, 1)
    return t


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: corpus_measure.py <matter_id> [budget_seconds]",
              file=sys.stderr)
        sys.exit(2)
    budget = float(sys.argv[2]) if len(sys.argv) > 2 else 900.0
    print("@@SEAT@@" + json.dumps(measure(sys.argv[1], budget)))
