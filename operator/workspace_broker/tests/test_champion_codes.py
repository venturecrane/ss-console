"""A champion code: issued only when the administrator's own email asks for one.

The gateway decides the kind of code from the administrator's words and passes
``kind`` to the broker (``enrollment_verbs.issue_verb``). The broker does not
take that on trust: it re-reads the administrator's email from the mailbox and
labels the code ``champion`` only when that email says "champion", only on a
guide seat. The code email and the result say "champion code" or
"representative code" in fixed words, and the ``SENDER_ENROLLMENT_CODE_ISSUED``
row carries the label. A champion redeems like anyone (the label rides on the
enrolled entry), and their removal is proven by ``purge --probe``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from workspace_broker import enrollment_verbs as ev
from workspace_broker import person_purge
from workspace_broker import sender_enrollment as se
from workspace_broker.sender_enrollment_cli import main as cli_main
from workspace_broker.tests.test_sender_enrollment import (  # noqa: F401 - store_dir is a pytest fixture used by name
    DEMO_YAML,
    GUIDE_YAML,
    SMD,
    FakeMail,
    _broker,
    _issue_verb,
    _redeem,
    store_dir,
)

CHAMPION = "dana@example-brokerage.test"


def _issued_rows(tmp_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(tmp_path / "audit.db"))
    try:
        rows = conn.execute("SELECT metadata FROM audit_log WHERE action_type = ?", (ev.ISSUED_TYPE,)).fetchall()
    finally:
        conn.close()
    return [json.loads(r[0]) for r in rows]


def test_a_champion_code_is_labelled_and_said_in_fixed_words(tmp_path: Path, store_dir: Path) -> None:
    mail = FakeMail(SMD, ["I need a champion code for Dana Reyes"])
    out = _issue_verb(_broker(tmp_path, GUIDE_YAML, mail), name="Dana Reyes", kind="champion")
    assert out["sent"] is True and out["kind"] == "champion code"
    [reply] = mail.replies
    assert reply["text"].startswith("Here is the champion code for Dana Reyes:")
    [row] = se.EnrollmentStore(store_dir).codes()
    assert row.label == ev.CHAMPION_LABEL
    [audit] = _issued_rows(tmp_path)
    assert audit["label"] == ev.CHAMPION_LABEL and audit["sent"] is True
    # Dana redeems: the label is on her enrolled entry, where the gateway reads it.
    code = se.find_code(reply["text"])
    assert code is not None
    redeemed = _redeem(_broker(tmp_path, GUIDE_YAML, FakeMail(CHAMPION, [code])), code, sender=CHAMPION)
    assert redeemed["outcome"] == "enrolled" and redeemed["label"] == ev.CHAMPION_LABEL
    [entry] = se.EnrollmentStore(store_dir).enrolled()
    assert entry["label"] == ev.CHAMPION_LABEL


def test_a_representative_code_says_so(tmp_path: Path, store_dir: Path) -> None:
    mail = FakeMail(SMD, ["I need a code for Jane Smith"])
    out = _issue_verb(_broker(tmp_path, GUIDE_YAML, mail), name="Jane Smith")
    assert out["kind"] == "representative code"
    assert mail.replies[0]["text"].startswith("Here is the representative code for Jane Smith:")
    assert se.EnrollmentStore(store_dir).codes()[0].label == ev.ISSUED_LABEL
    assert _issued_rows(tmp_path)[0]["label"] == ev.ISSUED_LABEL


def test_a_champion_claim_the_email_does_not_bear_out_is_refused(tmp_path: Path, store_dir: Path) -> None:
    mail = FakeMail(SMD, ["I need a code for Dana Reyes"])
    with pytest.raises(PermissionError):
        _issue_verb(_broker(tmp_path, GUIDE_YAML, mail), name="Dana Reyes", kind="champion")
    assert mail.replies == [] and se.EnrollmentStore(store_dir).codes() == []


def test_the_demo_seat_issues_no_champion_code(tmp_path: Path, store_dir: Path) -> None:
    mail = FakeMail(SMD, ["I need a champion code for Dana Reyes"])
    with pytest.raises(PermissionError):
        _issue_verb(_broker(tmp_path, DEMO_YAML, mail), name="Dana Reyes", kind="champion")
    assert se.EnrollmentStore(store_dir).codes() == []


def test_a_removed_champion_is_proven_gone_by_the_probe(tmp_path: Path) -> None:
    data = tmp_path / "opt-data"
    notes = data / "rep" / "notes"
    (notes / person_purge.person_slug(CHAMPION)).mkdir(parents=True)
    (notes / person_purge.person_slug(CHAMPION) / "x.md").write_text("left behind", encoding="utf-8")
    config = tmp_path / "customer.yaml"
    config.write_text(
        f"customer_id: guide-test\nrecord_stores:\n  - name: rep-notes\n    path: {notes}\n    partition: sender\n",
        encoding="utf-8",
    )
    store = se.EnrollmentStore(tmp_path / "enrollment")
    with store._locked():
        store._write_enrolled([{"address": CHAMPION, "label": ev.CHAMPION_LABEL, "code_id": "c_1"}])
    targets = person_purge.PurgeTargets(data_root=data, partition_roots=[notes])
    probe = ["purge", "--probe", CHAMPION, "--data-root", str(data), "--config", str(config)]
    assert cli_main(probe, store=store) == 3, "a champion still enrolled fails the probe"
    remove = ["remove", CHAMPION, "--data-root", str(data), "--config", str(config)]
    assert cli_main(remove, store=store, purge_targets=targets, isolate=False) == 0
    assert CHAMPION not in store.enrolled_addresses()
    assert person_purge.probe(CHAMPION, targets) == []
    assert cli_main(probe, store=store) == 0


@pytest.mark.parametrize(
    ("own", "subject"),
    [
        ("I need a code for Jane Smith.", "Re: Champion code for Dana"),
        ("Please send a champion code for Dana Reyes and a code for Jane Smith.", "codes"),
        ("I need a code for Jane Smith. Jane is a rep, not a champion.", "codes"),
    ],
)
def test_a_champion_claim_for_the_wrong_person_is_refused(
    tmp_path: Path,
    store_dir: Path,
    own: str,
    subject: str,
) -> None:
    """Whatever the gateway claims, the broker reads only the administrator's
    own words, never the subject or quoted history, for this person's name."""
    mail = FakeMail(SMD, [subject, own, "> On Monday: a champion code for Jane Smith"], own=own)
    with pytest.raises(PermissionError):
        _issue_verb(_broker(tmp_path, GUIDE_YAML, mail), name="Jane Smith", kind="champion")
    assert mail.replies == [] and se.EnrollmentStore(store_dir).codes() == []


def test_the_subject_alone_never_makes_a_champion_code(tmp_path: Path, store_dir: Path) -> None:
    mail = FakeMail(
        SMD, ["Champion code for Dana Reyes", "I need a code for Dana Reyes"], own="I need a code for Dana Reyes"
    )
    with pytest.raises(PermissionError):
        _issue_verb(_broker(tmp_path, GUIDE_YAML, mail), name="Dana Reyes", kind="champion")
