"""The run artifact: an id that binds to an outcome, and a report a reader can check.

WHY THE ID IS A DIGEST AND NOT A TIMESTAMP. The release gate asks an
``OVERLAY_REF`` bump PR to cite "the id of a green shadow-firm run on the
candidate ref". If the id were a timestamp, citing a green id for a red run
would be a typo away. The id hashes the seat, the candidate overlay ref, the
scenario ids and their outcomes, so an id that ends in ``-green`` cannot have
been produced by a run with a FAIL or a SKIP in it, and the digest can be
recomputed from the report body by anyone who doubts it.

The report also prints the ref the rig was OBSERVED running. The runner
refuses to drive unless that equals the candidate, so the two lines always
agree; printing both is what lets a reader tell an id produced against the
candidate from one produced by a rig still sitting on the previous release.

The report always prints SKIPPED as its own outcome. A suite that folded
SKIPPED into PASS would certify a release on scenarios that never ran, which is
the exact shape of the built-not-wired failure this program exists to close.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .scoring import FAIL, PASS, SKIPPED, ScenarioResult

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[2] / ".stitch" / "shadow-firm"


@dataclass
class Run:
    seat: str
    overlay_ref: str
    started_at: str
    #: The ref the rig was OBSERVED running when the gate in run.py read its
    #: seam. Not part of the digest below: the gate refuses to drive unless it
    #: equals ``overlay_ref``, so it adds no entropy. It is carried and rendered
    #: so a reader of the report can see that the equality was observed rather
    #: than assumed, which is the whole difference between this id and the ones
    #: three earlier bumps could not produce at all.
    running_ref: str | None = None
    results: list[ScenarioResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        tally = {PASS: 0, FAIL: 0, SKIPPED: 0}
        for result in self.results:
            tally[result.outcome] = tally.get(result.outcome, 0) + 1
        return tally

    @property
    def is_green(self) -> bool:
        """Green means every scenario passed. Not "nothing failed"."""
        return bool(self.results) and all(r.outcome == PASS for r in self.results)

    @property
    def run_id(self) -> str:
        payload = json.dumps(
            {
                "seat": self.seat,
                "overlay_ref": self.overlay_ref,
                "started_at": self.started_at,
                "outcomes": {r.scenario_id: r.outcome for r in sorted(self.results, key=lambda x: x.scenario_id)},
            },
            sort_keys=True,
        )
        digest = hashlib.sha256(payload.encode()).hexdigest()[:12]
        stamp = self.started_at.replace("-", "").replace(":", "").split(".")[0]
        verdict = "green" if self.is_green else "notgreen"
        return f"shadow-{self.seat}-{stamp}-{self.overlay_ref[:7]}-{digest}-{verdict}"


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def to_json(run: Run) -> dict:
    return {
        "run_id": run.run_id,
        "seat": run.seat,
        "overlay_ref": run.overlay_ref,
        "running_ref": run.running_ref,
        "started_at": run.started_at,
        "green": run.is_green,
        "counts": run.counts,
        "notes": run.notes,
        "scenarios": [
            {
                "id": result.scenario_id,
                "outcome": result.outcome,
                "reason": result.reason,
                "legs": [
                    {
                        "id": leg.leg_id,
                        "outcome": leg.outcome,
                        "reason": leg.reason,
                        "expectations": [
                            {"kind": e.kind, "verdict": e.verdict, "detail": e.detail} for e in leg.results
                        ],
                    }
                    for leg in result.legs
                ],
            }
            for result in run.results
        ],
    }


def to_markdown(run: Run, scenarios_by_id: dict[str, dict]) -> str:
    counts = run.counts
    lines = [
        f"# Shadow firm run {run.run_id}",
        "",
        f"- Seat: `{run.seat}`",
        f"- Candidate overlay ref: `{run.overlay_ref}`",
        f"- Rig running overlay ref (observed): `{run.running_ref or 'not observed'}`",
        f"- Started: {run.started_at}",
        f"- Result: **{'GREEN' if run.is_green else 'NOT GREEN'}** "
        f"({counts.get(PASS, 0)} pass, {counts.get(FAIL, 0)} fail, {counts.get(SKIPPED, 0)} skipped)",
        "",
        "A run is green only when every scenario passed. A SKIPPED scenario did not run,",
        "so it certifies nothing and a release gate must not accept this id.",
        "",
        "| Scenario | Replays | Outcome | Why |",
        "| --- | --- | --- | --- |",
    ]
    for result in run.results:
        scenario = scenarios_by_id.get(result.scenario_id, {})
        replays = ", ".join(str(r) for r in scenario.get("replays") or [])
        reason = " ".join(str(result.reason).split())[:220]
        lines.append(f"| `{result.scenario_id}` | {replays} | {result.outcome} | {reason} |")
    lines.append("")
    for result in run.results:
        scenario = scenarios_by_id.get(result.scenario_id, {})
        lines.append(f"## {result.scenario_id} ({result.outcome})")
        lines.append("")
        lines.append(f"Hostile act: {' '.join(str(scenario.get('hostile_act', '')).split())}")
        lines.append("")
        lines.append(f"Falsifier: {' '.join(str(scenario.get('falsifier', '')).split())}")
        lines.append("")
        for leg in result.legs:
            lines.append(f"- **{leg.leg_id}** = {leg.outcome}: {' '.join(str(leg.reason).split())}")
            for expectation in leg.results:
                lines.append(
                    f"  - `{expectation.kind}` {expectation.verdict}: {' '.join(str(expectation.detail).split())}"
                )
        lines.append("")
    if run.notes:
        lines.append("## Run notes")
        lines.append("")
        lines.extend(f"- {note}" for note in run.notes)
        lines.append("")
    return "\n".join(lines)


def write(run: Run, scenarios_by_id: dict[str, dict], output_dir: Path | None = None) -> tuple[Path, Path]:
    directory = output_dir or DEFAULT_OUTPUT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{run.run_id}.json"
    markdown_path = directory / f"{run.run_id}.md"
    json_path.write_text(json.dumps(to_json(run), indent=2) + "\n")
    markdown_path.write_text(to_markdown(run, scenarios_by_id))
    return json_path, markdown_path
