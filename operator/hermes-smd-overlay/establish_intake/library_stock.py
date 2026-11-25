"""SMD stocks a firm's document library from one of the firm's own filed papers.

WHY (2026-11-24). The only way into a firm's library was a firm
administrator's yes to a "set up our ..." job, so on the one live seat the
library stayed empty and every discovery paper was drafted on SMD's starter.
The library is our product; SMD stocks it. The jobs half
(``jobs/library_setup/stock.py``) makes the library file from the firm's filed
paper and leaves it in a directory with what it made; this, as root, hands it
to the SAME writer a firm's own acceptance uses
(:func:`establish_intake.library_accept.accept_file`): the registry slot check,
the contract check, the no-client-text check on the bytes
(``establish_intake/blank_form.py``), the replaces check, bytes first and the
ledger last, and the wait for the spec applier. Only the administrator's yes is
gone; ``accepted_by`` records who stocked it and from which filed paper:
``smd:from-firm-filing:<source file id>``.

THE REPLACES CHECK. A slot that already holds a file is replaced only with
``--replace-current``, and then only the file read at the start: a change in
between refuses, as it does for a firm's own acceptance.

Usage, as root with the intake's environment::

    python -m establish_intake.library_stock --dir DIR [--replace-current] [--check-only]

Prints one JSON object: the writer's answer, codes and counts only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from establish_intake import library_accept as A
from establish_intake.intake import STATUS_ACCEPTED_PENDING, STATUS_INSTALLED, STATUS_REJECTED
from shared import library_ledger as L
from spec_applier import library as lib

#: ``operator_connector_sdk.library.resolver.SMD_STOCKED``: the resolver reads
#: a file stocked this way from the seat's kept copy alone.
SMD_STOCKED = "smd:from-firm-filing"
MADE = "library-file.docx"
SIDECAR = "library-file.json"
_SIDECAR_KEYS = frozenset(
    {"kind", "attorney", "source_file_id", "source_version", "fixed_strings", "contract"}
)


class StockRefused(ValueError):
    """The directory does not hold a made file this command can stock."""


def read_made(directory: Path) -> tuple[dict[str, Any], bytes]:
    try:
        side = json.loads((directory / SIDECAR).read_text("utf-8"))
        data = (directory / MADE).read_bytes()
    except (OSError, ValueError) as exc:
        raise StockRefused(f"the made file cannot be read ({type(exc).__name__})") from exc
    if not isinstance(side, dict) or not _SIDECAR_KEYS <= set(side):
        raise StockRefused(f"{SIDECAR} must carry {sorted(_SIDECAR_KEYS)}")
    if not isinstance(side["source_file_id"], str) or not side["source_file_id"].strip():
        raise StockRefused("the source file id is empty")
    return side, data


def acceptance(
    side: dict[str, Any], data: bytes, replaces: L.Replaced | None, now: str
) -> A.Acceptance:
    return A.Acceptance(
        kind=str(side["kind"]),
        attorney=side["attorney"]
        if isinstance(side["attorney"], str) and side["attorney"]
        else None,
        file_id=str(side["source_file_id"]),
        version=str(side["source_version"] or "filed"),
        contract=str(side["contract"]),
        accepted_by=f"{SMD_STOCKED}:{side['source_file_id']}",
        accepted_at=now,
        data=data,
        fixed_strings=tuple(str(s) for s in side["fixed_strings"] or ()),
        replaces=replaces,
    )


def current(vault: A.Vault, kind: str, attorney: str | None) -> L.Replaced | None:
    """The slot's accepted file now, as the replaces check names it."""
    raw = lib.get_object(vault.s3_client, vault.bucket, lib.ledger_key(vault.slug))
    if raw is None:
        return None
    entry = L.parse_ledger(raw).entry_for(kind, attorney)
    return L.Replaced(entry.file_id, entry.version, entry.sha256) if entry else None


def stock(
    directory: Path, vault: A.Vault, *, replace_current: bool, check_only: bool, now: str
) -> dict[str, Any]:
    """Check, then (unless ``check_only``) write; the answer as JSON."""
    side, data = read_made(directory)
    draft = acceptance(side, data, None, now)
    problems, fixed_used = A.refusals(draft)
    if problems:
        return {"status": STATUS_REJECTED, "reasons": problems}
    now_held = current(vault, draft.kind, draft.attorney)
    if now_held is not None and not replace_current:
        return {
            "status": STATUS_REJECTED,
            "reasons": [
                "the slot already holds an accepted file; pass --replace-current to replace it"
            ],
            "current": {
                "file_id": now_held.file_id,
                "version": now_held.version,
                "sha256": now_held.sha256,
            },
        }
    if check_only:
        return {
            "status": "checked",
            "reasons": [],
            "fixed_used": len(fixed_used),
            "replaces": bool(now_held),
        }
    out = A.accept_file(acceptance(side, data, now_held, now), vault)
    return out.to_json()


def _vault() -> A.Vault:
    from establish_intake import __main__ as M

    return A.Vault(
        s3_client=M._build_s3_client(),  # noqa: SLF001 (the intake's own R2 wiring, not a copy of it)
        bucket=M._require_env(M._ENV_R2_BUCKET),  # noqa: SLF001
        slug=M._require_env(M._ENV_SLUG),  # noqa: SLF001
        spec_dir=Path(os.environ.get(M._ENV_SPEC_DIR) or M._DEFAULT_SPEC_DIR),  # noqa: SLF001
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m establish_intake.library_stock")
    ap.add_argument(
        "--dir", required=True, type=Path, help="the directory jobs.library_setup.stock wrote"
    )
    ap.add_argument(
        "--replace-current", action="store_true", help="replace the file the slot holds now"
    )
    ap.add_argument("--check-only", action="store_true", help="run every check, write nothing")
    args = ap.parse_args(argv)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        out = stock(
            args.dir,
            _vault(),
            replace_current=args.replace_current,
            check_only=args.check_only,
            now=now,
        )
    except (StockRefused, L.LedgerInvalid) as exc:
        out = {"status": STATUS_REJECTED, "reasons": [str(exc)]}
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0 if out.get("status") in (STATUS_INSTALLED, STATUS_ACCEPTED_PENDING, "checked") else 1


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "MADE",
    "SIDECAR",
    "SMD_STOCKED",
    "StockRefused",
    "acceptance",
    "current",
    "main",
    "read_made",
    "stock",
]
