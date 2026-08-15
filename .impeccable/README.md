# Impeccable design context

`PRODUCT.md` is the durable product record and `DESIGN.md` is the visual
authority for the implemented interface. `.impeccable/design.json` is their
machine-readable companion; local configuration, critiques, screenshots,
previews, annotations, and exploratory variants are intentionally not shared.

The current sidecar contract was produced with Impeccable 4.0.4. When either
durable record changes:

1. use the Codex skill command `$impeccable document` to refresh
   `.impeccable/design.json` from the accepted `DESIGN.md` and representative
   current interface source;
2. review the sidecar diff, including its component examples and responsive
   behavior, rather than editing only its generation timestamp;
3. run `$impeccable doctor` and require an empty findings list; and
4. parse the DESIGN frontmatter and sidecar as YAML and JSON during change
   validation.

Those are Codex skill commands, not repository shell commands. A future
Impeccable version change is an intentional design-tooling contract update and
must refresh this note and the sidecar together.
