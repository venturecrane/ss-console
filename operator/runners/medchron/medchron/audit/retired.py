"""Controls retired as badly chosen: by SMD (``medchron.audit_decide``), or by
the round itself when the control page plainly carries the claim.

A control is only a control when its page cannot carry the claim. A person
who finds a SUPPORTED control GENUINE (the page really states the claim, e.g.
a diagnosis index) records it here; the round report and the final verdict
gate leave those rows out.

THE ROUND RETIRES THE OBVIOUS ONES ITSELF (2026-10-23). On the Curtis
chronology (matter 200530) two of 612 controls came back SUPPORTED, both on
Exhibit 1 page 1, a diagnosis index that really states every diagnosis; one
such row INVALIDs the whole round, and the job waited on SMD to read it and
run ``audit_decide``. When the claim's own checkable values (its dates and
measured numbers, ``anchors.find_anchors``; with none, at least two of its
drug and proper-name anchors) are all in the control page's OWN TEXT, the
verifier was right and the control was badly chosen: the round records it
here (``by: rule``) and every consumer (round report, final gate, update
proof 5, ``audit_decide``) honours it. A SUPPORTED control whose page does
not carry those values is a rubber stamp and INVALIDs the round, as before.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import anchors as AN

FILE = "audit-retired-controls.json"
_STRONG = ("date", "num")
_WRITE = threading.Lock()


def path(out: Path) -> Path:
    return out / FILE


def live_controls(rows: list[dict[str, Any]], out: Path) -> list[dict[str, Any]]:
    """Every control row except the retired ones."""
    gone = keys(out)
    return [r for r in rows if str(r.get("kind", "")).startswith("control") and r.get("key") not in gone]


def keys(out: Path) -> set[str]:
    p = path(out)
    if not p.is_file():
        return set()
    body = json.loads(p.read_text(encoding="utf-8"))
    return {str(r["key"]) for r in body.get("retired") or [] if r.get("key") and r.get("reason")}


def carried(claim: str, page_text: str | None) -> list[str]:
    """The claim's anchors found in the control page's own text when they
    show the page carries the claim, else []: every date and number anchor
    when the claim has any, otherwise at least two drug or proper-name
    anchors, all of them found. A page with no text carries nothing."""
    if not page_text or not page_text.strip():
        return []
    anchors = AN.find_anchors(claim)
    strong = [a for a in anchors if a.partition(":")[0] in _STRONG]
    want = strong or anchors
    if not want or (not strong and len(want) < 2):
        return []
    on = AN.found_on(want, page_text)
    return on if len(on) == len(want) else []


def retire(out: Path, key: str, reason: str, *, by: str = "rule", source: str = "") -> None:
    """Record one retired control (idempotent per key; the file keeps SMD's rows)."""
    with _WRITE:
        p = path(out)
        body = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {"retired": []}
        rows = [r for r in body.get("retired") or [] if r.get("key") != key]
        rows.append(
            {
                "key": key,
                "reason": reason[:500],
                "by": by,
                "source": source,
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )
        body["retired"] = rows
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{FILE}.tmp")
        tmp.write_text(json.dumps(body, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(p)


def _first_page_text(pdf: Path) -> str:
    """Page 1's text layer, '' when it has none or cannot be read."""
    try:
        from pypdf import PdfReader

        return PdfReader(str(pdf)).pages[0].extract_text() or ""
    except Exception:  # noqa: BLE001 - no text means the control is judged as before (INVALID when SUPPORTED)
        return ""


def retire_if_carried(
    out: Path, c: dict[str, Any], other: int, block: str | None, pdf: Path, log: Callable[[str], None]
) -> dict[str, Any]:
    """A SUPPORTED control whose page's own text carries the claim's values
    is badly chosen, not a rubber stamp: retired by rule (``carried``), so the
    round is not INVALID on it. The text is the window the verifier read
    (text mode) or page 1's text layer (image mode; a scan has none, and
    stays INVALID as before). Returns the fields for the control's row."""
    text = block if block is not None else _first_page_text(pdf)
    on = carried(c["claim"], text)
    if not on:
        return {}
    retire(
        out,
        c["key"] + f"-ctl{other}",
        f"the control page (Exhibit {other} p.1) states the claim's own values ({', '.join(on[:6])}): "
        "a badly chosen control, retired by rule",
        source=f"Exhibit {other} p.1 text",
    )
    log(f"  -- CONTROL vs Ex{other} SUPPORTED, and its page carries the claim ({', '.join(on[:4])}): retired by rule")
    return {"retired": "rule", "anchors_on_control": on}
