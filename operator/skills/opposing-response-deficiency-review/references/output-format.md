# Opposing-Response Deficiency Review - Output Format

One output: an **internal, cited surface artifact** for the attorney. There is no
client-facing text and no external send. Every flagged item is a **candidate** pointed to
its request and response text. When an ask crosses the content ceiling (render the
judgment, or draft a motion or letter), the output is the **decline-and-surface** response
below.

## The surface artifact

```markdown
# Opposing-Response Review - <matter> - <response-set> (candidates for attorney review)

**Scope:** <propounded request set read> vs. <opposing response set read>, by name/id
**Note:** These are candidates surfaced for your review, not findings. Whether any
response is legally deficient, whether an objection has merit, and whether to meet and
confer or move to compel are your calls.
**Calibration:** <when the firm's sufficiency standard has not yet been learned from past
matters, state it plainly, e.g. "Not yet calibrated on this firm's past matters; candidates
below are surfaced conservatively.">

## Candidate gaps

- **Request <no.>** - candidate: <boilerplate/unsupported objection | non-answer |
  evasive/incomplete | missing verification>
  - Response says: "<cited response text>" - _<response doc name>, p.<n>_
  - Request asked: "<cited request text>" - _<request doc name>, p.<n>_
  - Why flagged (neutral): <one line, e.g. "stock objection with no substantive answer
    following">

## Unclear / needs your read first

- <item where the candidate is uncertain, or the request-to-response pairing is
  ambiguous> - _<source>_

## Not addressed

- <anything the ask touched that crosses the content ceiling: a rendered deficiency
  ruling, a meet-and-confer letter, a motion - named and left to the attorney>
```

Worked example:

```markdown
# Opposing-Response Review - Roe v. Doe - Defendant's responses to Special Interrogatories, Set One (candidates for attorney review)

**Scope:** Plaintiff's Special Interrogatories Set One (12 nos.) vs. Defendant's Responses to Special Interrogatories Set One
**Note:** These are candidates surfaced for your review, not findings.
**Calibration:** Not yet calibrated on this firm's past matters; candidates below are surfaced conservatively.

## Candidate gaps

- **Request 4** - candidate: boilerplate/unsupported objection
  - Response says: "Objection. Vague, ambiguous, overbroad, and unduly burdensome." (no answer follows) - _Def. Responses to SROG Set One, p.3_
  - Request asked: "State all facts supporting your Second Affirmative Defense." - _Pl. SROG Set One, p.2_
  - Why flagged (neutral): stock objection with no substantive answer following and no explanation tied to this request.
- **Request 7** - candidate: evasive/incomplete
  - Response says: "Responding party did not cause the collision." - _Def. Responses to SROG Set One, p.4_
  - Request asked: "State each fact supporting your contention that plaintiff was comparatively at fault." - _Pl. SROG Set One, p.3_
  - Why flagged (neutral): answers a different point than the specific facts requested.

## Unclear / needs your read first

- Whether Request 9's narrower answer is complete depends on what the firm treats as sufficient here; surfaced for your read - _Def. Responses to SROG Set One, p.5_

## Verification

- The response set does not appear to include a party verification; a substantive response set is expected to be verified (candidate - please confirm) - _Def. Responses to SROG Set One_
```

## Rules

1. **Candidate, never finding.** Every flagged item is labeled a candidate and is framed
   as _for the attorney to review_. No item states or implies that a response _is_
   deficient, that an objection lacks merit, or that a further response is required.
2. **Cited or absent.** Every candidate cites the specific request number and the response
   text (and, where useful, the request text) with a document + location. A candidate the
   skill cannot point to in the actual text is not surfaced. No fabrication.
3. **No next-step decision.** The artifact never decides to meet and confer, never decides
   to move to compel, and never computes or asserts the compel deadline. When a person
   asks, it may explain the general process step and its governing rule, as education,
   never as a direction to act, and never in the artifact itself.
4. **No argument.** No section argues why a response is insufficient. Surfacing the pattern
   is allowed; arguing the deficiency is work product and is banned.
5. **Internal only.** The artifact is for the attorney; it is never addressed to opposing
   counsel, a client, or a tribunal, and is never sent.
6. **Confidentiality.** Matter content appears only inside this internal artifact and never
   leaves the firm's surfaces.
7. **Calibration disclosed.** When the firm's sufficiency standard has not yet been
   calibrated on its past matters, the artifact says so in the **Calibration** line, so the
   reader knows the candidates were surfaced conservatively and not against a learned firm
   standard. The skill never presents an uncalibrated candidate as a confident finding.

## The decline-and-surface response (ask over the ceiling)

When asked to render the judgment ("just tell me these are deficient") or to draft ("draft
the motion to compel" / "write the meet-and-confer letter"), the skill does not comply and
does not apologize its way into producing it. It surfaces the candidates and states the
boundary:

```markdown
# Opposing-Response Review - <matter> - candidates (judgment and drafting declined)

I surface candidate gaps for your review; I don't render the deficiency judgment or draft
the motion or letter. Here are the candidates you'd weigh:

## Candidate gaps

- **Request <no.>** - candidate: <category>
  - Response says: "<cited text>" - _<source>_
  - Why flagged (neutral): <one line>

Whether any of these is legally deficient, and whether to meet and confer or move to
compel, is your call. The drafting is yours or your drafting tool's.
```

The decline always **hands over the candidates** - it is never a bare refusal. The attorney
gets everything the judgment would be built on; the judgment and the drafting stay with
them.
