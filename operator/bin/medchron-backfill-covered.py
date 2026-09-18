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
    b64=$(base64 < payloads.json)
    fly ssh console -a hermes-<slug> -C "sh -lc 'echo $b64 | base64 -d > /tmp/p.json'"
    fly ssh console -a hermes-<slug> -C "/opt/medchron/.venv/bin/python \
        /opt/medchron/bin/medchron-backfill-covered.py write --payloads /tmp/p.json"

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
* a matter whose delivered document cannot be found on the matter, or whose
  cited-exhibit count does not reconcile with the covered set.
"""

from __future__ import annotations

import argparse
import json
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

#: How a delivered chronology is recognised among a matter's files. Deliberately
#: loose on case and spacing and strict on the two words: a document that is not
#: a chronology must not be mistaken for one, and a matter with no match is
#: refused rather than assumed clean.
CHRONOLOGY_NAME_RE = re.compile(r"medical\s*chronology", re.I)

#: `Exhibit 12` / `Exhibit 12 - p. 4` inside a delivered document.
EXHIBIT_RE = re.compile(r"\bExhibit\s+(\d+)\b")


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
    to be wrong: it moved 94 of modellas' 180 covered documents to uncovered.
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


def _manifest_ids(slug_dir: Path) -> set[str]:
    """Document ids this delivery touched, for the mapping check.

    `manifest.json` is the matter's listing as the run saw it and is the first
    choice. One A&P folder (modellas) has none, so the fallback is the run's own
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
    rule_dates = json.loads(Path(args.rule_dates).read_text(encoding="utf-8")) if args.rule_dates else {}
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
        print(f"OK    {slug:18} covered={len(covered):4} uncovered={len(uncovered):4} units={len(units)}")

    Path(args.out).write_text(json.dumps(out, indent=1, sort_keys=True), encoding="utf-8")
    print(f"\n{len(out)} matter(s) written to {args.out} (document ids only, no content)")
    return 0


def compute_from_document(args: argparse.Namespace) -> int:
    """Robertus 201923 and anything else delivered before the pipeline existed.

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
            "source": "backfill-from-document",
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
    0 of 49 on Robertus and looked exactly like a total mismatch rather than a
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
    ends it. Without this, Price 201588 -- which returns exactly 500 -- would
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


def _delivered_exhibit_count(matter_id: str, files: dict[str, str]) -> int | None:
    """Exhibits cited by the delivered chronology AS FILED on the matter.

    This is the one check whose source the pipeline did not produce. The
    limitations section inside the document is computed from the same run
    artifacts `covered_sets` reads, so reconciling against it would agree by
    construction and measure nothing. The filed document is the artifact the
    client actually received, stored separately, and reading it back is the only
    over-claim detector available at no cost.
    """


    from smokeball_connector.server import get_download_url

    import io
    import zipfile
    from urllib.parse import urlparse

    import httpx

    candidates = [fid for fid, name in files.items() if CHRONOLOGY_NAME_RE.search(name)]
    if not candidates:
        return None

    for fid in candidates:
        try:
            url = get_download_url(matter_id, fid)
            href = url.get("url") if isinstance(url, dict) else str(url)
            # The href comes back from the vendor API, not from us, so the scheme
            # is checked rather than trusted: a `file://` value here would make
            # this function read the seat's own disk instead of the document.
            if urlparse(href).scheme != "https":
                print(f"    (refusing a non-https download url for {fid})")
                continue
            body = httpx.get(href, timeout=60.0, follow_redirects=True)
            body.raise_for_status()
            xml = zipfile.ZipFile(io.BytesIO(body.content)).read("word/document.xml")
            nums = {int(n) for n in EXHIBIT_RE.findall(xml.decode("utf-8", "replace"))}
            if nums:
                return len(nums)
        except Exception as exc:  # noqa: BLE001 - a refusal, never a crash
            print(f"    (could not read filed chronology {fid}: {exc})")
    return None


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
        missing = sorted(real - set(files))
        if missing:
            print(
                f"REFUSE {slug:18} {len(missing)} manifest id(s) are NOT on matter "
                f"{p['matter_number']} -- the mapping is wrong; first: {missing[0]}"
            )
            refused += 1
            continue
        print(
            f"CHECK  {slug:18} {len(real)}/{len(real)} manifest ids present"
            + (f", {synthetic} folded email attachment(s) not id-checked" if synthetic else "")
        )

        cited = _delivered_exhibit_count(p["matter_id"], files)
        covered_real = [i for i in p["covered"] if not i.startswith(SYNTHETIC_PREFIX)]
        if cited is None:
            if not args.allow_unreconciled:
                print(f"REFUSE {slug:18} no filed chronology found on the matter to reconcile against")
                refused += 1
                continue
            print(f"WARN   {slug:18} unreconciled (--allow-unreconciled)")
        elif cited > len(covered_real):
            # The filed document cites more exhibits than we are claiming were
            # covered. Under-claiming is the safe direction (those documents get
            # re-read), so this is a warning. The reverse is not.
            print(f"NOTE   {slug:18} filed document cites {cited} exhibits, covered set has {len(covered_real)}")
        else:
            print(f"CHECK  {slug:18} filed document cites {cited} exhibits <= {len(covered_real)} covered")

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
    d.add_argument("--smokeball-names", required=True, help='JSON {file_id: name} for the matter')
    d.add_argument("--slug", required=True)
    d.add_argument("--matter-id", required=True)
    d.add_argument("--matter-number", required=True)
    d.add_argument("--delivered-at", required=True)
    d.add_argument("--floor", type=float, default=0.80)
    d.add_argument("--out", required=True)
    d.set_defaults(fn=compute_from_document)

    w = sub.add_parser("write", help="seat, as root: verify against Smokeball, then write")
    w.add_argument("--payloads", required=True)
    w.add_argument("--socket", default="/run/smd-audit/broker.sock")
    w.add_argument("--dry-run", action="store_true")
    w.add_argument(
        "--allow-unreconciled",
        action="store_true",
        help="write a matter whose filed chronology cannot be found (states it per matter)",
    )
    w.set_defaults(fn=write)

    args = ap.parse_args()
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
