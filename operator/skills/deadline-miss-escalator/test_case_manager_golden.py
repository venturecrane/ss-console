"""The no-``case_manager`` golden: without the block, the escalator is byte-identical.

The casework change (docs/specs/operator/case-manager-deadline-work.md) touches
the escalator's pull, banding, render and envelope, and every one of those
edits is gated on an authored ``case_manager:`` block. A seat that authors none
(ashton-price today) must see exactly the wake line, the digest and the
dispatch envelope it saw before the change. The golden file below was captured
from ``origin/main`` BEFORE any casework edit landed (c3a1d149), so a diff here
is a behaviour change on an unconfigured seat, never a refactor.

Regenerate ONLY for a deliberate change to the unconfigured path::

    UPDATE_ESCALATOR_GOLDEN=1 python3 -m pytest \\
        operator/skills/deadline-miss-escalator/test_case_manager_golden.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import os
import sys
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve()
_SKILL = _HERE.parent
_GOLDEN = _SKILL / "tests" / "fixtures" / "golden-no-case-manager.json"
_FIXTURE = _SKILL / "tests" / "fixtures" / "live-pull-2026-08-24.json"
_MATTER_REF = _HERE.parents[2] / "connectors" / "smokeball" / "smokeball_connector" / "matter_ref.py"

TODAY = date(2026, 7, 20)
NOW = datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_pre_run = _load(_SKILL / "pre_run.py", "escalator_pre_run_golden")


class _FixtureClient:
    def __init__(self, matters):
        self._matters = matters

    def get(self, path, **params):
        matter = self._matters.get(path.rsplit("/", 1)[-1])
        if matter is None:
            raise RuntimeError("404")
        return matter


class _Source:
    def __init__(self, deadlines):
        self._deadlines = deadlines
        self.probe_stats = None

    def pull_deadlines(self):
        return self._deadlines


def _raw_pull() -> dict:
    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    tasks = fixture["tasks"]["value"]
    for task in tasks:
        # The live capture names a real provider; this public repo may not (the
        # medchron scrub gate), so it is swapped for a fictional one at load.
        head, sep, _provider = task["subject"].partition(" from ")
        if sep:
            task["subject"] = f"{head} from Valley Imaging"
    _load(_MATTER_REF, "golden_matter_ref").attach_matter_numbers(_FixtureClient(fixture["matters"]), tasks)
    events = [
        {
            "id": "ev-okafor-fsc",
            "title": "Final Status Conference",
            "startTime": "2026-07-23T15:30:00Z",
            "matter": {"id": "cd710b6b-a7ae-44b0-bb8a-be79e8c5d351"},
            "matterNumber": "2026-PI-104",
        }
    ]
    return {"tasks": {"value": tasks}, "events": {"value": events}}


def _ledger_events() -> list[dict]:
    """One recent raise (renders under-active-elsewhere) and one ack (omitted)."""
    ledger = _pre_run._load_ledger_module()
    raised = ledger.item_key(
        "f220c8e4-eab5-4fd9-8f1d-0becf715b390", "be08487c-1d5c-4c29-a52b-987876336ad9", "", "2026-07-10"
    )
    acked = ledger.item_key(
        "54bc1371-5c82-47ae-b722-da1b29e79ef5", "d1daf4fd-c484-4f26-868a-c8f7ed8c675a", "", "2026-07-07"
    )
    return [
        {
            "item_key": raised,
            "matter_id": "f220c8e4-eab5-4fd9-8f1d-0becf715b390",
            "event": "fired",
            "attempt": 1,
            "ts": "2026-07-19T14:00:00Z",
        },
        {
            "item_key": acked,
            "matter_id": "54bc1371-5c82-47ae-b722-da1b29e79ef5",
            "event": "fired",
            "attempt": 1,
            "ts": "2026-07-15T14:00:00Z",
        },
        {
            "item_key": acked,
            "matter_id": "54bc1371-5c82-47ae-b722-da1b29e79ef5",
            "event": "acked",
            "ts": "2026-07-16T14:00:00Z",
        },
    ]


def _capture(tmp_path: Path, monkeypatch) -> dict:
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(
        "escalation:\n  red_flag_recipients:\n    - triage@example-firm.test\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(yaml_path))
    deadlines, problem, _stats = _pre_run.parse_pull(_raw_pull(), now=NOW)
    assert problem is None
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = asyncio.new_event_loop().run_until_complete(
            _pre_run.run_once(
                [_Source(deadlines)],
                _pre_run.EscalationWindows(),
                lambda: None,
                today=TODAY,
                now=NOW,
                fire_policy=_pre_run.FirePolicy(),
                ledger_events=_ledger_events(),
            )
        )
    assert code == 0
    envelope = json.loads((tmp_path / ".smd" / "pre_run" / "deadline-miss-escalator.dispatch.json").read_text())
    envelope.pop("started_at", None)
    return {"wake": json.loads(buf.getvalue().strip().splitlines()[-1]), "envelope": envelope}


def test_no_case_manager_block_is_byte_identical_to_before(tmp_path, monkeypatch):
    got = _capture(tmp_path, monkeypatch)
    if os.environ.get("UPDATE_ESCALATOR_GOLDEN") == "1":
        _GOLDEN.write_text(json.dumps(got, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    want = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    assert got == want
    # Byte-identical bodies, not merely equal-after-parse.
    for sent, golden in zip(got["envelope"]["dispatches"], want["envelope"]["dispatches"]):
        assert sent["full_body"].encode("utf-8") == golden["full_body"].encode("utf-8")
