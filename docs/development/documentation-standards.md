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

For a substantial new document or rewrite, choose one primary shape before
writing:

- a tutorial helps a reader learn by completing a guided result;
- a how-to gives an experienced reader steps to a goal;
- reference states exact facts, options, limits, and errors for lookup; and
- explanation develops understanding of one bounded question or decision.

Split and link when combining those shapes would obscure the reader's task.
Write instructions as direct commands, put applicable conditions before the
action, and put the common case before exceptions. Use present-tense active
sentences where the actor matters. Keep one instruction or thought in a
sentence when combining them creates ambiguity. Repeat a noun instead of using
an unclear pronoun, keep `only` or `not` beside the words they modify, and keep
articles or connecting words that make a sentence parse only one way. Prefer
plain words and real symbols; remove filler without making the prose cryptic.

## Change rules

- Behavior changes update documentation in the same pull request.
- Persisted or API contract changes update reference docs. Before the first
  production release, use ADR-0013 clean-schema handling and declare a clean
  rebuild or same-change exact-predecessor derived-object adoption; migration
  and compatibility notes require a named post-release obligation.
- Architectural changes add or supersede an ADR.
- Security boundary changes update the threat model.
- Deployment changes update installation, upgrade, backup, and rollback docs.
- Deprecated behavior includes replacement guidance and removal timing.

## Verification

Documentation is checked where a test binds a document to the thing it
describes, and nowhere else. What merging proves today:

- every live document under `docs/` is reachable from `docs/README.md`,
  directly or transitively, so the list that claims to hold every live document
  cannot silently stop holding one;
- every relative link in a live document resolves to a path that exists, and
  every link is written in a form the checker can read;
- every file in `docs/architecture/decisions/` has a row in that directory's
  index, every index row names a file that is there, and every row's declared
  number is the number of the file it links to;
- no two ADRs share a number;
- every direct dependency `pyproject.toml` declares carries an authority grant
  in `docs/development/dependencies.md`, no grant outlives the dependency it
  covers, and a grant's own spelled count still matches its subjects;
- the generated `docs/reference/review-openapi-v1.json` matches the routes, and
  `docs/reference/config-v8.schema.json` matches the configuration model;
- `docs/examples/synthetic-config.json` is executable — the CLI tests load it;
- `CONTRIBUTING.md`'s check table matches `.github/workflows/public-gate.yml`
  row by row;
- `docs/operations/deployment.md` states the operational facts
  `tests/unit/test_operations.py` requires and none it forbids;
- no `CHANGELOG.md` entry exceeds its line budget, and every entry names its
  issue apart from the pre-convention entries the guard grandfathers by name;
- generating everything leaves the tree clean.

The first four are `scripts/verify_documentation_integrity.py`, which the
`fast-source` gate job runs and which reproduces from a clean checkout with one
command:

```text
uv run python scripts/verify_documentation_integrity.py
```

It reads the tree and nothing else — an `http`, `https` or `mailto` target is
skipped rather than fetched, so the check never needs a network. Its own
behaviour is proven against fixture corpora in
`tests/unit/test_documentation_integrity.py`, which also runs it against this
repository, so the fast tier fails on a corpus defect too. The ADR
reconciliation is by filename in both directions and never by arithmetic over
the numbers: a gap in the sequence is a decision that was never written, not a
missing file.

The fifth is `scripts/verify_dependency_authority.py`, a sibling in the same
job and the same shape:

```text
uv run python scripts/verify_dependency_authority.py
```

It reads `pyproject.toml` and `docs/development/dependencies.md` and nothing
else. It judges no grant's quality — that a grant exists is machine-checkable,
that it is true is not — and it reads no lockfile, licence, or transitive
package, because the document is explicitly about direct dependencies. Its own
behaviour is proven in `tests/unit/test_dependency_authority.py`, which also
runs it against this repository.

Write every link target inline, as `[text](path.md)`, optionally with a title.
Reference-style links — `[text][label]` with a `[label]: path.md` definition
elsewhere — are reported as findings rather than resolved. Resolving them means
telling a shortcut reference apart from ordinary bracketed prose, which is a
Markdown parser rather than a link check, and no document here needs the form.
Behind both rules sits a residue check: any `](` the checker's own pattern did
not consume is reported as a link it cannot read. A link form that is silently
ignored is reach the green result does not have, so no form is ignored.

Still unenforced, and owned by no ticket yet: Markdown style, spell checking,
duplicate headings, anchor targets inside a document, code-fence contents, ADR
status metadata, and external links, which would need network access in CI and
are a deliberate non-goal. For those, a rule stated in prose is enforced by
review, and prose that is not bound to an artifact can go stale without
anything failing — which is the argument for pointing at the artifact instead
of restating it.

Screenshots should be rare, sanitized, and accompanied by durable prose because
interfaces change faster than contracts.

## Documentation review checklist

- Is the intended reader clear?
- Does a substantial document have one clear primary shape?
- Is the documented behavior implemented and tested?
- Are failure and recovery paths included?
- Are safety warnings proportionate and actionable?
- Does the document reveal private information?
- Is another document already authoritative?
- Can any instruction or sentence be read in two materially different ways?
- Will the document still be understandable without issue or chat context?
