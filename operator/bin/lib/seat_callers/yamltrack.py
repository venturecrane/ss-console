"""Which customer.yaml keys a site reads, found by running it.

When `STATE.track` is on, a yaml load whose source is the current customer.yaml
input returns a TrackDict: a dict that records the path of every key read
(`get`, `[]`, `in`, iteration) and the type of the value found there, and wraps
nested mappings and lists the same way. The block-missing and block-malformed
inputs are built from those paths (inputs.py), so "the block this site reads"
is an observation, not a reading of the site's code.

Tracking is used only in the probe pass; recorded behaviour always comes from
plain dicts, because a dict subclass is not byte-for-byte a dict (yaml.safe_dump
refuses one).
"""

from __future__ import annotations

from typing import Any

from .sandbox import STATE

LIST = "[]"
ALL = "*"


def _note(path: tuple, value: Any) -> None:
    if STATE.track:
        STATE.accessed.setdefault(path, type(value).__name__)


def _wrap(value: Any, path: tuple) -> Any:
    if isinstance(value, dict) and not isinstance(value, TrackDict):
        return TrackDict(value, path)
    if isinstance(value, list) and not isinstance(value, TrackList):
        return TrackList(value, path)
    return value


class TrackDict(dict):
    def __init__(self, data: dict, path: tuple) -> None:
        super().__init__(data)
        self._path = path

    def _child(self, key: Any, value: Any) -> Any:
        path = (*self._path, key if isinstance(key, str) else repr(key))
        _note(path, value)
        return _wrap(value, path)

    def __getitem__(self, key: Any) -> Any:
        if key not in self.keys():
            _note((*self._path, str(key)), None)
        return self._child(key, super().__getitem__(key))

    def get(self, key: Any, default: Any = None) -> Any:
        if key in self.keys():
            return self._child(key, super().__getitem__(key))
        _note((*self._path, str(key)), None)
        return default

    def __contains__(self, key: object) -> bool:
        found = super().__contains__(key)
        if not found:
            _note((*self._path, str(key)), None)
        return found

    def items(self) -> Any:  # type: ignore[override]
        _note((*self._path, ALL), self)
        return [(k, self._child(k, v)) for k, v in super().items()]

    def values(self) -> Any:  # type: ignore[override]
        return [v for _, v in self.items()]

    def __iter__(self) -> Any:
        _note((*self._path, ALL), self)
        return iter(list(super().keys()))


class TrackList(list):
    def __init__(self, data: list, path: tuple) -> None:
        super().__init__(data)
        self._path = path

    def _item(self, value: Any) -> Any:
        path = (*self._path, LIST)
        _note(path, value)
        return _wrap(value, path)

    def __getitem__(self, index: Any) -> Any:
        value = super().__getitem__(index)
        if isinstance(index, slice):
            return [self._item(v) for v in value]
        return self._item(value)

    def __iter__(self) -> Any:
        return iter([self._item(v) for v in list(super().__iter__())])


def _is_current_input(stream: Any) -> bool:
    if STATE.yaml_target is None:
        return False
    if isinstance(stream, str | bytes):
        text = stream.decode("utf-8", "replace") if isinstance(stream, bytes) else stream
        return STATE.yaml_text is not None and text == STATE.yaml_text
    return getattr(stream, "name", None) == STATE.yaml_target


def install() -> bool:
    """Wrap yaml.load (safe_load and full_load call it). False when PyYAML is absent."""
    try:
        import yaml
    except ImportError:
        return False
    original = yaml.load

    def load(stream: Any, Loader: Any = None, **kwargs: Any) -> Any:
        mine = STATE.track and _is_current_input(stream)
        result = original(stream, Loader, **kwargs) if Loader is not None else original(stream, **kwargs)
        return _wrap(result, ()) if mine else result

    yaml.load = load
    return True
