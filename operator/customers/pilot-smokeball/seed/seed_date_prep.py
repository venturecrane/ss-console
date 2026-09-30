"""A court date inside the date-prep window, and the draft it preps from.
Added 2026-09-28; seeded by ``seed_staging.py`` alongside ``seed_data.py`` and
``seed_keeper_overdue.py`` (its own module because seed_data.py sits at its
module-size baseline).

WHY. The date-prep brief's new wording (#2963: the Done lines say what is
ready; yes-or-no questions; no dashes or ids) could not be proven live: the
only in-window court date on the tenant, 2026-PI-105's Final Status
Conference, was already briefed, and a date is briefed once. This adds one
fresh date on a DIFFERENT matter, 2026-PI-103, whose tenant record has no
overdue task (so the task-list-keeper's review is unchanged by it: a court
date in the window makes every task on its matter ``at_stake``).

The draft exhibit list is what makes a step offerable
(``operator/skills/date-prep-brief/catalog.py``: a witness or exhibit list on
file only as a draft offers ``exhibit_list_finalize``). Standing synthetic seed
data; every name here is fictional.

TIMES ARE LOCAL. Smokeball's ``startTime``/``endTime`` are the firm's wall
clock in ``timeZone``, never UTC (vendor create-event: "date and time will
correlate with the time zone provided"). So every time below is written as the
firm reads it, with no ``Z``: ``2026-10-06T09:30:00`` is 9:30 a.m. in
Los Angeles. (Until 2026-09-29 these were posted UTC-shifted with a ``Z``, and
Smokeball showed the firm 4:30 p.m. for the 9:30 a.m. hearing.) A test pins
that no seed event time carries a ``Z``.
"""

from __future__ import annotations

#: event key -> the POST /events body fields besides matterId and attendees
#: (seed_staging fills those in from the manifest and the tenant's staff).
DATE_PREP_EVENTS: dict[str, dict] = {
    "hearing-minors-compromise-ramirez": {
        "matter": "minor-ramirez",
        "subject": "Hearing on Petition to Approve Compromise of Minor's Claim",
        "startTime": "2026-10-06T09:30:00",  # 9:30 a.m. local
        "endTime": "2026-10-06T10:30:00",
        "timeZone": "America/Los_Angeles",
        "location": "Department 3",
    },
    # Added 2026-09-28 (second fresh date): proves a yes to "finalize the
    # witness list" runs that job, not the binder, and that a yes to a confirm
    # question is the confirmation. 2026-PI-107 has no open task and no other
    # date in the window. Stored as local time; the brief must say 8:30 a.m., Dept 14.
    "final-status-conference-alvarez-draper": {
        "matter": "lookalike-alvarez",
        "subject": "Final Status Conference",
        "startTime": "2026-10-08T08:30:00",  # 8:30 a.m. local
        "endTime": "2026-10-08T09:30:00",
        "timeZone": "America/Los_Angeles",
        "location": "Department 14",
    },
    # Added 2026-09-28 (third fresh date): proves a finalize step renders a
    # Word document carrying the draft's own caption (#2974). 2026-PI-104 has
    # no other date in the window. Stored as local time; 10:00 a.m., Dept 22.
    "mandatory-settlement-conference-whitfield": {
        "matter": "liens-whitfield",
        "subject": "Mandatory Settlement Conference",
        "startTime": "2026-10-09T10:00:00",  # 10:00 a.m. local
        "endTime": "2026-10-09T12:00:00",
        "timeZone": "America/Los_Angeles",
        "location": "Department 22",
    },
    # Added 2026-09-28 (fourth fresh date): proves the caption renders as a
    # real Word table (#2976) on a finalized witness list. 2026-PI-101 has no
    # overdue open task and no other date in the window.
    "trial-readiness-conference-alvarez": {
        "matter": "mva-alvarez",
        "subject": "Trial Readiness Conference",
        "startTime": "2026-10-07T09:00:00",  # 9:00 a.m. local
        "endTime": "2026-10-07T10:00:00",
        "timeZone": "America/Los_Angeles",
        "location": "Department 31",
    },
}


def _witness_list_alvarez_draper() -> list[str]:
    return [
        "PLAINTIFF'S WITNESS LIST (DRAFT)",
        "Alvarez v. Draper Logistics",
        "",
        "1. Maria Alvarez, plaintiff",
        "2. Tomas Alvarez, plaintiff's husband, damages",
        "3. Kevin Osei, Draper Logistics warehouse supervisor on duty",
        "4. Dr. Priya Natarajan, treating orthopedist",
        "5. Linda Ferreira, records custodian, Harbor Community Hospital",
        "",
        "DRAFT: confirm Dr. Natarajan will testify live, not by deposition, before serving.",
    ]


def _exhibit_list_ramirez() -> list[str]:
    return [
        "PETITIONER'S EXHIBIT LIST (DRAFT)",
        "Petition to Approve Compromise of Pending Action of Minor",
        "Minor: Sofia Ramirez, by her guardian ad litem Elena Ramirez",
        "",
        "Exhibit 1. Emergency department records, date of injury",
        "Exhibit 2. Plastic surgery consultation report and treatment plan",
        "Exhibit 3. Photographs of the injury at intake and at six weeks",
        "Exhibit 4. Defendant's written settlement offer",
        "Exhibit 5. Itemized medical expenses and lien statements",
        "",
        "DRAFT: confirm the final medical expense figures before filing.",
    ]


def _exhibit_list_whitfield() -> list[str]:
    return [
        "SUPERIOR COURT OF CALIFORNIA, COUNTY OF LOS ANGELES",
        "",
        "James Whitfield, Plaintiff,",
        "v.",
        "Pacific Freight, Defendant.",
        "",
        "Case No. 25STCV40712",
        "Department 22",
        "",
        "PLAINTIFF'S EXHIBIT LIST FOR MANDATORY SETTLEMENT CONFERENCE (DRAFT)",
        "",
        "Exhibit 1. Police traffic collision report",
        "Exhibit 2. Treating physician narrative report",
        "Exhibit 3. Itemized medical specials summary",
        "Exhibit 4. Medi-Cal (DHCS) lien itemization",
        "",
        "DRAFT: confirm Exhibit 4 should be included before serving.",
    ]


def _witness_list_alvarez_mva() -> list[str]:
    return [
        "SUPERIOR COURT OF CALIFORNIA, COUNTY OF LOS ANGELES",
        "",
        "Maria Alvarez, Plaintiff,",
        "v.",
        "Kenneth Draper, Defendant.",
        "",
        "Case No. 24STCV18223",
        "Department 31",
        "",
        "PLAINTIFF'S TRIAL WITNESS LIST (DRAFT)",
        "",
        "1. Maria Alvarez, plaintiff",
        "2. Officer R. Chavez, investigating officer",
        "3. Dr. Samuel Ikeda, treating chiropractor",
        "",
        "DRAFT: confirm Officer Chavez has been subpoenaed before serving.",
    ]


def build_date_prep_documents() -> dict[str, tuple[str, str, list[str]]]:
    """doc_key -> (matter_key, file_name, text lines), the seed_data shape."""
    return {
        "exhibit-list-draft-ramirez": (
            "minor-ramirez",
            "2026-09-28 Petitioner Exhibit List (draft) - Ramirez.pdf",
            _exhibit_list_ramirez(),
        ),
        "witness-list-draft-alvarez-draper": (
            "lookalike-alvarez",
            "2026-09-24 Plaintiff Witness List (draft) - Alvarez.pdf",
            _witness_list_alvarez_draper(),
        ),
        "exhibit-list-draft-whitfield": (
            "liens-whitfield",
            "2026-09-25 Plaintiff MSC Exhibit List (draft) - Whitfield.pdf",
            _exhibit_list_whitfield(),
        ),
        "witness-list-draft-alvarez-mva": (
            "mva-alvarez",
            "2026-09-26 Plaintiff Trial Witness List (draft) - Alvarez.pdf",
            _witness_list_alvarez_mva(),
        ),
    }
