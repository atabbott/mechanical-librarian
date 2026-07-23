# Deployment model

0.4 is a pre-release installed package, not a published container release. Its
supported runtime shape is one process invocation with:

- an explicit `config-v2` JSON file;
- an explicit SQLite database path on reliable local storage;
- owner-only target/provider secret files;
- an explicitly configured read-only EPUB mount; and
- outbound access only to the named Audiobookshelf origin and fixed Hardcover
  HTTPS operation.

The process needs no HTTP port, queue, scheduler, worker, external database, or
writeable media mount. Run it as a non-root principal, do not mount the Docker
socket, do not grant host networking or elevated capabilities, and keep the
root filesystem read-only where packaging permits.

Future one-container, dashboard, worker, reviewer, backup, and write-capable
deployment direction is not installed 0.4 authority.
