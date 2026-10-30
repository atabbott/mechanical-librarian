# Documentation Standards

Documentation describes the product as it is now. Incorrect documentation is a
bug.

## Keep it current

Every pull request updates the documentation its change makes stale, in the
same pull request:

- behaviour an owner or operator sees: the operations guide, reference page or
  product contract that describes it, and the [changelog](../../CHANGELOG.md);
- configuration, persisted state or API: the reference page, and the generated
  schema where there is one;
- a durable architectural decision: a new or superseding ADR;
- a security boundary: the [threat model](../architecture/security.md);
- a milestone opening, closing or being reordered: the README status and the
  [roadmap](../product/roadmap.md).

Update a document in place. Say what is true now, and delete what stopped
being true; git history, the changelog, issues and pull requests already keep
the record of how it got there. Do not add dated narrative, delivery history,
issue arithmetic or review history to a living document.

ADRs are the exception: an accepted ADR keeps its decision text, and a later
change supersedes or amends it.

## Where things live

- `README.md`: what the product is, its status, and where to go next.
- `PRODUCT.md`: the product boundary.
- `docs/product/`: scope, roadmap and product contracts.
- `docs/operations/`: running, backing up, recovering and upgrading.
- `docs/reference/`: exact configuration, API, state and vocabulary.
- `docs/architecture/`: structure, security, privacy and decisions.
- `docs/development/`: how the project is built, tested and delivered.
- `CHANGELOG.md`: one bullet per user-visible outcome, at most six lines, with
  its issue reference.

Every live document is listed in, or reachable from, [the index](../README.md).
A document that is no longer needed is deleted.

## Writing

- Lead with the answer or the rule. Keep documents short; link to the
  authoritative page instead of repeating it.
- Write for a reader without issue or chat context.
- Say plainly what is current and what is planned.
- State defaults, prerequisites, side effects and stop conditions.
- Commands are safe to copy, mark placeholders, and keep secrets off the
  command line.
- Never include real credentials, domains, personal paths, library titles or
  production identifiers.
- Write every link inline, as `[text](path.md)`.

## What CI checks

`scripts/verify_documentation_integrity.py` runs in the `fast-source` gate job.
It checks that every document under `docs/` is reachable from `docs/README.md`,
that every relative link resolves, and that the ADR index and the ADR files
match one to one with no shared numbers. Run it locally with:

```text
uv run python scripts/verify_documentation_integrity.py
```

The fast tier also checks that the generated OpenAPI and configuration schemas
match the code, that `docs/examples/synthetic-config.json` loads, that every
direct dependency has a grant in [dependencies](dependencies.md), and that the
check table in [CONTRIBUTING.md](../../CONTRIBUTING.md#what-merging-proves)
matches the gate workflow. Everything else, including whether prose is still
true, is checked in review.
