"""The replay's calls into the response path's own stages, at their real signatures.

Each function here runs one stage of the lane on a saved matter the way the
lane runs it (``served_plan._find_served``, ``response_cost.scope_and_estimate``,
``forms.read_stage``), with three differences that make it a $0 replay:

* every model call goes through the recorded doorway (``recorded.py``), and
  the finder's spend and next-call cost are 0, so its cap never trips;
* every file a stage writes lands under the replay's ``work`` directory: the
  finder's job dir is ``work``, and the scope step runs on ``work/data``, a
  copy of the saved data directory's own records (``*.json``, ``*.jsonl``;
  the extracted text they name is read where it lies, never written);
* the shapes the comparison reads (``compare.categories``, ``packs``,
  ``cost``) are built here from what the real stages return.

The functions are looked up through ``stages.entry`` at call time, so a test
can stand one in with ``monkeypatch.setattr`` on the real module.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from . import recorded, stages as S

#: The finder's model in a replay: the request sha ignores the model, so any name answers.
REPLAY_MODEL = "replay"
FORM = "form_interrogatories"


def request_text(matter: S.Matter) -> str:
    """The attorney's request on the saved job (``job.json``), else "" (every unanswered set)."""
    job = S.read_json(matter.job_dir / "job.json")
    return str(job.get("request_text") or "") if isinstance(job, dict) else ""


def find(matter: S.Matter, work: Path, doorway: Any, firm_data: dict[str, Any] | None = None) -> None:
    """``served_find.find`` on the saved matter: ``work/served.json`` and ``work/found-report.json``."""
    from .. import provenance, served as served_mod, served_find as sf

    fn = S.entry("find")
    replies: dict[str, str] = {}

    def ask(text: str) -> str:
        if text not in replies:
            messages: list[dict[str, Any]] = [{"role": "user", "content": text}]
            replies[text] = doorway.call(
                sf.STAGE, model=REPLAY_MODEL, system=sf.SYSTEM, messages=messages, max_tokens=sf.MAX_TOKENS, effort=""
            ).text
        return replies[text]

    request, clients = request_text(matter), served_mod.clients_of(matter.data)
    # The firm's authored attorney blocks, as the seat reads them (no seat roster on a replay).
    firm = provenance.our_firm((firm_data or {}).get("attorneys") or [])
    # The code half first, every read it would make asked of the recordings:
    # each one without a recording is listed, not just the first, and a stage
    # with any missing stops before the finder runs (``--no-model``).
    missing: list[recorded.NeedsModelCall] = []
    for text in sf.planned_reads(matter.data, request, clients, firm):
        try:
            ask(text)
        except recorded.NeedsModelCall as need:
            missing.append(need)
    if missing:
        raise recorded.NeedsModelCall(missing[0].stage, missing[0].sha, more=[m.sha for m in missing[1:]])
    fn(
        matter.data,
        work,
        request,
        sf.Reader(ask, lambda: 0.0, lambda _chars: 0.0),
        clients=clients,
        other_side=sf.other_side(matter.data),
        firm=firm,
    )


def work_data(matter: S.Matter, work: Path) -> Path:
    """``work/data``: the saved data directory's own records, copied fresh."""
    out = work / "data"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for p in matter.data.iterdir():
        if p.is_file() and p.suffix in (".json", ".jsonl"):
            shutil.copy2(p, out / p.name)
    return out


def plan_of(served_doc: Any, base: Path) -> list[dict[str, Any]]:
    """The unit plan the lane freezes from these served sets (``served.plan_units``)."""
    from .. import served as served_mod

    raw = json.dumps(served_doc).encode("utf-8")
    return served_mod.plan_units(served_mod.parse(raw, base))


def _categories(rows: list[dict[str, Any]], files: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    from .. import response_scope as rs

    by_id = {str(r.get("id")): r for r in rows}
    out: dict[str, dict[str, int]] = {}
    for f in files:
        c = out.setdefault(str(f["category"]), {"files": 0, "pages": 0, "scan_pages": 0, "chars": 0})
        r = by_id.get(str(f.get("id"))) or {}
        c["files"] += 1
        c["pages"] += int(f.get("pages") or 0)
        c["scan_pages"] += len(rs.scan_pages(r))
        c["chars"] += int(r.get("chars") or 0)
    return out


def _packs(data: Path, plan: list[dict[str, Any]], providers: list[str]) -> dict[str, dict[str, Any]]:
    from .. import response_scope as rs

    out: dict[str, dict[str, Any]] = {}
    for p in plan:
        st = str(p.get("variant"))
        for g in rs.FORM_GROUPS if st == FORM else (None,):
            got = rs.pack(data, st, g, providers=providers)
            out[f"{p['key']} {st}" + (f" {g}" if g else "")] = {
                "chars": got.chars,
                "cap": got.cap,
                "left_out": got.pages_left_out,
            }
    return out


def scope(matter: S.Matter, work: Path, served_doc: Any, base: Path, firm_data: dict[str, Any] | None) -> dict:
    """``response_scope.scope`` on a copy of the saved data, and each unit's pack."""
    from .. import response_scope as rs

    fn = S.entry("scope")
    data = work_data(matter, work)
    plan = plan_of(served_doc, base)
    facts = S.read_json(data / "facts.json") or {}
    providers = rs.providers_of(facts)
    before = rs.corpus_rows(data)
    rec = fn(data, firm_data, plan, providers)
    return {
        "categories": _categories(before, rec.get("files") or []),
        "packs": _packs(data, plan, providers),
        "plan": plan,
        "record": rec,
    }


def cost(work: Path, scoped: dict[str, Any], served_doc: Any, firm_data: dict[str, Any], pricing: Any) -> dict:
    """``response_cost.project`` over the scoped copy: dollars, minutes and each line."""
    from .. import response_cost as rc, response_scope as rs

    fn = S.entry("cost")
    data = work / "data"
    plan = scoped["plan"]
    sets = served_doc.get("sets") or [] if isinstance(served_doc, dict) else []
    counts = {u["key"]: int(sets[u["served"]]["request_count"]) for u in plan if isinstance(u.get("served"), int)}
    rows = rs.corpus_rows(data)
    # The pages the seat's page store already holds, read read-only as the lane
    # reads them (``response_cost.scope_and_estimate``), so the projection is
    # the lane's own figure; 0 where no store is configured.
    model = str((firm_data.get("models") or {}).get("transcription") or "")
    stored = rc.stored_pages(rows, model, log=lambda _m: None) if model else 0
    est = fn(rows, rs.categories(data), plan, firm_data, pricing, stored_pages=stored, request_counts=counts)
    lines = [{"item": "transcription", "dollars": est["parts"]["transcription"],
              "minutes": est["minutes"]["transcription"], "stored_pages": stored}]  # fmt: skip
    lines += [{"item": f"{k} {u['set_type']}", "dollars": u["usd"], "minutes": u["minutes"]}
              for k, u in est["units"].items()]  # fmt: skip
    lines.append({"item": "notes and review", "dollars": est["parts"]["notes_and_review"],
                  "minutes": est["minutes"]["notes_review_filing"]})  # fmt: skip
    return {"dollars": est["usd"], "minutes": est["minutes"]["total"], "lines": lines, "estimate": est}


def exhibits(work: Path, scoped: dict[str, Any], firm_data: dict[str, Any] | None, production: Path | None) -> dict:
    """``exhibit_plan.place`` over the scoped copy's record, as the lane's scope
    stage runs it, and, given a production response, ``exhibit_index``'s lines."""
    from .. import exhibit_index as xi, exhibit_plan as xp

    fn = S.entry("exhibits")
    table = xp.table(firm_data)
    if table is None:
        raise ValueError("the firm file authors no response_scope.exhibits table (or it does not validate)")
    data = work / "data"
    placed = fn(scoped["record"]["files"], table, xp.walled_ids(data))
    (data / xp.FILE).write_text(json.dumps(placed, indent=1), encoding="utf-8")
    listed = None
    if production is not None:
        md = production.read_text(encoding="utf-8") if production.suffix.lower() != ".docx" else _docx_md(production)
        listed = xi.index_lines(md, placed)[1]["listed"]
    return {"placed": placed, "listed": listed}


def _docx_md(path: Path) -> str:
    from ..revise.prior import docx_to_md

    return docx_to_md(path)


def form_read(path: Path) -> Any:
    """``forms.read_stage.pixel_read``: the $0 pixel read of one served form file."""
    return S.entry("form-read")(path)
