# Mechanical Librarian

Mechanical Librarian keeps the metadata and covers in a self-hosted ebook and
audiobook library correct. It runs as one container beside
[Audiobookshelf](https://www.audiobookshelf.org/). It reads the library and the
book files, gathers evidence from public catalogues, decides each field from
that evidence, and asks the owner only where a person's judgement is likely to
help.

It is evidence-first rather than AI-first. Every proposed value carries the
evidence behind it. Routine decisions follow deterministic, qualified rules.
Nothing is written to the library until the owner grants consent for that
library, and every write goes through Audiobookshelf's own API and is read back
to confirm it.

## Status

**Pre-release.** There is no published image or supported release yet. You
can build and run it from source, but each checkpoint may require a clean
rebuild of its state (see the [changelog](CHANGELOG.md)).

What works today:

- Guided first run in the browser: claim the install, connect
  Audiobookshelf, choose libraries and metadata providers.
- Background preparation of every ebook and audiobook: local evidence from
  EPUB packages and audio files, plus catalogue evidence from Hardcover, Open
  Library, Inventaire, Google Books and iTunes Search for ebooks, and Audible,
  Audnexus, AudioSilo, Hardcover and Open Library for audiobooks.
- One owner workspace with five views: Library, Attention (the fields that
  need a decision), work detail, Policy and Activity.
- Automatic decisions for qualified field categories, with every outcome
  recorded in history.
- Guarded writes to Audiobookshelf. Each write needs library consent, backup
  readiness, an exact-current plan, and read-back verification.
- An optional AI cover reviewer (Codex or an OpenRouter model) and optional
  description advice. Reviewers are advisory only and never receive library
  credentials.

What comes next is tracked as
[GitHub milestones](https://github.com/atabbott/mechanical-librarian/milestones)
and summarised in the [roadmap](docs/product/roadmap.md). The next
checkpoints improve audiobook matching, cover choice and series handling, and
lead to the 1.0.0 first public release.

## Running it

The [deployment guide](docs/operations/deployment.md) has the details. In
short, from a clone of this repository:

```text
cp docs/examples/review-bootstrap.json review-bootstrap.json
sudo chown 65532:65532 review-bootstrap.json && sudo chmod 600 review-bootstrap.json
# add your media folders as read-only mounts (see the deployment guide)
docker compose up --build -d
docker compose logs mechanical-librarian   # shows the setup claim token
```

Then open `http://localhost:8080` and enter the claim token to start first-run
setup.

Media mounts stay read-only, and the product never edits Audiobookshelf's
database directly.

## Documentation

- [PRODUCT.md](PRODUCT.md): what the product is for and the boundaries it keeps.
- [Documentation index](docs/README.md): operations, reference, architecture
  and decisions.
- [CONTRIBUTING.md](CONTRIBUTING.md): how to build, test and propose changes.
- [SECURITY.md](SECURITY.md): how to report a vulnerability.

## Licence

[AGPL-3.0-or-later](LICENSE).
