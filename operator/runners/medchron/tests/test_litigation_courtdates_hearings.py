"""Every hearing on the firm's calendar is a court date (``litigation/courtdates.py``).

2026-11-02: a case's calendar held a timed motion hearing, live for weeks and
present in the calendar read, but ``kind`` gave its wording no kind. The list
showed the case's next court date as one two years out and flagged the
papers' date as missing from the calendar. Synthetic wordings only.
"""

from __future__ import annotations

import datetime as dt

import pytest

from medchron.litigation import courtdates as cd

TODAY = dt.date(2026, 11, 2)


def ev(subject: str, *, all_day: bool = False, ltb: bool = False) -> dict:
    start = "2026-11-10T00:00:00" if all_day else "2026-11-10T08:30:00"
    return {"startTime": start, "allDay": all_day, "subject": subject, "isDeleted": False, "ltb": ltb}


#: Each read as no court date (or as a trial or mediation) before the fix.
MISSED = (
    "11/10 - Example. Motion to Compel dept 47",
    "11/10 - Example. MTC dept 47",
    "11/10 - Example. MSJ dept 47",
    "11/10 - Example. Demurrer dept 47",
    "MSJ",
    "Motion for Summary Judgment",
    "Motion to Compel Further Responses",
    "Mtn to Compel",
    "Mot. to Compel",
    "Def's Motion to Quash",
    "Motion in Limine",
    "Demurrer to Complaint",
    "Motion to Set Aside Default",
    "Petition to Approve Minor's Compromise",
    "Status Conference",
    "Ex-Parte Application",
    "Hearing on Motion for Leave to File Amended Complaint",
    "Hearing on Notice of Motion and Motion to Compel",
    "Motion to Compel Responses to Requests for Production",
    "OSC re: Failure to File Proof of Service",
    "Hearing on Motion to Compel (file reply 5 court days before)",
    "Hearing on Motion to Compel; Request remote appearance",
    "Hearing re Motion to Continue Trial",
    "Motion for Trial Preference",
    "Post-Mediation Status Conference",
)

#: Already recognised; must stay so.
KEPT = (
    "Hearing on Motion to Compel",
    "MTC hearing",
    "Hrg re: Motion",
    "Motion for Summary Judgment - Hearing",
    "OSC re Dismissal",
    "Demurrer hearing",
    "Hearing: Motion to Strike",
    "HRG - MSJ",
    "Ex Parte Application",
    "Order to Show Cause re Sanctions",
    "Hearing on MSJ - Opposition due",
)


@pytest.mark.parametrize("all_day", [False, True], ids=["timed", "all-day"])
@pytest.mark.parametrize("subject", MISSED + KEPT)
def test_an_entry_that_names_a_hearing_is_a_hearing(subject: str, all_day: bool):
    assert cd.kind(ev(subject, all_day=all_day)) == "hearing"


#: Deadlines, tasks and reminders that mention a motion are still not a date.
NOT_A_DATE = (
    "File motion to compel",
    "Serve MSJ papers",
    "Motion to Compel - file reply brief",
    "MSJ Opposition due",
    "Opposition to MTC due",
    "Reply to MTC due",
    "Prepare for MSJ hearing",
    "Last day to hear MSJ",
    "Last day to file motion",
    "Motion cut-off",
    "RMDR - hearing on motion to compel",
    "Motion hearing reminder",
    "2 WEEKS BEFORE MSJ HEARING",
    "CANCELLED - MTC hearing",
    "OFF - Motion to Compel",
    "HIDDEN Motion to Compel",
    "VACATED - Demurrer hearing",
    "L/D to Req Remote Appearance at MTC hearing",
    "Request phone appearance at MSJ hearing",
    "11/3 - Deadline. Example. LD to Submit Motion",
)


@pytest.mark.parametrize("all_day", [False, True], ids=["timed", "all-day"])
@pytest.mark.parametrize("subject", NOT_A_DATE)
def test_a_deadline_or_reminder_mentioning_a_motion_is_still_excluded(subject: str, all_day: bool):
    assert cd.kind(ev(subject, all_day=all_day)) is None


def test_a_lawtoolbox_all_day_rule_deadline_about_a_hearing_is_still_not_the_hearing():
    for subj in ("Hold summary judgment hearing", "Motion to Compel", "Hearing on Motion to Compel"):
        assert cd.kind(ev(subj, all_day=True, ltb=True)) is None, subj
    assert cd.kind(ev("HEARING- Motion to compel", all_day=True, ltb=True)) == "hearing"


def test_reminders_about_other_events_are_still_excluded():
    for subj in ("TWO WEEKS BEFORE DISCOVERY CUT-OFF", "2/8-EXAMPLE. THREE MONTHS BEFORE TRIAL"):
        assert cd.kind(ev(subj)) is None, subj
    assert cd.kind(ev("MOTION/DISCOVERY CUT-OFF", all_day=True, ltb=True)) == "discovery_cutoff"


def test_a_motion_hearing_on_the_calendar_is_the_next_court_date_and_agrees_with_the_papers():
    """The 2026-11-02 shape: a timed motion hearing next week, a trial two
    years out, and the papers giving the hearing's date."""
    cal = [ev("11/10 - Example. Motion to Compel dept 47"),
           {"startTime": "2028-03-06T08:30:00", "allDay": False, "subject": "TRIAL", "isDeleted": False}]  # fmt: skip
    papers = {"date": "2026-11-10", "event": "Hearing on motion to compel", "source": {"name": "Notice.pdf"}}
    got = cd.merge({"next_court_date": papers}, cal, TODAY)
    assert got["flag"] == "" and got["next"]["date"] == "2026-11-10"
    assert got["calendar"]["date"] == "2026-11-10" and got["calendar"]["time"] == "8:30 AM"
