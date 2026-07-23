# Observability

The 0.4 command has no daemon, health endpoint, metrics server, support-bundle
generator, job inspection, or verbose logging mode.

Automation should treat exit zero plus one canonical report line as success.
Every failure produces empty stdout, one fixed bounded stderr diagnostic, and a
nonzero exit. Diagnostics distinguish invalid invocation, configuration or
credential failure, unsupported media, pre-publication source drift, and a
generic safe operational failure without exposing rejected values.

The persisted report and its current pointer are the durable result. Operators
may use SQLite's integrity and foreign-key checks while the command is stopped;
direct row inspection or editing is not an application interface.
