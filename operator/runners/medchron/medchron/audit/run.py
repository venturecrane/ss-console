"""One audit round: every live claim in the chronology body, verified against
its cited pages, resumable by key, with controls.

CONTROL is non-optional: every control_every-th claim is re-run against a
page from a DIFFERENT exhibit and MUST come back unsupported. A verifier that
rubber-stamps makes every verdict above it worthless, so a wrongly SUPPORTED
control makes the round INVALID (2) and nothing repairs on an invalid audit.

TEXT MODE (`levers.audit_mode: text`): a claim whose cited pages all carry a
native text layer is audited against a cached window of page text; the
window is one cluster, one worker task, so the cache reads land inside the
block's life. The text verdict is only ever trusted to SAY yes: anything else
is re-decided on the image path, and one in eight text-SUPPORTED verdicts is
re-audited in image mode as a REVERSE CONTROL; a disagreement fails the
round exactly as a rubber stamp does. The audit is never batched.

DOUBLE-SWEEP GUARD: prior rows for THIS body whose keys are no longer
produced mean the hashing or extraction changed under an unchanged document,
and resuming would re-bill every claim: exit 3.

Return codes: 0 clean, 1 problems (repair), 2 invalid, 3 guard.
"""
from __future__ import annotations

import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .. import llm
from . import anchors as AN, claims as CL, verify as VF
from .page_text import PageIndex, exhibit_paths
from .render import img_block, render

SEED = 20260820
REVERSE_EVERY = 8
WINDOW_CAP = 12
TEXT_MIN_CHARS = 80
ERROR = {"verdict": "ERROR", "unsupported_assertions": [], "contradictions": []}


@dataclass
class AuditPaths:
    slug_dir: Path
    unit: str

    @property
    def doc(self) -> Path:
        return self.slug_dir / "runs" / self.unit / "final-chronology.md"

    @property
    def out(self) -> Path:
        return self.slug_dir / "out" / self.unit

    @property
    def pages(self) -> Path:
        return self.out / "auditpages"

    @property
    def results(self) -> Path:
        return self.out / "audit-results.jsonl"


@dataclass
class Round:
    doorway: llm.Doorway
    model: str
    paths: AuditPaths
    log: Callable[[str], None]
    mode: str = "image"
    workers: int = 4
    control_every: int = 8
    sample: int = 0
    force: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)
    counter: dict[str, int] = field(default_factory=lambda: {"n": 0, "text_ok": 0, "reverse_bad": 0})

    # ---- one claim, image path -------------------------------------------
    def image_verdict(self, c: dict[str, Any], pdfs: dict[int, Path]) -> tuple[dict[str, Any], list[int] | None]:
        """Cited pages, then widen once: 74% of one matter's flags were
        citation-window artifacts, content one page outside the cited span. A
        claim that passes widened is a CITATION defect, recorded as
        SUPPORTED_WIDENED and never plain SUPPORTED, so the citation rewrite
        downstream cannot be skipped silently."""
        ex = c["exhibit"]
        try:
            imgs = [img_block(p) for p in (render(pdfs[ex], pg, self.paths.pages, f"ex{ex}") for pg in c["pages"]) if p]
            if not imgs:
                raise RuntimeError("no page rendered")
            v = VF.verify_image(self.doorway, self.model, c["claim"], imgs, f"Exhibit {ex} p.{c['page_spec'] or '1'}",
                                custom_id=c["key"])
        except Exception as exc:  # noqa: BLE001 - an error is a verdict row, never a dead worker
            v = {**ERROR, "note": str(exc)[:200]}
        widened = None
        if v["verdict"] in ("PARTIAL", "UNSUPPORTED"):
            wide = sorted(set(c["pages"]) | {min(c["pages"]) - 1, max(c["pages"]) + 1})
            wide = [p for p in wide if 1 <= p <= c["npages"]]
            if wide != c["pages"]:
                try:
                    wimgs = [img_block(p) for p in (render(pdfs[ex], pg, self.paths.pages, f"ex{ex}") for pg in wide) if p]
                    wv = VF.verify_image(self.doorway, self.model, c["claim"], wimgs, f"Exhibit {ex} p.{min(wide)}-{max(wide)}",
                                         custom_id=c["key"] + "-wide")
                    if wv["verdict"] == "SUPPORTED":
                        v, widened = {**wv, "verdict": "SUPPORTED_WIDENED"}, wide
                except Exception:  # noqa: BLE001 - widen is best-effort; the flag stands
                    pass
        return v, widened

    def control_for(self, c: dict[str, Any], pdfs: dict[int, Path], index: PageIndex | None, doc_sha: str, ts: str) -> None:
        with self._lock:
            self.counter["n"] += 1
            do_ctl = self.counter["n"] % self.control_every == 0
        if not do_ctl:
            return
        other = next((o for o in sorted(pdfs) if o != c["exhibit"]), None)
        if other is None:
            return
        text_ctl = None
        if index is not None and c.get("window"):
            pages = [p for p in (1, 2, 3) if p <= (index.npages(other) or 0) and index.eligible(other, p)]
            if pages:
                text_ctl = (pages, index.window_text(other, pages))
        try:
            if text_ctl:
                pages, block = text_ctl
                cv = VF.verify_text(self.doorway, self.model, c["claim"], block, pages, c.get("anchors", []),
                                    f"Exhibit {other} p.1-3", custom_id=c["key"] + f"-ctl{other}")
                kind, extra = f"control-text(vs Ex{other})", {"mode": "text", "window": pages}
            else:
                ci = render(pdfs[other], 1, self.paths.pages, f"ctl{other}")
                cv = VF.verify_image(self.doorway, self.model, c["claim"], [img_block(ci)] if ci else [],
                                     f"Exhibit {other} p.1", custom_id=c["key"] + f"-ctl{other}")
                kind, extra = f"control(vs Ex{other})", {"mode": "image"}
        except Exception as exc:  # noqa: BLE001 - a control verification raising is recorded as an ERROR verdict for that claim; the audit continues
            cv, kind, extra = {**ERROR, "note": str(exc)[:200]}, f"control(vs Ex{other})", {"mode": self.mode}
        cv.pop("supporting_pages", None)
        CL.append_row(self.paths.results, {"key": c["key"] + f"-ctl{other}", "kind": kind, "exhibit": c["exhibit"],
                                           "page_spec": "1", "pages": [1], "claim": c["claim"][:200], **cv, **extra,
                                           "doc_sha": doc_sha, "ts": ts})
        self.log(f"  -- CONTROL Ex{c['exhibit']} claim vs Ex{other}: {cv.get('verdict')}")

    def report_line(self, c: dict[str, Any], v: dict[str, Any]) -> None:
        mark = {"SUPPORTED": "OK", "SUPPORTED_WIDENED": "W+", "PARTIAL": "!!", "UNSUPPORTED": "XX",
                "PAGE_UNREADABLE": "??", "ERROR": "ER"}.get(v.get("verdict"), "??")
        self.log(f"  {mark} Ex{c['exhibit']:<3} p.{(c['page_spec'] or '1'):<8} {str(v.get('verdict')):<15} "
                 f"{str(v.get('note'))[:64]}")

    def run_image(self, c: dict[str, Any], pdfs: dict[int, Path], index: PageIndex | None, doc_sha: str, ts: str) -> None:
        try:
            v, widened = self.image_verdict(c, pdfs)
            rec = {"key": c["key"], "kind": "real", "exhibit": c["exhibit"], "page_spec": c["page_spec"],
                   "pages": c["pages"], "claim": c["claim"][:500], **v, "mode": "image", "doc_sha": doc_sha, "ts": ts}
            if widened:
                rec["widened"] = widened
            CL.append_row(self.paths.results, rec)
            self.report_line(c, v)
            self.control_for(c, pdfs, index, doc_sha, ts)
        except Exception as exc:  # noqa: BLE001 - a worker must never kill the pool
            self.log(f"  ER Ex{c['exhibit']} p.{c['page_spec']} worker died: {str(exc)[:120]}")
            CL.append_row(self.paths.results, {"key": c["key"], "kind": "real", "exhibit": c["exhibit"],
                                               "page_spec": c["page_spec"], "pages": c.get("pages", []),
                                               "claim": c["claim"][:500], **ERROR,
                                               "note": f"worker exception: {str(exc)[:200]}", "mode": "image",
                                               "doc_sha": doc_sha, "ts": ts})

    def run_cluster(self, cl: dict[str, Any], pdfs: dict[int, Path], index: PageIndex, doc_sha: str, ts: str) -> None:
        ex = cl["exhibit"]
        try:
            block = index.window_text(ex, cl["pages"])
        except Exception as exc:  # noqa: BLE001 - a window-text failure is logged and the cluster's claims re-run in image mode below
            self.log(f"  ER Ex{ex} cluster window failed: {str(exc)[:120]}; running its claims in image mode")
            for c in cl["claims"]:
                self.run_image(c, pdfs, index, doc_sha, ts)
            return
        for c in cl["claims"]:
            try:
                cite_label = f"Exhibit {ex} p.{c['page_spec'] or '1'}"
                try:
                    tv = VF.verify_text(self.doorway, self.model, c["claim"], block, c["pages"], c["anchors"], cite_label,
                                        custom_id=c["key"])
                except Exception as exc:  # noqa: BLE001 - a text verification raising is recorded as an ERROR verdict for that claim; the audit continues
                    tv = {**ERROR, "note": str(exc)[:200], "supporting_pages": []}
                rec = {"key": c["key"], "kind": "real", "exhibit": ex, "page_spec": c["page_spec"], "pages": c["pages"],
                       "claim": c["claim"][:500], "mode": "text", "text_then_image": False,
                       "text_verdict": tv["verdict"], "text_note": tv["note"], "supporting_pages": tv["supporting_pages"],
                       "window": cl["pages"], "anchors": c["anchors"], "anchors_found": AN.found_on(c["anchors"], block),
                       "doc_sha": doc_sha, "ts": ts}
                v = {k: tv[k] for k in ("verdict", "unsupported_assertions", "contradictions", "note")}
                if tv["verdict"] != "SUPPORTED":
                    # The text verdict is only ever trusted to SAY yes.
                    iv, widened = self.image_verdict(c, pdfs)
                    v, rec["text_then_image"] = iv, True
                    if widened:
                        rec["widened"] = widened
                rec.update(v)
                CL.append_row(self.paths.results, rec)
                self.report_line(c, v)
                if v["verdict"] == "SUPPORTED" and not rec["text_then_image"]:
                    with self._lock:
                        self.counter["text_ok"] += 1
                        do_rev = self.counter["text_ok"] % REVERSE_EVERY == 0
                    if do_rev:
                        iv, widened = self.image_verdict(c, pdfs)
                        agree = iv["verdict"] == "SUPPORTED"
                        if not agree:
                            with self._lock:
                                self.counter["reverse_bad"] += 1
                        CL.append_row(self.paths.results, {"key": c["key"] + "-rev", "kind": "reverse-control",
                                                           "exhibit": ex, "page_spec": c["page_spec"], "pages": c["pages"],
                                                           "claim": c["claim"][:200], **iv, "mode": "image",
                                                           "text_verdict": "SUPPORTED", "agree": agree,
                                                           "widened": widened, "doc_sha": doc_sha, "ts": ts})
                        self.log(f"  -- REVERSE Ex{ex} p.{c['page_spec'] or '1'}: {'agree' if agree else '!! DISAGREE'}")
                self.control_for(c, pdfs, index, doc_sha, ts)
            except Exception as exc:  # noqa: BLE001 - one claim's worker dying is recorded as that claim's ERROR row; the remaining claims still run
                self.log(f"  ER Ex{ex} p.{c['page_spec']} worker died: {str(exc)[:120]}")
                CL.append_row(self.paths.results, {"key": c["key"], "kind": "real", "exhibit": ex,
                                                   "page_spec": c["page_spec"], "pages": c.get("pages", []),
                                                   "claim": c["claim"][:500], **ERROR,
                                                   "note": f"worker exception: {str(exc)[:200]}", "mode": "text",
                                                   "doc_sha": doc_sha, "ts": ts})

    # ---- the round ---------------------------------------------------------
    def plan_text(self, live: list[dict[str, Any]], index: PageIndex) -> tuple[list[dict], list[dict], dict[str, int]]:
        per_ex: dict[int, list[dict]] = {}
        image: list[dict] = []
        elig: dict[str, int] = {}
        for c in live:
            c["anchors"] = AN.find_anchors(c["claim"])
            np_ = index.npages(c["exhibit"]) or c["npages"]
            win = AN.choose_window(c["pages"], np_, index, c["exhibit"], c["anchors"], cap=WINDOW_CAP)
            if win is None:
                cls = next((index.classify(c["exhibit"], p)[0] for p in c["pages"]
                            if index.classify(c["exhibit"], p)[0] != "native"), "span>cap")
                elig[cls] = elig.get(cls, 0) + 1
                c["window"] = None
                image.append(c)
                continue
            elig["native"] = elig.get("native", 0) + 1
            c["window"] = win
            per_ex.setdefault(c["exhibit"], []).append(c)
        clusters = []
        for ex in sorted(per_ex):
            cs = per_ex[ex]
            by_key = {c["key"]: c for c in cs}
            np_ = index.npages(ex) or cs[0]["npages"]
            for pages, keys in AN.build_clusters([(c["key"], c["window"]) for c in cs], cap=WINDOW_CAP, floor=3,
                                                 index=index, exhibit=ex, npages=np_):
                clusters.append({"exhibit": ex, "pages": pages, "claims": [by_key[k] for k in keys]})
        return clusters, image, elig

    def execute(self) -> int:
        from pypdf import PdfReader

        pdfs = exhibit_paths(self.paths.out)
        if not pdfs:
            body0 = CL.body_of(self.paths.doc.read_text(encoding="utf-8"))
            if CL.CITE.search(body0):
                self.log("no exhibit bytes on disk while the body cites exhibits; build the exhibits first")
                return 2
            self.log("no exhibits and no citations: nothing to audit")
            return 0
        npages: dict[int, int] = {}
        for k, p in pdfs.items():
            try:
                npages[k] = len(PdfReader(str(p)).pages)
            except Exception as exc:  # noqa: BLE001 - an unreadable exhibit is logged and skipped; the audit still runs over the readable ones
                self.log(f"  !! Ex{k} unreadable as PDF: {str(exc)[:90]}")
        body = CL.body_of(self.paths.doc.read_text(encoding="utf-8"))
        claims = CL.extract_claims(body, set(npages))
        doc_sha = CL.doc_sha_of(body)
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        live: list[dict[str, Any]] = []
        oor: list[dict[str, Any]] = []
        for c in claims:
            pgs = CL.parse_pages(c["page_spec"])
            bad = [p for p in pgs if p < 1 or p > npages[c["exhibit"]]]
            if bad:
                oor.append({**c, "pages": pgs, "bad_pages": bad, "doc_pages": npages[c["exhibit"]],
                            "verdict": "PAGE_OUT_OF_RANGE", "kind": "real", "unsupported_assertions": [],
                            "contradictions": [], "note": f"cites page(s) {bad} in a {npages[c['exhibit']]}-page document",
                            "mode": self.mode, "doc_sha": doc_sha, "ts": ts})
                continue
            c["pages"] = pgs or [1]
            c["npages"] = npages[c["exhibit"]]
            live.append(c)
        self.log(f"claims: {len(claims)} | resolvable: {len(live)} | PAGE_OUT_OF_RANGE: {len(oor)} | mode: {self.mode}")
        prior = CL.read_rows(self.paths.results)
        orphans = CL.lineage_orphans(prior, {c["key"] for c in claims}, doc_sha)
        if orphans:
            self.log(f"!! DOUBLE-SWEEP GUARD: {len(orphans)} prior row(s) audited against THIS body under keys no longer "
                     f"produced; continuing would re-bill every claim")
            if not self.force:
                return 3
        for r in oor:
            self.log(f"  XX Ex{r['exhibit']} p.{r['page_spec']} -> doc has {r['doc_pages']} pages")
            CL.append_row(self.paths.results, r)
        if self.sample:
            rnd = random.Random(SEED)
            by_ex: dict[int, list[dict]] = {}
            for c in live:
                by_ex.setdefault(c["exhibit"], []).append(c)
            picked: list[dict] = []
            for k in sorted(by_ex):
                group = sorted(by_ex[k], key=lambda x: x["key"])
                picked.extend(group if len(group) <= self.sample else rnd.sample(group, self.sample))
            self.log(f"stratified sample seed={SEED}: {len(picked)} of {len(live)}")
            live = picked
        have = CL.done_keys(prior)
        todo = [c for c in live if c["key"] not in have]
        self.log(f"already verified: {len(have)} | this run: {len(todo)}")
        index: PageIndex | None = None
        clusters: list[dict] = []
        image_claims = todo
        if self.mode == "text":
            index = PageIndex(self.paths.slug_dir, self.paths.unit, min_chars=TEXT_MIN_CHARS)
            clusters, image_claims, elig = self.plan_text(todo, index)
            self.log(f"text-eligible claims: {sum(len(cl['claims']) for cl in clusters)}/{len(todo)} | image: "
                     f"{len(image_claims)} | " + ", ".join(f"{k}={v}" for k, v in sorted(elig.items())))
        tasks: list[Callable[[], None]] = [(lambda cl=cl: self.run_cluster(cl, pdfs, index, doc_sha, ts)) for cl in clusters]
        tasks += [(lambda c=c: self.run_image(c, pdfs, index, doc_sha, ts)) for c in image_claims]
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            list(ex.map(lambda t: t(), tasks))
        if index is not None:
            index.close()
        return self.report()

    def report(self) -> int:
        allr = CL.read_rows(self.paths.results)
        real = [r for r in allr if r.get("kind") == "real"]
        ctl = [r for r in allr if str(r.get("kind", "")).startswith("control")]
        rev = [r for r in allr if r.get("kind") == "reverse-control"]
        tally: dict[str, int] = {}
        for r in real:
            tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
        ctl_bad = [r for r in ctl if r["verdict"] == "SUPPORTED"]
        rev_bad = [r for r in rev if not r.get("agree")]
        self.log(f"CONTROLS: {len(ctl)} run, {len(ctl_bad)} wrongly SUPPORTED -> "
                 f"{'DISCRIMINATES' if not ctl_bad else '!! RUBBER STAMP'}")
        if rev:
            self.log(f"REVERSE CONTROLS: {len(rev)} re-audited in image mode, {len(rev_bad)} disagree")
        self.log("REAL: " + ", ".join(f"{k}={v}" for k, v in sorted(tally.items(), key=lambda kv: -kv[1])))
        problems = [r for r in real if r["verdict"] != "SUPPORTED"]
        for r in problems[:40]:
            self.log(f"  --- Ex{r['exhibit']} p.{r['page_spec']} : {r['verdict']}: {str(r.get('note', ''))[:200]}")
        if ctl_bad:
            self.log("RESULT: INVALID - verifier accepted a mismatched page")
            return 2
        if rev_bad:
            self.log("RESULT: INVALID - a text-SUPPORTED verdict was not SUPPORTED in image mode")
            return 2
        self.log(f"RESULT: {tally.get('SUPPORTED', 0)}/{len(real)} supported, {len(problems)} needing review")
        return 0 if not problems else 1
