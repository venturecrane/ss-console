"""The host matrix's lanes: how each is configured on the seat, fed one job
through its broker ledger's own ``submit``, has its firm inputs broken, and is
made to wait behind another lane's child (``host_matrix_kit.py``)."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from host_matrix_kit import Seat


@dataclass(frozen=True)
class LaneKit:
    name: str
    #: The env the seat's entrypoint sets for this lane (beyond its queue).
    env: Callable[[Seat], dict[str, str]]
    #: One job into the queue through the broker's own ledger; returns its id.
    submit: Callable[[Any, Seat], str]
    #: Names of env vars whose path is this lane's firm inputs (removed for the settings cell).
    settings_env: tuple[str, ...]
    #: A child pid file of another lane this lane waits behind, or None.
    busy_pid: str | None
    #: The lane's module (``build_lane``), from a fixed table.
    module: Callable[[], Any]
    queue_env: str = ""
    #: The verb prefix of this lane's broker verbs (resume).
    verbs: str = ""

    @property
    def queue_lane(self) -> str:
        """The queued lane whose queue this kit's jobs go to."""
        return "drafting" if self.name == "library_setup" else self.name


def _medchron_env(seat: Seat) -> dict[str, str]:
    import yaml
    from jobs_testkit import FIRM_CONFIG

    p = seat.root / "inputs" / "medchron" / "firm.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(FIRM_CONFIG, sort_keys=False), encoding="utf-8")
    return {"MEDCHRON_FIRM_CONFIG": str(p)}


def _demand_env(seat: Seat) -> dict[str, str]:
    from demand_testkit import make_inputs

    return {"JOBS_DEMAND_INPUTS": str(make_inputs(seat.root / "inputs" / "demand"))}


def _drafting_env(seat: Seat) -> dict[str, str]:
    from drafting_testkit import make_inputs

    return {"JOBS_DRAFTING_INPUTS": str(make_inputs(seat.root / "inputs" / "drafting"))}


def _litigation_env(seat: Seat) -> dict[str, str]:
    from litigation_testkit import make_inputs

    return {
        "JOBS_LITIGATION_INPUTS": str(make_inputs(seat.root / "inputs" / "litigation")),
        "JOBS_LITIGATION_STATE_DIR": str(seat.root / "state" / "litigation"),
    }


def _casefile_env(seat: Seat) -> dict[str, str]:
    from casefile_testkit import make_inputs

    return {
        "JOBS_CASEFILE_INPUTS_DIR": str(make_inputs(seat.root / "inputs" / "casefile")),
        "JOBS_CASEFILE_STATE_DIR": str(seat.root / "state" / "casefile"),
    }


def _negotiation_env(seat: Seat) -> dict[str, str]:
    return {"JOBS_NEGOTIATION_STATE_DIR": str(seat.root / "state" / "negotiation")}


def _records_env(seat: Seat) -> dict[str, str]:
    inputs = seat.root / "inputs" / "records"
    inputs.mkdir(parents=True, exist_ok=True)
    (inputs / "records-firm.yaml").write_text(
        "per_job_cap_usd: 5.0\nmonthly_budget_usd: 100.0\nusd_per_page: 0.01\n", encoding="utf-8"
    )
    return {
        "JOBS_RECORDS_INPUTS_DIR": str(inputs),
        "JOBS_RECORDS_STATE_DIR": str(seat.root / "state" / "records"),
        "SMOKEBALL_PAGE_INDEX_DIR": str(seat.root / "page-index"),
        "SMD_CUSTOMER_YAML": str(seat.root / "customer.yaml"),
        "SMD_MATTER_FILE_REVIEW_DIR": str(seat.root / "matter-file-review"),
        "SMD_MATTER_FILE_DECISIONS_DIR": str(seat.root / "matter-file-decisions"),
    }


def remove(path: str | Path) -> None:
    p = Path(path)
    if p.is_dir():
        shutil.rmtree(p)
    elif p.exists():
        p.unlink()


def break_settings(kit: LaneKit) -> None:
    for name in kit.settings_env:
        remove(os.environ[name])


def _submit_medchron(b: Any, seat: Seat) -> str:
    unit = {"unit": "alpha", "client_name": "Alpha Example", "name_token": "Example", "surname": "Example"}
    env = {
        "matter": {"id": "m-1", "number": "2026-PI-102", "title": "Example v. Example"},
        "units": [{**unit, "dob": "01/02/1980"}],
        "incident": {"date": "2026-01-15", "source": "administrator_request"},
        "allowance_remaining_documents": 40,
        "allowance_remaining_pages": 40,
        "cap_usd": 25.0,
        "requested_by": "admin@firm.example",
        "request_ref": "<req-1@firm.example>",
    }
    return b.medchron._db.submit(env, remaining=40)


def _submit_demand(b: Any, seat: Seat) -> str:
    from demand_testkit import job_doc

    keep = ("matter", "file_to", "requested_by", "request_ref", "request_text", "deliverables")
    return b.demand._db.submit({k: v for k, v in job_doc().items() if k in keep})


def _submit_drafting(b: Any, seat: Seat) -> str:
    from drafting_testkit import MATTER

    return b.drafting._db.submit(
        {
            "document_class": "mediation_brief",
            "file_to": None,
            "matter": {"id": MATTER, "number": "100001"},
            "request_ref": "<req-1@firm.example>",
            "request_text": "Please draft the mediation brief.",
            "requested_by": "admin@firm.example",
        }
    )


def _submit_library_setup(b: Any, seat: Seat) -> str:
    from workspace_broker.library_setup_ledger import SetupLedger

    from jobs import job_lanes

    led = SetupLedger(str(seat.audit_db), seat.run_dir / job_lanes.LANE_QUEUE_DIR["drafting"])
    env = {
        "kind": "memo",
        "attorney": None,
        "requested_by": "admin@firm.example",
        "request_ref": "<setup-1@firm.example>",
        "request_text": "Set up our memo",
    }
    return led.submit(env, library_number="OPS-LIBRARY", live=True)


def _submit_litigation(b: Any, seat: Seat) -> str:
    return b.litigation._db.submit(
        {
            "trigger": "request",
            "requester": "admin@firm.example",
            "message_ref": "<list-1@firm.example>",
            "request_text": "status list please",
            "scope": {"paralegal_staff_ids": ["11111111-2222-3333-4444-555555555551"]},
            "file_to_matter_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "file_to_matter_number": "OPS-OPERATOR-LIBRARY",
            "folder_name": "Litigation Status",
        }
    )


def _submit_casefile(b: Any, seat: Seat) -> str:
    from casefile_testkit import envelope

    return b.casefile._db.submit({k: v for k, v in envelope().items() if k not in ("kind", "job_id")})


def _submit_negotiation(b: Any, seat: Seat) -> str:
    return b.negotiation._db.submit(
        {
            "trigger": "scheduled",
            "requester": "admin@firm.example",
            "message_ref": "scheduled:2026-10-11T08",
            "request_text": "negotiation watch",
            "matter_statuses": ["Open"],
            "negotiation_design": "",
            "firm_words": ["example"],
            "seed_saved_before": "",
            "per_job_cap_usd": 8,
            "monthly_budget_usd": 25,
        }
    )


def _submit_records(b: Any, seat: Seat) -> str:
    return b.records._db.submit(
        {
            "trigger": "request",
            "requester": "pat.lane@example-firm.test",
            "message_ref": "AAMkAGraphMessageId0001",
            "request_text": "the clinic visit",
            "matter_id": "11111111-2222-3333-4444-555555555555",
            "ack": "turn",
        }
    )


def _no_submit(b: Any, seat: Seat) -> str:
    raise AssertionError("the note lane takes no submitted job")


def _medchron_mod() -> Any:
    from jobs.medchron import lane

    return lane


def _demand_mod() -> Any:
    from jobs import demand_lane

    return demand_lane


def _drafting_mod() -> Any:
    from jobs import drafting_lane

    return drafting_lane


def _litigation_mod() -> Any:
    from jobs import litigation_lane

    return litigation_lane


def _casefile_mod() -> Any:
    from jobs import casefile_lane

    return casefile_lane


def _negotiation_mod() -> Any:
    from jobs import negotiation_lane

    return negotiation_lane


def _records_mod() -> Any:
    from jobs import records_lane

    return records_lane


def _note_mod() -> Any:
    from jobs import note_lane

    return note_lane


KITS: dict[str, LaneKit] = {
    "medchron": LaneKit("medchron", _medchron_env, _submit_medchron, ("MEDCHRON_FIRM_CONFIG",), None,
                        _medchron_mod, verbs="medchron"),
    "demand": LaneKit("demand", _demand_env, _submit_demand, ("JOBS_DEMAND_INPUTS",), None,
                      _demand_mod, "SMD_DEMAND_QUEUE_DIR", "demand"),
    "drafting": LaneKit("drafting", _drafting_env, _submit_drafting, ("JOBS_DRAFTING_INPUTS",), "demand-child.pid",
                        _drafting_mod, "SMD_DRAFTING_QUEUE_DIR", "drafting"),
    "library_setup": LaneKit("library_setup", _drafting_env, _submit_library_setup, ("JOBS_DRAFTING_INPUTS",), None,
                             _drafting_mod, "SMD_DRAFTING_QUEUE_DIR", "drafting"),
    "litigation": LaneKit("litigation", _litigation_env, _submit_litigation, ("JOBS_LITIGATION_INPUTS",),
                          "demand-child.pid", _litigation_mod, "SMD_LITIGATION_QUEUE_DIR", "litigation"),
    "casefile": LaneKit("casefile", _casefile_env, _submit_casefile, ("JOBS_CASEFILE_INPUTS_DIR",), "child.pid",
                        _casefile_mod, "SMD_CASEFILE_QUEUE_DIR", "casefile"),
    "negotiation": LaneKit("negotiation", _negotiation_env, _submit_negotiation, (), "demand-child.pid",
                           _negotiation_mod, "SMD_NEGOTIATION_QUEUE_DIR", "negotiation"),
    "records": LaneKit("records", _records_env, _submit_records, ("JOBS_RECORDS_INPUTS_DIR",), "demand-child.pid",
                       _records_mod, "SMD_RECORDS_QUEUE_DIR", "records"),
    "note": LaneKit("note", lambda s: {}, _no_submit, (), None, _note_mod),
}  # fmt: skip
