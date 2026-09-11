"""The raw-client matter.id -> matter.number join (matter_ref.py, ss #2390).

The fixture is CAPTURED FROM THE LIVE API (pilot tenant, 2026-08-24, trimmed to
the fields this join reads), not authored — ss #2390 AC4: an authored fixture
once taught an unreachable branch. The capture holds 8 open tasks across 4
matters, so the cache/budget arithmetic below is the real shape (a pull re-uses
lookups), and a matter genuinely absent from the capture proves
absence-not-fabrication.
"""

from __future__ import annotations

import json
from pathlib import Path

from smokeball_connector import matter_ref

_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "skills"
    / "deadline-miss-escalator"
    / "tests"
    / "fixtures"
    / "live-pull-2026-08-24.json"
)


def _load():
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


class FakeClient:
    """Serves the captured matter records; counts and can fail lookups."""

    def __init__(self, matters: dict, fail_ids: set[str] | None = None) -> None:
        self._matters = matters
        self._fail = fail_ids or set()
        self.calls: list[str] = []

    def get(self, path: str, **params):
        self.calls.append(path)
        matter_id = path.rsplit("/", 1)[-1]
        if matter_id in self._fail:
            raise RuntimeError("boom")
        matter = self._matters.get(matter_id)
        if matter is None:
            raise RuntimeError("404")
        return matter


def test_the_live_capture_resolves_every_task_to_its_matters_own_number():
    data = _load()
    tasks = data["tasks"]["value"]
    client = FakeClient(data["matters"])
    counts = matter_ref.attach_matter_numbers(client, tasks)
    assert counts["resolved"] == len(tasks)
    # The falsifier direction: each attached number IS the number on the matter
    # record the task's own GUID names — compared against the source, so a
    # projection that attached a lookalike matter's number fails here.
    for task in tasks:
        source = data["matters"][task["matter"]["id"]]
        assert task["matterNumber"] == source["number"]


def test_the_cache_makes_a_multi_task_matter_cost_one_lookup():
    data = _load()
    tasks = data["tasks"]["value"]
    distinct = {t["matter"]["id"] for t in tasks}
    client = FakeClient(data["matters"])
    matter_ref.attach_matter_numbers(client, tasks)
    assert len(client.calls) == len(distinct)


def test_a_failed_lookup_is_typed_not_collapsed():
    data = _load()
    tasks = [dict(t) for t in data["tasks"]["value"][:3]]
    failing = tasks[0]["matter"]["id"]
    client = FakeClient(data["matters"], fail_ids={failing})
    counts = matter_ref.attach_matter_numbers(client, tasks)
    assert counts[matter_ref.LOOKUP_FAILED] >= 1
    failed = [t for t in tasks if t["matter"]["id"] == failing]
    for task in failed:
        assert "matterNumber" not in task  # fail-safe: attach NOTHING
        assert task[matter_ref.ABSENT_KEY] == matter_ref.LOOKUP_FAILED


def test_a_matter_with_no_number_field_is_authored_absence():
    """A fetched record that simply has no number is a different fact from a
    failed fetch — a firm whose matters carry no numbers must not read as a
    resolution failure (which would suppress its deadline watch)."""
    matters = {"m1": {"id": "m1", "status": "Open"}}
    tasks = [{"id": "t1", "matter": {"id": "m1"}}]
    counts = matter_ref.attach_matter_numbers(FakeClient(matters), tasks)
    assert counts[matter_ref.NO_NUMBER_ON_RECORD] == 1
    assert tasks[0][matter_ref.ABSENT_KEY] == matter_ref.NO_NUMBER_ON_RECORD
    assert "matterNumber" not in tasks[0]


def test_budget_zero_disables_the_join_with_a_typed_absence():
    """The staging rehearsal lever: an authored budget of 0 forces the degraded
    path without any test sentinel in production code."""
    data = _load()
    tasks = [dict(t) for t in data["tasks"]["value"]]
    client = FakeClient(data["matters"])
    counts = matter_ref.attach_matter_numbers(client, tasks, budget=0)
    assert counts["resolved"] == 0
    assert counts[matter_ref.BUDGET_EXHAUSTED] == len(tasks)
    assert client.calls == []


def test_a_budget_miss_is_not_cached():
    data = _load()
    matters = data["matters"]
    some_id = next(iter(matters))
    cache: dict = {}
    status, number = matter_ref.resolve_matter_number(FakeClient(matters), some_id, cache, [0])
    assert status == matter_ref.BUDGET_EXHAUSTED and number is None
    assert some_id not in cache
    status, number = matter_ref.resolve_matter_number(FakeClient(matters), some_id, cache, [1])
    assert status == "resolved" and number == matters[some_id]["number"]


def test_an_item_with_no_matter_link_is_typed():
    tasks = [{"id": "t1", "subject": "orphan"}]
    counts = matter_ref.attach_matter_numbers(FakeClient({}), tasks)
    assert counts[matter_ref.NO_MATTER_LINK] == 1
    assert tasks[0][matter_ref.ABSENT_KEY] == matter_ref.NO_MATTER_LINK


def test_item_matter_id_reads_both_api_shapes_and_nothing_else():
    assert matter_ref.item_matter_id({"matter": {"id": "abc"}}) == "abc"
    assert matter_ref.item_matter_id({"matterId": "def"}) == "def"
    assert matter_ref.item_matter_id({"subject": "PI-2026-0001"}) is None
