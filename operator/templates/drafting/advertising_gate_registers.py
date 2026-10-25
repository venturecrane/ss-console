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


def normalize_registers(raw: Any) -> dict:
    """Validate the registers and return them with every optional key filled.

    Raises :class:`RegistersError` on anything malformed, so a typo never
    reads as an empty register that happens to pass.
    """
    if not isinstance(raw, dict):
        raise RegistersError("the registers are not a mapping")
    if raw.get("schema") != 1:
        raise RegistersError("schema must be 1")
    firm_raw = raw.get("firm")
    if not isinstance(firm_raw, dict):
        raise RegistersError("firm must be a mapping")
    firm = {
        k: [s for s in _list_of(firm_raw.get(k), str, f"firm.{k}") if s.strip()]
        for k in ("names", "office_cities", "site_urls", "phones")
    }
    roster = []
    for i, row in enumerate(_list_of(raw.get("roster"), dict, "roster")):
        name = _opt_str(row.get("name"), f"roster[{i}].name")
        if not name:
            raise RegistersError(f"roster[{i}].name is required")
        lawyer = row.get("lawyer")
        if not isinstance(lawyer, bool):
            raise RegistersError(f"roster[{i}].lawyer must be true or false")
        roster.append(
            {
                "name": name,
                "title": _opt_str(row.get("title"), f"roster[{i}].title"),
                "lawyer": lawyer,
                "languages": [s.lower() for s in _list_of(row.get("languages"), str, f"roster[{i}].languages")],
            }
        )
    disc_raw = _mapping(raw.get("disclosures"), "disclosures")
    unknown = set(disc_raw) - set(_DISCLOSURE_KEYS)
    if unknown:
        raise RegistersError(f"disclosures has unknown keys {sorted(unknown)}")
    disclosures: dict[str, dict[str, str | None] | None] = {}
    for key in _DISCLOSURE_KEYS:
        entry = disc_raw.get(key)
        if entry is None:
            disclosures[key] = None
            continue
        entry = _mapping(entry, f"disclosures.{key}")
        disclosures[key] = {lang: _opt_str(entry.get(lang), f"disclosures.{key}.{lang}") for lang in ("en", "es")}
    results = []
    for i, row in enumerate(_list_of(raw.get("results"), dict, "results")):
        consent = row.get("consent")
        if not isinstance(consent, bool):
            raise RegistersError(f"results[{i}].consent must be true or false")
        results.append(
            {
                "id": str(row.get("id") or f"results[{i}]"),
                "text_match": [
                    s for s in _list_of(row.get("text_match"), str, f"results[{i}].text_match") if s.strip()
                ],
                "liability_basis": _opt_str(row.get("liability_basis"), f"results[{i}].liability_basis"),
                "injury": _opt_str(row.get("injury"), f"results[{i}].injury"),
                "consent": consent,
            }
        )
    testimonials = []
    for i, row in enumerate(_list_of(raw.get("testimonials"), dict, "testimonials")):
        text = _opt_str(row.get("text"), f"testimonials[{i}].text")
        consent = row.get("consent")
        if not text or not isinstance(consent, bool):
            raise RegistersError(f"testimonials[{i}] needs text and consent")
        testimonials.append({"text": text, "consent": consent})
    awards = []
    for i, row in enumerate(_list_of(raw.get("awards"), dict, "awards")):
        name = _opt_str(row.get("name"), f"awards[{i}].name")
        if not name:
            raise RegistersError(f"awards[{i}].name is required")
        awards.append(
            {
                "name": name,
                "ok": row.get("not_membership_based") is True and row.get("no_fee") is True,
            }
        )
    certifications = []
    for i, row in enumerate(_list_of(raw.get("certifications"), dict, "certifications")):
        certifications.append(
            {
                "lawyer": _opt_str(row.get("lawyer"), f"certifications[{i}].lawyer") or "",
                "field": _opt_str(row.get("field"), f"certifications[{i}].field") or "",
                "body": _opt_str(row.get("body"), f"certifications[{i}].body") or "",
                "expires": str(row.get("expires") or ""),
            }
        )
    fee_raw = _mapping(raw.get("fee_terms"), "fee_terms")
    percentages = []
    for p in _list_of(fee_raw.get("percentages"), object, "fee_terms.percentages"):
        if isinstance(p, bool) or not isinstance(p, (int, float, str)):
            raise RegistersError("fee_terms.percentages must be numbers")
        try:
            percentages.append(float(str(p).rstrip("%")))
        except ValueError as exc:
            raise RegistersError("fee_terms.percentages must be numbers") from exc
    no_fee = fee_raw.get("no_fee_phrases_allowed")
    if no_fee is not None and not isinstance(no_fee, bool):
        raise RegistersError("fee_terms.no_fee_phrases_allowed must be true or false")
    rr = _mapping(raw.get("review_reply"), "review_reply")
    contact = _mapping(rr.get("offline_contact"), "review_reply.offline_contact")
    channels_raw = _mapping(raw.get("channels"), "channels")
    channels = {}
    for ch, cfg in channels_raw.items():
        if ch not in CHANNELS:
            raise RegistersError(f"channels names an unknown channel {ch!r}")
        cfg = _mapping(cfg, f"channels.{ch}")
        flag = cfg.get("identity_in_template", False)
        if not isinstance(flag, bool):
            raise RegistersError(f"channels.{ch}.identity_in_template must be true or false")
        channels[ch] = {"identity_in_template": flag}
    return {
        "firm": firm,
        "roster": roster,
        "disclosures": disclosures,
        "results": results,
        "testimonials": testimonials,
        "awards": awards,
        "certifications": certifications,
        "fee_terms": {"percentages": percentages, "no_fee_phrases_allowed": no_fee is True},
        "blocklist": [s for s in _list_of(raw.get("blocklist"), str, "blocklist") if s.strip()],
        "offline_contact": {
            "name": _opt_str(contact.get("name"), "offline_contact.name"),
            "phone": _opt_str(contact.get("phone"), "offline_contact.phone"),
            "email": _opt_str(contact.get("email"), "offline_contact.email"),
        },
        "channels": channels,
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
