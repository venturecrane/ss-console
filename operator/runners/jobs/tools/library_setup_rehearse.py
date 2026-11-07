"""A $0 rehearsal of "set up our ..." on a firm's real matter listing: READ ONLY.

What it runs, in order, and what it prints (counts, codes, file ids and part
names only; never a file name, a person's name or a word of any document):

1. The hunt (``jobs.firmbase.hunt``) for the kind over the firm's open and
   pending matters (and closed ones since ``--closed-since``), narrowed to one
   attorney's own files with ``--attorney`` (matched to the roster the job uses,
   ``jobs.library_setup.roster``). Prints the hunt's counts and the candidates
   the job would show the firm: file id, saved-on date, score.
2. On the candidate ``--choose`` (1 = the first shown): the kind's adopt and
   clean (``drafting/brief_base.adopt`` or ``drafting/memo_base.adopt``), then
   the no-client-text check (``establish_intake/blank_form.check``) on the made
   file. Prints the number of problems and the parts they are in.
3. A part-by-part comparison of the made file's look with its source, words set
   aside: which parts differ, whether the page setup is the same, and how many
   of the made file's body elements keep a source element's look exactly.

NOTHING IS WRITTEN to the case system: the client is wrapped so that any call
but ``get`` and ``download_file`` raises, and nothing reaches a library, a
ledger, a vault or a mailbox. The made file is kept in memory only; with
``--keep DIR`` it is written there, for an SMD reader on the ops workspace.
No model is called.

USAGE (from the repo root, in the jobs runner's venv, where the case system's
client can be built: the client ops workspace):

    python operator/runners/jobs/tools/library_setup_rehearse.py --kind memo
    python operator/runners/jobs/tools/library_setup_rehearse.py --kind mediation_brief \\
        --attorney "<a name on the roster>" --inputs <drafting inputs dir> --choose 2

Exit 0 when every step ran (whatever it found), 2 when a step could not run.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve()
OVERLAY = HERE.parents[3] / "hermes-smd-overlay"
READS = frozenset({"get", "download_file"})


class ReadOnly:
    """The case system's client with every write refused."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def __getattr__(self, name: str) -> Any:
        if name not in READS:
            raise PermissionError(f"the rehearsal reads only; {name} is refused")
        return getattr(self._client, name)


def _adopter(kind: str) -> Callable[[bytes, Any], tuple[bytes, list[str]]]:
    from jobs.drafting import brief_base, memo_base

    return {"mediation_brief": brief_base.adopt, "memo": memo_base.adopt}[kind]


def blank_problems(made: bytes) -> dict[str, Any]:
    """The no-client-text check on the made file: how many problems, in which parts."""
    if str(OVERLAY) not in sys.path:
        sys.path.insert(0, str(OVERLAY))
    from establish_intake import blank_form

    result = blank_form.check(made)
    parts = sorted({p.split(":", 1)[0].split(" ", 1)[0] for p in result.problems})
    return {"problems": len(result.problems), "in": parts, "marks": len(result.marks)}


def _strip_words(data: bytes) -> bytes:
    from lxml import etree

    from jobs.firmbase import xmlproof
    from jobs.firmbase.base import W

    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError:
        return data
    for t in list(root.iter(f"{W}t")):
        t.getparent().remove(t)
    return xmlproof.canon(root)


def look_compare(source: bytes, made: bytes) -> dict[str, Any]:
    """Which parts differ once words are set aside, and how many of the made
    file's body elements keep a source element's look exactly."""
    from jobs.firmbase import xmlproof
    from jobs.firmbase.proof import _normal

    a, b = zipfile.ZipFile(io.BytesIO(source)), zipfile.ZipFile(io.BytesIO(made))
    skip = ("docProps/", "_rels/.rels", "word/document.xml")
    differ = sorted(
        n
        for n in set(a.namelist()) & set(b.namelist())
        if not n.startswith(skip)
        and n.endswith((".xml", ".rels"))
        and _strip_words(a.read(n)) != _strip_words(b.read(n))
    )
    xs, ys = xmlproof.body_of(source), xmlproof.body_of(made)
    looks = {xmlproof.canon(_normal(x)) for x in xs[:-1]}
    kept = sum(1 for y in ys[:-1] if xmlproof.canon(_normal(y)) in looks)
    return {
        "parts_only_in_source": sorted(set(a.namelist()) - set(b.namelist())),
        "parts_only_in_made": sorted(set(b.namelist()) - set(a.namelist())),
        "parts_differ_words_aside": differ,
        "page_setup_same": bool(xs and ys and xmlproof.canon(xs[-1]) == xmlproof.canon(ys[-1])),
        "body_elements": len(ys) - 1 if ys else 0,
        "body_elements_keeping_a_source_look": kept,
    }


def rehearse_candidate(kind: str, client: Any, candidate: dict[str, Any], matter_id: str) -> dict[str, Any]:
    """Adopt, clean, check and compare one candidate."""
    from jobs import firmbase
    from jobs.drafting.outcome import DraftingFailed

    blob = client.download_file(matter_id, candidate["file_id"])[1]
    source = firmbase.Source(matter_id, candidate["file_id"], "", hashlib.sha256(blob).hexdigest())
    try:
        made, notes = _adopter(kind)(blob, source)
    except (DraftingFailed, firmbase.FirmBaseUnfit, ValueError, KeyError) as exc:
        return {"adopted": False, "why": type(exc).__name__}
    return {
        "adopted": True,
        "notes": len(notes),
        "blank": blank_problems(made),
        "look": look_compare(blob, made),
        "made": made,
    }


def _roster_attorney(name: str, inputs: str | None, client: Any) -> tuple[Any, str]:
    from jobs.library_setup import job as job_mod, roster

    try:
        found, outcome = roster.pick(name, roster.attorneys(job_mod._inputs(inputs), client))
    except roster.RosterUnusable as exc:
        return None, exc.code
    return found, outcome


def rehearse(args: argparse.Namespace, client: Any) -> dict[str, Any]:
    from jobs.firmbase import hunt as hunt_mod
    from jobs.library_setup import job as job_mod

    client = ReadOnly(client)
    report: dict[str, Any] = {"kind": args.kind, "attorney_named": bool(args.attorney)}
    attorney = None
    if args.attorney:
        attorney, code = _roster_attorney(args.attorney, args.inputs, client)
        if attorney is None:
            return {**report, "stopped": code}
    hunted = hunt_mod.hunt(
        args.kind, client, closed_since=args.closed_since or "", person=attorney.case_system_name if attorney else None
    )
    shown = job_mod.candidates(hunted, client)
    report.update(
        counts=dict(sorted(hunted.counts.items())),
        candidates=[{k: c[k] for k in ("file_id", "saved_on", "score")} for c in shown],
    )
    if not shown or args.choose < 1 or args.choose > len(shown):
        return {**report, "chosen": None}
    pick = shown[args.choose - 1]
    by_id = {c.file_id: c.matter_id for c in hunted.candidates}
    result = rehearse_candidate(args.kind, client, pick, by_id[pick["file_id"]])
    made = result.pop("made", None)
    if made is not None and args.keep:
        Path(args.keep).mkdir(parents=True, exist_ok=True)
        (Path(args.keep) / f"{pick['file_id']}.made.docx").write_bytes(made)
    return {**report, "chosen": pick["file_id"], **result}


def main(argv: list[str] | None = None, client: Any = None) -> int:
    p = argparse.ArgumentParser(prog="library_setup_rehearse", description=(__doc__ or "").split("\n", 1)[0])
    p.add_argument("--kind", required=True, choices=("mediation_brief", "memo"))
    p.add_argument("--attorney", default="", help="one attorney's own files: a name on the roster")
    p.add_argument("--inputs", default=None, help="the drafting firm-inputs dir (the roster's source)")
    p.add_argument("--closed-since", default="", help="also read matters closed on or after this date")
    p.add_argument("--choose", type=int, default=1, help="which shown candidate to adopt (1 = first; 0 = none)")
    p.add_argument("--keep", default="", help="write the made file here (ops workspace only)")
    args = p.parse_args(argv)
    if client is None:
        from smokeball_connector.client import build_client_from_env

        client = build_client_from_env()
    try:
        out = rehearse(args, client)
    except Exception as exc:  # noqa: BLE001 - a rehearsal that could not run says so by type, never with text
        print(json.dumps({"error": type(exc).__name__}))
        return 2
    print(json.dumps(out, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
