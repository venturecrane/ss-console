"""``python -m jobs firmbase make``: a firm's base made from one of its filed Word
documents, on the workspace, before it is pinned (``format.firm_bases.<kind>``).

::

    python -m jobs firmbase make --kind discovery_set --contract styles \\
        --in filed.docx --out discovery-set-base.docx \\
        [--customer customer.yaml] [--inputs <drafting inputs dir>]

``--contract styles`` only: a marked base is made by the library-setup job,
through its class's plan. The allow list for the headers and footers is the
firm's ``firm_identity`` (``--customer``, else the seat's live customer.yaml)
and the attorney blocks authored in its drafting inputs (``--inputs``).

One JSON line on stdout: counts and reason codes, never a word of the document.
Exit 0 when the base was written; 1 when the document was refused (nothing is
written); 2 when the command itself was wrong.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

CONTRACTS = ("styles",)


def _identity(customer: str | None, inputs: str | None) -> list[str]:
    from smokeball_connector.letterhead import load_firm_identity

    from ..drafting import firm as firm_mod
    from ..library_setup.make import identity_pieces

    blocks = [a.get("block") or [] for a in firm_mod.load(inputs).attorneys] if inputs else []
    return identity_pieces(load_firm_identity(customer), blocks)


def cmd_make(args: argparse.Namespace) -> int:
    from ..drafting.firm import CLASSES
    from ..library_setup.make import Unusable
    from ..library_setup.styles_make import make_styles

    out: dict[str, Any] = {"kind": args.kind, "contract": args.contract}
    if args.kind not in CLASSES or args.contract not in CONTRACTS:
        print(json.dumps({**out, "made": False, "code": "usage"}, sort_keys=True))
        return 2
    src, dest = Path(args.in_path), Path(args.out)
    if not src.is_file():
        print(json.dumps({**out, "made": False, "code": "input_missing"}, sort_keys=True))
        return 2
    try:
        made = make_styles(args.kind, src.read_bytes(), _identity(args.customer, args.inputs))
    except Unusable as exc:
        detail = str(exc)[len(exc.code) + 2 :] if str(exc) != exc.code else ""
        print(json.dumps({**out, "made": False, "code": exc.code, "detail": detail}, sort_keys=True))
        return 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(f".{dest.name}.tmp")
    part.write_bytes(made.data)
    part.replace(dest)
    out.update(
        made=True,
        sha256=hashlib.sha256(made.data).hexdigest(),
        residue=made.residue,
        dropped=made.dropped,
        running_title_lines=made.running_title_lines,
    )
    print(json.dumps(out, sort_keys=True))
    return 0


def add_parser(sub: Any) -> None:
    p = sub.add_parser("firmbase", help="make a firm's base from one of its filed Word documents")
    fsub = p.add_subparsers(dest="firmbase_cmd", required=True)
    m = fsub.add_parser("make", help="a styles base: the firm's page setup and styles, the body cleared")
    m.add_argument("--kind", required=True, help="the document class (drafting/firm.py CLASSES)")
    m.add_argument("--contract", required=True, choices=CONTRACTS)
    m.add_argument("--in", dest="in_path", required=True, help="the firm's filed .docx")
    m.add_argument("--out", required=True, help="where the base is written (only when it passes)")
    m.add_argument("--customer", default=None, help="customer.yaml with firm_identity (default: the seat's)")
    m.add_argument("--inputs", default=None, help="the drafting inputs dir, for its authored attorney blocks")
    m.set_defaults(fn=cmd_make)


__all__ = ["add_parser", "cmd_make"]
