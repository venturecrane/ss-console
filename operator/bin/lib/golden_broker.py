"""The real broker the golden sets drive: ``workspace_broker.server.Broker``
with every job lane's verbs and ledgers built on one audit DB, exactly as
``Broker.__init__`` builds them, minus the credentials, the mail transports and
the establishment spool (absent, so the verbs that need them answer as a seat
without them does).

Shared by the job host matrix (``runners/jobs/tests/host_matrix_kit.py``) and
the broker ledger goldens (``workspace_broker/tests/ledger_golden_kit.py``), so
both drive one construction of the broker. Its callers name the queue dirs.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import yaml

#: The job skills a golden seat's customer.yaml enables.
SKILLS = (
    "medical-chronology-maintainer",
    "demand-letter-drafter",
    "document-drafter",
    "litigation-status",
    "case-file-manager",
    "negotiation-watch",
    "matter-records",
)


def customer_yaml(path: Path) -> Path:
    doc = {
        "scope": {"admins": ["admin@firm.example"]},
        "personas": [{"slug": "operator", "skills": [{"name": s, "enabled": True} for s in SKILLS]}],
    }
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


def build_broker(root: Path, audit_db: Path, queues: dict[str, Path], *, slug: str = "example") -> Any:
    """A ``Broker`` on ``audit_db`` whose lane verbs queue into ``queues[lane]``."""
    from workspace_broker.audit_ledger import LedgerWriter
    from workspace_broker.casefile_verbs import CasefileVerbs
    from workspace_broker.demand_verbs import DemandVerbs
    from workspace_broker.drafting_verbs import DraftingVerbs
    from workspace_broker.job_ledger import JobLedgerWriter
    from workspace_broker.litigation_verbs import LitigationVerbs
    from workspace_broker.medchron_verbs import MedchronVerbs
    from workspace_broker.negotiation_verbs import NegotiationVerbs
    from workspace_broker.records_verbs import RecordsVerbs
    from workspace_broker.server import Broker

    b = Broker.__new__(Broker)
    audit_db.parent.mkdir(parents=True, exist_ok=True)
    b.customer_path = customer_yaml(root / "customer.yaml")
    b.customer_slug = slug
    b.gateway_pid = 999_999_999
    b.socket_path = root / "broker.sock"
    b.credential_path = root / "credential.json"
    b.audit_db_path = str(audit_db)
    b.ledger = LedgerWriter(str(audit_db))
    b.job_ledger = JobLedgerWriter(str(audit_db))
    b.escalation_ledger_path = str(audit_db.parent / "escalation-ledger.jsonl")
    b._escalation_lock = threading.Lock()
    b.agent_uid = None
    b.agentmail = None
    b.msgraph = None
    b.establishment = None
    db = str(audit_db)
    # Each lane's verbs on the broker attribute ``Broker.__init__`` sets: the
    # class name less ``Verbs`` (the lane list itself lives in job-lanes.yaml).
    for verbs in (
        MedchronVerbs,
        DemandVerbs,
        DraftingVerbs,
        LitigationVerbs,
        NegotiationVerbs,
        CasefileVerbs,
        RecordsVerbs,
    ):
        lane = verbs.__name__.removesuffix("Verbs").lower()
        setattr(b, lane, verbs.build(b, audit_db_path=db, queue_dir=str(queues[lane])))
    return b


__all__ = ["SKILLS", "build_broker", "customer_yaml"]
