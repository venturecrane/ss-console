#!/usr/bin/env python3
"""Seed a staging matter's document record, so a drafting pass can be proven.

WHY. Pilot card 18 (demand-letter-drafter) was rehearsed 2026-08-12 and refused,
correctly: the matter held only a Complaint and a Summons, and the skill requires
every figure to trace to a source document. The drafting pass Chris asked for
cannot be proven against a record that does not exist.

WHY IT LIVES IN THE REPO. Uploaded, these fixtures exist only inside a Smokeball
staging tenant, a runtime layer nothing in git reconstructs. A staging reset
would erase the evidence behind a green card command and leave no trace of what
was erased. Idempotent by document name, so re-running after a reset restores the
set and re-running now is a no-op.

The records themselves live in ``lib/seed_fixtures/``, one module per matter,
each next to its own rationale. Read the fixture's ``purpose`` before changing
it: 2026-PI-102 contradicts its own Complaint ON PURPOSE.

RUN IT ON THE SEAT. The staging refresh token is not in Infisical; it lives on
the seat volume (ADR 0010) and must not cross the wire:

    B64=$(base64 < operator/bin/seed-staging-matter.py | tr -d '\\n')
    flyctl ssh console --app hermes-pilot-smokeball -C "sh -c 'echo $B64 |
      base64 -d > /tmp/seed.py && chmod 644 /tmp/seed.py &&
      su hermes -c \\"env SMD_DIRECT_WRITE_ACTOR=seed-staging-matter /opt/hermes/.venv/bin/python /tmp/seed.py <slug>\\"'"

SMD_DIRECT_WRITE_ACTOR is required: every Smokeball write is recorded in the
seat's audit ledger first, and a write whose writer is unnamed is refused
(smokeball_connector/write_record.py).

...but the fixture package has to reach the seat too, so in practice ship the
whole ``lib/seed_fixtures/`` directory alongside it or run from a checkout.

NO PERIODS IN DOCUMENT NAMES. Smokeball reads the tail after a "." as a file
extension and drops it ("Dr. Okonkwo" materialized as "Dr"). Materialization is
also asynchronous: a short count on the first read-back is not a failed upload.

THE MEDICAL RECORDS ARE PDFs, GENERATED, NOT COMMITTED. ``--records DIR``
uploads a matter's record set: the PDFs ``python -m jobs.medchron.sample``
writes to ``<out>/<slug>/records/`` from the committed case spec
(``Fixture.spec``). The folder must hold exactly the spec's documents, by
name: a missing or extra file refuses before anything is sent, so a stale or
half-built folder can never be half-uploaded. Those PDFs are tens of
megabytes, so they reach the seat as files (``fly sftp``), not through the
base64 command above.

WHAT THIS CANNOT FIX. A matter carries no responsible-attorney assignment, and
``PATCH /matters`` accepts ``personAssistingStaffs`` with a 200 and applies
nothing (both the object and bare-id shapes, on a token whose granted scopes
include ``matters/write``). Not settable through the API surface; set it in the
Smokeball UI, or confirm the role conversationally, which the drafter offers.
"""

from __future__ import annotations

import argparse
import os
import sys

from pathlib import Path

from bin.lib.seed_fixtures import FIXTURES, Fixture, record_names


def _client():
    """Built lazily, INSIDE main, so the fixture content stays importable
    off-seat. The connector package only exists on a Machine; a module-level
    import would make the consistency tests skip everywhere they actually run,
    and a skipped test measures nothing."""
    sys.path.insert(0, "/app/connectors/smokeball")
    os.environ.setdefault("SMOKEBALL_REFRESH_TOKEN_FILE", "/opt/data/.smokeball-mcp/refresh_token")
    from smokeball_connector.client import build_client_from_env

    return build_client_from_env()


class RecordsMismatch(RuntimeError):
    pass


def record_files(fixture: Fixture, records: Path) -> list[tuple[str, bytes]]:
    """``(file name, bytes)`` for every record PDF, in the spec's order.
    Refuses unless the folder holds exactly the spec's set."""
    want = record_names(fixture)
    have = sorted(p.name for p in records.glob("*.pdf"))
    missing = sorted(set(want) - set(have))
    extra = sorted(set(have) - set(want))
    if missing or extra:
        raise RecordsMismatch(
            f"{records} does not hold {fixture.slug}'s record set: missing {missing[:5]}, unexpected {extra[:5]}"
        )
    return [(name, (records / name).read_bytes()) for name in want]


def names_on_matter(files: list[dict]) -> set[str]:
    """Every name a file already on the matter answers to.

    Smokeball stores ``Report.pdf`` as name ``Report`` plus fileExtension ``.pdf``,
    so comparing an upload name to ``name`` alone never matches a PDF, and a
    re-run uploaded every record a second time (2026-11-10, 42 copies on the
    demo tenant). Deleted files do not count as present."""
    out: set[str] = set()
    for f in files:
        if f.get("isDeleted"):
            continue
        name = str(f.get("name") or "")
        out.add(name)
        ext = str(f.get("fileExtension") or "")
        if ext:
            out.add(name + (ext if ext.startswith(".") else "." + ext))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("slug", choices=sorted(FIXTURES), help="which matter's record to seed")
    ap.add_argument("--list", action="store_true", dest="just_list", help="print the set and exit; uploads nothing")
    ap.add_argument("--records", type=Path, help="the generated record PDFs for this matter (<out>/<slug>/records)")
    args = ap.parse_args(argv)
    fixture = FIXTURES[args.slug]
    uploads = [(name, text.encode("utf-8")) for name, text in fixture.docs]
    if args.records is not None:
        try:
            uploads += record_files(fixture, args.records)
        except RecordsMismatch as exc:
            print(f"REFUSED: {exc}")
            return 2

    print(f"{fixture.slug}  matter {fixture.matter_id}  ({len(uploads)} documents)")
    print(f"PURPOSE: {fixture.purpose}\n")
    if args.just_list:
        for name, data in uploads:
            print(f"  {name}  ({len(data)} bytes)")
        print("\nNothing uploaded.")
        return 0

    c = _client()
    existing = c.get(f"/matters/{fixture.matter_id}/documents/files", Limit=200, Offset=0)
    have = names_on_matter((existing.get("value") if isinstance(existing, dict) else existing) or [])
    print(f"already on matter: {len(have)}")

    added = skipped = failed = 0
    for name, data in uploads:
        if name in have:
            print(f"  SKIP (exists): {name}")
            skipped += 1
            continue
        try:
            res = c.add_file(fixture.matter_id, name, data)
            fid = res.get("fileId") if isinstance(res, dict) else res
            print(f"  ADDED: {name}  -> {fid}")
            added += 1
        except Exception as e:  # noqa: BLE001 — one bad upload must not strand the rest
            print(f"  FAILED: {name}  ({type(e).__name__}: {str(e)[:200]})")
            failed += 1
    print(f"\nadded={added} skipped={skipped} failed={failed} of {len(uploads)}")
    print("Materialization is async; re-read the matter before concluding a count is short.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
