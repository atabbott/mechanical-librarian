# Find this book

Find this book links a work in Library to the record, at one source, that is
its book. You search one source, choose the record, and every later
preparation reads that record first and asks the other sources about that
book instead of the file's own title and authors
([ADR-0104](../architecture/decisions/0104-owner-identity-links.md)). It is
how a Not matched work, or one whose sources found the wrong book, becomes an
ordinary review case.

## Where it is

A selected work's header in Library reads Evidence, Find this book and More.
History is under More. A work with no case has no Evidence or History, so its
header holds only Find this book. Once the work is linked, a pill in place of
Find this book says the link's state, and opens the link. Neither one adds a
row above the field tabs.

Find this book is not offered on a work that could not finish, or when the
work's library has no source it can search. A work that could not finish but
is already linked still shows its pill, with Withdraw only.

Find this book opens a dialog over Library. Escape or Close shuts it and
records nothing. Focus returns to the button or pill that opened it.

## Searching

The dialog asks for a source, among the ones enabled for the work's library
that can be searched:

| Work | Sources |
| --- | --- |
| Ebook | Hardcover, Open Library, Google Books, Inventaire |
| Audiobook | Audible, Hardcover, Open Library |

You search by title and authors (up to four), or by ISBN, or, on Audible, by
ASIN at the marketplace you pick. The fields start with the work's current
values, and you can change them. A line above Search says what is sent, for
example "Search sends the ISBN to Open Library."

Each search sends what you typed to that one source, in the request it already
uses for preparation, with the same key or contact email:

- Hardcover receives the title in the case forms preparation sends, and your
  author names unchanged, or the ISBN.
- Open Library and Google Books receive the title and authors, or the ISBN.
- Inventaire receives the title and authors in one search, then two reads of
  the works it names and their authors' names, or one read of the ISBN.
- Audible receives the title and authors, or the ASIN, at the marketplace you
  chose and no other.

A title or author is 1 to 512 characters with no web address or file path. An
ISBN is 10 or 13 digits with a valid check digit, and an ASIN is 10 letters
and digits. A value outside these is refused beside its field and kept as
typed.

## Limits

- One search runs at a time in each browser session.
- Each source takes 30 searches an hour from Find this book. The 31st says
  when to search again.
- A search shares its source's pace with preparation. If that source is busy
  with other lookups for 10 seconds, the search is not sent and says so.
- While a source is limiting requests, its quota is used up or it refused the
  key, the search says so, with the time to search again where there is one.
- A search is sent once. One that fails is not retried and is not handed to
  [provider recovery](../operations/provider-recovery.md); you search again.

## Results

A search shows at most ten records. When the source has more, the dialog
reads "More results. Refine the search." A record that cannot be read is left
out and counted. A record shows its title, authors, narrators, year,
publisher, language, ISBN or ASIN and running time where it states them, and
its cover where the source names one Mechanical Librarian fetches covers
from. A record with no ISBN or ID cannot be linked, and says so.

Results and covers are held in memory for this browser session for 15
minutes, and a new search replaces them. Signing out or restarting
Mechanical Librarian discards them. They are never stored and never become
evidence.

None of these closes the dialog and records nothing.

## Choosing

Choose opens a confirmation, "Link this work to {title} by {authors} from
{source}?", with a line for each thing about the record that disagrees with
the work: another ISBN or ASIN, a collection of several books, a language
outside the library's, none of the work's authors, or, for an audiobook,
another running time. These lines inform the choice and do not block it.

The confirmation offers This book, and also This edition (an ebook record
with an ISBN that is not a print or audio edition) or This recording (an
Audible record whose running time, narrators and version agree with the
file). A scope that is not offered says why.

Choosing records the link with your account and the time. The work is marked
to be prepared again, and its library's scan is asked to start. While
preparation is paused, the link waits for it to resume.

## The link

The pill says the link's state. Library's row says the same, with Linked in
place of Linked on {source}.

| Pill | When |
| --- | --- |
| Linked on {source} | the latest preparation read the record through the link |
| Linked, preparing | no preparation has read the current link yet |
| Linked, waiting for {source} | the source did not answer; provider recovery asks it again |
| Link contradicted | an edition or recording no longer agrees with the record, with the reason |
| Link needs replacing | the source no longer has the record, merged it into another, is turned off for the library, or the work changed between ebook and audiobook |

The pill opens the Book link view: the record, the scope, the state line,
Change and Withdraw. Change turns the dialog into a search, and choosing
there replaces the link. Withdraw asks first, "Withdraw the link to {title}?
Its evidence leaves this work at the next preparation."

If the link changed in another window, a choice or withdrawal is refused and
asks you to check it first. If no answer came back, check whether the work is
linked before choosing again.

## After a link changes

The next preparation reads the record through the link. A Not matched work
leaves Not matched once the linked source, or another source asked about the
linked book, returns records. The preparation panel says the run started
when a book link changed.

Until that preparation runs, a decision on the work's case is refused with
"The book link changed. This work is being prepared again." That covers
choosing, pinning, withdrawing and the description choice. It holds while
preparation is paused.

Evidence that only the link supports never decides a field automatically.
Such a value is left to you, with "Only the book you linked supports this
value, so it is left to you." A cover that only the link supports is offered
for your choice and is never switched automatically. A link never fills an
ISBN or ASIN and never withdraws a decision you made.

## History and Activity

A work's History lists each link, change and withdrawal with its time, for
example "You linked this work to {title} on {source}, as this book." Activity
lists them under the field Book link as Linked, Link changed and Link
withdrawn, with the source, the scope and what happens next.
