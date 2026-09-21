from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

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


def test_only_replacements_and_compatibility_policy_remain_manual() -> None:
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
            "description": "Require approval for dependency replacements",
            "matchUpdateTypes": ["replacement"],
            "automerge": False,
            "dependencyDashboardApproval": True,
        },
    ]


@pytest.mark.parametrize(
    "names,group",
    [
        (["node"], "Node runtime"),
        (["python"], "Python runtime"),
        (["astral-sh/uv", "ghcr.io/astral-sh/uv"], "uv resolver"),
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


def test_application_manifests_remain_exact() -> None:
    python = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert python["project"]["requires-python"] == ">=3.13,<3.15"
    for requirement in (
        python["project"]["dependencies"] + python["dependency-groups"]["dev"]
    ):
        assert "==" in requirement
    frontend = json.loads((ROOT / "frontend/package.json").read_bytes())
    for section in ("dependencies", "devDependencies", "overrides", "engines"):
        assert all(
            re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
            for version in frontend[section].values()
        )
    assert frontend["packageManager"] == "npm@" + frontend["engines"]["npm"]


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
    assert len(workflow_pins) == 4
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


def test_osv_custom_manager_is_narrow_and_digest_bound() -> None:
    assert CONFIG["ignorePresets"] == ["customManagers:tsconfigNodeVersions"]
    (manager,) = CONFIG["customManagers"]
    assert manager["managerFilePatterns"] == [
        r"/^\.github/workflows/public-gate\.yml$/"
    ]
    assert manager["depNameTemplate"] == "ghcr.io/google/osv-scanner"
    assert manager["datasourceTemplate"] == "docker"
    (pattern,) = manager["matchStrings"]
    python_pattern = pattern.replace("(?<", "(?P<")
    matches = list(
        re.finditer(
            python_pattern, (ROOT / ".github/workflows/public-gate.yml").read_text()
        )
    )
    assert len(matches) == 1
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", matches[0]["currentDigest"])


@pytest.mark.parametrize("index", range(4))
def test_workflow_npm_install_expands_the_actual_manifest_pin(index: int) -> None:
    commands = [
        line.strip().removeprefix("run: ")
        for line in (ROOT / ".github/workflows/public-gate.yml")
        .read_text()
        .splitlines()
        if "run: npm install --global" in line
    ]
    assert len(commands) == 4
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
