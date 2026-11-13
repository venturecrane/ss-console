"""One paid-model session per job: the kit every paid lane opens.

Until wave 2 each paid lane (chronology, demand, drafting, litigation, case
file, negotiation, records) built the same five things by hand in its own
constructor: the rate card (an explicit path, else ``$JOBS_PRICING_JSON``, else
the seat's card), the job's usage ledger, the spend :class:`~jobs.budget.Budget`
over that ledger, the four :class:`~jobs.limits.Limits` and the
:class:`~jobs.llm.Doorway` every model request goes through. Seven copies of
one setup meant a change to how a job is priced or limited had to land seven
times. They now come from here, built exactly as each lane built them: the
lane still computes its own figures (its cap, its month, its rates) and its own
per-call and per-batch checks, and hands them in; this module only puts them
together.

``open_session`` is the whole setup for a lane whose doorway writes one ledger
in the job's data folder. The chronology prices several ledgers and builds a
doorway per stage from the firm's levers (``stages/base.py``), so it uses the
parts: :func:`load_pricing`, :func:`budget` and :meth:`Caps.limits`.
"""

from __future__ import annotations

from .session import LEDGER_NAME, Caps, PaidSession, budget, load_pricing, open_session, pricing_path

__all__ = ["LEDGER_NAME", "Caps", "PaidSession", "budget", "load_pricing", "open_session", "pricing_path"]
