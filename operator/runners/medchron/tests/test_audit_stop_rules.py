"""2026-10-24 stop audit, rows 4 and 5: the audit's controls decide by rule.

* Row 4. A control page that is a scan has no text layer, so the "page carries
  the claim" retire rule could never fire on it: the vision transcript is read
  instead. A SUPPORTED control that is not retired is re-tested against the
  next exhibit of a different institution; when the re-test comes back
  unsupported the first control was badly chosen and is retired by rule. The
  round is INVALID only when the controls still SUPPORTED after the re-test
  reach max(3, ceil(1%)) (Curtis's shape: about 76 controls, 2 bad, passes).
* Row 5. A reverse control (a text-SUPPORTED claim re-read on its images) that
  disagrees hands its claim the image verdict, so the repair corrects or drops
  it; the round is INVALID only above the same threshold.
"""

from __future__ import annotations

import json
from pathlib import Path

from medchron.audit import claims as CL, controls, coverage, retired
from medchron.audit.run import AuditPaths, Round
from medchron.stages.base import StageRun
from medchron_testkit import make_pdf
from test_audit import PROSE, SUPPORTED, UNSUPPORTED, Scripted, _claim_of, _exhibit_set, _sr, _write_doc, tool_msg

CLAIM = "Seen 01/02/2026 with blood pressure 120/80 and ibuprofen 400 mg."
DENTAL = "Dental cleaning performed on 05/14/2024. No complaints. Return in six months."


def _eight_claims(sr: StageRun) -> None:
    _write_doc(
        sr,
        "\n\n".join(
            f"01/0{i}/2026\nExample Clinic | Medical Diagnoses\n\n{CLAIM} Visit {i} of eight. (Exhibit 1 - p. 1)"
            for i in range(1, 9)
        ),
    )


def _cite(p: dict) -> str:
    return p["messages"][0]["content"][-1]["text"]


def _add_exhibit(sr: StageRun, n: int, provider: str, page_text: str, transcript: str | None = None) -> None:
    """Exhibit `n` from its own unit file; `transcript` is the vision
    transcript of a page with no text layer (page_text '')."""
    out = sr.slug_dir / "out" / "alpha"
    name = f"Exhibit {n} - {provider} - 01-09-2026 (Medical Records).pdf"
    (out / name).write_bytes(make_pdf([page_text]))
    units = json.loads((sr.slug_dir / "units" / "alpha.json").read_text())
    units.append({"id": f"f{n}", "name": f"record {n}", "ext": ".pdf"})
    (sr.slug_dir / "units" / "alpha.json").write_text(json.dumps(units))
    pm = json.loads((out / "page_map.json").read_text())
    pm.append(
        {
            "exhibit": n,
            "total_pages": 1,
            "files": [{"file": f"record {n}.pdf", "old_exhibit": n, "start_page": 1, "pages": 1}],
        }
    )
    (out / "page_map.json").write_text(json.dumps(pm))
    if transcript is not None:
        (sr.slug_dir / "text" / f"f{n}.txt").write_text(f"[p.1] (machine transcription)\n{transcript}\n")


def test_a_scanned_control_page_whose_vision_transcript_carries_the_claim_is_retired(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """Row 4: the control page is a fax scan (no text layer) whose
    transcript is a diagnosis summary stating the claim's date and values."""
    log: list[str] = []
    sr = _sr(job_dir, firm_config_path, data_root, Scripted(lambda p, n: tool_msg(SUPPORTED)), log=log)
    _exhibit_set(sr, [PROSE, PROSE])
    _add_exhibit(sr, 2, "Riverside Imaging", "", transcript=PROSE)
    _eight_claims(sr)
    paths = AuditPaths(sr.slug_dir, "alpha")
    rc = Round(sr.doorway, "claude-sonnet-5", paths, log.append, mode="image", workers=1).execute()
    assert rc == 0, log
    ctl = [r for r in CL.read_rows(paths.results) if r["kind"].startswith("control")]
    assert len(ctl) == 1 and ctl[0]["retired"] == "rule"
    row = json.loads(retired.path(paths.out).read_text())["retired"][0]
    assert row["key"] == ctl[0]["key"] and "transcript" in row["source"]
    assert coverage.check(paths, log.append)[0]


def test_a_supported_control_cleared_by_a_retest_on_another_institution_is_retired(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """Row 4: the first control page does not carry the claim, yet came back
    SUPPORTED (a generic claim); the re-test against a third institution
    comes back UNSUPPORTED, so the verifier discriminates."""
    log: list[str] = []

    def reply(p, n):
        return tool_msg(UNSUPPORTED if "cited to Exhibit 3 p.1)" in _cite(p) else SUPPORTED)

    sr = _sr(job_dir, firm_config_path, data_root, Scripted(reply), log=log)
    _exhibit_set(sr, [PROSE, PROSE])
    _add_exhibit(sr, 2, "Riverside Imaging", DENTAL)
    _add_exhibit(sr, 3, "Desert Orthopedics", DENTAL)
    _eight_claims(sr)
    paths = AuditPaths(sr.slug_dir, "alpha")
    rc = Round(sr.doorway, "claude-sonnet-5", paths, log.append, mode="image", workers=1).execute()
    assert rc == 0, log
    rows = CL.read_rows(paths.results)
    first = [r for r in rows if r["kind"].startswith("control") and not r.get("retest_of")]
    retest = [r for r in rows if r.get("retest_of")]
    assert len(first) == 1 and first[0]["verdict"] == "SUPPORTED" and "vs Ex2" in first[0]["kind"]
    assert len(retest) == 1 and retest[0]["retest_of"] == first[0]["key"] and "vs Ex3" in retest[0]["kind"]
    assert retest[0]["verdict"] == "UNSUPPORTED"
    assert first[0]["key"] in retired.keys(paths.out)
    assert coverage.check(paths, log.append)[0]
    assert json.loads((paths.out / controls.FILE).read_text())["bad"] == []
    # a resume does not re-test again
    Round(sr.doorway, "claude-sonnet-5", paths, log.append, mode="image", workers=1).execute()
    assert len([r for r in CL.read_rows(paths.results) if r.get("retest_of")]) == 1


def test_a_control_supported_twice_stands_but_one_is_not_a_rubber_stamp_at_the_curtis_rate(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """Row 4, Curtis's shape: about 76 controls on file, 2 SUPPORTED twice
    (a generic claim) is not INVALID; 3 is."""
    sr = _sr(job_dir, firm_config_path, data_root, Scripted(lambda p, n: tool_msg(SUPPORTED)))
    _exhibit_set(sr, [PROSE, PROSE])
    _write_doc(
        sr, "01/02/2026\nExample Clinic | Medical Diagnoses\n\nA claim about the neck long enough. (Exhibit 1 - p. 1)"
    )
    paths = AuditPaths(sr.slug_dir, "alpha")
    body = CL.body_of(paths.doc.read_text())
    live = {c["key"] for c in CL.extract_claims(body, {1})}
    for k in live:
        CL.append_row(paths.results, {"key": k, "kind": "real", "verdict": "SUPPORTED", "doc_sha": CL.doc_sha_of(body)})

    def controls_on_file(bad: int) -> None:
        paths.results.write_text(
            "\n".join(ln for ln in paths.results.read_text().splitlines() if '"real"' in ln) + "\n"
        )
        for i in range(76):
            supported = i < bad
            CL.append_row(
                paths.results,
                {
                    "key": f"c{i}-ctl2",
                    "kind": "control(vs Ex2)",
                    "verdict": "SUPPORTED" if supported else "UNSUPPORTED",
                },
            )
            if supported:
                CL.append_row(
                    paths.results,
                    {
                        "key": f"c{i}-ctl3",
                        "kind": "control-retest(vs Ex3)",
                        "retest_of": f"c{i}-ctl2",
                        "verdict": "SUPPORTED",
                    },
                )

    log: list[str] = []
    controls_on_file(2)
    assert Round(sr.doorway, "m", paths, log.append).report(live) == 0, log
    assert coverage.check(paths, log.append)[0]
    assert any("max(3, ceil(1% x 76)) = 3" in ln for ln in log), log
    controls_on_file(3)
    assert Round(sr.doorway, "m", paths, log.append).report(live) == 2
    ok, summary = coverage.check(paths, log.append)
    assert not ok and summary["controls_bad"] == 3


def _proof5(rows: list[dict]) -> list[str]:
    from medchron.update import proofs as P

    r = P.Result()
    P._audit(P.Inputs([], [], "", "", [], [], rows, [], [], set()), r)
    return [f for f in r.fails if f.startswith("5")]


def test_the_update_proof_5_applies_the_rounds_own_control_rule() -> None:
    """Decision (3), 2026-10-24: proof 5 judged every un-retired SUPPORTED
    control row a failure, re-test rows included, so an update could fail on
    a control the round itself had passed. 76 controls with 2 SUPPORTED twice
    passes; 3 fails; a lone control SUPPORTED twice fails (every control)."""

    def rows(bad: int, n: int = 76) -> list[dict]:
        out: list[dict] = []
        for i in range(n):
            out.append(
                {"key": f"c{i}-ctl2", "kind": "control(vs Ex2)", "verdict": "SUPPORTED" if i < bad else "UNSUPPORTED"}
            )
            if i < bad:
                out.append(
                    {
                        "key": f"c{i}-ctl3",
                        "kind": "control-retest(vs Ex3)",
                        "retest_of": f"c{i}-ctl2",
                        "verdict": "SUPPORTED",
                    }
                )
        return out

    assert _proof5(rows(2)) == []
    assert _proof5(rows(3))
    assert _proof5(rows(1, n=1))


def test_every_control_bad_is_logged_as_the_rubber_stamp() -> None:
    log: list[str] = []
    a = controls.assess_rows([{"key": "c-ctl2", "kind": "control(vs Ex2)", "verdict": "SUPPORTED"}], set())
    controls.describe(a, log.append)
    assert a["invalid"] and a["invalid_by"] == "every control"
    assert any("EVERY CONTROL RUN (1)" in ln and "rubber-stamp" in ln for ln in log), log


def test_threshold_arithmetic() -> None:
    assert controls.threshold(76) == 3 and controls.threshold(612) == 7 and controls.threshold(10) == 3
    assert not controls.is_invalid(2, 76) and controls.is_invalid(3, 76)
    assert controls.is_invalid(1, 1), "every control SUPPORTED twice is the rubber stamp itself"
    assert not controls.is_invalid(1, 1, every=False)
    assert not controls.is_invalid(0, 0)


def test_a_reverse_control_that_disagrees_hands_its_claim_the_image_verdict(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """Row 5: a text-SUPPORTED claim whose image re-read disagrees (a
    handwritten page, a poor scan) takes the image verdict, so the repair
    corrects or drops it; one disagreement in two is not INVALID."""
    first_image: list[str] = []

    def reply(p, n):
        tools = p.get("tools") or []
        if tools and "supporting_pages" in tools[0]["input_schema"]["properties"]:
            return tool_msg({**SUPPORTED, "supporting_pages": [1]})
        claim = _claim_of(p)
        if not first_image:
            first_image.append(claim)
        return tool_msg(UNSUPPORTED if claim == first_image[0] else SUPPORTED)

    log: list[str] = []
    sr = _sr(job_dir, firm_config_path, data_root, Scripted(reply), log=log)
    _exhibit_set(sr, [PROSE, PROSE, PROSE])
    _write_doc(
        sr,
        "\n\n".join(
            f"01/02/2026\nExample Clinic | Medical Diagnoses\n\n"
            f"Blood pressure 120/80 and ibuprofen 400 mg on 01/02/2026, note {i} of sixteen. (Exhibit 1 - p. 1)"
            for i in range(1, 17)
        ),
    )
    paths = AuditPaths(sr.slug_dir, "alpha")
    rc = Round(sr.doorway, "claude-sonnet-5", paths, log.append, mode="text", workers=1).execute()
    assert rc == 1, log  # a problem for the repair, not an INVALID round
    rows = CL.read_rows(paths.results)
    rev = [r for r in rows if r["kind"] == "reverse-control"]
    assert len(rev) == 2 and sum(1 for r in rev if not r["agree"]) == 1
    bad_key = next(r for r in rev if not r["agree"])["key"].removesuffix("-rev")
    body = CL.body_of(paths.doc.read_text())
    latest = CL.latest_real(rows, {c["key"] for c in CL.extract_claims(body, {1})})
    assert latest[bad_key]["verdict"] == "UNSUPPORTED" and latest[bad_key]["reverse_override"] is True
    assert sum(1 for r in latest.values() if r["verdict"] == "SUPPORTED") == 15
