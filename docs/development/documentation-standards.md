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
- a milestone opening or being reordered: the README status and the
  [roadmap](../product/roadmap.md);
- a milestone's last pull request: the [closing pass](#closing-a-milestone).

Update a document in place. Say what is true now, and delete what stopped
being true; git history, the changelog, issues and pull requests already keep
the record of how it got there. Do not add dated narrative, delivery history,
issue arithmetic or review history to a living document.

ADRs are the exception: an accepted ADR keeps its decision text, and a later
change supersedes or amends it.

## Closing a milestone

The last pull request to merge in a milestone also brings the documentation up
to date as of the milestone's close. In the same pull request:

- Close what the milestone owns. Its changelog section drops "(in progress)"
  and has one bullet for each delivered user-visible outcome. The README status
  and the roadmap show the milestone as delivered and say what comes next.
  Every product, operations, reference and architecture page its issues
  changed describes the delivered behaviour.
- Sweep the rest. Read every other living document reachable from
  [the index](../README.md) against current `main`, and correct whatever has
  stopped being true, whichever change made it stale.

A pull request is the last one when every other issue in the milestone is
closed. If the exit checks after it merges need another pull request, that one
becomes the last and repeats the pass for whatever it changes. The milestone is
closed only after its closing pass has merged. The pass follows the rules on
this page: it edits in place and adds no delivery history.

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
A run of related pages in the index is a nested list, one link per line in
alphabetical order. A document that is no longer needed is deleted.

`CHANGELOG.md` and `docs/README.md` merge by keeping both sides' lines
(`merge=union` in `.gitattributes`), so taking in `main` never stops on two
entries added at the same place. Read their diff after the merge, above all
when one side closed a milestone's section: an entry edited on both sides
comes out twice, and the check below reads only open sections.

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
that every relative link resolves, that the ADR index and the ADR files match
one to one with no shared numbers, that no heading outside an ADR names an
issue (such as `(#1070)`), that no living document other than the
changelog exceeds 6,000 words, and that every entry in an open changelog
section is one block without blank lines that ends with a full stop, with no
sentence starting in lower case straight after a closing bracket, the marks a
union merge leaves when it splices two entries. Run it locally with:

```text
uv run python scripts/verify_documentation_integrity.py
```

The fast tier also checks that the generated OpenAPI and configuration schemas
match the code, that `docs/examples/synthetic-config.json` loads, that every
direct dependency has a grant in [dependencies](dependencies.md), and that the
check table in [CONTRIBUTING.md](../../CONTRIBUTING.md#what-merging-proves)
matches the gate workflow. Everything else, including whether prose is still
true, is checked in review.
