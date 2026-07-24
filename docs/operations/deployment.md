# Deployment model

0.6 is a pre-release installed package, not a published container release. Its
supported runtime shape is an explicitly invoked catalogue, foreground batch,
or single-report process with:

- an explicit `config-v3` JSON file;
- separate explicit current-schema catalogue and report SQLite database paths
  on reliable local storage;
- an owner-controlled cover artifact store and, for batch use, an
  owner-controlled immutable manifest path;
- owner-only target/provider secret files;
- an explicitly configured read-only EPUB mount; and
- outbound access only to the named Audiobookshelf origin and fixed Hardcover
  HTTPS operation.

The process needs no HTTP port, queue, scheduler, worker, external database, or
writeable media mount. Run it as a non-root principal, do not mount the Docker
socket, do not grant host networking or elevated capabilities, and keep the
root filesystem read-only where packaging permits.

Future one-container, dashboard, worker, reviewer, backup, and write-capable
deployment direction is not installed 0.6 authority.
