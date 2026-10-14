"""A hand-recorded update delivery (``medchron.deliver_by_smd``) ADDS to the
matter's coverage; it never replaces what an earlier delivery covered.

The tool records THIS delivery's sets on its own row. The ledger unions every
delivered row on the matter at read time (``delivered_coverage``), so the
earlier chronology's documents stay covered and the next update reads only
what neither delivery covered. The ledger path the tool drives is
``failed -> running -> delivered`` through ``record``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker.medchron_ledger import MedchronLedger

MATTER = "b041dd06-30a4-4c1f-912b-27724bd77a64"


def _submit(ledger: MedchronLedger, day: str) -> str:
    return ledger.submit(
        {
            "matter": {"id": MATTER, "number": "900301", "title": "Doe v. Roe"},
            "units": [{"client_name": "Jane Doe", "surname": "Doe", "dob": "01/01/1980"}],
            "incident": {"date": day, "source": "matter_layout"},
            "requested_by": "admin@firm.example",
            "request_ref": "<r@mail.firm.example>",
        },
        remaining=100000,
    )


def test_a_hand_recorded_update_adds_to_the_matters_coverage(tmp_path: Path) -> None:
    ledger = MedchronLedger(str(tmp_path / "audit.db"), tmp_path / "mq")
    first = _submit(ledger, "2025-01-01")
    ledger.record(first, "running", {})
    ledger.record(first, "delivered", {"covered": {"covered": ["a", "b"], "uncovered": ["x"], "pulled": 3}})

    update = _submit(ledger, "2025-01-02")
    ledger.record(update, "failed", {"reason": "stopped by SMD"})
    ledger.record(update, "running", {})
    ledger.record(
        update,
        "delivered",
        {"folder_id": "f", "covered": {"covered": ["c", "d"], "uncovered": [], "pulled": 2}},
    )

    union = ledger.delivered_coverage(MATTER)
    assert union is not None
    assert union["covered_document_ids"] == ["a", "b", "c", "d"]
    assert union["uncovered_document_ids"] == ["x"]
    assert union["deliveries"] == 2
