"""`filter`: scope pre-incident treatment to what bears on the claim. Paid
(judgment tier), one call.

Post-incident entries are kept in full, always. Pre-incident entries are
classified keep/omit by one model call working ONLY from each entry's own
diagnoses and headings, and the decision is recorded per entry so the firm
can audit it. Omitted entries are NOT deleted silently: the document gains a
note stating how many pre-incident encounters were reviewed and not itemized,
with their date span, and the full unfiltered assembly stays on disk.

Refuses (exit 1) to scope a document whose merged clusters are missing: a
merge once failed, merged.md never appeared, and this step happily produced
a 155-entry document with all 34 clusters absent and nothing saying so.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .. import llm, prompts
from .base import StageRun

ENTRY = re.compile(r"(?m)^(?=\d{2}/\d{2}/\d{4}\s*(?:\(|$))")
DATE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})")
VERDICT = re.compile(r"\s*(\d+)\s*\|\s*(MATERIAL|OMIT)\s*\|\s*(.*)")
GRAB_HEADINGS = ("Medical Diagnoses", "Patient Complaints & Limitations", "HPI & Prior Medical History")
NOTE = (
    "[NTD: {n} additional pre-incident encounters dated {span} were reviewed and are not itemized in this "
    "chronology. They record routine or unrelated care with no bearing on the claimed injuries. They remain in "
    "the exhibits and can be itemized on request.]"
)


def coclient_name(entry_text: str, client_name: str, surname: str) -> str | None:
    """The OTHER patient this entry is attributed to, or None.

    On a joint matter the composer labels an entry's patient in the provider
    line's parenthetical: `Some Radiology Group (Other Patient Name) | ...`.
    A chronology is one patient's, so an entry labelled with a DIFFERENT
    patient is not hers however it reached the corpus.

    The test is deliberately narrow: the parenthetical must contain this
    unit's SURNAME (co-plaintiffs on a motor-vehicle matter are related and
    share it) and must not contain the client's own given name. That keeps
    clinical parentheticals - `VacaValley Home Training (PD)`, `(CMS-1500)`,
    `(second interpretation)` - which carry no surname and must never be
    dropped.

    Live 2026-09-23 on a two-plaintiff matter: folder scoping kept every file out of the
    co-client's folder, and 29 of her entries still reached the document
    because her records sit inside files filed under the client's own folder
    and the matter root - same collision, same providers, same dates. Folders
    cannot separate patients; only attribution can.
    """
    lines = [ln for ln in entry_text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    given = (client_name or "").split()[0].lower()
    for m in re.finditer(r"\(([^)]{3,60})\)", lines[1]):
        inside = m.group(1)
        if surname.lower() in inside.lower() and given and given not in inside.lower():
            return inside.strip()
    return None


def drop_coclient(sr: StageRun, d: Path, entries: list[dict[str, str]]) -> list[dict[str, str]]:
    """Entries belonging to this unit's client only.

    A chronology is ONE patient's, so an entry the composer attributed to a
    different patient leaves before anything else is decided about it - before
    the pre/post-incident split, before the materiality call. What left is
    recorded in `omitted_coclient.json` so the firm can audit the removal.
    """
    found = [(e, who) for e in entries if (who := coclient_name(e["text"], sr.unit.client_name, sr.unit.surname))]
    if not found:
        return entries
    drop = {id(e) for e, _ in found}
    sr.log(f"  co-client entries removed: {len(found)} ({', '.join(sorted({w for _, w in found}))[:80]})")
    (d / "omitted_coclient.json").write_text(
        json.dumps(
            [{"date": e["iso"], "patient": who, "head": e["text"].splitlines()[1][:120]} for e, who in found],
            indent=1,
        ),
        encoding="utf-8",
    )
    return [e for e in entries if id(e) not in drop]


def split_entries(text: str) -> list[dict[str, str]]:
    entries = []
    for e in ENTRY.split(text):
        e = e.strip()
        m = DATE.match(e)
        if m:
            entries.append({"iso": f"{m.group(3)}-{m.group(1)}-{m.group(2)}", "text": e})
    entries.sort(key=lambda x: x["iso"])
    return entries


def brief(i: int, e: dict[str, str]) -> str:
    """One numbered line per pre-incident entry: its header and up to two
    diagnosis/complaint lines, nothing else reaches the model."""
    lines = e["text"].splitlines()
    head = lines[1] if len(lines) > 1 else ""
    dx: list[str] = []
    grab = False
    for ln in lines:
        s = ln.strip()
        if s in GRAB_HEADINGS:
            grab = True
            continue
        if grab:
            if s and not ln.startswith(" ") and s.istitle() and len(s) < 60 and "(" not in s:
                grab = False
                continue
            if s:
                dx.append(s[:400])
                if len(dx) >= 2:
                    grab = False
    return f"{i}. {e['iso']} | {head[:90]}\n   {' '.join(dx)[:600]}"


def run(sr: StageRun) -> int:
    d = sr.slug_dir / "runs" / sr.unit.unit
    incident = sr.job.incident_date
    text = (d / "entries.md").read_text(encoding="utf-8")
    clusters, merged_p = d / "clusters.md", d / "merged.md"
    n_clusters = (
        len(re.findall(r"(?m)^##### CLUSTER ", clusters.read_text(encoding="utf-8"))) if clusters.is_file() else 0
    )
    if n_clusters and (not merged_p.is_file() or merged_p.stat().st_size < 50):
        sr.log(f"REFUSING TO SCOPE: {n_clusters} merged cluster(s) expected but merged.md is missing or empty")
        return 1
    merged = merged_p.read_text(encoding="utf-8") if merged_p.is_file() else ""
    full = (text + "\n\n" + merged).strip()
    if not (d / "entries_full.md").is_file():
        (d / "entries_full.md").write_text(full, encoding="utf-8")
    entries = drop_coclient(sr, d, split_entries(full))
    pre = [e for e in entries if e["iso"] < incident]
    post = [e for e in entries if e["iso"] >= incident]
    sr.log(f"{sr.unit.unit}: {len(pre)} pre-incident, {len(post)} post-incident")
    verdict: dict[int, tuple[str, str]] = {}
    if pre:
        payload = f"CLAIM INJURIES: {sr.job.injuries}\n\nPRE-INCIDENT ENCOUNTERS:\n\n" + "\n\n".join(
            brief(i, e) for i, e in enumerate(pre, 1)
        )
        r = sr.doorway.call(
            "filter",
            model=llm.model_for(sr.cfg, "judgment"),
            system=prompts.load("filter-system", sr.cfg),
            max_tokens=16000,
            messages=[{"role": "user", "content": payload}],
            effort="high",
            stream=True,
            custom_id="filter",
        )
        (d / "preincident_triage.txt").write_text(r.text, encoding="utf-8")
        for line in r.text.splitlines():
            m = VERDICT.match(line)
            if m:
                verdict[int(m.group(1))] = (m.group(2), m.group(3).strip())
    keep_pre, omit_pre = [], []
    for i, e in enumerate(pre, 1):
        v = verdict.get(i)
        (keep_pre.append(e) if v is None or v[0] == "MATERIAL" else omit_pre.append((e, v[1])))  # unclassified KEEPS
    sr.log(f"  pre-incident: {len(keep_pre)} material, {len(omit_pre)} omitted")
    unclassified = [i for i in range(1, len(pre) + 1) if i not in verdict]
    if unclassified:
        sr.log(f"  {len(unclassified)} unclassified, kept by default")
    kept = sorted(keep_pre + post, key=lambda x: x["iso"])
    (d / "entries_scoped.md").write_text("\n\n".join(e["text"] for e in kept), encoding="utf-8")
    (d / "omitted_preincident.json").write_text(
        json.dumps(
            [
                {
                    "date": e["iso"],
                    "reason": why,
                    "head": e["text"].splitlines()[1] if len(e["text"].splitlines()) > 1 else "",
                }
                for e, why in omit_pre
            ],
            indent=1,
        ),
        encoding="utf-8",
    )
    if omit_pre:
        span = f"{omit_pre[0][0]['iso']} to {omit_pre[-1][0]['iso']}"
        (d / "preincident_note.txt").write_text(NOTE.format(n=len(omit_pre), span=span), encoding="utf-8")
        sr.log(f"  disclosure note written ({span})")
    sr.log(f"  scoped document: {len(kept)} entries")
    return 0
