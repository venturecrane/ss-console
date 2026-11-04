"""`jobs records --rehearse <dir>`: the records lane's reply, built from
copies of a seat's page indexes, under a dollar cap, reading no page.

WHY (ADR 0102 amendment 4, decision 2). A records reply is code from the
finder's results; what can still go wrong is the match (which sections answer
which record) and how the results become words. Both can be checked against an
answer key without buying a single page again: the indexes already hold every
page the seat read. This runs the job's own assembly (``assemble.py``) on
those copies, so what it checks is what a live job would send.

The directory::

    records.json    [{n, asked, hints?, files: [sha256, ...]}]  the records, in
                    the colleague's words, and the candidate files (by sha256)
                    in the order the job would try them
    index/          <sha256>.json: copies of the seat's page indexes
    files/          <sha256>.pdf (the file, for the cuts' hashes) or
                    <sha256>.pagecount (its page count, when the index lacks one)
    names.json      optional {sha256: the file's name on the matter}
    matter.json     optional {"matter_id": ...} (else the indexes' own matter)
    expected.json   {text?, html?, items?}: the answer key

What it guarantees: NO PAGE IS READ (a page request raises and fails the
rehearsal; an index that does not cover every page settles nothing and is
reported unsettled); the match may be asked, through the same cap hook a job
uses, at most ``--cap`` dollars. A match answered once is cached in the copied
index, so a second run makes no request and must print the same bytes.

Exit 0 when the reply matches the key and no page was read; 1 otherwise.
Output: ``out/reply.txt``, ``out/reply.html``, ``out/report.json``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from smokeball_connector import matter_files as mf, page_finder as pf

from .. import budget as budget_mod, limits as limits_mod
from ..ledger import Ledger
from . import assemble


class PageRead(pf.CallRefused):
    """A page request in a rehearsal: never allowed."""


class _Hook:
    def __init__(self, limits: limits_mod.Limits, budget: budget_mod.Budget, ledger: Ledger, pricing: Any) -> None:
        self.limits, self.budget, self.ledger, self.pricing = limits, budget, ledger, pricing

    def before(self, model: str, est_tokens: int) -> None:
        self.pricing.rate_for(model)
        self.limits.check_each_call(self.budget.refresh(), "rehearse")

    def after(self, model: str, usage: dict[str, int]) -> None:
        tokens = SimpleNamespace(
            input_tokens=int(usage.get("input_tokens") or 0), output_tokens=int(usage.get("output_tokens") or 0)
        )
        self.ledger.record("rehearse", model, tokens, effort="low", cache=False, batch=False)


def _pricing_path(explicit: str | None) -> Path:
    for p in (explicit, os.environ.get(budget_mod.PRICING_ENV), budget_mod.PRICING_DEFAULT):
        if p and Path(p).is_file():
            return Path(p)
    repo = Path(__file__).resolve().parents[4] / "adapter" / "cost_telemetry" / "anthropic_pricing.json"
    return repo


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _prepare_indexes(d: Path) -> dict[str, dict[str, Any]]:
    """Each index copy, with its page count filled from ``files/<sha>.pagecount``
    when it carries none. Returns {sha: index as on disk}."""
    out = {}
    for path in sorted((d / "index").glob("*.json")):
        data = _read(path, {})
        sha = str(data.get("sha256") or path.stem)
        if not isinstance(data.get("page_count"), int):
            count = (
                (d / "files" / f"{sha}.pagecount").read_text(encoding="utf-8").strip()
                if (d / "files" / f"{sha}.pagecount").is_file()
                else ""
            )
            if count.isdigit():
                data["page_count"] = int(count)
                path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        out[sha] = data
    return out


@contextlib.contextmanager
def _env(indexes: dict[str, dict[str, Any]]) -> Any:
    """The finder's model and view as the indexes were made under, unless set:
    an index made under another model or view is not reused."""
    first = next(iter(indexes.values()), {})
    saved = {k: os.environ.get(k) for k in (pf.MODEL_ENV, pf.VIEW_ENV)}
    if not saved[pf.MODEL_ENV] and first.get("model"):
        os.environ[pf.MODEL_ENV] = str(first["model"])
    if not saved[pf.VIEW_ENV] and first.get("view") == "bands":
        os.environ[pf.VIEW_ENV] = "bands"
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextlib.contextmanager
def _no_page_reads(counter: list[int]) -> Any:
    real = pf.index_batch

    def refuse(pages: Any) -> Any:
        counter[0] += 1
        raise PageRead("rehearsal: a page read was attempted")

    pf.index_batch = refuse
    try:
        yield
    finally:
        pf.index_batch = real


def _locate(
    d: Path, records: list[dict[str, Any]], names: dict[str, str], mid: str, hook: _Hook
) -> tuple[list[Any], set[int], list[int]]:
    located: list[tuple[int, str, dict[str, Any]]] = []
    unresolved: set[int] = set()
    unsettled: list[int] = []
    for rec in records:
        n = int(rec["n"])
        shas = [str(s) for s in rec.get("files") or []]
        if not shas:
            unresolved.add(n)
        for sha in shas:
            try:
                out = pf.locate_cached(sha, str(rec["asked"]), base=d / "index", hook=hook)
            except pf.FinderRefused:
                unsettled.append(n)
                break
            out["items"] = [{k: v for k, v in it.items() if not k.startswith("_")} for it in out.get("items") or []]
            out.update(matterId=mid, fileId=sha, name=names.get(sha) or sha[:12])
            located.append((n, sha, out))
            if assemble.found(out):
                break
    return located, unresolved, unsettled


def _cuts(d: Path, results: dict[str, Any], owners: dict[Any, int], names: dict[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    taken: set[str] = set()
    counter: dict[int, int] = {}
    for fid, result in results.items():
        for pages, n in assemble.ranges_to_cut(result, fid, owners):
            counter[n] = k = counter.get(n, 0) + 1
            span = mf.parse_pages(pages) if pages else None
            pdf = d / "files" / f"{fid}.pdf"
            sha = ""
            if pdf.is_file():
                with tempfile.TemporaryDirectory() as tmp:
                    src = Path(tmp) / "source.pdf"
                    src.write_bytes(pdf.read_bytes())
                    blob, _ = mf.prepare_local(src, span)
                sha = hashlib.sha256(blob).hexdigest()
            rows.append(
                {
                    "n": n,
                    "k": k,
                    "file_id": fid,
                    "pages": pages,
                    "page_count": span[1] - span[0] + 1 if span else int(result.get("pageCount") or 0),
                    "sha256": sha,
                    "size": 0,
                    "name": mf.attachment_name(names.get(fid) or fid[:12], span, taken),
                }
            )
    return rows


def rehearse(
    d: Path, *, pricing: str | None = None, cap_usd: float = 2.0, log: Callable[[str], None] = print
) -> tuple[str, int]:
    """(the report JSON, exit code)."""
    records = _read(d / "records.json", [])
    names = _read(d / "names.json", {})
    indexes = _prepare_indexes(d)
    first_matters = next((i.get("matters") for i in indexes.values() if i.get("matters")), None) or ["rehearsal"]
    mid = str(_read(d / "matter.json", {}).get("matter_id") or first_matters[0]).lower()
    out_dir = d / "out"
    out_dir.mkdir(exist_ok=True)
    ledger_path = out_dir / "usage-ledger.jsonl"
    prices = budget_mod.Pricing.load(_pricing_path(pricing))
    budget = budget_mod.Budget(prices, cap_usd, [ledger_path], 1.0)
    limits = limits_mod.Limits(
        cap_usd=cap_usd, monthly_budget_usd=cap_usd, usd_per_scanned_page=0.0, usd_per_audit_claim=0.0
    )
    hook = _Hook(limits, budget, Ledger(ledger_path), prices)
    reads = [0]
    stopped = ""
    with _env(indexes), _no_page_reads(reads):
        try:
            located, unresolved, unsettled = _locate(d, records, names, mid, hook)
        except (limits_mod.LimitHold, pf.CallRefused, budget_mod.BudgetError) as exc:
            located, unresolved, unsettled, stopped = [], set(), [], str(exc)
    results, owners = assemble.merge(located)
    cuts = _cuts(d, results, owners, names)
    files = [{k: c[k] for k in ("file_id", "name", "pages", "page_count", "sha256", "size")} for c in cuts]
    rendered = assemble.render(results, files, mid) if results else None
    per_n: dict[int, int] = {}
    for c in cuts:
        per_n[int(c["n"])] = per_n.get(int(c["n"]), 0) + 1
    hold = rendered.hold_reason if rendered is not None else None
    items = assemble.outcomes(records, rendered.items if rendered and not hold else [], owners, unresolved, per_n)
    block_t = rendered.text if rendered and not hold else ""
    block_h = rendered.html if rendered and not hold else ""
    text, html = assemble.compose(block_t, block_h, held=bool(hold or unsettled or stopped))
    (out_dir / "reply.txt").write_text(text, encoding="utf-8")
    (out_dir / "reply.html").write_text(html, encoding="utf-8")
    expected = _read(d / "expected.json", {})
    got = {"text": text, "html": html, "items": items}
    diff = [k for k in ("text", "html", "items") if k in expected and expected[k] != got[k]]
    report = {
        "text": text,
        "html": html,
        "items": items,
        "cuts": [{k: c[k] for k in ("n", "k", "file_id", "pages", "page_count", "sha256")} for c in cuts],
        "page_reads_attempted": reads[0],
        "unsettled": unsettled,
        "hold_reason": hold,
        "stopped": stopped,
        "cents": int(round(budget.refresh() * 100)),
        "differs_from_expected": diff,
    }
    body = json.dumps(report, indent=1, sort_keys=True)
    (out_dir / "report.json").write_text(body, encoding="utf-8")
    ok = not diff and not reads[0] and not stopped and bool(expected)
    log(
        f"rehearsal: {'PASS' if ok else 'FAIL'}; page reads {reads[0]}; differs: {diff or 'none'}; cents {report['cents']}"
    )
    return body, 0 if ok else 1


__all__ = ["PageRead", "rehearse"]
