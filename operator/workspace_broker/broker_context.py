"""What a verb handler may read off the broker: the ``BrokerContext`` Protocol.

WHY THIS EXISTS (code review 2026-09-25, Code Quality 1, top action item 4).
The verb-table refactor (``verbs.py``) moved 35 ``Broker`` method bodies into
module-level handlers whose first parameter was ``broker: Any``. The
authorization structure got better and the type coverage of the same code went
to zero: ``Any`` produces no pyright errors, so a typo'd attribute, a missing
``None`` check on a ledger that is optional by design, or a wrong argument to a
transport all passed the ratchet silently. 53 parameters across ten modules
were blind.

THE CONTRACT. Every attribute a handler reads is declared here, with the type
``Broker.__init__`` gives it. Read-only properties, not plain attributes: a
handler reads the broker's state and never rebinds it, and a property is
covariant, so a test double may hand back a narrower type. ``Broker`` satisfies
this structurally, and pyright proves it where ``Broker`` passes ``self`` to
``verbs.dispatch`` and ``MedchronVerbs.build`` (both typed ``BrokerContext``);
``workspace_broker/tests/test_broker_context.py`` pins the parts pyright cannot
see (no handler back on ``Any``; every member defined by ``Broker``).

Typing the handlers surfaced eight errors ``Any`` had hidden, fixed at the
site: six ``msgraph`` reads and one ``audit_db_path`` pass in ``send_as_acts``
that relied on a guard in another function (now ``_graph`` / ``_store``
narrow and refuse), and the medchron audit-append lambda that dereferenced an
optional ledger (now a closure that refuses).

Adding an attribute a handler reads means adding it here first. That is the
point: the Protocol is the list of what the handlers depend on, and pyright
enforces it at every read.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .agentmail_ops import AgentMailOps
    from .audit_ledger import LedgerWriter
    from .establishment import EstablishmentStore
    from .job_ledger import JobLedgerWriter
    from .medchron_verbs import MedchronVerbs
    from .msgraph_ops import MsGraphOps
    from .operations import WorkspaceOperations
    from .server import GrantStore


class BrokerContext(Protocol):
    @property
    def socket_path(self) -> Path: ...
    @property
    def customer_path(self) -> Path: ...
    @property
    def credential_path(self) -> Path: ...
    @property
    def customer_slug(self) -> str: ...
    @property
    def gateway_pid(self) -> int: ...
    @property
    def audit_db_path(self) -> str | None: ...
    @property
    def escalation_ledger_path(self) -> str | None: ...

    @property
    def operations(self) -> WorkspaceOperations: ...
    @property
    def grants(self) -> GrantStore: ...
    @property
    def ledger(self) -> LedgerWriter | None: ...
    @property
    def job_ledger(self) -> JobLedgerWriter | None: ...
    @property
    def establishment(self) -> EstablishmentStore | None: ...
    @property
    def medchron(self) -> MedchronVerbs | None: ...
    @property
    def agentmail(self) -> AgentMailOps | None: ...
    @property
    def msgraph(self) -> MsGraphOps | None: ...

    # The two serialization locks. Underscored because they are the broker's
    # own, but the escalation and establishment handlers take them.
    @property
    def _escalation_lock(self) -> threading.Lock: ...
    @property
    def _establish_lock(self) -> threading.Lock: ...

    def _resolve_agent_uid(self) -> int | None: ...


__all__ = ["BrokerContext"]
