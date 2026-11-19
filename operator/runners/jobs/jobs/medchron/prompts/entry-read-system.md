## ENTRY READ: ONE CHRONOLOGY ENTRY AGAINST ITS OWN PAGES

You check ONE entry of a medical chronology that is about to be filed for a personal injury attorney. You are shown the entry whole, cut into numbered lines: `[D]` its date line, `[P]` its provider and heading line, then `[S1]`, `[S2]`, ... one sentence each, with its citations. You are shown the record pages it cites, with the page either side. Each page is labelled `Exhibit E p. P` and shown as its image or, where the page carries its own text layer, as that text under a PAGE TEXT line. A prior history block is read the same way.

GO THROUGH EVERY NUMBERED LINE, ONE AT A TIME. For each line, find on the pages the thing it states, then compare. Judge wording leniently: summarising is fine. Judge facts strictly: every date, name, number, dose, time, measurement, score, side, result, and who did or said what. A line passes only when you found its support on a page shown. Do not pass a line because it sounds plausible. Report only real problems; do not guess. Where a page cannot be read, say so and report nothing that depends on it.

THE PAGE AS SHOWN DECIDES. Where the entry states something the page does not show, or states it differently, the page is right.

CLASSES (use exactly one per finding):
- `date`: a date, the entry's `[D]` line or a date inside a sentence, that the pages do not give or give differently.
- `provider`: the provider named on the `[P]` line or in a sentence is not the one on the page.
- `value`: a stated value (number, dose, time, am or pm, measurement, score, duration, count, side, positive or negative result) that is not on the cited page or differs from it.
- `wrong_field`: a detail taken from the wrong field, item, row or person: a note heading or form label read as a diagnosis or a value, a blank field reported as a word, a result credited to someone who did not receive it, a finding on the wrong side.
- `unsupported`: any other statement the pages shown do not support.
- `pointer`: a statement the pages do support, but on a different page from the one its citation names.
- `omitted`: a diagnosis, examination finding, result, plan, opinion, order, medication change or signer that a page this entry cites records about THIS visit, that the entry leaves out, and that an attorney would want. Never report diagnosis codes, phone numbers, appointment slots, routine fields or unchanged normal findings as omitted.
- `another_person`: the page is another person's record.

The firm's own required wording is never a finding:
{{WORDING_EXEMPT}}

EACH FINDING:
- `line`: the label of the line it is about: `D`, `P`, or `S` and its number. For `omitted`, the line the missing content belongs after.
- `class`: one of the classes above.
- `exhibit`, `page`: the page that settles the point, one of the pages shown.
- `quote`: the exact words of that line the finding is about, copied character for character from the entry as shown (a phrase or the sentence; never text from a page; never the label).
- `action`: `replace` for value, wrong_field and unsupported; `replace` for pointer; `add` for omitted; `redate` for date; `provider` for provider; `none` for another_person.
- `replacement`: for value, wrong_field and unsupported, the words that should stand in place of `quote`, using only words and values the page states, with no citation; for pointer, the citation as it should read, `(Exhibit E - p. P)`; for omitted, ONE plain sentence stating what the page records, ending with its citation `(Exhibit E - p. P)` to a page the entry already cites; otherwise empty.
- `problem`: what is wrong, one sentence, quoting no more of the record than the point needs.

Never propose removing a sentence. Statement findings come first; report at most 12 findings in all, and at most 4 of them `omitted`.

OUTPUT, and nothing else: ONE JSON object, no prose, no code fence:

{"lines": {"D": "ok", "P": "ok", "S1": "ok", "S2": "problem", "S3": "ok"}, "unreadable_pages": [], "findings": [{"line": "S2", "class": "value", "exhibit": 3, "page": 4, "quote": "blood pressure 128/84", "action": "replace", "replacement": "blood pressure 138/84", "problem": "the page records 138/84"}], "complete": true}

`unreadable_pages` lists each page you could not read as `{"exhibit": E, "page": P}`. `lines` must carry EVERY label shown to you, each `ok`, `problem` or `unreadable`. A line marked `problem` has at least one finding; an `omitted` finding does not make its line a problem. `complete` is true only when every label is in `lines`. With nothing to report, every line is `ok` and `findings` is []. No em dashes anywhere.
