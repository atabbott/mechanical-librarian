# Identifiers file

An identifiers file links many works at once to their ISBN or ASIN, from
Settings › Libraries › Import identifiers
([Find this book](../product/find-this-book.md#linking-from-a-file)). Each
link it makes is a [book link](../architecture/decisions/0104-owner-identity-links.md)
whose source is the file.

## Format

The file is UTF-8 text of at most 1 MiB. One byte-order mark at its start is
ignored. Lines end with LF or CRLF, and blank lines are ignored anywhere.

The first line is `item` or `path`, a tab, then `identifier`. Each line after
it is one row: a work, a tab, and its identifier. Fields are never trimmed.

```text
path	identifier
Fantasy/The Hollow Crown	9780306406157
```

- `item` rows name the Audiobookshelf item ID exactly.
- `path` rows name the item's folder within the library. The path is
  normalised to NFC and split on `/` or `\`; it matches a work when its last
  segments equal the whole of that work's folder path. Segments compare
  exactly and case matters. A row that matches two works is refused: use the
  item ID or a longer path.

An ebook takes an ISBN of 10 or 13 digits, with hyphens allowed between
digits, and a valid check digit; it is linked as this edition by its ISBN-13.
An audiobook takes an ASIN of 10 letters and digits, linked as this recording.

## Refusals

The whole file is refused when it is over 1 MiB, is not UTF-8, holds a control
character other than tab and a line end, has another first line, has more
than 5,000 rows, or has no rows.

A row is refused on its own, and its row number is its line in the file. In
order, a row is refused when it:

1. is longer than 4,096 bytes, or is not two non-empty values with one tab;
2. matches no work, or more than one;
3. has an identifier that is not an ISBN or ASIN, or the wrong one for the
   work's medium;
4. gives a work a different identifier from another row, or repeats an
   earlier row;
5. names a work that is already linked.

Every other row will be linked.

## Preview, import and withdrawal

The preview shows the counts, the rows that will be linked with each work's
title and authors, and each refused row's number and reason. It never shows a
path, item ID or row text. It is held in memory for 15 minutes, for this
browser session only, and a new preview replaces it.

Link records every will-link row as a link, the import and one Activity row,
together or not at all. It is refused when the library's works or links
changed since the preview; preview the file again. A lost answer is checked
with the server, never sent again by itself.

Withdraw this import, in the import's Activity Details, withdraws each of its
links that is still current. A link changed or withdrawn since is left as it
is. Every work whose link is made or withdrawn is prepared again.
