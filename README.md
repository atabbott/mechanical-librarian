# Mechanical Librarian

Mechanical Librarian keeps the metadata and covers in a self-hosted ebook and
audiobook library correct. It runs as one container beside
[Audiobookshelf](https://www.audiobookshelf.org/). It reads the library and the
book files and gathers evidence from public catalogues. It decides each field
from that evidence and asks you only where your judgement is likely to help.

It is evidence-first rather than AI-first. Every proposed value carries the
evidence behind it. Routine decisions follow rules that give the same answer
for the same evidence. Mechanical Librarian writes nothing to a library until
you give write permission for that library. Each write goes through
Audiobookshelf's own API, and Mechanical Librarian then reads the new value
back to confirm it.

## Status

Mechanical Librarian is pre-release. It has no published image yet. You can
build and run it from source. An upgrade that changes stored state needs a
clean rebuild, and the [changelog](CHANGELOG.md) marks each change that needs
one. A clean rebuild empties Mechanical Librarian's database and leaves
Audiobookshelf as it is.

What works today:

- Guided first-run setup in the browser. You claim the install, connect
  Audiobookshelf, choose metadata sources and libraries, and check that your
  media folders are mounted read-only.
- Background preparation of every ebook and audiobook. Local evidence comes
  from EPUB packages and audio files. Ebook catalogue evidence comes from
  Hardcover, Open Library, Inventaire, Google Books and iTunes Search.
  Audiobook catalogue evidence comes from Audible, Audnexus, AudioSilo,
  Hardcover and Open Library. iTunes Search also supplies an audiobook's cover
  while its Cover evidence switch in Settings is on.
- The workspace you sign in to: Library (with Attention and work detail),
  Policy, Activity and Settings. Attention holds only the fields that need a
  decision.
- Automatic decisions for the options that a qualification record names.
  Mechanical Librarian records every outcome in History.
- Writes to Audiobookshelf. Before each write, Mechanical Librarian checks that
  its own database is consistent and that the planned change still matches the
  library.
- An optional AI cover reviewer (Codex or an OpenRouter model) and optional
  description advice. Reviewers are advisory only and never receive library
  credentials.
- Plain words in the workspace, which is built to WCAG 2.2 AA. It works by
  keyboard and stays usable on phones and at 200% text.

[GitHub milestones](https://github.com/atabbott/mechanical-librarian/milestones)
hold what comes next, and the [roadmap](docs/product/roadmap.md) summarises
them. The next milestones improve audiobook matching, cover choice and series
handling, and lead to the 1.0.0 first public release.

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

Mechanical Librarian reads your media folders through read-only mounts. It
never edits Audiobookshelf's database directly.

## Documentation

- [PRODUCT.md](PRODUCT.md): what the product is for and the boundaries it keeps.
- [Documentation index](docs/README.md): operations, reference, architecture
  and decisions.
- [CONTRIBUTING.md](CONTRIBUTING.md): how to build, test and propose changes.
- [SECURITY.md](SECURITY.md): how to report a vulnerability.

## Licence

[AGPL-3.0-or-later](LICENSE).
