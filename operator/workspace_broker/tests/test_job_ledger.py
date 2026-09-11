"""Unit tests for the broker-owned job ledger (B1, ADR 0051).

Focus: the trust-critical invariants — lease fencing (a stale epoch's write is
a no-op) and idempotency (a side-effecting step recorded before the effect
cannot double-fire on resume). These are the properties the durable runner's
correctness rests on, so they are tested in isolation, not assumed.
"""

from __future__ import annotations

import pytest

from workspace_broker.job_ledger import JobLedgerWriter

# Lexicographically-ordered fixed timestamps (the ISO format is sortable).
T0 = "2026-06-18T00:00:00.000Z"
T1 = "2026-06-18T00:01:00.000Z"
T2 = "2026-06-18T00:10:00.000Z"
# A cutoff EARLIER than any live lease (nothing expired) vs LATER (all expired).
CUTOFF_NONE_EXPIRED = "2026-06-17T00:00:00.000Z"
CUTOFF_ALL_EXPIRED = "2026-06-18T09:00:00.000Z"


def _writer(tmp_path) -> JobLedgerWriter:
    return JobLedgerWriter(str(tmp_path / "broker.db"))


def _new_job(w: JobLedgerWriter, budget_cents: int = 500) -> str:
    return w.create(
        {
            "customer_slug": "acme",
            "persona_id": "intake-coordinator",
            "model": "claude-sonnet-4-6",
            "brief": "Review the three production documents and surface gaps.",
            "brief_digest": "sha256:abc",
            "deliver_to": "telegram:123",
            "budget_cents": budget_cents,
        }
    )


def test_create_and_read_roundtrip(tmp_path):
    w = _writer(tmp_path)
    job_id = _new_job(w)
    row = w.read(job_id)
    assert row is not None
    assert row["status"] == "queued"
    assert row["customer_slug"] == "acme"
    assert row["budget_cents"] == 500
    assert row["spent_cents"] == 0
    assert row["lease_epoch"] == 0
    assert row["attempts"] == 0
    assert row["cancel_requested"] == 0


def test_create_rejects_unknown_and_missing_columns(tmp_path):
    w = _writer(tmp_path)
    with pytest.raises(ValueError):
        w.create({"customer_slug": "x", "persona_id": "p", "brief": "b", "budget_cents": 1, "status": "running"})
    with pytest.raises(ValueError):
        w.create({"customer_slug": "x", "persona_id": "p", "brief": "b"})  # no budget_cents


def test_ensure_schema_is_idempotent(tmp_path):
    db = str(tmp_path / "broker.db")
    JobLedgerWriter(db)
    # A second writer over the same file must not raise (CREATE IF NOT EXISTS).
    w2 = JobLedgerWriter(db)
    assert w2.read("nope") is None


def test_claim_is_exclusive_until_lease_expires(tmp_path):
    w = _writer(tmp_path)
    job_id = _new_job(w)

    epoch1 = w.claim(job_id, "worker-A", now=T0, lease_expiry_cutoff=CUTOFF_NONE_EXPIRED)
    assert epoch1 == 1
    row = w.read(job_id)
    assert row["status"] == "running"
    assert row["lease_owner"] == "worker-A"
    assert row["attempts"] == 1

    # A second claimant while the lease is live is rejected.
    assert w.claim(job_id, "worker-B", now=T1, lease_expiry_cutoff=CUTOFF_NONE_EXPIRED) is None

    # Once the lease is considered expired, a new claim wins with a HIGHER epoch.
    epoch2 = w.claim(job_id, "worker-B", now=T2, lease_expiry_cutoff=CUTOFF_ALL_EXPIRED)
    assert epoch2 == 2
    row = w.read(job_id)
    assert row["lease_owner"] == "worker-B"
    assert row["attempts"] == 2


def test_record_is_epoch_fenced(tmp_path):
    w = _writer(tmp_path)
    job_id = _new_job(w)
    epoch = w.claim(job_id, "worker-A", now=T0, lease_expiry_cutoff=CUTOFF_NONE_EXPIRED)

    # Correct epoch writes.
    assert w.record(job_id, epoch, {"spent_cents": 120, "current_tip_session_id": "sess-2"}) is True
    assert w.read(job_id)["spent_cents"] == 120

    # A stale (older) epoch is fenced out — its write is a silent no-op.
    assert w.record(job_id, epoch - 1, {"spent_cents": 999}) is False
    assert w.read(job_id)["spent_cents"] == 120  # unchanged

    # Non-mutable fields are rejected outright.
    with pytest.raises(ValueError):
        w.record(job_id, epoch, {"budget_cents": 1})
    with pytest.raises(ValueError):
        w.record(job_id, epoch, {"lease_owner": "x"})


def test_fencing_defeats_two_live_workers(tmp_path):
    """The respawn-produces-two-workers scenario: A claims, B re-claims after
    expiry, A wakes and tries to write — A's write must be rejected."""
    w = _writer(tmp_path)
    job_id = _new_job(w)
    epoch_a = w.claim(job_id, "worker-A", now=T0, lease_expiry_cutoff=CUTOFF_NONE_EXPIRED)
    epoch_b = w.claim(job_id, "worker-B", now=T2, lease_expiry_cutoff=CUTOFF_ALL_EXPIRED)
    assert epoch_b == epoch_a + 1

    # Stale worker A's checkpoint and completion are both no-ops.
    assert w.record(job_id, epoch_a, {"result_ref": "A-wrote-this"}) is False
    assert w.record(job_id, epoch_b, {"result_ref": "B-wrote-this"}) is True
    assert w.read(job_id)["result_ref"] == "B-wrote-this"


def test_idempotency_begin_then_complete_then_skip(tmp_path):
    w = _writer(tmp_path)
    job_id = _new_job(w)
    epoch = w.claim(job_id, "worker-A", now=T0, lease_expiry_cutoff=CUTOFF_NONE_EXPIRED)

    # First sight of the logical effect: proceed and perform it.
    assert w.idempotency_begin(job_id, "send:invoice-42", epoch) == "proceed"
    # While in_progress (e.g. crashed before completion), a resume must NOT
    # re-fire — fail closed to review.
    assert w.idempotency_begin(job_id, "send:invoice-42", epoch) == "review"
    # After the effect succeeds, mark done.
    assert w.idempotency_complete(job_id, "send:invoice-42", epoch) is True
    # A later resume now sees it done and skips.
    assert w.idempotency_begin(job_id, "send:invoice-42", epoch) == "skip"


def test_idempotency_complete_is_epoch_fenced(tmp_path):
    w = _writer(tmp_path)
    job_id = _new_job(w)
    epoch = w.claim(job_id, "worker-A", now=T0, lease_expiry_cutoff=CUTOFF_NONE_EXPIRED)
    assert w.idempotency_begin(job_id, "send:x", epoch) == "proceed"
    # A stale worker cannot retroactively mark the step done.
    assert w.idempotency_complete(job_id, "send:x", epoch - 1) is False


def test_request_cancel_sets_flag_and_respects_terminal(tmp_path):
    w = _writer(tmp_path)
    job_id = _new_job(w)
    epoch = w.claim(job_id, "worker-A", now=T0, lease_expiry_cutoff=CUTOFF_NONE_EXPIRED)
    assert w.request_cancel(job_id) is True
    assert w.read(job_id)["cancel_requested"] == 1

    # A terminal job cannot be cancelled.
    assert w.record(job_id, epoch, {"status": "done"}) is True
    assert w.request_cancel(job_id) is False


def test_list_all_returns_every_job_newest_first(tmp_path):
    """The observability seam (``jobs`` runtime-read kind) needs ALL jobs —
    terminal and live-leased included — newest-created first, with no lease
    filter (unlike ``list_claimable``)."""
    w = _writer(tmp_path)
    first = _new_job(w)
    second = _new_job(w)
    third = _new_job(w)

    # Drive them to distinct states: one terminal, one live-leased, one queued.
    epoch = w.claim(first, "w", now=T0, lease_expiry_cutoff=CUTOFF_NONE_EXPIRED)
    w.record(first, epoch, {"status": "done"})
    w.claim(second, "w", now=T0, lease_expiry_cutoff=CUTOFF_NONE_EXPIRED)  # live lease

    rows = w.list_all()
    ids = [r["id"] for r in rows]
    # All three present regardless of status/lease.
    assert set(ids) == {first, second, third}
    # Newest-created first (ULIDs are lexicographically time-sortable).
    assert ids == sorted(ids, reverse=True)
    # The terminal and live-leased jobs that list_claimable would hide are here.
    by_id = {r["id"]: r for r in rows}
    assert by_id[first]["status"] == "done"
    assert by_id[second]["lease_owner"] == "w"


def test_list_all_empty_when_no_jobs(tmp_path):
    assert _writer(tmp_path).list_all() == []


def test_list_claimable_excludes_terminal_and_live_leases(tmp_path):
    w = _writer(tmp_path)
    queued = _new_job(w)
    leased = _new_job(w)
    terminal = _new_job(w)

    epoch_t = w.claim(terminal, "w", now=T0, lease_expiry_cutoff=CUTOFF_NONE_EXPIRED)
    w.record(terminal, epoch_t, {"status": "done"})
    w.claim(leased, "w", now=T0, lease_expiry_cutoff=CUTOFF_NONE_EXPIRED)  # live lease

    # Nothing expired yet: only the never-claimed queued job is claimable.
    ids = {r["id"] for r in w.list_claimable(now=T1, lease_expiry_cutoff=CUTOFF_NONE_EXPIRED)}
    assert ids == {queued}

    # With all leases expired: the leased job re-appears; terminal stays out.
    ids = {r["id"] for r in w.list_claimable(now=T2, lease_expiry_cutoff=CUTOFF_ALL_EXPIRED)}
    assert ids == {queued, leased}


def test_same_millisecond_burst_orders_by_id_every_time(tmp_path):
    """The regression for ss#2486, and it pins the contract that actually holds.

    ``list_all`` orders ``created_at DESC, id DESC``, so the id and the timestamp
    are one composite sort key. They used to be minted from two separate clock
    reads, which let a pair straddle a millisecond boundary: ``created_at`` said
    a row came second while its ULID said it came first, and the composite order
    became neither id order nor creation order. That flaked the ``substrate``
    merge gate on PR #2484 and passed on re-run, which is the worst shape a gate
    can have.

    WHAT IS AND IS NOT PROMISED. For rows in the same millisecond the ledger
    gives a STABLE order, not creation order: the ULID time prefix ties too, so
    ``id DESC`` falls through to the 80 random bits. Asserting creation order
    here would be asserting something the ledger cannot deliver at millisecond
    resolution, and it fails outright. What one clock read buys is that the
    composite key is always exactly id order, on every row, forever.

    A single burst would catch the old bug only by luck, so this runs many.
    """
    for burst in range(40):
        d = tmp_path / f"burst-{burst}"
        d.mkdir()
        w = _writer(d)
        created = [_new_job(w) for _ in range(8)]
        got = [r["id"] for r in w.list_all()]
        assert set(got) == set(created), "list_all lost or invented a row"
        assert got == sorted(created, reverse=True), (
            "list_all's composite key stopped agreeing with id order, which means "
            "created_at and the ULID disagree about when a row was made (ss#2486)"
        )


def test_id_and_created_at_come_from_one_clock_read(tmp_path):
    """Pin the mechanism, not just the effect.

    A burst test can pass by luck on a fast machine, which would make it a check
    that cannot fail. This asserts the property that makes luck irrelevant: the
    millisecond embedded in the ULID and the stored ``created_at`` describe the
    same instant, because both are derived from one integer.
    """
    from datetime import UTC, datetime

    from workspace_broker.audit_ledger import _CROCKFORD

    w = _writer(tmp_path)
    job_id = _new_job(w)
    row = next(r for r in w.list_all() if r["id"] == job_id)

    ulid_ms = 0
    for ch in job_id[:10]:
        ulid_ms = ulid_ms * 32 + _CROCKFORD.index(ch)
    stamp_ms = int(datetime.strptime(row["created_at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC).timestamp() * 1000)
    assert ulid_ms == stamp_ms, (
        f"ULID says {ulid_ms}, created_at says {stamp_ms}: the two were minted "
        "from separate clock reads and can disagree (ss#2486)"
    )
