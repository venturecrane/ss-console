"""The job envelope: everything about ONE requested chronology package that is
not firm posture. Authored by the requesting skill (slice 7) or the Captain-side
skill today, read by the driver, never inferred.

The envelope is where the incident date lives. The pipeline used to take it
three ways (required env, optional env with a silent pre-incident defect, and
argv); the driver supplies it uniformly from here, with its source recorded so
the limitations section can say where the date came from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DOB_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
INCIDENT_SOURCES = {"matter_layout", "intake_document", "administrator_request", "record_citation"}


class JobError(ValueError):
    """The envelope is missing something the run needs. Never filled in."""


@dataclass(frozen=True)
class Unit:
    unit: str  # the slug the pipeline uses for this client
    client_name: str  # rendered into the document title and folder name
    name_token: str  # matches folder/file names
    surname: str
    dob: str  # MM/DD/YYYY, the identity check's anchor
    folder_prefix: str | None = None  # joint matters: this client's folder


@dataclass(frozen=True)
class Job:
    path: Path
    slug: str
    matter_number: str
    matter_id: str
    matter_title: str
    units: list[Unit]
    incident_date: str  # YYYY-MM-DD
    incident_source: str  # one of INCIDENT_SOURCES
    injuries: str  # the plain-words injury list the filter stage reads
    cap_usd: float | None  # None -> firm default
    data_root: Path
    # Where the install-level artifacts live: the scanned-page classifier's
    # authored control pages (`controls/controls.json` + PDFs) and the vendored
    # ICD tables (`controls/icd/`). On a laptop it IS data_root (one tree per
    # firm, controls beside the matters) and the icd_tables stage fetches the
    # tables into it once. On a seat every job gets a fresh data_root under
    # jobs/<id>/, so the controls would never be there: the daemon points this
    # at the run dir, which the entrypoint pre-seeds from the firm's vault on
    # every boot as a root-owned, read-only tree (provision-customer.sh stages
    # the controls AND the console-vendored ICD tables into that vault). A run
    # never writes here on a seat, and never a matter's bytes anywhere.
    install_root: Path
    allowance_remaining_documents: int | None = None
    # The month's state as the broker read it, stamped fresh before every run
    # and every resume (the daemon re-fetches; the envelope's own copy goes
    # stale the moment another job records cents). `allowance_month` is the
    # month those counts belong to, so a run that spans midnight on the last
    # of the month can say which month it metered against.
    allowance_pages: int | None = None
    allowance_remaining_pages: int | None = None
    month_pages_used: int | None = None
    month_cents_used: int | None = None
    allowance_month: str | None = None
    selection_overrides: dict[str, Any] = field(default_factory=dict)
    requested_by: str | None = None
    request_ref: str | None = None

    @property
    def joint(self) -> bool:
        return len(self.units) > 1

    def unit(self, name: str) -> Unit:
        for u in self.units:
            if u.unit == name:
                return u
        raise JobError(f"unit {name!r} is not on this job ({[u.unit for u in self.units]})")


def _req(d: dict[str, Any], key: str, where: str) -> Any:
    if key not in d or d[key] in (None, ""):
        raise JobError(f"{where}.{key}: required")
    return d[key]


def _opt_nonneg_int(d: dict[str, Any], key: str) -> int | None:
    """An optional count the broker stamped. Absent is None (a laptop run);
    present and malformed is a refusal, never a coerced zero, because a zero
    remainder and an unknown remainder mean opposite things to the limits."""
    value = d.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise JobError(f"job.{key}: must be a non-negative integer")
    return value


def parse(data: Any, *, path: Path) -> Job:
    if not isinstance(data, dict):
        raise JobError(f"{path}: top level must be a map")
    matter = _req(data, "matter", "job")
    if not isinstance(matter, dict):
        raise JobError("job.matter: must be a map")
    incident = _req(data, "incident", "job")
    if not isinstance(incident, dict):
        raise JobError("job.incident: must be a map")
    date = str(_req(incident, "date", "job.incident"))
    if not DATE_RE.match(date):
        raise JobError("job.incident.date: expected YYYY-MM-DD")
    source = str(_req(incident, "source", "job.incident"))
    if source not in INCIDENT_SOURCES:
        raise JobError(f"job.incident.source: expected one of {sorted(INCIDENT_SOURCES)}")
    raw_units = _req(data, "units", "job")
    if not isinstance(raw_units, list) or not raw_units:
        raise JobError("job.units: at least one unit is required")
    units: list[Unit] = []
    for i, u in enumerate(raw_units):
        where = f"job.units[{i}]"
        if not isinstance(u, dict):
            raise JobError(f"{where}: must be a map")
        dob = str(_req(u, "dob", where))
        if not DOB_RE.match(dob):
            raise JobError(f"{where}.dob: expected MM/DD/YYYY")
        units.append(
            Unit(
                unit=str(_req(u, "unit", where)),
                client_name=str(_req(u, "client_name", where)),
                name_token=str(_req(u, "name_token", where)),
                surname=str(_req(u, "surname", where)),
                dob=dob,
                folder_prefix=u.get("folder_prefix"),
            )
        )
    if len({u.unit for u in units}) != len(units):
        raise JobError("job.units: unit slugs must be unique")
    if len(units) > 1 and any(not u.folder_prefix for u in units):
        raise JobError("job.units: a joint matter needs folder_prefix on every unit")
    cap = data.get("cap_usd")
    if cap is not None and (not isinstance(cap, (int, float)) or cap <= 0):
        raise JobError("job.cap_usd: must be a positive number when present")
    allowance = _opt_nonneg_int(data, "allowance_remaining_documents")
    month = data.get("allowance_month")
    if month is not None and not (isinstance(month, str) and MONTH_RE.match(month)):
        raise JobError("job.allowance_month: expected YYYY-MM")
    data_root = data.get("data_root")
    if not data_root:
        raise JobError("job.data_root: required (the durable data root outside any repo)")
    install_root = data.get("install_root") or str(data_root)
    if not isinstance(install_root, str):
        raise JobError("job.install_root: must be a path when present")
    return Job(
        path=path,
        slug=str(_req(data, "slug", "job")),
        matter_number=str(_req(matter, "number", "job.matter")),
        matter_id=str(_req(matter, "id", "job.matter")),
        matter_title=str(matter.get("title") or ""),
        units=units,
        incident_date=date,
        incident_source=source,
        injuries=str(data.get("injuries") or ""),
        cap_usd=float(cap) if cap is not None else None,
        data_root=Path(str(data_root)).expanduser(),
        install_root=Path(install_root).expanduser(),
        allowance_remaining_documents=allowance,
        allowance_pages=_opt_nonneg_int(data, "allowance_pages"),
        allowance_remaining_pages=_opt_nonneg_int(data, "allowance_remaining_pages"),
        month_pages_used=_opt_nonneg_int(data, "month_pages_used"),
        month_cents_used=_opt_nonneg_int(data, "month_cents_used"),
        allowance_month=str(month) if month is not None else None,
        selection_overrides=dict(data.get("selection") or {}),
        requested_by=data.get("requested_by"),
        request_ref=data.get("request_ref"),
    )


def load(job_dir: Path) -> Job:
    path = Path(job_dir) / "job.yaml"
    if not path.is_file():
        raise JobError(f"{path}: not found")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise JobError(f"{path}: not valid YAML ({exc})") from exc
    return parse(data, path=path)
