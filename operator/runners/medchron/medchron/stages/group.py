"""`group`: a unit's source files into provider record-sets, one per exhibit.
$0.

Provider attribution comes from the MAP INDEX, what the record itself states,
not from the file name or the folder it sits in (a file named for one clinic
was another's per the record's own clinic block). Folder is the fallback for
files that produced no index rows (billing-only sources), walking up past
filing-convention names to the nearest ancestor that names someone; a
matter-root file with no brand in its name goes to ONE sentinel lane that
must be resolved before exhibits, never to a lane of its own.

The facility alias table lives in the firm config (`providers.aliases`): it
grew by hand on every delivered matter when it lived in source, and an alias
is a fact about how one firm's records name its providers. Brands decide
first, against the original string, because a department-first record name
("Internal Medicine, Example Health System Fairfield") otherwise loses its brand
to the trailing-bare-name rule and draws a lane per department.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from .base import StageRun, read_json

# Generic filing-convention folder names: a document kind, never a provider.
# An open set; the undated-lane presumption below is the structural backstop.
KIND_FOLDERS = {
    "MEDICAL",
    "RECORDS",
    "BILLS",
    "INVOICES",
    "REFERRALS",
    "REPORTS",
    "BILLING",
    "AUTHS",
    "CORRESPONDENCE",
    "MISC",
    "OTHER",
    "(EMAIL ATTACHMENT)",
    "EMAILS",
    "EMAILS ALL",
    "PHOTOS",
    "MEDIA",
    "PLEADINGS",
    "DISCOVERY",
}
CRED = r"M\.?D|D\.?O|D\.?C|P\.?T|N\.?P|PA-?C|LCSW|Au\.?D|R\.?N|M\.?A|D\.?P\.?M|O\.?D|Ph\.?D"
CLINICIAN = re.compile(rf"\s*\([^()]*?\b(?:{CRED})\b[^()]*\)", re.I)
# The name atom takes a star, not a plus: a middle initial otherwise fails the
# whole pattern and every clinician-bearing variant survives as its own lane.
NAME = r"[A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){0,3}"
CRED_SEG = re.compile(rf"^\s*(?:{CRED})\.?\s*$", re.I)
NAME_CRED = re.compile(rf"^\s*{NAME},?\s*(?:{CRED})\.?\s*$", re.I)
BARE_NAME = re.compile(rf"^\s*{NAME}\s*$")
# A capitalised trailing segment is not automatically a person: a department
# name carrying a facility word stays.
FACILITY_WORD = re.compile(
    r"(?i)\b(department|dept|center|centre|clinic|hospital|medicine|medical|imaging|radiology|lab|laboratory|"
    r"therapy|surgery|surgical|health|orthoped|neurolog|chiropractic|emergency|urgent|care|associates|group|"
    r"institute|services|pharmacy|periop|oncology|cancer)\b"
)
CORP = re.compile(r"(?i)^(inc|llc|l\.l\.c|pc|p\.c|apc|a\.p\.c|corp|ltd|co)\.?$")
INDEX_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAP_FILE = re.compile(r"map-\d+(-\d+)?\.md$")


class Canon:
    def __init__(self, cfg: Any) -> None:
        self.aliases = [
            (re.compile(str(a["match"]), re.I), str(a["label"])) for a in (cfg.get("providers", "aliases") or [])
        ]
        self.unresolved = str(cfg.get("providers", "unresolved_label") or "(unattributed - resolve before exhibits)")
        self.suffix = re.compile(str(cfg.get("folders", "status_suffix_regex") or r"(?!x)x"))

    def brand(self, text: str) -> str | None:
        for rx, label in self.aliases:
            if rx.search(text or ""):
                return label
        return None

    def __call__(self, name: str) -> str:
        """Collapse clinician variants, preserve facility identity."""
        raw = (name or "").strip()
        hit = self.brand(raw)
        if hit:
            return hit
        n = CLINICIAN.sub("", raw).strip()
        segs = [s.strip() for s in n.split(",")]
        keep: list[str] = []
        i = 0
        while i < len(segs):
            s = segs[i]
            if NAME_CRED.match(s):
                i += 1
                continue
            if BARE_NAME.match(s) and not FACILITY_WORD.search(s) and i + 1 < len(segs) and CRED_SEG.match(segs[i + 1]):
                i += 2
                continue
            if BARE_NAME.match(s) and not FACILITY_WORD.search(s) and len(segs) > 1 and i == len(segs) - 1 and keep:
                i += 1
                continue
            keep.append(s)
            i += 1
        stripped = ", ".join(x for x in keep if x).strip(" -,")
        # A solo practice IS the clinician's name; if stripping leaves nothing
        # a reader could identify, keep the original.
        if not stripped or CORP.match(stripped) or len(stripped) < 4:
            stripped = CLINICIAN.sub("", raw).strip(" -,")
        hit = self.brand(stripped)
        if hit:
            return hit
        n = self.suffix.sub("", stripped).strip(" -,")
        n = re.sub(r"\s+", " ", n)
        return n.title() if n.isupper() else n


def index_rows(run_dir) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    idx_dates: dict[str, list[str]] = defaultdict(list)
    idx_prov: dict[str, list[str]] = defaultdict(list)
    for p in sorted(run_dir.iterdir()):
        if not MAP_FILE.match(p.name):
            continue
        m = re.search(r"##\s*INDEX\s*\n(.*?)(?=\n##\s|\Z)", p.read_text(encoding="utf-8"), re.S)
        if not m:
            continue
        for line in m.group(1).splitlines():
            parts = [x.strip() for x in line.split("|")]
            if len(parts) >= 4 and INDEX_DATE.match(parts[0]):
                idx_dates[parts[3]].append(parts[0])
                idx_prov[parts[3]].append(parts[1])
    return idx_dates, idx_prov


def _folder_provider(f: dict[str, Any], canon: Canon, unit_prefixes: list[str]) -> str:
    segs = [s for s in (f.get("folder") or "").split("/") if s and s != "(root)"]
    for s in reversed(segs):
        if s.upper() in KIND_FOLDERS or any(s.startswith(p.strip("/")) for p in unit_prefixes if p):
            continue
        if re.fullmatch(r"[\d\s.,/-]+", s):  # a policy-limit notation, never a provider
            continue
        return canon(s)
    # A matter-root file has no folder to speak for it: a brand in the NAME is
    # real attribution; otherwise a filename is not an organisation.
    return canon.brand(f.get("name") or "") or canon.unresolved


def run(sr: StageRun) -> int:
    d = sr.slug_dir
    (d / "groups").mkdir(parents=True, exist_ok=True)
    files = read_json(d / "units" / f"{sr.unit.unit}.json", [])
    canon = Canon(sr.cfg)
    idx_dates, idx_prov = index_rows(d / "runs" / sr.unit.unit)
    unit_prefixes = [u.folder_prefix for u in sr.job.units if getattr(u, "folder_prefix", None)]
    groups: dict[str, dict[str, list]] = defaultdict(lambda: {"file_ids": [], "dates": []})
    unattributed: list[str] = []
    for f in files:
        fname = f["name"] + (f.get("ext") or "")
        provs = idx_prov.get(fname) or []
        if provs:
            # Facility identity beats individual clinician: the exhibit belongs
            # to the facility that produced the record set.
            facility_hits = [p for p in provs if canon.brand(p)]
            pool = facility_hits or provs
            prov = canon(max(set(pool), key=pool.count))
        else:
            prov = _folder_provider(f, canon, unit_prefixes) or canon.unresolved
            unattributed.append(f["name"])
        groups[prov]["file_ids"].append(f["id"])
        groups[prov]["dates"] += idx_dates.get(fname, [])
    out = []
    for prov, g in groups.items():
        ds = sorted(set(g["dates"]))
        out.append(
            {
                "provider": prov,
                "file_ids": g["file_ids"],
                "first": ds[0] if ds else "9999-99-99",
                "last": ds[-1] if ds else "",
                "dated_files": len(ds),
            }
        )
    out.sort(key=lambda x: x["first"])
    # An undated lane is PRESUMED not to be an exhibit (lien paperwork, vendor
    # invoices, transport folders). Presumed, not proven: build_exhibits
    # overrides it for any lane the text actually cites and hard-refuses only
    # a cited sentinel. Marked, never deleted, so coverage still sees every file.
    for g in out:
        g["exhibit"] = g["dated_files"] > 0 and g["provider"] != canon.unresolved
    (d / "groups" / f"{sr.unit.unit}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    for g in out:
        span = g["first"] if g["first"] != "9999-99-99" else "(no dated entries)"
        sr.log(
            f"{g['provider'][:44]:44s} {len(g['file_ids']):3d} files  {span} .. {g['last']}"
            + ("" if g["exhibit"] else "  [NOT AN EXHIBIT]")
        )
    sentinel = sum(len(g["file_ids"]) for g in out if g["provider"] == canon.unresolved)
    if sentinel:
        sr.log(f"{sentinel} file(s) in the sentinel lane: a filename is not a provider; resolve before exhibits")
    if unattributed:
        sr.log(f"{len(unattributed)} file(s) with no index attribution (grouped by folder)")
    return 0
