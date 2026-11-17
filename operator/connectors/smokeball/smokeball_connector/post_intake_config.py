"""Where combined post intake files a letter, read off the seat's own
customer.yaml at write time, the ``vendor_invoice_intake`` shape: the firm
authors the folder, the tool resolves it, and the model never supplies an id.

    combined_post_intake:
      medical_folder: Medical   # medical records and medical bills
      # or an ordered list, the first one the matter has wins:
      medical_folder: [MEDICAL, MEDICAL RECORDS]

A LIST, because a firm's matters do not all carry the same folder name (A&P,
2026-11-17: 2,976 of 4,255 matters have "MEDICAL", 114 have "MEDICAL
RECORDS", 10 have both). The firm's written rule named "MEDICAL RECORDS", so
a single name sent the medical bills of most matters to the root.

Unauthored (no file, no block, no key) means the matter root and nothing said
about it: no folder is imposed on a firm that named none. A block that will not
parse is reported on the filing as such, and the letter still files at the
root, because the firm's paper reaching its matter matters more than which
folder it lands in."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .library import CUSTOMER_YAML_ENV, DEFAULT_CUSTOMER_YAML
from . import seat_config

CONFIG_BLOCK = "combined_post_intake"
#: The kinds of letter a filing can name, and the config key holding its folder.
KIND_FOLDERS = {"medical": "medical_folder"}
KINDS = frozenset({"letter", *KIND_FOLDERS})


@dataclass(frozen=True)
class PostIntakeConfig:
    #: Per kind, the folder names to try in order; the first the matter has wins.
    folders: dict[str, tuple[str, ...]]
    error: str | None = None


def parse_post_intake_config(block: Any) -> PostIntakeConfig:
    if block is None:
        return PostIntakeConfig(folders={})
    if not isinstance(block, dict):
        return PostIntakeConfig(folders={}, error=f"{CONFIG_BLOCK} must be a mapping")
    folders: dict[str, tuple[str, ...]] = {}
    for kind, key in KIND_FOLDERS.items():
        value = block.get(key)
        if value is None:
            continue
        names = _folder_names(value)
        if not names:
            return PostIntakeConfig(
                folders={}, error=f"{CONFIG_BLOCK}.{key} must be a folder name or a list of folder names"
            )
        folders[kind] = names
    return PostIntakeConfig(folders=folders)


def _folder_names(value: Any) -> tuple[str, ...]:
    """The non-empty folder names ``value`` holds, in order, or () when it is not
    a name or a list of names (any non-string entry refuses the whole list)."""
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not items:
        return ()
    if not all(isinstance(v, str) and v.strip() for v in items):
        return ()
    return tuple(v.strip() for v in items)


def load_post_intake_config(path: str | None = None) -> PostIntakeConfig:
    path = path or os.environ.get(CUSTOMER_YAML_ENV) or DEFAULT_CUSTOMER_YAML
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        return PostIntakeConfig(folders={})
    try:
        data = seat_config.parse(raw) or {}
    except Exception as exc:  # noqa: BLE001 - an unparseable config is reported on the filing, never raised
        return PostIntakeConfig(folders={}, error=f"customer.yaml not parseable: {exc.__class__.__name__}")
    return parse_post_intake_config(data.get(CONFIG_BLOCK) if isinstance(data, dict) else None)


__all__ = ["CONFIG_BLOCK", "KINDS", "PostIntakeConfig", "load_post_intake_config", "parse_post_intake_config"]
