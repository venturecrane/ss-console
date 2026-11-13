"""What each home of a model fact says about each model id, against goldens.

A model's facts (its output ceiling, whether it accepts a forced tool call,
its price, whether a firm file may name it) live in several homes today
(lanes review 2026-11-10, section 4, concept 22): ``demand.gapaudit.OUTPUT_MAX``
(read by drafting too), ``medchron.config.NO_FORCED_TOOL_MODELS`` (read through
the chronology validator), the rate cards and their two loaders, each lane's
firm validator, and model ids hard-coded in negotiation and litigation. Wave 2
gives them one home. Before it does, this file records what every home answers
for every id any of them names, by CALLING each home (never by reading its
source), so the move can be proven to change no answer.

``goldens/paid_calls/model-facts.json`` is the table. Where two homes disagree
about one id, ``model-facts-disagreements.json`` lists it. This file does not
reconcile them: wave 2 decides each one by name.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable

import goldens_kit as gk
from jobs import budget as budget_mod
from jobs_testkit import PRICING as TESTKIT_PRICING

DIR = gk.GOLDENS / "paid_calls"
ALLOWED = gk.allowed("paid_calls")
REPO_OPERATOR = Path(__file__).resolve().parents[3]
CARDS = {
    "adapter/cost_telemetry/anthropic_pricing.json": REPO_OPERATOR
    / "adapter"
    / "cost_telemetry"
    / "anthropic_pricing.json",
    "hermes-smd-overlay/shared/anthropic_pricing.json": REPO_OPERATOR
    / "hermes-smd-overlay"
    / "shared"
    / "anthropic_pricing.json",
}
#: Concrete members of the families the chronology's forced-tool rule matches by prefix.
FAMILY_MEMBERS = ("claude-fable-5", "claude-fable-5-1", "claude-fable-5-2", "claude-mythos-5-1")


def _cards(tmp: Path) -> dict[str, Path]:
    out = dict(CARDS)
    p = tmp / "testkit-pricing.json"
    p.write_text(json.dumps(TESTKIT_PRICING), encoding="utf-8")
    out["tests/jobs_testkit.PRICING"] = p
    return out


def _loaders() -> dict[str, Callable[[Path], Any]]:
    from jobs.drafting import evidence_cost

    return {
        "budget.Pricing.load": budget_mod.Pricing.load,
        "drafting.evidence_cost.load_pricing": evidence_cost.load_pricing,
    }


def _fixture_firms(tmp: Path) -> dict[str, tuple[Callable[[Any], list[str]], dict[str, Any], list[tuple[str, ...]]]]:
    """lane -> (its validator, the lane's fixture firm file as its test kit
    writes it, the model key paths it reads). Each fixture validates clean, so
    a problem a substituted id raises is that id's."""
    import yaml

    import casefile_testkit
    import demand_testkit
    import drafting_testkit
    import litigation_testkit
    from jobs.casefile import firm as cf
    from jobs.demand import firm as dm
    from jobs.drafting import firm as dr
    from jobs.litigation import firm as lt
    from jobs.medchron import config as mc
    from jobs.medchron.probes import SYNTHETIC_FIRM

    def keys(data: dict[str, Any], *prefix: str) -> list[tuple[str, ...]]:
        node: Any = data
        for p in prefix:
            node = node[p]
        return [(*prefix, k) for k in sorted(node) if isinstance(node[k], str)]

    def written(make: Callable[[Path], Path], lane: str, name: str) -> dict[str, Any]:
        root = make(tmp / "model-facts" / lane)
        return yaml.safe_load((root / name).read_text(encoding="utf-8"))

    out: dict[str, tuple[Callable[[Any], list[str]], dict[str, Any], list[tuple[str, ...]]]] = {}
    for lane, validate, data, prefix in (
        ("drafting", dr.validate, written(drafting_testkit.make_inputs, "drafting", dr.CONFIG_NAME), ("models",)),
        ("demand", dm.validate, written(demand_testkit.make_inputs, "demand", dm.CONFIG_NAME), ("models",)),
        ("casefile", cf.validate, written(casefile_testkit.make_inputs, "casefile", cf.CONFIG_NAME), ("models",)),
        ("litigation", lt.validate, written(litigation_testkit.make_inputs, "litigation", lt.CONFIG_NAME), ("models",)),
        ("medchron", mc.validate, copy.deepcopy(SYNTHETIC_FIRM), ("models", "tiers")),
    ):
        assert validate(data) == [], f"{lane}: the fixture firm does not validate clean: {validate(data)}"
        out[lane] = (validate, data, keys(data, *prefix))
    return out


def _recorded_requests() -> list[dict[str, Any]]:
    rows = []
    for p in sorted(DIR.glob("*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            ev = json.loads(line)
            if ev.get("event") == "request":
                rows.append({"case": p.stem, **ev})
    return rows


def _ids(tmp: Path) -> list[str]:
    from jobs.demand import gapaudit
    from jobs.litigation.discovery12 import run as d12
    from jobs.medchron import config as mc
    from jobs.negotiation import extract as nx, offer_check, run as nr

    ids: set[str] = set(gapaudit.OUTPUT_MAX)
    ids |= {m for m in mc.NO_FORCED_TOOL_MODELS if not m.endswith("-")} | set(FAMILY_MEMBERS)
    ids |= {nx.MODEL, nr.OCR_MODEL, offer_check.REVIEW_MODEL, d12.REFUSAL_RETRY_MODEL}
    for path in _cards(tmp).values():
        ids |= set(json.loads(path.read_text(encoding="utf-8")).get("models") or {})
    for _v, data, paths in _fixture_firms(tmp).values():
        for kp in paths:
            node: Any = data
            for k in kp:
                node = node[k]
            ids.add(str(node))
    ids |= {str(r["model"]) for r in _recorded_requests() if r.get("model")}
    return sorted(ids)


def _set(data: dict[str, Any], kp: tuple[str, ...], value: Any) -> dict[str, Any]:
    d = copy.deepcopy(data)
    node = d
    for k in kp[:-1]:
        node = node[k]
    node[kp[-1]] = value
    return d


def model_facts(tmp: Path) -> dict[str, Any]:
    from jobs.demand import gapaudit
    from jobs.litigation.discovery12 import run as d12
    from jobs.negotiation import extract as nx, offer_check, run as nr

    ids = _ids(tmp)
    cards, loaders, firms = _cards(tmp), _loaders(), _fixture_firms(tmp)
    loaded: dict[str, Any] = {}
    for cname, cpath in cards.items():
        for lname, load in loaders.items():
            try:
                loaded[f"{lname} x {cname}"] = load(cpath)
            except Exception as exc:  # noqa: BLE001 - a loader that refuses a card is a fact
                loaded[f"{lname} x {cname}"] = (
                    f"refused: {type(exc).__name__}: {str(exc).replace(str(cpath), '<CARD>')}"
                )
    facts: dict[str, Any] = {}
    for mid in ids:
        f: dict[str, Any] = {"output_max (demand.gapaudit.output_max, fallback None)": gapaudit.output_max(mid, None)}  # type: ignore[arg-type]
        price: dict[str, Any] = {}
        for key, p in loaded.items():
            if isinstance(p, str):
                price[key] = p
                continue
            try:
                r = p.rate_for(mid)
                price[key] = [r.input_per_million_cents, r.output_per_million_cents, r.cache_read_multiplier]
            except budget_mod.BudgetError:
                price[key] = None
        f["rate (input cents/M, output cents/M, own cache-read multiplier)"] = price
        verdicts: dict[str, Any] = {}
        for lane, (validate, data, paths) in firms.items():
            for kp in paths:
                problems = validate(_set(data, kp, mid))
                verdicts[f"{lane}:{'.'.join(kp)}"] = problems
        f["firm validators (every problem, when this id is set at the key of a clean fixture)"] = verdicts
        facts[mid] = f
    hard = {
        "negotiation.extract.MODEL": nx.MODEL,
        "negotiation.offer_check.REVIEW_MODEL": offer_check.REVIEW_MODEL,
        "negotiation.run.OCR_MODEL": nr.OCR_MODEL,
        "litigation.discovery12.run.REFUSAL_RETRY_MODEL": d12.REFUSAL_RETRY_MODEL,
    }
    sent: dict[str, Any] = {}
    for r in _recorded_requests():
        s = sent.setdefault(
            r["model"], {"cases": set(), "max_tokens_max": 0, "tool_choice_types": set(), "batch": False}
        )
        s["cases"].add(r["case"])
        s["max_tokens_max"] = max(s["max_tokens_max"], int(r.get("max_tokens") or 0))
        tc = r.get("tool_choice")
        s["tool_choice_types"].add(tc.get("type") if isinstance(tc, dict) else None)
        s["batch"] = s["batch"] or bool(r.get("batch"))
    return {
        "ids": ids,
        "facts": facts,
        "hard_coded": hard,
        "loaders": {k: (v if isinstance(v, str) else "loaded") for k, v in loaded.items()},
        "sent_in_paid_call_goldens": gk.plain(sent),
    }


def disagreements(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Every id where two homes answer differently. Listed, never reconciled."""
    out: list[dict[str, Any]] = []
    shipped = "budget.Pricing.load x adapter/cost_telemetry/anthropic_pricing.json"
    for mid, f in doc["facts"].items():
        ceiling = f["output_max (demand.gapaudit.output_max, fallback None)"]
        rates = f["rate (input cents/M, output cents/M, own cache-read multiplier)"]
        priced = rates.get(shipped)
        if ceiling is not None and priced is None:
            out.append(
                {
                    "id": mid,
                    "kind": "ceiling known, not priced",
                    "homes": ["demand.gapaudit.OUTPUT_MAX", "adapter rate card (budget.Pricing)"],
                    "detail": f"output ceiling {ceiling}; the shipped card's rate_for refuses it (no row)",
                }
            )
        if ceiling is None and priced is not None:
            out.append(
                {
                    "id": mid,
                    "kind": "priced, no ceiling",
                    "homes": ["adapter rate card (budget.Pricing)", "demand.gapaudit.OUTPUT_MAX"],
                    "detail": {
                        "output_ceiling": None,
                        "validators_refusing": sorted(
                            k
                            for k, v in f[
                                "firm validators (every problem, when this id is set at the key of a clean fixture)"
                            ].items()
                            if v
                        ),
                    },
                }
            )
        distinct = {json.dumps(v) for k, v in rates.items() if "jobs_testkit" not in k and not isinstance(v, str)}
        if len(distinct) > 1:
            out.append(
                {
                    "id": mid,
                    "kind": "rate cards or loaders disagree",
                    "homes": sorted(k for k in rates if "jobs_testkit" not in k),
                    "detail": {k: v for k, v in rates.items() if "jobs_testkit" not in k},
                }
            )
        verdicts = f["firm validators (every problem, when this id is set at the key of a clean fixture)"]
        by_lane: dict[str, bool] = {}
        for key, problems in verdicts.items():
            lane = key.split(":")[0]
            by_lane[lane] = by_lane.get(lane, True) and not problems
        if len(set(by_lane.values())) > 1:
            out.append(
                {
                    "id": mid,
                    "kind": "firm validators disagree",
                    "homes": sorted(by_lane),
                    "detail": {
                        "accepted_everywhere_in": sorted(k for k, v in by_lane.items() if v),
                        "refused_somewhere_in": {k: v for k, v in verdicts.items() if v},
                    },
                }
            )
        refuses_forced = any(
            "forced tool_choice" in p for k, v in verdicts.items() if k.startswith("medchron") for p in v
        )
        sent = doc["sent_in_paid_call_goldens"].get(mid)
        if sent:
            forced = [t for t in sent["tool_choice_types"]["__set"] if t in ("tool", "any")]
            if refuses_forced and forced:
                out.append(
                    {
                        "id": mid,
                        "kind": "forced tool sent to a model the rule says refuses one",
                        "homes": ["medchron.config.NO_FORCED_TOOL_MODELS", "recorded requests"],
                        "detail": {"cases": sent["cases"], "tool_choice_types": forced},
                    }
                )
            if ceiling is not None and sent["max_tokens_max"] > ceiling:
                out.append(
                    {
                        "id": mid,
                        "kind": "max_tokens above the ceiling",
                        "homes": ["demand.gapaudit.OUTPUT_MAX", "recorded requests"],
                        "detail": {
                            "ceiling": ceiling,
                            "max_tokens_max": sent["max_tokens_max"],
                            "cases": sent["cases"],
                        },
                    }
                )
    for key, mid in doc["hard_coded"].items():
        if (
            mid not in doc["facts"]
            or doc["facts"][mid]["rate (input cents/M, output cents/M, own cache-read multiplier)"].get(shipped) is None
        ):
            out.append(
                {"id": mid, "kind": "hard-coded id not priced", "homes": [key, "adapter rate card"], "detail": ""}
            )
    for key, verdict in doc["loaders"].items():
        if verdict != "loaded":
            out.append(
                {"id": None, "kind": "a loader refuses a card the other loads", "homes": [key], "detail": verdict}
            )
    return out


def test_model_facts_match_the_golden(tmp_path: Path) -> None:
    """MUTATION: add or change an output ceiling, a forced-tool prefix, a rate
    row, a hard-coded id or a validator's model rule and this fails."""
    doc = model_facts(tmp_path)
    bad = gk.write_or_compare(DIR / "model-facts.json", doc, ALLOWED["model-facts"])
    assert bad == [], bad[:20]


def test_the_model_fact_disagreements_match_the_golden(tmp_path: Path) -> None:
    """The listed disagreements are today's; wave 2 resolves each by name and
    says so in allowed-changes.json."""
    got = disagreements(model_facts(tmp_path))
    bad = gk.write_or_compare(DIR / "model-facts-disagreements.json", got, ALLOWED["model-facts-disagreements"])
    assert bad == [], bad[:20]


def test_a_planted_ceiling_fails_the_model_facts(tmp_path: Path, monkeypatch: Any) -> None:
    """FALSIFIER: one output ceiling added to ``demand.gapaudit.OUTPUT_MAX``,
    and the model-fact table and its disagreements both move."""
    from jobs.demand import gapaudit

    monkeypatch.setattr(gapaudit, "OUTPUT_MAX", {**gapaudit.OUTPUT_MAX, "claude-haiku-5-5": 64_000})
    doc = model_facts(tmp_path)
    assert gk.compare(DIR / "model-facts.json", doc)
    assert gk.compare(DIR / "model-facts-disagreements.json", disagreements(doc))
