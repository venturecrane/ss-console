"""The broker's jobs-family path defaults resolve to the values they always had.

The broker reads its queue defaults from the generated
``workspace_broker.seat_layout`` (operator/contracts/seat-layout.yaml). Each row
is a default that was a hand-written literal before the migration, with that
literal; the env override still wins when set and non-empty.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from workspace_broker import casefile_casework, records_store, reply_binding


class _Broker:
    audit_db_path = Path("/nonexistent/audit.db")


def _ledger_queue(monkeypatch: pytest.MonkeyPatch, module: Any, cls: str, call: Callable[[], Any]) -> str:
    seen: list[str] = []

    def record(_db: str, queue: str) -> object:
        seen.append(queue)
        return object()

    monkeypatch.setattr(module, cls, record)
    call()
    (queue,) = seen
    return queue


# (env override, module, ledger class the function constructs, the call, the literal it replaced)
QUEUES: dict[str, tuple[str, Any, str, str, str]] = {
    "demand": ("SMD_DEMAND_QUEUE_DIR", reply_binding, "DemandLedger", "_demand_ledger", "/run/smd-jobs/demand-queue"),
    "drafting": (
        "SMD_DRAFTING_QUEUE_DIR",
        reply_binding,
        "DraftingLedger",
        "_drafting_ledger",
        "/run/smd-jobs/drafting-queue",
    ),
    "litigation": (
        "SMD_LITIGATION_QUEUE_DIR",
        reply_binding,
        "LitigationLedger",
        "_litigation_ledger",
        "/run/smd-jobs/litigation-queue",
    ),
    "negotiation": (
        "SMD_NEGOTIATION_QUEUE_DIR",
        reply_binding,
        "NegotiationLedger",
        "_negotiation_ledger",
        "/run/smd-jobs/negotiation-queue",
    ),
    "medchron": ("SMD_MEDCHRON_QUEUE_DIR", reply_binding, "MedchronLedger", "_medchron_ledger", "/run/smd-jobs/queue"),
    "casefile": (
        "SMD_CASEFILE_QUEUE_DIR",
        casefile_casework,
        "CasefileLedger",
        "casefile_ledger_of",
        "/run/smd-jobs/casefile-queue",
    ),
}


@pytest.mark.parametrize("lane", sorted(QUEUES))
def test_a_queue_default_resolves_to_its_old_literal(lane: str, monkeypatch: pytest.MonkeyPatch) -> None:
    env, module, cls, fn, literal = QUEUES[lane]
    if lane == "drafting":
        monkeypatch.setattr(reply_binding, "of_drafting", lambda _d: None)
        monkeypatch.setattr(reply_binding, "DraftingOrSetup", lambda _d, _s: None)
    call = getattr(module, fn)
    monkeypatch.delenv(env, raising=False)
    assert _ledger_queue(monkeypatch, module, cls, lambda: call(_Broker())) == literal
    monkeypatch.setenv(env, "")
    assert _ledger_queue(monkeypatch, module, cls, lambda: call(_Broker())) == literal
    monkeypatch.setenv(env, "/tmp/elsewhere")
    assert _ledger_queue(monkeypatch, module, cls, lambda: call(_Broker())) == "/tmp/elsewhere"


def test_the_records_queue_default_resolves_to_its_old_literal() -> None:
    assert records_store.DEFAULT_QUEUE_DIR == "/opt/data/jobs/records-queue"
    assert type(records_store.DEFAULT_QUEUE_DIR) is str
