"""What a drafting job reads before the order of precedence is decided
(``layout_precedence.Facts``), and the one call ``library_pick`` makes.

Read at the moment the job's Word file is chosen, once per job run: the rules
this job lays (frozen at job start by ``firm_rules.start``), when each was said
(``rule_times``), the firm's written-down layout, what the accepted file says
(``layout_props``), what the copy it replaces said when this job is the one that
met the firm's save, and the slot's earlier versions from the installed ledger.
Nothing here writes. No model call.
"""

from __future__ import annotations

from typing import Any

from operator_connector_sdk.library import RESOLVED, read_installed

from . import layout_props as P, layout_precedence as LP, rule_times
from .firm_rules import STATE_KEY, _spec_dir


def frozen_rules(run: Any) -> dict[str, tuple[str, Any]]:
    """key -> (rule id, value) of each rule value this job lays, later winning."""
    frozen = run._state().get(STATE_KEY)
    rows = frozen.get("rows") if isinstance(frozen, dict) else None
    out: dict[str, tuple[str, Any]] = {}
    for row in rows or ():
        if isinstance(row, dict) and row.get("applied") and row.get("key") and "value" in row:
            out[str(row["key"])] = (str(row.get("rule_id") or ""), row["value"])
    return out


def _chain(spec: Any, file_id: str) -> tuple[tuple[str, str], ...]:
    got = read_installed(spec)
    ledger = got.ledger
    if ledger is None:
        return ()
    entries = (*ledger.superseded, *ledger.entries)
    return tuple((e.accepted_by, e.accepted_at) for e in entries if e.file_id == file_id)


def facts(run: Any, res: Any, replaced: bytes | None) -> LP.Facts | None:
    """The facts for this job's accepted file, or None when nothing is accepted
    (the order then decides nothing and the record is as it was). ``replaced``:
    the copy the firm's save replaces, only for the job that met the save."""
    f = res.file
    if res.status != RESOLVED or f is None or res.data is None:
        return None
    fmt, paper, spec = run.firm.data["format"], run.paper(), _spec_dir()
    authored = dict((fmt.get("layout") or {}).get(run.cls) or {})
    margins = (fmt.get("pleading") or {}).get("margins_in") if paper == "pleading" else None
    return LP.Facts(
        cls=run.cls,
        paper=paper,
        rules=frozen_rules(run),
        rule_at=rule_times.read(spec),
        authored=authored,
        authored_margins=tuple(float(x) for x in margins) if margins else None,
        accepted_by=f.accepted_by,
        accepted_at=f.accepted_at,
        version=f.version,
        after=P.read(res.data, f.contract, run.cls, paper),
        before=P.read(replaced, f.contract, run.cls, paper) if replaced is not None else None,
        chain=_chain(spec, f.file_id),
    )


def for_job(run: Any, res: Any, replaced: bytes | None) -> LP.Precedence | None:
    got = facts(run, res, replaced)
    return LP.decide(got) if got is not None else None


__all__ = ["facts", "for_job", "frozen_rules"]
