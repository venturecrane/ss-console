"""``find_pages`` and the page-finder CLI (page_finder.py, ADR 0102).

``find_pages`` fetches one PDF on a matter (its own record must name the
matter), finds which pages hold what a colleague asked for, and returns page
ranges with the evidence each rests on. Only on a seat that authors
``matter_file_delivery``. It was the in-turn tool ``find_pages_in_file`` until
ADR 0102 amendment 4 (2026-11-03): a turn cannot stop its own spend, so the
tool is no longer registered on any seat and the records lane calls
``page_finder.locate`` under a cost cap instead. The function stays importable.

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
from .page_finder import FinderRefused, build_index, locate, parse_window, purge

TOOL_NOTE = (
    "If a record found here also exists on the matter as its OWN file (find_files_on_matter by "
    "its name, provider or date), send that file rather than these pages cut from a larger set. "
    "Otherwise, to send what was found, call stage_matter_files with these page ranges. A record this "
    "file does not hold is reported found=false with what the index shows instead; say that, "
    "never guess. complete=false means pages remain unread: call again before saying a record "
    "is not here. The seat writes the attached files, their pages and the page counts into your "
    "reply from this result: do not retype pages or counts yourself."
)


def _for_the_model(out: dict[str, Any]) -> dict[str, Any]:
    """The result without its internal keys (``_modelReason``: the match model's
    own sentence for an item that is not established, which is never shown)."""
    out["items"] = [{k: v for k, v in item.items() if not k.startswith("_")} for item in out.get("items") or []]
    return out


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
            out = _for_the_model(locate(source, what, pages_dir, window=window, matter_id=matter_id))
    except (FinderRefused, mf.StageRefused) as exc:
        return {"error": str(exc), "matterId": matter_id, "fileId": file_id}
    except Exception as exc:  # noqa: BLE001 - could not look is never "not there"
        return {"error": f"the pages could not be read ({exc.__class__.__name__}); nothing was found or ruled out"}
    return {"matterId": matter_id, "fileId": file_id, "name": meta.get("name"), **out, "nextStep": TOOL_NOTE}


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

    The connector's own staging step (``matter_files.prepare_local``: the page
    cut, or the whole file restaged with embedded files stripped), never a copy."""
    blob, removed = mf.prepare_local(source, pages)
    return blob, int(removed)


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


def _cost(tokens: dict[str, int]) -> str:
    dollars = tokens.get("input_tokens", 0) * 4 / 1e6 + tokens.get("output_tokens", 0) * 20 / 1e6
    return f"{tokens} (about ${dollars:.2f} at Opus 5.5 list price)"


def _print_locate(out: dict[str, Any], run_tokens: dict[str, int]) -> None:
    print(
        f"{out['indexedPages']} of {out['pageCount']} pages indexed, {out['remainingPages']} not read yet "
        f"(view: {out.get('view')}; match from cache: {out.get('matchFromCache')})"
    )
    print(f"counts: {out.get('counts')}")
    for key in ("matchError", "note"):
        if out.get(key):
            print(f"{key}: {out[key]}")
    for key in ("unreadablePages", "failedPages"):
        for row in out.get(key) or []:
            print(f"{key[:-5]}: p{row['page']}: {row['reason']}")
    for item in out["items"]:
        print(f"\nASKED: {item['asked']}\n  found: {item['found']}  reason: {item['reason']}")
        for key in ("closest", "relatedCandidates"):
            for ptr in item.get(key) or []:
                cells = f"{ptr.get('date') or '-'} | {ptr.get('heading') or '-'}"
                print(f"  {key}: {ptr['section']} pp. {ptr['pages']}: {cells}")
        for r in item["ranges"]:
            print(f"  pages {r['pages']}")
            for ev in r["evidence"]:
                cells = [ev.get(k) or "-" for k in ("date", "provider", "document_type", "heading")]
                print(f"    {ev['section']} pp. {ev['pages']}: " + " | ".join(cells))
                if ev.get("dates"):
                    print(f"      dates printed: {'; '.join(ev['dates'])}")
                if ev.get("unreadablePages"):
                    print(f"      unreadable pages inside it: {', '.join(ev['unreadablePages'])}")
    if out.get("documents"):
        print("\nSECTIONS OF THE FILE (PDF positions):")
        for sec in out["documents"]:
            cells = [sec.get(k) or "-" for k in ("date", "provider", "document_type", "heading")]
            print(f"  {sec['section']} pp. {sec['pages']}: " + " | ".join(cells))
    print(f"\ntokens this run: {_cost(run_tokens)}")
    print(f"tokens for this file so far: {_cost(out.get('tokensForThisFile') or {})}")


def _staged_copy(pdf: Path, tmp: str) -> Path:
    source = Path(tmp) / "source.pdf"
    os.symlink(pdf.resolve(), source)
    (Path(tmp) / "pages").mkdir()
    return source


def _cmd_locate(args: argparse.Namespace) -> int:
    pdf = Path(args.pdf)
    checks = None if args.no_check else _import_send_checks(args.repo)
    if not args.index_dir and not os.environ.get("SMOKEBALL_PAGE_INDEX_DIR"):
        os.environ["SMOKEBALL_PAGE_INDEX_DIR"] = str(pdf.resolve().parent / ".page-index")
    if args.strips:
        os.environ["SMOKEBALL_PAGE_FINDER_VIEW"] = "bands"
    window = parse_window(args.pages)
    run_tokens = {"input_tokens": 0, "output_tokens": 0}
    # --all: read every page first (each call reads up to PAGES_PER_CALL new
    # ones), then match ONCE. Stops when nothing is left or a call made no
    # progress, and says which.
    while args.all:
        with tempfile.TemporaryDirectory(prefix="smd-page-finder-") as tmp:
            built = build_index(_staged_copy(pdf, tmp), Path(tmp) / "pages", window=window)
        for key, value in built["tokens"].items():
            run_tokens[key] += value
        index = built["index"]
        left = int(index.get("page_count") or 0) - len(index["pages"]) - len(index["unreadable"])
        progress = len(built["read_now"]) + len(built["unreadable_now"])
        print(
            f"read {len(built['read_now'])} pages, {len(built['unreadable_now'])} unreadable, {left} left",
            file=sys.stderr,
        )
        if left <= 0 or window:
            break
        if not progress:
            print(
                f"stopped: a call read no new page ({len(built['failed'])} failed); re-run to retry them",
                file=sys.stderr,
            )
            break
    with tempfile.TemporaryDirectory(prefix="smd-page-finder-") as tmp:
        out = locate(_staged_copy(pdf, tmp), args.what, Path(tmp) / "pages", window=window)
    for key, value in out["tokens"].items():
        run_tokens[key] += value
    out["tokensThisRun"] = run_tokens
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        _print_locate(out, run_tokens)
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
    loc.add_argument("--strips", action="store_true", help="show only header/footer strips (default: whole pages)")
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


__all__ = ["check_cut", "find_pages", "main"]
