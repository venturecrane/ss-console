# Document classes and markers: document-drafter

The reference both modes read when the words of an email or a job report need
mapping. The drafting itself, and the firm's house style for each class, live
in the drafting job's own inputs on the Machine, never here.

## The five classes

| Class                | The firm asks for                                                                                           | Never this class                                                        |
| -------------------- | ----------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| `mediation_brief`    | a mediation brief or mediation statement                                                                    | a demand to a carrier (the demand lane); a settlement figure            |
| `discovery_set`      | discovery for us to serve: special or form interrogatories, requests for admission, requests for production | our answers to a set served on our client                               |
| `discovery_response` | our objections and answers to a set served on our client                                                    | a set we propound                                                       |
| `memo`               | a drafted memo DOCUMENT: a case or legal memo to the file or to the attorney, filed as a Word document      | a note or memo entry in the practice-management record ("note to file") |
| `depo_outline`       | a deposition outline, questions for a named deponent                                                        | a deposition notice or subpoena                                         |

A request whose words fit two classes or none is asked about in one sentence,
and nothing is submitted until the requester answers.

A class not in the seat's `enabled_classes` setting is refused by the broker
with a sentence the skill relays as given ("A memo isn't switched on for your
firm"). The requester check runs first, so a sender who may not ask is never
told which classes are on.

## The markers the job reports

The job leaves three kinds of item in the draft and lists each in its report
(`markers` on the job record, each `{kind, text}`):

- `{{ATTORNEY}}`: a call that is the attorney's. In a mediation brief this
  always includes settlement authority, the target figure and the bracket.
- `{{NOT IN RECORD}}`: a fact the draft needs that the file does not hold.
- `{{CLIENT}}`: a fact only the client knows. In discovery responses the
  verification is also left for the client to sign.

The DELIVER reply groups them by kind and quotes each `text` as reported.
It never fills one, and never adds an item the report does not carry.

## Caption discrepancies

The job compares the caption in the court papers with the practice-management
record and reports only exact-match fields it can quote (case number, court,
party names, attorney email): `{field, document_value, record_value, why,
source, quote}`. The reply lists the field, what the court paper says and what
the record says. It never corrects the record itself; a person decides.
