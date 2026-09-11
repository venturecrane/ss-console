"""Tests for the body-verify phase (operator/bin/lib/send_verify.py).

Two failure modes are pinned with equal weight:

* an instrument that grades wrong (a diverged body passing, a skeleton fallback
  read as divergence, one wake laundering a run of dispatches);
* an instrument that LEAKS. Both repos are public, so a client email body must
  never appear in any output this phase produces -- report text, --json, the
  finding fingerprint, anything. The leak tests are structural (the dataclass
  cannot hold a body) AND behavioral (a sentinel body fed through the full
  render/json paths appears nowhere), and the walker that proves the behavioral
  half is itself falsified (a deliberately regressed verdict is caught), because
  a check that cannot fail measures nothing.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

_BIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BIN / "lib"))

import send_verify as sv  # noqa: E402 -- path injected above; the import must follow the sys.path shim

# The reconciler, spec-loaded the way test_reconcile_sends.py does, to drive the
# integrated render()/--json paths the leak tests must cover.
_spec = importlib.util.spec_from_file_location("reconcile_sends", _BIN / "reconcile-sends.py")
rec = importlib.util.module_from_spec(_spec)
sys.modules["reconcile_sends"] = rec
_spec.loader.exec_module(rec)

CONTRACTS = _BIN.parent / "contracts"

SENTINEL = "CLIENT-BODY-SENTINEL-9f3a"

UTC = timezone.utc


def _ts(minute: int, second: int = 0) -> str:
    return datetime(2026, 8, 30, 9, minute, second, tzinfo=UTC).isoformat()


def _wake_row(skill="deadline-miss-escalator", minute=0, hashes=None, items=None):
    meta = {}
    if hashes is not None:
        meta["body_sha256"] = hashes
    if items is not None:
        meta["items"] = items
    return {
        "id": f"wake-{skill}-{minute}",
        "ts": _ts(minute),
        "action_type": "EMITTED_WAKE",
        "skill_name": skill,
        "metadata": json.dumps(meta),
    }


def _dispatch_row(
    skill="deadline-miss-escalator",
    minute=1,
    sha="",
    variant="full",
    outcome="sent",
    extra=None,
    plain="",
    message_id="",
):
    """One CONFIRM_SEND_DISPATCHED row as the broker writes it.

    ``skill=None`` is the row a seat writes while its pinned overlay predates
    the ``skill_name`` stamp (B3): the column is NULL. The fixture PAIR --
    the same diverged scene with the column set and with it NULL -- is what
    states what the column buys (see the primary-check tests).

    ``message_id`` puts the vendor id in metadata the way the broker records
    it, which is the identity join the secondary check reads (B7). A row
    without one can only ever be claimed by window, and is graded hold-only.
    """
    meta = {"outcome": outcome}
    if sha:
        meta["rendered_body_sha256"] = sha
        meta["body_variant"] = variant
    if plain:
        # The overlay's second stamp. Omitted entirely when unset, which is
        # exactly how a seat on an older OVERLAY_REF writes the row.
        meta["plain_body_sha256"] = plain
    if message_id:
        meta["message_id"] = message_id
    if extra:
        meta.update(extra)
    return {
        "id": f"disp-{skill}-{minute}",
        "ts": _ts(minute),
        "action_type": "CONFIRM_SEND_DISPATCHED",
        "skill_name": skill,
        "metadata": json.dumps(meta),
    }


def _declares(mode="templated"):
    template = "operator/skills/deadline-miss-escalator/references/output-format.md"
    return {
        "deadline-miss-escalator": sv.RenderDecl(
            skill="deadline-miss-escalator",
            render=mode,
            template=template if mode != "compositional" else None,
        ),
        "client-verification-tracker": sv.RenderDecl(
            skill="client-verification-tracker",
            render="slot-templated",
            template="operator/skills/client-verification-tracker/render.py",
        ),
        "medical-records-chaser": sv.RenderDecl(skill="medical-records-chaser", render="compositional"),
    }


# ---------------------------------------------------------------------------
# the canon function against the shared arbiter fixture
# ---------------------------------------------------------------------------


def test_canonical_hash_matches_every_shared_vector():
    """The fixture is the arbiter BOTH repos load. An implementation drift here
    fails this suite the same way it fails the overlay's."""
    fixture = json.loads((CONTRACTS / "fixtures" / "body-canon-vectors.json").read_text())
    vectors = fixture["vectors"]
    assert len(vectors) >= 8
    for vector in vectors:
        assert sv.canonical_body_sha256(vector["input"]) == vector["sha256"], vector["name"]


def test_the_required_trailing_newline_vector_exists_and_is_an_equivalence():
    fixture = json.loads((CONTRACTS / "fixtures" / "body-canon-vectors.json").read_text())
    by_name = {v["name"]: v for v in fixture["vectors"]}
    assert "trailing_newline" in by_name
    assert by_name["trailing_newline"]["sha256"] == by_name["plain_two_lines"]["sha256"]
    assert by_name["trailing_newline"]["input"] != by_name["plain_two_lines"]["input"]


def test_only_space_and_tab_strip_exotic_whitespace_is_content():
    """The ss#2664 render-pair drift: a bare rstrip() eats EVERY Unicode
    whitespace, so a mail-client-converted trailing nbsp would hash differently
    here than at the render-side stamp sites (which strip " \\t" only) and
    false-HOLD the channel check. The nbsp_tail_survives vector is the shared
    pin; this test states the semantics directly."""
    fixture = json.loads((CONTRACTS / "fixtures" / "body-canon-vectors.json").read_text())
    by_name = {v["name"]: v for v in fixture["vectors"]}
    assert "nbsp_tail_survives" in by_name
    assert sv.canonical_body_sha256("Hello\xa0\n") == by_name["nbsp_tail_survives"]["sha256"]
    # The exotic tails are CONTENT: none of them may collapse to plain "Hello".
    plain = sv.canonical_body_sha256("Hello")
    for tail in ("\xa0", "\x0c", "\x0b"):
        assert sv.canonical_body_sha256(f"Hello{tail}\n") != plain, repr(tail)
    # While space and tab still strip exactly as specified.
    assert sv.canonical_body_sha256("Hello \t\n") == plain


# ---------------------------------------------------------------------------
# the committed contract file parses and is in the wave-1 authored state
# ---------------------------------------------------------------------------


def test_the_committed_send_render_contract_loads():
    declares = sv.load_send_render()
    # The cron x derived-outbound join on the authored seats (the arming gate
    # asserts the join itself; here we pin that the committed file names the
    # known-armed set so the verifier and the gate read the same skills).
    for skill in (
        "deadline-miss-escalator",
        "discovery-response-tracker",
        "client-verification-tracker",
        "medical-records-chaser",
        "lien-ledger-tracker",
        "minors-compromise-packet",
        "status-report-assembler",
    ):
        assert skill in declares, f"{skill} missing from send-render.yaml"


def test_a_templated_declaration_without_a_template_refuses(tmp_path):
    bad = tmp_path / "send-render.yaml"
    bad.write_text("schema_version: 1\nskills:\n  x:\n    render: templated\n")
    with pytest.raises(sv.SendRenderError):
        sv.load_send_render(str(bad))


def test_an_unknown_render_mode_refuses(tmp_path):
    bad = tmp_path / "send-render.yaml"
    bad.write_text("schema_version: 1\nskills:\n  x:\n    render: freestyle\n")
    with pytest.raises(sv.SendRenderError):
        sv.load_send_render(str(bad))


# ---------------------------------------------------------------------------
# primary check: the wake <-> dispatch hash join
# ---------------------------------------------------------------------------


def test_matching_hashes_grade_match():
    sha = sv.canonical_body_sha256("Deadline alert body\n")
    wakes = sv.index_wakes([_wake_row(hashes=[{"body_sha256_full": sha}])])
    dispatches = sv.index_dispatches([_dispatch_row(sha=sha)])
    verdicts = sv.verify_hash_join(wakes, dispatches, _declares())
    assert [v.verdict for v in verdicts] == [sv.VERDICT_MATCH]


def test_a_diverged_dispatch_hash_is_the_finding():
    wake_sha = sv.canonical_body_sha256("authored body")
    sent_sha = sv.canonical_body_sha256("recomposed body")
    wakes = sv.index_wakes([_wake_row(hashes=[{"body_sha256_full": wake_sha}])])
    dispatches = sv.index_dispatches([_dispatch_row(sha=sent_sha)])
    verdicts = sv.verify_hash_join(wakes, dispatches, _declares())
    assert [v.verdict for v in verdicts] == [sv.VERDICT_DIVERGED]
    assert verdicts[0].is_finding


def test_a_skeleton_match_grades_degraded_never_diverged():
    full = sv.canonical_body_sha256("full body")
    skeleton = sv.canonical_body_sha256("skeleton body")
    wakes = sv.index_wakes([_wake_row(hashes=[{"body_sha256_full": full, "body_sha256_skeleton": skeleton}])])
    dispatches = sv.index_dispatches([_dispatch_row(sha=skeleton, variant="skeleton")])
    verdicts = sv.verify_hash_join(wakes, dispatches, _declares())
    assert [v.verdict for v in verdicts] == [sv.VERDICT_DEGRADED]
    assert not verdicts[0].is_finding and not verdicts[0].is_hold


def test_a_templated_dispatch_with_no_wake_hash_holds_not_finds():
    """The designed interim state: render cluster not yet deployed to the seat."""
    sha = sv.canonical_body_sha256("body")
    dispatches = sv.index_dispatches([_dispatch_row(sha=sha)])
    verdicts = sv.verify_hash_join([], dispatches, _declares())
    assert [v.verdict for v in verdicts] == [sv.VERDICT_NO_WAKE_HASH]
    assert verdicts[0].is_hold and not verdicts[0].is_finding


def test_a_templated_dispatch_with_no_stamp_holds():
    sha = sv.canonical_body_sha256("body")
    wakes = sv.index_wakes([_wake_row(hashes=[{"body_sha256_full": sha}])])
    dispatches = sv.index_dispatches([_dispatch_row(sha="")])
    verdicts = sv.verify_hash_join(wakes, dispatches, _declares())
    assert [v.verdict for v in verdicts] == [sv.VERDICT_NO_DISPATCH_STAMP]
    assert verdicts[0].is_hold


def test_compositional_skills_are_never_hash_graded():
    sha = sv.canonical_body_sha256("body")
    wakes = sv.index_wakes([_wake_row(skill="medical-records-chaser", hashes=[{"body_sha256_full": sha}])])
    dispatches = sv.index_dispatches(
        [_dispatch_row(skill="medical-records-chaser", sha=sv.canonical_body_sha256("other"))]
    )
    assert sv.verify_hash_join(wakes, dispatches, _declares()) == []


def test_one_wake_cannot_launder_more_dispatches_than_it_stamped():
    """Consumption discipline: a wake with ONE stamped body accounts for ONE
    dispatch; the second dispatch in the window must answer for itself."""
    sha = sv.canonical_body_sha256("body")
    wakes = sv.index_wakes([_wake_row(hashes=[{"body_sha256_full": sha}])])
    dispatches = sv.index_dispatches([_dispatch_row(minute=1, sha=sha), _dispatch_row(minute=2, sha=sha)])
    verdicts = sv.verify_hash_join(wakes, dispatches, _declares())
    assert sorted(v.verdict for v in verdicts) == [
        sv.VERDICT_MATCH,
        sv.VERDICT_NO_WAKE_HASH,
    ]


def test_a_dispatch_outside_the_window_does_not_join():
    sha = sv.canonical_body_sha256("body")
    wakes = sv.index_wakes([_wake_row(minute=0, hashes=[{"body_sha256_full": sha}])])
    late = {
        "id": "disp-late",
        "ts": datetime(2026, 8, 30, 11, 1, tzinfo=UTC).isoformat(),  # > 3600s later
        "action_type": "CONFIRM_SEND_DISPATCHED",
        "skill_name": "deadline-miss-escalator",
        "metadata": json.dumps({"outcome": "sent", "rendered_body_sha256": sha}),
    }
    verdicts = sv.verify_hash_join(wakes, sv.index_dispatches([late]), _declares())
    assert [v.verdict for v in verdicts] == [sv.VERDICT_NO_WAKE_HASH]


def test_a_refused_dispatch_row_is_not_graded():
    sha = sv.canonical_body_sha256("body")
    rows = [_dispatch_row(sha=sha, outcome="refused")]
    assert sv.index_dispatches(rows) == []


# ---------------------------------------------------------------------------
# primary check: attribution (claims review 2026-09-04, B3)
#
# The broker never wrote skill_name on its rows, so `declares.get("")` was None
# and the primary check graded NOTHING on every live seat, silently. The column
# is written now; these pin what it buys, and what the hash fallback covers on
# a seat whose pinned overlay predates it.
# ---------------------------------------------------------------------------


def test_the_pair_a_diverged_dispatch_is_a_finding_with_the_column_and_silent_without():
    """THE PIN. Same diverged scene twice: with the column set the verifier
    files BODY_DIVERGED; with the column NULL (a seat whose overlay predates
    the stamp) no wake's hashes recognise the dispatch and it gets no verdict
    at all -- today's silent skip, kept on purpose so establishment ops notes
    (unlabelled rows with hashes) do not redden every run. The difference
    between the two halves IS what the skill_name column is for."""
    wake_sha = sv.canonical_body_sha256("authored body")
    sent_sha = sv.canonical_body_sha256("recomposed body")
    wakes = sv.index_wakes([_wake_row(hashes=[{"body_sha256_full": wake_sha}])])
    labelled = sv.index_dispatches([_dispatch_row(sha=sent_sha)])
    assert [v.verdict for v in sv.verify_hash_join(wakes, labelled, _declares())] == [sv.VERDICT_DIVERGED]
    wakes = sv.index_wakes([_wake_row(hashes=[{"body_sha256_full": wake_sha}])])
    unlabelled = sv.index_dispatches([_dispatch_row(sha=sent_sha, skill=None)])
    assert unlabelled[0].skill_name == ""
    assert sv.verify_hash_join(wakes, unlabelled, _declares()) == []


def test_an_unlabelled_dispatch_is_attributed_by_hash():
    """Pre-pin seat: the column is NULL but the overlay already stamps
    rendered_body_sha256, and a sha256 over a rendered body with per-matter
    identifiers does not collide by accident. The pair grades MATCH, the verdict
    names the WAKE's skill, and the attribution is counted as hash."""
    sha = sv.canonical_body_sha256("Deadline alert body\n")
    wakes = sv.index_wakes([_wake_row(hashes=[{"body_sha256_full": sha}])])
    dispatches = sv.index_dispatches([_dispatch_row(sha=sha, skill=None)])
    verdicts = sv.verify_hash_join(wakes, dispatches, _declares())
    assert [v.verdict for v in verdicts] == [sv.VERDICT_MATCH]
    assert verdicts[0].skill_name == "deadline-miss-escalator"
    assert verdicts[0].attribution == "hash"
    assert verdicts[0].detail == "attributed by hash"
    assert sv.attribution_counts(verdicts) == {
        "attributed_by_skill": 0,
        "attributed_by_hash": 1,
    }


def test_a_labelled_dispatch_is_attributed_by_skill_and_counted_so():
    sha = sv.canonical_body_sha256("Deadline alert body\n")
    wakes = sv.index_wakes([_wake_row(hashes=[{"body_sha256_full": sha}])])
    dispatches = sv.index_dispatches([_dispatch_row(sha=sha)])
    verdicts = sv.verify_hash_join(wakes, dispatches, _declares())
    assert [v.verdict for v in verdicts] == [sv.VERDICT_MATCH]
    assert verdicts[0].attribution == "skill"
    assert verdicts[0].detail is None  # the column is the expected path; nothing to say
    assert sv.attribution_counts(verdicts) == {
        "attributed_by_skill": 1,
        "attributed_by_hash": 0,
    }


def test_an_unlabelled_skeleton_dispatch_attributes_by_hash_and_grades_degraded():
    full = sv.canonical_body_sha256("full body")
    skeleton = sv.canonical_body_sha256("skeleton body")
    wakes = sv.index_wakes([_wake_row(hashes=[{"body_sha256_full": full, "body_sha256_skeleton": skeleton}])])
    dispatches = sv.index_dispatches([_dispatch_row(sha=skeleton, variant="skeleton", skill=None)])
    verdicts = sv.verify_hash_join(wakes, dispatches, _declares())
    assert [v.verdict for v in verdicts] == [sv.VERDICT_DEGRADED]
    assert verdicts[0].attribution == "hash"


def test_hash_attribution_consumes_capacity_like_the_skill_claim():
    """One wake, ONE stamped body, two unlabelled dispatches carrying it: the
    second is not laundered into a second MATCH. (It gets no verdict rather
    than a hold -- an unlabelled row nothing claims is the silent-skip shape.)"""
    sha = sv.canonical_body_sha256("body")
    wakes = sv.index_wakes([_wake_row(hashes=[{"body_sha256_full": sha}])])
    dispatches = sv.index_dispatches(
        [
            _dispatch_row(minute=1, sha=sha, skill=None),
            _dispatch_row(minute=2, sha=sha, skill=None),
        ]
    )
    verdicts = sv.verify_hash_join(wakes, dispatches, _declares())
    assert [v.verdict for v in verdicts] == [sv.VERDICT_MATCH]


def test_hash_attribution_respects_the_window_and_the_declaration():
    sha = sv.canonical_body_sha256("body")
    # Outside the window: the hash matches but the wake is an hour too old.
    wakes = sv.index_wakes([_wake_row(minute=0, hashes=[{"body_sha256_full": sha}])])
    late = _dispatch_row(sha=sha, skill=None)
    late["ts"] = datetime(2026, 8, 30, 11, 1, tzinfo=UTC).isoformat()
    assert sv.verify_hash_join(wakes, sv.index_dispatches([late]), _declares()) == []
    # A compositional wake's hashes attribute nothing: only hash-verified
    # declarations may claim by hash, same rule as the skill path.
    wakes = sv.index_wakes([_wake_row(skill="medical-records-chaser", hashes=[{"body_sha256_full": sha}])])
    dispatches = sv.index_dispatches([_dispatch_row(sha=sha, skill=None)])
    assert sv.verify_hash_join(wakes, dispatches, _declares()) == []


def test_an_unlabelled_dispatch_with_no_hash_gets_no_verdict():
    """A bare NULL-column row with no rendered stamp is not evidence of
    anything -- there is nothing to attribute by. Silent, not a hold."""
    sha = sv.canonical_body_sha256("body")
    wakes = sv.index_wakes([_wake_row(hashes=[{"body_sha256_full": sha}])])
    dispatches = sv.index_dispatches([_dispatch_row(sha="", skill=None)])
    assert sv.verify_hash_join(wakes, dispatches, _declares()) == []


def test_the_attribution_counts_reach_the_report_and_the_json():
    """Counted metrics, not a detail string: the two numbers are what the
    runtime proof reads before and after the overlay pin."""
    sha = sv.canonical_body_sha256("body")
    rows = [
        _wake_row(minute=0, hashes=[{"body_sha256_full": sha}] * 2),
        _dispatch_row(minute=1, sha=sha),
        _dispatch_row(minute=2, sha=sha, skill=None),
    ]
    verifier = sv.SendVerifier(_declares(), {"recipients": {}, "ack_codes": {}})
    verdicts, findings, proposals = verifier.verify_inbox([], rows, None)
    report = rec.InboxReport(inbox="i@agentmail.to", slug="pilot-smokeball")
    report.body_verdicts, report.invariant_findings = verdicts, findings
    report.invariant_proposals = proposals
    as_json = rec.report_dict(report)
    assert as_json["attributed_by_skill"] == 1
    assert as_json["attributed_by_hash"] == 1
    assert [v["attribution"] for v in as_json["body_verdicts"]] == ["skill", "hash"]
    assert "attributed_by_skill=1 attributed_by_hash=1" in rec.render([report])
    # And the line is absent when nothing was paired: a hold-only inbox must
    # not print zeros that read as "measured and clean".
    empty = rec.InboxReport(inbox="i@agentmail.to", slug="pilot-smokeball")
    assert "attributed_by" not in rec.render([empty])


# ---------------------------------------------------------------------------
# secondary check: the channel body fetch
# ---------------------------------------------------------------------------


def _sent_message(minute=1, mid="<m1>", token=""):
    message = {
        "message_id": mid,
        "timestamp": _ts(minute),
        "to": ["x@example.invalid"],
        "subject": "s",
    }
    if token:
        # What msgraph_channel.normalize_graph_message lifts off X-SMD-Audit-Row.
        message["audit_row_token"] = token
    return message


def test_channel_body_matching_the_wake_hash_grades_match():
    body = "Deadline alert body\n"
    sha = sv.canonical_body_sha256(body)
    wakes = sv.index_wakes([_wake_row(hashes=[{"body_sha256_full": sha}])])
    verdicts = sv.verify_channel_bodies([_sent_message()], wakes, _declares(), lambda m: body)
    assert [v.verdict for v in verdicts] == [sv.VERDICT_MATCH]


def test_a_pre_deploy_inbox_with_no_plain_stamp_anywhere_still_holds():
    """MERGE-ORDER SAFETY, and the reason the promotion is conditional rather
    than a flag day. A seat whose pinned OVERLAY_REF predates
    `plain_body_sha256` emits dispatch rows without it, yet STILL down-renders
    templated sends -- so the mailbox holds the plain text while the only stamp
    hashes raw markdown. Grading against that stamp would file a false
    BODY_DIVERGED on every conformant run. No plain stamp anywhere on the inbox
    means the overlay cannot be read at all, so absence carries no information
    and the check keeps yesterday's hold."""
    sha = sv.canonical_body_sha256("**authored**")
    wakes = sv.index_wakes([_wake_row(hashes=[{"body_sha256_full": sha}])])
    dispatches = sv.index_dispatches([_dispatch_row(sha=sha)])  # no plain stamp
    assert dispatches[0].plain_body_sha256 == ""
    verdicts = sv.verify_channel_bodies(
        [_sent_message()],
        wakes,
        _declares(),
        lambda m: "authored",  # the down-render the old overlay left unrecorded
        dispatches=dispatches,
    )
    assert [v.verdict for v in verdicts] == [sv.VERDICT_CHANNEL_MISMATCH]
    assert verdicts[0].is_hold and not verdicts[0].is_finding


def test_a_window_spanning_the_plain_stamp_deploy_grades_each_row_by_its_side_of_the_edge():
    """THE 2026-09-04 LIVE MISGRADE (pilot-smokeball, --days 7). The 09-01
    escalator send predated the seat's #338 reprovision: no plain stamp, and
    the mailbox holds its down-render. The 09-03/09-04 rows on the same inbox
    carried the stamp. A per-INBOX discriminator read the whole inbox as
    post-deploy and filed BODY_DIVERGED on the 09-01 row as "no down-render on
    this send". The edge is per ROW: the earliest stamped row on the inbox is
    the deploy boundary; before it, absence is a hold; at or after it, absence
    is deliberate and grades.

    Two identified templated sends on one inbox, a day apart. The first row
    carries no plain stamp and its channel body is the down-render (a conformant
    pre-deploy send); the second carries the stamp and matches. Expected:
    pre-edge + match. FALSIFIER: replace the edge with the old per-inbox boolean
    (`plain_edge = min ts` -> `any(stamps)`) and the first grades BODY_DIVERGED.

    The pre row is `pre_stamp_edge`, not a hold (2026-09-09): the seat stamps
    now, so nothing can ever grade that row, and a hold on it was a red the
    scheduled run could never clear (eleven in a row on this exact send).
    """
    raw = "**Deadline** alert body\n"
    plain = "Deadline alert body\n"
    raw_sha, plain_sha = sv.canonical_body_sha256(raw), sv.canonical_body_sha256(plain)
    day_later = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
    pre = _dispatch_row(minute=1, sha=raw_sha, message_id="<m-pre>")  # 08-30, no plain stamp
    post = _dispatch_row(minute=1, sha=raw_sha, plain=plain_sha, message_id="<m-post>")
    post["ts"] = (day_later.replace(minute=1)).isoformat()
    post["id"] = "disp-post"
    wake_post = _wake_row(hashes=[{"body_sha256_full": raw_sha}])
    wake_post["ts"] = day_later.isoformat()
    wake_post["id"] = "wake-post"
    wakes = sv.index_wakes([_wake_row(hashes=[{"body_sha256_full": raw_sha}]), wake_post])
    dispatches = sv.index_dispatches([pre, post])
    assert [d.plain_body_sha256 for d in dispatches] == ["", plain_sha]
    sent = [
        _sent_message(minute=1, mid="<m-pre>"),
        {**_sent_message(minute=1, mid="<m-post>"), "timestamp": post["ts"]},
    ]
    verdicts = sv.verify_channel_bodies(sent, wakes, _declares(), lambda m: plain, dispatches=dispatches)
    graded = {v.message_id: v.verdict for v in verdicts}
    assert graded == {"<m-pre>": sv.VERDICT_PRE_EDGE, "<m-post>": sv.VERDICT_MATCH}
    pre_verdict = next(v for v in verdicts if v.message_id == "<m-pre>")
    assert pre_verdict.is_pre_edge
    assert not pre_verdict.is_hold and not pre_verdict.is_finding
    assert "predates the inbox's first plain_body_sha256 stamp" in pre_verdict.detail
    assert "unverifiable by construction" in pre_verdict.detail
    # The edge itself, stated: the post row's timestamp, and rows AT the edge grade.
    assert sv.plain_stamp_edge(dispatches) == dispatches[1].ts
    assert sv.stamps_plain_at(sv.plain_stamp_edge(dispatches), dispatches[1])
    assert not sv.stamps_plain_at(sv.plain_stamp_edge(dispatches), dispatches[0])
    assert sv.plain_stamp_edge([dispatches[0]]) is None


def _edge_scene():
    """The 09-01 pilot-smokeball shape, minimal: one identified templated send
    whose row predates the plain stamp and whose channel body is its
    down-render, plus a later stamped row that IS the edge."""
    raw, plain = "**Deadline** alert body\n", "Deadline alert body\n"
    raw_sha, plain_sha = sv.canonical_body_sha256(raw), sv.canonical_body_sha256(plain)
    day_later = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
    pre = _dispatch_row(minute=1, sha=raw_sha, message_id="<m-pre>")
    post = _dispatch_row(minute=1, sha=raw_sha, plain=plain_sha, message_id="<m-post>")
    post["ts"], post["id"] = day_later.replace(minute=1).isoformat(), "disp-post"
    wake_pre = _wake_row(hashes=[{"body_sha256_full": raw_sha}])
    wake_post = _wake_row(hashes=[{"body_sha256_full": raw_sha}])
    wake_post["ts"], wake_post["id"] = day_later.isoformat(), "wake-post"
    sent = [
        _sent_message(minute=1, mid="<m-pre>"),
        {**_sent_message(minute=1, mid="<m-post>"), "timestamp": post["ts"]},
    ]
    return plain, [wake_pre, wake_post], [pre, post], sent


def _report_for(rows, sent, body):
    verifier = sv.SendVerifier(_declares(), {"recipients": {}, "ack_codes": {}})
    verdicts, findings, proposals = verifier.verify_inbox(sent, rows, lambda m: body)
    report = rec.InboxReport(inbox="pilot-smokeball@agentmail.to", slug="pilot-smokeball")
    report.sent_total = len(sent)
    report.body_verdicts, report.invariant_findings = verdicts, findings
    report.invariant_proposals = proposals
    return report


def test_a_row_behind_the_edge_neither_reddens_nor_files_and_the_same_row_with_no_edge_still_reddens():
    """THE 2026-09-01..09-09 RED STREAK. With the edge present (the seat stamps
    now), the pre-edge row is reported and the run is CLEAN: no `HOLD` line,
    exit 0, nothing in the fingerprint, the count printed indented so it is
    visible. FALSIFIER, same scene minus the edge (drop the stamped row and
    its wake): the identical pre-deploy row is a HOLD again, exit 2, a `HOLD`
    line in column 0 -- because on a seat that has never stamped the stamp may
    still arrive, and red is the pressure to deploy. Both halves in one test
    so the discriminator is the edge and nothing else."""
    plain, wakes, dispatches, sent = _edge_scene()
    report = _report_for(wakes + dispatches, sent, plain)
    verdicts = {v.message_id: v for v in report.body_verdicts if v.message_id}
    assert verdicts["<m-pre>"].verdict == sv.VERDICT_PRE_EDGE
    assert verdicts["<m-post>"].verdict == sv.VERDICT_MATCH
    assert not report.is_finding
    assert not sv.has_holds(report.body_verdicts)
    assert rec.exit_code([report]) == rec.EXIT_CLEAN
    rendered = rec.render([report])
    assert not any(line.startswith("HOLD") for line in rendered.splitlines())
    assert "pre-edge 1 send(s) unverifiable by construction [deadline-miss-escalator]" in rendered
    assert rec.finding_digest([report]) == ""
    emitted = rec.report_dict(report)["body_verdicts"]
    assert sv.VERDICT_PRE_EDGE in {v["verdict"] for v in emitted}  # visible in --json too

    # The falsifier: no edge on the inbox, the same row holds and reddens.
    report_no_edge = _report_for([wakes[0], dispatches[0]], sent[:1], plain)
    assert [v.verdict for v in report_no_edge.body_verdicts if v.message_id] == [sv.VERDICT_CHANNEL_MISMATCH]
    assert sv.has_holds(report_no_edge.body_verdicts)
    assert rec.exit_code([report_no_edge]) == rec.EXIT_HOLD
    assert any(line.startswith("HOLD") for line in rec.render([report_no_edge]).splitlines())
    assert "no dispatch row on this inbox carries plain_body_sha256 yet" in (report_no_edge.body_verdicts[-1].detail)


def test_pre_edge_is_its_own_class_not_a_hold_in_disguise():
    """Law 12 for the predicate set: the three classes are disjoint, and the
    hold tuple the workflow's red is derived from does not contain the new
    verdict. A regression that slipped `pre_stamp_edge` back into
    `_HOLD_VERDICTS` would fail here before it reddened a run."""
    assert sv.VERDICT_PRE_EDGE not in sv._HOLD_VERDICTS
    verdict = sv.BodyVerdict(skill_name="deadline-miss-escalator", verdict=sv.VERDICT_PRE_EDGE)
    assert verdict.is_pre_edge
    assert not verdict.is_hold and not verdict.is_finding and not verdict.is_degraded
    for name in (
        sv.VERDICT_NO_WAKE_HASH,
        sv.VERDICT_NO_DISPATCH_STAMP,
        sv.VERDICT_BODY_UNAVAILABLE,
        sv.VERDICT_CHANNEL_MISMATCH,
    ):
        assert sv.BodyVerdict(skill_name="x", verdict=name).is_hold
        assert not sv.BodyVerdict(skill_name="x", verdict=name).is_pre_edge


def test_a_pre_edge_line_carries_hashes_only():
    """The new report line goes through the same leak discipline as the rest:
    a sentinel channel body behind the edge appears in no rendered line."""
    plain, wakes, dispatches, sent = _edge_scene()
    body = f"Deadline alert body\n{SENTINEL}\n"  # behind the edge, mismatching, sentinel-bearing
    report = _report_for(wakes + dispatches, sent, body)
    assert any(v.is_pre_edge for v in report.body_verdicts)
    assert SENTINEL not in rec.render([report])
    assert _walk_for_sentinel(json.loads(json.dumps(rec.report_dict(report)))) == []


def test_absence_on_a_stamping_inbox_grades_against_the_rendered_hash():
    """ABSENCE IS A FACT, NOT A GAP (hermes-smd-overlay#338). The overlay omits
    `plain_body_sha256` rather than duplicating the raw hash whenever no
    down-render ran -- a prose reply, a composer-supplied html body. On an inbox
    where the overlay demonstrably DOES stamp, that omission means "the channel
    text is still the bytes the gate allowed", so `rendered_body_sha256` is the
    right counterpart and the send grades MATCH. Holding it instead would leave
    every prose reply permanently red."""
    prose = "No markdown here, just a reply.\n"
    prose_sha = sv.canonical_body_sha256(prose)
    report_raw = "**Report** body\n"
    # The wake authored the REPORT body, so the prose reply that lands in the
    # same window matches neither wake hash -- which is what forces the grading
    # down to the dispatch row instead of short-circuiting on the wake stamp.
    wakes = sv.index_wakes(
        [
            _wake_row(
                minute=0,
                hashes=[{"body_sha256_full": sv.canonical_body_sha256(report_raw)}],
            )
        ]
    )
    dispatches = sv.index_dispatches(
        [
            # A sibling report send on the same inbox, EARLIER, proves the
            # overlay stamps from that row on -- the deploy edge -- which is
            # what makes the omission on the later row readable as deliberate.
            _dispatch_row(
                minute=1,
                sha=sv.canonical_body_sha256(report_raw),
                plain=sv.canonical_body_sha256("Report body\n"),
            ),
            # The prose send, after the edge: rendered only, because no
            # down-render happened. Identified so its own row is the counterpart.
            _dispatch_row(minute=2, sha=prose_sha, message_id="<m1>"),
        ]
    )
    verdicts = sv.verify_channel_bodies(
        [_sent_message(minute=2)],
        wakes,
        _declares(),
        lambda m: prose,
        dispatches=dispatches,
    )
    assert [v.verdict for v in verdicts] == [sv.VERDICT_MATCH]
    assert not verdicts[0].is_hold and not verdicts[0].is_finding
    # Proven to have gone through the ABSENCE path, not the wake-stamp
    # short-circuit: the expectation names the rendered hash of the claimed row.
    assert verdicts[0].expected_sha256 == prose_sha
    assert verdicts[0].dispatch_ts is not None


def test_absence_on_a_stamping_inbox_still_finds_a_real_divergence():
    """The other half of the same rule: absence routes the comparison to
    `rendered_body_sha256`, it does not excuse it. A body matching neither hash
    is still a finding."""
    prose_sha = sv.canonical_body_sha256("No markdown here.\n")
    report_raw = "**Report** body\n"
    wakes = sv.index_wakes(
        [
            _wake_row(
                minute=0,
                hashes=[{"body_sha256_full": sv.canonical_body_sha256(report_raw)}],
            ),
            _wake_row(minute=10, hashes=[{"body_sha256_full": prose_sha}]),
        ]
    )
    dispatches = sv.index_dispatches(
        [
            # The stamped report row first: the deploy edge, so the prose row
            # after it is post-deploy and its absence is deliberate.
            _dispatch_row(
                minute=1,
                sha=sv.canonical_body_sha256(report_raw),
                plain=sv.canonical_body_sha256("Report body\n"),
            ),
            # Joined to the message by id (B7): an identified divergence is the
            # finding; the unidentified twin is pinned as a hold further down.
            _dispatch_row(minute=11, sha=prose_sha, message_id="<m1>"),
        ]
    )
    verdicts = sv.verify_channel_bodies(
        [_sent_message(minute=11)],
        wakes,
        _declares(),
        lambda m: "something the routine never wrote",
        dispatches=dispatches,
    )
    assert [v.verdict for v in verdicts] == [sv.VERDICT_DIVERGED]
    assert verdicts[0].is_finding


def test_a_channel_body_matching_the_plain_stamp_grades_match():
    """The calibration itself: AgentMail stores the post-render_plain text, so
    the channel body matches the PLAIN stamp while mismatching the raw-markdown
    wake stamp. That is a correct send, not a divergence."""
    raw = "**Deadline** alert body\n"
    plain = "Deadline alert body\n"  # what render_plain attached to the channel
    wakes = sv.index_wakes([_wake_row(hashes=[{"body_sha256_full": sv.canonical_body_sha256(raw)}])])
    dispatches = sv.index_dispatches(
        [_dispatch_row(sha=sv.canonical_body_sha256(raw), plain=sv.canonical_body_sha256(plain))]
    )
    verdicts = sv.verify_channel_bodies([_sent_message()], wakes, _declares(), lambda m: plain, dispatches=dispatches)
    assert [v.verdict for v in verdicts] == [sv.VERDICT_MATCH]
    assert not verdicts[0].is_finding and not verdicts[0].is_hold


def test_a_channel_body_matching_neither_stamp_is_a_finding():
    """Calibration is done, so a real divergence is now a FINDING, not a hold:
    the plain stamp gives the channel body a same-representation counterpart, so
    a mismatch against it can no longer be explained by a channel transform."""
    raw = "**Deadline** alert body\n"
    plain = "Deadline alert body\n"
    wakes = sv.index_wakes([_wake_row(hashes=[{"body_sha256_full": sv.canonical_body_sha256(raw)}])])
    dispatches = sv.index_dispatches(
        [
            _dispatch_row(
                sha=sv.canonical_body_sha256(raw),
                plain=sv.canonical_body_sha256(plain),
                message_id="<m1>",
            )
        ]
    )
    verdicts = sv.verify_channel_bodies(
        [_sent_message()],
        wakes,
        _declares(),
        lambda m: "something else entirely",
        dispatches=dispatches,
    )
    assert [v.verdict for v in verdicts] == [sv.VERDICT_DIVERGED]
    assert verdicts[0].is_finding and not verdicts[0].is_hold
    # The report must name what was actually expected -- the plain stamp, not
    # the raw wake hash it could never have equalled.
    assert verdicts[0].expected_sha256 == sv.canonical_body_sha256(plain)
    assert verdicts[0].dispatch_ts is not None


# ---------------------------------------------------------------------------
# secondary check: identity, not proximity (claims review 2026-09-04, B7)
#
# The window claim graded whichever message landed first inside a wake's hour
# against that wake's stamps. An in-turn send in a tracker's window was filed
# BODY_DIVERGED for a body the tracker never authored. A message is attributed
# by its id against the dispatch rows first; the window is the fallback, and a
# fallback holds.
# ---------------------------------------------------------------------------


def test_an_unidentified_divergence_is_a_hold_never_a_finding():
    """The same scene as the finding above with ONE difference: no dispatch row
    carries the message's id, so nothing ties the body to this routine. The
    window still claims it (there is nothing else to do with it) but a
    divergence found by proximity is a guess, and a guess holds."""
    raw = "**Deadline** alert body\n"
    plain = "Deadline alert body\n"
    wakes = sv.index_wakes([_wake_row(hashes=[{"body_sha256_full": sv.canonical_body_sha256(raw)}])])
    dispatches = sv.index_dispatches(
        [_dispatch_row(sha=sv.canonical_body_sha256(raw), plain=sv.canonical_body_sha256(plain))]
    )
    verdicts = sv.verify_channel_bodies(
        [_sent_message()],
        wakes,
        _declares(),
        lambda m: "something else entirely",
        dispatches=dispatches,
    )
    assert [v.verdict for v in verdicts] == [sv.VERDICT_CHANNEL_MISMATCH]
    assert verdicts[0].is_hold and not verdicts[0].is_finding
    assert "no dispatch row identifies this message" in verdicts[0].detail


def test_the_cross_skill_scene_grades_the_tracker_against_its_own_dispatch():
    """THE MOTION-CALENDAR CASE. A tracker wake stamps its hash at :00. An
    in-turn send (a NULL-column row, prose body, its own message id) goes out
    at :01, inside the tracker's window; the tracker's own dispatch follows at
    :02. Before B7 the window claimed the :01 message first -- it was the oldest
    in the window -- and filed BODY_DIVERGED against the tracker for a body the
    tracker never wrote. Now the in-turn message is identified as nobody's
    (row joined, skill "") and is never claimable; the tracker wake grades its
    OWN message, and grades it MATCH.

    FALSIFIER: drop the identity gate from _messages_in_window and this scene
    produces a DIVERGED verdict for <m-inturn> again.
    """
    tracker_raw = "**Verification** hold surface\n"
    tracker_plain = "Verification hold surface\n"
    prose = "Re: the motion calendar for next week.\n"
    wakes = sv.index_wakes(
        [
            _wake_row(
                skill="client-verification-tracker",
                minute=0,
                hashes=[{"body_sha256_full": sv.canonical_body_sha256(tracker_raw)}],
            )
        ]
    )
    dispatches = sv.index_dispatches(
        [
            _dispatch_row(
                skill=None,
                minute=1,
                sha=sv.canonical_body_sha256(prose),
                message_id="<m-inturn>",
            ),
            _dispatch_row(
                skill="client-verification-tracker",
                minute=2,
                sha=sv.canonical_body_sha256(tracker_raw),
                plain=sv.canonical_body_sha256(tracker_plain),
                message_id="<m-tracker>",
            ),
        ]
    )
    bodies = {"<m-inturn>": prose, "<m-tracker>": tracker_plain}
    verdicts = sv.verify_channel_bodies(
        [
            _sent_message(minute=1, mid="<m-inturn>"),
            _sent_message(minute=2, mid="<m-tracker>"),
        ],
        wakes,
        _declares(),
        lambda m: bodies[m["message_id"]],
        dispatches=dispatches,
    )
    assert [(v.message_id, v.verdict) for v in verdicts] == [("<m-tracker>", sv.VERDICT_MATCH)]
    assert verdicts[0].skill_name == "client-verification-tracker"
    # Graded against ITS row's plain stamp, not the in-turn row's.
    assert verdicts[0].expected_sha256 == sv.canonical_body_sha256(tracker_plain)


def test_a_message_identified_as_another_skills_is_not_claimed_by_this_wake():
    """Two templated routines in one hour. The escalator's message must not be
    graded against the tracker's stamps just because it landed first."""
    esc_raw, esc_plain = "**Deadline** alert\n", "Deadline alert\n"
    trk_raw, trk_plain = "**Verification** hold\n", "Verification hold\n"
    wakes = sv.index_wakes(
        [
            _wake_row(
                skill="client-verification-tracker",
                minute=0,
                hashes=[{"body_sha256_full": sv.canonical_body_sha256(trk_raw)}],
            ),
            _wake_row(
                skill="deadline-miss-escalator",
                minute=0,
                hashes=[{"body_sha256_full": sv.canonical_body_sha256(esc_raw)}],
            ),
        ]
    )
    dispatches = sv.index_dispatches(
        [
            _dispatch_row(
                skill="deadline-miss-escalator",
                minute=1,
                sha=sv.canonical_body_sha256(esc_raw),
                plain=sv.canonical_body_sha256(esc_plain),
                message_id="<m-esc>",
            ),
            _dispatch_row(
                skill="client-verification-tracker",
                minute=2,
                sha=sv.canonical_body_sha256(trk_raw),
                plain=sv.canonical_body_sha256(trk_plain),
                message_id="<m-trk>",
            ),
        ]
    )
    bodies = {"<m-esc>": esc_plain, "<m-trk>": trk_plain}
    verdicts = sv.verify_channel_bodies(
        [
            _sent_message(minute=1, mid="<m-esc>"),
            _sent_message(minute=2, mid="<m-trk>"),
        ],
        wakes,
        _declares(),
        lambda m: bodies[m["message_id"]],
        dispatches=dispatches,
    )
    graded = {v.message_id: (v.skill_name, v.verdict) for v in verdicts}
    assert graded == {
        "<m-esc>": ("deadline-miss-escalator", sv.VERDICT_MATCH),
        "<m-trk>": ("client-verification-tracker", sv.VERDICT_MATCH),
    }


def test_an_unlabelled_row_identified_by_hash_still_finds_a_divergence():
    """Pre-pin seat, secondary check: the joined row has a NULL column but its
    rendered hash is the wake's, so the message IS this routine's (attributed
    by hash, same rule as the primary check) -- and a channel body that
    matches neither hash is then a finding, not a hold."""
    raw, plain = "**Deadline** alert body\n", "Deadline alert body\n"
    wakes = sv.index_wakes([_wake_row(hashes=[{"body_sha256_full": sv.canonical_body_sha256(raw)}])])
    dispatches = sv.index_dispatches(
        [
            _dispatch_row(
                skill=None,
                sha=sv.canonical_body_sha256(raw),
                plain=sv.canonical_body_sha256(plain),
                message_id="<m1>",
            )
        ]
    )
    verdicts = sv.verify_channel_bodies(
        [_sent_message()],
        wakes,
        _declares(),
        lambda m: "something else entirely",
        dispatches=dispatches,
    )
    assert [v.verdict for v in verdicts] == [sv.VERDICT_DIVERGED]
    # And the conformant twin of the same scene grades MATCH through the same
    # identified row.
    wakes = sv.index_wakes([_wake_row(hashes=[{"body_sha256_full": sv.canonical_body_sha256(raw)}])])
    dispatches = sv.index_dispatches(
        [
            _dispatch_row(
                skill=None,
                sha=sv.canonical_body_sha256(raw),
                plain=sv.canonical_body_sha256(plain),
                message_id="<m1>",
            )
        ]
    )
    verdicts = sv.verify_channel_bodies([_sent_message()], wakes, _declares(), lambda m: plain, dispatches=dispatches)
    assert [v.verdict for v in verdicts] == [sv.VERDICT_MATCH]


def test_msgraph_messages_join_on_the_audit_row_token():
    """Graph's 202 returns no id, so the msgraph reader lifts the broker's own
    ULID off the X-SMD-Audit-Row header; the dispatch row recorded the same
    token. That is the identity join on the paying seat's channel."""
    raw, plain = "**Deadline** alert body\n", "Deadline alert body\n"
    wakes = sv.index_wakes([_wake_row(hashes=[{"body_sha256_full": sv.canonical_body_sha256(raw)}])])
    dispatches = sv.index_dispatches(
        [
            _dispatch_row(
                sha=sv.canonical_body_sha256(raw),
                plain=sv.canonical_body_sha256(plain),
                extra={
                    "audit_row_token": "01ABC",
                    "graph_message_id": "(no id available)",
                },
            )
        ]
    )
    assert dispatches[0].join_keys == frozenset({"01ABC"})
    verdicts = sv.verify_channel_bodies(
        [_sent_message(mid="", token="01ABC")],
        wakes,
        _declares(),
        lambda m: "something else entirely",
        dispatches=dispatches,
    )
    assert [v.verdict for v in verdicts] == [sv.VERDICT_DIVERGED]  # identified => finding


def test_identity_beats_proximity_when_choosing_the_stamp_to_grade_against():
    """Two same-skill dispatches in one window; the message belongs to the
    SECOND by id. The stamp the body is graded against must be its own row's,
    not the oldest unconsumed one."""
    raw = "**Deadline** alert body\n"
    first_plain, second_plain = "Deadline alert body A\n", "Deadline alert body B\n"
    wakes = sv.index_wakes([_wake_row(hashes=[{"body_sha256_full": sv.canonical_body_sha256(raw)}] * 2)])
    dispatches = sv.index_dispatches(
        [
            _dispatch_row(
                minute=1,
                sha=sv.canonical_body_sha256(raw),
                plain=sv.canonical_body_sha256(first_plain),
                message_id="<m-a>",
            ),
            _dispatch_row(
                minute=2,
                sha=sv.canonical_body_sha256(raw),
                plain=sv.canonical_body_sha256(second_plain),
                message_id="<m-b>",
            ),
        ]
    )
    verdicts = sv.verify_channel_bodies(
        [_sent_message(minute=2, mid="<m-b>")],
        wakes,
        _declares(),
        lambda m: second_plain,
        dispatches=dispatches,
    )
    assert [v.verdict for v in verdicts] == [sv.VERDICT_MATCH]
    assert verdicts[0].expected_sha256 == sv.canonical_body_sha256(second_plain)
    assert dispatches[1].plain_consumed and not dispatches[0].plain_consumed


def test_one_plain_stamp_cannot_vouch_for_two_channel_bodies():
    """Consumption discipline, the same one-to-one rule `_claim_wake` enforces
    on the primary check. Two mailbox messages, ONE stamped dispatch: the first
    is graded against the stamp, the second finds no unconsumed stamp and falls
    back to the hold. Without this, one correct send would launder every later
    body in the window."""
    plain = "Deadline alert body\n"
    raw = "**Deadline** alert body\n"
    wakes = sv.index_wakes([_wake_row(hashes=[{"body_sha256_full": sv.canonical_body_sha256(raw)}] * 2)])
    dispatches = sv.index_dispatches(
        [_dispatch_row(sha=sv.canonical_body_sha256(raw), plain=sv.canonical_body_sha256(plain))]
    )
    verdicts = sv.verify_channel_bodies(
        [_sent_message(minute=1, mid="<m1>"), _sent_message(minute=2, mid="<m2>")],
        wakes,
        _declares(),
        lambda m: plain,
        dispatches=dispatches,
    )
    assert [v.verdict for v in verdicts] == [
        sv.VERDICT_MATCH,
        sv.VERDICT_CHANNEL_MISMATCH,
    ]


def test_a_plain_stamp_outside_the_window_does_not_vouch():
    """The window is the same one the primary check uses. A stamp from a
    different run must not rescue this run's body."""
    plain = "Deadline alert body\n"
    raw = "**Deadline** alert body\n"
    wakes = sv.index_wakes([_wake_row(hashes=[{"body_sha256_full": sv.canonical_body_sha256(raw)}])])
    dispatches = sv.index_dispatches(
        [
            _dispatch_row(
                minute=1,
                sha=sv.canonical_body_sha256(raw),
                plain=sv.canonical_body_sha256(plain),
            )
        ]
    )
    # 30s window: the message (wake minute 0) is inside it, the dispatch
    # (minute 1, 60s later) is not.
    verdicts = sv.verify_channel_bodies(
        [_sent_message(minute=0)],
        wakes,
        _declares(),
        lambda m: plain,
        dispatches=dispatches,
        window_s=30,
    )
    assert [v.verdict for v in verdicts] == [sv.VERDICT_CHANNEL_MISMATCH]
    assert dispatches[0].plain_consumed is False


def test_body_unavailable_stays_a_hold_even_with_a_plain_stamp():
    """A body we could not fetch is a transport fact. The promotion must not
    turn a 503 into an accusation."""
    raw = "**Deadline** alert body\n"
    wakes = sv.index_wakes([_wake_row(hashes=[{"body_sha256_full": sv.canonical_body_sha256(raw)}])])
    dispatches = sv.index_dispatches(
        [_dispatch_row(sha=sv.canonical_body_sha256(raw), plain=sv.canonical_body_sha256("x"))]
    )

    def _explode(_message):
        raise RuntimeError("HTTP 503")

    verdicts = sv.verify_channel_bodies([_sent_message()], wakes, _declares(), _explode, dispatches=dispatches)
    assert [v.verdict for v in verdicts] == [sv.VERDICT_BODY_UNAVAILABLE]
    assert verdicts[0].is_hold and not verdicts[0].is_finding
    # And an unfetchable body must not burn the stamp for a later message.
    assert dispatches[0].plain_consumed is False


def test_the_dispatch_stamp_stays_structurally_body_free():
    """The plain stamp is a HASH. DispatchStamp is body-free for the same reason
    the verdicts are: both repos are public. A regression that parks the plain
    TEXT here for convenience must fail before it ships.

    This searches the WHOLE field name rather than `name.split("_")[0]`, which
    is what `_BODY_FIELD_PATTERN`'s negative lookahead was written for: splitting
    on "_" throws away the "_sha256" / "_variant" the lookahead needs to see, so
    the split form both false-flags `body_variant` and misses `body_text`.
    """
    for name in sv.DispatchStamp.__dataclass_fields__:
        assert name not in ("body", "text", "content", "html", "raw", "snippet"), (
            f"DispatchStamp.{name} is a body-content field; the leak safety is structural"
        )
        assert not _BODY_FIELD_PATTERN.search(name), f"DispatchStamp.{name} could hold body content"


def test_the_dispatch_stamp_leak_check_can_fail():
    """The falsifier for the test above -- a check that cannot fail measured
    nothing. The pattern must accept today's hash fields and reject the field
    names a convenience regression would actually reach for."""
    for allowed in ("rendered_body_sha256", "plain_body_sha256", "body_variant", "ts"):
        assert not _BODY_FIELD_PATTERN.search(allowed), allowed
    for regressed in ("body", "body_text", "plain_body", "html_body", "rendered_text"):
        assert _BODY_FIELD_PATTERN.search(regressed), regressed


def test_a_failed_body_fetch_holds():
    sha = sv.canonical_body_sha256("authored")
    wakes = sv.index_wakes([_wake_row(hashes=[{"body_sha256_full": sha}])])

    def _explode(_message):
        raise RuntimeError("HTTP 503")

    verdicts = sv.verify_channel_bodies([_sent_message()], wakes, _declares(), _explode)
    assert [v.verdict for v in verdicts] == [sv.VERDICT_BODY_UNAVAILABLE]


# ---------------------------------------------------------------------------
# cross-run invariants
# ---------------------------------------------------------------------------


_EMPTY_COMMITTED = {"recipients": {}, "ack_codes": {}}


def test_ack_codes_first_seen_against_empty_committed_are_proposals_not_findings():
    """The cold-start rule (PR #2651 review, finding 2): the day the render
    cluster's stamps land, every legitimate ACK code appears at once, and a
    control that pages on the fleet's first honest day is muted by its second.
    Two DIFFERENT codes for one uncommitted key surface as two proposals the
    invariants-PR reviewer sees side by side -- visible, not paged."""
    wakes = sv.index_wakes(
        [
            _wake_row(minute=0, items=[{"item_key": "matter-1|SOL", "ack_code": "AK7Q"}]),
            _wake_row(minute=30, items=[{"item_key": "matter-1|SOL", "ack_code": "ZZ99"}]),
        ]
    )
    findings, proposals = sv.ack_invariant(wakes, _declares(), _EMPTY_COMMITTED)
    assert findings == []
    assert [(p.rule, p.value) for p in proposals] == [
        ("ack_stability", "AK7Q"),
        ("ack_stability", "ZZ99"),
    ]
    # The raw item_key never appears; only its hash does.
    assert "matter-1" not in json.dumps(sv.as_dicts([], [], proposals)[2])


def test_a_committed_ack_code_that_conflicts_is_the_finding():
    key = sv._item_hash("deadline-miss-escalator", "matter-1|SOL")
    committed = {"recipients": {}, "ack_codes": {key: "AK7Q"}}
    wakes = sv.index_wakes([_wake_row(items=[{"item_key": "matter-1|SOL", "ack_code": "AK7Q"}])])
    assert sv.ack_invariant(wakes, _declares(), committed) == ([], [])
    drifted = sv.index_wakes([_wake_row(items=[{"item_key": "matter-1|SOL", "ack_code": "XX00"}])])
    findings, proposals = sv.ack_invariant(drifted, _declares(), committed)
    assert [(f.rule, f.expected, f.actual) for f in findings] == [("ack_stability", "AK7Q", "XX00")]
    assert proposals == []


def test_recipients_first_seen_against_empty_committed_are_proposals_not_findings():
    rows = [
        _dispatch_row(
            skill="medical-records-chaser",
            sha="",
            extra={"recipient": "records@vendor.invalid"},
        )
    ]
    findings, proposals = sv.recipient_invariant(rows, _declares(), _EMPTY_COMMITTED)
    assert findings == []
    assert [p.rule for p in proposals] == ["recipient_set"]
    emitted = json.dumps(sv.as_dicts([], [], proposals)[2])
    assert "records@vendor.invalid" not in emitted  # hashes only, ever
    # Committing the proposed hash quiets the proposal entirely.
    committed = {
        "recipients": {"medical-records-chaser": [proposals[0].hashed_key]},
        "ack_codes": {},
    }
    assert sv.recipient_invariant(rows, _declares(), committed) == ([], [])


def test_a_recipient_outside_a_nonempty_committed_set_is_the_flapping_finding():
    """Once a routine HAS a reviewed recipient set, a dispatch outside it is
    the recipient-flapping defect from the review week -- a conflict with a
    committed expectation, not a cold-start artifact."""
    rows = [
        _dispatch_row(
            skill="medical-records-chaser",
            sha="",
            extra={"recipient": "stranger@elsewhere.invalid"},
        )
    ]
    committed = {
        "recipients": {
            "medical-records-chaser": [sv._recipient_hash("medical-records-chaser", "records@vendor.invalid")]
        },
        "ack_codes": {},
    }
    findings, proposals = sv.recipient_invariant(rows, _declares(), committed)
    assert [f.rule for f in findings] == ["recipient_set"]
    assert proposals == []
    assert "stranger@elsewhere.invalid" not in json.dumps(sv.as_dicts([], findings)[1])


def test_a_dispatch_row_with_no_recipient_stamp_is_skipped_not_guessed():
    rows = [_dispatch_row(skill="medical-records-chaser", sha="")]
    assert sv.recipient_invariant(rows, _declares(), _EMPTY_COMMITTED) == ([], [])


def test_missing_or_corrupt_invariants_file_loads_as_empty(tmp_path):
    assert sv.load_invariants(str(tmp_path / "absent.json")) == {
        "recipients": {},
        "ack_codes": {},
    }
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json")
    assert sv.load_invariants(str(corrupt)) == {"recipients": {}, "ack_codes": {}}


def test_the_committed_invariants_file_parses():
    loaded = sv.load_invariants()
    assert set(loaded) == {"recipients", "ack_codes"}


# ---------------------------------------------------------------------------
# LEAK SAFETY (the public-repo constraint)
# ---------------------------------------------------------------------------

_BODY_FIELD_PATTERN = re.compile(r"body(?!_sha256|_variant)|text|content|html", re.IGNORECASE)


def _walk_for_sentinel(value, path="$"):
    """Every string anywhere in a structure. Returns the paths that carry the
    sentinel -- the behavioral half of the leak test."""
    found = []
    if isinstance(value, str):
        if SENTINEL in value:
            found.append(path)
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and SENTINEL in key:
                found.append(f"{path}.{key}(key)")
            found += _walk_for_sentinel(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found += _walk_for_sentinel(item, f"{path}[{index}]")
    return found


def test_verdict_dataclasses_are_structurally_body_free():
    for cls in (sv.BodyVerdict, sv.InvariantFinding):
        for name in cls.__dataclass_fields__:
            assert not _BODY_FIELD_PATTERN.fullmatch(name.split("_")[0]) or name in (
                "expected_sha256",
                "actual_sha256",
            ), f"{cls.__name__}.{name} could hold body content"
            assert name not in ("body", "text", "content", "html", "raw", "snippet"), (
                f"{cls.__name__}.{name} is a body-content field; the leak safety is structural"
            )


def _sentinel_report():
    """A full InboxReport whose channel body carries the sentinel and whose
    wake hash mismatches, driven through the real verifier."""
    body = f"Dear client,\n{SENTINEL}\nregards"
    wake_sha = sv.canonical_body_sha256("the authored body")
    rows = [
        _wake_row(hashes=[{"body_sha256_full": wake_sha}]),
        # Joined by id and plain-stamped so the run reaches the FINDING paths
        # on both checks -- the surfaces with the most fields, and so the most
        # places to leak.
        _dispatch_row(
            sha=sv.canonical_body_sha256(body),
            plain=sv.canonical_body_sha256("the plain text the overlay attached"),
            message_id="<m1>",
        ),
    ]
    verifier = sv.SendVerifier(_declares(), {"recipients": {}, "ack_codes": {}})
    verdicts, invariants, proposals = verifier.verify_inbox([_sent_message()], rows, lambda m: body)
    assert [v.verdict for v in verdicts] == [sv.VERDICT_DIVERGED, sv.VERDICT_DIVERGED]
    report = rec.InboxReport(inbox="pilot-smokeball@agentmail.to", slug="pilot-smokeball")
    report.sent_total = 1
    report.body_verdicts = verdicts
    report.invariant_findings = invariants
    report.invariant_proposals = proposals
    return report


def test_the_sentinel_body_reaches_no_output_surface():
    report = _sentinel_report()
    rendered = rec.render([report])
    assert SENTINEL not in rendered
    as_json = json.dumps(rec.report_dict(report))
    assert _walk_for_sentinel(json.loads(as_json)) == []
    assert SENTINEL not in rec.finding_digest([report])
    assert _walk_for_sentinel(rec.baseline_entries([report])) == []


def test_the_walker_itself_can_fail():
    """The falsifier: a regressed verdict that carries the body IS caught by the
    walker. Without this, the test above is green for a reason nobody proved."""

    @dataclass
    class RegressedVerdict:
        skill_name: str
        verdict: str
        body: str  # the regression

        @property
        def is_finding(self):
            return True

        @property
        def is_hold(self):
            return False

    leaked = {"verdicts": [{"skill_name": "x", "verdict": "BODY_DIVERGED", "body": f"a {SENTINEL} b"}]}
    assert _walk_for_sentinel(leaked) != []
    # And the structural test would refuse the field name.
    assert "body" in RegressedVerdict.__dataclass_fields__


def test_render_lines_and_digest_keys_carry_hashes_only():
    report = _sentinel_report()
    lines = sv.render_lines(
        report.inbox,
        report.body_verdicts,
        report.invariant_findings,
        report.invariant_proposals,
    )
    for line in lines:
        assert SENTINEL not in line
    for key in sv.digest_keys(report.inbox, report.body_verdicts, report.invariant_findings):
        assert SENTINEL not in key


# ---------------------------------------------------------------------------
# integration with the reconciler's contract
# ---------------------------------------------------------------------------


def test_a_body_finding_reddens_exit_and_joins_the_fingerprint():
    sha_wake = sv.canonical_body_sha256("authored")
    sha_sent = sv.canonical_body_sha256("recomposed")
    rows = [
        _wake_row(hashes=[{"body_sha256_full": sha_wake}]),
        _dispatch_row(sha=sha_sent),
    ]
    verifier = sv.SendVerifier(_declares(), {"recipients": {}, "ack_codes": {}})
    verdicts, invariants, proposals = verifier.verify_inbox([], rows, None)
    report = rec.InboxReport(inbox="i@agentmail.to", slug="pilot-smokeball")
    report.body_verdicts, report.invariant_findings = verdicts, invariants
    report.invariant_proposals = proposals
    assert report.is_finding
    assert rec.exit_code([report]) == rec.EXIT_FINDING
    assert rec.finding_digest([report]) != ""


def test_a_body_hold_reddens_without_filing():
    sha = sv.canonical_body_sha256("authored")
    rows = [_dispatch_row(sha=sha)]  # no wake -> hold
    verifier = sv.SendVerifier(_declares(), {"recipients": {}, "ack_codes": {}})
    verdicts, _findings, _proposals = verifier.verify_inbox([], rows, None)
    report = rec.InboxReport(inbox="i@agentmail.to", slug="pilot-smokeball")
    report.body_verdicts = verdicts
    assert not report.is_finding
    assert rec.exit_code([report]) == rec.EXIT_HOLD
    rendered = rec.render([report])
    assert "HOLD" in rendered and "no_wake_hash" in rendered
    assert rec.finding_digest([report]) == ""  # holds file nothing


def test_proposals_alone_neither_find_nor_redden():
    """The whole point of the tier: a cold-start fleet prints paste-ready
    proposal rows and exits CLEAN. No finding, no digest, no hold."""
    rows = [
        _wake_row(items=[{"item_key": "matter-1|SOL", "ack_code": "AK7Q"}]),
        _dispatch_row(
            skill="medical-records-chaser",
            sha="",
            extra={"recipient": "records@vendor.invalid"},
        ),
    ]
    verifier = sv.SendVerifier(_declares(), {"recipients": {}, "ack_codes": {}})
    verdicts, findings, proposals = verifier.verify_inbox([], rows, None)
    assert findings == [] and len(proposals) == 2
    report = rec.InboxReport(inbox="i@agentmail.to", slug="pilot-smokeball")
    report.body_verdicts, report.invariant_findings = verdicts, findings
    report.invariant_proposals = proposals
    assert not report.is_finding
    assert rec.exit_code([report]) == rec.EXIT_CLEAN
    assert rec.finding_digest([report]) == ""
    rendered = rec.render([report])
    assert "PROPOSAL" in rendered and "send-invariants.json" in rendered
    assert "records@vendor.invalid" not in rendered  # hashes only, always


def test_a_clean_report_stays_clean_with_the_verifier_attached():
    report = rec.InboxReport(inbox="i@agentmail.to", slug="pilot-smokeball")
    assert rec.exit_code([report]) == rec.EXIT_CLEAN
