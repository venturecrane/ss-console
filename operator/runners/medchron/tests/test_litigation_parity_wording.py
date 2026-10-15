"""A party list written shorter, and a status that claims no paper (2026-10-14
22:03Z proof run: 10 parity holds; 6 were one discovery set whose served-by
party the read wrote as surnames where the last list had full names, 3 were a
"Not served" or "Unclear" on a defendant the last list carried bare, and 1 was
a real change). Every name here is synthetic; the shapes are the run's."""

from __future__ import annotations

import copy

from medchron.litigation import identity, vocab
from test_litigation_identity import matter, theirs, unexplained

LONG = "Defendants Alma Quell, Bret Ostrander, Corvin Middle School, Eastvale School District"
SHORT = "Defendants Quell, Ostrander, Corvin Middle School and Eastvale School District"
ANSWERED = {"value": "yes", "date": "2026-09-29", "source": {"file_id": "fr", "name": "fr.pdf", "doc_date": None}}


def sets(party: str, date: str = "2026-06-12") -> list[dict]:
    return [
        theirs(name, party, date, "fs", copy.deepcopy(ANSWERED))
        for name in ("Form Interrogatories, Set One", "Requests for Production, Set One")
    ]


def test_a_party_list_written_shorter_is_the_same_set():
    prior, new = matter(discovery_served_on_client=sets(LONG)), matter(discovery_served_on_client=sets(SHORT))
    assert unexplained(prior, new) == []
    assert unexplained(new, prior) == []  # longer this time: the same set too


def test_a_shorter_party_list_with_a_different_date_still_holds():
    prior, new = (
        matter(discovery_served_on_client=sets(LONG)),
        matter(discovery_served_on_client=sets(SHORT, "2026-06-19")),
    )
    held = unexplained(prior, new)
    assert held and all(p.endswith(".date") for p in held)


def test_parties_that_are_not_one_within_the_other_are_two_sets():
    other = "Defendants Quell, Ostrander, Westbrook Unified"
    prior, new = matter(discovery_served_on_client=sets(LONG)), matter(discovery_served_on_client=sets(other))
    assert identity.pair_rows(prior["discovery_served_on_client"], new["discovery_served_on_client"]) == {}
    assert len(unexplained(prior, new)) == 4  # the last list's 2 sets x (date, responses) vanished


def test_two_candidate_pairs_are_left_unpaired():
    prior = matter(discovery_served_on_client=sets(LONG))
    new = matter(discovery_served_on_client=sets(SHORT) + sets("Defendants Quell"))
    assert identity.pair_rows(prior["discovery_served_on_client"], new["discovery_served_on_client"]) == {}


def _defendant(name: str, status: str | None) -> dict:
    return {
        "name": name,
        "status": status,
        "served": {"date": None, "source": None},
        "answered": {"date": None, "source": None},
    }


def test_a_status_claiming_no_paper_needs_none_and_one_claiming_service_does():
    prior = matter(
        defendants=[
            _defendant("Does 1 to 25", None),
            _defendant("Harlow Ridge LP (Doe 2)", None),
            _defendant("Pell Creek Resort", vocab.NOT_SERVED),
        ]
    )
    new = matter(
        defendants=[
            _defendant("Does 1 to 25", vocab.NOT_SERVED),
            _defendant("Harlow Ridge LP (Doe 2)", vocab.D_UNCLEAR),
            _defendant("Pell Creek Resort", vocab.SERVED_NO_ANSWER),
        ]
    )
    assert unexplained(prior, new) == ["defendants[pellcreekresort].status"]


def test_a_new_defendant_claiming_nothing_passes_and_one_claiming_service_holds():
    # A Doe amendment names two parties; no proof of service was read for them.
    new = matter(
        defendants=[
            _defendant("Harlow Ridge LP (Doe 1)", vocab.D_UNCLEAR),
            _defendant("Pell Creek Resort", vocab.ANSWERED),
        ]
    )
    assert unexplained(matter(), new) == ["defendants[pellcreekresort].status"]


def test_a_doe_placeholder_is_not_a_party():
    prior = matter(defendants=[_defendant("Does 1 to 25", vocab.NOT_SERVED), _defendant("Doe 3", vocab.NOT_SERVED)])
    assert unexplained(prior, matter()) == []
    assert unexplained(matter(), prior) == []


def _served(name: str, status: str, fid: str | None) -> dict:
    src = {"file_id": fid, "name": f"{fid}.pdf", "doc_date": None} if fid else None
    return {**_defendant(name, status), "served": {"date": "2026-03-30" if fid else None, "source": src}}


def test_not_served_beside_its_own_cited_proof_is_corrected_from_that_proof():
    prior = matter(defendants=[_served("Pell Creek Resort", vocab.NOT_SERVED, "pos")])
    assert unexplained(prior, matter(defendants=[_served("Pell Creek Resort", vocab.SERVED_NO_ANSWER, "pos")])) == []
    # a different paper, or a last list with no cited service, still holds
    assert unexplained(prior, matter(defendants=[_served("Pell Creek Resort", vocab.SERVED_NO_ANSWER, "other")])) != []
    bare = matter(defendants=[_served("Pell Creek Resort", vocab.NOT_SERVED, None)])
    assert unexplained(bare, matter(defendants=[_served("Pell Creek Resort", vocab.SERVED_NO_ANSWER, None)])) != []


def test_a_known_status_turning_unclear_still_holds():
    prior = matter(defendants=[_defendant("Pell Creek Resort", vocab.ANSWERED)])
    new = matter(defendants=[_defendant("Pell Creek Resort", vocab.D_UNCLEAR)])
    assert unexplained(prior, new) == ["defendants[pellcreekresort].status"]
