"""The firm's registers for the advertising gate (ADR 0098): schema 1, validated
fail-closed. The schema is in ``advertising_gate_check.py``'s docstring.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any


def _sibling(name: str) -> Any:
    """Load a sibling module of the advertising gate by path, once per directory.

    The gate is run as a script (``python3 advertising_gate_check.py``) and
    loaded by path from the overlay, so its siblings cannot rely on sys.path.
    """
    here = Path(__file__).resolve().parent
    key = "_smd_adgate_" + re.sub(r"\W", "_", str(here)) + "_" + name
    module = sys.modules.get(key)
    if module is None:
        spec = importlib.util.spec_from_file_location(key, here / f"{name}.py")
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return module


_core = _sibling("advertising_gate_core")
CHANNELS = _core.CHANNELS
_DISCLOSURE_KEYS = _core._DISCLOSURE_KEYS


# ---------------------------------------------------------------------------
# Registers
# ---------------------------------------------------------------------------


class RegistersError(Exception):
    """The registers are missing or malformed. The gate fails closed on it."""


def _list_of(value: Any, kind: type, where: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, kind) for v in value):
        raise RegistersError(f"{where} must be a list of {kind.__name__}")
    return value


def _mapping(value: Any, where: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RegistersError(f"{where} must be a mapping")
    return value


def _opt_str(value: Any, where: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RegistersError(f"{where} must be a string or null")
    return value.strip() or None


def _strings(value: Any, where: str) -> list[str]:
    return [s for s in _list_of(value, str, where) if s.strip()]


def _bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise RegistersError(f"{where} must be true or false")
    return value


def _firm(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise RegistersError("firm must be a mapping")
    return {k: _strings(raw.get(k), f"firm.{k}") for k in ("names", "office_cities", "site_urls", "phones")}


def _roster(raw: Any) -> list[dict]:
    roster = []
    for i, row in enumerate(_list_of(raw, dict, "roster")):
        name = _opt_str(row.get("name"), f"roster[{i}].name")
        if not name:
            raise RegistersError(f"roster[{i}].name is required")
        roster.append(
            {
                "name": name,
                "title": _opt_str(row.get("title"), f"roster[{i}].title"),
                "lawyer": _bool(row.get("lawyer"), f"roster[{i}].lawyer"),
                "languages": [s.lower() for s in _list_of(row.get("languages"), str, f"roster[{i}].languages")],
            }
        )
    return roster


def _disclosures(raw: Any) -> dict[str, dict[str, str | None] | None]:
    disc_raw = _mapping(raw, "disclosures")
    unknown = set(disc_raw) - set(_DISCLOSURE_KEYS)
    if unknown:
        raise RegistersError(f"disclosures has unknown keys {sorted(unknown)}")
    out: dict[str, dict[str, str | None] | None] = {}
    for key in _DISCLOSURE_KEYS:
        entry = disc_raw.get(key)
        if entry is None:
            out[key] = None
            continue
        entry = _mapping(entry, f"disclosures.{key}")
        out[key] = {lang: _opt_str(entry.get(lang), f"disclosures.{key}.{lang}") for lang in ("en", "es")}
    return out


def _results(raw: Any) -> list[dict]:
    return [
        {
            "id": str(row.get("id") or f"results[{i}]"),
            "text_match": _strings(row.get("text_match"), f"results[{i}].text_match"),
            "liability_basis": _opt_str(row.get("liability_basis"), f"results[{i}].liability_basis"),
            "injury": _opt_str(row.get("injury"), f"results[{i}].injury"),
            "consent": _bool(row.get("consent"), f"results[{i}].consent"),
        }
        for i, row in enumerate(_list_of(raw, dict, "results"))
    ]


def _testimonials(raw: Any) -> list[dict]:
    out = []
    for i, row in enumerate(_list_of(raw, dict, "testimonials")):
        text = _opt_str(row.get("text"), f"testimonials[{i}].text")
        if not text:
            raise RegistersError(f"testimonials[{i}] needs text and consent")
        out.append({"text": text, "consent": _bool(row.get("consent"), f"testimonials[{i}].consent")})
    return out


def _awards(raw: Any) -> list[dict]:
    out = []
    for i, row in enumerate(_list_of(raw, dict, "awards")):
        name = _opt_str(row.get("name"), f"awards[{i}].name")
        if not name:
            raise RegistersError(f"awards[{i}].name is required")
        out.append({"name": name, "ok": row.get("not_membership_based") is True and row.get("no_fee") is True})
    return out


def _certifications(raw: Any) -> list[dict]:
    return [
        {
            "lawyer": _opt_str(row.get("lawyer"), f"certifications[{i}].lawyer") or "",
            "field": _opt_str(row.get("field"), f"certifications[{i}].field") or "",
            "body": _opt_str(row.get("body"), f"certifications[{i}].body") or "",
            "expires": str(row.get("expires") or ""),
        }
        for i, row in enumerate(_list_of(raw, dict, "certifications"))
    ]


def _fee_terms(raw: Any) -> dict:
    fee_raw = _mapping(raw, "fee_terms")
    percentages = []
    for p in _list_of(fee_raw.get("percentages"), object, "fee_terms.percentages"):
        if isinstance(p, bool) or not isinstance(p, (int, float, str)):
            raise RegistersError("fee_terms.percentages must be numbers")
        try:
            percentages.append(float(str(p).rstrip("%")))
        except ValueError as exc:
            raise RegistersError("fee_terms.percentages must be numbers") from exc
    no_fee = fee_raw.get("no_fee_phrases_allowed")
    if no_fee is not None:
        _bool(no_fee, "fee_terms.no_fee_phrases_allowed")
    return {"percentages": percentages, "no_fee_phrases_allowed": no_fee is True}


def _offline_contact(raw: Any) -> dict:
    rr = _mapping(raw, "review_reply")
    contact = _mapping(rr.get("offline_contact"), "review_reply.offline_contact")
    return {k: _opt_str(contact.get(k), f"offline_contact.{k}") for k in ("name", "phone", "email")}


def _channels(raw: Any) -> dict:
    channels = {}
    for ch, cfg in _mapping(raw, "channels").items():
        if ch not in CHANNELS:
            raise RegistersError(f"channels names an unknown channel {ch!r}")
        cfg = _mapping(cfg, f"channels.{ch}")
        flag = _bool(cfg.get("identity_in_template", False), f"channels.{ch}.identity_in_template")
        channels[ch] = {"identity_in_template": flag}
    return channels


def normalize_registers(raw: Any) -> dict:
    """Validate the registers and return them with every optional key filled.

    Raises :class:`RegistersError` on anything malformed, so a typo never
    reads as an empty register that happens to pass.
    """
    if not isinstance(raw, dict):
        raise RegistersError("the registers are not a mapping")
    if raw.get("schema") != 1:
        raise RegistersError("schema must be 1")
    return {
        "firm": _firm(raw.get("firm")),
        "roster": _roster(raw.get("roster")),
        "disclosures": _disclosures(raw.get("disclosures")),
        "results": _results(raw.get("results")),
        "testimonials": _testimonials(raw.get("testimonials")),
        "awards": _awards(raw.get("awards")),
        "certifications": _certifications(raw.get("certifications")),
        "fee_terms": _fee_terms(raw.get("fee_terms")),
        "blocklist": _strings(raw.get("blocklist"), "blocklist"),
        "offline_contact": _offline_contact(raw.get("review_reply")),
        "channels": _channels(raw.get("channels")),
    }


def load_registers(path: str | Path) -> tuple[dict | None, str | None]:
    """``(parsed registers, None)`` or ``(None, why they are unavailable)``.

    JSON always; YAML only when PyYAML imports. Never raises.
    """
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - reported, then fail closed
        return None, f"cannot read {path} ({exc.__class__.__name__})"
    try:
        return json.loads(text), None
    except ValueError:
        pass
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return None, f"{path} is not JSON and no YAML reader is installed"
    try:
        return yaml.safe_load(text), None
    except Exception as exc:  # noqa: BLE001 - an unparseable file is reported and fails closed
        return None, f"{path} does not parse ({exc.__class__.__name__})"
