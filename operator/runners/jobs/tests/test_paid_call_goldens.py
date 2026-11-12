"""Every paid lane's model requests, receipts and limit decisions, against goldens.

Wave 2 (job kit, Phase A, A3) moves seven lanes' paid-session setup into one
kit. This is the instrument that proves the move changed nothing a model is
asked or a firm is billed. Each case runs one EXISTING scripted-client test of
a lane, unchanged, with a recorder wrapped around the model client:

* every request the doorway sends (``llm.bounded_call`` and the Batch API's
  ``batches.create``): the stage, model id, max tokens, stream or batch, tool
  choice, tool names, effort and output format, thinking, the paths that carry a
  cache breakpoint, and a sha256 of system + messages (scrubbed of the run's
  temp dir and today's date, page images and documents reduced to their media type);
* every request the records lane's page finder sends at its HTTP seam, the same way;
* every usage-ledger row the run wrote (minus its timestamp);
* every limit or budget decision (``Limits.*``, ``Budget.check``) with its
  arguments and either ``pass`` or the hold's setting and sentence;
* every money figure in the run's result (cents, dollars).

``goldens/paid_calls/<case>.jsonl`` holds one event per line. A change to any of
it fails, except the dotted paths ``allowed-changes.json`` names for that case.
``JOBS_WRITE_KIT_GOLDENS=1`` rewrites the goldens (then read the diff).

Two more goldens pin the facts the kit will centralise: the cost function over
a usage grid (``cost-grid.json``), every limit sentence over a grid of inputs
(``limits-grid.json``); ``test_model_fact_goldens.py`` pins the model facts.
"""

from __future__ import annotations

import inspect
import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

import pytest

import goldens_kit as gk
from jobs import budget as budget_mod, limits as limits_mod, llm

FAMILY = "paid_calls"
DIR = gk.GOLDENS / FAMILY
ALLOWED = gk.allowed(FAMILY)

_TODAY_FORMATS = (
    "%B %d, %Y",
    "%b %d, %Y",
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%m/%d/%y",
    "%m-%d-%y",
)


def _today_forms() -> list[str]:
    now = time.localtime()
    out = {time.strftime(f, now) for f in _TODAY_FORMATS}
    # The unpadded forms (``11/2/26``) without a platform-specific strftime flag.
    m, d, y = now.tm_mon, now.tm_mday, now.tm_year
    out |= {f"{m}/{d}/{y}", f"{m}/{d}/{y % 100:02d}", f"{m}-{d}-{y % 100:02d}", f"{time.strftime('%B', now)} {d}, {y}"}
    return sorted(out, key=len, reverse=True)


class Tape:
    """What one run asked, recorded, and decided."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = str(tmp)
        self.today = _today_forms()
        self.lock = threading.Lock()
        self.local = threading.local()
        self.requests: list[dict[str, Any]] = []
        self.limits: list[dict[str, Any]] = []
        self.results: list[Any] = []

    # ---- scrubbing ---------------------------------------------------------
    def scrub(self, text: str) -> str:
        text = text.replace(self.tmp, "<TMP>")
        for form in self.today:
            text = text.replace(form, "<TODAY>")
        return text

    def body(self, x: Any) -> Any:
        """System or messages as plain JSON with page images and documents
        reduced to their media type (their bytes carry a PDF's creation stamp and a
        platform's PNG compression)."""
        if isinstance(x, dict):
            if x.get("type") == "base64" and "data" in x:
                return {**{k: v for k, v in x.items() if k != "data"}, "data": "<base64>"}
            return {k: self.body(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [self.body(v) for v in x]
        if isinstance(x, str):
            return self.scrub(x)
        p = gk.plain(x)
        return self.body(p) if isinstance(p, (dict, list)) else p

    # ---- the events --------------------------------------------------------
    def request(self, params: dict[str, Any], *, stream: bool, batch: bool, stage: str | None, seam: str) -> None:
        oc = params.get("output_config") or {}
        fmt = oc.get("format") if isinstance(oc, dict) else None
        tools = params.get("tools") or []
        known = {"model", "max_tokens", "system", "messages", "output_config", "tools", "tool_choice", "thinking"}
        ev = {
            "event": "request",
            "seam": seam,
            "stage": stage,
            "model": params.get("model"),
            "max_tokens": params.get("max_tokens"),
            "stream": stream,
            "batch": batch,
            "tool_choice": gk.plain(params.get("tool_choice")),
            "tools": [t.get("name") if isinstance(t, dict) else gk.plain(t) for t in tools],
            "tools_sha256": gk.sha(gk.canon(tools)) if tools else None,
            "effort": oc.get("effort") if isinstance(oc, dict) else None,
            "output_format": (fmt or {}).get("type") if isinstance(fmt, dict) else None,
            "output_format_sha256": gk.sha(gk.canon(fmt)) if fmt else None,
            "thinking": gk.plain(params.get("thinking")),
            "cache_breakpoints": _cache_paths({"system": params.get("system"), "messages": params.get("messages")}),
            "body_sha256": gk.sha(
                json.dumps(
                    {"system": self.body(params.get("system")), "messages": self.body(params.get("messages"))},
                    sort_keys=True,
                    ensure_ascii=False,
                )
            ),
            "other_keys": sorted(set(params) - known),
        }
        with self.lock:
            self.requests.append(ev)

    def limit(self, what: str, args: dict[str, Any], outcome: dict[str, Any]) -> None:
        ev = {"event": "limit", "check": what, "args": gk.plain(args), **outcome}
        with self.lock:
            self.limits.append(ev)


def _cache_paths(x: Any, path: str = "") -> list[str]:
    if isinstance(x, dict):
        out = [path or "<root>"] if "cache_control" in x else []
        for k, v in x.items():
            if k != "cache_control":
                out += _cache_paths(v, f"{path}.{k}" if path else str(k))
        return out
    if isinstance(x, (list, tuple)):
        return [p for i, v in enumerate(x) for p in _cache_paths(v, f"{path}.{i}")]
    return []


# ---- the recorder ---------------------------------------------------------------
class _BatchTap:
    def __init__(self, real: Any, tape: Tape) -> None:
        self._real, self._tape = real, tape

    def create(self, *, requests: list[dict[str, Any]], **kw: Any) -> Any:
        for r in requests:
            self._tape.request(
                r["params"], stream=False, batch=True, stage=getattr(self._tape.local, "stage", None), seam="batch"
            )
        return self._real.create(requests=requests, **kw)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class _MessagesTap:
    def __init__(self, real: Any, tape: Tape) -> None:
        self._real, self._tape = real, tape

    @property
    def batches(self) -> Any:
        return _BatchTap(self._real.batches, self._tape)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class _ClientTap:
    def __init__(self, real: Any, tape: Tape) -> None:
        self._real, self._tape = real, tape

    @property
    def messages(self) -> Any:
        return _MessagesTap(self._real.messages, self._tape)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def _limit_wrapper(tape: Tape, what: str, real: Callable[..., Any]) -> Callable[..., Any]:
    params = list(inspect.signature(real).parameters)[1:]

    def wrapped(self: Any, *a: Any, **kw: Any) -> Any:
        args = {**dict(zip(params, a)), **kw}
        state = {k: getattr(self, k, None) for k in ("cap_usd", "monthly_budget_usd", "month_cents_used")}
        try:
            out = real(self, *a, **kw)
        except (limits_mod.LimitHold, budget_mod.BudgetError) as exc:
            setting = getattr(exc, "setting", None)
            tape.limit(what, {**args, "state": state}, {"outcome": "hold", "setting": setting, "text": str(exc)})
            raise
        tape.limit(what, {**args, "state": state}, {"outcome": "pass"})
        return out

    return wrapped


def install(mp: pytest.MonkeyPatch, tape: Tape, run_classes: list[type]) -> None:
    real_call, real_batch, real_bounded, real_client = (
        llm.Doorway.call,
        llm.Doorway.batch_call,
        llm.bounded_call,
        llm.Doorway._client,
    )

    def staged(real: Callable[..., Any]) -> Callable[..., Any]:
        def wrapped(self: Any, stage: str, *a: Any, **kw: Any) -> Any:
            prev = getattr(tape.local, "stage", None)
            tape.local.stage = stage
            try:
                return real(self, stage, *a, **kw)
            finally:
                tape.local.stage = prev

        return wrapped

    def bounded(client: Any, params: dict[str, Any], *, stream: bool, **kw: Any) -> Any:
        tape.request(params, stream=stream, batch=False, stage=getattr(tape.local, "stage", None), seam="doorway")
        return real_bounded(client, params, stream=stream, **kw)

    mp.setattr(llm.Doorway, "call", staged(real_call))
    mp.setattr(llm.Doorway, "batch_call", staged(real_batch))
    mp.setattr(llm, "bounded_call", bounded)
    mp.setattr(llm.Doorway, "_client", lambda self, timeout: _ClientTap(real_client(self, timeout), tape))
    for name in ("check_before_first_paid", "check_before_paid", "check_each_call"):
        mp.setattr(limits_mod.Limits, name, _limit_wrapper(tape, f"Limits.{name}", getattr(limits_mod.Limits, name)))
    mp.setattr(budget_mod.Budget, "check", _limit_wrapper(tape, "Budget.check", budget_mod.Budget.check))
    for cls in run_classes:
        real_run = cls.run

        def run(self: Any, *a: Any, _real: Callable[..., Any] = real_run, **kw: Any) -> Any:
            out = _real(self, *a, **kw)
            tape.results.append(out)
            return out

        mp.setattr(cls, "run", run)


# ---- what a run leaves --------------------------------------------------------
_MONEY_KEY = re.compile(r"(?i)cents|dollars|usd")


def _money(x: Any, path: str = "") -> list[list[Any]]:
    out: list[list[Any]] = []
    if isinstance(x, dict):
        for k, v in sorted(x.items()):
            sub = f"{path}.{k}" if path else k
            if _MONEY_KEY.search(k) and isinstance(v, (int, float)) and not isinstance(v, bool):
                out.append([sub, round(v, 6)])
            else:
                out += _money(v, sub)
    elif isinstance(x, list):
        for i, v in enumerate(x):
            out += _money(v, f"{path}.{i}" if path else str(i))
    return out


def _result(x: Any) -> Any:
    to_json = getattr(x, "to_json", None)
    if callable(to_json):
        got = to_json()
        return json.loads(got) if isinstance(got, str) else gk.plain(got)
    return gk.plain(x)


def _ledgers(tape: Tape, root: Path) -> list[dict[str, Any]]:
    out = []
    for p in sorted(root.rglob("usage-ledger.jsonl")):
        rel = tape.scrub(str(p.relative_to(root)))
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            row = json.loads(line)
            row.pop("ts", None)
            out.append({"event": "ledger", "ledger": rel, "row": n, **row})
    return out


def _events(tape: Tape, root: Path, ordered: bool) -> list[dict[str, Any]]:
    """The run's events. A lane whose reads run on worker threads interleaves
    them differently each run, so its requests and decisions are compared as a
    sorted multiset (``ordered`` False); the others in the order they happened."""

    def keyed(evs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if ordered:
            return evs
        for e in evs:
            # The spend a per-call check sees depends on which worker's receipt
            # landed first; a PASS's figure is interleaving, a hold's is kept.
            if e.get("check") == "Limits.check_each_call" and e.get("outcome") == "pass":
                e["args"]["spent_usd"] = "<interleaved>"
        return sorted(evs, key=lambda e: json.dumps(e, sort_keys=True))

    ledgers = _ledgers(tape, root)
    if not ordered:
        for row in ledgers:
            row.pop("row")
        ledgers = keyed(ledgers)
    results = [_result(r) for r in tape.results]
    money = {"event": "final", "runs": len(results), "money": [_money(r) for r in results]}
    return keyed(tape.requests) + keyed(tape.limits) + ledgers + [money]


# ---- the cases: each lane's existing scripted-client test, unchanged ---------------
def _drafting(tmp: Path, mp: pytest.MonkeyPatch, rq: Any) -> None:
    import test_drafting_run as t

    inspect.unwrap(t._checker)(mp)
    t.test_a_mediation_brief_runs_pull_to_read_back(tmp, inspect.unwrap(t.pricing)(tmp))


def _demand(tmp: Path, mp: pytest.MonkeyPatch, rq: Any) -> None:
    import test_demand as t

    inspect.unwrap(t._checker)(mp)
    t.test_a_demand_runs_pull_to_read_back_and_files_both_deliverables(tmp, inspect.unwrap(t.pricing)(tmp))


def _demand_estimate_hold(tmp: Path, mp: pytest.MonkeyPatch, rq: Any) -> None:
    import test_demand as t

    inspect.unwrap(t._checker)(mp)
    t.test_the_estimate_holds_before_anything_is_paid(tmp, inspect.unwrap(t.pricing)(tmp))


def _litigation(tmp: Path, mp: pytest.MonkeyPatch, rq: Any) -> None:
    import test_litigation_run as t

    inspect.unwrap(t._pricing)(tmp, rq.getfixturevalue("pricing_path"))
    t.test_a_full_run_delivers_a_read_back_workbook_and_commits_state(tmp)


def _litigation_cap(tmp: Path, mp: pytest.MonkeyPatch, rq: Any) -> None:
    import test_litigation_run as t

    inspect.unwrap(t._pricing)(tmp, rq.getfixturevalue("pricing_path"))
    t.test_the_cap_stops_the_run_inside_a_read(tmp)


def _casefile(tmp: Path, mp: pytest.MonkeyPatch, rq: Any) -> None:
    import test_casefile_run as t

    t.test_full_job_delivers_and_proposes_only_what_passes(
        inspect.unwrap(t.env)(tmp, mp, rq.getfixturevalue("pricing_path"))
    )


def _negotiation(tmp: Path, mp: pytest.MonkeyPatch, rq: Any) -> None:
    import test_negotiation_proof as t

    t.test_a_clean_offer_is_written_reviewed_and_proven(tmp, mp)


def _records_with(test: str) -> Callable[[Path, pytest.MonkeyPatch, Any], list[Any]]:
    def case(tmp: Path, mp: pytest.MonkeyPatch, rq: Any) -> list[Any]:
        import test_records_run as t

        model = t.FakeFinderModel()
        env = inspect.unwrap(t.env)(tmp, mp, model)
        getattr(t, test)(tmp, env, model)
        return list(model.bodies)

    return case


def _medchron(tmp: Path, mp: pytest.MonkeyPatch, rq: Any) -> None:
    import test_decisions_driver as t

    t.test_the_whole_dag_runs_in_process_without_the_frozen_pipeline(
        tmp,
        rq.getfixturevalue("data_root"),
        rq.getfixturevalue("firm_config_path"),
        rq.getfixturevalue("pricing_path"),
        mp,
    )


def _medchron_update(tmp: Path, mp: pytest.MonkeyPatch, rq: Any) -> None:
    import test_update_e2e as t

    data_root = rq.getfixturevalue("data_root")
    scene = inspect.unwrap(t.scene)(tmp, data_root)
    t.test_an_update_files_one_complete_document_into_the_delivered_folder(
        scene, data_root, rq.getfixturevalue("firm_config_path"), rq.getfixturevalue("pricing_path"), mp, tmp
    )


def _run_classes() -> dict[str, list[type]]:
    from jobs.casefile.run import CasefileRun
    from jobs.demand.run import DemandRun
    from jobs.drafting.run import DraftingRun
    from jobs.litigation.run import LitigationRun
    from jobs.medchron.driver import Driver
    from jobs.negotiation.run import NegotiationRun
    from jobs.records.run import RecordsRun

    return {
        "casefile": [CasefileRun],
        "demand": [DemandRun],
        "drafting": [DraftingRun],
        "litigation": [LitigationRun],
        "medchron": [Driver],
        "negotiation": [NegotiationRun],
        "records": [RecordsRun],
    }


#: case -> (lane, the case, whether its events are compared in order)
CASES: dict[str, tuple[str, Callable[..., Any], bool]] = {
    "casefile": ("casefile", _casefile, False),
    "demand": ("demand", _demand, False),
    "demand_estimate_hold": ("demand", _demand_estimate_hold, True),
    "drafting": ("drafting", _drafting, False),
    "litigation": ("litigation", _litigation, False),
    "litigation_cap": ("litigation", _litigation_cap, False),
    "medchron": ("medchron", _medchron, False),
    "medchron_update": ("medchron", _medchron_update, False),
    "negotiation": ("negotiation", _negotiation, True),
    "records": ("records", _records_with("test_a_whole_request_delivers_both_records_with_code_written_facts"), False),
    "records_cap": ("records", _records_with("test_a_cap_reached_mid_read_holds_with_a_words_only_reply"), False),
}


def record_case(case: str, tmp: Path, mp: pytest.MonkeyPatch, rq: Any) -> list[dict[str, Any]]:
    lane, fn, ordered = CASES[case]
    tape = Tape(tmp)
    install(mp, tape, _run_classes()[lane])
    finder = fn(tmp, mp, rq)
    for body in finder or []:
        tape.request(body, stream=False, batch=False, stage="page_finder", seam="http")
    return _events(tape, tmp, ordered)


def test_every_golden_has_a_case_and_an_allowed_list() -> None:
    files = {p.stem for p in DIR.glob("*.jsonl")}
    assert files == set(CASES) and set(CASES) <= set(ALLOWED) - {"_why"}


@pytest.mark.parametrize("case", sorted(CASES))
def test_a_lanes_paid_calls_match_the_golden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: Any, case: str):
    """MUTATION: change a model id, max_tokens, effort, tool choice, a cache
    breakpoint, a prompt byte, a ledger row, a limit sentence or a cents figure
    in any of these lanes and its case fails, naming the path."""
    got = record_case(case, tmp_path, monkeypatch, request)
    assert any(e["event"] == "final" and e["runs"] for e in got), "the lane's run never returned"
    bad = gk.write_or_compare_jsonl(DIR / f"{case}.jsonl", got, ALLOWED[case])
    assert bad == [], f"{case}: {bad[:20]}"


# ---- the cost function and the limit sentences over grids ----------------------
def _pricing(path: Path) -> budget_mod.Pricing:
    return budget_mod.Pricing.load(path)


def cost_grid(card: Path) -> dict[str, Any]:
    """``budget.Pricing.price_row`` (what every lane's cap and receipt is
    priced with) over a usage grid, for every model the shipped card prices,
    plus one unknown id (its refusal text)."""
    p = _pricing(card)
    grid = []
    usages = [
        {"in": 1_000_000},
        {"out": 1_000_000},
        {"cache_read": 1_000_000},
        {"cache_write": 1_000_000},
        {"in": 1234, "out": 567, "cache_read": 8910, "cache_write": 1112},
        {"in": 1234, "out": 567, "cache_read": 8910, "cache_write": 1112, "batch": True},
        {"in": 0, "out": 0},
    ]
    for model in [*sorted(p.rates), "claude-haiku-4-5-20251001", "claude-opus-5-5-20261201", "claude-unknown-9"]:
        for u in usages:
            row = {"model": model, **u}
            try:
                grid.append({"row": row, "usd": round(p.price_row(row), 9)})
            except budget_mod.BudgetError as exc:
                grid.append({"row": row, "error": str(exc).replace(str(card), "<CARD>")})
    return {"card_multipliers": [p.batch_multiplier, p.cache_write_multiplier, p.cache_read_multiplier], "grid": grid}


def limits_grid() -> list[dict[str, Any]]:
    """Every sentence ``limits.Limits`` can hold with, over a grid of inputs."""
    out: list[dict[str, Any]] = []
    for month_cents, remaining in ((None, None), (0, 100), (9_000, 5), (10_000, 0)):
        lim = limits_mod.Limits(
            cap_usd=10.0,
            monthly_budget_usd=100.0,
            usd_per_scanned_page=0.03,
            usd_per_audit_claim=0.06,
            month_cents_used=month_cents,
            allowance_remaining_pages=remaining,
            allowance_month="2026-09" if remaining is not None else None,
            allowance_cycle_label="the cycle ending Oct 14" if month_cents == 9_000 else None,
        )
        calls: list[tuple[str, dict[str, Any]]] = []
        for spent in (0.0, 5.0, 10.0, 95.0):
            calls.append(("check_each_call", {"spent_usd": spent, "stage": "compose"}))
            for projected in (0.0, 4.0, 6.0):
                for batch in (False, True):
                    calls.append(
                        (
                            "check_before_paid",
                            {"projected_usd": projected, "spent_usd": spent, "stage": "vision", "batch": batch},
                        )
                    )
                for pages, approved in ((10, 0), (200, 0), (200, 150)):
                    calls.append(
                        (
                            "check_before_first_paid",
                            {
                                "pages": pages,
                                "projected_usd": projected,
                                "spent_usd": spent,
                                "stage": "vision",
                                "approved_pages": approved,
                            },
                        )
                    )
        for name, kw in calls:
            try:
                getattr(lim, name)(**kw)
                res: dict[str, Any] = {"outcome": "pass"}
            except limits_mod.LimitHold as h:
                res = {"outcome": "hold", "setting": h.setting, "text": h.reason, "allowance": h.allowance}
            out.append(
                {
                    "month_cents_used": month_cents,
                    "allowance_remaining_pages": remaining,
                    "call": name,
                    "args": kw,
                    **res,
                }
            )
    return out


def _shipped_card() -> Path:
    return Path(__file__).resolve().parents[3] / "adapter" / "cost_telemetry" / "anthropic_pricing.json"


def test_the_cost_function_over_a_usage_grid_matches_the_golden() -> None:
    bad = gk.write_or_compare(DIR / "cost-grid.json", cost_grid(_shipped_card()), ALLOWED["cost-grid"])
    assert bad == [], bad[:20]


def test_every_limit_sentence_matches_the_golden() -> None:
    bad = gk.write_or_compare(DIR / "limits-grid.json", limits_grid(), ALLOWED["limits-grid"])
    assert bad == [], bad[:20]


# ---- the instrument can fail ------------------------------------------------------
def test_a_planted_effort_change_fails_the_chronology_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: Any
) -> None:
    """FALSIFIER: the audit stage's default effort removed (``llm.EFFORT_DEFAULTS``),
    and the chronology's recorded requests no longer match their golden."""
    monkeypatch.setattr(llm, "EFFORT_DEFAULTS", {})
    got = record_case("medchron", tmp_path, monkeypatch, request)
    bad = gk.compare_jsonl(DIR / "medchron.jsonl", got)
    assert bad and any(p.endswith(".effort") for p in bad), bad


def test_a_planted_limit_sentence_change_fails_the_limits_grid(monkeypatch: pytest.MonkeyPatch) -> None:
    """FALSIFIER: the monthly budget's setting name changed, and the limit
    sentences no longer match their golden. (In the held litigation case the
    lane's own test already refuses this change before the recorder compares.)"""
    monkeypatch.setattr(limits_mod, "BUDGET_SETTING", "monthly_budget")
    bad = gk.compare(DIR / "limits-grid.json", limits_grid())
    assert any(p.endswith((".text", ".setting")) for p in bad), bad[:5]


def test_a_planted_price_change_fails_the_cost_grid(monkeypatch: pytest.MonkeyPatch) -> None:
    """FALSIFIER: the batch discount priced as cache reads are, and the cost grid moves."""
    real = budget_mod.Pricing.load

    def load(path: Path) -> budget_mod.Pricing:
        p = real(path)
        return budget_mod.Pricing(
            p.rates, p.cache_read_multiplier, p.cache_write_multiplier, p.cache_read_multiplier, p.source
        )

    monkeypatch.setattr(budget_mod.Pricing, "load", staticmethod(load))
    assert any(p.endswith(".usd") for p in gk.compare(DIR / "cost-grid.json", cost_grid(_shipped_card())))
