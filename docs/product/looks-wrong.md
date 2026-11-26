# Looks wrong

Looks wrong lists works whose current values have a shape that is usually a
mistake, whether or not a source disagrees. Each work names what was found: a
title that is only a disc or part number or ends with a disc number, a title
that is the author's name or includes it as "Author - Title", a title in
capitals or all lower case, a title with an announcement such as "Now a Major
Motion Picture", or an author's or narrator's name that starts with a credit
label such as "Narrated by". A title that a source spells the same way is not
listed for being the author's name or for being in lower case. Nothing is
changed for you. These works also appear in the other lenses.

## Where it shows

Looks wrong is a lens in Library, after All and after
[New](library-progress.md#new-since-your-last-visit) while works are new, and
keeps All's order and headings. Its count follows the others beside Works, only when it is
not zero, for example "2,026 works · 3 not matched · 9 could not finish · 37
look wrong", or "1 looks wrong" for one work. Under a search inside the lens
the status reads "Found 1 of 1 work"; a screen reader also hears "looking
wrong". Every row with a finding, in any lens, names each finding on its own
line, and a screen reader reads them with the row. A work that could not
finish before it had a report has no current values and no findings.

## The rules

The rules read only the current title, authors and narrators, and prefer
missing a mistake to naming a correct value.

| Finding | What it means | Not a finding |
| --- | --- | --- |
| Title is only a disc or part number | "Disc 01", "Part 3 of 5", "[Disc 2]", "Part IV" | "Part One", "Part 2: The Return" |
| Title ends with a disc number | "Example Story - Disc 1", "Example Story (CD 2)" | a title ending in a Part, which real titles do |
| Title is the author's name | the title equals a current author | "Example Author's Notebook" |
| Title includes the author's name | "Example Author - Example Story" | "Example Story - A Novel" |
| Title is in capitals | at least two words of two or more letters, all capitals | a one-word acronym, "QB VII" |
| Title is all lower case | the same, all lower case | a title with no cased letters |
| Title includes an announcement | "Example Story (Now a Major Motion Picture)" | "The Bestseller" |
| A name starts with a credit label | "Narrated by Example Reader", "Narrator: Example Reader" | "Byron Read" |

A title that a source in the same report states with exactly the same spelling
is not listed as the author's name or as all lower case: a self-titled work
and a title published in lower case are real. The findings are worked out when
a report is recorded and kept with it; a build with a different rule list
needs `regenerate-derived`
([Upgrades](../operations/upgrades.md#upgrading-with-a-regeneration)).
