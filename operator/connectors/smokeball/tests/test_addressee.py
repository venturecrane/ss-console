"""A letter's reader from the role its addressee holds on the matter
(``smokeball_connector/addressee.py``). The role records are read by the forms'
own reader (``form_letter_facts.read_parties``) from a made-up matter."""

from __future__ import annotations

from smokeball_connector.addressee import reader_of
from smokeball_connector.form_letter_facts import read_parties

ROLES = {
    "value": [
        {
            "id": "r-client",
            "name": "Client",
            "isClient": True,
            "contactId": "c-client",
            "relationships": [{"id": "rel-1", "name": "Insurer", "contactId": "c-own-carrier"}],
        },
        {
            "id": "r-defendant",
            "name": "Defendant",
            "isOtherSide": True,
            "contactId": "c-defendant",
            "relationships": [
                {"id": "rel-2", "name": "Adjuster", "contactId": "c-adjuster"},
                {"id": "rel-3", "name": "Defense Attorney", "contactId": "c-counsel"},
            ],
        },
        {"id": "r-vendor", "name": "Records Vendor", "contactId": "c-vendor", "relationships": []},
    ]
}


class Roles:
    def get(self, path: str, **_params):
        assert path == "/matters/m-1/roles"
        return ROLES


def test_each_role_the_records_distinguish_names_its_reader():
    parties = read_parties(Roles(), "m-1")
    assert reader_of(parties, "c-client")[0] == "client"
    assert reader_of(parties, "c-own-carrier")[0] == "adjuster"
    assert reader_of(parties, "c-adjuster")[0] == "adjuster"
    assert reader_of(parties, "c-counsel")[0] == "opposing_counsel"


def test_a_role_the_records_do_not_distinguish_is_none_never_a_guess():
    parties = read_parties(Roles(), "m-1")
    for contact in ("c-vendor", "c-defendant", "c-unknown", None):
        reader, why = reader_of(parties, contact)
        assert reader is None and why
