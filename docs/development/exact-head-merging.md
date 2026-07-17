# Public CI and Exact-Head Merge Boundary

## Purpose

The tracked repository supplies a reproducible public CI gate. It does not
supply, authenticate, or authorize the exact-head merge controller. This
separation prevents pull-request content from becoming part of the authority
that decides whether that same content may be merged.

The authoritative merge controller, its validator programs, its workflow
definitions, its evidence schemas, and its terminal `main` containment control
are maintainer-owned local controls under ignored `.codex/**`. They are never
tracked in the candidate repository. Maintainers approve and pin those control
bytes independently of candidate content; a file supplied or modified by a
candidate is not trusted merely because it has a familiar name or path.

## Tracked public gate

`.github/workflows/public-gate.yml` runs for every pull request and every push
to `main`. It has read-only repository contents permission and:

1. checks out the exact event revision without persisting credentials;
2. uses commit-pinned GitHub Actions and a version-pinned `uv` installation;
3. rejects a stale lockfile with `uv lock --check`;
4. synchronizes all locked groups in frozen, non-editable mode;
5. runs the complete public test suite;
6. builds the distribution; and
7. exercises the installed `state-check` operator command against synthetic
   temporary state.

The concurrency key distinguishes pull-request work from `main` push events. A
new head may cancel obsolete work for the same pull request. Each push event
creates one non-cancelled run bound to that event's tip SHA; a later push cannot
cancel it. A batched multi-commit push does not create one run per included
commit and is outside the autonomous transaction contract. Autonomous merges
are emitted as individual pushes, and the local controller requires the
resulting merge SHA's post-`main` `public-gate` result before terminal success.

Public CI contains no service credential, private corpus, merge permission, or
branch-protection override. Private qualification remains local and may expose
only the redacted, aggregate exact-commit evidence permitted by its governing
contract.

## Local merge authority

The local-only controller is the final mutation boundary. Before it can merge,
it must independently bind the repository, pull request, base ref and commit,
exact reviewed head, live required checks, review state, linked issue, and
approved local review and qualification evidence. Immediately before mutation
it must repeat all race-sensitive gates and use GitHub's expected-head merge
guard. It must not offer an administrator, bypass, force, or
protection-weakening path.

After mutation, the local terminal control must prove that the returned merge
commit has the qualified base as its sole parent, is contained by remote
`main`, and is the tip SHA qualified by its own post-`main` `public-gate` run.
Ambiguous responses and post-mutation failures remain explicit recovery states;
they are not reported as success and are not automatically rolled back.

Candidate code is evidence to inspect, never merge authority. In particular:

- the candidate does not choose controller or validator paths;
- the candidate cannot approve new control digests or authenticate copied
  controls;
- tracked scripts and workflow files are not imported as trusted validators;
- local review artifacts and private qualification records remain ignored and
  bounded; and
- the merge credential is available only to the maintainer-owned control
  environment.

## Activation and external prerequisite

Autonomous merging requires strict server-enforced up-to-date checks,
approving reviews that are dismissed on new commits, resolved conversations,
administrative enforcement, no bypass actors, and disabled force pushes and
branch deletion. The repository's current private account plan does not make
the required enforcement available. The authoritative local controller must
therefore return structured `BLOCKED` before mutation. Issue #267 owns this
external plan and server-enforcement prerequisite; no delivery transaction may
change visibility, protection, or plan settings as an implied workaround.

The maintainer-owned controller, validators, evidence contracts, and terminal
containment under ignored `.codex/**` are qualified independently of candidate
content. The `Deliver slice <ID>` instruction now grants standing authority for
one bounded slice transaction, including exact-head merge and cleanup when all
prerequisites pass. Passing public CI alone is still not merge authorization,
and current strict-enforcement absence means the transaction stops before
mutation. Activation of the transaction policy does not claim automatic merge
mutation works in the repository today.
