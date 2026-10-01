"""Where combined post intake files a letter, read off the seat's own
customer.yaml at write time, the ``vendor_invoice_intake`` shape: the firm
authors the folder, the tool resolves it, and the model never supplies an id.

    combined_post_intake:
      medical_folder: Medical   # medical records and medical bills

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

CONFIG_BLOCK = "combined_post_intake"
#: The kinds of letter a filing can name, and the config key holding its folder.
KIND_FOLDERS = {"medical": "medical_folder"}
KINDS = frozenset({"letter", *KIND_FOLDERS})


@dataclass(frozen=True)
class PostIntakeConfig:
    folders: dict[str, str]
    error: str | None = None


def parse_post_intake_config(block: Any) -> PostIntakeConfig:
    if block is None:
        return PostIntakeConfig(folders={})
    if not isinstance(block, dict):
        return PostIntakeConfig(folders={}, error=f"{CONFIG_BLOCK} must be a mapping")
    folders: dict[str, str] = {}
    for kind, key in KIND_FOLDERS.items():
        value = block.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            return PostIntakeConfig(folders={}, error=f"{CONFIG_BLOCK}.{key} must be a folder name")
        folders[kind] = value.strip()
    return PostIntakeConfig(folders=folders)


def load_post_intake_config(path: str | None = None) -> PostIntakeConfig:
    path = path or os.environ.get(CUSTOMER_YAML_ENV) or DEFAULT_CUSTOMER_YAML
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        return PostIntakeConfig(folders={})
    try:
        import yaml

        data = yaml.safe_load(raw) or {}
    except Exception as exc:  # noqa: BLE001 - an unparseable config is reported on the filing, never raised
        return PostIntakeConfig(folders={}, error=f"customer.yaml not parseable: {exc.__class__.__name__}")
    return parse_post_intake_config(data.get(CONFIG_BLOCK) if isinstance(data, dict) else None)


__all__ = ["CONFIG_BLOCK", "KINDS", "PostIntakeConfig", "load_post_intake_config", "parse_post_intake_config"]
