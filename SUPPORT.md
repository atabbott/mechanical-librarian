# Support

Mechanical Librarian is pre-release. You build it from source as one
self-hosted container ([Deployment](docs/operations/deployment.md)). There is no
published image, no supported release and no support period yet. Builds may
change incompatibly and can require a clean rebuild between versions; see
[CHANGELOG.md](CHANGELOG.md).

Support covers only the platforms and versions in
[Compatibility](docs/reference/compatibility.md).

## Where to ask

- **Questions, setup help and early ideas:** the
  [discussion forum](https://github.com/atabbott/mechanical-librarian/discussions).
- **Reproducible defects and accepted feature proposals:** the
  [issue tracker](https://github.com/atabbott/mechanical-librarian/issues).
- **Suspected vulnerabilities:** the private route in [SECURITY.md](SECURITY.md),
  never a public post.

Never post API tokens, credentials, private URLs, complete library exports,
book contents or unredacted logs.

## What a useful report contains

- the source revision you built;
- host operating system, architecture and container runtime;
- Audiobookshelf version and any other relevant service versions;
- the smallest configuration that reproduces the problem, with secrets removed;
- exact steps, expected behaviour and actual behaviour;
- relevant job IDs, preparation or **Preparation stopped** references, and
  timestamps; and
- a support bundle, if it helps. The API builds a redacted bundle of at most
  64 KiB; review its preview before sharing. See
  [Observability](docs/operations/observability.md#support-bundle).

Maintainers should not need access to your live library or host to diagnose an
ordinary failure.
