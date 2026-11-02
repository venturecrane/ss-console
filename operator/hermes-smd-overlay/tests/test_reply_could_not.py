"""The decline classifier (2026-11-01, ashton-price): replay and rule.

Two sets, both real-SHAPED and written for this test (no client text is in
the repo, ADR 0081): declines an Operator writes when it could not do what
was asked, and ordinary replies that must not fire. The replay asserts the
measured rate exactly, so a pattern change that moves either number has to
move this file too.
"""

from __future__ import annotations

import pytest

from shared import reply_could_not

#: (reply text, expected code). The first is the 2026-11-01 miss, paraphrased.
DECLINES = [
    (
        "Hi Mark,\n\nI wasn't able to find or attach any of the five records you listed. "
        "I searched the matter's documents in Smokeball for each one.",
        "cannot_find",
    ),
    ("I could not find the MRI report from Banner in the file.", "cannot_find"),
    ("I couldn't locate the signed retainer agreement for this client.", "cannot_find"),
    ("Unfortunately I can't attach the PDFs to this email.", "cannot_attach"),
    ("I can’t attach those records here, they are too large.", "cannot_attach"),
    ("I cannot open the scan you sent; it appears to be corrupted.", "cannot_access"),
    ("I was unable to retrieve the billing statement from the provider folder.", "cannot_find"),
    ("We were unable to complete the chronology for this matter.", "cannot_complete"),
    ("I'm unable to send the letter until the address is confirmed.", "cannot_attach"),
    ("I'm not able to read page 4 of the deposition; the image is blank.", "cannot_access"),
    ("I haven't been able to locate a police report in the documents.", "cannot_find"),
    ("I have not been able to identify which provider sent this bill.", "cannot_find"),
    ("I can't seem to find the intake form for Ms. Lopez.", "cannot_find"),
    ("I don't have access to the firm's billing system.", "cannot_access"),
    ("I do not have access to that calendar.", "cannot_access"),
    ("I'm not permitted to send documents to an outside address.", "not_permitted"),
    ("I'm not allowed to change a matter's status.", "not_permitted"),
    ("None of the records you asked for could be found in the matter.", "none_available"),
    ("None of those documents were attached to the matter in Smokeball.", "none_available"),
    ("I couldn't download the files from the portal link.", "cannot_find"),
    ("We couldn't pull the medical records for 2023.", "cannot_find"),
    (
        "I couldn't draft the demand letter because the treatment dates are missing.",
        "cannot_complete",
    ),
    ("Sorry, I wasn't able to schedule the call for Thursday.", "cannot_complete"),
    ("I can't update the client's phone number in Smokeball.", "cannot_complete"),
    ("I could not verify the policy limits from the documents I have.", "cannot_complete"),
    ("I cannot provide the settlement figures you asked for.", "cannot_attach"),
    ("I was not able to file the letter to the matter.", "cannot_complete"),
    (
        "Thanks for your patience. I couldn't produce the records index; the folder is empty.",
        "cannot_attach",
    ),
    (
        "Here is what I found:\n- The bill from Valley Imaging is attached.\n"
        "- I couldn't find the records from Dr. Shah.",
        "cannot_find",
    ),
    ("I've been unable to access the shared folder you linked.", "cannot_access"),
    ("We weren't able to deliver the chronology; the review held it.", "cannot_attach"),
    ("I can't view the attachment you sent.", "cannot_access"),
    ("I couldn't still find it after a second search.", "cannot_find"),
    ("I couldn't include the photos; none were in the matter.", "cannot_attach"),
    ("I am unable to process this request right now.", "cannot_complete"),
    ("I'm sorry, but I can't help with that request.", "cannot_complete"),
]

#: Ordinary replies: answers, acknowledgements, offers, hypotheticals, idioms.
NORMAL = [
    "I've attached the five records you asked for.",
    "Here are the records from Banner Health, attached.",
    "On it. I'll send the chronology once it's finished.",
    "The chronology is queued and I'll reply here when it is done.",
    "If I can't find it, I'll let you know right away.",
    "Let me know if you can't open the attachment.",
    "If you cannot open it, reply and I'll send it another way.",
    "I couldn't agree more.",
    "I can't wait to get this finished for you.",
    "I can't thank you enough for the quick reply.",
    "I searched and could not find any conflicts for this client.",
    "I checked the bill and couldn't find any errors.",
    "I could not find any discrepancies between the two statements.",
    "The hearing is on November 12 at 9:00 AM.",
    "Done. The letter is filed to the matter.",
    "I filed the letter and sent you a copy.",
    "You can't miss it: it's the first document in the folder.",
    "Your client can't be reached at the number on file; here is the other number we have.",
    "The provider couldn't find the records, so they sent a no-records certificate, attached.",
    "I updated the phone number in Smokeball.",
    "I found all five records and attached them.",
    "Can't open it? Reply and I'll resend.",
    "Thanks, Scott",
    "",
    "Unless you need it sooner, I can't see a reason to rush it.",
    "We can do that. I'll schedule it for Thursday.",
    "I can send the letter today.",
    "I can attach the records once you confirm the matter.",
    "Here is the summary you asked for.\n\nOn Mon, Nov 1, 2026 at 10:26 AM Mark wrote:\n> I couldn't find the records, can you attach them?",
    "> I couldn't find the records\nHere they are, attached.",
    "Whether or not I can find the original, the copy attached is certified.",
    "In case I can't reach you, I'll leave the file in the matter.",
    "None of the dates conflict with the trial calendar.",
    "There were none missing: all five records are attached.",
    "I can't help but notice the second bill is a duplicate; both are attached.",
    "The records were attached to the matter on October 3.",
    "I have access to the matter and attached the records.",
    "I was able to find all of them.",
    "We were able to complete the chronology; it is attached.",
    "I'm allowed to send this to you as the responsible attorney, so it's attached.",
]


@pytest.mark.parametrize(("text", "code"), DECLINES)
def test_each_decline_fires_with_its_code(text: str, code: str) -> None:
    assert reply_could_not.classify(text) == code


@pytest.mark.parametrize("text", NORMAL)
def test_each_ordinary_reply_does_not_fire(text: str) -> None:
    assert reply_could_not.classify(text) is None


def test_measured_rate_on_the_replay_set() -> None:
    """The numbers the PR reports: every decline caught, no ordinary reply fired."""
    caught = sum(1 for text, _ in DECLINES if reply_could_not.classify(text) is not None)
    fired = sum(1 for text in NORMAL if reply_could_not.classify(text) is not None)
    assert (caught, len(DECLINES)) == (36, 36)
    assert (fired, len(NORMAL)) == (0, 40)


def test_known_false_negatives_are_documented() -> None:
    """Declines the rule does NOT catch, named in the module's RISK note. If one
    starts firing, move it to DECLINES; that is an improvement."""
    for text in (
        "Those records are not in Smokeball.",
        "Unfortunately that isn't something the system holds.",
    ):
        assert reply_could_not.classify(text) is None


def test_codes_are_closed_and_non_text_is_none() -> None:
    for _text, code in DECLINES:
        assert code in reply_could_not.CODES
    assert reply_could_not.classify(None) is None
    assert reply_could_not.classify(42) is None
    assert reply_could_not.metadata("I couldn't find it.") == {"could_not": "cannot_find"}
    assert reply_could_not.metadata("Attached.") == {}
