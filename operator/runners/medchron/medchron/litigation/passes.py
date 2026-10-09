"""The three read stages over every matter, one matter at a time.

ONE MATTER'S FAILURE DOES NOT FAIL THE JOB (2026-10-08 seat rehearsal: one
read hit its call cap and the whole run failed with four matters unread).
A read that raises is recorded ``unread.json`` on that matter with a coded
reason; the stage moves on; the gates refuse the list while any matter is
unread, so the job HOLDS at the end with every other matter read. A limit
(the job's cap, the month's budget) is not one matter's failure: it stops
the run.

Every artifact a resume trusts is per matter (``m/<id>/readN.json``), so a
resume re-reads only what did not finish.
"""

from __future__ import annotations

from typing import Any

from ..limits import LimitHold
from . import fetch as fetch_mod, gates, manifest, parity, read, update, vocab
from .tools import MatterContext, ReadIncomplete, dump

UNREAD = "unread.json"
#: Settlement-scan hits the audit is asked to resolve, newest first.
MAX_HITS = 20


def unread(r: Any, mid: str) -> dict[str, Any] | None:
    return r._mjson(mid, UNREAD)


def _mark_unread(r: Any, mid: str, stage: str, exc: BaseException) -> None:
    code = "read_incomplete" if isinstance(exc, ReadIncomplete) else f"read_error ({type(exc).__name__})"
    dump(r._mfile(mid, UNREAD), {"stage": stage, "reason": f"{code}: {str(exc)[:300]}"})
    r.log(f"[{stage}] a matter could not be read ({code.split(' ')[0]}); continuing with the others")


def _each(r: Any, stage: str, wants: Any, one: Any) -> None:
    prog = r._reading(stage, audit=(stage == "read3"))
    for m in r._matters():
        mid, p = m["id"], r._plan()[m["id"]]
        if not wants(p):
            continue
        prog.step()
        if r._mfile(mid, f"{stage}.json").is_file() or unread(r, mid):
            continue
        try:
            one(m, mid, p)
        except LimitHold:
            raise
        except Exception as exc:  # noqa: BLE001 - one matter's failure is recorded on that matter; the run goes on
            _mark_unread(r, mid, stage, exc)


def read1(r: Any) -> None:
    def one(m: dict[str, Any], mid: str, p: dict[str, Any]) -> None:
        ctx = r._ctx(mid)
        if p.get("mode") == update.MODE:
            prior = manifest.load_prior(r.state, mid) or {}
            res = read.pass1(
                r.doorway, r.firm, ctx, r._header(m), p["read_groups"], r._listing(ctx, mid), r.today,
                extra="\nCURRENT VALUES (on the list now):\n" + read.for_model(prior, ctx), tail=update.TAIL,
            )
            merged, log = update.guard(
                prior, read.merge(prior, res, p["read_groups"]), p["read_groups"], p["trigger_files"], texts(r, mid)
            )
            dump(r._mfile(mid, "read1.json"), {"groups": p["read_groups"], "result": res, "guarded": merged, "update": log})
            return
        res = read.pass1(r.doorway, r.firm, ctx, r._header(m), p["read_groups"], r._listing(ctx, mid), r.today)
        dump(r._mfile(mid, "read1.json"), {"groups": p["read_groups"], "result": res})

    _each(r, "read1", lambda p: bool(p["read_groups"]), one)


def read2(r: Any) -> None:
    def one(m: dict[str, Any], mid: str, p: dict[str, Any]) -> None:
        r1 = r._mjson(mid, "read1.json")
        ctx = r._ctx(mid)
        if p.get("mode") == update.MODE:
            merged = r1["guarded"]
            changed = r1["update"]["changed"]
            if not changed:  # nothing changed: nothing to check, no call
                dump(r._mfile(mid, "read2.json"), {"result": merged, "overturns": [], "verdicts": []})
                return
            got = read.check_pass(
                r.doorway, r.firm, ctx, "verify", r._header(m), merged, r._listing(ctx, mid), r.today,
                update.check_scope(changed), groups=p["read_groups"] or None,
            )
            log = read.apply_verdicts(ctx, merged, got["verdicts"], "verify")
            dump(r._mfile(mid, "read2.json"), {"result": merged, "overturns": log, "verdicts": got["verdicts"]})
            return
        merged = read.merge(manifest.load_prior(r.state, mid), r1["result"], r1["groups"])
        got = read.check_pass(
            r.doorway,
            r.firm,
            ctx,
            "verify",
            r._header(m),
            merged,
            r._listing(ctx, mid),
            r.today,
            "",
            groups=p["read_groups"] or None,
        )
        log = read.apply_verdicts(ctx, merged, got["verdicts"], "verify")
        dump(r._mfile(mid, "read2.json"), {"result": merged, "overturns": log, "verdicts": got["verdicts"]})

    _each(r, "read2", lambda p: bool(p["read_groups"]), one)


def after_read2(r: Any, mid: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    r2 = r._mjson(mid, "read2.json")
    if r2 is not None:
        return r2["result"], r2["overturns"]
    return manifest.load_prior(r.state, mid), []


def texts(r: Any, mid: str) -> dict[str, str]:
    """``{file_id: extracted text}`` for the files this run fetched."""
    d = fetch_mod.matter_dir(r.data, mid) / "txt"
    return {p.stem: p.read_text(encoding="utf-8", errors="replace") for p in d.glob("*.txt")} if d.is_dir() else {}


def settlement_hits(r: Any, ctx: MatterContext, mid: str, result: dict[str, Any]) -> list[str]:
    """The hits the audit resolves: an open matter's settlement-scan hits,
    newest first, at most ``MAX_HITS`` (the gate checks exactly these)."""
    if (result.get("case_status") or {}).get("value") not in vocab.OPEN_STATUSES:
        return []
    hits = [h for h in gates.settlement_hits(texts(r, mid), r.firm) if h in ctx.by_id]
    return sorted(hits, key=lambda h: ctx.by_id[h])[:MAX_HITS]  # doc numbers run newest first


def audit_extra(r: Any, ctx: MatterContext, mid: str, result: dict[str, Any], audit: str) -> tuple[str, list[str]]:
    prior = manifest.load_prior(r.state, mid)
    if audit == "all" or prior is None:
        scope = "AUDIT: every value in the result."
    else:
        changed = [c["path"] for c in parity.compare(prior, result, moved_files=set(), overturns=[], today=r.today)]
        scope = "AUDIT these values (changed since the last list): " + ("; ".join(changed) or "none")
    hits = settlement_hits(r, ctx, mid, result)
    refs = [f"[doc {ctx.by_id[h]}]" for h in hits]
    extra = scope + ("\nSETTLEMENT-SCAN HITS to resolve in settlement_reviewed: " + ", ".join(refs) if refs else "")
    return extra, hits


def read3(r: Any) -> None:
    def one(m: dict[str, Any], mid: str, p: dict[str, Any]) -> None:
        result, log = after_read2(r, mid)
        if result is None:
            return
        ctx = r._ctx(mid)
        extra, hits = audit_extra(r, ctx, mid, result, p["audit"])
        if p.get("mode") == update.MODE:
            hits = [h for h in hits if h in set(p["trigger_files"])]
            extra = "AUDIT: only the settlement-scan hits below." + (
                "\nSETTLEMENT-SCAN HITS to resolve in settlement_reviewed: "
                + ", ".join(f"[doc {ctx.by_id[h]}]" for h in hits)
                if hits
                else "none"
            )
            if not hits:
                extra = "none"
        if extra.endswith("none") and not hits:
            rec = {"result": result, "overturns": log, "settlement_reviewed": [], "hits_asked": []}
            dump(r._mfile(mid, "read3.json"), rec)
            return
        got = read.check_pass(
            r.doorway,
            r.firm,
            ctx,
            "audit",
            r._header(m),
            result,
            r._listing(ctx, mid),
            r.today,
            extra,
            groups=p["read_groups"] or None,
        )
        log = log + read.apply_verdicts(ctx, result, got["verdicts"], "audit")
        reviewed = [
            {"file_id": (ctx.source(x.get("doc")) or {}).get("file_id"), "finding": x.get("finding")}
            for x in got["settlement_reviewed"]
            if isinstance(x, dict)
        ]
        rec = {"result": result, "overturns": log, "settlement_reviewed": reviewed, "hits_asked": hits}
        dump(r._mfile(mid, "read3.json"), rec)

    def wants(p: dict[str, Any]) -> bool:
        return bool(p["read_groups"]) or p["audit"] == "all"

    _each(r, "read3", wants, one)


def unread_matters(r: Any) -> list[dict[str, Any]]:
    out = []
    for m in r._matters():
        u = unread(r, m["id"])
        if u:
            out.append({"number": m["number"], "matter_id": m["id"], **u})
    return out
