"""`gate`: the drafting discipline's mechanical gates over the finished document.

The same checker and disposition table demand's gate uses
(``smokeball_connector.record_check``), with drafting's sources: every
document the digest was built from (machine-transcribed ones as vision
sources), plus the firm's own authored text (house style, the class skeleton,
the declaration and proof-of-service attachments), which is the firm's words
and must not read as invented. The emails behind the privilege wall ride as
held-out sources, names stripped (demand's ``gate.strip_names``), so the
leakage gate proves none of their text reached the document.

Not demand's ``gate.collect``: it reads demand inputs (the voice's fixed
strings, the demand variant's skeleton).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..demand.gate import strip_names


def collect(
    data: Path, firm: Any, cls: str
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
    rows = [json.loads(ln) for ln in (data / "extracted.jsonl").read_text(encoding="utf-8").splitlines() if ln]
    tp = data / "transcribed.json"
    machine = set(json.loads(tp.read_text(encoding="utf-8"))) if tp.is_file() else set()
    sources, vision = [], []
    for r in rows:
        p = r.get("text_path")
        if not p or not Path(p).is_file():
            continue
        text = Path(p).read_text(encoding="utf-8", errors="replace")
        (vision if str(r.get("name")) in machine else sources).append((str(r.get("name")), text))
    sources += [
        ("firm house style", firm.style),
        (f"firm {cls} skeleton", firm.skeleton(cls)),
        ("firm declaration attachment", firm.attachment("decl_2030_050")),
        ("firm proof of service attachment", firm.attachment("pos")),
    ]
    wp = data / "walled.json"
    walled = json.loads(wp.read_text(encoding="utf-8")) if wp.is_file() else []
    names = [str(r.get("name") or "") for r in rows] + [str(w.get("name") or "") for w in walled]
    held = [
        (
            f"held-out {w['name']}",
            strip_names(Path(w["text_path"]).read_text(encoding="utf-8", errors="replace"), names),
        )
        for w in walled
        if w.get("text_path") and Path(w["text_path"]).is_file()
    ]
    return sources, vision, held


def run(data: Path, firm: Any, cls: str, md: str, name: str = "gate.json") -> dict[str, Any]:
    from smokeball_connector.record_check import run_record_check

    sources, vision, held = collect(data, firm, cls)
    v = run_record_check(
        md, sources + held, held_out_names={n for n, _ in held}, unextractable=[], vision_sources=vision
    )
    out = {
        "passed": v.passed,
        "disposition": v.disposition,
        "refusals": v.refusals,
        "warnings": v.warnings,
        "infos": v.infos,
        "checked_sources": v.checked_sources,
    }
    (data / name).write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def source_texts(data: Path, firm: Any, cls: str) -> list[str]:
    """What demand's ``quotefix.repair`` searches for a quotation's region."""
    sources, vision, _held = collect(data, firm, cls)
    return [t for _, t in sources + vision]
