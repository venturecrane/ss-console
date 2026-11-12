"""lib/transient_http.py and the reads that use it (2026-11-12).

The daily send reconcile held both msgraph inboxes on one Graph 503/502 (run
43278803074) because its Graph and AgentMail reads did not retry, while the
seam reader beside them did. These tests pin the shared behaviour:

* a transient failure followed by success returns the success (it is not a hold);
* a non-transient failure (403) raises at once, with no retry;
* a failure that stays transient for every attempt still raises (fail loud),
  with the caller's own error wording.

What would make them false: a read that goes back to opening the URL once, or a
retry that swallows the last error.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

from bin.lib import msgraph_channel, transient_http

_BIN = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("reconcile_sends_transient", _BIN / "reconcile-sends.py")
rec = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = rec
_spec.loader.exec_module(rec)


class _Resp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.invalid", code, "x", {}, io.BytesIO(b""))


def _scripted(*outcomes):
    """An opener that raises or answers per call, recording each call."""
    calls: list[str] = []
    queue = list(outcomes)

    def opener(request, timeout=None):
        calls.append(request.full_url)
        outcome = queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return _Resp(outcome)

    return opener, calls


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(transient_http.time, "sleep", slept.append)
    return slept


GRAPH_URL = msgraph_channel.GRAPH_API_BASE + "/users/x/messages"


def test_graph_get_recovers_from_one_503(_no_sleep):
    opener, calls = _scripted(_http_error(503), {"value": []})
    assert msgraph_channel._graph_get(GRAPH_URL, "tok", opener=opener) == {"value": []}
    assert len(calls) == 2 and _no_sleep == [transient_http.RETRY_DELAYS_SECONDS[0]]


def test_graph_get_does_not_retry_a_403(_no_sleep):
    opener, calls = _scripted(_http_error(403), {"value": []})
    with pytest.raises(rec.ReconcileError, match="HTTP 403"):
        msgraph_channel._graph_get(GRAPH_URL, "tok", opener=opener)
    assert len(calls) == 1 and _no_sleep == []


def test_graph_get_still_holds_when_every_attempt_fails(_no_sleep):
    attempts = len(transient_http.RETRY_DELAYS_SECONDS) + 1
    opener, calls = _scripted(*[_http_error(502)] * attempts)
    with pytest.raises(rec.ReconcileError, match="HTTP 502"):
        msgraph_channel._graph_get(GRAPH_URL, "tok", opener=opener)
    assert len(calls) == attempts and _no_sleep == list(transient_http.RETRY_DELAYS_SECONDS)


def test_agentmail_get_recovers_from_a_reset(_no_sleep):
    opener, calls = _scripted(urllib.error.URLError(ConnectionResetError()), {"inboxes": []})
    assert rec._agentmail_get("/inboxes", "key", opener=opener) == {"inboxes": []}
    assert len(calls) == 2


def test_agentmail_get_does_not_retry_a_401(_no_sleep):
    opener, calls = _scripted(_http_error(401), {"inboxes": []})
    with pytest.raises(rec.ReconcileError, match="agentmail GET /inboxes failed"):
        rec._agentmail_get("/inboxes", "key", opener=opener)
    assert len(calls) == 1


def test_classifier_is_narrow():
    assert transient_http.is_transient(_http_error(503))
    assert transient_http.is_transient(_http_error(429))
    assert transient_http.is_transient(TimeoutError())
    assert not transient_http.is_transient(_http_error(404))
    assert not transient_http.is_transient(ValueError("bad json"))
