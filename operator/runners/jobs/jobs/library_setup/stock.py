"""``jobs library-stock``: SMD makes a firm's discovery library file from one of
the firm's own filed papers, for the root writer to stock (the jobs half of
``establish_intake/library_stock.py``).

Run in the client's ops workspace by SMD, with a finished paper the firm filed
(downloaded from the matter's file; its file id is the provenance)::

    jobs library-stock --kind discovery_response --docx PAPER.docx \\
        --source-file-id <file id> --out DIR [--source-version V] [--attorney KEY]

It refuses a paper the Operator wrote (``firmbase.adopt.operator_authored``: our
words must never become the firm's standard), makes the file with
``frame_make.make_frame`` (the firm's styles and its standard wording, the case
facts taken out), and runs the root writer's own no-client-text check on the
result (``establish_intake/blank_form.py``) before it writes anything. It writes
``DIR/library-file.docx`` and ``DIR/library-file.json`` and prints counts and
codes, never a word of the paper. A paragraph of the paper that kept a possible
case fact is dropped; the file is refused only when nothing of its front was
kept (``--allow-partial`` overrides). A frame whose closing came out empty is
stocked with its front alone (``closing: none``): drafts keep their own closing.

A DEMAND LETTER (``--kind demand_letter.pre_suit`` or ``.litigation``) is
stocked from the firm's tagged house reference, its own demand with each
paragraph kind tagged ``PROTO:<kind>`` (``demand_make``): every prototype keeps
its formatting and only its tag, the letterhead and footer keep the firm's
fixed wording, and the file must carry every kind the ``--layout`` writer
needs. Its contract is ``prototypes``; the root writer judges its body to hold
the tags and nothing else.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import accept as accept_mod
from .frame_make import make_frame
from ..drafting.firm import ENV_DIR as INPUTS_ENV
from .make import Unusable

MADE, SIDECAR = "library-file.docx", "library-file.json"


DEMAND_KINDS = ("demand_letter.pre_suit", "demand_letter.litigation")


def demand_made(blob: bytes, layout: str, identity: list[str]) -> tuple[Any, dict[str, Any]]:
    """A demand library file (``demand_make``) and its answer, the root
    writer's own check run on it where the overlay can be imported."""
    from .demand_make import make_prototypes

    try:
        made = make_prototypes(blob, layout, identity)
    except Unusable as exc:
        return None, {"status": "refused", "code": exc.code, "detail": str(exc)}
    answer = {"status": "made", "contract": "prototypes", "kinds": len(made.kinds), "counts": made.counts}
    try:
        from establish_intake import blank_form
    except ImportError:
        return made, {**answer, "check": "root_writer"}
    tags = [s for s in made.fixed_strings if s.startswith("PROTO:")]
    checked = blank_form.check(made.data, made.fixed_strings, document_fixed=tags)
    if not checked.ok:
        return None, {"status": "refused", "code": "no_client_text_check", "problems": len(checked.problems)}
    return made, {**answer, "check": "passed"}


def made_for(
    kind: str, blob: bytes, identity: list[str], blocks: list[list[str]], allow_partial: bool = False
) -> tuple[Any, dict[str, Any]]:
    """(the made file or None, the answer). Every check before anything is
    written; the root writer runs the no-client-text check again on the bytes
    it is handed, so where the jobs runner cannot import it (a seat's jobs
    environment holds no overlay) the answer says it is left to the writer."""
    from ..firmbase.adopt import operator_authored

    if operator_authored(blob):
        return None, {"status": "refused", "code": "operator_authored"}
    try:
        made = make_frame(kind, blob, identity, blocks)
    except Unusable as exc:
        return None, {"status": "refused", "code": exc.code}
    if not made.counts.get("kept_front", 0) and not allow_partial:
        return None, {"status": "refused", "code": "standard_wording_not_kept", "counts": made.counts}
    answer = {
        "status": "made",
        "set_type": made.set_type,
        "counts": made.counts,
        # A frame with no closing: drafts keep their own (the roster's signature block).
        "closing": "kept" if made.counts.get("kept_closing", 0) else "none",
    }
    try:
        from establish_intake import blank_form
    except ImportError:
        return made, {**answer, "check": "root_writer"}
    checked = blank_form.check(made.data, made.fixed_strings)
    if not checked.ok:
        return None, {"status": "refused", "code": "no_client_text_check", "problems": len(checked.problems)}
    return made, {**answer, "check": "passed"}


def write(
    out: Path, made: Any, kind: str, source_file_id: str, version: str, attorney: str | None, contract: str = "styles"
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / MADE).write_bytes(made.data)
    side = {
        "kind": kind,
        "attorney": attorney,
        "source_file_id": source_file_id,
        "source_version": version,
        "contract": contract,
        "fixed_strings": list(made.fixed_strings),
        "set_type": made.set_type,
        "counts": made.counts,
    }
    (out / SIDECAR).write_text(json.dumps(side, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def cmd(args: argparse.Namespace) -> int:
    inputs = args.inputs or os.environ.get(INPUTS_ENV) or None
    with contextlib.redirect_stdout(sys.stderr):
        blocks = accept_mod.attorney_blocks(inputs)
        identity = accept_mod.identity(inputs)
        blob = Path(args.docx).read_bytes()
        if args.kind in DEMAND_KINDS:
            made, answer = demand_made(blob, args.layout, identity)
            contract = "prototypes"
        else:
            made, answer = made_for(args.kind, blob, identity, blocks, args.allow_partial)
            contract = "styles"
        if made is not None:
            write(Path(args.out), made, args.kind, args.source_file_id, args.source_version, args.attorney, contract)
            answer["wrote"] = str(Path(args.out))
    print(json.dumps(answer, indent=2, sort_keys=True))
    return 0 if made is not None else 1


def add_parser(sub: Any) -> None:
    p = sub.add_parser("library-stock", help="make a firm's discovery or demand library file from its own papers")
    p.add_argument("--kind", required=True, choices=("discovery_response", "discovery_set", *DEMAND_KINDS))
    p.add_argument(
        "--layout", default="house", choices=("house", "numbered", "crabtree"), help="a demand's writer layout"
    )
    p.add_argument("--docx", required=True, help="the firm's filed paper, a .docx from the matter's file")
    p.add_argument("--source-file-id", required=True, help="the paper's file id in the case system")
    p.add_argument("--source-version", default="filed")
    p.add_argument("--attorney", default=None, help="an attorney key for that attorney's own slot; none for the firm")
    p.add_argument("--out", required=True, help="the directory to write the made file into")
    p.add_argument(
        "--inputs", default=None, help="the drafting firm-inputs dir (default: the drafting lane's inputs env var)"
    )
    p.add_argument("--allow-partial", action="store_true", help="stock even when one block kept a case fact")
    p.set_defaults(fn=cmd)


__all__ = ["DEMAND_KINDS", "MADE", "SIDECAR", "add_parser", "cmd", "demand_made", "made_for", "write"]
