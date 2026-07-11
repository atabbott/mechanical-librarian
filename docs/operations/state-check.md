# Durable state check

The `state-check` command is the supported diagnostic for the application-owned
SQLite state. It creates the database and parent directories when they do not
exist, applies the current migrations atomically, records one code-owned audit
marker, and reads that exact marker back through the application audit
contract.

Run it with an explicit state database path:

```text
mechanical-librarian state-check --database /var/lib/mechanical-librarian/state.sqlite
```

The path must name durable filesystem state. SQLite's exact `:memory:` special
name is rejected as a command-line error and never invokes the diagnostic.

A successful check prints exactly `State check passed.` to standard output and
exits with status `0`. Repeating the command, including after a process restart,
reuses the same audit marker without adding duplicate records.

A failed check prints one fixed diagnostic to standard error and exits with
status `1`. Failures include an unavailable or locked database, incompatible or
partially applied schema, failed migration, corrupt audit data, and audit
readback disagreement. Diagnostics never include the configured database path,
stored event values, or exception text. Command-line syntax errors retain the
standard `argparse` exit status `2`.

The command does not repair, replace, or downgrade state. Before opening an
existing database, it validates a stable disposable copy with its recovery
journal or WAL. Empty, truncated, incompatible, partial, or corrupt state fails
without changing the database or its recovery sidecars. A migration failure is
rolled back atomically and can be retried after the underlying problem is
resolved. Preserve rejected state and follow the
[backup and recovery guidance](backups-and-recovery.md) before taking corrective
action.
