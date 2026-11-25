"""`python -m jobs.drafting.response_replay`: the discovery-response path, replayed at $0.

Before a firm's Operator drafts discovery responses for a real request, SMD
runs this on a saved matter in the client's ops workspace and compares the
result with an answer key built from the hand drafts (design
``operator-drafts-discovery-responses``, section F)::

    python -m jobs.drafting.response_replay <job_dir_or_matter_dir> --key <key.json> \\
        [--stage find|scope|cost|form-read|exhibits|all] [--no-model] [--body <file>] [--work <dir>] \\
        [--firm <drafting-firm.yaml>] [--pricing <rate card>] [--production <response>]

Stages, each a code stage of the response path (``replay/stages.py`` names the
module and function each one calls):

* ``find``: candidate finding, sha256 dedupe, typing, response-paper pairing,
  request slicing and 1..N numbering (served.json + found-report.json).
  Compared with the key: sets found, missed and extra, request counts, and the
  first 80 characters of each sliced request against ``request_head``.
* ``scope``: category assignment and pack sizes (characters per category and
  the share in "other"; characters per unit's pack and what was left out).
  Uses the find output, or the job's own served.json when find did not run;
  runs on a copy of the data directory's records under ``--work``. ``--firm``
  gives the firm's own category patterns.
* ``cost``: the cost projection, dollars and minutes with each line item.
  Runs scope first; needs ``--firm`` (models, budget) and a rate card.
* ``form-read``: the pixel checkbox read of each form set in the key (found by
  its sha256 in the pull's corpus.json, else raw_manifest.jsonl), against
  ``form.checked``.
* ``exhibits``: each pulled file's exhibit by the firm's table (``exhibit_plan``,
  needs ``--firm`` with ``response_scope.exhibits``) against the key's Index of
  Exhibits (``exhibits``), file by file; with ``--production`` (a production
  response, .md or .docx) each index line's request numbers too
  (``exhibit_index``). Runs scope first. Bar: no extra file; a miss names
  code's reason.
* ``--body <file>``: a produced body's per-number kind (from a JSON body; a
  text or .docx body's kinds are reported unread), the key's names in
  each form response, and its layout (one subpart per line, one provider per
  line).

NO MODEL IS CALLED, and ``--no-model`` (accepted, the only mode) says so: a
model call is answered by a recording beside the key (``replay/recorded.py``);
with none, the stage reports "needs a model call" with the request's sha and
the stages after it stop there. ``find`` runs its code half first and lists
EVERY read it would make without a recording (all their shas in
``needed_calls``), so one pass collects them all. A key with no served set
exits 2 at once, naming the field its entries lacked. Everything the stage writes goes to ``--work``
(default ``<key stem>.replay/`` beside the key), never into the saved matter.
The table is printed and written as JSON to ``<key stem>.replay.json`` beside
the key.

EXIT: 0 when every compared item matches; 1 when any mismatches (listed) or a
stage failed; 2 when nothing mismatched but a stage could not run (a missing
module, a needed model call, or an input the command line did not give).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from .replay import body as body_mod, compare, key as key_mod, real, recorded, stages as S
from .replay.compare import Section

STAGES = ("find", "scope", "cost", "form-read", "exhibits", "all")
INCOMPLETE = ("needs_model", "missing_module", "needs_input")


class NeedsInput(Exception):
    """A stage the replay cannot run without an input the command line did not give."""


class Run:
    """One replay: the matter, the key, the recorded doorway, the sections so far."""

    def __init__(
        self,
        matter: S.Matter,
        key: key_mod.Key,
        work: Path,
        client: recorded.RecordedClient,
        firm: dict[str, Any] | None = None,
        pricing: Any = None,
    ) -> None:
        self.matter, self.key, self.work, self.client = matter, key, work, client
        self.firm, self.pricing = firm, pricing
        self.production: Path | None = None
        self.doorway = recorded.doorway(work, client)
        self.sections: list[Section] = []
        self.scope: Any = None

    def guarded(self, name: str, fn: Callable[[], list[Section]]) -> bool:
        """Run one stage; a missing module, a needed model call or an error is its section."""
        self.client.stage = name
        try:
            self.sections.extend(fn())
            return True
        except recorded.NeedsModelCall as stop:
            self.sections.append(Section(name, status="needs_model", notes=[str(stop)]))
        except S.MissingModule as exc:
            self.sections.append(Section(name, status="missing_module", notes=[str(exc)]))
        except NeedsInput as exc:
            self.sections.append(Section(name, status="needs_input", notes=[str(exc)]))
        except Exception as exc:  # noqa: BLE001 - a stage's own failure is reported as that stage's result
            notes = self._report_notes() if name == "find" else []
            self.sections.append(
                Section(name, status="error", mismatches=[f"{type(exc).__name__}: {exc}"[:300]], notes=notes)
            )
        return False

    def _report_notes(self) -> list[str]:
        report = S.read_json(self.work / "found-report.json")
        if not isinstance(report, dict):
            return []
        return [f"found-report {k}: {report[k]}" for k in ("stop", "reason") if report.get(k)]

    # ---- the stages --------------------------------------------------------
    def find(self) -> list[Section]:
        (self.work / "served.json").unlink(missing_ok=True)  # the finder leaves a served.json alone
        real.find(self.matter, self.work, self.doorway, self.firm)
        served = S.read_json(self.work / "served.json")
        if served is None:
            raise RuntimeError("find wrote no served.json")
        sec = compare.find(self.key, served, S.slices(served, self.matter))
        sec.notes.extend(self._report_notes())
        return [sec]

    def scope_stage(self) -> list[Section]:
        served, base = S.served_source(self.matter, self.work)
        if served is None:
            raise RuntimeError("no served.json (run --stage find, or the job's own served.json)")
        self.scope = real.scope(self.matter, self.work, served, base, self.firm)
        return [compare.categories(self.scope), compare.packs(self.scope)]

    def cost(self) -> list[Section]:
        if self.firm is None or self.pricing is None:
            raise NeedsInput("the cost projection needs --firm (the firm's drafting-firm.yaml) and a rate card")
        served = S.served_doc(self.matter, self.work)
        return [compare.cost(real.cost(self.work, self.scope, served, self.firm, self.pricing))]

    def exhibits(self) -> list[Section]:
        from .exhibit_plan import table

        if not self.key.exhibits:
            return [Section("exhibits vs key", notes=["the key has no exhibits; nothing compared"])]
        if table(self.firm) is None:
            raise NeedsInput("the exhibits stage needs --firm with its response_scope.exhibits table")
        got = real.exhibits(self.work, self.scope, self.firm, self.production)
        return [compare.exhibits(self.key, got["placed"], got["listed"])]

    def form_read(self) -> list[Section]:
        forms = [s for s in self.key.served_sets if s.type == "form_interrogatories"]
        if not forms:
            return [Section("form pixel read", notes=["the key names no form interrogatory set"])]
        out = []
        for s in forms:
            label = f"set {s.set_number}"
            path = S.file_by_sha(self.matter, s.sha256)
            if path is None:
                out.append(Section(f"form pixel read ({label})", status="error", mismatches=["file not in the matter"]))
                continue
            out.append(compare.form_read(label, self.key.checked, real.form_read(path)))
        return out

    def body(self, path: Path) -> list[Section]:
        return [compare.body(self.key, body_mod.read(path, None))]


def replay(run: Run, stage: str, body_path: Path | None) -> None:
    want = set(STAGES[:-1]) if stage == "all" else {stage}
    chain = True
    if "find" in want or (want & {"scope", "cost", "exhibits"} and S.served_doc(run.matter, run.work) is None):
        chain = run.guarded("find", run.find)
    if chain and want & {"scope", "cost", "exhibits"}:
        chain = run.guarded("scope", run.scope_stage)
    if chain and "cost" in want:
        run.guarded("cost", run.cost)
    if chain and "exhibits" in want:
        run.guarded("exhibits", run.exhibits)
    if "form-read" in want:
        run.guarded("form-read", run.form_read)
    if body_path is not None:
        run.guarded("body", lambda: run.body(body_path))


def exit_code(sections: list[Section]) -> int:
    if any(s.mismatches or s.status in ("mismatch", "error") for s in sections):
        return 1
    if any(s.status in INCOMPLETE for s in sections):
        return 2
    return 0


def _cell(v: Any) -> str:
    if isinstance(v, list):
        return ",".join(str(x) for x in v) or "-"
    return "-" if v is None else str(v)


def render(sections: list[Section]) -> str:
    """The plain table: one block per section, columns from its rows."""
    lines: list[str] = []
    for s in sections:
        lines.append(f"== {s.name}: {s.status.upper()} ==")
        if s.rows:
            cols = list(dict.fromkeys(k for r in s.rows for k in r))
            cells = [[_cell(r.get(c)) for c in cols] for r in s.rows]
            widths = [max(len(c), *(len(row[i]) for row in cells)) for i, c in enumerate(cols)]
            lines.append("  " + "  ".join(c.ljust(w) for c, w in zip(cols, widths)))
            lines.extend("  " + "  ".join(v.ljust(w) for v, w in zip(row, widths)) for row in cells)
        lines.extend(f"  MISMATCH: {m}" for m in s.mismatches)
        lines.extend(f"  note: {n}" for n in s.notes)
    return "\n".join(lines)


def _args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="python -m jobs.drafting.response_replay", description=(__doc__ or "").split("\n")[0]
    )
    ap.add_argument("matter", type=Path, help="a saved job dir (with data/) or matter data dir (with manifest.json)")
    ap.add_argument("--key", type=Path, required=True, help="the answer key JSON")
    ap.add_argument("--stage", choices=STAGES, default="all")
    ap.add_argument("--no-model", action="store_true", help="the only mode: model calls come from recordings")
    ap.add_argument("--body", type=Path, help="a produced response body (.json, .docx or text)")
    ap.add_argument("--work", type=Path, help="where the stages write (default: <key stem>.replay/ beside the key)")
    ap.add_argument("--firm", type=Path, help="the firm's drafting-firm.yaml (its scope patterns, models and budget)")
    ap.add_argument("--pricing", type=Path, help="the rate card (default: the lane's own, as the seat finds it)")
    ap.add_argument("--production", type=Path, help="a production response (.md or .docx) for the exhibits stage")
    return ap.parse_args(argv)


def _firm(path: Path | None) -> dict[str, Any] | None:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8")) if path is not None else None


def _pricing(path: Path | None) -> Any:
    import os

    from .. import budget as budget_mod
    from .evidence_cost import load_pricing

    for p in (path, os.environ.get(budget_mod.PRICING_ENV), budget_mod.PRICING_DEFAULT):
        if p and Path(p).is_file():
            return load_pricing(Path(p))
    return None


def main(argv: list[str] | None = None) -> int:
    a = _args(argv)
    matter = S.Matter.at(a.matter)
    try:
        key = key_mod.load(a.key)
    except key_mod.EmptyKey as exc:
        print(f"exit 2: {a.key}: {exc}", file=sys.stderr)
        return 2
    work = a.work or a.key.with_name(f"{a.key.stem}.replay")
    work.mkdir(parents=True, exist_ok=True)
    client = recorded.RecordedClient(recorded.load_recordings(a.key))
    run = Run(matter, key, work, client, _firm(a.firm), _pricing(a.pricing))
    run.production = a.production
    if key.dropped:
        run.sections.append(Section("answer key", notes=[f"entries dropped as malformed: {', '.join(key.dropped)}"]))
    replay(run, a.stage, a.body)
    code = exit_code(run.sections)
    print(render(run.sections))
    print(f"exit {code}: {'every compared item matches' if code == 0 else 'see above'}")
    doc = {
        "matter": key.matter,
        "stage": a.stage,
        "exit": code,
        "model_calls": len(client.calls),
        "needed_calls": client.needed,
        "sections": [s.as_json() for s in run.sections],
    }
    a.key.with_name(f"{a.key.stem}.replay.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(main())
