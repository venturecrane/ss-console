# referral-source-acknowledgment algorithm

Source of truth for thanking the referrer without breaching client confidentiality.

## Referral-source resolution

```
src = matter.referral_source (a Smokeball contact reference)
if not src or not resolvable:
    surface "referral source not recorded - human to confirm"   # never guess a referrer
contact = get_contact(src.id)   # name + contact channel
```

A thank-you to the wrong person, or to a guessed referrer, is worse than none. Only a recorded, resolved referral source proceeds.

## The confidentiality gate

This is the skill's defining logic. A referral source is not the firm's client; the engagement's existence and details are confidential.

```
if firm.authored_permission_to_share(src):     # explicit, per-source firm flag
    draft MAY reference the client/matter as the firm authored
else:                                           # DEFAULT
    draft acknowledges the referral GENERALLY:
        - thank them for thinking of the firm / sending someone their way
        - NO client name, NO matter type, NO matter detail
```

Default is general acknowledgment. Detail is the exception, allowed only on an explicit firm-authored permission for that source - never inferred from "they probably know already" or "they're a close referral partner."

## Why general is the safe default

Often the referrer _does_ know who they sent - but the firm confirming it in writing is still a disclosure the firm may not be authorized to make, and the value of the thank-you (promptness, warmth) does not require it. So the default costs nothing and risks nothing: "Thank you for thinking of us - we appreciate you sending business our way" carries the full relationship value without naming anyone.

## Recipient check

The drafted message is addressed to the resolved referral source's contact channel only. A guard confirms the recipient is the referrer, not the client and not another matter party - a misaddressed thank-you would itself disclose the referral relationship to the wrong person.

## External send - the firm's authored ceiling

Whether the thank-you sends or drafts is the firm's authored `external_send` ceiling (ADR 0035; see `operator/references/send-posture.md`). `draft_for_review` - surfaced for a human to send under their identity - is the recommended starting posture, not a floor: the law-firm `external_send` floor was removed (ADR 0073).
