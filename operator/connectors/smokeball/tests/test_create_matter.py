"""``create_matter`` — the Operator's own internal matter, and only that.

WHAT THIS TOOL IS FOR, and therefore what these tests are about. Every other
write in this connector puts a note, a task, or a document into a matter that
already exists. This one creates a matter, which is the firm's system of record
gaining a row that did not exist before. It is classified COMMITMENT at the
overlay for that reason, it never happens autonomously, and by the time it is
called a Named Administrator has been shown the exact matter and has said yes
to it in their own words.

So the load-bearing assertions here are the ones about NOT creating:

* the duplicate check fails CLOSED. A lookup that found a match refuses, and a
  lookup that could not complete refuses too, because "I could not check" and
  "there is nothing there" are different facts and only one of them makes it
  safe to write. A tool that treated a transport error as "nothing found" would
  open a second matter every time the case system hiccuped;
* the POST carries ``status``, because the API refuses it otherwise (400 "Must
  provide a valid Status") while the published docs call the field optional;
* a slow materialization is a SUCCESS with a pending flag, never an exception.
  The vendor answers 404 on the new matter's own id for several seconds after
  accepting it (probed live 2026-08-21: 404 at 0.6s, 2.8s, 4.9s, 7.2s, 200 at
  9.6s), and a created-but-slow matter that surfaced as a failure would invite a
  second create, which is the duplicate the whole check exists to prevent.
"""

from __future__ import annotations

import pytest

from smokeball_connector import server
from smokeball_connector.client import SmokeballApiError

NUMBER = "OPS-OPERATOR-LIBRARY"
DESCRIPTION = "Operator Library"
CONTACT_ID = "11111111-2222-3333-4444-555555555555"
TYPE_ID = "66666666-7777-8888-9999-aaaaaaaaaaaa_CA"
NEW_ID = "84693f0c-0000-0000-0000-000000000000"


class _FakeClient:
    """A Smokeball tenant with a scripted listing and a scripted read-back.

    ``get_sequence`` is the answer to each ``GET /matters/{id}`` in order: an
    int is an HTTP status to raise, a dict is a record to return.
    """

    def __init__(
        self,
        *,
        matters: list[dict] | None = None,
        get_sequence: list | None = None,
        list_error: Exception | None = None,
        search_error: Exception | None = None,
        accepted: dict | None = None,
    ) -> None:
        self.matters = matters or []
        self.get_sequence = list(get_sequence or [])
        self.list_error = list_error
        self.search_error = search_error
        self.accepted = accepted if accepted is not None else {"id": NEW_ID, "href": "/matters/x"}
        self.posts: list[tuple[str, str, dict]] = []
        self.matter_gets = 0

    def get(self, path: str, **params):
        if path == "/matters":
            if "Search" in params:
                if self.search_error:
                    raise self.search_error
                return {"value": self.matters}
            if self.list_error:
                raise self.list_error
            return {"value": self.matters}
        if path.startswith("/matters/"):
            self.matter_gets += 1
            if not self.get_sequence:
                raise AssertionError("read-back polled more times than the script allows")
            nxt = self.get_sequence.pop(0)
            if isinstance(nxt, int):
                raise SmokeballApiError("GET", path, nxt, "")
            return nxt
        raise AssertionError(path)

    def request(self, method: str, path: str, *, params=None, json=None):
        self.posts.append((method, path, json or {}))
        return self.accepted


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """The poll interval is two seconds and there are fifteen of them. Nothing
    here is testing the clock."""
    monkeypatch.setattr(server.time, "sleep", lambda _seconds: None)


def _install(monkeypatch, client: _FakeClient) -> _FakeClient:
    monkeypatch.setattr(server, "_get_client", lambda: client)
    return client


def _create(**over):
    args = {
        "description": DESCRIPTION,
        "matter_type_id": TYPE_ID,
        "client_contact_id": CONTACT_ID,
        "number": NUMBER,
    }
    args.update(over)
    return server.create_matter(**args)


# ---- the happy path, against the observed wire shape ---------------------------------


def test_the_matter_is_created_and_read_back_after_the_vendors_own_delay(monkeypatch) -> None:
    record = {"id": NEW_ID, "number": NUMBER, "description": DESCRIPTION, "status": "Open"}
    client = _install(monkeypatch, _FakeClient(get_sequence=[404, 404, record]))
    result = _create()
    assert result == {
        "created": True,
        "readback": record,
        "accepted": {"id": NEW_ID, "href": "/matters/x"},
    }
    assert client.matter_gets == 3


def test_the_post_body_carries_status_open_because_the_api_requires_it(monkeypatch) -> None:
    """Without it the API answers 400 "Must provide a valid Status". The
    published docs list the field optional and they are wrong; this is pinned
    from a live probe, not from the documentation."""
    client = _install(monkeypatch, _FakeClient(get_sequence=[{"id": NEW_ID}]))
    _create()
    method, path, body = client.posts[0]
    assert (method, path) == ("POST", "/matters")
    assert body == {
        "description": DESCRIPTION,
        "matterTypeId": TYPE_ID,
        "clientIds": [CONTACT_ID],
        "number": NUMBER,
        "status": "Open",
    }


def test_a_slow_materialization_is_a_success_with_a_pending_flag(monkeypatch) -> None:
    """Never an exception. A created matter that surfaced as a failure would
    invite a second create."""
    client = _install(monkeypatch, _FakeClient(get_sequence=[404] * 15))
    result = _create()
    assert result == {
        "created": True,
        "pending": True,
        "matter_id": NEW_ID,
        "accepted": {"id": NEW_ID, "href": "/matters/x"},
        "readback": None,
    }
    assert client.matter_gets == 15


def test_a_non_404_error_during_read_back_is_not_swallowed_into_pending(monkeypatch) -> None:
    """A 404 is "still materializing". A 403 is an answer, and a wrong answer
    reported as "pending" would be a lie told once a turn forever."""
    _install(monkeypatch, _FakeClient(get_sequence=[404, 403]))
    with pytest.raises(SmokeballApiError):
        _create()


# ---- the duplicate check, which fails closed -----------------------------------------


def test_an_existing_matter_with_this_number_refuses_before_the_post(monkeypatch) -> None:
    client = _install(
        monkeypatch,
        _FakeClient(matters=[{"id": "already-there", "number": NUMBER}]),
    )
    with pytest.raises(ValueError, match="already exists"):
        _create()
    assert client.posts == []


def test_an_existing_matter_with_this_description_and_client_refuses(monkeypatch) -> None:
    """A number can be edited off a matter while the matter remains. The second
    predicate is what stops the Operator opening a second Operator Library for
    the same firm."""
    client = _install(
        monkeypatch,
        _FakeClient(
            matters=[
                {
                    "id": "already-there",
                    "number": "SOMETHING-ELSE",
                    "description": DESCRIPTION,
                    "clientIds": [CONTACT_ID],
                }
            ]
        ),
    )
    with pytest.raises(ValueError, match="already exists"):
        _create()
    assert client.posts == []


def test_a_lookup_that_could_not_complete_refuses_and_posts_nothing(monkeypatch) -> None:
    """FAIL CLOSED. "I could not check" is not "there is nothing there"."""
    client = _install(monkeypatch, _FakeClient(list_error=RuntimeError("connection reset")))
    with pytest.raises(ValueError, match="could not complete"):
        _create()
    assert client.posts == []


def test_a_search_failure_alone_does_not_block_a_complete_enumeration(monkeypatch) -> None:
    """The cheap search is an optimization over the page-through, not a second
    source of truth. A search that errors while the full listing succeeds has
    still been fully enumerated, so the create proceeds."""
    client = _install(
        monkeypatch,
        _FakeClient(search_error=RuntimeError("search timeout"), get_sequence=[{"id": NEW_ID}]),
    )
    result = _create()
    assert result["created"] is True
    assert len(client.posts) == 1


def test_a_matter_with_the_same_description_for_a_different_client_does_not_block(
    monkeypatch,
) -> None:
    """Two firms can both call a matter "Operator Library". The pair, not the
    description alone, is the second key."""
    client = _install(
        monkeypatch,
        _FakeClient(
            matters=[
                {
                    "id": "someone-else",
                    "number": "OTHER-1",
                    "description": DESCRIPTION,
                    "clientIds": ["99999999-9999-9999-9999-999999999999"],
                }
            ],
            get_sequence=[{"id": NEW_ID}],
        ),
    )
    assert _create()["created"] is True
    assert len(client.posts) == 1


# ---- arguments -----------------------------------------------------------------------


@pytest.mark.parametrize("field", ["description", "matter_type_id", "client_contact_id", "number"])
def test_every_argument_is_required(monkeypatch, field: str) -> None:
    """``number`` included, and that is the deliberate difference from the
    vendor's optional field: it is the key this seat's library resolves on and
    the key the duplicate check uses."""
    client = _install(monkeypatch, _FakeClient(get_sequence=[{"id": NEW_ID}]))
    with pytest.raises(ValueError, match=f"{field} is required"):
        _create(**{field: "   "})
    assert client.posts == []


def test_an_accepted_response_with_no_id_is_a_named_failure(monkeypatch) -> None:
    """Without an id the matter can be neither read back nor de-duplicated, and
    a silent success would leave the firm with a matter nobody can name."""
    _install(monkeypatch, _FakeClient(accepted={"href": "/matters/x"}))
    with pytest.raises(ValueError, match="returned no matter id"):
        _create()


def test_the_docstring_says_this_is_never_a_client_matter() -> None:
    """The docstring is the runtime instruction: it is what the model reads when
    it decides whether this tool applies. The sentence that keeps a client's
    case out of it has to be in there."""
    doc = server.create_matter.__doc__ or ""
    assert "NOT A TOOL FOR OPENING A CLIENT" in doc
    assert "COMMITMENT" in doc
