"""The summary at the top of a Negotiation Details tab stays true after a run
writes a row: only a summary the Operator wrote is rewritten, from the tab as it
now stands; a firm-written one is never touched; a summary that does not read
back is reported in the firm's notice. Every name, number and date is invented.

The defect this defends (2026-10-10, the first live run): a carrier's tender
went in beneath a summary that went on saying "our demand ... has no response
in the file"."""

from __future__ import annotations

import pytest

from medchron import arrivals
from medchron.negotiation import correct as C, dryrun as D, summary as S

from test_negotiation import FILES, M1, NEW, Layout, Seat, _offer, _run

HEAD = "Entered 10/9/26 from the offer letters and emails saved in this file."
OURS = f"{HEAD} Latest: our $1,000,000 demand of 7/28/26 has no response in the file."
DEMAND = {"row": 0, "demand_amount": "1000000", "demand_date": "2026-07-28", "note": "Our 998 offer, per 'x' letter"}
TENDER = {
    "row": 1,
    "offer_amount": "956000",
    "offer_date": "2026-09-23",
    "note": "Example Mutual, via counsel Pat Doe policy-limits tender; answers our 998 offer of 7/28/26; from 'y' email",
}


# ---- the pure rule ---------------------------------------------------------------------------
def test_a_newer_offer_replaces_no_response_with_who_and_when():
    assert S.refreshed(OURS, [DEMAND, TENDER]) == f"{HEAD} Latest: Example Mutual $956,000 (9/23/26)."


def test_an_offer_the_document_showed_accepted_says_so():
    new = S.refreshed(OURS, [DEMAND, TENDER], {1: "2026-09-23"})
    assert new == f"{HEAD} Latest: Example Mutual $956,000 (9/23/26), accepted 9/23/26."


def test_a_newer_counter_of_ours_is_named_as_unanswered():
    rows = [
        {
            "row": 0,
            "offer_amount": "25000",
            "offer_date": "2026-09-03",
            "note": "Our counter; Acme / Beta offer, per 'z'",
        },
        {"row": 1, "demand_amount": "500000", "demand_date": "2026-10-02", "note": "Our counter, per 'w' email"},
    ]
    assert S.latest(rows) == (
        "Latest: Acme / Beta $25,000 (9/3/26); our $500,000 counter of 10/2/26 has no response in the file."
    )


def test_a_carriers_answer_to_that_counter_is_the_latest():
    rows = [
        {"row": 0, "demand_amount": "500000", "demand_date": "2026-10-02", "note": "Our counter, per 'w' email"},
        {
            "row": 1,
            "offer_amount": "28000",
            "offer_date": "2026-10-09",
            "note": "Acme / Beta offer; answers our counter",
        },
    ]
    assert S.latest(rows) == "Latest: Acme / Beta $28,000 (10/9/26)."


@pytest.mark.parametrize(
    "firm",
    ["Chris: carrier at limits, client deciding.", "Entered by Christa; see file.", "", None],
)
def test_a_summary_without_the_operator_marker_is_never_rewritten(firm):
    """FALSIFIER: drop the marker match and the firm's own summary is replaced."""
    assert S.refreshed(firm, [DEMAND, TENDER]) is None


def test_a_summary_already_current_is_left_alone():
    current = f"{HEAD} Latest: Example Mutual $956,000 (9/23/26)."
    assert S.refreshed(current, [DEMAND, TENDER]) is None


@pytest.mark.parametrize(
    "note,want",
    [
        ("Acme / Beta offer; answers our counter of 10/2/26; from 'a' letter", "Acme / Beta"),
        ("Our demand (amount not stated in the file); Acme / Beta offer, per 'b' email", "Acme / Beta"),
        ("Joint, all plaintiffs. Gamma counter-offer, per 'c' letter", "Gamma"),
        ("Delta offer (amount to be checked against the letter), per 'd' letter", "Delta"),
        ("Our counter, per 'e' email; no response in the file", ""),
        ("", ""),
    ],
)
def test_who_comes_from_the_rows_own_note_or_not_at_all(note, want):
    assert S.who_of(note) == want


def test_an_offer_whose_maker_the_note_does_not_name_is_given_no_name():
    row = {"row": 0, "offer_amount": "9000", "offer_date": "2026-10-01", "note": "Settlement figure, per 'f'"}
    assert S.latest([row]) == "Latest: $9,000 (10/1/26)."


# ---- the run ---------------------------------------------------------------------------------
class SummaryLayout(Layout):
    """A tab whose after-write read-back carries a summary."""

    def __init__(self, details=OURS, refresh_status="written", **kw):
        super().__init__(details=details, rows=[DEMAND], **kw)
        self.refresh_status = refresh_status
        self.refreshes: list = []

    def add_negotiation_rows(self, mid, rows, plaintiff_index=None, details=""):
        out = super().add_negotiation_rows(mid, rows, plaintiff_index, details)
        after = [DEMAND] + [{"row": a["row"], **{k: v for k, v in a.items() if k != "row"}} for a in rows]
        out["negotiation"] = {"rows": after, "details": self.details}
        return out

    def refresh_operator_details(self, mid, details, expected, plaintiff_index=None):
        self.refreshes.append((mid, details, expected, plaintiff_index))
        return {"status": self.refresh_status}


def _tender():
    return [_offer(kind="policy_limits_tender", by="Example Mutual", amount=956000, date="2026-09-23")]


def test_a_written_row_rewrites_the_operators_summary(tmp_path, monkeypatch):
    arrivals.commit("negotiation", M1, FILES, data=tmp_path / "state")
    layout = SummaryLayout()
    v = _run(tmp_path, Seat(NEW), layout, monkeypatch, events=_tender()).run()
    assert len(layout.refreshes) == 1
    _mid, new, expected, pidx = layout.refreshes[0]
    assert expected == OURS and pidx == 0
    assert new == f"{HEAD} Latest: Example Mutual $956,000 (9/23/26)."
    assert "summary" not in v["notices"][0]["text"]


def test_an_accepted_offers_summary_says_accepted(tmp_path, monkeypatch):
    arrivals.commit("negotiation", M1, FILES, data=tmp_path / "state")
    events = _tender() + [_offer(kind="acceptance", by="Ashton for the client", amount=956000, date="2026-09-23")]
    layout = SummaryLayout()
    v = _run(tmp_path, Seat(NEW), layout, monkeypatch, events=events).run()
    assert layout.refreshes[0][1].endswith("Example Mutual $956,000 (9/23/26), accepted 9/23/26.")
    assert v["notices"] == []


def test_a_firm_written_summary_gets_no_refresh_call(tmp_path, monkeypatch):
    arrivals.commit("negotiation", M1, FILES, data=tmp_path / "state")
    layout = SummaryLayout(details="Chris: carrier at limits.")
    layout.rows = []  # an empty tab is the Operator's to fill even under the firm's summary
    v = _run(tmp_path, Seat(NEW), layout, monkeypatch, events=_tender()).run()
    assert layout.calls and layout.refreshes == []
    assert "summary" not in v["notices"][0]["text"]


@pytest.mark.parametrize("status", ["readback_mismatch", "refused", "raise"])
def test_a_summary_that_did_not_take_is_told_to_the_firm(tmp_path, monkeypatch, status):
    """FALSIFIER: ignore the refresh result and a stale summary goes unreported."""
    arrivals.commit("negotiation", M1, FILES, data=tmp_path / "state")
    layout = SummaryLayout(refresh_status=status)
    if status == "raise":
        layout.refresh_operator_details = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("vendor down"))
    v = _run(tmp_path, Seat(NEW), layout, monkeypatch, events=_tender()).run()
    text = v["notices"][0]["text"]
    assert v["notices"][0]["status"] == "entered"
    assert "The summary at the top of the tab could not be updated, please check it." in text


def test_the_dry_run_never_writes_a_summary():
    with pytest.raises(D.DryRunWriteRefused):
        D._ReadOnlyLayout(SummaryLayout()).refresh_operator_details("m", "x", "y")


# ---- the one-off correction -------------------------------------------------------------------
class TabLayout:
    def __init__(self, details, rows, sticks=True):
        self.details, self.rows, self.sticks, self.writes = details, rows, sticks, []

    def get_matter_layouts(self, mid, section=""):
        return {
            "status": "ok",
            "items": [{"parent_index": 0, "negotiation": {"rows": self.rows, "details": self.details}}],
        }

    def refresh_operator_details(self, mid, details, expected, plaintiff_index=None):
        self.writes.append(details)
        if self.sticks:
            self.details = details
            return {"status": "written"}
        return {"status": "readback_mismatch"}


def test_the_correction_shows_without_writing():
    tab = TabLayout(OURS, [DEMAND, TENDER])
    out = C.correct(tab, "m", 0, {1: "2026-09-23"}, write=False)
    assert out["status"] == "would_write" and tab.writes == [] and out["before"] == OURS
    assert out["after"].endswith("accepted 9/23/26.")


def test_the_correction_writes_and_reads_back():
    tab = TabLayout(OURS, [DEMAND, TENDER])
    out = C.correct(tab, "m", 0, {}, write=True)
    assert out["status"] == "written" and out["read_back_matches"] is True
    assert out["after"] == f"{HEAD} Latest: Example Mutual $956,000 (9/23/26)."


def test_the_correction_reports_a_read_back_mismatch():
    tab = TabLayout(OURS, [DEMAND, TENDER], sticks=False)
    out = C.correct(tab, "m", 0, {}, write=True)
    assert out["status"] == "readback_mismatch" and out["read_back_matches"] is False and out["after"] == OURS


def test_the_correction_leaves_a_firm_summary_alone():
    tab = TabLayout("Chris: settled.", [DEMAND, TENDER])
    out = C.correct(tab, "m", 0, {}, write=True)
    assert out["status"].startswith("unchanged") and tab.writes == []
