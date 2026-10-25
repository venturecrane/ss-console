"""An update reuses the delivered run's finding on a file (``stages/prior_reading``)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from medchron.stages import prior_reading, update_select
from medchron.update import outcome

BASE = "01M5BASE00000000000000000A"


def _jsonl(p: Path, rows: list[dict]) -> None:
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _scene(tmp_path: Path, *, base_sha: str = "aaa") -> SimpleNamespace:
    run = tmp_path / "run"
    base = run / "jobs" / BASE / "data" / "firm"
    (base / "units").mkdir(parents=True)
    (base / "runs" / "alpha").mkdir(parents=True)
    _jsonl(
        base / "raw_manifest.jsonl",
        [
            {"id": "p1", "ok": True, "sha256": base_sha},
            {"id": "b1", "ok": True, "sha256": "bbb"},
            {"id": "e1", "ok": True, "sha256": "eee"},
            {"id": "q1", "ok": True, "sha256": "qqq"},
        ],
    )
    (base / "units" / "alpha.json").write_text(
        json.dumps(
            [
                {"id": "p1", "name": "scene", "ext": ".jpg"},
                {"id": "b1", "name": "invoice", "ext": ".pdf", "compose": False, "compose_skip": "billing-only source"},
                {"id": "e1", "name": "clinic", "ext": ".pdf"},
                {"id": "q1", "name": "quiet", "ext": ".pdf"},
            ]
        )
    )
    (base / "runs" / "alpha" / "map-01.md").write_text(
        "=== FILE: scene.jpg (fileId p1) === | nothing extractable: scene photograph, no record text\n"
        "=== FILE: clinic.pdf (fileId e1) === | entries: 3\n"
        "=== FILE: quiet.pdf (fileId q1) === | entries: 0\n"
    )
    sd = tmp_path / "upd" / "firm"
    sd.mkdir(parents=True)
    rows = []
    for rid, sha, ext in (("p1", "aaa", ".jpg"), ("b1", "bbb", ".pdf"), ("e1", "eee", ".pdf"), ("q1", "qqq", ".pdf")):
        src = tmp_path / f"{rid}{ext}"
        src.write_bytes(b"x")
        rows.append({"id": rid, "ok": True, "sha256": sha, "ext": ext, "path": str(src), "name": rid})
    _jsonl(sd / "raw_manifest.jsonl", rows)
    job = SimpleNamespace(install_root=str(run), prior_delivered=[BASE])
    return SimpleNamespace(job=job, slug_dir=sd, unit=SimpleNamespace(unit="alpha"))


def test_the_delivered_runs_finding_decides_the_same_file(tmp_path):
    sr = _scene(tmp_path)
    got = prior_reading.decided(sr, {"p1", "b1", "e1", "q1"})  # type: ignore[arg-type]
    assert got == {"p1": "composer: scene photograph, no record text", "b1": "compose skipped: billing-only source"}


def test_no_finding_is_reused_for_other_bytes_a_source_or_a_silent_pass(tmp_path):
    """Falsifiers: the same id with other bytes is read again; a file that
    gave entries, or one passed over with no reason, has no finding."""
    sr = _scene(tmp_path, base_sha="changed")
    got = prior_reading.decided(sr, {"p1", "b1", "e1", "q1"})  # type: ignore[arg-type]
    assert (
        "p1" not in got
        and "e1" not in got
        and "q1" not in got
        and got == {"b1": "compose skipped: billing-only source"}
    )


def test_a_file_the_run_set_aside_with_a_reason_is_decided_and_a_locked_one_is_not(tmp_path):
    from medchron import unopened

    sr = _scene(tmp_path)
    base = Path(sr.job.install_root) / "jobs" / BASE / "data" / "firm"
    _jsonl(
        base / "raw_manifest.jsonl",
        [
            {"id": "o1", "ok": True, "sha256": "o1", "name": "engagement letter", "ext": ".pdf"},
            {"id": "o2", "ok": True, "sha256": "o2", "name": "locked", "ext": ".pdf"},
            {"id": "o3", "ok": True, "sha256": "o3x", "name": "request", "ext": ".pdf"},
        ],
    )
    (base / "orphans.json").write_text(
        json.dumps(
            {
                "orphans": [
                    {
                        "name": "engagement letter.pdf",
                        "reason": "identity or engagement document, not a chronology source",
                    },
                    {"name": "locked.pdf", "reason": unopened.REASON},
                    {"name": "request.pdf", "reason": "records request"},
                ]
            }
        )
    )
    _jsonl(sr.slug_dir / "raw_manifest.jsonl", [{"id": i, "ok": True, "sha256": i} for i in ("o1", "o2", "o3")])
    got = prior_reading.decided(sr, {"o1", "o2", "o3"})  # type: ignore[arg-type]
    # o2 is locked (read again once it opens); o3's bytes changed (read again).
    assert got == {"o1": "set aside: identity or engagement document, not a chronology source"}


def test_update_select_sets_a_decided_file_aside_and_the_outcome_names_its_finding(tmp_path):
    sr = _scene(tmp_path)
    out, skipped = update_select._candidates(sr, {})  # type: ignore[arg-type]
    key = prior_reading.PREFIX + "composer: scene photograph, no record text"
    assert skipped[key] == ["p1"] and {r["id"] for r in out} == {"e1", "q1"}
    assert outcome._skipped("p1", {"skipped": skipped}) == (outcome.PRIOR, "composer: scene photograph, no record text")
