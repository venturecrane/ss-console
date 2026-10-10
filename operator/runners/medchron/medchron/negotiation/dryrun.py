"""`medchron negotiate-dry-run <workdir>`: the whole negotiation watch on the
firm's real data, stopping before any write or email.

It runs exactly what a first live run runs: the real matter listing, the real
first-run seeding with ``seed_saved_before`` (into a THROWAWAY cursor under
``<workdir>/state``, never the lane's), the real selection, the real text
reads (vision included) and model extraction, and the real row planning
against each matter's current Negotiation Details. Then it stops: the
connector's ``add_negotiation_rows`` is never called (the layout handle it
holds refuses it), no notice reaches a broker (there is no broker on this
path), and nothing is emailed.

It writes ``<workdir>/dry-run-report.json`` and a plain-text twin, one entry
per candidate document: the matter number, the file name, its dateCreated,
the extracted events, the planned row for each (or ``already_present``,
``possible_duplicate``, a firm-kept-tab skip, a full tab), and the exact email
text the live run would send for it. Stdout carries one JSON summary line.

It is not a mode of the lane's ``negotiate`` command on purpose: that
command's verdict goes to the broker, which turns each notice into an email.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import rows as rows_mod
from .run import NegotiationRun, default_state_dir
from .select import full_name

REPORT = "dry-run-report.json"
REPORT_TEXT = "dry-run-report.txt"


class DryRunWriteRefused(RuntimeError):
    """A write was attempted on the dry-run path."""


class _ReadOnlyLayout:
    """The connector's layout tools with the write removed."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def get_matter_layouts(self, *a: Any, **k: Any) -> Any:
        return self._inner.get_matter_layouts(*a, **k)

    def add_negotiation_rows(self, *a: Any, **k: Any) -> Any:
        raise DryRunWriteRefused("the dry run never writes Negotiation Details")


def _unsafe_state(state: Path) -> bool:
    """True when ``state`` is, or sits inside, the lane's real state dir."""
    real = default_state_dir().resolve()
    here = state.resolve()
    return here == real or real in here.parents


class NegotiationDryRun(NegotiationRun):
    def __init__(self, job_dir: Path, *, only_matters: list[str] | None = None, **kw: Any) -> None:
        self.only = set(only_matters or [])
        state = Path(kw.pop("state_dir", None) or Path(job_dir) / "state")
        if _unsafe_state(state):
            raise DryRunWriteRefused(f"{state} is the lane's real cursor; a dry run uses a throwaway dir")
        super().__init__(job_dir, state_dir=state, **kw)
        self.entries: list[dict[str, Any]] = []
        self._open: dict[str, Any] | None = None
        #: matter id -> plaintiff index -> rows this dry run would have written,
        #: laid over each re-read so a later document on the same matter is
        #: planned the way a live run plans it (after the first one's write).
        self._sim: dict[str, dict[int, list[dict[str, Any]]]] = {}

    @property
    def layout(self) -> Any:
        return _ReadOnlyLayout(super().layout)

    def matter(self, matter: dict[str, Any]) -> None:
        """Every open matter, or only the ids/numbers asked for (a spot check)."""
        if self.only and str(matter.get("id")) not in self.only and str(matter.get("number")) not in self.only:
            return
        super().matter(matter)

    # -- attempts are not recorded: a dry run must not move a live run's retries --
    def _attempts(self) -> dict[str, int]:
        return {}

    def _set_attempts(self, value: dict[str, int]) -> None:
        return None

    # -- the observation hooks -------------------------------------------------------
    def _planned(self, matter: dict[str, Any], f: dict[str, Any], events: list, plan: dict, tabs: list) -> None:
        self._open = {
            "matter_number": matter.get("number") or "",
            "matter_title": matter.get("title") or "",
            "file": full_name(f),
            "file_id": str(f.get("id")),
            "date_created": f.get("created") or "",
            "events": events,
            "plan": plan,
            "tabs": tabs,
            "notice_from": len(self.notices),
        }
        self.entries.append(self._open)

    def _read_failed(self, matter: dict[str, Any], f: dict[str, Any], exc: Exception) -> None:
        self.entries.append(
            {
                "matter_number": matter.get("number") or "",
                "matter_title": matter.get("title") or "",
                "file": full_name(f),
                "file_id": str(f.get("id")),
                "date_created": f.get("created") or "",
                "read_error": f"{type(exc).__name__}: {str(exc)[:200]}",
            }
        )

    def _tabs(self, mid: str) -> list[dict[str, Any]]:
        tabs = super()._tabs(mid)
        for t in tabs:
            extra = self._sim.get(mid, {}).get(t["plaintiff_index"], [])
            if not extra:
                continue
            rows = {int(r["row"]): dict(r) for r in t["rows"]}
            for a in extra:
                rows.setdefault(int(a["row"]), {"row": int(a["row"])}).update(a)
            t["rows"] = [rows[n] for n in sorted(rows)]
            t["details"] = t.get("details") or "Entered (dry run)"
        return tabs

    def _write(self, mid: str, plan: dict[str, Any], tabs: list[dict[str, Any]]) -> None:
        """What the live write would report if every value read back: no call."""
        for pidx, tp in plan["tabs"].items():
            self._sim.setdefault(mid, {}).setdefault(pidx, []).extend(tp.args)
        for rec in plan["offers"]:
            if rec.get("status") == "to_write":
                rec["status"] = "written"
            elif rec.get("status") == "possible_duplicate":
                rec["status"] = "not_entered"

    # -- the report -------------------------------------------------------------------
    def report(self) -> list[dict[str, Any]]:
        out = []
        for i, e in enumerate(self.entries):
            if "read_error" in e:
                out.append(e)
                continue
            nxt = next((x["notice_from"] for x in self.entries[i + 1 :] if "notice_from" in x), len(self.notices))
            rows = [
                {"plaintiff_index": pidx, "row": a["row"], **{k: v for k, v in a.items() if k != "row"}}
                for pidx, tp in e["plan"]["tabs"].items()
                for a in tp.args
            ]
            details = [
                {"plaintiff_index": pidx, "details": rows_mod.details_for(tp.tab, self.today)}
                for pidx, tp in e["plan"]["tabs"].items()
                if tp.args and rows_mod.details_for(tp.tab, self.today)
            ]
            offers = [
                {k: rec.get(k) for k in ("status", "row", "reason", "date_only", "plaintiff_index", "joint")}
                | {"event": rec["event"]}
                for rec in e["plan"]["offers"]
            ]
            out.append(
                {
                    **{k: e[k] for k in ("matter_number", "matter_title", "file", "file_id", "date_created", "events")},
                    "rows_planned": rows,
                    "details_planned": details,
                    "offers": offers,
                    "emails": [n["text"] for n in self.notices[e["notice_from"] : nxt]],
                }
            )
        return out

    def run(self) -> dict[str, Any]:
        verdict = super().run()
        entries = self.report()
        summary = {
            "verdict": verdict["verdict"],
            "reason": verdict.get("reason"),
            "matters_total": verdict.get("matters_total"),
            "matters_seeded": verdict.get("matters_seeded"),
            "candidates": len(entries),
            "read_errors": sum(1 for e in entries if "read_error" in e),
            "rows_planned": sum(len(e.get("rows_planned") or []) for e in entries),
            "emails": sum(len(e.get("emails") or []) for e in entries),
            "cents": verdict.get("cents"),
            "dry_run": True,
        }
        out_dir = self.job.job_dir
        (out_dir / REPORT).write_text(json.dumps({"summary": summary, "entries": entries}, indent=1), encoding="utf-8")
        (out_dir / REPORT_TEXT).write_text(render_text(summary, entries), encoding="utf-8")
        return summary


def render_text(summary: dict[str, Any], entries: list[dict[str, Any]]) -> str:
    lines = [json.dumps(summary), ""]
    for e in entries:
        lines.append(f"== matter {e['matter_number']} | {e['file']} | created {e['date_created']}")
        if "read_error" in e:
            lines += [f"   READ ERROR: {e['read_error']}", ""]
            continue
        for ev in e["events"]:
            lines.append(
                "   event: {kind} by={by!r} amount={amount} confirmed={amount_confirmed} date={date} "
                "plaintiff={plaintiff_index} note={note!r}".format(**{k: ev.get(k) for k in rows_mod_keys()})
            )
        for r in e["rows_planned"]:
            lines.append(f"   would write: {r}")
        for d in e["details_planned"]:
            lines.append(f"   would set details: {d}")
        for o in e["offers"]:
            lines.append(f"   offer -> {o['status']} row={o.get('row')} reason={o.get('reason')!r}")
        for t in e["emails"]:
            lines += ["   --- email ---", *("   " + ln for ln in t.splitlines()), "   -------------"]
        lines.append("")
    return "\n".join(lines) + "\n"


def rows_mod_keys() -> tuple[str, ...]:
    return ("kind", "by", "amount", "amount_confirmed", "date", "plaintiff_index", "note")


def write_job(
    workdir: Path,
    *,
    slug: str,
    seed_saved_before: str,
    design: str,
    firm_words: list[str],
    statuses: list[str],
    cap_usd: float,
    job_id: str,
) -> Path:
    """The job.json a dry run reads (no broker on this path)."""
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "data").mkdir(exist_ok=True)
    doc = {
        "kind": "negotiation",
        "job_id": job_id,
        "slug": slug,
        "matter_statuses": statuses,
        "negotiation_design": design,
        "firm_words": firm_words,
        "seed_saved_before": seed_saved_before,
        "per_job_cap_usd": cap_usd,
        "monthly_budget_usd": cap_usd,
        "month_cents_used": 0,
    }
    (workdir / "job.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return workdir


__all__ = ["DryRunWriteRefused", "NegotiationDryRun", "render_text", "write_job"]
