from __future__ import annotations

import tempfile
import tomllib
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

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


def test_an_accepted_advisory_inside_the_time_box_passes() -> None:
    document = _register(
        f'id = "GHSA-0000-0000-0001"\nignoreUntil = 2026-09-15\n{_REASON}'
    )
    assert register_failures(document, _TODAY) == ()


def test_an_expired_acceptance_fails() -> None:
    document = _register(
        f'id = "GHSA-0000-0000-0001"\nignoreUntil = 2026-08-14\n{_REASON}'
    )
    assert register_failures(document, _TODAY) == (
        "IgnoredVulns[GHSA-0000-0000-0001]: acceptance expired on 2026-08-14",
    )


def test_an_acceptance_expiring_today_has_already_expired() -> None:
    document = _register(
        f'id = "GHSA-0000-0000-0001"\nignoreUntil = 2026-08-15\n{_REASON}'
    )
    assert len(register_failures(document, _TODAY)) == 1


def test_an_open_ended_acceptance_fails() -> None:
    document = _register(
        f'id = "GHSA-0000-0000-0001"\nignoreUntil = 2027-08-15\n{_REASON}'
    )
    failures = register_failures(document, _TODAY)
    assert len(failures) == 1
    assert "more than 90 days out" in failures[0]


def test_an_acceptance_without_a_stated_reason_fails() -> None:
    document = _register(
        'id = "GHSA-0000-0000-0001"\nignoreUntil = 2026-09-15\nreason = "later"'
    )
    failures = register_failures(document, _TODAY)
    assert len(failures) == 1
    assert "reason must state" in failures[0]


def test_an_acceptance_without_an_expiry_fails() -> None:
    document = _register(f'id = "GHSA-0000-0000-0001"\n{_REASON}')
    failures = register_failures(document, _TODAY)
    assert len(failures) == 1
    assert "bare TOML date" in failures[0]


def test_a_repository_wide_loosening_fails() -> None:
    document = tomllib.loads(
        '[[PackageOverrides]]\nname = "starlette"\nignore = true\n'
    )
    assert register_failures(document, _TODAY) == (
        "PackageOverrides: only [[IgnoredVulns]] may appear in the register",
    )


def test_the_committed_register_holds_no_expired_or_open_ended_acceptance() -> None:
    document = tomllib.loads(ACCEPTANCE_REGISTER.read_text(encoding="utf-8"))
    assert register_failures(document, date.today()) == ()


@pytest.mark.parametrize(
    "argument",
    ("scan source\n", "--lockfile=uv.lock\n", f"--config={ACCEPTANCE_REGISTER.name}\n"),
    ids=("scan_source", "lockfile", "config"),
)
def test_the_public_gate_audits_the_lockfile_through_the_register(
    argument: str,
) -> None:
    workflow = PUBLIC_GATE.read_text(encoding="utf-8")
    assert "  dependency-audit:\n" in workflow
    assert argument in workflow
    # Mounted read-only, so the audit can never rewrite the tree it audits.
    assert '--volume "$PWD:/src:ro"' in workflow


def test_the_scanner_is_pinned_by_image_digest() -> None:
    workflow = PUBLIC_GATE.read_text(encoding="utf-8")
    marker = "ghcr.io/google/osv-scanner@sha256:"
    assert marker in workflow
    digest = workflow.split(marker, 1)[1].split()[0]
    assert len(digest) == 64
    assert all(value in "0123456789abcdef" for value in digest)


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
            declared.extend(
                (indent, scope, value) for scope, value in _inline_permissions(inline)
            )
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
        if len(pinned) != 40 or any(
            value not in "0123456789abcdef" for value in pinned
        ):
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
    top = tuple(
        (scope, value)
        for indent, scope, value in declared_permissions(source)
        if indent == 0
    )
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


# The sweep constrains workflows added later, so it is proven to bite.
def _probe(name: str, body: str) -> tuple[str, str]:
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        (directory / name).write_text(body, encoding="utf-8")
        found = workflow_sources(directory)
    assert len(found) == 1
    return found[0]


def test_the_sweep_discovers_both_workflow_extensions() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        (directory / "first.yml").write_text("name: first\n", encoding="utf-8")
        (directory / "second.yaml").write_text("name: second\n", encoding="utf-8")
        (directory / "notes.md").write_text("ignored\n", encoding="utf-8")
        assert [name for name, _ in workflow_sources(directory)] == [
            "first.yml",
            "second.yaml",
        ]


def test_a_yaml_workflow_with_a_mutable_action_reference_is_flagged() -> None:
    name, source = _probe(
        "mutable.yaml", "jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n"
    )
    assert len(action_pin_failures(name, source)) == 1


def test_a_pin_without_its_version_comment_is_flagged() -> None:
    pinned = "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5"
    name, source = _probe(
        "bare.yaml", f"jobs:\n  a:\n    steps:\n      - uses: {pinned}\n"
    )
    assert len(action_pin_failures(name, source)) == 1


def test_a_yaml_workflow_that_persists_checkout_credentials_is_flagged() -> None:
    name, source = _probe(
        "persisting.yaml",
        "jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n",
    )
    assert len(checkout_credential_failures(name, source)) == 1


@pytest.mark.parametrize(
    "scope",
    (
        "issues",
        "checks",
        "packages",
        "id-token",
        "pages",
        "deployments",
        "security-events",
        "contents",
    ),
    ids=(
        "issues",
        "checks",
        "packages",
        "id_token",
        "pages",
        "deployments",
        "security_events",
        "contents",
    ),
)
def test_every_write_scope_is_rejected_not_only_the_familiar_ones(scope: str) -> None:
    name, source = _probe(
        "scoped.yaml",
        f"permissions:\n  contents: read\n\njobs:\n  a:\n    permissions:\n      {scope}: write\n",
    )
    assert write_permission_failures(name, source) == (
        f"scoped.yaml: job permissions grant {scope}: write",
    )


def test_the_write_all_shorthand_is_rejected() -> None:
    name, source = _probe("shorthand.yaml", "permissions: write-all\n")
    assert write_permission_failures(name, source) == (
        "shorthand.yaml: workflow permissions grant all: write",
    )


def test_the_read_all_shorthand_is_accepted() -> None:
    name, source = _probe("shorthand.yaml", "permissions: read-all\n")
    assert write_permission_failures(name, source) == ()


def test_a_named_allow_list_entry_admits_exactly_its_own_scope() -> None:
    name, source = _probe(
        "future.yaml",
        "permissions:\n  contents: read\n\njobs:\n  a:\n    permissions:\n      security-events: write\n      issues: write\n",
    )
    permitted = {("future.yaml", "security-events"): "CodeQL uploads results."}
    assert write_permission_failures(name, source, permitted) == (
        "future.yaml: job permissions grant issues: write",
    )


def test_a_workflow_without_read_only_defaults_is_flagged() -> None:
    name, source = _probe("open.yaml", "permissions:\n  contents: write\n")
    assert len(default_permission_failures(name, source)) == 1


def test_the_sweep_covers_the_repository_workflows() -> None:
    assert [name for name, _ in workflow_sources(WORKFLOWS)] == ["public-gate.yml"]


@pytest.mark.parametrize(
    "check",
    (
        action_pin_failures,
        checkout_credential_failures,
        default_permission_failures,
        write_permission_failures,
    ),
    ids=(
        "action_pin",
        "checkout_credential",
        "default_permission",
        "write_permission",
    ),
)
def test_every_workflow_satisfies_the_hardening_floor(
    check: Callable[[str, str], tuple[str, ...]],
) -> None:
    for name, source in workflow_sources(WORKFLOWS):
        assert check(name, source) == ()


def test_every_permitted_write_scope_names_a_workflow_scope_and_reason() -> None:
    # Empty today, so the loop stays a loop: parameterizing an empty mapping
    # would collect no case at all and retire the guard silently.
    for (workflow, scope), reason in PERMITTED_WRITE_SCOPES.items():
        assert workflow.strip() and scope.strip()
        assert len(reason.strip()) >= MIN_REASON_CHARACTERS
