"""`medchron negotiate <job_dir>`: one negotiation-watch job.

For every open matter: list its files; a matter the lane has never seen is
SEEDED (its cursor set to today's file set, nothing read, nothing announced);
otherwise each offer-related document saved since the cursor is fetched, read
(``extract``), turned into rows (``rows``) and written with the connector's
``add_negotiation_rows`` (re-read before, read back after). Each new offer
becomes a notice (``notice``). The matter's cursor moves only AFTER its writes
read back, and only past the documents actually handled: a document whose
fetch, text or read failed stays new for the next run (three failures and it is
handed to the firm as "not entered, please check the letter").

The verdict is ONE JSON object (stdout's only line and ``verdict.json``):
``delivered`` with counts and the notices, or ``failed`` (our machinery: the
matter list could not be read, the job could not start). A spend cap reached
part way stops the reading and still delivers what was entered.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Callable

from .. import arrivals, budget as budget_mod, limits as limits_mod
from ..ledger import Ledger
from ..llm import Doorway
from ..demand.pull import fetch_all
from ..stages.base import read_jsonl
from . import extract as read_mod, notice as notice_mod, rows as rows_mod, text as text_mod
from .job import load as load_job
from .select import extension, full_name, select

LANE = "negotiation"
OCR_MODEL = "claude-sonnet-5"
MAX_ATTEMPTS = 3
EXIT = {"delivered": 0, "held": 1, "failed": 2}


STATE_ENV = "MEDCHRON_NEGOTIATION_STATE_DIR"


def default_state_dir() -> Path:
    """The lane's persistent state on the volume: the arrivals cursors and the
    per-document read attempts."""
    if os.environ.get(STATE_ENV):
        return Path(os.environ[STATE_ENV])
    return Path(os.environ.get(arrivals.DATA_ENV) or arrivals.DEFAULT_DATA_DIR) / "negotiation" / "state"


def _saved_on_or_after(f: dict[str, Any], cutoff: dt.datetime) -> bool:
    """Whether a file's ``dateCreated`` is at or after ``cutoff``. A file with
    no readable creation time is taken as older (seeded), never as a flood."""
    raw = str(f.get("created") or "").strip()
    if not raw:
        return False
    try:
        when = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return when >= cutoff


def _dump(path: Path, obj: Any) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
    tmp.replace(path)


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


class NegotiationRun:
    def __init__(
        self,
        job_dir: Path,
        *,
        state_dir: str | Path | None = None,
        pricing: str | None = None,
        seat_factory: Callable[[], Any] | None = None,
        client: Any = None,
        layout: Any = None,
        log: Callable[[str], None] = print,
        today: Callable[[], dt.date] | None = None,
    ) -> None:
        self.job = load_job(job_dir)
        self.data = self.job.data
        self.data.mkdir(parents=True, exist_ok=True)
        self.state = Path(state_dir) if state_dir else default_state_dir()
        self.log = log
        price_path = Path(pricing or os.environ.get(budget_mod.PRICING_ENV) or budget_mod.PRICING_DEFAULT)
        self.budget = budget_mod.Budget(
            budget_mod.Pricing.load(price_path), self.job.per_job_cap_usd, [self.data / "usage-ledger.jsonl"], 1.0
        )
        self.limits = limits_mod.Limits(
            cap_usd=self.job.per_job_cap_usd,
            monthly_budget_usd=self.job.monthly_budget_usd,
            usd_per_scanned_page=0.0,
            usd_per_audit_claim=0.0,
            month_cents_used=self.job.month_cents_used,
        )
        self.doorway = Doorway(
            ledger=Ledger(self.data / "usage-ledger.jsonl"), client=client, log=log, before_request=self._before_call
        )
        self._seat_factory, self._seat = seat_factory, None
        self._layout = layout
        self.today = (today or (lambda: dt.date.today()))().isoformat()
        self.counts = {"matters_total": 0, "matters_seeded": 0, "docs_read": 0, "docs_failed": 0}
        self.notices: list[dict[str, Any]] = _load(self.data / "notices.json", [])
        self.stopped: str | None = None

    # ---- plumbing --------------------------------------------------------------
    def _before_call(self, stage: str) -> None:
        self.limits.check_each_call(self.budget.refresh(), stage)

    @property
    def seat(self) -> Any:
        if self._seat is None:
            if self._seat_factory is not None:
                self._seat = self._seat_factory()
            else:
                from ..seat import open_seat

                self._seat = open_seat(self.job.slug)
        return self._seat

    @property
    def layout(self) -> Any:
        """The connector's layout tools, bound to this job's client and the
        firm's authored negotiation design (the child cannot read the seat's
        customer.yaml, so the lane stamps the design into job.json)."""
        if self._layout is None:
            from smokeball_connector import layout_tools
            from smokeball_connector.layout_config import LayoutConfig

            client = self.seat.client
            layout_tools._client = lambda: client
            design = self.job.negotiation_design
            layout_tools.load_layout_config = lambda path=None: LayoutConfig(negotiation_design=design)
            self._layout = layout_tools
        return self._layout

    def _ocr(self, png: bytes) -> str:
        import base64

        img = {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": base64.standard_b64encode(png).decode()},
        }
        r = self.doorway.call(
            "negotiation_ocr",
            model=OCR_MODEL,
            messages=[{"role": "user", "content": [img, {"type": "text", "text": text_mod.OCR_PROMPT}]}],
            max_tokens=8000,
            cache_blocks=(),
        )
        return r.text

    def _attempts(self) -> dict[str, int]:
        return _load(self.state / "attempts.json", {})

    def _set_attempts(self, value: dict[str, int]) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        _dump(self.state / "attempts.json", value)

    # ---- one matter -------------------------------------------------------------
    def _tabs(self, mid: str) -> list[dict[str, Any]]:
        got = self.layout.get_matter_layouts(mid, section="Negotiation Details")
        if not isinstance(got, dict) or got.get("status") != "ok":
            raise RuntimeError("the matter's Negotiation Details could not be read")
        tabs = []
        for item in got.get("items") or []:
            view = item.get("negotiation") or {}
            try:
                pidx = int(item.get("parent_index") or 0)
            except (TypeError, ValueError):
                pidx = 0
            tabs.append(
                {
                    "plaintiff_index": pidx,
                    "name": str(item.get("description") or ""),
                    "rows": view.get("rows") or [],
                    "details": view.get("details"),
                }
            )
        return tabs

    def _fetch(self, mid: str, f: dict[str, Any]) -> Path | None:
        """The document's bytes on the job's disk; a content duplicate resolves
        to its original."""
        mdir = self.data / "m" / mid
        target = {
            "id": str(f["id"]),
            "name": f.get("name"),
            "ext": f.get("ext") or "",
            "size": f.get("size"),
            "folder": f.get("folderId"),
        }
        fetch_all(self.seat, mid, [target], mdir, self.log)
        rows = {r["id"]: r for r in read_jsonl(mdir / "pulled.jsonl")}
        r = rows.get(str(f["id"]))
        for _ in range(3):
            if not r or not r.get("ok"):
                return None
            if r.get("path") and Path(r["path"]).is_file():
                return Path(r["path"])
            r = rows.get(str(r.get("duplicate_of") or ""))
        return None

    def _text(self, mid: str, f: dict[str, Any]) -> str:
        path = self._fetch(mid, f)
        if path is None:
            raise RuntimeError("the document could not be fetched")
        text, _ = text_mod.text_of(path, extension(f), self._ocr)
        if not text.strip():
            raise RuntimeError("the document produced no text")
        return text

    def _write(self, mid: str, plan: dict[str, Any], tabs: list[dict[str, Any]]) -> None:
        """Every tab plan through the connector; each offer gets its final status."""
        for pidx, tp in plan["tabs"].items():
            if not tp.args:
                continue
            res = self.layout.add_negotiation_rows(
                mid, tp.args, plaintiff_index=pidx, details=rows_mod.details_for(tp.tab, self.today)
            )
            status = res.get("status") if isinstance(res, dict) else "refused"
            entries = res.get("entries") or [] if isinstance(res, dict) else []
            for rec in plan["offers"]:
                if rec.get("plaintiff_index") != pidx or rec.get("status") != "to_write":
                    continue
                if status in ("written", "nothing_to_write"):
                    entry = entries[rec["entry"]] if rec["entry"] < len(entries) else {}
                    got = entry.get("status") or "written"
                    rec["status"] = "written" if got in ("written", "to_write") else got
                    if got == "possible_duplicate":
                        rec["reason"] = "the same amount is already on that row with a different date"
                elif status == "readback_mismatch":
                    rec.update(status="not_entered", reason="Smokeball did not show it as entered when checked")
                else:
                    rec.update(status="not_entered", reason="the tab could not be updated")
        for rec in plan["offers"]:
            if rec.get("status") == "to_write":
                rec.update(status="not_entered", reason="the tab could not be updated")
            if rec.get("status") == "possible_duplicate":
                rec["status"] = "not_entered"

    def _notice(
        self, matter: dict[str, Any], doc: dict[str, Any], rec: dict[str, Any], tabs: list[dict[str, Any]]
    ) -> None:
        if rec.get("status") == "already_present":
            return
        multi = len(tabs) > 1
        name = next((t["name"] for t in tabs if t["plaintiff_index"] == rec.get("plaintiff_index")), "")
        plaintiff = "all plaintiffs" if multi and rec.get("joint") else (name if multi else "")
        self.notices.append(
            {
                "matter_id": matter["id"],
                "matter_number": matter.get("number") or "",
                "status": "entered" if rec["status"] == "written" else "not_entered",
                "text": notice_mod.compose(matter, doc, rec, plaintiff),
            }
        )
        _dump(self.data / "notices.json", self.notices)

    def _gave_up(self, matter: dict[str, Any], doc: dict[str, Any]) -> None:
        rec = {"unread": True, "status": "not_entered", "reason": "the Operator could not read it after three tries"}
        self.notices.append(
            {
                "matter_id": matter["id"],
                "matter_number": matter.get("number") or "",
                "status": "not_entered",
                "text": notice_mod.compose(matter, doc, rec),
            }
        )
        _dump(self.data / "notices.json", self.notices)

    def _seed(self, mid: str, files: list[dict[str, Any]]) -> dict[str, str] | None:
        """A matter the lane has never seen. Unauthored cutoff: every file now
        present is taken as handled (nothing read, nothing emailed; returns
        None). With ``seed_saved_before``: only files saved before it are taken
        as handled (what the 2026-10-09 fill read); the rest are returned as
        new, so an offer saved between that fill and this first run is entered
        and announced like any other."""
        self.counts["matters_seeded"] += 1
        cutoff = self.job.seed_saved_before
        if cutoff is None:
            arrivals.commit(LANE, mid, files, data=self.state)
            return None
        handled = [f for f in files if not _saved_on_or_after(f, cutoff)]
        arrivals.commit(LANE, mid, handled, data=self.state)
        return arrivals.manifest_of(handled)

    def matter(self, matter: dict[str, Any]) -> None:
        mid = matter["id"]
        files = arrivals.list_files(self.seat, mid)
        prior = arrivals.cursor(LANE, mid, data=self.state)
        if prior is None:
            prior = self._seed(mid, files)
            if prior is None:
                return
        new = [f for f in arrivals.new_since(files, prior) if select(f)]
        failed: set[str] = set()
        if new and not self.stopped:
            tabs = self._tabs(mid)
            attempts = self._attempts()
            for f in new:
                fid = str(f["id"])
                doc = {"name": full_name(f), "ext": extension(f), "saved": str(f.get("modified") or "")[:10]}
                try:
                    events = read_mod.read_document(
                        self.doorway, doc, self._text(mid, f), [r for t in tabs for r in t["rows"]], tabs
                    )
                except limits_mod.LimitHold as exc:
                    self.stopped = exc.reason
                    failed.update(str(x["id"]) for x in new[new.index(f) :])
                    break
                except Exception as exc:  # noqa: BLE001 - one document's failure stays new for the next run
                    self.log(f"negotiation: a document on a matter could not be read ({type(exc).__name__})")
                    self._read_failed(matter, f, exc)
                    attempts[fid] = attempts.get(fid, 0) + 1
                    self.counts["docs_failed"] += 1
                    if attempts[fid] < MAX_ATTEMPTS:
                        failed.add(fid)
                    else:
                        self._gave_up(matter, doc)
                    continue
                self.counts["docs_read"] += 1
                attempts.pop(fid, None)
                plan = rows_mod.plan_document(events, doc, tabs, self._tokens(tabs))
                self._planned(matter, f, events, plan, tabs)
                self._write(mid, plan, tabs)
                for rec in plan["offers"]:
                    self._notice(matter, doc, rec, tabs)
                if any(tp.args for tp in plan["tabs"].values()):
                    tabs = self._tabs(mid)
            self._set_attempts(attempts)
        elif new:
            failed.update(str(f["id"]) for f in new)
        rows = [f for f in files if str(f.get("id")) not in failed]
        rows += [{"id": fid, "modified": prior[fid]} for fid in failed if fid in prior]
        arrivals.commit(LANE, mid, rows, data=self.state)

    # Observation hooks: no-ops here; the dry run (dryrun.py) records through them.
    def _planned(self, matter: dict[str, Any], f: dict[str, Any], events: list, plan: dict, tabs: list) -> None:
        return None

    def _read_failed(self, matter: dict[str, Any], f: dict[str, Any], exc: Exception) -> None:
        return None

    def _tokens(self, tabs: list[dict[str, Any]]) -> set[str]:
        import re

        words = {w.lower() for t in tabs for w in re.findall(r"[A-Za-z]{3,}", t.get("name") or "")}
        return words | set(self.job.firm_words)

    # ---- the job ---------------------------------------------------------------
    def run(self) -> dict[str, Any]:
        try:
            matters = arrivals.open_matters(self.seat, self.job.matter_statuses)
        except Exception as exc:  # noqa: BLE001 - our machinery: failed, resumable
            return self._verdict("failed", "inventory", f"inventory_unreadable: {type(exc).__name__}")
        self.counts["matters_total"] = len(matters)
        for m in matters:
            try:
                self.matter(m)
            except Exception as exc:  # noqa: BLE001 - one matter's failure leaves its cursor where it was
                self.log(f"negotiation: a matter could not be processed ({type(exc).__name__})")
                self.counts["docs_failed"] += 1
        return self._verdict("delivered", "report", self.stopped)

    def _verdict(self, verdict: str, stage: str, reason: str | None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "verdict": verdict,
            "stage": stage,
            "cents": int(round(self.budget.refresh() * 100)),
            **self.counts,
            "notices": list(self.notices) if verdict == "delivered" else [],
        }
        if reason:
            out["reason"] = str(reason)[:500]
        return out


__all__ = ["EXIT", "LANE", "STATE_ENV", "NegotiationRun", "default_state_dir"]
