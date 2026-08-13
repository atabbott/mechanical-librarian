# Dependency updates

Mechanical Librarian uses the Mend-hosted Renovate GitHub App to discover,
group, and prepare dependency updates. The repository configuration in
`.github/renovate.json` bounds that preparation authority. Renovate has no
direct-write or ruleset-bypass authority. GitHub-native pull-request auto-merge
may merge only the low-risk update classes named below after the protected
branch accepts the exact head. Other updates remain maintainer-merged. Do not
duplicate version or security update pull requests with Dependabot.

## Reproducibility boundary

`pyproject.toml` and `frontend/package.json` pin direct dependencies.
`uv.lock` and `frontend/package-lock.json` record the exact transitive versions
and integrity data installed by development, CI, container builds, and production.
CI continues to use `uv lock --check`, locked or frozen uv operations, and
`npm ci`; Renovate never makes an uncommitted production resolution current.
The PEP 621 `project.requires-python` field is a supported interpreter
compatibility range rather than a dependency pin, so Renovate does not manage it.

Build backends, Python, Node, npm, uv, container image digests, GitHub Action
SHAs, and Playwright remain exact pins because the application lockfiles do not
fully govern them. Exact direct pins also prevent lockfile maintenance from
quietly moving a direct dependency through a differently classified update.

## Update policy

Renovate checks in one weekly window, waits seven days after an ordinary
release, limits concurrent branches and pull requests to five, creates at most
two pull requests per hour, and groups compatible updates by ecosystem. GitHub
vulnerability alerts skip the ordinary schedule and use the lowest fixed
version, but retain the same merge policy.

Renovate automatically prepares pull requests and requests GitHub-native squash
auto-merge for:

- ordinary Python and npm patch updates, including Playwright patches;
- stable Python and npm library minor updates; and
- scheduled lockfile-only maintenance refreshes.

The default branch requires a pull request and the current exact head to pass
`fast-source`, `exact-installed`, and `protected-browser`. It also requires the
head to be current with `main`. Renovate receives no bypass. GitHub holds an
eligible pull request until those rules pass, then performs the configured
squash merge without depending on a maintainer workstation.

Renovate prepares a pull request automatically, but does not request auto-merge,
for:

- pre-1.0 minor updates;
- Docker images and GitHub Actions;
- Python, Node, npm, and uv runtimes or toolchains;
- the current frontend compiler, bundler, linter, generator, test runner, test
  environment, and build plugins;
- Python build backends; and
- Playwright minor updates.

Major updates and dependency replacements require Dependency Dashboard approval
before Renovate prepares their pull requests, and never auto-merge. Newly
introduced dependencies, licensing changes, and changes to network, credential,
persistence, parser, or production authority remain tracked maintainer work.

A failing, pending, stale, conflicted, or manually edited pull request cannot
auto-merge. Close an unsuitable update or disable it in the repository
configuration with a package-specific rationale; never weaken a test or safety
gate to admit it. Non-auto-merge Renovate pull requests use connector review and
the maintainer-owned exact-head controller.

## Tracking and recovery

Only pull requests whose GitHub author is `renovate[bot]` are exempt from the
normal closing-issue requirement. Titles, labels, branch names, and commit
authors do not confer that exception. Human-authored dependency changes remain
tracked focused work.

To pause all automated updates, set top-level `enabled` to `false` in the
Renovate configuration through an ordinary tracked pull request. For urgent
containment, a repository administrator may suspend or uninstall the GitHub
App, then record and reconcile that external action. Existing open Renovate
pull requests can be closed without merging. Reverting an already merged
dependency update follows the normal tracked defect workflow and restores a
previously qualified lockfile resolution.

See the [dependency automation security review](dependency-updates-security.md)
for the hosted-service trust boundary and least-authority requirements.
