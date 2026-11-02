"""The ``find_pages_in_file`` tool and the page-finder CLI (page_finder.py, ADR 0102).

The TOOL (READ, registered through attachment_tools): fetch one PDF on a matter
the way ``stage_matter_files`` does (its own record must name the matter), find
which pages hold what a colleague asked for, and return page ranges with the
evidence each rests on. Only on a seat that authors ``matter_file_delivery``.

The CLI runs the same finder and the same send-side checks on a LOCAL copy, so
an acceptance run in the ops workspace exercises exactly what the seat would
do, without the documents leaving that workspace::

    python -m smokeball_connector.page_finder locate --pdf FILE --what "WORDS" [--all] [--pages 1-120]
    python -m smokeball_connector.page_finder check --pdf FILE [--pages 3-11]
    python -m smokeball_connector.page_finder purge (--matter ID | --all)

``locate`` prints each record asked for with its page ranges and per-page
evidence, then (unless ``--no-check``) the send check of every range it found.
``check`` runs the send check alone: the connector's cut or restage (embedded
files removed), the overlay's structural PDF check, the broker's byte scan,
and the per-file size cap. Run from a checkout of the repo (``pip install -e
operator/connectors/_sdk -e operator/connectors/smokeball``): the overlay and
broker checks are imported from the checkout, and a CLI that cannot find them
fails closed rather than reporting a pass it did not check.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import matter_files as mf
from .page_finder import FinderRefused, locate, parse_window, purge

TOOL_NOTE = (
    "To send what was found, call stage_matter_files with these page ranges. A record this "
    "file does not hold is reported found=false with what the index shows instead; say that, "
    "never guess. complete=false means pages remain unread: call again before saying a record "
    "is not here."
)


def find_pages(client: Any, matter_id: str, file_id: str, what: str, pages: str | None = None) -> dict[str, Any]:
    """The work behind ``find_pages_in_file``."""
    if not mf.load_recipients():
        return {"error": "this seat does not send matter files (matter_file_delivery is not authored)"}
    for label, value in (("matter id", matter_id), ("file id", file_id)):
        if not isinstance(value, str) or not mf._ID_RE.match(value.strip()):
            return {"error": f"{value!r} is not a {label} (a UUID)"}
    matter_id, file_id = matter_id.strip(), file_id.strip()
    try:
        window = parse_window(pages)
        meta = client.get(f"/matters/{matter_id}/documents/files/{file_id}")
        if not mf._on_matter(meta, matter_id):
            raise FinderRefused("the file's own record does not name this matter")
        if str(meta.get("fileExtension") or "").strip().lower().lstrip(".") != "pdf":
            raise FinderRefused("only a PDF's pages can be found")
        work = mf.spool_dir()
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="smd-page-finder-", dir=work) as tmp:
            source = Path(tmp) / "source.pdf"
            mf._download_to(client, matter_id, file_id, source, mf.MAX_SOURCE_BYTES)
            pages_dir = Path(tmp) / "pages"
            pages_dir.mkdir()
            out = locate(source, what, pages_dir, window=window, matter_id=matter_id)
    except (FinderRefused, mf.StageRefused) as exc:
        return {"error": str(exc), "matterId": matter_id, "fileId": file_id}
    except Exception as exc:  # noqa: BLE001 - could not look is never "not there"
        return {"error": f"the pages could not be read ({exc.__class__.__name__}); nothing was found or ruled out"}
    return {"matterId": matter_id, "fileId": file_id, "name": meta.get("name"), **out, "nextStep": TOOL_NOTE}


def register(server: Any) -> None:
    def _client() -> Any:
        from . import server as _server

        return _server._get_client()

    @server.tool()
    def find_pages_in_file(matter_id: str, file_id: str, what: str, pages: str | None = None) -> Any:
        """Find WHICH PAGES of a matter PDF hold the record a colleague asked
        for, including in scanned, image-only files. ``what`` is the request in
        its own words ("the 3/3/2021 clinic visit and the discharge
        summary"); ``pages`` optionally limits the look to a range like
        "1-120". Returns, per record asked for, ``found``, page ranges with the
        evidence printed on those pages (date, provider, document type,
        heading), and a ``documents`` outline of the file. Up to 100 new pages
        are read per call; pages already read are free. Use it when a file is
        too large or refused whole, then stage just those pages."""
        from . import server as _server

        resp = find_pages(_client(), matter_id, file_id, what, pages)
        if resp.get("matterId") and not resp.get("error"):
            _server._attach_matter_ref(_client(), resp, matter_id=resp["matterId"])
        return resp


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _import_send_checks(repo_operator: Path | None) -> tuple[Any, Any]:
    """(overlay check_record_pdf, broker active_outside_streams) from the checkout."""
    base = repo_operator or Path(__file__).resolve().parents[3]
    for path in (base / "hermes-smd-overlay", base):
        if path.is_dir() and str(path) not in sys.path:
            sys.path.insert(0, str(path))
    try:
        from shared.matter_file_reply import check_record_pdf  # type: ignore[import-not-found]
        from workspace_broker.agentmail_attachments import active_outside_streams  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SystemExit(
            f"the overlay and broker checks were not found under {base} ({exc}); run from the repo "
            "checkout or pass --repo <path to operator/>. Nothing was checked."
        ) from exc
    return check_record_pdf, active_outside_streams


def _as_staged(source: Path, pages: tuple[int, int] | None) -> tuple[bytes, int]:
    """The bytes ``stage_matter_files`` would attach, and embedded files removed.

    Uses the connector's own staging step: ``matter_files.prepare_local`` when
    the restage (embedded files stripped) is on this checkout, else the page
    cut / whole-file read staging does today. Never a copy of either."""
    prepare = getattr(mf, "prepare_local", None)
    if callable(prepare):
        blob, removed = prepare(source, pages)
        return blob, int(removed)
    if pages:
        return mf._cut_pages(source, pages), 0
    return source.read_bytes(), 0


def check_cut(pdf: Path, pages: tuple[int, int] | None, checks: tuple[Any, Any]) -> dict[str, Any]:
    """What the seat would do to send ``pages`` of ``pdf`` (or the whole file)."""
    check_record_pdf, active_outside_streams = checks
    label = f"pp. {pages[0]}-{pages[1]}" if pages else "whole file"
    with tempfile.TemporaryDirectory(prefix="smd-page-check-") as tmp:
        source = Path(tmp) / "source.pdf"
        os.symlink(pdf.resolve(), source)
        try:
            blob, removed = _as_staged(source, pages)
            page_count = check_record_pdf(blob)
            if len(blob) > mf.MAX_FILE_BYTES:
                raise ValueError(f"it is {len(blob) / 1048576:.1f} MB, over the per-file cap")
            if active_outside_streams(blob):
                raise ValueError("the mail service's byte scan finds scripts, actions or embedded files")
        except Exception as exc:  # noqa: BLE001 - every refusal is reported by its reason
            return {"range": label, "passes": False, "reason": str(exc)}
    return {"range": label, "passes": True, "pages": page_count, "bytes": len(blob), "embeddedFilesRemoved": removed}


def _print_locate(out: dict[str, Any]) -> None:
    print(
        f"{out['indexedPages']} of {out['pageCount']} pages indexed ({out['readNow']} read now, {out['fromCache']} cached)"
    )
    for key in ("failedPages", "unreadablePages", "matchError", "note"):
        if out.get(key):
            print(f"{key}: {out[key]}")
    for item in out["items"]:
        print(f"\nASKED: {item['asked']}\n  found: {item['found']}  reason: {item['reason']}")
        for r in item["ranges"]:
            print(f"  pages {r['pages']}")
            for ev in r["evidence"]:
                print(
                    f"    p{ev['page']}: {ev['date'] or '-'} | {ev['provider'] or '-'} | {ev['document_type'] or '-'} | {ev['title'] or '-'}"
                )
    tokens = out["tokens"]
    cost = tokens["input_tokens"] * 4 / 1e6 + tokens["output_tokens"] * 20 / 1e6
    print(f"\ntokens: {tokens} (about ${cost:.2f} at Opus 5.5 list price)")


def _cmd_locate(args: argparse.Namespace) -> int:
    pdf = Path(args.pdf)
    checks = None if args.no_check else _import_send_checks(args.repo)
    if not args.index_dir and not os.environ.get("SMOKEBALL_PAGE_INDEX_DIR"):
        os.environ["SMOKEBALL_PAGE_INDEX_DIR"] = str(pdf.resolve().parent / ".page-index")
    window = parse_window(args.pages)
    while True:
        with tempfile.TemporaryDirectory(prefix="smd-page-finder-") as tmp:
            source = Path(tmp) / "source.pdf"
            os.symlink(pdf.resolve(), source)
            (Path(tmp) / "pages").mkdir()
            out = locate(source, args.what, Path(tmp) / "pages", window=window)
        if out["complete"] or not args.all or not out["readNow"]:
            break
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        _print_locate(out)
    if checks:
        print("\nSEND CHECK (what stage_matter_files would do):")
        for item in out["items"]:
            for r in item["ranges"]:
                first, last = (int(x) for x in r["pages"].split("-"))
                print("  " + json.dumps(check_cut(pdf, (first, last), checks)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m smokeball_connector.page_finder")
    sub = parser.add_subparsers(dest="cmd", required=True)
    loc = sub.add_parser("locate", help="find the pages that hold the records asked for")
    loc.add_argument("--pdf", required=True)
    loc.add_argument("--what", required=True, help="the request in its own words")
    loc.add_argument("--pages", help="limit the look to a range like 1-120")
    loc.add_argument("--all", action="store_true", help="keep reading until every page is indexed")
    loc.add_argument("--no-check", action="store_true", help="skip the send check of each range found")
    loc.add_argument("--index-dir", help="where the page index is cached (default: .page-index beside the PDF)")
    loc.add_argument("--repo", type=Path, help="path to the repo's operator/ directory (default: this checkout)")
    loc.add_argument("--json", action="store_true")
    chk = sub.add_parser("check", help="run the send check on a page range or the whole file")
    chk.add_argument("--pdf", required=True)
    chk.add_argument("--pages")
    chk.add_argument("--repo", type=Path)
    pur = sub.add_parser("purge", help="delete cached page indexes")
    group = pur.add_mutually_exclusive_group(required=True)
    group.add_argument("--matter")
    group.add_argument("--all", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "locate":
            if args.index_dir:
                os.environ["SMOKEBALL_PAGE_INDEX_DIR"] = args.index_dir
            return _cmd_locate(args)
        if args.cmd == "check":
            result = check_cut(Path(args.pdf), parse_window(args.pages), _import_send_checks(args.repo))
            print(json.dumps(result))
            return 0 if result["passes"] else 1
        print(f"{purge(matter_id=args.matter, everything=args.all)} cached index(es) removed")
        return 0
    except FinderRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


__all__ = ["check_cut", "find_pages", "main", "register"]
