# Dependency updates

Mechanical Librarian uses the Mend-hosted Renovate GitHub App to discover,
group, and prepare dependency updates. The repository configuration in
`.github/renovate.json` bounds that preparation authority. Renovate has no
merge authority; every update uses the maintainer-owned exact-head merge
controller. Do not duplicate version or security update pull requests with
Dependabot.

## Reproducibility boundary

`pyproject.toml` and `frontend/package.json` pin direct dependencies.
`uv.lock` and `frontend/package-lock.json` record the exact transitive versions
and integrity data installed by development, CI, container builds, and production.
CI continues to use `uv lock --check`, locked or frozen uv operations, and
`npm ci`; Renovate never makes an uncommitted production resolution current.

Build backends, Python, Node, npm, uv, container image digests, and GitHub
Action SHAs remain exact pins because the application lockfiles do not govern
them. Playwright also remains approval-only because an update changes the
separately verified browser payload.

## Update policy

Renovate checks in one weekly window, waits seven days after an ordinary
release, limits concurrent branches and pull requests to three, and groups
compatible updates by ecosystem. GitHub vulnerability alerts skip the ordinary
schedule and use the lowest fixed version, but retain the same merge policy.

Renovate automatically prepares and groups pull requests for:

- Python and npm patch updates;
- stable Python and npm minor updates; and
- the scheduled lockfile-only maintenance refresh.

All Renovate and platform-native auto-merge paths are disabled. Passing public
checks is evidence, not merge authorization. Each pull request remains open
until the maintainer-owned controller binds its exact head to current checks,
connector review, repository policy, and final race-sensitive preflight.

Dashboard approval and maintainer review are required for:

- major updates and pre-1.0 minor updates;
- Docker images and GitHub Actions;
- Python, Node, npm, and uv toolchains, build backends, and Playwright;
- replacement packages, newly introduced dependencies, licensing changes, or
  any update that changes network, credential, persistence, parser, or
  production authority.

A failing, pending, stale, conflicted, or manually edited pull request does not
qualify for controller merge. Close an unsuitable update or disable it in the
repository configuration with a package-specific rationale; never weaken a
test or safety gate to admit it.

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
