"""`merge`: same-date, same-provider fragments become one entry. Code merges
what is set arithmetic over text the map pass already wrote (union the
paragraphs, keep every citation verbatim, collapse only same-citation exact
text or exact containment, headings in canonical order, fullest provider
wording), and routes to the model only the clusters code will not decide:

  * a cluster it cannot parse exactly (unknown heading, uncited paragraph, a
    fragment whose date or provider disagrees with the cluster header, a
    fragment count that disagrees, date labels that disagree);
  * two same-subsection paragraphs that read as the same sentence but carry
    different numbers or dates (the disagreement the prompt marks; code never
    writes that marker);
  * two same-citation paragraphs that read as the same sentence reworded.

Thirteen ledgers priced the model doing all of this at 8% of a run. Whatever
merged the cluster, the falsifier proves nothing was lost before the stage
exits 0; a lost citation, paragraph or entry is exit 3, 4 or 5.
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import merge_falsify as mf, merge_model
from .assemble import norm_provider
from .base import StageRun

JACCARD_ROUTE = 0.8
NUMBER_ROUTE = 0.5  # number-set Jaccard that marks two paragraphs as the
NUMBER_SHARED = 3  # same measurements, when they share this many
NUMBER = re.compile(r"\d+(?:[./:\-]\d+)*")
WORD = re.compile(r"[a-z0-9]+")


class RouteError(Exception):
    pass


def parse_fragment(text: str, cluster: dict[str, Any], hd: mf.Headings) -> dict[str, Any]:
    lines = [ln.rstrip() for ln in text.strip().splitlines()]
    if len(lines) < 2:
        raise RouteError("fragment shorter than its two-line header")
    m = mf.DATE_LINE.match(lines[0].strip())
    if not m:
        raise RouteError(f"bad date line: {lines[0][:40]!r}")
    iso = f"{m.group(3)}-{m.group(1)}-{m.group(2)}"
    if iso != cluster["date"]:
        raise RouteError(f"fragment date {iso} != cluster {cluster['date']}")
    label = (m.group(4) or "").strip()
    head = lines[1].strip()
    if "|" not in head:
        raise RouteError("provider line without '|'")
    provider, _, first = head.rpartition("|")
    provider, first = provider.strip(), first.strip()
    if norm_provider(provider) != cluster["key"]:
        raise RouteError(f"provider key {norm_provider(provider)!r} != cluster key {cluster['key']!r}")
    first_h = hd.canon_heading(first)
    if first_h is None:
        raise RouteError(f"unknown first heading {first[:40]!r}")
    paras: list[tuple[str, str, str]] = []
    buf: list[str] = []
    cur = first_h
    for raw in lines[2:]:
        line = raw.strip()
        if not line:
            if buf:
                raise RouteError(f"uncited paragraph: {' '.join(buf)[:60]!r}")
            continue
        if not buf:
            h = hd.canon_heading(line)
            if h is not None:
                cur = h
                continue
            # A bare Title Case line that is not a heading we know and carries
            # no citation is a heading outside the menu, never prose.
            if (
                not mf.CITE.search(line)
                and len(line) < 60
                and line[:1].isupper()
                and not line.endswith((".", ",", ";"))
            ):
                raise RouteError(f"unknown heading {line[:40]!r}")
        buf.append(line)
        if mf.CITE_END.search(line):
            prose, cite = mf.split_cited(" ".join(buf))
            paras.append((cur, prose, cite))
            buf = []
    if buf:
        raise RouteError(f"uncited paragraph: {' '.join(buf)[:60]!r}")
    if not paras:
        raise RouteError("fragment carries no cited paragraph")
    return {"label": label, "provider": provider, "first_heading": first_h, "paras": paras}


def _tokens(prose: str) -> set[str]:
    return set(WORD.findall(prose.lower()))


def _jaccard(a: set, b: set) -> float:
    return 1.0 if not a and not b else len(a & b) / len(a | b)


def merge_cluster(cluster: dict[str, Any], hd: mf.Headings) -> tuple[str | None, list[str]]:
    """(entry_text, reasons); entry_text is None when routed to the model."""
    reasons: list[str] = []
    if cluster["date"] is None:
        return None, ["cluster header did not parse"]
    if len(cluster["fragments"]) != cluster["n"]:
        return None, [f"header says {cluster['n']} fragments, found {len(cluster['fragments'])}"]
    frags = []
    for f in cluster["fragments"]:
        try:
            frags.append(parse_fragment(f, cluster, hd))
        except RouteError as exc:
            reasons.append(str(exc))
    if reasons:
        return None, reasons
    labels = {f["label"] for f in frags}
    if len(labels) > 1:
        return None, [f"date labels differ: {sorted(labels)}"]
    label = labels.pop()
    by_heading: dict[str, list[tuple[str, str, str]]] = {}
    seen: set[tuple[str, str]] = set()
    for f in frags:
        for h, prose, cite in f["paras"]:
            k = (mf.norm_text(prose), cite)
            if k in seen:
                continue
            seen.add(k)
            by_heading.setdefault(h, []).append((prose, cite, k[0]))
    for h, plist in by_heading.items():  # containment collapse, same citation only
        by_heading[h] = [
            (prose, cite, n)
            for i, (prose, cite, n) in enumerate(plist)
            if not any(j != i and c2 == cite and n != n2 and n in n2 for j, (_, c2, n2) in enumerate(plist))
        ]
    for h, plist in by_heading.items():  # near-duplicates the code will not adjudicate
        for i in range(len(plist)):
            for j in range(i + 1, len(plist)):
                pa, ca, _ = plist[i]
                pb, cb, _ = plist[j]
                jac = _jaccard(_tokens(pa), _tokens(pb))
                na, nb = set(NUMBER.findall(pa)), set(NUMBER.findall(pb))
                njac = _jaccard(na, nb) if (na or nb) else 1.0
                conflict = bool(na - nb) and bool(nb - na)
                if conflict and (jac >= JACCARD_ROUTE or (njac >= NUMBER_ROUTE and len(na & nb) >= NUMBER_SHARED)):
                    reasons.append(
                        f"{h}: near-duplicate (J={jac:.2f}, numbers J={njac:.2f}) with different "
                        f"numbers/dates ({ca} vs {cb})"
                    )
                elif jac >= JACCARD_ROUTE and ca == cb:
                    reasons.append(f"{h}: same-citation reworded pair (J={jac:.2f}) {ca}")
    if reasons:
        return None, reasons
    provider = max((f["provider"] for f in frags), key=len)
    headings = sorted(by_heading, key=hd.index)
    first = headings[0]
    out = [
        f"{cluster['date'][5:7]}/{cluster['date'][8:10]}/{cluster['date'][:4]}" + (f" {label}" if label else ""),
        f"{provider} | {first}",
        "",
    ]
    for h in headings:
        if h != first:
            out += [h, ""]
        for prose, cite, _ in by_heading[h]:
            out += [f"{prose} {cite}", ""]
    return "\n".join(out).rstrip() + "\n", []


def merge_all(clusters_text: str, hd: mf.Headings) -> tuple[list[dict], dict[int, str], list[dict]]:
    clusters = mf.parse_clusters(clusters_text)
    code: dict[int, str] = {}
    route: list[dict[str, Any]] = []
    for i, c in enumerate(clusters, 1):
        entry, reasons = merge_cluster(c, hd)
        if entry is None:
            route.append({"id": i, "date": c["date"], "key": c["key"], "reasons": reasons})
        else:
            code[i] = entry
    return clusters, code, route


def run(sr: StageRun) -> int:
    d = sr.slug_dir / "runs" / sr.unit.unit
    src = (d / "clusters.md").read_text(encoding="utf-8")
    if not src.strip():
        (d / "merged.md").write_text("", encoding="utf-8")
        sr.log("no clusters to merge")
        return 0
    hd = mf.Headings.from_config(sr.cfg)
    clusters, code, route = merge_all(src, hd)
    n = len(clusters)
    sr.log(f"{n} clusters: {len(code)} merged in code, {len(route)} routed to the model ({len(route) / n * 100:.0f}%)")
    for r in route:
        sr.log(f"  route #{r['id']} {r['date']} {r['key']}: {'; '.join(r['reasons'])[:140]}")
    (d / "merged_code.md").write_text("\n".join(code[i] for i in sorted(code)), encoding="utf-8")
    (d / "merge_route.json").write_text(
        json.dumps({"clusters": n, "code": sorted(code), "routed": route}, indent=1), encoding="utf-8"
    )
    model: dict[int, str] = {}
    if route:
        blocks = [b for b in mf.CLUSTER_SPLIT.split(src.strip()) if b.strip()]
        picked = [blocks[r["id"] - 1] for r in route]
        merged_model = merge_model.merge_blocks(sr, d, picked, hd)
        if merged_model is None:
            sr.log("model merge failed; merged.md NOT written")
            return 1
        (d / "merged_model.md").write_text(merged_model, encoding="utf-8")
        # the model wrote the routed clusters in their cluster order, and its
        # own falsifier proved one entry per cluster, so the k-th entry is the
        # k-th routed id.
        for r, e in zip(route, mf.parse_entries(merged_model)):
            model[r["id"]] = e["body"].rstrip() + "\n"
    parts = [code.get(i) or model.get(i) or "" for i in range(1, n + 1)]
    (d / "merged.md").write_text("\n\n".join(p.rstrip() for p in parts), encoding="utf-8")
    rc, rep = mf.check(src, (d / "merged.md").read_text(encoding="utf-8"), hd)
    for line in rep:
        sr.log(line)
    return rc
