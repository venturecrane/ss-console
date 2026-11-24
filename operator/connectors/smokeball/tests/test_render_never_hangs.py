"""``render_docx_draft`` always answers inside the MCP tool timeout.

2026-11-24 (A&P): two render calls each ran into the gateway's 300 s tool
timeout, and the Operator filed a .txt and told the attorney to paste it into
his template. Nothing in the render path was bounded as a whole. These tests
hold each blocking stage on a fake that never returns (or never stops being
slow) and require the call to come back with a value the Operator can act on.

Every value here is invented. The stage deadlines are shrunk to fractions of a
second so the suite runs at $0 and in under a few seconds; the production
numbers are pinned against the tool timeout separately.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx
import pytest

from smokeball_connector import draft_budget, draft_sources, readback, server
from smokeball_connector import record_check as rc

DRAFT = "# DRAFT\n\nThe intake note records the first visit on the listed day.\n"
UPLOAD_URL = "https://s3.example.com/apiuploads/draft-9?X-Amz-Signature=cafe"


@pytest.fixture
def short_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(draft_budget, "SOURCES_BY", 0.3)
    monkeypatch.setattr(draft_budget, "CHECK_BY", 0.6)
    monkeypatch.setattr(draft_budget, "RENDER_BY", 0.9)
    monkeypatch.setattr(draft_budget, "FILED_BY", 1.2)


@pytest.fixture
def never() -> Any:
    """A wait that does not end until the test is over."""
    gate = threading.Event()
    yield gate
    gate.set()


def _client(captured: list[httpx.Request]) -> Any:
    from smokeball_connector.client import SmokeballClient

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600, "token_type": "Bearer"})
        if request.method == "POST" and request.url.path.endswith("/documents/files"):
            return httpx.Response(202, json={"fileId": "file-91", "uploadUrl": UPLOAD_URL})
        return httpx.Response(200, json={"ok": True})

    client = SmokeballClient(region="us", environment="staging", client_id="c", client_secret="s", api_key="k")
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def _record_check_passes(monkeypatch: pytest.MonkeyPatch, seen: dict[str, Any] | None = None) -> None:
    monkeypatch.setattr(server, "_collect_matter_sources", lambda _m: ([("Intake Note", "text")], [], []))

    def run(*_a: Any, **k: Any) -> rc.RecordCheckResult:
        if seen is not None:
            seen.update(k)
        return rc.RecordCheckResult(passed=True, disposition="pass", refusals=[], checked_sources=1)

    monkeypatch.setattr(rc, "run_record_check", run)


def _render_in_thread(*args: Any) -> tuple[dict[str, Any] | None, float]:
    """The render, run where a hang shows as a thread still alive after 5 s."""
    box: dict[str, Any] = {}
    started = time.monotonic()
    worker = threading.Thread(target=lambda: box.setdefault("out", server.render_docx_draft(*args)), daemon=True)
    worker.start()
    worker.join(5)
    assert not worker.is_alive(), "render_docx_draft was still blocked after 5 s"
    return box.get("out"), time.monotonic() - started


def test_a_matter_read_that_never_returns_is_a_refusal_not_a_hang(monkeypatch, short_budget, never) -> None:
    """THE PROOF. Before draft_budget, this thread was still alive at 5 s: the
    render waited on the matter read for as long as it took."""
    captured: list[httpx.Request] = []
    monkeypatch.setattr(server, "_get_client", lambda: _client(captured))
    monkeypatch.setattr(server, "_collect_matter_sources", lambda _m: never.wait() and ([], [], []))

    out, took = _render_in_thread("m-201", "Response Draft", DRAFT)

    assert took < 2.0
    assert out["fileId"] is None
    assert out["outOfTime"] == "reading the matter's documents"
    # A refused render pages SMD (#4217's shape): status, reason and note set.
    assert (out["status"], out["reason"]) == ("refused", "out_of_time")
    assert out["note"]
    assert out["refusals"][0].startswith("Not filed:")
    assert "Let me look into that. I'll reply in this thread." in out["refusals"][0]
    assert not [r for r in captured if r.method in ("POST", "PUT")], "nothing may be sent after a refusal"


def test_a_template_fetch_that_never_returns_is_a_refusal(monkeypatch, short_budget, never) -> None:
    captured: list[httpx.Request] = []
    monkeypatch.setattr(server, "_get_client", lambda: _client(captured))
    _record_check_passes(monkeypatch)
    monkeypatch.setattr(server, "_render_with_format", lambda *_a: never.wait())

    out, _took = _render_in_thread("m-202", "Response Draft", DRAFT, None, None, "discovery_response")

    assert out["fileId"] is None
    assert out["outOfTime"] == "rendering the Word document"
    assert not [r for r in captured if r.method in ("POST", "PUT")]


def test_a_filing_that_never_returns_is_a_shortfall_that_forbids_a_second_upload(
    monkeypatch, short_budget, never
) -> None:
    """Once the upload started it may land; a refusal would invite the retry
    that files a duplicate. So it is the read-back's link_not_visible, which
    pages SMD and tells the agent not to file it again."""
    _record_check_passes(monkeypatch)
    monkeypatch.setattr(server, "add_file", lambda *_a, **_k: never.wait())

    out, _took = _render_in_thread("m-203", "Response Draft", DRAFT)

    assert out["status"] == readback.NOT_VISIBLE
    assert out["outOfTime"] == "filing the Word document"
    assert out["fileId"] is None
    assert out["sha256"] and out["sizeBytes"]
    assert "Do NOT retry" in out["readback_note"]


def test_the_record_check_is_given_only_the_time_left(monkeypatch) -> None:
    seen: dict[str, Any] = {}
    captured: list[httpx.Request] = []
    monkeypatch.setattr(server, "_get_client", lambda: _client(captured))
    _record_check_passes(monkeypatch, seen)

    out = server.render_docx_draft("m-204", "Response Draft", DRAFT)

    assert out["fileId"] == "file-91"
    assert 0 < seen["timeout"] <= draft_budget.CHECK_BY < rc.DEFAULT_TIMEOUT_SECONDS


def test_every_stage_ends_well_inside_the_tool_timeout() -> None:
    stages = [draft_budget.SOURCES_BY, draft_budget.CHECK_BY, draft_budget.RENDER_BY, draft_budget.FILED_BY]
    assert stages == sorted(stages)
    assert draft_budget.FILED_BY <= draft_budget.TOOL_TIMEOUT_SECONDS - 30


def test_the_filing_window_holds_the_whole_read_back() -> None:
    """A healthy filing must never be cut into link_not_visible: the smallest
    window the deadlines leave holds every read-back pause with 30 s to spare
    for the upload and the reads themselves."""
    assert draft_budget.FILING_WINDOW_MIN >= sum(readback.WAITS) + 30


def test_a_put_that_fails_after_the_post_is_a_shortfall_never_an_error(monkeypatch) -> None:
    """Review of #4223. The metadata POST creates the file record; a PUT that
    then times out used to raise SmokeballWriteError, and the model's retry
    filed a duplicate. Anything after the filing starts may have landed."""
    sent: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append((request.method, request.url.path))
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600, "token_type": "Bearer"})
        if request.method == "POST":
            return httpx.Response(202, json={"fileId": "file-92", "uploadUrl": UPLOAD_URL})
        if request.method == "PUT":
            raise httpx.ReadTimeout("slow upload", request=request)
        return httpx.Response(200, json={})

    from smokeball_connector.client import SmokeballClient

    client = SmokeballClient(region="us", environment="staging", client_id="c", client_secret="s", api_key="k")
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(server, "_get_client", lambda: client)
    _record_check_passes(monkeypatch)

    out = server.render_docx_draft("m-208", "Response Draft", DRAFT)

    assert out["status"] == readback.NOT_VISIBLE
    assert out["filingError"] == "SmokeballWriteError"
    assert "Do NOT retry" in out["readback_note"]
    assert "X-Amz-Signature" not in out["readback_detail"], "a presigned URL never reaches the agent"
    assert [p for m, p in sent if m == "POST" and p.endswith("/documents/files")] == ["/matters/m-208/documents/files"]


def test_a_record_check_cut_short_by_the_budget_is_out_of_time_not_a_finding(monkeypatch) -> None:
    monkeypatch.setattr(server, "_collect_matter_sources", lambda _m: ([("Intake Note", "text")], [], []))
    monkeypatch.setattr(
        rc,
        "run_record_check",
        lambda *_a, **_k: rc.RecordCheckResult(passed=False, disposition="timeout", refusals=["slow"]),
    )

    out = server.render_docx_draft("m-209", "Response Draft", DRAFT)

    assert out["outOfTime"] == "checking the draft against the record"
    assert (out["status"], out["reason"]) == ("refused", "out_of_time")
    assert "recordCheck" not in out


def test_a_listed_folder_count_that_never_returns_is_a_refusal(monkeypatch, short_budget, never) -> None:
    """#4217's list count calls Smokeball when listed_folder_id is set; it
    runs under the same clock."""
    from smokeball_connector import list_check

    monkeypatch.setattr(list_check, "count_refusal", lambda *_a, **_k: never.wait())
    monkeypatch.setattr(server, "_get_client", lambda: object())
    draft = "# PRODUCTION LIST\n\n1. Intake Note\n"

    out, _took = _render_in_thread("m-210", "Production List", draft, None, None, None, "folder-3")

    assert out["outOfTime"] == "counting the listed folder"
    assert out["reason"] == "out_of_time"


class _Limited:
    """Smokeball answering every request with a 30 s rate limit."""

    timeout = httpx.Timeout(30.0)

    def request(self, *_a: Any, **_k: Any) -> Any:
        return httpx.Response(429, headers={"retry-after": "30"}, text="slow down")


def _limited_client(monkeypatch: pytest.MonkeyPatch, sleeps: list[float]) -> Any:
    from smokeball_connector import client as client_mod

    monkeypatch.setattr(client_mod.time, "sleep", sleeps.append)
    client = _client([])
    client._http = _Limited()
    client._token, client._token_deadline = "tok", float("inf")
    return client


def test_a_rate_limit_is_not_slept_out_past_the_deadline(monkeypatch) -> None:
    """A 429 that would outlast the stage is the stage stopping (OutOfTime),
    never an API error a caller would read as an unreadable document."""
    sleeps: list[float] = []
    client = _limited_client(monkeypatch, sleeps)

    with pytest.raises(draft_budget.OutOfTime):
        draft_budget.Clock().run("reading", 5.0, client.get, "/matters/x/documents/files")
    assert sleeps == []


def test_a_rate_limit_out_of_attempts_is_still_an_api_error(monkeypatch) -> None:
    """Outside a render there is no deadline: the attempts run out as before."""
    from smokeball_connector.client import SmokeballApiError

    sleeps: list[float] = []
    client = _limited_client(monkeypatch, sleeps)

    with pytest.raises(SmokeballApiError):
        client.get("/matters/x/documents/files")
    assert len(sleeps) == 5


def test_a_rate_limit_on_a_document_is_out_of_time_never_an_unreadable_record(monkeypatch) -> None:
    """Review of #4223 (the reviewer's probe): the budget-cut 429 used to land
    the file in ``unextractable`` and refuse the draft as source_unextractable."""
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", "/nonexistent/customer.yaml")
    limited = _limited_client(monkeypatch, [])

    class Matter:
        def get(self, _path: str, **_params: Any) -> Any:
            return {"value": [{"id": "f1", "name": "Intake.pdf", "fileExtension": ".pdf", "versionId": "v1"}]}

        def download_file(self, matter_id: str, file_id: str) -> Any:
            return limited.get(f"/matters/{matter_id}/documents/files/{file_id}")

    with pytest.raises(draft_budget.OutOfTime) as stopped:
        draft_budget.Clock().run(draft_budget.READING, 10.0, draft_sources.collect, Matter(), "m-212")
    assert stopped.value.stage == draft_budget.READING


def test_a_rate_limited_render_is_out_of_time(monkeypatch, short_budget) -> None:
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", "/nonexistent/customer.yaml")
    limited = _limited_client(monkeypatch, [])
    monkeypatch.setattr(server, "_get_client", lambda: limited)

    out = server.render_docx_draft("m-213", "Response Draft", DRAFT)

    assert (out["reason"], out["outOfTime"]) == ("out_of_time", "reading the matter's documents")
    assert out["fileId"] is None


def test_a_rate_limited_list_count_is_out_of_time_not_an_unread_folder(monkeypatch, short_budget) -> None:
    limited = _limited_client(monkeypatch, [])
    monkeypatch.setattr(server, "_get_client", lambda: limited)

    out = server.render_docx_draft("m-214", "Production List", "# LIST\n\n1. Intake\n", None, None, None, "fold-4")

    assert (out["reason"], out["outOfTime"]) == ("out_of_time", "counting the listed folder")


def test_the_readback_stops_waiting_at_the_deadline(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(readback, "SLEEP", sleeps.append)

    def unreadable() -> Any:
        raise RuntimeError("not there yet")

    value, done, error = draft_budget.Clock().run("filing", 1.0, readback.poll, unreadable, bool)

    assert (value, done) == (None, False)
    assert sleeps == [], "the first pause (2 s) would cross a 1 s deadline"
    assert error


class _SlowMatter:
    """A matter of plain-text documents, each download taking ``delay``."""

    def __init__(self, count: int, *, delay: float = 0.0, versioned: bool = True) -> None:
        self.delay = delay
        self.downloads: list[str] = []
        self.entries = [
            {
                "id": f"doc-{n}",
                "name": f"Note {n}",
                "fileExtension": ".txt",
                **({"versionId": f"v-{n}-a"} if versioned else {}),
            }
            for n in range(count)
        ]

    def get(self, _path: str, **_params: Any) -> Any:
        return {"value": self.entries}

    def download_file(self, _matter_id: str, file_id: str) -> tuple[dict[str, Any], bytes]:
        time.sleep(self.delay)
        self.downloads.append(file_id)
        if file_id == "doc-bad":
            return {}, b"\x00\x01binary"
        return {}, f"Plain record text of {file_id}.".encode()


def test_collection_stops_at_its_deadline_and_does_not_run_on(monkeypatch) -> None:
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", "/nonexistent/customer.yaml")
    matter = _SlowMatter(40, delay=0.05)

    with pytest.raises(draft_budget.OutOfTime):
        draft_budget.Clock().run("reading", 0.3, draft_sources.collect, matter, "m-205")
    time.sleep(0.3)
    stopped_at = len(matter.downloads)
    time.sleep(0.3)

    assert stopped_at < 40
    assert len(matter.downloads) == stopped_at, "the abandoned read kept downloading"


def test_a_versioned_document_is_read_once_and_a_new_version_again(monkeypatch) -> None:
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", "/nonexistent/customer.yaml")
    matter = _SlowMatter(3)

    first = draft_sources.collect(matter, "m-206")
    second = draft_sources.collect(matter, "m-206")
    matter.entries[0]["versionId"] = "v-0-b"
    draft_sources.collect(matter, "m-206")

    assert first == second
    assert [name for name, _ in first[0]] == ["Note 0", "Note 1", "Note 2"]
    assert matter.downloads == ["doc-0", "doc-1", "doc-2", "doc-0"]


def test_an_unversioned_or_refusing_document_is_read_every_time(monkeypatch) -> None:
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", "/nonexistent/customer.yaml")
    matter = _SlowMatter(1, versioned=False)
    matter.entries.append({"id": "doc-bad", "name": "Scan", "fileExtension": "", "versionId": "v-bad"})

    _s, _v, unextractable = draft_sources.collect(matter, "m-207")
    draft_sources.collect(matter, "m-207")

    assert unextractable == ["Scan"]
    assert matter.downloads == ["doc-0", "doc-bad", "doc-0", "doc-bad"]


def test_a_machine_transcription_is_never_kept(monkeypatch) -> None:
    """Its source is the on-disk vision cache, which can be purged; a purge
    must show on the next render, not after a restart."""
    from smokeball_connector import extract

    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", "/nonexistent/customer.yaml")
    monkeypatch.setattr(
        extract,
        "extract_text_ex",
        lambda *_a, **_k: extract.ExtractResult("[p.1] (machine transcription) text", extract.METHOD_VISION_CACHED),
    )
    matter = _SlowMatter(1)

    draft_sources.collect(matter, "m-211")
    _s, vision, _u = draft_sources.collect(matter, "m-211")

    assert [name for name, _ in vision] == ["Note 0"]
    assert matter.downloads == ["doc-0", "doc-0"]


def test_the_kept_readings_stay_under_their_bound(monkeypatch) -> None:
    monkeypatch.setattr(draft_sources, "MEMO_MAX_CHARS", 100)
    memo = draft_sources._Memo()
    for n in range(10):
        memo.put(("m", f"f{n}", "v"), draft_sources.SOURCE, "x" * 30)
    memo.put(("m", "f9", "v"), draft_sources.SOURCE, "y" * 40)

    assert memo.chars == sum(len(t) for _k, t in memo.rows.values()) <= 100
    assert memo.get(("m", "f0", "v")) is None, "the oldest went first"
    assert memo.get(("m", "f9", "v")) == (draft_sources.SOURCE, "y" * 40)
