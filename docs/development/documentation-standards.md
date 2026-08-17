# Documentation Standards

## Principle

Documentation is versioned product behavior. Incorrect documentation is a bug.

## Authoritative locations

- `README.md`: product identity, status, goals, and navigation.
- `docs/product/`: outcomes, scope, and roadmap.
- `docs/architecture/`: boundaries, models, and threat analysis.
- `docs/architecture/decisions/`: durable decisions and consequences.
- `docs/development/`: contribution, testing, quality, and release practices.
- `docs/operations/`: deployment, backup, recovery, upgrade, and diagnosis.
- `docs/reference/`: exact configuration, state, API, and vocabulary contracts.
- `CHANGELOG.md`: user-visible changes, one outcome per top-level bullet in at
  most six lines with its issue reference.

## Authority and lifecycle

Current installed behavior is defined by the README current-status section and
the operations, reference, and generated contracts it links. Delivery slices
own milestone outcomes and historical exit boundaries; the roadmap summarizes
them but does not override them. Accepted ADRs preserve their decision boundary,
with later evolution recorded explicitly. Historical and future plans cannot
grant current behavior.

## Writing rules

- Lead with the outcome or governing rule.
- Use precise nouns from the domain vocabulary.
- Distinguish current behavior, planned behavior, examples, and historical
  context.
- State defaults, prerequisites, side effects, and stop conditions.
- Never include real credentials, domains, personal paths, library titles, or
  production identifiers.
- Commands must be safe to copy, declare placeholders, and avoid secrets on the
  command line.
- Link to the authoritative rule instead of duplicating it.
- Use diagrams only where relationships are materially clearer than prose.
- Add alternative text or textual explanation for diagrams.

## Change rules

- Behavior changes update documentation in the same pull request.
- Persisted or API contract changes update reference docs. Before the first
  production release, use ADR-0013 clean-schema handling; migration and
  compatibility notes require a named compatibility decision and post-release
  obligation.
- Architectural changes add or supersede an ADR.
- Security boundary changes update the threat model.
- Deployment changes update installation, upgrade, backup, and rollback docs.
- Deprecated behavior includes replacement guidance and removal timing.

## Verification

Documentation is checked where a test binds a document to the thing it
describes, and nowhere else. What merging proves today:

- the generated `docs/reference/review-openapi-v1.json` matches the routes, and
  `docs/reference/config-v6.schema.json` matches the configuration model;
- `docs/examples/synthetic-config.json` is executable — the CLI tests load it;
- `CONTRIBUTING.md`'s check table matches `.github/workflows/public-gate.yml`
  row by row;
- `docs/operations/deployment.md` states the operational facts
  `tests/unit/test_operations.py` requires and none it forbids;
- no `CHANGELOG.md` entry exceeds its line budget;
- generating everything leaves the tree clean.

There is no Markdown style check, link checker, spell check, duplicate-heading
or anchor check, code-fence check, or ADR metadata check. #686 owns adding
real ones. Until then, a rule stated in prose is enforced by review, and prose
that is not bound to an artifact can go stale without anything failing — which
is the argument for pointing at the artifact instead of restating it.

Screenshots should be rare, sanitized, and accompanied by durable prose because
interfaces change faster than contracts.

## Documentation review checklist

- Is the intended reader clear?
- Is the documented behavior implemented and tested?
- Are failure and recovery paths included?
- Are safety warnings proportionate and actionable?
- Does the document reveal private information?
- Is another document already authoritative?
- Will the document still be understandable without issue or chat context?
