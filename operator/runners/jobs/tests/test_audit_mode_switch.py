"""A firm that moves the per-sentence audit from page text to the picture.

A live update, 2026-11-21 (values invented here): a text-mode audit stopped
INVALID when its reverse controls disagreed past the threshold. The firm moved
to picture mode, and a resume then re-audited nothing (every text verdict
counted as done) and stopped INVALID again on the same reverse rows. Under the
main audit's picture mode a text verdict is read again, and once the round has
read the claims again it records every reverse control whose text verdict no
longer stands as retired. Text mode, and the update review's own picture check
of new sentences, are unchanged.
"""

from __future__ import annotations

from jobs.medchron.audit import claims as CL
from jobs.medchron.audit import controls

TS = "2026-11-21T00:00:00Z"


def _real(key: str, mode: str, verdict: str = "SUPPORTED", **extra) -> dict:
    return {
        "key": key,
        "kind": "real",
        "exhibit": 3,
        "claim": f"claim {key}",
        "verdict": verdict,
        "mode": mode,
        **extra,
    }


def _rev(key: str, agree: bool) -> dict:
    return {
        "key": f"{key}-rev",
        "kind": "reverse-control",
        "exhibit": 3,
        "claim": f"claim {key}",
        "verdict": "SUPPORTED" if agree else "PARTIAL",
        "mode": "image",
        "text_verdict": "SUPPORTED",
        "agree": agree,
    }


def _text_pass(n: int, bad: int) -> list[dict]:
    """n text verdicts, every one reverse-checked, ``bad`` disagreeing (with the
    override image row the audit writes for each disagreement)."""
    rows: list[dict] = []
    for i in range(n):
        k = f"k{i}"
        rows.append(_real(k, "text"))
        agree = i >= bad
        rows.append(_rev(k, agree))
        if not agree:
            rows.append(_real(k, "image", "PARTIAL", reverse_override=True))
    return rows


def _picture_round(rows: list[dict], live: set[str]) -> list[dict]:
    """What an image-mode main-audit round does: read on the picture exactly
    the claims ``done_keys`` leaves (all SUPPORTED here), then record the
    retirements (``Round.finish``)."""
    todo = live - CL.done_keys(rows, "image")
    rows = rows + [_real(k, "image") for k in sorted(todo)]
    return rows + controls.retirements(rows, live, TS)


def test_a_text_pass_past_the_threshold_is_invalid():
    assert controls.assess_rows(_text_pass(40, 12), set())["reverse_invalid"]


def test_under_picture_mode_text_verdicts_are_read_again_and_overrides_are_not():
    rows = _text_pass(40, 12)
    keys = {f"k{i}" for i in range(40)}
    assert keys <= CL.done_keys(rows)  # text mode and the update review: unchanged
    assert keys - CL.done_keys(rows, "image") == {f"k{i}" for i in range(12, 40)}


def test_a_picture_round_after_a_text_pass_is_not_invalid():
    live = {f"k{i}" for i in range(40)}
    a = controls.assess_rows(_picture_round(_text_pass(40, 12), live), set())
    assert a["reverse"] == 0
    assert not a["invalid"]


def test_the_overridden_claims_keep_their_picture_verdict_for_repair():
    live = {f"k{i}" for i in range(40)}
    rows = _picture_round(_text_pass(40, 12), live)
    latest = CL.latest_real(rows, live)
    assert {k for k, r in latest.items() if r["verdict"] != "SUPPORTED"} == {f"k{i}" for i in range(12)}


def test_a_repaired_claim_retires_its_old_reverse_control():
    rows = _text_pass(40, 12)
    live = {f"k{i}" for i in range(12, 40)} | {f"k{i}-repaired" for i in range(12)}
    rows = _picture_round(rows, live)
    assert controls.assess_rows(rows, set())["reverse"] == 0


def test_text_mode_never_retires_a_reverse_control():
    rows = _text_pass(40, 12)
    rows += [_real(f"k{i}", "image") for i in range(40)]  # picture rows, but no picture-mode round
    a = controls.assess_rows(rows, set())
    assert a["reverse"] == 40
    assert a["reverse_invalid"]


def test_a_claim_still_on_a_text_verdict_keeps_its_reverse_control():
    rows = _text_pass(40, 12)
    live = {f"k{i}" for i in range(40)}
    rows += controls.retirements(rows, live, TS)  # no picture reads yet
    a = controls.assess_rows(rows, set())
    assert a["reverse"] == 28
    assert not a["reverse_bad"]


def test_retirement_is_written_once():
    live = {f"k{i}" for i in range(40)}
    rows = _picture_round(_text_pass(40, 12), live)
    assert controls.retirements(rows, live, TS) == []


def test_a_text_round_verdict_that_came_from_the_picture_is_not_read_again():
    rows = [_real("x", "text", "PARTIAL", text_then_image=True)]
    assert "x" in CL.done_keys(rows, "image")


def test_a_reverse_control_written_after_its_retirement_still_counts():
    live = {f"k{i}" for i in range(40)}
    rows = _picture_round(_text_pass(40, 12), live)
    rows += [{"key": "k0", "kind": CL.REAUDIT}, _real("k0", "text"), _rev("k0", False)]  # back on text
    a = controls.assess_rows(rows, set())
    assert a["reverse"] == 1
    assert len(a["reverse_bad"]) == 1
