from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import NamedTuple, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / ".github" / "renovate.json"


@pytest.fixture(scope="module")
def config() -> dict[str, object]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _effective_policy(
    config: dict[str, object],
    *,
    manager: str,
    package: str,
    current_version: str,
    update_type: str,
    dep_name: str | None = None,
) -> dict[str, object]:
    effective: dict[str, object] = {
        "automerge": config["automerge"],
        "dependencyDashboardApproval": False,
    }
    for rule in config["packageRules"]:
        if managers := rule.get("matchManagers"):
            if manager not in managers:
                continue
        if packages := rule.get("matchPackageNames"):
            if package not in packages:
                continue
        if dep_names := rule.get("matchDepNames"):
            if (dep_name or package) not in dep_names:
                continue
        if update_types := rule.get("matchUpdateTypes"):
            if update_type not in update_types:
                continue
        if version_pattern := rule.get("matchCurrentVersion"):
            negated = version_pattern.startswith("!/")
            pattern = version_pattern[2:-1] if negated else version_pattern[1:-1]
            matches = re.search(pattern, current_version) is not None
            if matches == negated:
                continue
        for field in (
            "automerge",
            "dependencyDashboardApproval",
            "groupName",
        ):
            if field in rule:
                effective[field] = rule[field]
    return effective


def test_supported_managers_and_bounded_schedule_are_explicit(
    config: dict[str, object],
) -> None:
    assert set(cast("list[str]", config["enabledManagers"])) == {
        "dockerfile",
        "github-actions",
        "npm",
        "pep621",
    }
    assert config["minimumReleaseAge"] == "7 days"
    assert config["prCreation"] == "immediate"
    assert config["prConcurrentLimit"] == 5
    assert config["branchConcurrentLimit"] == 5
    assert config["rangeStrategy"] == "pin"
    assert not config["ignoreTests"]
    assert config["platformAutomerge"]
    assert not config["automerge"]
    assert config["automergeType"] == "pr"
    assert config["automergeStrategy"] == "squash"


def test_only_named_low_risk_classes_enable_platform_automerge(
    config: dict[str, object],
) -> None:
    assert not config["automerge"]
    assert config["platformAutomerge"]
    assert not config["ignoreTests"]
    assert config["lockFileMaintenance"]["enabled"]
    assert config["lockFileMaintenance"]["automerge"]
    automerge_rules = {
        rule["description"] for rule in config["packageRules"] if rule.get("automerge")
    }
    assert automerge_rules == {
        "Group compatible frontend updates",
        "Group Python patch updates",
        "Group stable Python minor updates",
    }


def test_project_python_compatibility_range_is_not_managed(
    config: dict[str, object],
) -> None:
    ignored = [
        rule
        for rule in config["packageRules"]
        if rule.get("matchManagers") == ["pep621"]
        and rule.get("matchDepTypes") == ["requires-python"]
    ]
    assert len(ignored) == 1
    assert not ignored[0]["enabled"]


def test_npm_is_not_managed_independently_of_the_pinned_node(
    config: dict[str, object],
) -> None:
    """npm is whatever the pinned Node ships, so Renovate must not move it alone.

    ADR-0043 pins the pair, and the gate, the Dockerfile frontend stage, and
    `scripts/build_frontend.py` each assert the real `npm --version` rather than
    the manifest, so a standalone bump declares a toolchain nothing installs.
    The exactness sentinel below catches that once per Renovate run; this rule
    is what stops the run (#902).
    """
    ignored = [
        rule
        for rule in config["packageRules"]
        if rule.get("matchManagers") == ["npm"]
        and rule.get("matchDepTypes") == ["engines", "packageManager"]
    ]
    assert len(ignored) == 1
    assert ignored[0]["matchPackageNames"] == ["npm"]
    assert not ignored[0]["enabled"]


def test_only_major_and_replacement_updates_require_dashboard_approval(
    config: dict[str, object],
) -> None:
    manual = [
        rule
        for rule in config["packageRules"]
        if rule.get("dependencyDashboardApproval")
    ]
    assert {rule["description"] for rule in manual} == {
        "Require approval for major updates",
        "Require approval for dependency replacements",
    }


def test_protected_classes_override_earlier_automerge_rules(
    config: dict[str, object],
) -> None:
    rules = cast("list[dict[str, object]]", config["packageRules"])
    positions = {rule["description"]: index for index, rule in enumerate(rules)}
    protected = {
        "Prevent automatic merging of pre-1.0 minor updates",
        "Require approval for major updates",
        "Prevent automatic merging of infrastructure and workflow updates",
        "Prevent automatic merging of runtime, toolchain, and build-backend updates",
        "Prevent automatic merging of Playwright minor updates",
        "Require approval for dependency replacements",
        "Move the qualified and shipped interpreter and resolver in lockstep",
    }
    for description in protected:
        rule = config["packageRules"][positions[description]]
        assert not rule["automerge"]

    enabling_positions = [
        positions["Group compatible frontend updates"],
        positions["Group Python patch updates"],
        positions["Group stable Python minor updates"],
    ]
    assert all(
        positions[description] > max(enabling_positions) for description in protected
    )

    runtime_packages = next(
        set(rule["matchPackageNames"])
        for rule in config["packageRules"]
        if rule["description"]
        == "Prevent automatic merging of runtime, toolchain, and build-backend updates"
    )
    assert runtime_packages == {
        "@eslint/js",
        "@types/node",
        "@vitejs/plugin-react",
        "eslint",
        "eslint-plugin-react-hooks",
        "eslint-plugin-react-refresh",
        "globals",
        "jsdom",
        "node",
        "npm",
        "openapi-typescript",
        "python",
        "setuptools",
        "typescript",
        "typescript-eslint",
        "vite",
        "vitest",
    }


@pytest.mark.parametrize(
    ("manager", "package", "current", "update", "automerge", "approval"),
    (
        pytest.param(
            "npm", "react", "19.2.8", "minor", True, False, id="npm_react_minor"
        ),
        pytest.param(
            "npm",
            "@testing-library/react",
            "16.3.2",
            "patch",
            True,
            False,
            id="npm_testing_library_react_patch",
        ),
        pytest.param(
            "npm", "vite", "8.2.1", "minor", False, False, id="npm_vite_minor"
        ),
        pytest.param(
            "npm", "eslint", "10.8.1", "patch", False, False, id="npm_eslint_patch"
        ),
        pytest.param(
            "npm",
            "eslint-plugin-react-refresh",
            "0.5.3",
            "patch",
            False,
            False,
            id="npm_eslint_plugin_react_refresh_patch",
        ),
        pytest.param(
            "pep621",
            "playwright",
            "1.61.0",
            "patch",
            True,
            False,
            id="pep621_playwright_patch",
        ),
        pytest.param(
            "pep621",
            "playwright",
            "1.61.0",
            "minor",
            False,
            False,
            id="pep621_playwright_minor",
        ),
        pytest.param(
            "pep621",
            "fastapi",
            "0.116.1",
            "minor",
            False,
            False,
            id="pep621_fastapi_minor",
        ),
        pytest.param(
            "pep621",
            "pydantic",
            "2.13.4",
            "minor",
            True,
            False,
            id="pep621_pydantic_minor",
        ),
        pytest.param(
            "dockerfile",
            "python",
            "3.13.14",
            "patch",
            False,
            False,
            id="dockerfile_python_patch",
        ),
        pytest.param(
            "github-actions",
            "actions/checkout",
            "6.0.2",
            "patch",
            False,
            False,
            id="github_actions_checkout_patch",
        ),
        pytest.param(
            "npm", "react", "19.2.8", "major", False, True, id="npm_react_major"
        ),
        pytest.param(
            "npm",
            "react",
            "19.2.8",
            "replacement",
            False,
            True,
            id="npm_react_replacement",
        ),
    ),
)
def test_effective_update_classification_preserves_manual_boundaries(
    config: dict[str, object],
    manager: str,
    package: str,
    current: str,
    update: str,
    automerge: bool,
    approval: bool,
) -> None:
    effective = _effective_policy(
        config,
        manager=manager,
        package=package,
        current_version=current,
        update_type=update,
    )
    assert effective["automerge"] == automerge
    assert effective["dependencyDashboardApproval"] == approval


def test_application_manifests_and_toolchains_remain_exact() -> None:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        python = tomllib.load(stream)
    assert python["project"]["requires-python"] == ">=3.13,<3.15"
    for requirement in python["project"]["dependencies"]:
        assert "==" in requirement
    dev = python["dependency-groups"]["dev"]
    assert "setuptools==83.0.0" in dev
    for requirement in dev:
        assert "==" in requirement

    frontend = json.loads(
        (ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    )
    for section in ("dependencies", "devDependencies"):
        for requirement in frontend[section].values():
            assert re.search(r"^\d+\.\d+\.\d+$", requirement) is not None
    for requirement in frontend["overrides"].values():
        assert re.search(r"^\d+\.\d+\.\d+$", requirement) is not None
    assert frontend["engines"]["node"] == "24.21.0"
    assert frontend["engines"]["npm"] == "11.19.0"
    assert frontend["packageManager"] == "npm@11.19.0"


class _PinSite(NamedTuple):
    """One place a toolchain version is written down in the repository."""

    path: str
    pattern: str
    expected: int


# Every repository surface that declares a toolchain version, keyed by the
# logical tool. The gate's promise is that what was qualified is what ships,
# so all sites for one tool must state one version (#911). `count` pins the
# number of declarations, so a site that moves or changes shape fails loudly
# instead of silently dropping out of the comparison.
_TOOLCHAIN_PINS: dict[str, tuple[_PinSite, ...]] = {
    "python": (
        _PinSite(
            "Dockerfile", r"^FROM python:(\d+\.\d+\.\d+)-slim-bookworm@sha256:", 2
        ),
        _PinSite(
            ".github/workflows/public-gate.yml",
            r'^\s+python-version: "(\d+\.\d+\.\d+)"$',
            4,
        ),
    ),
    "uv": (
        _PinSite(
            "Dockerfile", r"^COPY --from=ghcr\.io/astral-sh/uv:(\d+\.\d+\.\d+)@", 1
        ),
        _PinSite(
            ".github/workflows/public-gate.yml", r'^\s+version: "(\d+\.\d+\.\d+)"$', 4
        ),
    ),
    "node": (
        _PinSite("Dockerfile", r"^FROM node:(\d+\.\d+\.\d+)-bookworm-slim@sha256:", 2),
        _PinSite("Dockerfile", r'test "\$\(node --version\)" = "v(\d+\.\d+\.\d+)"', 1),
        _PinSite(
            ".github/workflows/public-gate.yml",
            r'^\s+node-version: "(\d+\.\d+\.\d+)"$',
            4,
        ),
        _PinSite(
            ".github/workflows/public-gate.yml",
            r'test "\$\(node --version\)" = "v(\d+\.\d+\.\d+)"',
            2,
        ),
        _PinSite("frontend/package.json", r'"node": "(\d+\.\d+\.\d+)"', 1),
        _PinSite(
            "scripts/build_frontend.py", r'^NODE_VERSION = "v(\d+\.\d+\.\d+)"$', 1
        ),
    ),
    "npm": (
        _PinSite("Dockerfile", r'test "\$\(npm --version\)" = "(\d+\.\d+\.\d+)"', 1),
        _PinSite(
            ".github/workflows/public-gate.yml",
            r'test "\$\(npm --version\)" = "(\d+\.\d+\.\d+)"',
            2,
        ),
        _PinSite("frontend/package.json", r'"npm": "(\d+\.\d+\.\d+)"', 1),
        _PinSite(
            "frontend/package.json", r'"packageManager": "npm@(\d+\.\d+\.\d+)"', 1
        ),
        _PinSite("scripts/build_frontend.py", r'^NPM_VERSION = "(\d+\.\d+\.\d+)"$', 1),
    ),
    "setuptools": (_PinSite("pyproject.toml", r'"setuptools==(\d+\.\d+\.\d+)"', 2),),
}


@pytest.mark.parametrize("tool", sorted(_TOOLCHAIN_PINS))
def test_every_pin_site_for_a_tool_declares_one_version(tool: str) -> None:
    """The shipped artifact and the gate that qualifies it state one toolchain.

    The interpreter and the resolver are each pinned once for the container
    ADR-0001 ships and once for the gate that qualifies it; Node and npm add
    manifest, build-script, and inline-assert declarations. Nothing structural
    binds the sites, so parity is asserted here: an update that moves one site
    without the others is a red test instead of a quiet fact (#911).
    """
    observed: dict[str, set[str]] = {}
    for site in _TOOLCHAIN_PINS[tool]:
        text = (ROOT / site.path).read_text(encoding="utf-8")
        versions = re.findall(site.pattern, text, flags=re.MULTILINE)
        assert len(versions) == site.expected, (
            f"{site.path} declares {len(versions)} {tool} pins where "
            f"{site.expected} are registered; update _TOOLCHAIN_PINS for the "
            "moved or reshaped declaration"
        )
        observed[f"{site.path}#{site.pattern}"] = set(versions)
    distinct: set[str] = set()
    for versions_at_site in observed.values():
        distinct |= versions_at_site
    assert len(distinct) == 1, f"{tool} pin sites disagree: {observed}"


# The dependency names Renovate's managers report for the qualified pair, as
# the Dependency Dashboard (#610) lists them. The lockstep rule must match
# exactly these via `matchDepNames`: the bare name `uv` matches neither
# manager's dependency, which is how the pair originally split across two
# groups and drifted (#911).
_QUALIFIED_PAIR_DEPENDENCIES: tuple[tuple[str, str], ...] = (
    ("dockerfile", "python"),
    ("dockerfile", "ghcr.io/astral-sh/uv"),
    ("github-actions", "python"),
    ("github-actions", "astral-sh/uv"),
)


def test_qualified_pair_moves_as_one_manual_group(
    config: dict[str, object],
) -> None:
    """One Renovate branch moves all four interpreter and resolver pins.

    Grouping is co-scheduling, not atomicity: a group pull request can still
    carry a half-eligible pair, and the parity sentinel above is what refuses
    it at the merge boundary. This test keeps the compliant path in existence
    — without one shared group, every Renovate proposal for the pair would
    arrive pre-split and permanently red (#911).
    """
    for manager, dep_name in _QUALIFIED_PAIR_DEPENDENCIES:
        effective = _effective_policy(
            config,
            manager=manager,
            package=dep_name,
            current_version="3.14.7",
            update_type="minor",
            dep_name=dep_name,
        )
        assert effective["groupName"] == "qualified runtime toolchain", (
            manager,
            dep_name,
        )
        assert not effective["automerge"]
        assert not effective["dependencyDashboardApproval"]
