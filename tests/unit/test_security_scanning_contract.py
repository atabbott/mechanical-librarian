from __future__ import annotations

import tempfile
import tomllib
import unittest
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
PUBLIC_GATE = WORKFLOWS / "public-gate.yml"
ACCEPTANCE_REGISTER = ROOT / "osv-scanner.toml"

# GitHub reads both extensions from `.github/workflows`. The hardening sweep
# below exists to constrain workflows added later, so it must walk both: a
# sweep that silently skips half the namespace reads as coverage while
# providing none.
WORKFLOW_SUFFIXES = frozenset({".yml", ".yaml"})

# Write scopes a workflow may hold, keyed by (workflow file, scope) and valued
# by the reason. Every other `write` value fails the sweep, so this is the one
# place a write scope can be granted -- named, reasoned, and narrow, the same
# discipline `osv-scanner.toml` applies to an advisory.
#
# Empty today: no workflow in this repository needs write authority of any
# kind. Issue #674 adds `security-events: write` for its CodeQL job, which
# cannot upload results to code scanning without it, and records that reason
# here when it lands.
PERMITTED_WRITE_SCOPES: Mapping[tuple[str, str], str] = {}

# An acceptance is a deadline, not a decision to live with an advisory. Ninety
# days is long enough to wait out an upstream release and short enough that the
# entry is revisited within one milestone.
MAX_ACCEPTANCE_DAYS = 90
# Long enough that "wontfix" or "later" cannot pass for a reason.
MIN_REASON_CHARACTERS = 20
# The only table the register accepts. `PackageOverrides` and the scanner's
# other configuration tables can silence a package or an entire ecosystem
# without naming an advisory or an expiry, which is precisely the repository-
# wide loosening this register exists to prevent.
PERMITTED_TABLES = frozenset({"IgnoredVulns"})
PERMITTED_ENTRY_KEYS = frozenset({"id", "ignoreUntil", "reason"})


def register_failures(document: Mapping[str, object], today: date) -> tuple[str, ...]:
    """Report every way a parsed acceptance register violates the time box."""

    failures: list[str] = []
    for table in sorted(set(document) - PERMITTED_TABLES):
        failures.append(f"{table}: only [[IgnoredVulns]] may appear in the register")
    entries = document.get("IgnoredVulns", [])
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
        return (*failures, "IgnoredVulns: expected an array of tables")
    for position, entry in enumerate(entries):
        name = f"IgnoredVulns[{position}]"
        if not isinstance(entry, Mapping):
            failures.append(f"{name}: expected a table")
            continue
        identifier = entry.get("id")
        if isinstance(identifier, str) and identifier.strip():
            name = f"IgnoredVulns[{identifier}]"
        else:
            failures.append(f"{name}: id must name one advisory")
        for key in sorted(set(entry) - PERMITTED_ENTRY_KEYS):
            failures.append(f"{name}: unexpected key {key}")
        reason = entry.get("reason")
        if not isinstance(reason, str) or len(reason.strip()) < MIN_REASON_CHARACTERS:
            failures.append(
                f"{name}: reason must state why the advisory cannot be fixed now"
            )
        expiry = entry.get("ignoreUntil")
        # `tomllib` returns `datetime.date` for a bare date and
        # `datetime.datetime` for a date-time; `datetime` is a `date` subclass,
        # so it is excluded explicitly.
        if not isinstance(expiry, date) or isinstance(expiry, datetime):
            failures.append(f"{name}: ignoreUntil must be a bare TOML date")
            continue
        if expiry <= today:
            failures.append(f"{name}: acceptance expired on {expiry.isoformat()}")
        elif expiry - today > timedelta(days=MAX_ACCEPTANCE_DAYS):
            failures.append(
                f"{name}: acceptance runs to {expiry.isoformat()},"
                f" more than {MAX_ACCEPTANCE_DAYS} days out"
            )
    return tuple(failures)


def _register(entry: str) -> Mapping[str, object]:
    return tomllib.loads(f"[[IgnoredVulns]]\n{entry}\n")


_TODAY = date(2026, 8, 15)
_REASON = 'reason = "No fixed release exists upstream; tracked in issue 700."'


class AcceptanceRegisterTests(unittest.TestCase):
    def test_an_accepted_advisory_inside_the_time_box_passes(self) -> None:
        document = _register(
            f'id = "GHSA-0000-0000-0001"\nignoreUntil = 2026-09-15\n{_REASON}'
        )
        self.assertEqual(register_failures(document, _TODAY), ())

    def test_an_expired_acceptance_fails(self) -> None:
        document = _register(
            f'id = "GHSA-0000-0000-0001"\nignoreUntil = 2026-08-14\n{_REASON}'
        )
        self.assertEqual(
            register_failures(document, _TODAY),
            ("IgnoredVulns[GHSA-0000-0000-0001]: acceptance expired on 2026-08-14",),
        )

    def test_an_acceptance_expiring_today_has_already_expired(self) -> None:
        document = _register(
            f'id = "GHSA-0000-0000-0001"\nignoreUntil = 2026-08-15\n{_REASON}'
        )
        self.assertEqual(len(register_failures(document, _TODAY)), 1)

    def test_an_open_ended_acceptance_fails(self) -> None:
        document = _register(
            f'id = "GHSA-0000-0000-0001"\nignoreUntil = 2027-08-15\n{_REASON}'
        )
        failures = register_failures(document, _TODAY)
        self.assertEqual(len(failures), 1)
        self.assertIn("more than 90 days out", failures[0])

    def test_an_acceptance_without_a_stated_reason_fails(self) -> None:
        document = _register(
            'id = "GHSA-0000-0000-0001"\nignoreUntil = 2026-09-15\nreason = "later"'
        )
        failures = register_failures(document, _TODAY)
        self.assertEqual(len(failures), 1)
        self.assertIn("reason must state", failures[0])

    def test_an_acceptance_without_an_expiry_fails(self) -> None:
        document = _register(f'id = "GHSA-0000-0000-0001"\n{_REASON}')
        failures = register_failures(document, _TODAY)
        self.assertEqual(len(failures), 1)
        self.assertIn("bare TOML date", failures[0])

    def test_a_repository_wide_loosening_fails(self) -> None:
        document = tomllib.loads('[[PackageOverrides]]\nname = "starlette"\nignore = true\n')
        self.assertEqual(
            register_failures(document, _TODAY),
            ("PackageOverrides: only [[IgnoredVulns]] may appear in the register",),
        )

    def test_the_committed_register_holds_no_expired_or_open_ended_acceptance(
        self,
    ) -> None:
        document = tomllib.loads(ACCEPTANCE_REGISTER.read_text(encoding="utf-8"))
        self.assertEqual(register_failures(document, date.today()), ())


class DependencyAuditContractTests(unittest.TestCase):
    def test_the_public_gate_audits_the_lockfile_through_the_register(self) -> None:
        workflow = PUBLIC_GATE.read_text(encoding="utf-8")
        self.assertIn("  dependency-audit:\n", workflow)
        for argument in (
            "scan source\n",
            "--lockfile=uv.lock\n",
            f"--config={ACCEPTANCE_REGISTER.name}\n",
        ):
            with self.subTest(argument=argument.strip()):
                self.assertIn(argument, workflow)
        # Mounted read-only, so the audit can never rewrite the tree it audits.
        self.assertIn('--volume "$PWD:/src:ro"', workflow)

    def test_the_scanner_is_pinned_by_image_digest(self) -> None:
        workflow = PUBLIC_GATE.read_text(encoding="utf-8")
        marker = "ghcr.io/google/osv-scanner@sha256:"
        self.assertIn(marker, workflow)
        digest = workflow.split(marker, 1)[1].split()[0]
        self.assertEqual(len(digest), 64)
        self.assertTrue(all(value in "0123456789abcdef" for value in digest))


def workflow_sources(directory: Path) -> tuple[tuple[str, str], ...]:
    """Every workflow in one directory, under either accepted extension."""

    paths = sorted(
        (path for path in directory.iterdir() if path.suffix in WORKFLOW_SUFFIXES),
        key=lambda path: path.name,
    )
    return tuple((path.name, path.read_text(encoding="utf-8")) for path in paths)


def _without_comment(text: str) -> str:
    # Every scalar these workflows declare is unquoted, so `#` starts a comment.
    return text.split("#", 1)[0].strip()


def _inline_permissions(inline: str) -> tuple[tuple[str, str], ...]:
    if inline.startswith("{"):
        body = inline.strip("{}").strip()
        pairs: list[tuple[str, str]] = []
        for part in body.split(","):
            scope, separator, value = part.partition(":")
            if separator:
                pairs.append((scope.strip(), value.strip()))
        return tuple(pairs)
    # `read-all` and `write-all` set every scope at once; `all` names that.
    return (("all", inline.removesuffix("-all")),)


def declared_permissions(source: str) -> tuple[tuple[int, str, str], ...]:
    """Every (indent, scope, value) a workflow declares, at any level."""

    declared: list[tuple[int, str, str]] = []
    lines = source.splitlines()
    for position, line in enumerate(lines):
        if not line.strip().startswith("permissions:"):
            continue
        indent = len(line) - len(line.lstrip())
        inline = _without_comment(line.strip().removeprefix("permissions:"))
        if inline:
            declared.extend((indent, scope, value) for scope, value in _inline_permissions(inline))
            continue
        for following in lines[position + 1 :]:
            if not following.strip():
                continue
            if len(following) - len(following.lstrip()) <= indent:
                break
            scope, separator, value = _without_comment(following).partition(":")
            if separator:
                declared.append((indent, scope.strip(), value.strip()))
    return tuple(declared)


def action_pin_failures(name: str, source: str) -> tuple[str, ...]:
    failures: list[str] = []
    for line in source.splitlines():
        # A step may write `uses:` on its own line or as the list item itself.
        stripped = line.strip().removeprefix("-").strip()
        if not stripped.startswith("uses:"):
            continue
        reference = stripped.removeprefix("uses:").strip()
        _, separator, revision = reference.partition("@")
        pinned = revision.split()[0] if separator and revision.split() else ""
        if len(pinned) != 40 or any(value not in "0123456789abcdef" for value in pinned):
            failures.append(f"{name}: {reference} is not pinned to a commit")
        elif "#" not in revision:
            failures.append(f"{name}: {reference} carries no version comment")
    return tuple(failures)


def checkout_credential_failures(name: str, source: str) -> tuple[str, ...]:
    checkouts = source.count("actions/checkout@")
    withheld = source.count("persist-credentials: false")
    if checkouts != withheld:
        return (f"{name}: {checkouts} checkouts but {withheld} withhold credentials",)
    return ()


def default_permission_failures(name: str, source: str) -> tuple[str, ...]:
    top = tuple((scope, value) for indent, scope, value in declared_permissions(source) if indent == 0)
    if top != (("contents", "read"),):
        return (f"{name}: workflow permissions must be exactly contents: read",)
    return ()


def write_permission_failures(
    name: str,
    source: str,
    permitted: Mapping[tuple[str, str], str] = PERMITTED_WRITE_SCOPES,
) -> tuple[str, ...]:
    """Reject every `write` value rather than enumerating known scopes."""

    failures: list[str] = []
    for indent, scope, value in declared_permissions(source):
        if value != "write" or permitted.get((name, scope)):
            continue
        level = "workflow" if indent == 0 else "job"
        failures.append(f"{name}: {level} permissions grant {scope}: write")
    return tuple(failures)


class WorkflowHardeningSweepTests(unittest.TestCase):
    """The sweep constrains workflows added later, so it is proven to bite."""

    def _probe(self, name: str, body: str) -> tuple[str, str]:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (directory / name).write_text(body, encoding="utf-8")
        found = workflow_sources(directory)
        self.assertEqual(len(found), 1)
        return found[0]

    def test_the_sweep_discovers_both_workflow_extensions(self) -> None:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (directory / "first.yml").write_text("name: first\n", encoding="utf-8")
        (directory / "second.yaml").write_text("name: second\n", encoding="utf-8")
        (directory / "notes.md").write_text("ignored\n", encoding="utf-8")
        self.assertEqual(
            [name for name, _ in workflow_sources(directory)],
            ["first.yml", "second.yaml"],
        )

    def test_a_yaml_workflow_with_a_mutable_action_reference_is_flagged(self) -> None:
        name, source = self._probe(
            "mutable.yaml", "jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n"
        )
        self.assertEqual(len(action_pin_failures(name, source)), 1)

    def test_a_pin_without_its_version_comment_is_flagged(self) -> None:
        pinned = "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5"
        name, source = self._probe("bare.yaml", f"jobs:\n  a:\n    steps:\n      - uses: {pinned}\n")
        self.assertEqual(len(action_pin_failures(name, source)), 1)

    def test_a_yaml_workflow_that_persists_checkout_credentials_is_flagged(self) -> None:
        name, source = self._probe(
            "persisting.yaml",
            "jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n",
        )
        self.assertEqual(len(checkout_credential_failures(name, source)), 1)

    def test_every_write_scope_is_rejected_not_only_the_familiar_ones(self) -> None:
        for scope in (
            "issues",
            "checks",
            "packages",
            "id-token",
            "pages",
            "deployments",
            "security-events",
            "contents",
        ):
            with self.subTest(scope=scope):
                name, source = self._probe(
                    "scoped.yaml",
                    f"permissions:\n  contents: read\n\njobs:\n  a:\n    permissions:\n      {scope}: write\n",
                )
                self.assertEqual(
                    write_permission_failures(name, source),
                    (f"scoped.yaml: job permissions grant {scope}: write",),
                )

    def test_the_write_all_shorthand_is_rejected(self) -> None:
        name, source = self._probe("shorthand.yaml", "permissions: write-all\n")
        self.assertEqual(
            write_permission_failures(name, source),
            ("shorthand.yaml: workflow permissions grant all: write",),
        )

    def test_the_read_all_shorthand_is_accepted(self) -> None:
        name, source = self._probe("shorthand.yaml", "permissions: read-all\n")
        self.assertEqual(write_permission_failures(name, source), ())

    def test_a_named_allow_list_entry_admits_exactly_its_own_scope(self) -> None:
        name, source = self._probe(
            "future.yaml",
            "permissions:\n  contents: read\n\njobs:\n  a:\n    permissions:\n      security-events: write\n      issues: write\n",
        )
        permitted = {("future.yaml", "security-events"): "CodeQL uploads results."}
        self.assertEqual(
            write_permission_failures(name, source, permitted),
            ("future.yaml: job permissions grant issues: write",),
        )

    def test_a_workflow_without_read_only_defaults_is_flagged(self) -> None:
        name, source = self._probe("open.yaml", "permissions:\n  contents: write\n")
        self.assertEqual(len(default_permission_failures(name, source)), 1)


class CommittedWorkflowHardeningTests(unittest.TestCase):
    def test_the_sweep_covers_the_repository_workflows(self) -> None:
        self.assertEqual(
            [name for name, _ in workflow_sources(WORKFLOWS)], ["public-gate.yml"]
        )

    def test_every_workflow_satisfies_the_hardening_floor(self) -> None:
        for name, source in workflow_sources(WORKFLOWS):
            for check in (
                action_pin_failures,
                checkout_credential_failures,
                default_permission_failures,
                write_permission_failures,
            ):
                with self.subTest(workflow=name, check=check.__name__):
                    self.assertEqual(check(name, source), ())

    def test_every_permitted_write_scope_names_a_workflow_scope_and_reason(self) -> None:
        # Empty today. An entry may only ever be added with its reason stated.
        for (workflow, scope), reason in PERMITTED_WRITE_SCOPES.items():
            with self.subTest(workflow=workflow, scope=scope):
                self.assertTrue(workflow.strip() and scope.strip())
                self.assertGreaterEqual(len(reason.strip()), MIN_REASON_CHARACTERS)


if __name__ == "__main__":
    unittest.main()
