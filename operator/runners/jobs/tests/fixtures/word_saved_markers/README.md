# Marker fixtures saved by Word

Each `.xml` file here is one `w:p` paragraph whose run structure was written by
Microsoft Word when a person saved a document. Word cuts one line of text into
several runs when it saves: a proofing mark, a revision id, a format change on
part of the line, a bookmark. A marked place (`[[name]]`) that a person typed in
one piece can therefore sit in two or more runs after a save, and a reader that
looks at one run at a time finds no marked place at all.

`tests/test_firmbase_markers_word_saved.py` loads every fixture and asserts
that the single-run reader (`jobs.firmbase.base.slot_names`) finds nothing in
it and that the joined-run reader (`jobs.firmbase.markers.slot_names`) finds
exactly the fixture's marker.

## How they were made

1. Word-saved `.docx` files were searched for body paragraphs holding one short
   line of text that Word had cut across two or more text pieces. Only files
   whose `docProps/app.xml` names Word as the application were used. Each
   fixture comes from a different file.
2. Only the paragraph's structure was kept. Every character of its text was
   replaced: the paragraph's joined text became one marker, `[[fixture.one]]`
   for the first fixture and so on, cut across the same text pieces at the same
   proportional points, so the marker is split the way Word split the line.
3. Everything that could identify anyone or anything was replaced by a fixed
   made-up value: revision ids (adjacent runs that differed still differ),
   paragraph and text ids, authors, dates, bookmark names and ids, fonts,
   colours, style ids that are not Word built-ins, link targets and field
   instructions. Deleted text became `#` characters. A content control's own
   properties were dropped. Nothing outside the one `w:p` was kept: no styles,
   no document properties, no headers.
4. The files are indented for reading. Word writes no white space between
   elements; none of it is inside a text piece.

No word in a fixture comes from a document. The only words are the marker, the
WordprocessingML element and attribute names, the values Word itself defines
(`spellStart`, `single`, `ListParagraph`), and the made-up values.

## The scrub check

`tests/firmbase_word_saved_scrub.py` holds the closed lists of what a fixture
may contain and fails anything else: a word in a text piece, an author, a date,
a revision id that is not one of the made-up ones, a font, an unlisted element,
attribute or file-name word, a comment. The test file runs it over this whole
directory and also shows it failing on dirtied copies. Run it by hand with:

```
python tests/firmbase_word_saved_scrub.py tests/fixtures/word_saved_markers
```

The made-up values it allows:

| What                       | Value                    |
| -------------------------- | ------------------------ |
| Revision ids               | `00A00001` to `00A00008` |
| Paragraph id, text id      | `0A000001`, `0A000002`   |
| Font                       | `Fixture Sans`           |
| Author of a tracked change | `Fixture Author`         |
| Date of a tracked change   | `2000-01-01T00:00:00Z`   |
| Bookmark names             | `bm1`, `bm2`             |
| Link relationship id       | `rId1`                   |
| Style id                   | `Body`                   |
| Field instruction          | `REF bm1`                |
| Deleted text               | `#` characters           |
| Colour                     | `000000`                 |

## What each fixture holds

| File                                 | What cut the line                                                           | Application string of the source file |
| ------------------------------------ | --------------------------------------------------------------------------- | ------------------------------------- |
| `one-proof-mark-spelling.xml`        | A spelling mark (`w:proofErr`) between two runs                             | Microsoft Office Word 16.0000         |
| `two-proof-mark-grammar.xml`         | A grammar mark (`w:proofErr`) between two runs                              | Microsoft Office Word 16.0000         |
| `three-revision-id-run.xml`          | Adjacent runs with the same format and a different `w:rsidR`                | Microsoft Office Word 12.0000         |
| `four-revision-id-format.xml`        | Adjacent runs with the same format and a different `w:rsidRPr`              | Microsoft Office Word 16.0000         |
| `five-bold-on-part.xml`              | Bold on part of the line                                                    | Microsoft Macintosh Word 14.0000      |
| `six-underline-on-part.xml`          | Underline on part of the line                                               | Microsoft Office Word 16.0000         |
| `seven-superscript-on-part.xml`      | Superscript on part of the line                                             | Microsoft Office Word 16.0000         |
| `eight-bookmark-start.xml`           | A `w:bookmarkStart` between two runs                                        | Microsoft Office Word 16.0000         |
| `nine-bookmark-end.xml`              | A `w:bookmarkEnd` between two runs                                          | Microsoft Office Word 16.0000         |
| `ten-rendered-page-break.xml`        | A `w:lastRenderedPageBreak` inside a run                                    | Microsoft Office Word 16.0000         |
| `eleven-tab-in-line.xml`             | Tab runs inside the line                                                    | Microsoft Office Word 15.0000         |
| `twelve-line-break-in-line.xml`      | A `w:br` run inside the line                                                | Microsoft Office Word 16.0000         |
| `thirteen-two-texts-one-run.xml`     | One run holding two text pieces with a `w:br` between them                  | Microsoft Office Word 16.0000         |
| `fourteen-no-break-hyphen.xml`       | A `w:noBreakHyphen` inside a run                                            | Microsoft Office Word 16.0000         |
| `fifteen-smart-tag-on-part.xml`      | Nested `w:smartTag` wrappers around part of the line                        | Microsoft Office Word 16.0000         |
| `sixteen-tracked-insertion.xml`      | A tracked insertion (`w:ins`) around part of the line, then a deletion      | Microsoft Office Word 16.0000         |
| `seventeen-tracked-deletion.xml`     | A tracked deletion (`w:del`) inside the line, then two insertions           | Microsoft Office Word 16.0000         |
| `eighteen-hyperlink-on-part.xml`     | A `w:hyperlink` wrapper around part of the line                             | Microsoft Office Word 16.0000         |
| `nineteen-field-on-part.xml`         | A field (`w:fldChar` begin, instruction, separate, end) on part of the line | Microsoft Office Word 16.0000         |
| `twenty-content-control-on-part.xml` | A content control (`w:sdt`) around part of the line                         | Microsoft Office Word 16.0000         |
| `twentyone-identical-runs.xml`       | Adjacent runs with the same format, no revision ids and nothing between     | Microsoft Macintosh Word 14.0000      |

Application strings seen across the source files: Microsoft Office Word
16.0000, Microsoft Office Word 15.0000, Microsoft Office Word 12.0000 and
Microsoft Macintosh Word 14.0000.

## Looked for and not found

No source file held a tracked move (`w:moveFrom`, `w:moveTo`), a `w:customXml`
wrapper, a comment range inside a line, or a `w:fldSimple` field with text
inside it. Those structures have no fixture yet.

## Adding a fixture

Name it with the next number word and words from `LABEL_WORDS` in the scrub
check, hold only its marker, and add its label to `CAUSES` in the test file.
If it needs an element, attribute or value that is on no list, add that to the
scrub check in the same change, and only if it cannot hold a name, a date, a
path or an id.
