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
"""

from __future__ import annotations

#: event key -> the POST /events body fields besides matterId and attendees
#: (seed_staging fills those in from the manifest and the tenant's staff).
DATE_PREP_EVENTS: dict[str, dict] = {
    "hearing-minors-compromise-ramirez": {
        "matter": "minor-ramirez",
        "subject": "Hearing on Petition to Approve Compromise of Minor's Claim",
        "startTime": "2026-10-06T16:30:00Z",  # 9:30 a.m. Pacific
        "endTime": "2026-10-06T17:30:00Z",
        "timeZone": "America/Los_Angeles",
        "location": "Department 3",
    },
}


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


def build_date_prep_documents() -> dict[str, tuple[str, str, list[str]]]:
    """doc_key -> (matter_key, file_name, text lines), the seed_data shape."""
    return {
        "exhibit-list-draft-ramirez": (
            "minor-ramirez",
            "2026-09-28 Petitioner Exhibit List (draft) - Ramirez.pdf",
            _exhibit_list_ramirez(),
        ),
    }
