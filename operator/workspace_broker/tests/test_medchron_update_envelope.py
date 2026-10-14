"""An update INTO the delivered chronology, as the broker queues it
(2026-10-13): `update: {into: delivered_chronology[, chronology_file_id]}`,
one client, the documents to read named by id. Everything else refuses in
prose, the way the runner's own `job.parse_update` refuses it again."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker.medchron_ledger import EnvelopeError, validate_envelope, work_digest

UNIT = {"client_name": "Alpha Example", "surname": "Example", "dob": "01/02/1980"}


def envelope(**over):
    e = {
        "matter": {"id": "m-1", "number": "2026-PI-102"},
        "units": [UNIT],
        "incident": {"date": "2026-01-15", "source": "administrator_request"},
        "selection": {"include_file_ids": ["d-1", "d-2"]},
    }
    e.update(over)
    return e


def test_an_update_into_the_delivered_chronology_is_queued_in_its_known_shape() -> None:
    e = validate_envelope(envelope(update={"into": "delivered_chronology", "chronology_file_id": "doc-9", "x": 1}))
    assert e["update"] == {"into": "delivered_chronology", "chronology_file_id": "doc-9"}
    assert validate_envelope(envelope(update={"into": "delivered_chronology"}))["update"] == {
        "into": "delivered_chronology"
    }
    # The update is different WORK from a run on the same documents.
    assert work_digest(e) != work_digest(validate_envelope(envelope()))
    # A run's envelope is unchanged by the new key: no `update` appears.
    assert "update" not in validate_envelope(envelope())


@pytest.mark.parametrize(
    "bad, why",
    [
        ({"update": {"into": "a new package"}}, "update.into"),
        ({"update": "delivered_chronology"}, "update.into"),
        ({"update": {"into": "delivered_chronology"}, "selection": None}, "include_file_ids"),
        (
            {
                "update": {"into": "delivered_chronology"},
                "units": [
                    {**UNIT, "folder_prefix": "/A"},
                    {**UNIT, "unit": "b", "surname": "Other", "folder_prefix": "/B"},
                ],
            },
            "exactly one client",
        ),
        ({"update": {"into": "delivered_chronology", "chronology_file_id": "../x"}}, "document id"),
    ],
)
def test_an_update_the_runner_could_not_place_refuses(bad, why) -> None:
    with pytest.raises(EnvelopeError, match=why):
        validate_envelope(envelope(**bad))
