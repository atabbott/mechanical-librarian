from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONFIG = json.loads((ROOT / ".github/renovate.json").read_bytes())


def test_managed_upgrades_use_strict_current_platform_automerge() -> None:
    assert set(CONFIG["enabledManagers"]) == {
        "dockerfile",
        "github-actions",
        "npm",
        "pep621",
        "custom.regex",
    }
    assert CONFIG["automerge"] is True
    assert CONFIG["platformAutomerge"] is True
    assert CONFIG["ignoreTests"] is False
    assert CONFIG["automergeType"] == "pr"
    assert CONFIG["automergeStrategy"] == "squash"
    assert CONFIG["rebaseWhen"] == "behind-base-branch"
    assert CONFIG["updateNotScheduled"] is True
    assert CONFIG["schedule"] == ["before 6am on Monday"]
    assert CONFIG["minimumReleaseAge"] == "7 days"
    assert CONFIG["internalChecksFilter"] == "strict"
    assert (
        CONFIG["prConcurrentLimit"],
        CONFIG["branchConcurrentLimit"],
        CONFIG["prHourlyLimit"],
    ) == (5, 5, 2)
    assert CONFIG["rangeStrategy"] == "pin"
    assert CONFIG["lockFileMaintenance"]["enabled"]
    assert CONFIG["lockFileMaintenance"]["automerge"]


def test_only_majors_replacements_compatibility_python_lines_playwright_and_hand_checked_pins_stay_manual() -> (
    None
):
    restrictions = [
        rule
        for rule in CONFIG["packageRules"]
        if rule.get("automerge") is False
        or rule.get("enabled") is False
        or rule.get("dependencyDashboardApproval")
    ]
    assert restrictions == [
        {
            "description": "Preserve the project Python compatibility range",
            "matchManagers": ["pep621"],
            "matchDepTypes": ["requires-python"],
            "enabled": False,
        },
        {
            "description": "Require approval for every major update",
            "matchUpdateTypes": ["major"],
            "automerge": False,
            "dependencyDashboardApproval": True,
        },
        {
            "description": "Require approval for a new Python minor or major version",
            "matchDepNames": ["python"],
            "matchUpdateTypes": ["minor", "major"],
            "automerge": False,
            "dependencyDashboardApproval": True,
        },
        {
            "description": "Require approval for dependency replacements",
            "matchUpdateTypes": ["replacement"],
            "automerge": False,
            "dependencyDashboardApproval": True,
        },
        {
            "description": (
                "Hold Playwright out of the Python packages batch until its newer "
                "Chromium is checked against the browser tests; approval opens its "
                "own pull request"
            ),
            "matchManagers": ["pep621"],
            "matchPackageNames": ["playwright"],
            "groupName": "Playwright",
            "automerge": False,
            "dependencyDashboardApproval": True,
        },
        {
            "description": (
                "List new FFmpeg and Codex releases on the dependency dashboard; "
                "approval opens a pull request whose Dockerfile checksums are "
                "refreshed by hand"
            ),
            "matchManagers": ["custom.regex"],
            "matchDepNames": ["ffmpeg", "@openai/codex"],
            "automerge": False,
            "dependencyDashboardApproval": True,
        },
    ]


@pytest.mark.parametrize(
    "names,group",
    [
        (["node"], "Node runtime"),
        (["python"], "Python runtime"),
        (["ghcr.io/astral-sh/uv"], "uv resolver"),
        (["npm"], "npm package manager"),
    ],
)
def test_tool_groups_cover_every_native_manager_name(
    names: list[str], group: str
) -> None:
    assert [
        rule["matchDepNames"]
        for rule in CONFIG["packageRules"]
        if rule.get("groupName") == group
    ] == [names]


def test_minor_and_patch_updates_batch_per_manager_under_the_tool_groups() -> None:
    """One weekly pull request per manager keeps the backlog inside the limits.

    One pull request per package exceeded the two-per-hour limit in the weekly
    window, so updates queued for weeks as rate-limited. Later package rules
    win, so the tool groups must follow the batches to keep their own branch.
    """
    rules = CONFIG["packageRules"]
    batches = [
        (index, rule["matchManagers"], rule["groupName"])
        for index, rule in enumerate(rules)
        if rule.get("matchUpdateTypes") == ["minor", "patch"] and "groupName" in rule
    ]
    assert [(managers, group) for _, managers, group in batches] == [
        (["pep621"], "Python packages"),
        (["npm"], "npm packages"),
        (["github-actions"], "GitHub Actions"),
    ]
    assert all(
        set(rules[index])
        == {"description", "matchManagers", "matchUpdateTypes", "groupName"}
        for index, _, _ in batches
    )
    last_batch = max(index for index, _, _ in batches)
    tool_groups = {
        "Node runtime",
        "Python runtime",
        "uv resolver",
        "npm package manager",
        "Playwright",
    }
    assert all(
        index > last_batch
        for index, rule in enumerate(rules)
        if rule.get("groupName") in tool_groups
    )


def test_application_manifests_remain_exact() -> None:
    python = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert python["project"]["requires-python"] == ">=3.13,<3.16"
    for requirement in (
        python["project"]["dependencies"]
        + python["dependency-groups"]["dev"]
        + python["build-system"]["requires"]
    ):
        assert "==" in requirement
    setuptools_pins = [
        requirement
        for requirements in (
            python["build-system"]["requires"],
            python["dependency-groups"]["dev"],
        )
        for requirement in requirements
        if requirement.startswith("setuptools")
    ]
    assert len(setuptools_pins) == 2
    assert len(set(setuptools_pins)) == 1
    assert re.fullmatch(r"setuptools==[0-9]+\.[0-9]+\.[0-9]+", setuptools_pins[0])
    frontend = json.loads((ROOT / "frontend/package.json").read_bytes())
    for section in ("dependencies", "devDependencies", "overrides", "engines"):
        assert all(
            re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
            for version in frontend[section].values()
        )
    assert frontend["packageManager"] == "npm@" + frontend["engines"]["npm"]


def test_every_runtime_pin_is_looked_up_where_its_image_pin_is() -> None:
    """Each tool's declarations see one release list, so one update moves all.

    The image registries publish no release dates; without the optional
    behaviour the release age would hold the image pins forever while the dated
    pins moved alone (#1177, #1448).
    """
    overrides = [
        {key: value for key, value in rule.items() if key != "description"}
        for rule in CONFIG["packageRules"]
        if "overrideDatasource" in rule
    ]
    assert overrides == [
        {
            "matchManagers": ["github-actions", "npm"],
            "matchDepNames": ["node"],
            "overrideDatasource": "docker",
            "overridePackageName": "node",
        },
        {
            "matchManagers": ["github-actions"],
            "matchDepNames": ["python"],
            "overrideDatasource": "docker",
            "overridePackageName": "python",
        },
        {
            "matchManagers": ["github-actions"],
            "matchDepNames": ["astral-sh/uv"],
            "overrideDatasource": "docker",
            "overrideDepName": "ghcr.io/astral-sh/uv",
            "overridePackageName": "ghcr.io/astral-sh/uv",
        },
    ]
    optional = [
        (rule["matchDepNames"], rule["groupName"])
        for rule in CONFIG["packageRules"]
        if rule.get("minimumReleaseAgeBehaviour") == "timestamp-optional"
    ]
    assert optional == [
        (["node"], "Node runtime"),
        (["python"], "Python runtime"),
        (["ghcr.io/astral-sh/uv"], "uv resolver"),
    ]
    assert all(
        rule.get("minimumReleaseAgeBehaviour") in {None, "timestamp-optional"}
        for rule in CONFIG["packageRules"]
    )
    assert "minimumReleaseAgeBehaviour" not in CONFIG


@pytest.mark.parametrize(
    "tool,manifest_pattern,docker_count",
    [
        ("node", r'^\s+node-version: "(\d+\.\d+\.\d+)"$', 2),
        ("python", r'^\s+python-version: "(\d+\.\d+\.\d+)"$', 2),
        ("uv", r'^\s+version: "(\d+\.\d+\.\d+)"$', 1),
    ],
)
def test_native_managed_toolchain_declarations_agree(
    tool: str, manifest_pattern: str, docker_count: int
) -> None:
    workflow = (ROOT / ".github/workflows/public-gate.yml").read_text()
    workflow_pins = re.findall(manifest_pattern, workflow, re.MULTILINE)
    assert len(workflow_pins) == 7
    prefix = "ghcr.io/astral-sh/uv" if tool == "uv" else tool
    docker_pins = re.findall(
        re.escape(prefix) + r":(\d+\.\d+\.\d+)", (ROOT / "Dockerfile").read_text()
    )
    assert len(docker_pins) == docker_count
    values = workflow_pins + docker_pins
    if tool == "node":
        values.append(
            json.loads((ROOT / "frontend/package.json").read_bytes())["engines"]["node"]
        )
    assert len(set(values)) == 1


def _custom_manager(dep_name: str) -> dict[str, object]:
    (manager,) = [
        manager
        for manager in CONFIG["customManagers"]
        if manager["depNameTemplate"] == dep_name
    ]
    return manager


def _matches(manager: dict[str, object], path: str) -> list[re.Match[str]]:
    (pattern,) = cast("list[str]", manager["matchStrings"])
    return list(re.finditer(pattern.replace("(?<", "(?P<"), (ROOT / path).read_text()))


def test_custom_managers_are_exactly_the_osv_ffmpeg_and_codex_pins() -> None:
    assert CONFIG["ignorePresets"] == ["customManagers:tsconfigNodeVersions"]
    assert sorted(m["depNameTemplate"] for m in CONFIG["customManagers"]) == [
        "@openai/codex",
        "ffmpeg",
        "ghcr.io/google/osv-scanner",
    ]


def test_osv_custom_manager_is_narrow_and_digest_bound() -> None:
    manager = _custom_manager("ghcr.io/google/osv-scanner")
    assert manager["managerFilePatterns"] == [
        r"/^\.github/workflows/(?:public-gate|scheduled-audit)\.yml$/"
    ]
    assert manager["datasourceTemplate"] == "docker"
    pins = set()
    for path in (
        ".github/workflows/public-gate.yml",
        ".github/workflows/scheduled-audit.yml",
    ):
        (match,) = _matches(manager, path)
        assert re.fullmatch(r"sha256:[a-f0-9]{64}", match["currentDigest"])
        pins.add(match[0])
    # Both workflows run one scanner, so Renovate moves them together.
    assert len(pins) == 1


@pytest.mark.parametrize(
    ("dep_name", "datasource", "version_source"),
    (
        ("ffmpeg", "github-tags", r"ARG FFMPEG_VERSION=([0-9.]+)\n"),
        ("@openai/codex", "npm", r'"codex-cli ([0-9.]+)"'),
    ),
    ids=("ffmpeg", "codex"),
)
def test_hand_checksummed_image_pins_are_each_watched_once(
    dep_name: str, datasource: str, version_source: str
) -> None:
    """New FFmpeg and Codex releases reach the dashboard (security review, 1.0)."""
    manager = _custom_manager(dep_name)
    assert manager["managerFilePatterns"] == ["/^Dockerfile$/"]
    assert manager["datasourceTemplate"] == datasource
    (match,) = _matches(manager, "Dockerfile")
    pinned = re.findall(version_source, (ROOT / "Dockerfile").read_text())
    assert pinned == [match["currentValue"]]


def test_ffmpeg_release_tags_read_as_versions() -> None:
    manager = _custom_manager("ffmpeg")
    assert manager["packageNameTemplate"] == "FFmpeg/FFmpeg"
    extract = cast("str", manager["extractVersionTemplate"]).replace("(?<", "(?P<")
    for tag, version in (("n7.1.6", "7.1.6"), ("n8.0", "8.0")):
        match = re.fullmatch(extract, tag)
        assert match is not None and match["version"] == version
    assert re.fullmatch(extract, "n8.1-dev") is None
    # Every version the tags yield is found again once it is the pin, or the
    # next release would never reach the dashboard.
    (pattern,) = cast("list[str]", manager["matchStrings"])
    pin = pattern.replace("(?<", "(?P<")
    for version in ("7.1.6", "8.0"):
        found = re.fullmatch(pin, f"ARG FFMPEG_VERSION={version}\n")
        assert found is not None and found["currentValue"] == version


@pytest.mark.parametrize("index", range(7))
def test_workflow_npm_install_expands_the_actual_manifest_pin(index: int) -> None:
    commands = [
        line.strip().removeprefix("run: ")
        for line in (ROOT / ".github/workflows/public-gate.yml")
        .read_text()
        .splitlines()
        if "run: npm install --global" in line
    ]
    assert len(commands) == 7
    # Intercept only installation; the workflow's shell and Node expression run.
    result = subprocess.check_output(
        ["sh", "-eu", "-c", 'npm() { printf "%s\\n" "$@"; }; ' + commands[index]],
        cwd=ROOT,
        text=True,
        timeout=30,
    )
    manifest = json.loads((ROOT / "frontend/package.json").read_bytes())
    assert result.splitlines() == [
        "install",
        "--global",
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
        "npm@" + manifest["engines"]["npm"],
    ]
