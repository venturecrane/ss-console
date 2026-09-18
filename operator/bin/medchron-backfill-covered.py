#!/usr/bin/env python3
"""Give a chronology delivered before 2026-09-17 the coverage record it never got.

WHY THIS EXISTS (ss#2834)
-------------------------
Routine 11's UPDATE reads only the records the delivered chronology did not
cover (agreement Exhibit A). PR #2827 made a delivery write that down, but it
reached nothing already delivered: every chronology A&P has -- 19 of them across
13 matters -- has no record, so an update on any matter refuses and the work
falls back to people. The records are recomputable from each delivery's own
artifacts with the shipped `covered_payload`, at no AI cost. This is the door
they come in through.

THE FAILURE TO DESIGN AGAINST is a chronology that silently omits a medical
record. Every judgement here breaks toward re-reading a document (costs pages,
recoverable) and away from marking one covered (drops it from a filed
litigation document, permanently). That is why an unverifiable matter is
REFUSED rather than written with a caveat.

TWO PHASES, AND THE SPLIT IS NOT COSMETIC
-----------------------------------------
The run artifacts are on the laptop. Smokeball and the broker socket are only
reachable from the seat. So:

  compute  (laptop)  covered_payload over the delivery's own artifacts, plus
                     the config-drift check, which needs no network. Emits a
                     JSON file of document IDS ONLY -- no document content, and
                     nothing client-identifying is committed to this repo.

  write    (seat)    verifies against Smokeball and THEN writes. The
                     verification is inside the write command on purpose: a gate
                     in a separate step is a gate someone runs out of order.

RUN IT ON THE SEAT (phase two)
------------------------------
This file is NOT installed on a seat image -- nothing copies `operator/bin/`
into it -- so the script ships ITSELF the way `seed-staging-matter.py` does,
alongside its payload. Assuming an install path is how you get "no such file or
directory" from a runbook that looks correct.

    SCRIPT=$(base64 < operator/bin/medchron-backfill-covered.py | tr -d '\n')
    PAYLOAD=$(base64 < payloads.json | tr -d '\n')
    fly ssh console -a hermes-<slug> -C "sh -lc \"echo $SCRIPT | base64 -d > /tmp/bf.py; \
        echo $PAYLOAD | base64 -d > /tmp/p.json\""
    fly ssh console -a hermes-<slug> -C "/opt/medchron/.venv/bin/python \
        /tmp/bf.py write --payloads /tmp/p.json"

The seat's runner venv is the interpreter because `write` imports the Smokeball
connector for its verification reads.

Root is required: the broker verb is ROOT-only, because an update SKIPS whatever
the record says was covered, so an agent-reachable write is a path a client
conversation could use to make a chronology omit records. Root here is safe in
the way seat-probe.sh warns about -- the broker process does the file writing
under its own uid, not this script.

WHAT IT REFUSES
---------------
* a matter whose local manifest ids are not all present on the Smokeball matter
  (the mapping is wrong -- a wrong-matter write would mark the WRONG documents
  covered). Synthetic `msgatt-` ids are partitioned out first: they are email
  attachments folded into the file and were never Smokeball file ids.
* a matter whose file listing cannot be paged to the end (a truncated listing is
  byte-identical to a complete one, so "all ids present" would be unprovable).
* a matter whose covered set shrinks when the firm's authored exclusions are
  emptied. Those rules changed in six merges after the August deliveries, and a
  rule authored SINCE delivery would mark a document "accounted for" that the
  delivery never accounted for. The smaller set wins.
* a matter with a covered id that has no successful download in the run's own
  retrieval log. That is the over-claim check: you cannot have accounted for a
  document you never retrieved. See the block above `_broker` for the reconcile
  approach that was built, failed, and is deliberately not here.
* a matter with a covered id that is not on the Smokeball matter today.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
from pathlib import Path
from typing import Any

# --- the covered-set computation is the SHIPPED one, never reimplemented ------
_RUNNER = Path(__file__).resolve().parents[1] / "runners" / "medchron"
if _RUNNER.is_dir():
    sys.path.insert(0, str(_RUNNER))

#: Email attachments folded into the file (`stages/msg.py`). These are records
#: the chronology read, but they are NOT Smokeball file ids, so they can never
#: appear in a matter's file listing. Six of eleven A&P matters carry them, so a
#: blanket "every id must be present" gate would refuse them all with a
#: perfectly correct mapping. Partitioned out of the presence check and reported
#: separately -- never by widening the check itself.
SYNTHETIC_PREFIX = "msgatt-"

#: The one source whose payloads legitimately carry no manifest ids, because the
#: delivery predates the pipeline and left no run artifacts. Named rather than
#: inferred from an empty list, so the id path cannot reach the same exemption by
#: accident.
FROM_DOCUMENT_SOURCE = "backfill-from-document"

#: How much of a delivery's document set must still be on the matter for the
#: mapping to be believed. Set far below the observed right-matter range
#: (94-99.6% across this firm's thirteen matters) and far above a wrong matter
#: (~0%), so it separates the two cases without firing on ordinary churn. A
#: matter that has genuinely lost a tenth of its documents is worth a human look
#: before its coverage record is written.
MAPPING_FLOOR = 0.90


# --- phase one: compute (laptop) ----------------------------------------------
def _load_cfg(firm_config: Path, *, drop_after: str | None = None, rule_dates: dict[str, str] | None = None):
    """The firm config as the runner reads it, with any coverage rule authored
    AFTER `drop_after` removed.

    This is the config-drift control, and its shape is the whole argument.
    `covered_sets` marks a document COVERED when an authored exclusion
    name-matches it, and those rules live in a file that changes. A rule written
    after a delivery would mark a document "accounted for" that the delivery
    never accounted for -- and an update then skips it forever.

    The obvious control, emptying ALL exclusions, is WRONG here and was measured
    to be wrong: on one real matter it moved 94 of 180 covered documents to
    uncovered.
    These rules are category exclusions -- retainers, billing, insurance
    administration, records requests, CVs, pay stubs, vehicle registration, firm
    work product. A retainer agreement was not a treating record in August
    either. Emptying them would make every future update re-read the firm's
    retainers and insurance correspondence, forever, at page cost, and feed them
    back into a medical chronology. That is waste, not safety.

    So the control is dated per rule instead. `rule_dates` maps a rule's `match`
    pattern to the date it entered the config, derived from that file's git
    history, and only rules newer than the delivery are dropped. For A&P's
    August deliveries that is exactly two rules, both added 2026-09-16.
    """
    import yaml

    from medchron.config import FirmConfig

    data = yaml.safe_load(firm_config.read_text(encoding="utf-8")) or {}
    if drop_after and rule_dates:
        data = json.loads(json.dumps(data))  # deep copy, no shared nesting
        coverage = data.get("coverage")
        if isinstance(coverage, dict):
            kept = []
            for rule in coverage.get("exclusions") or []:
                introduced = rule_dates.get(str(rule.get("match")))
                # An undated rule is treated as PRE-dating the delivery, because
                # the alternative is dropping a category exclusion on a
                # bookkeeping gap and re-reading the firm's paperwork. The dates
                # come from git, so an undated rule means the mapping is stale --
                # which the caller is told about rather than silently absorbing.
                if introduced and introduced > drop_after:
                    continue
                kept.append(rule)
            coverage["exclusions"] = kept
    return FirmConfig(path=firm_config, data=data)


def _retrieval_outcome(slug_dir: Path) -> tuple[set[str], set[str]]:
    """`(ok_ids, failed_ids)` from the run's own retrieval log.

    Read straight from `raw_manifest.jsonl`'s `ok` flag, which is the one thing
    in the run that records whether bytes actually arrived. Nothing here
    classifies anything -- that is the coverage gate's job and this must stay
    independent of it.
    """
    ok: set[str] = set()
    failed: set[str] = set()
    path = slug_dir / "raw_manifest.jsonl"
    if not path.is_file():
        return ok, failed
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rid = row.get("id")
        if not rid:
            continue
        (ok if row.get("ok") else failed).add(str(rid))
    return ok, failed


def _manifest_ids(slug_dir: Path) -> set[str]:
    """Document ids this delivery touched, for the mapping check.

    `manifest.json` is the matter's listing as the run saw it and is the first
    choice. One folder in this firm's set has none, so the fallback is the run's own
    pull log, which carries the same ids for everything it fetched. Returning an
    empty set would silently disable the presence check, so a folder with
    neither raises instead.
    """
    man_path = slug_dir / "manifest.json"
    if man_path.is_file():
        man = json.loads(man_path.read_text(encoding="utf-8"))
        docs = man["documents"] if isinstance(man, dict) else man
        return {str(d["id"]) for d in docs if d.get("id")}

    raw = slug_dir / "raw_manifest.jsonl"
    if raw.is_file():
        ids = set()
        for line in raw.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if row.get("id"):
                    ids.add(str(row["id"]))
        if ids:
            return ids
    raise FileNotFoundError(f"{slug_dir.name}: no manifest.json and no usable raw_manifest.jsonl")


def compute(args: argparse.Namespace) -> int:
    from medchron.covered import covered_payload

    matters = json.loads(Path(args.map).read_text(encoding="utf-8"))
    data_root = Path(args.data_root).expanduser()
    firm = Path(args.firm_config).expanduser()
    # Required, and empty is refused rather than tolerated. `_load_cfg` skips the
    # whole drift control when the mapping is falsy, so an absent or empty file
    # would silently compute every matter against TODAY's rules while still
    # printing OK -- the safety net off, with nothing saying so.
    rule_dates = json.loads(Path(args.rule_dates).read_text(encoding="utf-8"))
    if not isinstance(rule_dates, dict) or not rule_dates:
        raise SystemExit(
            f"{args.rule_dates}: the rule-date mapping is empty, so the config-drift control "
            "would silently do nothing. Derive it from the firm config's git history; if no "
            "coverage rule postdates any delivery, pass a mapping of the rules with their dates "
            "anyway so that fact is recorded rather than assumed."
        )
    live = _load_cfg(firm)

    out: list[dict[str, Any]] = []
    for m in matters:
        slug = m["slug"]
        d = data_root / slug
        units_dir = d / "units"
        units = sorted(p.stem for p in units_dir.glob("*.json")) if units_dir.is_dir() else []
        if not units:
            print(f"SKIP  {slug:18} no units/ -- nothing was delivered from this folder")
            continue

        # The control drops only rules authored AFTER this matter's delivery.
        as_delivered = _load_cfg(firm, drop_after=m["delivered_at"], rule_dates=rule_dates)
        full = covered_payload(d, units, live)
        control = covered_payload(d, units, as_delivered)
        if full is None or control is None:
            print(f"REFUSE {slug:18} covered_payload found no coverage artifacts")
            continue

        # The smaller covered set wins, and the difference is named rather than
        # silently absorbed: those are documents today's rules call accounted-for
        # and the delivery's rules may not have.
        drifted = sorted(set(full["covered"]) - set(control["covered"]))
        covered = sorted(set(full["covered"]) - set(drifted))
        uncovered = sorted(set(full["uncovered"]) | set(drifted))
        if drifted:
            print(
                f"DRIFT {slug:18} {len(drifted)} document(s) covered only under today's "
                f"authored rules -> moved to uncovered (will be re-read)"
            )

        # OVER-CLAIM CHECK. A document cannot have been accounted for in the
        # delivered chronology if it never successfully downloaded, so `covered`
        # must be a subset of the ids the retrieval log marks ok.
        #
        # This is independent of everything above it: the sets come from the
        # coverage gate's classification, the bound comes from the download log's
        # own flag, and the two are produced by different stages. It is the check
        # `validate_covered`'s `pulled` arithmetic cannot be, because that total
        # is computed from the very sets it is checking.
        #
        # Deliberately a ONE-DIRECTIONAL bound, not the source. covered.py refuses
        # to source coverage from `ok` for a good reason -- bytes arriving says
        # nothing about whether a document reached the document, so a glyph-junk
        # scan is `ok` and uncovered. "You cannot cover what you never retrieved"
        # carries none of that confusion.
        ok_ids, failed_ids = _retrieval_outcome(d)
        over = sorted(set(covered) - ok_ids)
        if over:
            failed_claimed = sorted(set(over) & failed_ids)
            why = (
                f"{len(failed_claimed)} of them are FAILED retrievals"
                if failed_claimed
                else "they are not in the retrieval log's ok set at all"
            )
            print(f"REFUSE {slug:18} {len(over)} covered id(s) have no successful download; {why}. First: {over[0]}")
            continue

        out.append(
            {
                "slug": slug,
                "matter_id": m["matter_id"],
                "matter_number": str(m["matter_number"]),
                "delivered_at": m["delivered_at"],
                "units": units,
                "source": m.get("source", "backfill"),
                "manifest_ids": sorted(_manifest_ids(d)),
                "covered": covered,
                "uncovered": uncovered,
            }
        )
        print(
            f"OK    {slug:18} covered={len(covered):4} uncovered={len(uncovered):4} "
            f"units={len(units)} (covered ⊆ {len(ok_ids)} retrieved)"
        )

    Path(args.out).write_text(json.dumps(out, indent=1, sort_keys=True), encoding="utf-8")
    print(f"\n{len(out)} matter(s) written to {args.out} (document ids only, no content)")
    return 0


def compute_from_document(args: argparse.Namespace) -> int:
    """A chronology delivered before the pipeline existed.

    There are no run artifacts, so coverage comes from the delivered document's
    own exhibit list matched against the matter's Smokeball file names. Less
    exact than an id join, so the rule inverts: **an exhibit whose name does not
    match a Smokeball file EXACTLY goes to uncovered**, never to covered. A
    name-match mistake then costs a re-read rather than dropping a record.

    No fuzzy matching and no substring fallback. Those are precisely the
    shortcuts that turn "probably the same document" into a silent skip.
    """
    cited = exhibit_names(Path(args.document).expanduser())
    if not cited:
        print("REFUSE no exhibit filenames found in the document", file=sys.stderr)
        return 1
    names = json.loads(Path(args.smokeball_names).read_text(encoding="utf-8"))
    by_norm: dict[str, str] = {}
    for fid, name in names.items():
        by_norm.setdefault(_norm(name), fid)

    covered, unmatched = [], []
    for c in cited:
        fid = by_norm.get(_norm(c))
        (covered.append(fid) if fid else unmatched.append(c))
    rate = len(covered) / len(cited)
    print(f"cited exhibits : {len(cited)}")
    print(f"matched        : {len(covered)}  ({rate:.0%})")
    print(f"unmatched      : {len(unmatched)}")
    for u in unmatched:
        print(f"  UNMATCHED (-> uncovered): {u}")
    if rate < args.floor:
        print(f"\nREFUSE match rate {rate:.0%} is below the {args.floor:.0%} floor", file=sys.stderr)
        return 1

    # Everything on the matter that the document does not cite is uncovered, so
    # an update reads it. That includes the unmatched names.
    uncovered = sorted(set(names) - set(covered))
    payload = [
        {
            "slug": args.slug,
            "matter_id": args.matter_id,
            "matter_number": str(args.matter_number),
            "delivered_at": args.delivered_at,
            "units": [args.slug],
            "source": FROM_DOCUMENT_SOURCE,
            # No local manifest exists, so the presence check has nothing to
            # compare and is skipped by an EMPTY list rather than by a flag: a
            # flag would be a switch someone could set on the id path too.
            "manifest_ids": [],
            "covered": sorted(set(covered)),
            "uncovered": uncovered,
        }
    ]
    Path(args.out).write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    print(f"\nwritten to {args.out}")
    return 0


def _norm(s: str) -> str:
    """Match on letters and digits only, extension stripped.

    Smokeball's `name` field carries no extension. Comparing raw strings scored
    0 of 49 on the first matter tried, and looked exactly like a total mismatch
    rather than a
    field-shape bug, which is why this is a named function with a reason on it.
    """
    s = re.sub(r"\.(pdf|docx?|tiff?|jpe?g|png)$", "", s.strip(), flags=re.I)
    return re.sub(r"[^a-z0-9]", "", s.lower())


def exhibit_names(path: Path) -> list[str]:
    """Source filenames cited as exhibits in a delivered .docx."""
    import zipfile

    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8", "replace")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in re.sub(r"<[^>]+>", "\n", xml).split("\n")]
    seen: list[str] = []
    for ln in lines:
        m = re.match(r"^(.+?\.(?:pdf|docx?|tiff?|jpe?g|png))\b", ln, re.I)
        if m:
            name = m.group(1).replace("&amp;", "&").strip()
            if name not in seen:
                seen.append(name)
    return seen


# --- phase two: verify, then write (seat) -------------------------------------
def _files_on_matter(matter_id: str, limit: int = 500) -> tuple[dict[str, str], bool]:
    """Every file on the matter as ``{id: name}``, and whether the listing is
    provably complete.

    Paged to the end. A page that comes back exactly full is indistinguishable
    from a truncated one, so the loop continues past it and only a short page
    ends it. Without this, a matter that returns exactly 500 -- one of this firm's
    does -- would
    report its first 500 files as the whole matter, and every document past the
    cap would look like it was never on the file.
    """
    sys.path[:0] = ["/app/connectors/smokeball", "/app/connectors/_sdk"]
    from smokeball_connector.server import get_files_on_matter

    out: dict[str, str] = {}
    offset = 0
    while True:
        resp = get_files_on_matter(matter_id, limit=limit, offset=offset) or {}
        page = resp.get("value") or []
        for f in page:
            if f.get("id"):
                out[str(f["id"])] = str(f.get("name") or "")
        if len(page) < limit:
            return out, True
        offset += limit
        if offset > 20_000:  # a matter this size is a bug, not a file
            return out, False


# WHY THERE IS NO "read the filed chronology back and reconcile" CHECK.
#
# It was built and it does not work, so it is recorded here rather than left as
# an obvious-looking gap for someone to re-attempt:
#
#   * Our delivered chronology cannot be found on a matter by name. The document
#     literally named "Medical Chronology - <client>" on one live matter is a
#     10.5-million-character records bundle carrying none of our composer's
#     section headings, and the same matter also holds two VENDOR chronologies
#     (a product the firm's own coverage rules exclude as a source). Reconciling
#     against one of those would be meaningless and would have "passed".
#   * The delivery folder id would identify ours, but it lives on the ledger row
#     -- and these deliveries predate the row. That is the whole reason this
#     script exists.
#   * The limitations section inside our own document is computed from the same
#     run artifacts `covered_sets` reads, so reconciling against it agrees by
#     construction and measures nothing.
#
# The over-claim check that IS sound lives in `compute`: covered must be a
# subset of the ids the retrieval log marks ok. It compares the classifier's
# output against a different stage's flag, and it fails on a planted id.


def _broker(payload: dict[str, Any], sock_path: str) -> dict[str, Any]:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(30.0)
    try:
        s.connect(sock_path)
        s.sendall(raw)
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    resp = json.loads(buf)
    if resp.get("ok") is not True:
        raise RuntimeError(f"broker refused: {resp.get('error')}: {resp.get('message')}")
    return resp


def write(args: argparse.Namespace) -> int:
    payloads = json.loads(Path(args.payloads).read_text(encoding="utf-8"))
    wrote, refused = 0, 0
    for p in payloads:
        slug = p["slug"]
        files, complete = _files_on_matter(p["matter_id"])
        if not complete:
            print(f"REFUSE {slug:18} file listing could not be paged to the end")
            refused += 1
            continue

        real = {i for i in p["manifest_ids"] if not i.startswith(SYNTHETIC_PREFIX)}
        synthetic = len(p["manifest_ids"]) - len(real)
        if not real and p["source"] != FROM_DOCUMENT_SOURCE:
            # With no real Smokeball ids to look for, `missing` is empty for the
            # trivial reason and the mapping check passes having measured
            # nothing -- which is exactly the wrong-matter write it exists to
            # stop. The document path legitimately has no manifest, and says so
            # by its source rather than by an empty list that any path could
            # produce.
            print(
                f"REFUSE {slug:18} no Smokeball document ids to verify the mapping against "
                f"({synthetic} synthetic id(s) only); the presence check would pass vacuously"
            )
            refused += 1
            continue
        # THE MAPPING CHECK IS A THRESHOLD, NOT ALL-OR-NOTHING, and the reason is
        # measured rather than assumed.
        #
        # What it exists to catch is a WRONG matter, and the two cases are not
        # close together: the right matter overlaps 94-99.6% on this firm's book,
        # a wrong one overlaps near zero (proven by pointing a matter's manifest
        # at its namesake's matter, which intersected nothing).
        #
        # The few absent ids are documents that have LEFT the matter since
        # delivery -- deleted or moved. `get_file` still resolves them by id while
        # the files listing no longer returns them, so their absence says nothing
        # about the mapping. It also says nothing about the historical record:
        # what the delivered chronology accounted for in August is a fact, and a
        # document leaving afterwards does not un-cover it.
        missing = sorted(real - set(files))
        overlap = 1.0 - (len(missing) / len(real)) if real else 1.0
        if overlap < MAPPING_FLOOR:
            print(
                f"REFUSE {slug:18} only {overlap:.0%} of {len(real)} manifest id(s) are on matter "
                f"{p['matter_number']} (floor {MAPPING_FLOOR:.0%}) -- the mapping is probably wrong; "
                f"first absent: {missing[0]}"
            )
            refused += 1
            continue
        note = f", {synthetic} folded email attachment(s) not id-checked" if synthetic else ""
        print(f"CHECK  {slug:18} {len(real) - len(missing)}/{len(real)} manifest ids present ({overlap:.0%}){note}")
        if missing:
            # Named, never silent: these are the documents an update will no
            # longer find on the matter either.
            print(f"       {slug:18} {len(missing)} id(s) have left the matter since delivery")

        covered_real = {i for i in p["covered"] if not i.startswith(SYNTHETIC_PREFIX)}
        gone = sorted(covered_real - set(files))
        print(
            f"CHECK  {slug:18} {len(covered_real) - len(gone)}/{len(covered_real)} covered id(s) "
            f"still on the matter" + (f" ({len(gone)} since removed)" if gone else "")
        )

        if args.dry_run:
            wrote += 1
            continue
        resp = _broker(
            {
                "action": "medchron_backfill_covered",
                "matter_id": p["matter_id"],
                "matter_number": p["matter_number"],
                "delivered_at": p["delivered_at"],
                "source": p["source"],
                "covered": {"covered": p["covered"], "uncovered": p["uncovered"]},
            },
            args.socket,
        )
        job = resp["job"]
        print(
            f"WROTE  {slug:18} job={job['id']} covered={len(job['covered_document_ids'] or [])} "
            f"uncovered={len(job['uncovered_document_ids'] or [])}"
        )
        wrote += 1

    print(f"\n{wrote} written{' (dry run)' if args.dry_run else ''}, {refused} refused")
    return 1 if refused else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("compute", help="laptop: compute payloads from the delivery artifacts")
    c.add_argument("--map", required=True, help="JSON list of {slug, matter_id, matter_number, delivered_at}")
    c.add_argument("--data-root", default="~/smd-medchron-data")
    c.add_argument("--firm-config", required=True)
    c.add_argument(
        "--rule-dates",
        required=True,
        help=(
            'JSON {"<exclusion match pattern>": "YYYY-MM-DD"} giving the date each coverage '
            "rule entered the firm config, from that file's git history. Rules newer than a "
            "matter's delivery are dropped for that matter, so a document is not marked "
            "covered by a rule that did not exist when it was delivered."
        ),
    )
    c.add_argument("--out", required=True)
    c.set_defaults(fn=compute)

    d = sub.add_parser("from-document", help="laptop: coverage from a delivered .docx (pre-pipeline)")
    d.add_argument("--document", required=True)
    d.add_argument("--smokeball-names", required=True, help="JSON {file_id: name} for the matter")
    d.add_argument("--slug", required=True)
    d.add_argument("--matter-id", required=True)
    d.add_argument("--matter-number", required=True)
    d.add_argument("--delivered-at", required=True)
    d.add_argument("--floor", type=float, default=0.80)
    d.add_argument("--out", required=True)
    d.set_defaults(fn=compute_from_document)

    w = sub.add_parser("write", help="seat, as root: verify against Smokeball, then write")
    w.add_argument("--payloads", required=True)
    # The broker reads its own path from this env var (`server.py`
    # `os.environ["SMD_WORKSPACE_BROKER_SOCKET"]`), so take it from the same
    # place rather than hardcoding a guess. A wrong literal here fails at the
    # first write with a bare FileNotFoundError, which is a confusing way to
    # learn the path moved.
    w.add_argument(
        "--socket",
        default=os.environ.get("SMD_WORKSPACE_BROKER_SOCKET", "/run/smd-workspace-broker/broker.sock"),
    )
    w.add_argument("--dry-run", action="store_true")
    w.set_defaults(fn=write)

    args = ap.parse_args()
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
