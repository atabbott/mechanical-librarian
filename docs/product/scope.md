# Scope and non-goals

[PRODUCT.md](../../PRODUCT.md) states the product boundary. Inside that
boundary, the first release includes the scope below and leaves out the
non-goals.

## First-release scope

- One self-contained Linux container.
- Audiobookshelf ebook and audiobook libraries, the first library service
  Mechanical Librarian supports.
- Scans of each library on an interval and when you choose Scan now.
  Mechanical Librarian then prepares each new or changed item on its own.
- Metadata and cover changes written through the service's supported APIs.
- Evidence from an EPUB's package metadata, its opening pages up to a size
  limit, its navigation and its structural marks.
- An audiobook's tags, chapters and running time, read but never written.
- Metadata sources, each behind its own replaceable adapter.
- SQLite storage for state, job recovery, the audit history and configuration.
- Readiness and backup checks, a check that the planned change still matches
  the library, the write itself, read-back verification and recovery. Activity
  shows the state of each write.
- A private single-user API and the bundled workspace, built to the
  [review-experience contract](review-experience.md).

## Planned extensions

- AZW3 and MOBI metadata evidence.
- Optional transcription of a limited stretch of an audiobook's opening, to
  read the credits spoken there
  ([opening-audio evidence](opening-audio-evidence.md)).
- Calibre-Web/Calibre-compatible service adapter.
- Other AI reviewer backends. Today a reviewer gives advice on covers and
  descriptions only
  ([future reviewer backends](future-reviewer-backends.md)).
- Writing metadata into media files, behind backup checks of its own.
- Notifications delivered outside Mechanical Librarian, and sign-in through
  external identity providers.

## Not in the first release

- Deleting media files automatically.
- Removing duplicates or formats automatically.
- Editing Audiobookshelf or other application databases directly.
- Transcribing a whole audiobook, or transcribing for any purpose other than
  the opening credits. Only a limited stretch of the opening is planned.
- OCR or broad PDF text extraction.
- Kubernetes, or deployment as several instances that share the load.
- Requiring PostgreSQL, Redis, a message broker or a public-cloud account.
- A general media acquisition or download-management system.
- Replacing Audiobookshelf's playback, reading, user, or library-management UI.
- Waiting for an exact edition match before improving a cover.

## Promises

Beyond the operating context in PRODUCT.md, the first release commits to four
things:

- New items stay available to read or play straight away. Mechanical
  Librarian works on them in the background once their files stop changing.
- The decisions waiting for you in Attention stay few. Mechanical Librarian
  records automatic decisions in History, and you do not have to review them.
- A failure stops the work it affects safely and says how to recover.
- You can back up and restore the configuration and the whole `/data` volume
  with ordinary tools. Nothing ties you to one vendor.

## Measures of success

- Most new items become acceptably correct without your help.
- No failure of a source, an AI reviewer, authentication, a backup or a write
  can corrupt your library's metadata unnoticed.
- Preparing an unchanged item again changes nothing in your library.
- You can understand why a field changed and which evidence supported it.
- The documentation says how to recover from a failed upgrade, an expired AI
  sign-in or a bad write, and each recovery is testable.
- A new contributor can find the contract that governs a behaviour without
  asking a maintainer.
- You can install and run Mechanical Librarian without knowing how it is built
  or how its author set it up.

## Services and stored state

Stored state follows the pre-release clean-state rule in
[compatibility](../reference/compatibility.md#the-clean-state-rule).

Each service adapter must detect what the service supports and report it. If
the service has no supported way to write a field, Mechanical Librarian does
not write it. It never edits the service's database or drives its screens
instead. If a change to the service breaks a capability, the writes that need
it must stop until the capability is confirmed again.

Supporting Audiobookshelf first does not tie the rest of Mechanical Librarian
to it. Audiobookshelf's payloads, identifiers, API calls, its own metadata
sources and whether they are available stay inside its adapter. A report-only
inventory that needs no source stays in scope. So do local evidence,
rule-based handling of values that need no change or are rejected, proposals
within fixed limits and your own review.

The planned Calibre-Web/Calibre-compatible adapter must use a supported service
API and declare each capability. No Calibre adapter, database access, screen
automation or write path exists today.
