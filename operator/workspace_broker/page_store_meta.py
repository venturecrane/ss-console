"""A job's page-store record, carried from the runner's report to its audit row.

The jobs runner keeps each page a model read in one store (wave 5,
``runners/jobs/jobs/matter/store.py``), and each job that read a page reports
``page_store`` in its final record: ``{store: on|off, hits, misses}``. No
ledger keeps it as a column (each refuses a field it does not know), so a
lane's ``_record`` takes it out of the fields first (:func:`take`) and puts
the checked copy on the transition's audit row, where the hourly work-health
digest reads it (``bin/lib/work_health_page_store.py``: a lane whose jobs run
with the store off, or pay for pages and never hit).

Counts only: no page text, no file name, no matter reaches the row.
"""

from __future__ import annotations

from typing import Any

FIELD = "page_store"


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def take(fields: dict[str, Any]) -> dict[str, Any] | None:
    """Remove ``page_store`` from a record's fields; its checked copy, or None
    when it is absent or not the runner's shape."""
    raw = fields.pop(FIELD, None)
    if not isinstance(raw, dict) or raw.get("store") not in ("on", "off"):
        return None
    return {"store": raw["store"], "hits": _count(raw.get("hits")), "misses": _count(raw.get("misses"))}


def add(meta: dict[str, Any], store: dict[str, Any] | None) -> dict[str, Any]:
    """``meta`` with the record's page store on it, when it carried one."""
    if store is not None:
        meta[FIELD] = store
    return meta


__all__ = ["FIELD", "add", "take"]
