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

PROVEN, NOT CLAIMED (2026-10-13). Every row about to be written is checked
against the document first (``offer_check``: the amount and date printed in the
text, then an independent model read of side, amount, date and kind); a miss is
HELD, never entered or announced, and its document stays new for a retry. A
delivered verdict carries the ``proof`` block the daemon requires: the rows
written and read back, the review's status and spend, and the alerts
(``review_held``, ``readback_mismatch``) that page SMD while the run's notices
still go out.
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
from . import extract as read_mod, notice as notice_mod, offer_check, rows as rows_mod, summary as summary_mod
from . import text as text_mod
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
        self.counts = {
            "matters_total": 0,
            "matters_seeded": 0,
            "docs_read": 0,
            "docs_failed": 0,
            "offers_held": 0,
            "events_excluded": 0,
            "seed_offers_entered": 0,
            "offers_reviewed": 0,
            "offers_held_review": 0,
            "offers_held_final": 0,
            "rows_read_back": 0,
            "rows_readback_mismatch": 0,
        }
        self.notices: list[dict[str, Any]] = _load(self.data / "notices.json", [])
        #: Offers a document showed with no date: neither entered nor emailed,
        #: listed in the verdict for SMD (never dropped without a record).
        self.held: list[dict[str, Any]] = []
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
        names: dict[int, str] | None = None
        for item in got.get("items") or []:
            view = item.get("negotiation") or {}
            try:
                pidx = int(item.get("parent_index") or 0)
            except (TypeError, ValueError):
                pidx = 0
            name = str(item.get("description") or "")
            if not name:
                names = self._plaintiff_names(mid) if names is None else names
                name = names.get(pidx, "")
            tabs.append(
                {
                    "plaintiff_index": pidx,
                    "name": name,
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

    def _plaintiff_names(self, mid: str) -> dict[int, str]:
        """{plaintiff index: name} from the matter's Plaintiff contacts. A tab
        carries no name of its own (the firm's data, 2026-10-10: a reader could
        not tell which plaintiff an offer named). A failed read names no one."""
        out: dict[int, str] = {}
        try:
            matter = self.seat.client.get(f"/matters/{mid}")
            for i, p in enumerate(((matter or {}).get("items") or {}).get("Plaintiff") or []):
                cid = ((p or {}).get("contact") or {}).get("id")
                if cid:
                    c = self.seat.client.get(f"/contacts/{cid}") or {}
                    person, org = c.get("person") or {}, c.get("company") or c.get("organisation") or {}
                    first_last = (person.get("firstName"), person.get("lastName"))
                    out[i] = " ".join(x for x in first_last if x) or str(org.get("name") or "")
        except Exception as exc:  # noqa: BLE001 - names help the reader; their absence never stops a matter
            self.log(f"negotiation: plaintiff names could not be read ({type(exc).__name__})")
        return out

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
            if status == "written":
                self.counts["rows_read_back"] += sum(1 for x in entries if x.get("status") == "written")
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
                    self.counts["rows_readback_mismatch"] += 1
                    rec.update(status="not_entered", reason="Smokeball did not show it as entered when checked")
                else:
                    rec.update(status="not_entered", reason="the tab could not be updated")
            if status == "written" and not self._refresh_summary(mid, pidx, res, plan["offers"]):
                for rec in plan["offers"]:
                    if rec.get("plaintiff_index") == pidx and rec.get("status") == "written":
                        rec["summary_stale"] = True
        for rec in plan["offers"]:
            if rec.get("status") == "to_write":
                rec.update(status="not_entered", reason="the tab could not be updated")
            if rec.get("status") == "possible_duplicate":
                rec["status"] = "not_entered"

    def _refresh_summary(self, mid: str, pidx: int, res: dict[str, Any], offers: list[dict[str, Any]]) -> bool:
        """Bring an Operator-written summary in line with the tab after the
        write (summary.py). False only when such a summary was left stale: a
        firm-written summary is never touched, and that is not a failure."""
        view = res.get("negotiation") or {}
        accepted = {
            int(r["row"]): r["accepted"]
            for r in offers
            if r.get("plaintiff_index") == pidx and r.get("status") == "written" and r.get("accepted") is not None
        }
        new = summary_mod.refreshed(view.get("details"), view.get("rows") or [], accepted)
        if new is None:
            return True
        try:
            got = self.layout.refresh_operator_details(mid, new, view.get("details"), plaintiff_index=pidx)
        except Exception as exc:  # noqa: BLE001 - the rows are in; a stale summary is reported, never a crash
            got = {"status": "refused", "reason": type(exc).__name__}
        status = got.get("status") if isinstance(got, dict) else "refused"
        if status in ("written", "nothing_to_write"):
            return True
        self.log(f"negotiation: the tab summary was not updated ({status})")
        return False

    def _notice(
        self, matter: dict[str, Any], doc: dict[str, Any], rec: dict[str, Any], tabs: list[dict[str, Any]]
    ) -> None:
        if rec.get("status") == "already_present":
            return
        if rec.get("accepted") is not None:
            # The firm asked to hear "when new offer is received" so an attorney
            # can act. An offer the same document shows was already accepted
            # (Peschke's 9/23 tender, accepted and paid) is entered, not sent.
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

    def _quiet(self) -> dict[str, list[str]]:
        return _load(self.state / "seed-quiet.json", {})

    def _set_quiet(self, value: dict[str, list[str]]) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        _dump(self.state / "seed-quiet.json", {k: v for k, v in value.items() if v})

    def _uncovered(self, mid: str) -> bool:
        """True when no Negotiation Details tab on the matter holds an offer
        and the Operator may write one: what the file says about offers has
        never been entered. A failed read is NOT uncovered (never a flood)."""
        try:
            tabs = self._tabs(mid)
        except Exception as exc:  # noqa: BLE001 - "could not look" must not read as "nothing there"
            self.log(f"negotiation: a seeded matter's tabs could not be read ({type(exc).__name__})")
            return False
        if not tabs or not any(rows_mod.operator_kept(t) for t in tabs):
            return False
        return not any(r.get("offer_amount") or r.get("offer_date") for t in tabs for r in t["rows"])

    def _seed(self, mid: str, files: list[dict[str, Any]]) -> dict[str, str]:
        """A matter the lane has never seen. Files saved before
        ``seed_saved_before`` (all of them when it is unauthored) are what an
        earlier fill already read, and are taken as handled: nothing read,
        nothing emailed. Files saved after it are new like any other.

        EXCEPT when the matter's Negotiation Details hold no offer at all
        (2026-10-12: a carrier's tender emailed a month before the first run
        was taken as handled and never entered). Then the earlier offer-named
        files are read too, and their offers ENTERED QUIETLY: written to the
        tab, never emailed as new (they are not new; the firm has had them)."""
        self.counts["matters_seeded"] += 1
        cutoff = self.job.seed_saved_before
        handled = [f for f in files if cutoff is None or not _saved_on_or_after(f, cutoff)]
        early = [f for f in handled if select(f)]
        if early and self._uncovered(mid):
            quiet = self._quiet()
            quiet[mid] = sorted({*quiet.get(mid, []), *(str(f["id"]) for f in early)})
            self._set_quiet(quiet)
            ids = {str(f["id"]) for f in early}
            handled = [f for f in handled if str(f["id"]) not in ids]
        arrivals.commit(LANE, mid, handled, data=self.state)
        return arrivals.manifest_of(handled)

    def matter(self, matter: dict[str, Any]) -> None:
        mid = matter["id"]
        files = arrivals.list_files(self.seat, mid)
        prior = arrivals.cursor(LANE, mid, data=self.state)
        if prior is None:
            prior = self._seed(mid, files)
        new = [f for f in arrivals.new_since(files, prior) if select(f)]
        quiet = set(self._quiet().get(mid, []))
        failed: set[str] = set()
        if new and not self.stopped:
            tabs = self._tabs(mid)
            attempts = self._attempts()
            for f in new:
                fid = str(f["id"])
                doc = {"name": full_name(f), "ext": extension(f), "saved": str(f.get("modified") or "")[:10]}
                try:
                    text = self._text(mid, f)
                    events = read_mod.read_document(self.doorway, doc, text, [r for t in tabs for r in t["rows"]], tabs)
                    checked = offer_check.screen(self.doorway, doc, text, events, self._tokens(tabs), fid)
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
                self.counts["offers_reviewed"] += checked["reviewed"]
                plan = rows_mod.plan_document(checked["events"], doc, tabs, self._tokens(tabs))
                plan["held"] = list(plan.get("held") or []) + checked["held"]
                self._planned(matter, f, events, plan, tabs)
                self._write(mid, plan, tabs)
                if self._hold(matter, f, doc, plan, attempts):
                    failed.add(fid)
                for rec in plan["offers"]:
                    if fid in quiet:
                        # A seeded matter's earlier offer, entered so the tab is
                        # true; the firm has had it, so it is not "new".
                        self.counts["seed_offers_entered"] += rec.get("status") == "written"
                        continue
                    self._notice(matter, doc, rec, tabs)
                if any(tp.args for tp in plan["tabs"].values()):
                    tabs = self._tabs(mid)
            self._set_attempts(attempts)
        elif new:
            failed.update(str(f["id"]) for f in new)
        rows = [f for f in files if str(f.get("id")) not in failed]
        rows += [{"id": fid, "modified": prior[fid]} for fid in failed if fid in prior]
        arrivals.commit(LANE, mid, rows, data=self.state)
        if quiet:
            left = self._quiet()
            left[mid] = sorted(quiet & failed)
            self._set_quiet(left)

    def _hold(
        self, matter: dict[str, Any], f: dict[str, Any], doc: dict[str, Any], plan: dict[str, Any], attempts: dict
    ) -> bool:
        """Record what a document showed but the run did not enter or email.
        True when the document must stay NEW: a row the pre-write checks held
        (``offer_check``) is read again next run, up to ``MAX_ATTEMPTS``; after
        the last try the hold is kept in the lane's state for SMD."""
        self.counts["events_excluded"] += len(plan.get("excluded") or [])
        reviewed: list[dict[str, Any]] = []
        for h in plan.get("held") or []:
            e = h["event"]
            rec = {
                "matter_number": matter.get("number") or "",
                "file_id": str(f.get("id")),
                "file": doc["name"],
                "by": str(e.get("by") or ""),
                "kind": str(e.get("kind") or ""),
                "amount": e.get("amount"),
                "date": e.get("date"),
                "reason": h["reason"],
            }
            if h.get("by") in ("code", "review"):
                self.counts["offers_held_review"] += 1
                rec["held_by"] = h["by"]
                reviewed.append(rec)
                self.log(f"negotiation: a row was held by the pre-write {h['by']} check")
            else:
                self.counts["offers_held"] += 1
                self.log("negotiation: an offer with no date was held from the notice")
            self.held.append(rec)
        if not reviewed:
            return False
        key = f"held:{f.get('id')}"
        attempts[key] = attempts.get(key, 0) + 1
        retry = attempts[key] < MAX_ATTEMPTS
        for rec in reviewed:
            rec["retry"] = retry
        if not retry:
            attempts.pop(key, None)
            offer_check.remember(self.state, reviewed)
            self.counts["offers_held_final"] += sum(offer_check.pages_smd(rec) for rec in reviewed)
        return retry

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
            "held": list(self.held),
        }
        if verdict == "delivered":
            out["proof"] = offer_check.proof(self.counts, self.budget.by_stage())
        if reason:
            out["reason"] = str(reason)[:500]
        return out


__all__ = ["EXIT", "LANE", "STATE_ENV", "NegotiationRun", "default_state_dir"]
