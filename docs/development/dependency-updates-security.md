# Dependency automation security review

## Boundary

The Mend-hosted Renovate GitHub App is an external repository-maintenance
actor. It reads dependency manifests, lockfiles, Dockerfiles, GitHub workflows,
release metadata, and GitHub vulnerability alerts. It may create branches,
commits, issues, and pull requests and may merge only the update classes
authorized by `.github/renovate.json`.

The App receives no Mechanical Librarian application credential, owner
password, target-service credential, reviewer credential, production path,
database, media, backup, provider input, deployment secret, package-registry
secret, or production write authority. All current dependencies are public, so
no Renovate host rule or Mend-managed secret is required.

## Threats and controls

Compromise or misclassification by the hosted service could propose untrusted
third-party code or an update outside maintainer intent. The protected assets
are repository integrity, CI execution, reproducible production artifacts, and
the maintainer's review boundary.

The current controls are:

- repository configuration admits only PEP 621/uv, npm, Dockerfile, and GitHub
  Actions managers;
- ordinary releases age for seven days before a pull request;
- manifests bound compatibility while exact lockfiles and image/Action pins
  make the proposed artifact reviewable;
- auto-merge is limited to ranged patch updates, stable ranged minor updates,
  and lockfile maintenance;
- infrastructure, workflows, major and pre-1.0 minor updates, Playwright, and
  setuptools require dashboard approval;
- platform-native auto-merge is disabled and the bot must observe successful
  exact-head checks;
- CI installs without npm lifecycle scripts, verifies lock currency, audits
  npm dependencies and licences, builds deterministically, and exercises the
  exact installed Python and protected browser artifacts;
- Renovate pull requests have no Actions secrets, production credentials, or
  deployment authority.

Repository policy cannot defend against a fully compromised GitHub App that
uses its granted repository permissions outside Renovate's documented
behavior. Restrict the App installation to this repository, review its GitHub
permission changes before accepting them, and suspend or uninstall it on any
unexpected branch, workflow, configuration, or merge activity.

## Failure and recovery

Registry unavailability, an unsupported manifest, a lockfile conflict, a
failed update command, or a failing check leaves the update unmerged for
maintainer inspection. The supported recovery is to close or rebase the pull
request, correct the repository configuration through tracked work, or suspend
the App. No dependency automation failure authorizes a direct `main` write,
test bypass, production-service access, or unreviewed compatibility expansion.
