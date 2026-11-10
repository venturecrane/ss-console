# Operator Enablement Kit

For a sales representative getting to know SMD Services and its flagship, the Operator. Every part leads with a picture; the text underneath explains it.

## Start here: the product, for any business

| File                                                  | What it is                                                                                                       |
| ----------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| [How the Operator Works](how-the-operator-works.html) | The explainer: seven views, one figure each, vertical-neutral, with one industry example at the end.             |
| [Product model](product-model.md)                     | The source the explainer rests on: every claim cited to code, config or a decision record, with maturity marked. |
| [Exemplar research](research-exemplars.md)            | The published explainers the views were benchmarked against, and the conventions taken from them.                |
| [Designer brief](designer-brief/figures-brief.html)   | The brief for the figures a designer redraws, built by `designer-brief/build_brief.py`.                          |

The explainer is generated: edit `figures-src/gen3.body.html` (text) or `figures-src/gen3.py` (figures), then run `python3 docs/enablement/figures-src/gen3.py docs/enablement/how-the-operator-works.html`.

## Reading order for the law-firm overlay (v0)

Parts 1 to 4 are the first draft, written for one industry (legal) before the product-level explainer above. Read them after it, as the example of how the general model is pitched in one vertical.

> **Visual V0: The kit at a glance.** Brief in [Visual briefs](05-visual-briefs.md).

| Part | File                                                   | Read it to answer                                               |
| ---- | ------------------------------------------------------ | --------------------------------------------------------------- |
| 1    | [What it is](01-what-it-is.md)                         | "What is this, in one breath?"                                  |
| 2    | [How it works](02-how-it-works.md)                     | "What happens when a client uses it? Where does the data live?" |
| 3    | [How an engagement goes](03-how-an-engagement-goes.md) | "How does SMD find what a client actually needs?"               |
| 4    | [How to pitch it](04-how-to-pitch-it.md)               | "What do I say, to whom, and what do I say back?"               |

## Ground rules for anything made from this kit

- No dollar amounts and no fixed timeframes in anything a prospect sees. Both are scoped per engagement.
- No client names. Our first client is "a personal-injury law firm."
- Proof figures are used exactly as written here or not at all.
- Firm voice: "we" and "our team."

## Reshaping it

Every page is plain text, so it pastes into a slide, a document or an email without fighting formatting. The visuals are produced separately, from the briefs in [Visual briefs](05-visual-briefs.md), in tools whose output stays editable.

## Version notes

- **v0:** first draft for your reactions. Tell us what's missing, what's unclear, and what format your last company used. We'll match it.
