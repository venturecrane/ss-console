"""A $0 rehearsal of "set up our ..." on a firm's real matter listing: READ ONLY.

What it runs, in order, and what it prints (counts, codes, file ids and part
names only; never a file name, a person's name or a word of any document):

1. The hunt (``jobs.firmbase.hunt``) for the kind over the firm's open and
   pending matters (and closed ones since ``--closed-since``), narrowed to one
   attorney's own files with ``--attorney`` (matched to the roster the job uses,
   ``jobs.library_setup.roster``). Prints the hunt's counts (a candidate the kind's
   plan cannot adopt is not shown and is counted by reason, ``plan_refused_*``)
   and the candidates the job would show the firm: file id, saved-on date, score.
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

``--accept-dry`` runs, on the chosen candidate, exactly the checks the accept job
runs before it files anything (``jobs/library_setup/accept.py``, ``make.py``):
the made file, what its drops removed by category (custom XML parts, template
links, document variables, mail merge, external links, foreign header and
footer fields), its header and footer residue by category (page numbering, firm
identity, refused words, refused digits, note words; counts only), the marked
places proof, and the root writer's no-client-text check with the fixed strings
that remained. It files nothing:

    python operator/runners/jobs/tools/library_setup_rehearse.py --kind mediation_brief \\
        --inputs <drafting inputs dir> --choose 1 --accept-dry

``--edited`` runs piece 4's checks on a firm's saved library file against the
slot's accepted copy (``--accepted``, the seat-kept file), with no hunt and no
case system: the cleaning, the codes a job would refuse it for, the fixed
strings read off the accepted copy, the root writer's check on the cleaned
bytes with them, and the look comparison of the save with what is kept:

    python operator/runners/jobs/tools/library_setup_rehearse.py --kind mediation_brief \\
        --edited <saved.docx> --accepted <accepted copy.docx> --contract marked

``--layout`` runs piece 5's order of layout precedence on the seat's installed
rules (the compiled layout rules file the spec applier installs for work product) and the slot's
accepted file, with the firm's drafting inputs: per layout property, which
source wins (a court rule, a firm rule, the firm's own edit, the authored
layout) and whether the format check holds it on a fictional brief laid into
the file; the rules a later edit replaced, the rules it could not place, and
what a rule would write into the file. With ``--edited``, the firm's later save
of the file is taken as the firm's own edit, later than every rule. Counts, rule
ids and key names only:

    python operator/runners/jobs/tools/library_setup_rehearse.py --kind mediation_brief \\
        --layout <compiled layout rules file> --accepted <accepted brief.docx> --inputs <drafting inputs dir>

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
from typing import Any

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


def blank_problems(made: bytes, fixed: tuple[str, ...] = ()) -> dict[str, Any]:
    """The no-client-text check on the made file: how many problems, in which parts."""
    if str(OVERLAY) not in sys.path:
        sys.path.insert(0, str(OVERLAY))
    from establish_intake import blank_form

    result = blank_form.check(made, fixed)
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
    from jobs.library_setup import plan as plan_mod

    blob = client.download_file(matter_id, candidate["file_id"])[1]
    source = firmbase.Source(matter_id, candidate["file_id"], "", hashlib.sha256(blob).hexdigest())
    try:
        made, notes = plan_mod.adopter(kind)(blob, source)
    except (DraftingFailed, firmbase.FirmBaseUnfit, ValueError, KeyError) as exc:
        return {"adopted": False, "why": type(exc).__name__}
    return {
        "adopted": True,
        "notes": len(notes),
        "blank": blank_problems(made),
        "look": look_compare(blob, made),
        "made": made,
    }


def accept_dry(
    kind: str,
    client: Any,
    candidate: dict[str, Any],
    matter_id: str,
    inputs: str | None,
    donors: list[Any] | None = None,
) -> dict[str, Any]:
    """The accept job's checks on one candidate, writing nothing: adopt and clean,
    custom XML dropped, headers and footers judged against the firm's identity
    (the residue by category, never the words), marked places proven, the root
    writer's no-client-text check with the fixed strings that remained, and the
    look comparison. A memo with no chart takes its tables' look as the accept job
    does (``accept._made``): the hunt's ``chart_donors`` by rank, else its body look,
    and ``chart_source`` names which."""
    from jobs.library_setup import accept as accept_mod, make as make_mod

    blob = client.download_file(matter_id, candidate["file_id"])[1]
    choice = {"file_id": candidate["file_id"], "matter_id": matter_id, "chart_donors": list(donors or [])}
    try:
        made, chart_source = accept_mod._made({"setup_kind": kind}, blob, choice, inputs, None, client)
    except make_mod.Unusable as exc:
        residue = dict(item.rsplit(" ", 1) for item in str(exc).split(": ", 1)[-1].split(", ") if " " in item)
        return {
            "accept_dry": "unusable",
            "code": exc.code,
            "residue": residue if exc.code == "case_words_remain" else {},
        }
    return {
        "accept_dry": "would_file",
        "chart_source": chart_source,
        "residue": made.residue,
        "dropped": made.dropped,
        "fixed_strings": len(made.fixed_strings),
        "blank": blank_problems(made.data, made.fixed_strings),
        "look": look_compare(blob, made.data),
        "made": made.data,
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
    shown = job_mod.candidates(hunted, client, args.kind)
    donors = job_mod.chart_donors(hunted, client, args.kind) if shown else []
    report.update(
        chart_donors=[d["file_id"] for d in donors],
        counts=dict(sorted(hunted.counts.items())),
        dropped_files={k: list(v) for k, v in sorted(hunted.files.items())},
        candidates=[{k: c[k] for k in ("file_id", "saved_on", "score")} for c in shown],
    )
    if not shown or args.choose < 1 or args.choose > len(shown):
        return {**report, "chosen": None}
    pick = shown[args.choose - 1]
    by_id = {c.file_id: c.matter_id for c in hunted.candidates}
    if args.accept_dry:
        result = accept_dry(args.kind, client, pick, by_id[pick["file_id"]], args.inputs, donors)
    else:
        result = rehearse_candidate(args.kind, client, pick, by_id[pick["file_id"]])
    made = result.pop("made", None)
    if made is not None and args.keep:
        Path(args.keep).mkdir(parents=True, exist_ok=True)
        (Path(args.keep) / f"{pick['file_id']}.made.docx").write_bytes(made)
    return {**report, "chosen": pick["file_id"], **result}


def edited_check(kind: str, contract: str, accepted: bytes, edited: bytes) -> dict[str, Any]:
    """Piece 4's checks on a firm's saved library file, exactly as a job runs them
    (``jobs/library_edit/check.py``), and the root writer's check on the cleaned
    bytes with the slot's fixed strings (read off the accepted copy). Codes and
    counts only; nothing is written."""
    from jobs.library_edit import check

    cleaned = check.clean(edited)
    fixed = tuple(sorted(check.fixed_of(accepted)))
    codes = check.problems(kind, contract, accepted, cleaned)
    return {
        "edited": {
            "would_use": not codes,
            "codes": codes,
            "fixed_strings": len(fixed),
            "paragraphs_not_fixed": check.words_changed(cleaned, fixed),
            "cleaned_sha256": hashlib.sha256(cleaned).hexdigest(),
            "root_writer_check": blank_problems(cleaned, fixed),
            "look": look_compare(edited, cleaned),
        }
    }


def layout_check(args: argparse.Namespace) -> dict[str, Any]:
    """Piece 5's order of layout precedence on the seat's installed rules and the
    slot's accepted file (and, with ``--edited``, the firm's later save of it):
    each property's winner and whether the format check holds it. Counts only."""
    from jobs.drafting import layout_rehearse

    edited = Path(args.edited).read_bytes() if args.edited else None
    return layout_rehearse.rehearse(
        args.kind, Path(args.layout).read_bytes(), Path(args.accepted).read_bytes(), args.inputs, edited
    )


def _print(step: Any) -> int:
    try:
        out = step()
    except Exception as exc:  # noqa: BLE001 - a rehearsal that could not run says so by type, never with text
        print(json.dumps({"error": type(exc).__name__}))
        return 2
    print(json.dumps(out, indent=1, sort_keys=True))
    return 0


def main(argv: list[str] | None = None, client: Any = None) -> int:
    p = argparse.ArgumentParser(prog="library_setup_rehearse", description=(__doc__ or "").split("\n", 1)[0])
    p.add_argument("--kind", required=True, choices=("mediation_brief", "memo"))
    p.add_argument("--attorney", default="", help="one attorney's own files: a name on the roster")
    p.add_argument("--inputs", default=None, help="the drafting firm-inputs dir (the roster's source)")
    p.add_argument("--closed-since", default="", help="also read matters closed on or after this date")
    p.add_argument("--choose", type=int, default=1, help="which shown candidate to adopt (1 = first; 0 = none)")
    p.add_argument("--keep", default="", help="write the made file here (ops workspace only)")
    p.add_argument(
        "--accept-dry",
        action="store_true",
        help="run the accept job's checks on the chosen candidate (made file, allow list, proofs); file nothing",
    )
    p.add_argument("--edited", default="", help="a firm's saved library file to run piece 4's checks on (no hunt)")
    p.add_argument("--accepted", default="", help="with --edited: the slot's accepted copy (the seat-kept file)")
    p.add_argument("--contract", default="marked", choices=("marked", "styles"), help="with --edited")
    p.add_argument("--layout", default="", help="the seat's compiled layout rules file: piece 5's order on --accepted")
    args = p.parse_args(argv)
    if args.layout:
        return _print(lambda: layout_check(args))
    if args.edited:
        try:
            out = edited_check(
                args.kind, args.contract, Path(args.accepted).read_bytes(), Path(args.edited).read_bytes()
            )
        except Exception as exc:  # noqa: BLE001 - a rehearsal that could not run says so by type, never with text
            print(json.dumps({"error": type(exc).__name__}))
            return 2
        print(json.dumps(out, indent=1, sort_keys=True))
        return 0
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
