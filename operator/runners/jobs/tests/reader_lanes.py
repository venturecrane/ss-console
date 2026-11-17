"""One cell per jobs lane: every fixture read through the lane's own entry
point, on its real run object, with the model client, the seat, sleep and the
file tree as the only things faked (``reader_kit``).

What each records, per fixture: the text the lane wrote (page by page, as the
lane marks pages), its record (a failure record included), and, for the run,
every request sent to paid reading (``Model.record``: the page, its render
size, the request's shape) and the store the run left behind. Litigation and
casefile run a SECOND job over the same files and store: what it downloads and
pays for again is the store's reuse.

The entry points are the contract a refactor keeps (the docstring of
``test_reader_goldens.py``): ``LitigationRun`` + ``_fetch`` / ``_extract``,
``CasefileRun`` + ``casefile.matter._extract``, ``NegotiationRun._text``, the
medchron ``StageRun`` stages ``extract.run`` / ``vision.run``, and the demand
lane's ``preflight.extract_one`` / ``transcribe.run``.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import reader_kit as rk
from reader_fixtures import FIXTURES

MID = "11111111-1111-4111-8111-111111111111"
#: The fixtures each lane is handed: what its readers are written for, and
#: the kinds it must refuse by name.
ALL = list(FIXTURES)
NO_MSG = [k for k in ALL if k != "outlook_msg"]
#: Three model ids the litigation store's method hash is pinned for.
MODELS = ("claude-sonnet-5", "claude-opus-5-5", "claude-haiku-4-5")
JOB_IDS = {
    "job1": "01KTJ0BX0000000000000000A1",
    "job2": "01KTJ0BX0000000000000000A2",
    "cfjob1": "01KTJ0BX0000000000000000C1",
    "cfjob2": "01KTJ0BX0000000000000000C2",
}


def _read(p: Any) -> str | None:
    p = Path(p) if p else None
    return p.read_text(encoding="utf-8") if p is not None and p.is_file() else None


def _jsonl(p: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x] if p.is_file() else []


def _pricing(tmp: Path) -> str:
    from jobs_testkit import PRICING

    p = tmp / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    return str(p)


def _files_json(data: Path, docs: list[dict[str, Any]]) -> None:
    (data / "files").mkdir(parents=True, exist_ok=True)
    (data / "files" / f"{MID}.json").write_text(json.dumps(docs), encoding="utf-8")


# ---- litigation ---------------------------------------------------------------------------
#: the run's frozen "today". Passed to the run, which writes it into its state
#: on the first attempt: setting ``run.today`` after construction left the
#: state holding the wall clock's date in the firm's zone, so the golden held
#: one date and failed from the next midnight on (2026-11-17).
TODAY = dt.date(2026, 11, 16)


def _lit_run(tmp: Path, name: str, seat: rk.Seat, model: rk.Model, store: Path) -> Any:
    import litigation_testkit as lt
    from jobs.litigation.run import LitigationRun

    inputs = lt.make_inputs(tmp / "inputs", concurrency=1)
    job = lt.make_job(tmp / name, job_id=JOB_IDS[name])
    return LitigationRun(
        job, inputs_dir=str(inputs), pricing=_pricing(tmp), state_dir=tmp / "state", seat_factory=lambda: seat, client=model,
        log=lambda _m: None, textcache_dir=store, today=lambda: TODAY,
    )  # fmt: skip


def _lit_job(tmp: Path, name: str, keys: list[str], store: Path) -> dict[str, Any]:
    from jobs.litigation import fetch as fetch_mod

    docs, blobs = rk.listing(keys)
    seat, model = rk.Seat(docs, blobs), rk.Model()
    run = _lit_run(tmp, name, seat, model, store)
    _files_json(run.data, docs)
    ids = [d["id"] for d in docs]
    (run.data / "plan.json").write_text(
        json.dumps({MID: {"read_groups": ["court"], "audit": "", "candidates": ids}}), encoding="utf-8"
    )
    run._fetch()
    run._extract()
    mdir = fetch_mod.matter_dir(run.data, MID)
    recs = {r["file_id"]: r for r in _jsonl(mdir / "extracted.jsonl")}
    pulled = fetch_mod.pulled(run.data, MID)
    names = rk.by_id()
    files = {}
    for d in docs:
        fid = d["id"]
        rec = dict(recs.get(fid) or {})
        files[names[fid]] = {"pulled": pulled.get(fid), "record": rec, "text": rk.paged(_read(rec.get("text_path")))}
    return {"files": files, "paid": model.record(), "seat": seat.events, "state": run._state()}


def litigation(tmp: Path, mp: Any) -> dict[str, Any]:
    from jobs.litigation import textcache

    rk.Sleeps(mp)
    _fake_msg(mp)
    store = tmp / "store"
    first = _lit_job(tmp, "job1", ALL, store)
    after_first = rk.tree(store, tmp)
    second = _lit_job(tmp, "job2", ALL, store)
    return rk.scrub(
        {
            "first": first,
            "store_after_first": after_first,
            "second": second,
            "store_after_second": rk.tree(store, tmp),
            "method_version": {m: textcache.method_version(m) for m in MODELS},
        },
        tmp,
    )


# ---- casefile -----------------------------------------------------------------------------
def _cf_job(tmp: Path, name: str, keys: list[str], store: Path, mp: Any) -> dict[str, Any]:
    import casefile_testkit as ct
    from jobs.casefile import matter as matter_mod
    from jobs.casefile.run import CasefileRun
    from jobs.litigation import fetch as fetch_mod

    # The fake browser the lane prints with; JOBS_DATA_DIR is the test's own
    # (``test_reader_goldens.STORE_ENV``).
    mp.setenv("SMD_CHROME", ct.environ(tmp)["SMD_CHROME"])
    docs, blobs = rk.listing(keys)
    seat, model = rk.Seat(docs, blobs), rk.Model()
    inputs = ct.make_inputs(tmp / "cf-inputs")
    job = ct.make_job(tmp / name, job_id=JOB_IDS[name])
    run = CasefileRun(
        job, inputs_dir=str(inputs), pricing=_pricing(tmp), state_dir=tmp / "cf-state", seat_factory=lambda: seat, client=model,
        log=lambda _m: None, textcache_dir=store,
    )  # fmt: skip
    mdir = fetch_mod.matter_dir(run.data, MID)
    (mdir / "inv.json").write_text(json.dumps({"files": docs}), encoding="utf-8")
    ids = [d["id"] for d in docs]
    fetch_mod.fill_from_cache(run.cache, run.data, MID, docs, ids)
    fetch_mod.fetch_matter(run.seat, MID, docs, ids, run.data, run.log)
    fetch_mod.index_downloads(run.cache, run.data, MID, docs)
    matter_mod._extract(run, MID)
    recs = {r["file_id"]: r for r in _jsonl(mdir / "extracted.jsonl")}
    names = rk.by_id()
    files = {names[f]: {"record": r, "text": rk.paged(_read(r.get("text_path")))} for f, r in recs.items()}
    return {"files": files, "paid": model.record(), "seat": seat.events}


def casefile(tmp: Path, mp: Any) -> dict[str, Any]:
    rk.Sleeps(mp)
    _fake_msg(mp)
    store = tmp / "store"
    first = _cf_job(tmp, "cfjob1", ALL, store, mp)
    after = rk.tree(store, tmp)
    second = _cf_job(tmp, "cfjob2", ALL, store, mp)
    return rk.scrub(
        {"first": first, "store_after_first": after, "second": second, "store_after_second": rk.tree(store, tmp)}, tmp
    )


# ---- negotiation --------------------------------------------------------------------------
def negotiation(tmp: Path, mp: Any) -> dict[str, Any]:
    from jobs.negotiation.run import NegotiationRun

    rk.Sleeps(mp)
    _fake_msg(mp)
    jd = tmp / "neg-job"
    (jd / "data").mkdir(parents=True)
    job = {"kind": "negotiation", "job_id": "01KTJ0BX0000000000000000NG", "slug": "example",
           "matter_statuses": ["Open"], "firm_words": ["example"], "per_job_cap_usd": 50.0,
           "monthly_budget_usd": 100.0, "month_cents_used": 0}  # fmt: skip
    (jd / "job.json").write_text(json.dumps(job), encoding="utf-8")
    docs, blobs = rk.listing(ALL)
    seat, model = rk.Seat(docs, blobs), rk.Model()
    run = NegotiationRun(jd, state_dir=tmp / "neg-state", pricing=_pricing(tmp), seat_factory=lambda: seat,
                         client=model, log=lambda _m: None)  # fmt: skip
    names = rk.by_id()
    files: dict[str, Any] = {}
    for d in docs:
        try:
            files[names[d["id"]]] = {"text": rk.paged(run._text(MID, d))}
        except Exception as exc:  # noqa: BLE001 - the refusal IS the record: its class and message
            files[names[d["id"]]] = {"raised": type(exc).__name__, "message": str(exc)[:300]}
    return rk.scrub({"files": files, "paid": model.record(), "seat": seat.events}, tmp)


# ---- medchron -----------------------------------------------------------------------------
def _stage_run(root: Path, model: rk.Model, seat: rk.Seat) -> Any:
    import yaml

    from jobs.medchron import config as config_mod, job as job_mod
    from jobs.stages.base import StageRun
    from jobs_testkit import FIRM_CONFIG, job_yaml

    data_root = root / "data"
    (data_root / "example-matter").mkdir(parents=True, exist_ok=True)
    jd = root / "job"
    jd.mkdir(parents=True, exist_ok=True)
    (jd / "job.yaml").write_text(job_yaml(data_root), encoding="utf-8")
    firm = root / "firm.yaml"
    firm.write_text(yaml.safe_dump(FIRM_CONFIG, sort_keys=False), encoding="utf-8")
    job = job_mod.load(jd)
    return StageRun(
        job=job, cfg=config_mod.load(str(firm)), unit=job.units[0], slug_dir=data_root / "example-matter",
        decided={}, log=lambda _m: None, seat_factory=lambda: seat, client_factory=lambda: model,
        date_stamp="11-16-26",
    )  # fmt: skip


def _seed_raw(sd: Path, keys: list[str]) -> None:
    import hashlib

    raw = sd / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    rows = []
    for k in keys:
        f = FIXTURES[k]
        p = raw / f"{f.id}{f.ext}"
        p.write_bytes(f.data)
        rows.append({"id": f.id, "name": f.name, "ext": f.ext, "folder": "/MEDICAL", "ok": True, "path": str(p),
                     "size_got": len(f.data), "sha256": hashlib.sha256(f.data).hexdigest()})  # fmt: skip
    (sd / "raw_manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def medchron(tmp: Path, mp: Any) -> dict[str, Any]:
    from jobs.stages import extract as extract_stage, vision as vision_stage

    rk.Sleeps(mp)
    model = rk.Model()
    sr = _stage_run(tmp / "mc", model, rk.Seat([], {}))
    sd = sr.slug_dir
    _seed_raw(sd, NO_MSG)
    code_x = extract_stage.run(sr)
    queue = json.loads((sd / "scan_queue.json").read_text(encoding="utf-8"))
    code_v = vision_stage.run(sr)
    names = rk.by_id()
    files = {}
    for r in _jsonl(sd / "extracted.jsonl"):
        files[names[r["id"]]] = {"record": r, "text": rk.paged(_read(sd / "text" / f"{r['id']}.txt"))}
    return rk.scrub(
        {"exits": {"extract": code_x, "vision": code_v}, "files": files, "scan_queue": queue,
         "ocr_results": _jsonl(sd / "ocr_results.jsonl"), "paid": model.record()},
        tmp,
    )  # fmt: skip


# ---- demand / drafting --------------------------------------------------------------------
def demand(tmp: Path, mp: Any) -> dict[str, Any]:
    from jobs.demand import preflight, transcribe
    from jobs.ledger import Ledger
    from jobs.llm import Doorway

    rk.Sleeps(mp)
    data = tmp / "demand"
    (data / "raw").mkdir(parents=True)
    (data / "text").mkdir()
    # Paths relative to the job's dir: a reader's error message carries the
    # path, cut at a fixed length, so an absolute temp path would move it.
    mp.chdir(data)
    rows = []
    for k in NO_MSG:
        f = FIXTURES[k]
        p = Path("raw") / f"{f.id}{f.ext}"
        p.write_bytes(f.data)
        rows.append(
            {"id": f.id, "name": f.full, "folder": "/MEDICAL", "kind": "document", "ext": f.ext, "path": str(p)}
        )
    extracted = [preflight.extract_one(r, Path("text")) for r in rows]
    preflight.write_extracted(data, extracted)
    model = rk.Model()
    doorway = Doorway(ledger=Ledger(data / "usage-ledger.jsonl"), client=model, log=lambda _m: None)
    machine = transcribe.run(data, doorway, "claude-sonnet-5", lambda _m: None, 1)
    names = rk.by_id()
    files = {names[r["id"]]: {"record": r, "text": rk.paged(_read(r.get("text_path")))} for r in extracted}
    return rk.scrub({"files": files, "transcribed": machine, "paid": model.record()}, tmp)


# ---- an Outlook container -----------------------------------------------------------------
class FakeMsg:
    """``extract_msg.Message`` for the ``.msg`` fixture: a real container needs
    the OLE writer no test has (``reader_fixtures.MSG``). It carries a two-page
    scan and a Word letter."""

    def __init__(self, path: str, **kw: Any) -> None:
        from reader_fixtures import MSG, MSG_ATTACHMENT, docx

        if Path(path).read_bytes() != MSG:
            raise ValueError("not an OLE file")
        self.subject, self.sender, self.to, self.cc, self.date = (
            "RE: offer", "Adjuster <adjuster@carrier.example>", "intake@firm.example", "", "2026-10-07 10:00:00",
        )  # fmt: skip
        self.body = "Our offer is 9,000 to settle. The signed release is attached."
        scan, letter = type("Att", (), {})(), type("Att", (), {})()
        scan.longFilename, scan.shortFilename, scan.data = "Release.pdf", None, MSG_ATTACHMENT
        letter.longFilename, letter.shortFilename, letter.data = "Letter.docx", None, docx(merged=False)
        self.attachments = [scan, letter]

    def close(self) -> None:
        pass


def _fake_msg(mp: Any) -> None:
    import extract_msg

    mp.setattr(extract_msg, "Message", FakeMsg)


LANES = {
    "litigation": litigation,
    "casefile": casefile,
    "negotiation": negotiation,
    "medchron": medchron,
    "demand": demand,
}

__all__ = ["LANES", "MODELS", "FakeMsg"]
