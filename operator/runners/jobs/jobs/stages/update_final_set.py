"""`update_final_set`: the update's final set of added entries, between the
update review and assemble. $0: no model call, nothing sent anywhere.

The rules are ``medchron/update/final_set``'s (stray citation lines, every
heading looked for on its pages, repeats dropped only when the other entry
covers them, all delete-only). This stage reads the reviewed audit document
(``runs/<unit>/final-chronology.md``), writes it back, and writes
``update/final_rows.jsonl``, ``update/corrections.json`` and
``update/final_set.json``; a verdict it carries to a claim's own words under
its heading is an audit row (``mode: carry``). It refuses (exit 1) only if a
rule would have created a claim key, which each rule checks it cannot do.

`final_proofs` is the update proofs' second half (``update/proofs_final``,
proofs 11 to 15), read off the rendered document, and `record_final` merges
its record-only findings and the proven chronology's sha into
``update/proofs.json``; ``update_proofs`` calls both.
"""

from __future__ import annotations

from typing import Any

from ..medchron import graded_proof as GP
from ..medchron.audit import claims as CL
from ..medchron.update import (
    body as B,
    docx_md,
    final_set as FS,
    final_set_io as FIO,
    heading as HD,
    proofs_final as PF,
)
from .base import StageRun, read_json
from .group import Canon
from .render import chronology_name
from .update_base import headings, plan, update_dir

EXIT_REFUSED = 1


def run(sr: StageRun) -> int:
    if not FIO.added_entries(sr.slug_dir, sr.unit.unit):
        FIO.write_nothing_added(sr.slug_dir, sr.unit.unit)
        sr.log("final set: no added entries; nothing to do")
        return 0
    try:
        x = FIO.load_inputs(sr.slug_dir, sr.unit.unit, headings(sr.cfg), Canon(sr.cfg))
        src = x.md
        o = FS.run(x)
    except AssertionError as exc:
        sr.log(f"update_final_set: {exc}")
        return EXIT_REFUSED
    FIO.write(sr.slug_dir, sr.unit.unit, o, src)
    c = o.counts
    sr.log(
        f"final set: {c['added entries']} added entries; stray citation lines {c['stray lines before']} -> "
        f"{c['stray lines after']} ({c['verdicts carried']} verdict(s) carried); repeats dropped "
        f"{sum(v for k, v in c.items() if k.startswith('repeats: safe drops'))}, kept whole "
        f"{sum(v for k, v in c.items() if k.startswith('repeats: kept whole'))}; "
        f"headings on a cited page {c['headings ' + HD.CITED]}, same note {c['headings ' + HD.SAME_NOTE]}, "
        f"no text layer {c['headings ' + HD.NO_TEXT]}, failed {c['headings ' + HD.FAILED]} "
        f"(recorded only); new claim keys {c['new claim keys created']}, keys lost {c['claim keys lost unaccounted']}"
    )
    return 0


def _filed_titles(md: str, new: list[dict[str, Any]]) -> dict[int, str]:
    titles = B.exhibit_titles(md)
    nums = {int(r["exhibit"]) for r in new if not r.get("reused") and not r.get("refiled")}
    return {n: t for n, t in titles.items() if n in nums}


def _supported(rows: list[dict[str, Any]], respelled: dict[str, str]) -> Any:
    """By key, and for a claim whose provider spelling assemble applied, by
    the key of the words the audit read (``proofs._as_audited``)."""
    from ..medchron.update.providers import unspell

    base = GP.latest_supported(rows)

    def get(c: dict[str, Any]) -> tuple[bool, str]:
        ok, mode = base(c)
        if ok or not respelled:
            return ok, mode
        k = CL.claim_key(int(c["exhibit"]), c.get("spec"), unspell(str(c["claim"]), respelled))
        return base({**c, "key": k})

    return get


def final_proofs(sr: StageRun) -> PF.Result:
    """Proofs 11 to 15 of the rendered update; the filed chronology's sha is
    written beside the record-only findings in ``update/proofs.json``."""
    ud, out = update_dir(sr), sr.slug_dir / "out" / sr.unit.unit
    p = plan(sr)
    base_md = (ud / "base.md").read_text(encoding="utf-8")
    upd_md = (ud / "updated-chronology.md").read_text(encoding="utf-8")
    delivered, updated = sr.slug_dir / "delivered" / p["chronology"]["name"], out / chronology_name(sr)
    new = [r for r in read_json(ud / "new_exhibits.final.json", []) if isinstance(r, dict)]
    rep = read_json(ud / "assemble_report.json", {}) or {}
    pre, base_ents = B.entries_of(base_md)
    manifest = read_json(out / "upload_manifest.json", None)
    x = PF.Inputs(
        delivered_rows=docx_md.entry_rows(delivered),
        updated_rows=docx_md.entry_rows(updated),
        corrections=read_json(ud / FS.CORRECTIONS, []) or [],
        delivered_list=[r for t in docx_md.section_tables(delivered, "Exhibit List") for r in t[1:]],
        updated_list=[r for t in docx_md.section_tables(updated, "Exhibit List") for r in t[1:]],
        delivered_max=max(B.delivered_numbers(base_md) or {0}),
        new_numbers=[int(r["exhibit"]) for r in new if not r.get("reused") and isinstance(r.get("exhibit"), int)],
        titles=_filed_titles(upd_md, new),
        final_rows=FIO.read_rows(ud),
        added=B.entries_of((sr.slug_dir / "runs" / sr.unit.unit / "final-chronology.md").read_text(encoding="utf-8"))[
            1
        ],
        manifest_names=[str(m.get("name")) for m in manifest if isinstance(m, dict)]
        if isinstance(manifest, list)
        else None,
        delivered_pdfs={str(e.get("name")) for e in p.get("exhibits") or [] if isinstance(e, dict)},
        graded=GP.check(
            CL.body_of(upd_md),
            _supported(CL.read_rows(out / "audit-results.jsonl"), dict(rep.get("provider_spelling_from_audit") or {})),
            headings=headings(sr.cfg),
            delivered=[*base_ents, *[q.strip() for q in pre.split("\n\n")]],
        ),
        canon=Canon(sr.cfg),
    )
    r = PF.run(x)
    r.filed = {"name": updated.name, "sha256": GP.file_sha(updated)}
    return r


def record_final(sr: StageRun, r: PF.Result) -> None:
    """Merge the record-only findings and the proven file's sha into
    ``update/proofs.json`` (after the update proofs wrote their lines)."""
    GP.write(update_dir(sr) / GP.PROOFS, r.recorded, r.filed)


__all__ = ["EXIT_REFUSED", "final_proofs", "record_final", "run"]
