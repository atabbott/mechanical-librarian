from __future__ import annotations

import tomllib
import unittest
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
PUBLIC_GATE = WORKFLOWS / "public-gate.yml"
ACCEPTANCE_REGISTER = ROOT / "osv-scanner.toml"

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


class ScanningWorkflowHardeningTests(unittest.TestCase):
    def test_every_workflow_pins_every_action_to_a_commit(self) -> None:
        for workflow in sorted(WORKFLOWS.glob("*.yml")):
            source = workflow.read_text(encoding="utf-8")
            references = [
                line.strip().removeprefix("uses:").strip()
                for line in source.splitlines()
                if line.strip().startswith("uses:")
            ]
            for reference in references:
                with self.subTest(workflow=workflow.name, reference=reference):
                    _, separator, revision = reference.partition("@")
                    self.assertEqual(separator, "@")
                    pinned = revision.split()[0]
                    self.assertEqual(len(pinned), 40)
                    self.assertTrue(all(value in "0123456789abcdef" for value in pinned))
                    self.assertIn("#", revision, "a pin carries its version comment")

    def test_no_workflow_checkout_persists_credentials(self) -> None:
        for workflow in sorted(WORKFLOWS.glob("*.yml")):
            source = workflow.read_text(encoding="utf-8")
            with self.subTest(workflow=workflow.name):
                self.assertEqual(
                    source.count("persist-credentials: false"),
                    source.count("actions/checkout@"),
                )

    def test_every_workflow_defaults_to_read_only_contents(self) -> None:
        for workflow in sorted(WORKFLOWS.glob("*.yml")):
            source = workflow.read_text(encoding="utf-8")
            with self.subTest(workflow=workflow.name):
                self.assertIn("\npermissions:\n  contents: read\n\n", source)

    def test_no_workflow_grants_write_authority_over_the_repository(self) -> None:
        for workflow in sorted(WORKFLOWS.glob("*.yml")):
            source = workflow.read_text(encoding="utf-8")
            for scope in ("contents: write", "pull-requests: write", "id-token: write"):
                with self.subTest(workflow=workflow.name, scope=scope):
                    self.assertNotIn(scope, source)


if __name__ == "__main__":
    unittest.main()
