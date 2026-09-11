"""Stage-attributed token/cost ledger for the work-product drafting lane.

WHY THIS EXISTS
---------------
The drafting lane shipped with a cost figure and no instrument. The 2026-07-29
prove-out measured a demand at roughly $0.96 -- against a twelve-document
fictional matter. The firm's real matters carry 192 to 304 documents
(``vfy_01M1EXFJD5FNBQC3XJQBFV6SHE``), so that number describes a corpus nobody
will ever draft against, and there was nothing in the lane that would have said
so. A first run without this module produces a bill; a first run with it
produces a calibration row that prices every run after it.

This is a port of the medical-chronology pipeline's ledger, which earned its
shape the hard way. Three of its lessons are carried here verbatim, because
they are the reason the chronology's unit cost fell from $1.30 to $0.40 per
document and stayed down:

1. **Tokens are canonical; dollars are derived.** A row stores tokens and a
   model name. Dollars come from :func:`price` at the rate card in
   ``rate-card.json``, at read time, never at write time. Rate cards move.
2. **A ledger row is attribution, not truth.** One chronology run's ledger
   captured $3.68 of a real ~$14.60 because a stage ran without its environment
   exported. So a stage that cannot resolve its run still writes -- to an
   orphan file -- and the gap is visible instead of silent.
3. **Never let the ledger kill a paid call.** :func:`record` swallows every
   exception. Losing a measurement is a defect; losing the work being measured
   is a bigger one.

WHAT THIS DOES NOT MEASURE, and it is the important sentence
------------------------------------------------------------
This records usage reported by the API call the caller just made. It therefore
measures a HARNESS-DRIVEN run -- the drafting pipeline driven from a workstation
against the API directly, which is how the prove-out and every chronology run to
date were executed.

It does NOT measure a run performed by the Operator on its own seat. The seat's
audit log records that a turn happened and on which model, and nothing else: the
``LLM_TURN_COMPLETED`` metadata carries ``customer, model, per_llm_audit,
platform, session_id`` across all 232 rows on the A&P seat and no token counts
at any point (probed 2026-09-01). A seat-side drafting run is therefore
attributable only from the organisation cost report, at whole-workspace
granularity and a day's lag, with no stage breakdown. Closing that needs the
overlay to record usage into the audit row; until it does, do not describe a
seat run's cost as measured by this file.

USAGE
-----
    import ledger
    resp = client.messages.create(model=..., ...)
    ledger.record("compose", model, resp.usage)
    ...
    print(ledger.report(slug, unit))          # table + machine-readable blob
    ledger.append_calibration(slug, unit, artifact_class="demand",
                              chars=..., extra={...})

Environment: ``SMD_DRAFT_DATA`` (required, the data root), ``SMD_SLUG``
(the matter), ``SMD_UNIT`` (the run; defaults to the slug).
"""

from __future__ import annotations

import json
import os
import sys
import time

_CARD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rate-card.json")

#: Stages whose model choice is a routing decision the discipline governs
#: (Part III). Recording under a name outside this set is allowed -- an
#: unrecognised stage is a new stage, not an error -- but the report marks it,
#: because an unrouted stage is how an Opus call hides inside a cheap pipeline.
KNOWN_STAGES = frozenset(
    {
        "assemble",  # pulling the matter record; usually no model call at all
        "extract",  # mechanical text extraction (free; recorded for the count)
        "digest",  # collapsing the record into a cited fact digest
        "compose",  # the draft itself -- work-product model, never delegated
        "audit",  # citation / quotation verification
        "coverage",  # propounded-vs-response diffing
        "gates",  # the ten mechanical gates
        "lint",  # SPROG / subpart lint
        "repair",  # correction passes over a composed draft
        "revise",  # an attorney-requested revision round
    }
)

#: A stage's ceiling must budget for THINKING, which is billed against the same
#: allowance as the text. On the first instrumented run every stage was sized
#: from expected output length: compose stopped at section V of eleven, and the
#: audit spent 31,999 of 32,000 tokens thinking and returned no text at all.
#: A caller that sets max_tokens from "how long should the answer be" is wrong by
#: however much the model decides to think, and must read ``stop_reason`` to find
#: out. Where the task is verification rather than composition, disabling
#: thinking outright is better than raising the ceiling: the reasoning has no
#: natural stopping point and a bigger budget only buys more silence.


def _load_card():
    with open(_CARD_PATH) as fh:
        return json.load(fh)


RATES = _load_card()


def _run_dir(data, slug, unit):
    return os.path.join(data, slug, "runs", unit)


def record(stage, model, usage, extra=None):
    """Append one call's usage. Best-effort by design: never raises."""
    try:
        data = os.environ.get("SMD_DRAFT_DATA")
        if not data:
            return
        slug = os.environ.get("SMD_SLUG")
        unit = os.environ.get("SMD_UNIT") or slug
        if not slug:
            # A stage that lost its environment still writes. The money totals
            # and the hole is visible -- see lesson 2 in the module docstring.
            path = os.path.join(data, "usage-ledger-orphan.jsonl")
        else:
            path = os.path.join(_run_dir(data, slug, unit), "usage-ledger.jsonl")
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "stage": stage,
            "model": model,
            "in": getattr(usage, "input_tokens", None),
            "out": getattr(usage, "output_tokens", None),
            "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        }
        if extra:
            rec.update(extra)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:  # noqa: BLE001 - the ledger is best-effort telemetry; a write failure must never break the drafting call it records
        pass


def rate_for(model):
    """Exact match, then longest prefix match. None for an unknown model.

    Prefix matching is what lets a dated model id (``claude-opus-5-20260814``)
    price against its family without a card entry per snapshot.
    """
    if not model:
        return None
    if model in RATES and model != "_meta":
        return RATES[model]
    best = None
    for key in RATES:
        if key == "_meta" or key.startswith("_"):
            continue
        if model.startswith(key) and (best is None or len(key) > len(best)):
            best = key
    return RATES[best] if best else None


def price(row):
    """Dollars for one ledger row, or None when the model is unknown.

    Returning None rather than 0.0 for an unknown model is deliberate: a zero
    would total silently and read as a cheap run. The report counts these.
    """
    r = rate_for(row.get("model"))
    if r is None:
        return None
    m = RATES["_meta"]
    tin = row.get("in") or 0
    tout = row.get("out") or 0
    cr = row.get("cache_read") or 0
    cw = row.get("cache_write") or 0
    cents_per_m = tin * r["in"] + cw * r["in"] * m["cache_write_5m"] + cr * r["in"] * m["cache_read"] + tout * r["out"]
    dollars = cents_per_m / 1e8
    if row.get("batch"):
        dollars *= m["batch"]
    return dollars


def _read_rows(path):
    rows = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001 - a corrupt ledger line is skipped so the readable rows are still returned
                continue
    return rows


def summarise(rows):
    """Fold rows into per-stage totals. Pure; the reporting shape lives here."""
    by_stage = {}
    unknown = 0
    for row in rows:
        s = by_stage.setdefault(
            row.get("stage") or "?",
            {
                "models": set(),
                "calls": 0,
                "in": 0,
                "out": 0,
                "cache_read": 0,
                "cache_write": 0,
                "batch": 0,
                "dollars": 0.0,
            },
        )
        s["models"].add(row.get("model") or "?")
        s["calls"] += 1
        for k in ("in", "out", "cache_read", "cache_write"):
            s[k] += row.get(k) or 0
        s["batch"] += 1 if row.get("batch") else 0
        d = price(row)
        if d is None:
            unknown += 1
        else:
            s["dollars"] += d
    return by_stage, unknown


def report(slug, unit):
    data = os.environ.get("SMD_DRAFT_DATA")
    if not data:
        raise SystemExit("SMD_DRAFT_DATA not set")
    path = os.path.join(_run_dir(data, slug, unit), "usage-ledger.jsonl")
    by_stage, unknown = summarise(_read_rows(path))

    lines = [
        f"ledger: {path}",
        f"{'stage':12s} {'calls':>6s} {'in':>12s} {'out':>10s} "
        f"{'cache_rd':>10s} {'cache_wr':>10s} {'batch':>6s} "
        f"{'dollars':>9s} {'share':>7s}",
    ]
    tot = {"calls": 0, "in": 0, "out": 0, "cache_read": 0, "cache_write": 0, "batch": 0, "dollars": 0.0}
    for s in by_stage.values():
        for k in tot:
            tot[k] += s[k]
    grand = tot["dollars"] or 1.0

    tokens_by_stage, dollars_by_stage, unrouted = {}, {}, []
    for stage in sorted(by_stage, key=lambda k: -by_stage[k]["dollars"]):
        s = by_stage[stage]
        mark = "" if stage in KNOWN_STAGES else "  <- unrouted stage"
        if stage not in KNOWN_STAGES:
            unrouted.append(stage)
        lines.append(
            f"{stage:12s} {s['calls']:6d} {s['in']:12,d} "
            f"{s['out']:10,d} {s['cache_read']:10,d} "
            f"{s['cache_write']:10,d} {s['batch']:6d} "
            f"{s['dollars']:9.2f} {100 * s['dollars'] / grand:6.1f}%"
            f"{mark}"
        )
        tokens_by_stage[stage] = {
            "model": ",".join(sorted(s["models"])),
            "calls": s["calls"],
            "in": s["in"],
            "out": s["out"],
            "cache_read": s["cache_read"],
            "cache_write": s["cache_write"],
        }
        dollars_by_stage[stage] = round(s["dollars"], 4)

    lines.append(
        f"{'TOTAL':12s} {tot['calls']:6d} {tot['in']:12,d} "
        f"{tot['out']:10,d} {tot['cache_read']:10,d} "
        f"{tot['cache_write']:10,d} {tot['batch']:6d} "
        f"{tot['dollars']:9.2f}"
    )
    lines.append(f"unknown-model rows (unpriced): {unknown}")
    if unrouted:
        lines.append("unrouted stages (not in KNOWN_STAGES): " + ", ".join(sorted(unrouted)))
    lines.append(
        json.dumps(
            {
                "rate_card": RATES,
                "tokens_by_stage": tokens_by_stage,
                "dollars_by_stage": dollars_by_stage,
                "dollars_total": round(tot["dollars"], 4),
                "unpriced_rows": unknown,
                "unrouted_stages": sorted(unrouted),
            },
            sort_keys=True,
        )
    )
    return "\n".join(lines)


def append_calibration(slug, unit, artifact_class, chars, extra=None):
    """Append one completed run to the shared calibration corpus.

    ``artifact_class`` is what makes the corpus usable across the lane: a
    demand, a discovery-response set and a chronology are different cost
    shapes, and a projection that anchors on the wrong class is worse than no
    anchor. Chronology rows carry ``artifact_class: "chronology"``.
    """
    data = os.environ.get("SMD_DRAFT_DATA")
    if not data:
        raise SystemExit("SMD_DRAFT_DATA not set")
    path = os.path.join(_run_dir(data, slug, unit), "usage-ledger.jsonl")
    by_stage, unknown = summarise(_read_rows(path))
    row = {
        "slug": slug,
        "unit": unit,
        "artifact_class": artifact_class,
        "date": time.strftime("%Y-%m-%d"),
        "chars": chars,
        "rate_card_as_of": RATES["_meta"]["as_of"],
        "unpriced_rows": unknown,
        "tokens_by_stage": {
            k: {
                "model": ",".join(sorted(v["models"])),
                "calls": v["calls"],
                "in": v["in"],
                "out": v["out"],
                "cache_read": v["cache_read"],
                "cache_write": v["cache_write"],
            }
            for k, v in by_stage.items()
        },
        "dollars_total": round(sum(v["dollars"] for v in by_stage.values()), 4),
    }
    if extra:
        row.update(extra)
    out = os.path.join(data, "calibration.jsonl")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "a") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def anchors(artifact_class, chars, k=3):
    """The k calibration rows nearest by extracted characters, same class.

    Project from extracted CHARACTERS, never from bytes: Epic EMR exports
    measured 63% more characters per byte than a mixed corpus, and a quote
    projected from megabytes came in 30% low.
    """
    data = os.environ.get("SMD_DRAFT_DATA")
    if not data:
        raise SystemExit("SMD_DRAFT_DATA not set")
    path = os.path.join(data, "calibration.jsonl")
    if not os.path.exists(path):
        return []
    rows = [r for r in _read_rows(path) if r.get("artifact_class") == artifact_class and r.get("chars")]
    rows.sort(key=lambda r: abs((r.get("chars") or 0) - chars))
    return rows[:k]


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "report":
        print(report(sys.argv[2], sys.argv[3]))
    elif len(sys.argv) == 4 and sys.argv[1] == "anchors":
        for r in anchors(sys.argv[2], int(sys.argv[3])):
            print(json.dumps(r, sort_keys=True))
    else:
        print(
            "usage: ledger.py report <slug> <unit>\n       ledger.py anchors <artifact_class> <chars>", file=sys.stderr
        )
        sys.exit(2)
