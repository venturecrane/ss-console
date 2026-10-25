"""youtube-public: conformance, the stdio round-trip, and the two reads against
a saved feed fixture (no network)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import anyio
import pytest
from mcp import Client, StdioServerParameters
from operator_connector_sdk import conformance
from operator_connector_sdk.manifest import AuthModel, ConnectorManifest
from youtube_public_connector import feed, server

ROOT = Path(__file__).resolve().parents[1]
FEED = (ROOT / "tests" / "fixtures" / "channel_feed.xml").read_bytes()
CHANNEL = "UCAAAAAAAAAAAAAAAAAAAAAA"
_SCRIPT = shutil.which("youtube-public-mcp")


def _manifest() -> ConnectorManifest:
    return ConnectorManifest.from_toml(ROOT / "manifest.toml")


def test_manifest_has_no_secrets_and_only_reads() -> None:
    m = _manifest()
    assert m.name == "youtube-public"
    assert m.capability == "VideoChannel"
    assert m.auth_model is AuthModel.STATIC
    assert m.required_secrets == []
    assert set(m.tool_classes.values()) == {"read"}


def test_conformance() -> None:
    runtime_map = conformance.run_all(server.server, _manifest())
    assert runtime_map == {
        "mcp_youtube_public_list_channel_videos": "read",
        "mcp_youtube_public_get_video": "read",
    }


@pytest.fixture
def fake_http(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    def _get(url: str) -> bytes:
        seen.append(url)
        if url.startswith(feed.FEED_URL):
            return FEED
        return json.dumps(
            {
                "title": "Episode 41: What to do after a freeway crash",
                "author_name": "Example Law Radio",
                "author_url": "https://www.youtube.com/@examplelawradio",
                "thumbnail_url": "https://i.ytimg.com/vi/aaaaaaaaaaa/hqdefault.jpg",
            }
        ).encode()

    monkeypatch.setattr(feed, "http_get", _get)
    return seen


def test_list_channel_videos_parses_the_feed(fake_http: list[str]) -> None:
    out = server.list_channel_videos(CHANNEL)
    assert out["channel_title"] == "Example Law Radio"
    assert out["count"] == 2
    first = out["videos"][0]
    assert first["video_id"] == "aaaaaaaaaaa"
    assert first["url"] == "https://www.youtube.com/watch?v=aaaaaaaaaaa"
    assert first["published"].startswith("2026-10-18")
    assert first["thumbnail"].endswith("hqdefault.jpg")
    assert "freeway collisions" in first["description"]
    assert out["videos"][1]["title"] == "Episode 40: Dog bites & homeowner insurance"
    assert fake_http == [f"{feed.FEED_URL}?channel_id={CHANNEL}"]


def test_since_filters_by_published_date(fake_http: list[str]) -> None:
    out = server.list_channel_videos(CHANNEL, since="2026-10-10")
    assert [v["video_id"] for v in out["videos"]] == ["aaaaaaaaaaa"]
    with pytest.raises(ValueError):
        server.list_channel_videos(CHANNEL, since="last week")


def test_get_video_with_and_without_channel(fake_http: list[str]) -> None:
    bare = server.get_video("aaaaaaaaaaa")
    assert bare["title"].startswith("Episode 41")
    assert bare["description"] is None
    full = server.get_video("aaaaaaaaaaa", channel_id=CHANNEL)
    assert full["published"].startswith("2026-10-18")
    assert "freeway collisions" in full["description"]


@pytest.mark.parametrize(
    "bad", ["", "UCshort", "PLAAAAAAAAAAAAAAAAAAAAAA", "UCAAAAAAAAAAAAAAAAAAAAA/", "../etc/passwd"]
)
def test_bad_channel_ids_never_reach_a_url(bad: str, fake_http: list[str]) -> None:
    with pytest.raises(ValueError):
        server.list_channel_videos(bad)
    assert fake_http == []


def test_bad_video_id_refused(fake_http: list[str]) -> None:
    with pytest.raises(ValueError):
        server.get_video("abc")
    assert fake_http == []


def test_http_get_only_fetches_the_two_endpoints() -> None:
    for url in ("file:///etc/passwd", "https://evil.example/feeds/videos.xml?x", "https://www.youtube.com/watch?v=x"):
        with pytest.raises(feed.FeedError):
            feed.http_get(url)


def test_malformed_feed_is_an_error() -> None:
    with pytest.raises(feed.FeedError):
        feed.parse_feed(b"<feed><entry>")


def test_entity_bomb_is_refused() -> None:
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa">'
        b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;"><!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">'
        b'<!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;"><!ENTITY e "&d;&d;&d;&d;&d;&d;&d;&d;&d;&d;">'
        b'<!ENTITY f "&e;&e;&e;&e;&e;&e;&e;&e;&e;&e;"><!ENTITY g "&f;&f;&f;&f;&f;&f;&f;&f;&f;&f;">]>'
        b"<feed><title>&g;</title></feed>"
    )
    with pytest.raises(feed.FeedError):
        feed.parse_feed(bomb)


@pytest.mark.skipif(_SCRIPT is None, reason="youtube-public-mcp console-script not on PATH")
def test_stdio_serves_over_console_script() -> None:
    async def _roundtrip() -> None:
        async with Client(StdioServerParameters(command=_SCRIPT)) as client:
            listed = await client.list_tools()
            assert {t.name for t in listed.tools} == {"list_channel_videos", "get_video"}

    anyio.run(_roundtrip)
