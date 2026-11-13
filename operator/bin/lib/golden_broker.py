"""The real broker the golden sets drive: ``workspace_broker.server.Broker``
constructed by its own ``__init__``, from the environment the broker process
reads on a seat, so every lane's verbs and ledgers are built exactly as the
entrypoint builds them. The credentials, mail transports and establishment
spool are absent (their variables unset), so the verbs that need them answer
as a seat without them does.

Shared by the job host matrix (``runners/jobs/tests/host_matrix_kit.py``), the
broker ledger goldens (``workspace_broker/tests/ledger_golden_kit.py``) and the
wire snapshot, so all three drive one construction of the broker. Its callers
name the queue dirs.
"""

from __future__ import annotations

import os
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


#: Variables ``Broker.__init__`` reads that would give the golden broker a
#: credential, a mail transport, an establishment spool or a fixed agent uid. A
#: golden seat has none of them, whatever the shell that runs the tests holds.
ABSENT = (
    "GOOGLE_SERVICE_ACCOUNT_JSON",
    "GOOGLE_TOKEN_JSON",
    "AGENTMAIL_SEND_API_KEY",
    "SMD_AGENTMAIL_CREDENTIAL_PATH",
    "SMD_MSGRAPH_CREDENTIAL_PATH",
    "SMD_MSGRAPH_READ_CREDENTIAL_PATH",
    "SMD_ESTABLISH_SPOOL_DIR",
    "SMD_ESCALATION_LEDGER_PATH",
    "SMD_AGENT_UID",
)
GATEWAY_PID = 999_999_999


def queue_env(lane: str) -> str:
    """The variable ``Broker.__init__`` reads a lane's queue dir from."""
    if lane == "records":
        from workspace_broker.records_store import QUEUE_DIR_ENV

        return QUEUE_DIR_ENV
    return f"SMD_{lane.upper()}_QUEUE_DIR"


def broker_env(root: Path, audit_db: Path, queues: dict[str, Path], *, slug: str, socket_path: Path) -> dict[str, str]:
    """The environment a seat's broker process starts with, for this golden seat."""
    return {
        "SMD_WORKSPACE_BROKER_SOCKET": str(socket_path),
        "SMD_CUSTOMER_YAML": str(customer_yaml(root / "customer.yaml")),
        "SMD_WORKSPACE_CREDENTIAL_PATH": str(root / "credential.json"),
        "CUSTOMER_SLUG": slug,
        "SMD_GATEWAY_PID": str(GATEWAY_PID),
        "SMD_AUDIT_DB_PATH": str(audit_db),
        **{queue_env(lane): str(q) for lane, q in queues.items()},
    }


def build_broker(
    root: Path, audit_db: Path, queues: dict[str, Path], *, slug: str = "example", socket_path: Path | None = None
) -> Any:
    """A ``Broker`` built by ``Broker.__init__`` on ``audit_db``, whose lane
    verbs queue into ``queues[lane]``. The process environment is set for the
    construction only and restored after it."""
    from workspace_broker.server import Broker

    audit_db.parent.mkdir(parents=True, exist_ok=True)
    env = broker_env(root, audit_db, queues, slug=slug, socket_path=socket_path or root / "broker.sock")
    saved = {k: os.environ.get(k) for k in (*env, *ABSENT)}
    try:
        for k in ABSENT:
            os.environ.pop(k, None)
        os.environ.update(env)
        return Broker()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


__all__ = ["ABSENT", "GATEWAY_PID", "SKILLS", "broker_env", "build_broker", "customer_yaml", "queue_env"]
