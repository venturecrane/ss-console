"""medchron_cross_client_gate: on a joint matter, a file whose TEXT names the
other unit's client (surname or date of birth) more than its own is flagged
before composition, where the correction is $0. A report, not a hard stop,
by design: shared documents (insurance acknowledgments, police reports,
lien requests naming both claimants) are legitimate, and the flag list is
the input to a decision. DOB counts as much as surname: a claim form renders
it unpunctuated. Registered probe: a page naming the other client's DOB and
none of its own is flagged.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..stages.base import read_json


def dob_variants(dob: str | None) -> list[str]:
    m = re.match(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", dob or "")
    if not m:
        return []
    mo, dy, yr = m.groups()
    mo2, dy2 = mo.zfill(2), dy.zfill(2)
    return sorted(
        {f"{mo2}/{dy2}/{yr}", f"{int(mo)}/{int(dy)}/{yr}", f"{mo2}-{dy2}-{yr}", f"{yr}-{mo2}-{dy2}", f"{mo2}{dy2}{yr}"}
    )


def score(text: str, ident: dict[str, Any]) -> int:
    hits = 0
    if ident.get("surname"):
        hits += len(re.findall(rf"(?i)(?<![A-Za-z]){re.escape(ident['surname'])}(?![A-Za-z])", text))
    low = text.lower()
    return hits + sum(low.count(v.lower()) for v in ident.get("dob_variants", []))


def identities(units: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        u: {"surname": rule.get("surname") or rule.get("name_token"), "dob_variants": dob_variants(rule.get("dob"))}
        for u, rule in units.items()
    }


def flags(slug_dir: Path) -> tuple[list[dict[str, Any]], int, list[str]]:
    """(flags, files checked, units without a DOB). Empty when the matter has
    one unit: there is no other client to name."""
    spec = read_json(slug_dir / "units.json", None) or {}
    if len(spec) < 2:
        return [], 0, []
    idents = identities(spec)
    out: list[dict[str, Any]] = []
    checked = 0
    for u in spec:
        for r in read_json(slug_dir / "units" / f"{u}.json", []):
            tp = Path(r.get("text_path") or (slug_dir / "text" / f"{r['id']}.txt"))
            if not tp.is_file():
                continue
            text = tp.read_text(encoding="utf-8", errors="replace")
            checked += 1
            own = score(text, idents[u])
            for u2 in spec:
                if u2 == u:
                    continue
                other = score(text, idents[u2])
                if other > own and other > 0:
                    out.append(
                        {
                            "unit": u,
                            "names": u2,
                            "file": r.get("name"),
                            "folder": r.get("folder"),
                            "other": other,
                            "own": own,
                        }
                    )
    return out, checked, [u for u, i in idents.items() if not i["dob_variants"]]
