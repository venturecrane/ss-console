"""Fetch and parse YouTube's public channel feed and oEmbed record.

Both endpoints are public and unauthenticated:

* ``https://www.youtube.com/feeds/videos.xml?channel_id=<UC...>``: an Atom feed
  of the channel's most recent uploads (YouTube serves the latest 15).
* ``https://www.youtube.com/oembed?url=<watch url>&format=json``: one video's
  title, channel name and thumbnail.

Only those two fixed hosts and paths are ever fetched, the ids are validated by
shape before they reach a URL, and responses are size-capped. Parsing uses the
defusedxml, which refuses DTDs, entity expansion and external entities outright.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from defusedxml import DefusedXmlException
from defusedxml import ElementTree as SafeET
from typing import Any

FEED_URL = "https://www.youtube.com/feeds/videos.xml"
OEMBED_URL = "https://www.youtube.com/oembed"
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 20
_DESCRIPTION_CHARS = 2000

CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


def _https_only_opener() -> urllib.request.OpenerDirector:
    """An opener that speaks https and nothing else: no file://, no ftp://, and
    no redirect handler, so a 30x is an error rather than a hop to another host."""
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.HTTPSHandler(),
        urllib.request.HTTPErrorProcessor(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.UnknownHandler(),
    ):
        opener.add_handler(handler)
    return opener


_HTTPS_ONLY = _https_only_opener()


class FeedError(RuntimeError):
    """The feed could not be fetched or read."""


def validate_channel_id(channel_id: str) -> str:
    cid = (channel_id or "").strip()
    if not CHANNEL_ID_RE.fullmatch(cid):
        raise ValueError(
            "channel_id must be a YouTube channel id: 'UC' followed by 22 letters, digits, '-' or '_' "
            "(find it on the channel's About page, Share channel, Copy channel ID)"
        )
    return cid


def validate_video_id(video_id: str) -> str:
    vid = (video_id or "").strip()
    if not VIDEO_ID_RE.fullmatch(vid):
        raise ValueError("video_id must be an 11-character YouTube video id (the v= value of a watch URL)")
    return vid


def http_get(url: str) -> bytes:
    """GET one of the two fixed https endpoints, size-capped."""
    if not (url.startswith(FEED_URL + "?") or url.startswith(OEMBED_URL + "?")):
        raise FeedError("refusing to fetch a URL other than the YouTube feed or oEmbed endpoint")
    req = urllib.request.Request(url, headers={"User-Agent": "smd-operator-youtube-public/0.1"})  # noqa: S310 - url is one of two fixed https constants, checked on the line above
    try:
        with _HTTPS_ONLY.open(req, timeout=TIMEOUT_SECONDS) as resp:
            blob = resp.read(MAX_BYTES + 1)
    except OSError as exc:
        raise FeedError(f"could not fetch {url.split('?', 1)[0]}: {exc}") from exc
    if len(blob) > MAX_BYTES:
        raise FeedError("response over the size limit")
    return blob


def feed_url(channel_id: str) -> str:
    return f"{FEED_URL}?{urllib.parse.urlencode({'channel_id': validate_channel_id(channel_id)})}"


def oembed_url(video_id: str) -> str:
    watch = f"https://www.youtube.com/watch?v={validate_video_id(video_id)}"
    return f"{OEMBED_URL}?{urllib.parse.urlencode({'url': watch, 'format': 'json'})}"


def _text(el: Any) -> str:
    return (el.text or "").strip() if el is not None else ""


def parse_feed(blob: bytes) -> dict[str, Any]:
    try:
        root = SafeET.fromstring(blob, forbid_dtd=True)
    except (SafeET.ParseError, DefusedXmlException) as exc:
        raise FeedError(f"the channel feed is not readable XML ({exc})") from exc
    channel_title = _text(root.find("atom:title", _NS))
    channel_id = _text(root.find("yt:channelId", _NS))
    videos: list[dict[str, Any]] = []
    for entry in root.findall("atom:entry", _NS):
        group = entry.find("media:group", _NS)
        link = entry.find("atom:link[@rel='alternate']", _NS)
        thumb = group.find("media:thumbnail", _NS) if group is not None else None
        description = _text(group.find("media:description", _NS)) if group is not None else ""
        videos.append(
            {
                "video_id": _text(entry.find("yt:videoId", _NS)),
                "title": _text(entry.find("atom:title", _NS)),
                "published": _text(entry.find("atom:published", _NS)),
                "updated": _text(entry.find("atom:updated", _NS)),
                "url": link.get("href", "") if link is not None else "",
                "description": description[:_DESCRIPTION_CHARS],
                "thumbnail": thumb.get("url", "") if thumb is not None else "",
            }
        )
    return {"channel_id": channel_id, "channel_title": channel_title, "videos": videos}


def parse_oembed(blob: bytes) -> dict[str, Any]:
    try:
        data = json.loads(blob)
    except ValueError as exc:
        raise FeedError("the oEmbed record is not readable JSON") from exc
    if not isinstance(data, dict):
        raise FeedError("the oEmbed record is not an object")
    return {
        "title": str(data.get("title", "")),
        "channel_title": str(data.get("author_name", "")),
        "channel_url": str(data.get("author_url", "")),
        "thumbnail": str(data.get("thumbnail_url", "")),
    }
