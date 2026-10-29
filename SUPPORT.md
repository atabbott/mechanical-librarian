# Support

Mechanical Librarian is pre-release software under active development. Its current checkpoint curates Audiobookshelf ebook and audiobook metadata in one owner workspace, running as a single self-hosted container that you build from source with the supplied `Dockerfile` and `compose.yaml` (see [Deployment](docs/operations/deployment.md)); there is no published image and no supported release yet, and pre-release state may require a clean rebuild between versions (see [CHANGELOG.md](CHANGELOG.md)).

This policy describes the support model the project intends to use once public releases begin.

## Where to ask for help

Use the repository's [discussion forum](https://github.com/atabbott/mechanical-librarian/discussions) for:

- installation and configuration questions;
- integration questions;
- ideas that are not yet concrete feature requests; and
- help interpreting non-sensitive diagnostic output.

Use the issue tracker for reproducible defects and accepted feature proposals. Use the private security-reporting route described in [SECURITY.md](SECURITY.md) for suspected vulnerabilities.

Do not post API tokens, credentials, private URLs, complete library exports, book contents, or unredacted logs.

## Supported environments

Support is limited to the platforms, versions, and deployment modes listed in the published compatibility matrix. See [Compatibility policy](docs/reference/compatibility.md).

Until releases begin, the deployment is the single container built from source with persistent `/data` storage. Once releases begin, the primary deployment will be the official single-container image; source builds and alternative container runtimes will remain welcome, but maintainers may first ask reporters to reproduce a problem with a supported image and configuration.

## What a useful report contains

Include:

- the Mechanical Librarian version and image digest, or the source revision you built;
- host operating system, architecture, and container runtime;
- relevant adapter and target-service versions;
- the smallest reproducible configuration, with secrets removed;
- expected and actual behavior;
- exact reproduction steps;
- relevant correlation IDs, job IDs, and timestamps; and
- a redacted support bundle when the product can safely generate one.

Maintainers should not need access to a user's live library or host to diagnose ordinary failures.

## Support bundles

The planned support-bundle feature must:

- default to metadata and diagnostics only;
- exclude secrets and authentication material by construction;
- warn before including filenames, titles, authors, or remote URLs;
- show an inventory before export; and
- produce a portable archive the user can inspect before sharing.

See [Observability](docs/operations/observability.md) for the diagnostic design.

## Maintenance and lifecycle

Release support periods, deprecation notices, and end-of-support dates will be published with the first stable release policy. Until then, pre-release builds may change incompatibly and must not be treated as production-supported software.
