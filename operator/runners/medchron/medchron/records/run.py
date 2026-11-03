"""`medchron records <job_dir>`: one matter records job, resumable.

    parse -> resolve -> preflight -> locate -> cut -> facts -> verdict

* ``parse`` (one paid call, cached): the request's own words as numbered
  records (``parse.py``). A record asked for about someone else holds the job
  ``for_another_person`` before anything else is spent.
* ``resolve`` ($0): the matter (an id, or a number naming exactly one matter),
  then per record the matter's PDFs whose names carry its words, or its
  provider; each candidate downloaded once to ``data/files/<file_id>.pdf``.
* ``preflight`` ($0): the candidates' unread pages times ``usd_per_page``
  against the job's cap and the month's budget. A job that cannot afford its
  reading holds here, before any page is bought.
* ``locate`` (paid, capped at every request): the page finder ONCE per
  (record, file), with the record's verbatim words; the file's pages are read
  first, looped only while pages remain and each pass makes progress. Every
  result is saved to ``data/locate/<n>/<file_id>.json`` and never overwritten,
  so a re-run reads it back and pays nothing. A record found in one file is not
  looked for in the next.
* ``cut`` ($0): each found range, or the whole file when the range is every
  page, through the connector's one preparation path into
  ``data/cuts/<n>-<k>.pdf`` with its sha256, size and page count.
* ``facts`` ($0): the contract's renderer on the results and the cuts, and the
  reply: a fixed opening line and that block, nothing a model wrote.
* ``verdict``: ``delivered`` (with its proof block) when every record is
  accounted for and nothing held; else ``held`` with its coded reason and a
  words-only reply. A proof run ends ``dry_run`` with the would-be reply under
  ``proof/``.

A cost limit (:class:`limits.LimitHold`, from the doorway or the page
finder's hook) is caught only at the top, and becomes the hold reason.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from smokeball_connector import page_finder as pf

from .. import budget as budget_mod, limits as limits_mod, proof_mode, stop_request
from ..ledger import Ledger
from ..litigation.runbase import RunBase
from ..litigation.tools import dump
from ..llm import Doorway
from . import assemble, firm as firm_mod, job as job_mod, parse as parse_mod
from .outcome import REASON, RecordsFailed, RecordsHold, Verdict

STAGES = ("parse", "resolve", "preflight", "locate", "cut", "facts")
#: The statuses a matter named by its number is resolved among.
MATTER_STATUSES = ("Open", "Pending")
#: Candidate files per record, at most.
MAX_CANDIDATES = 3
#: Words that never make a file-name search narrower.
_STOP = frozenset(
    "a an and any all as at by for from in into me my of on or our please send the their this to with records record "
    "copy copies file files documents document".split()
)


class CapStop(pf.CallRefused):
    """A cost limit reached inside the page finder: carries the LimitHold, and
    passes out of ``locate`` unchanged (``page_finder.CallRefused``)."""

    def __init__(self, hold: limits_mod.LimitHold) -> None:
        super().__init__(hold.reason)
        self.hold = hold


class CapHook:
    """The page finder's ``CALL_HOOK`` for one job: every request is checked
    against the job's cap and the month's budget before it is sent (spend
    priced from the usage ledger, by each request's own model), and its usage
    is appended to that ledger when it returns."""

    def __init__(self, run: "RecordsRun", stage: str = "locate") -> None:
        self.run, self.stage = run, stage

    def before(self, model: str, est_tokens: int) -> None:
        try:
            self.run.pricing.rate_for(model)
            spent = self.run.budget.refresh()
        except budget_mod.BudgetError as exc:
            why = f"{limits_mod.CAP_SETTING}: a request's model has no price, so the cap cannot bind; the run stopped"
            raise CapStop(limits_mod.LimitHold(limits_mod.CAP_SETTING, why)) from exc
        try:
            self.run.limits.check_each_call(spent, self.stage)
        except limits_mod.LimitHold as hold:
            raise CapStop(hold) from hold

    def after(self, model: str, usage: dict[str, int]) -> None:
        tokens = SimpleNamespace(
            input_tokens=int(usage.get("input_tokens") or 0), output_tokens=int(usage.get("output_tokens") or 0)
        )
        self.run.ledger.record(self.stage, model, tokens, effort="low", cache=False, batch=False)


def _query(text: str) -> str:
    words = [w for w in " ".join(str(text or "").split()).split() if w.casefold().strip(".,;:") not in _STOP]
    return " ".join(words[:8])


class _ListingCache:
    """The client, with the matter's file and folder listings read once per job
    (``find_files`` lists the whole matter on every search)."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self._seen: dict[str, Any] = {}

    def get(self, path: str, **params: Any) -> Any:
        if path.endswith("/documents/files") or path.endswith("/documents/folders"):
            key = json.dumps([path, params], sort_keys=True)
            if key not in self._seen:
                self._seen[key] = self._client.get(path, **params)
            return self._seen[key]
        return self._client.get(path, **params)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


class RecordsRun(RunBase):
    def __init__(
        self,
        job_dir: Path,
        *,
        inputs_dir: str | None = None,
        pricing: str | None = None,
        state_dir: str | Path | None = None,
        client: Any = None,
        model_client: Any = None,
        log: Callable[[str], None] = print,
    ) -> None:
        self.job = job_mod.load(job_dir)
        self.firm = firm_mod.load(inputs_dir)
        self.data = self.job.data
        self.data.mkdir(parents=True, exist_ok=True)
        self.state = parse_mod.state_dir(state_dir)
        self.log = log
        cap = self.job.per_job_cap_usd or self.firm.per_job_cap_usd
        monthly = self.job.monthly_budget_usd or self.firm.monthly_budget_usd
        self.pricing = budget_mod.Pricing.load(
            Path(pricing or os.environ.get(budget_mod.PRICING_ENV) or budget_mod.PRICING_DEFAULT)
        )
        self.ledger = Ledger(self.data / "usage-ledger.jsonl")
        self.budget = budget_mod.Budget(self.pricing, cap, [self.ledger.path], 1.0)
        self.limits = limits_mod.Limits(
            cap_usd=cap,
            monthly_budget_usd=monthly,
            usd_per_scanned_page=0.0,
            usd_per_audit_claim=0.0,
            month_cents_used=self.job.month_cents_used,
        )
        self.doorway = Doorway(ledger=self.ledger, client=model_client, log=log, before_request=self._before_call)
        self._client = client
        self.seat = None

    # ---- plumbing -------------------------------------------------------------
    @property
    def client(self) -> Any:
        if self._client is None:
            from smokeball_connector.client import build_client_from_env

            self._client = build_client_from_env()
        if not isinstance(self._client, _ListingCache):
            self._client = _ListingCache(self._client)
        return self._client

    def reopen(self, stages: list[str]) -> None:
        known = [s for s in stages if s in STAGES]
        if not known:
            return
        first = min(STAGES.index(s) for s in known)
        for s in STAGES[first:]:
            if s in self._state():
                self._put(s, {"status": "reopened"})

    def _records(self) -> list[dict[str, Any]]:
        return list(self._json("records.json"))

    def _matter(self) -> str:
        return str(self._json("matter.json")["matter_id"])

    def _current(self) -> str:
        st = self._state()
        return next((s for s in STAGES if st.get(s, {}).get("status") != "done"), "verdict")

    # ---- parse --------------------------------------------------------------------
    def _ask_parse(self, model: str, text: str) -> dict[str, Any]:
        r = self.doorway.call(
            "parse",
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "THE COLLEAGUE'S EMAIL:\n" + text},
                        {"type": "text", "text": parse_mod.INSTRUCTION},
                    ],
                }
            ],
            max_tokens=parse_mod.MAX_TOKENS,
            effort={"effort": "low", "format": {"type": "json_schema", "schema": parse_mod.SCHEMA}},
            cache_blocks=(),
        )
        return parse_mod.answer_json(r.text)

    def _parse(self) -> None:
        from smokeball_connector import page_finder as pf

        try:
            rows = parse_mod.parse(self.job.request_text, pf.model(), self._ask_parse, cache=self.state / "parse")
        except parse_mod.ParseRefused as exc:
            raise RecordsHold(f"scope_unresolved: {exc}") from exc
        dump(self.data / "records.json", rows)

    def _check_parsed(self) -> None:
        rows = self._records()
        if not rows:
            raise RecordsHold("scope_unresolved: the request names no record to look for")
        if any(r.get("for_another_person") for r in rows):
            raise RecordsHold("for_another_person: a record asked for is about someone other than the matter's client")

    # ---- resolve --------------------------------------------------------------------
    def _matter_id(self) -> str:
        if self.job.matter_id:
            return self.job.matter_id
        if not self.job.matter_number:
            raise RecordsHold("scope_unresolved: the request named no matter")
        from ..arrivals import open_matters

        # The case file lane's resolution: the number against the firm's open
        # (and pending) matters, exactly one or a hold.
        want = " ".join(self.job.matter_number.split()).casefold()
        try:
            rows = open_matters(SimpleNamespace(client=self.client), MATTER_STATUSES)
        except Exception as exc:  # could not look is never "no such matter"
            raise RecordsFailed(f"unexpected: the matter could not be looked up ({type(exc).__name__})") from exc
        ids = {str(m["id"]) for m in rows if " ".join(str(m["number"]).split()).casefold() == want}
        if len(ids) != 1:
            raise RecordsHold(
                "scope_unresolved: the matter number names " + ("no matter" if not ids else "more than one matter")
            )
        return str(next(iter(ids))).lower()

    def _fetch(self, mid: str, file_id: str, files: dict[str, Any]) -> bool:
        """Download one candidate once. False (with the reason noted) when it is
        not a PDF on this matter, or cannot be fetched."""
        from smokeball_connector import matter_files as mf, page_finder as pf
        from smokeball_connector.page_split import split

        if file_id in files:
            return "sha256" in files[file_id]
        dest = self.data / "files" / f"{file_id}.pdf"
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            meta = self.client.get(f"/matters/{mid}/documents/files/{file_id}")
            if not mf._on_matter(meta, mid):
                raise mf.StageRefused("the file's own record does not name this matter")
            if str(meta.get("fileExtension") or "").strip().lower().lstrip(".") != "pdf":
                raise mf.StageRefused("not a PDF")
            if not dest.is_file():
                tmp = dest.with_suffix(".part")
                mf._download_to(self.client, mid, file_id, tmp, mf.MAX_SOURCE_BYTES)
                tmp.replace(dest)
            with open(dest, "rb") as fh:
                if fh.read(5) != b"%PDF-":
                    raise mf.StageRefused("not a PDF")
            with tempfile.TemporaryDirectory(dir=self.data) as tmpdir:
                total, _ = split(dest, 1, 0, Path(tmpdir))
        except Exception as exc:  # noqa: BLE001 - one file's refusal is recorded, never fatal to the job
            files[file_id] = {"refused": type(exc).__name__}
            dest.unlink(missing_ok=True)
            return False
        files[file_id] = {
            "name": str(meta.get("name") or ""),
            "sha256": pf.file_sha256(dest),
            "page_count": int(total),
            "size": dest.stat().st_size,
        }
        return True

    def _candidates(self, mid: str, rec: dict[str, Any]) -> list[str]:
        from smokeball_connector import matter_files as mf

        for words in (_query(rec["asked"]), _query(rec["hints"].get("provider", ""))):
            if not words:
                continue
            got = mf.find_files(self.client, mid, name=words, extension="pdf", limit=MAX_CANDIDATES)
            if got.get("error"):
                raise RecordsHold("finder_failed: the matter's files could not be searched")
            ids = [str(f["fileId"]) for f in got.get("files") or [] if f.get("fileId")]
            if ids:
                return ids[:MAX_CANDIDATES]
        return []

    def _resolve(self) -> None:
        mid = self._matter_id()
        dump(self.data / "matter.json", {"matter_id": mid})
        files: dict[str, Any] = {}
        candidates: dict[str, list[str]] = {}
        for rec in self._records():
            ids = [fid for fid in self._candidates(mid, rec) if self._fetch(mid, fid, files)]
            candidates[str(rec["n"])] = ids
        # A record whose own words and provider name no file ("the discharge
        # summary") is looked for in the files the request's other records led
        # to: one email's records are almost always from the same sets.
        shared = list(dict.fromkeys(fid for ids in candidates.values() for fid in ids))[:MAX_CANDIDATES]
        for n, ids in candidates.items():
            if not ids:
                candidates[n] = list(shared)
        dump(self.data / "files.json", files)
        dump(self.data / "candidates.json", candidates)

    # ---- preflight --------------------------------------------------------------------
    def _unread(self, file_id: str, meta: dict[str, Any]) -> int:
        from smokeball_connector import page_finder as pf

        index = pf.load_index(meta["sha256"])
        settled = sum(1 for k in index["unreadable"] if pf._settled(index, k))
        return max(0, int(meta["page_count"]) - len(index["pages"]) - settled)

    def _preflight(self) -> None:
        files = self._json("files.json")
        cand = self._json("candidates.json")
        wanted = {fid for ids in cand.values() for fid in ids}
        pages = sum(self._unread(fid, files[fid]) for fid in sorted(wanted))
        self.log(f"[preflight] {len(wanted)} files, {pages} unread pages")
        self.limits.check_before_paid(
            projected_usd=pages * self.firm.usd_per_page, spent_usd=self.budget.refresh(), stage="preflight"
        )

    # ---- locate --------------------------------------------------------------------
    def _read_all(self, source: Path, mid: str, hook: CapHook) -> None:
        """Read the file's every page, a pass at a time, while pages remain and
        a pass made progress (bounded by the page count)."""
        from smokeball_connector import page_finder as pf

        passes = 0
        while True:
            with tempfile.TemporaryDirectory(dir=self.data) as tmp:
                built = pf.build_index(source, Path(tmp), window=None, matter_id=mid, hook=hook)
            index = built["index"]
            left = int(index.get("page_count") or 0) - len(index["pages"]) - len(index["unreadable"])
            passes += 1
            progress = len(built["read_now"]) + len(built["unreadable_now"])
            if left <= 0 or not progress or passes > int(index.get("page_count") or 0) // pf.PAGES_PER_CALL + 2:
                return

    def locate_path(self, n: int, file_id: str) -> Path:
        return self.data / "locate" / str(n) / f"{file_id}.json"

    def _locate_one(
        self, n: int, file_id: str, what: str, mid: str, meta: dict[str, Any], hook: CapHook
    ) -> dict[str, Any]:
        from smokeball_connector import page_finder as pf

        path = self.locate_path(n, file_id)
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        source = self.data / "files" / f"{file_id}.pdf"
        try:
            self._read_all(source, mid, hook)
            with tempfile.TemporaryDirectory(dir=self.data) as tmp:
                out = pf.locate(source, what, Path(tmp), matter_id=mid, hook=hook)
        except pf.FinderRefused as exc:
            raise RecordsHold(f"finder_failed: {exc}") from exc
        out["items"] = [{k: v for k, v in item.items() if not k.startswith("_")} for item in out.get("items") or []]
        out.update(matterId=mid, fileId=file_id, name=meta.get("name") or "")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # never overwritten
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(out, fh, sort_keys=True)
        return out

    def located(self) -> list[tuple[int, str, dict[str, Any]]]:
        """Every saved (record, file, result), in record then candidate order."""
        out = []
        cand = self._json("candidates.json") if (self.data / "candidates.json").is_file() else {}
        for rec in self._records():
            for fid in cand.get(str(rec["n"])) or []:
                p = self.locate_path(int(rec["n"]), fid)
                if p.is_file():
                    out.append((int(rec["n"]), fid, json.loads(p.read_text(encoding="utf-8"))))
        return out

    def _locate(self) -> None:
        mid, files, cand = self._matter(), self._json("files.json"), self._json("candidates.json")
        hook = CapHook(self)
        for rec in self._records():
            n = int(rec["n"])
            for fid in cand.get(str(n)) or []:
                stop_request.check("records")
                out = self._locate_one(n, fid, rec["asked"], mid, files[fid], hook)
                if out.get("matchError"):
                    raise RecordsHold("finder_failed: the match step did not answer")
                if assemble.found(out):
                    break
        for n, _fid, out in self.located():
            if int(out.get("remainingPages") or 0) > 0:
                raise RecordsHold(f"match_unsettled: record {n}'s file still has pages not read")

    # ---- cut ------------------------------------------------------------------------
    def _cut(self) -> None:
        from records_contract import batches as batches_mod
        from smokeball_connector import matter_files as mf

        files = self._json("files.json")
        results, owners = assemble.merge(self.located())
        rows: list[dict[str, Any]] = []
        taken: set[str] = set()
        (self.data / "cuts").mkdir(exist_ok=True)
        counter: dict[int, int] = {}
        for fid, result in results.items():
            for pages, n in assemble.ranges_to_cut(result, fid, owners):
                counter[n] = k = counter.get(n, 0) + 1
                try:
                    span = mf.parse_pages(pages) if pages else None
                    blob, _removed = mf.prepare_local(self.data / "files" / f"{fid}.pdf", span)
                except mf.StageRefused as exc:
                    raise RecordsHold(f"cut_refused: {exc}") from exc
                if len(blob) > mf.MAX_FILE_BYTES:
                    raise RecordsHold("cut_refused: a cut is over the size one email carries")
                out = self.data / "cuts" / f"{n}-{k}.pdf"
                out.write_bytes(blob)
                count = span[1] - span[0] + 1 if span else int(files[fid]["page_count"])
                rows.append(
                    {
                        "n": n,
                        "k": k,
                        "file_id": fid,
                        "pages": pages,
                        "page_count": count,
                        "sha256": hashlib.sha256(blob).hexdigest(),
                        "size": len(blob),
                        "name": mf.attachment_name(files[fid].get("name"), span, taken),
                        "path": str(out.relative_to(self.data)),
                    }
                )
        try:
            batches_mod.batches(rows)
        except batches_mod.BatchesRefused as exc:
            raise RecordsHold(f"cut_refused: {exc}") from exc
        dump(self.data / "cuts.json", rows)

    # ---- facts ------------------------------------------------------------------------
    def compose(self, hold: str | None) -> dict[str, Any]:
        """``facts.json``: the block, its accounting, and the reply. On a hold
        the reply is words only (rendered with no file attached)."""
        records = self._records() if (self.data / "records.json").is_file() else []
        mid = self._matter() if (self.data / "matter.json").is_file() else (self.job.matter_id or "")
        located = self.located() if records else []
        results, owners = assemble.merge(located)
        cuts = self._json("cuts.json") if (hold is None and (self.data / "cuts.json").is_file()) else []
        files = [{k: c[k] for k in ("file_id", "name", "pages", "page_count", "sha256", "size")} for c in cuts]
        rendered = assemble.render(results, files, mid) if results or files else None
        why = rendered.hold_reason if rendered is not None else None
        if why and hold is None:
            hold = f"finder_failed: {why}"
            rendered = assemble.render(results, [], mid)
            why = rendered.hold_reason
        block_t = rendered.text if rendered is not None and not why else ""
        block_h = rendered.html if rendered is not None and not why else ""
        cand = self._json("candidates.json") if (self.data / "candidates.json").is_file() else {}
        unresolved = {int(r["n"]) for r in records if not cand.get(str(r["n"]))}
        per_n: dict[int, int] = {}
        for c in cuts:
            per_n[int(c["n"])] = per_n.get(int(c["n"]), 0) + 1
        outcomes = assemble.outcomes(records, rendered.items if rendered is not None else [], owners, unresolved, per_n)
        if hold is None and records and any(o["outcome"] == "unresolved" for o in outcomes):
            hold = "scope_unresolved: a record asked for matched no file on the matter"
        if hold is None and any(o["outcome"] == "unplaced" for o in outcomes):
            hold = "cut_refused: a record was found and no cut carries it"
        if hold is not None:
            return self.compose_held(hold, results, mid, records, owners, unresolved)
        text, html = assemble.compose(block_t, block_h, held=False)
        doc = {
            "text": block_t,
            "html": block_h,
            "items": outcomes,
            "hold_reason": None,
            "reply_text": text,
            "reply_html": html,
        }
        dump(self.data / "facts.json", doc)
        return {**doc, "cuts": cuts}

    def compose_held(
        self,
        hold: str,
        results: dict[str, Any],
        mid: str,
        records: list[dict[str, Any]],
        owners: Any,
        unresolved: set[int],
    ) -> dict[str, Any]:
        rendered = assemble.render(results, [], mid) if results else None
        ok = rendered is not None and not rendered.hold_reason
        block_t, block_h = (rendered.text, rendered.html) if ok and rendered is not None else ("", "")
        outcomes = assemble.outcomes(
            records, rendered.items if ok and rendered is not None else [], owners, unresolved, {}
        )
        text, html = assemble.compose(block_t, block_h, held=True)
        doc = {
            "text": block_t,
            "html": block_h,
            "items": outcomes,
            "hold_reason": hold,
            "reply_text": text,
            "reply_html": html,
        }
        dump(self.data / "facts.json", doc)
        return {**doc, "cuts": []}

    # ---- the walk ----------------------------------------------------------------------
    def _walk(self) -> None:
        steps = (
            ("parse", self._parse),
            ("resolve", self._resolve),
            ("preflight", self._preflight),
            ("locate", self._locate),
            ("cut", self._cut),
        )
        for name, fn in steps:
            self._stage(name, fn)
            if name == "parse":
                self._check_parsed()

    def run(self) -> Verdict:
        hold: str | None = None
        failed: str | None = None
        try:
            self._walk()
        except limits_mod.LimitHold as h:
            hold = h.reason
        except CapStop as stop:
            hold = stop.hold.reason
        except RecordsHold as h:
            hold = str(h)
        except RecordsFailed as exc:
            failed = str(exc)
        except stop_request.StopRequested:
            raise
        except Exception as exc:  # noqa: BLE001 - the verdict carries a sentence; the trace goes to the log
            self.log(traceback.format_exc())
            failed = f"unexpected: {type(exc).__name__}: {str(exc)[:300]}"
        stage = self._current()
        try:
            doc = self.compose(hold) if failed is None else None
        except Exception as exc:  # noqa: BLE001 - a reply that cannot be composed is our failure
            self.log(traceback.format_exc())
            doc, failed = None, f"unexpected: the reply could not be composed ({type(exc).__name__})"
        if doc is not None:
            self._put("facts", {"status": "done"})
            hold = doc["hold_reason"]
        v = self._verdict(stage, hold, failed, doc)
        if v.reason and not REASON.match(v.reason):
            v.reason = f"unexpected: {v.reason}"
        v.cents = int(round(self.budget.refresh() * 100))
        return v

    def _verdict(self, stage: str, hold: str | None, failed: str | None, doc: dict[str, Any] | None) -> Verdict:
        if failed is not None or doc is None:
            return Verdict("failed", stage=stage, reason=failed or "unexpected: no reply was composed")
        items, cuts = doc["items"], doc["cuts"]
        if hold is not None:
            return Verdict("held", stage=stage, reason=hold, items=items)
        if proof_mode.active():
            self._proof_out(doc)
            return Verdict(proof_mode.OUTCOME, stage="verdict", items=items, cuts=cuts)
        proof = {
            "readback": "confirmed",
            "files": [
                {"name": Path(c["path"]).name, "file_id": c["file_id"], "size": c["size"], "sha256": c["sha256"]}
                for c in cuts
            ],
            "review": "code_only",
            "review_cents": 0,
            "review_model": "",
        }
        if not cuts:
            proof["nothing_to_file"] = True
        return Verdict("delivered", stage="verdict", items=items, cuts=cuts, proof=proof)

    def _proof_out(self, doc: dict[str, Any]) -> None:
        out = self.job.job_dir / "proof"
        out.mkdir(exist_ok=True)
        (out / "reply.txt").write_text(doc["reply_text"], encoding="utf-8")
        (out / "reply.html").write_text(doc["reply_html"], encoding="utf-8")
        dump(
            out / "cuts.json",
            [{k: c[k] for k in ("n", "k", "file_id", "pages", "page_count", "sha256", "size")} for c in doc["cuts"]],
        )


__all__ = ["STAGES", "CapHook", "RecordsRun"]
