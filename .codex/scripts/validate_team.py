#!/usr/bin/env python3
"""Validate the declarative workflow profiles and report comparison metrics."""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path
from typing import Any


CODEX = Path(__file__).resolve().parents[1]
CONFIG = CODEX / "config.toml"
MANIFEST = CODEX / "team.toml"
BASELINE = CODEX / "workflow-control-baseline.json"
SHARED_REFERENCE = re.compile(r"\.codex/[a-z0-9-]+\.md")


class ValidationError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def load_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValidationError(f"cannot read {path}: {error}") from error


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValidationError(f"cannot read {path}: {error}") from error
    require(isinstance(value, dict), f"{path}: expected an object")
    return value


def line_count(paths: list[Path]) -> int:
    return sum(len(path.read_text(encoding="utf-8").splitlines()) for path in paths)


def frontmatter_name(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    require(lines and lines[0] == "---", f"{path}: missing YAML frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise ValidationError(f"{path}: unterminated YAML frontmatter") from error
    names = [line.split(":", 1)[1].strip() for line in lines[1:end] if line.startswith("name:")]
    require(len(names) == 1 and names[0], f"{path}: requires one frontmatter name")
    return names[0]


def metrics(manifest: dict[str, Any]) -> dict[str, int]:
    scripts = sorted((CODEX / "scripts").glob("*.py"))
    tests = sorted((CODEX / "tests").glob("test_*.py"))
    skills = [CODEX / item["skill"] for item in manifest["workflows"].values()]
    shared_reads = [
        len(set(SHARED_REFERENCE.findall(path.read_text(encoding="utf-8"))))
        for path in skills
    ]
    return {
        "profiles": len(manifest["profiles"]),
        "workflows": len(manifest["workflows"]),
        "runtime_python_lines": line_count(scripts),
        "test_python_lines": line_count(tests),
        "skill_entry_lines": line_count(skills),
        "skill_entry_lines_max": max(map(lambda path: len(path.read_text(encoding="utf-8").splitlines()), skills)),
        "mandatory_shared_reads_max": max(shared_reads),
        "validator_lines": len(Path(__file__).read_text(encoding="utf-8").splitlines()),
    }


def validate_baseline() -> dict[str, Any]:
    baseline = load_json(BASELINE)
    require(
        set(baseline) == {
            "schema_version", "captured_at", "source", "definitions", "metrics",
            "forward_run_observation",
        },
        "invalid comparison baseline keys",
    )
    require(baseline["schema_version"] == 1, "unsupported comparison baseline schema")
    source = baseline["source"]
    require(
        isinstance(source, dict) and set(source) == {"ref", "parent_ref", "subject"},
        "invalid comparison baseline source",
    )
    require(
        all(isinstance(source[field], str) and source[field].strip() for field in source),
        "invalid comparison baseline source values",
    )
    require(
        all(re.fullmatch(r"[0-9a-f]{40}", source[field]) for field in ("ref", "parent_ref")),
        "invalid comparison baseline refs",
    )
    metrics_value = baseline["metrics"]
    require(
        isinstance(metrics_value, dict)
        and metrics_value
        and all(isinstance(value, int) and value >= 0 for value in metrics_value.values()),
        "invalid comparison baseline metrics",
    )
    require(
        isinstance(baseline["definitions"], dict)
        and set(baseline["definitions"]) == set(metrics_value),
        "comparison baseline definitions differ from metrics",
    )
    observation = baseline["forward_run_observation"]
    require(
        isinstance(observation, dict)
        and observation.get("qualified") is False
        and observation.get("status") == "REMEDIATION"
        and isinstance(observation.get("basis"), str),
        "invalid forward-run baseline observation",
    )
    return baseline


def validate() -> dict[str, Any]:
    config = load_toml(CONFIG)
    manifest = load_toml(MANIFEST)
    baseline = validate_baseline()
    require(manifest.get("schema_version") == 1, "unsupported manifest schema")
    require(config.get("model") == manifest["root"].get("model"), "root model mismatch")
    require(
        config.get("model_reasoning_effort") == manifest["root"].get("reasoning"),
        "root reasoning mismatch",
    )
    require(config.get("agents", {}).get("max_depth") == 1, "agent depth must be one")

    profiles = manifest.get("profiles")
    workflows = manifest.get("workflows")
    require(isinstance(profiles, dict) and profiles, "profiles must be a nonempty table")
    require(isinstance(workflows, dict) and workflows, "workflows must be a nonempty table")
    registry = {
        name: value
        for name, value in config.get("agents", {}).items()
        if isinstance(value, dict)
    }
    require(set(registry) == set(profiles), "config and manifest profile registries differ")

    profile_summary: dict[str, Any] = {}
    for name, profile in profiles.items():
        require(
            set(profile) == {"config", "kind", "permissions", "capabilities"},
            f"{name}: invalid profile declaration",
        )
        require(profile["kind"] in {"reader", "writer", "reviewer"}, f"{name}: invalid kind")
        expected_permission = ":workspace" if profile["kind"] == "writer" else ":read-only"
        require(profile["permissions"] == expected_permission, f"{name}: unsafe permission")
        require(
            isinstance(profile["capabilities"], list)
            and profile["capabilities"]
            and len(profile["capabilities"]) == len(set(profile["capabilities"])),
            f"{name}: invalid capabilities",
        )
        path = CODEX / profile["config"]
        actual = load_toml(path)
        require(actual.get("name") == name, f"{name}: config name mismatch")
        require(actual.get("default_permissions") == expected_permission, f"{name}: config permission mismatch")
        require(registry[name].get("config_file") == profile["config"], f"{name}: registry path mismatch")
        require(actual.get("model"), f"{name}: model is required")
        require(actual.get("model_reasoning_effort") in {"medium", "high"}, f"{name}: invalid reasoning")
        profile_summary[name] = {
            "kind": profile["kind"],
            "permissions": profile["permissions"],
            "capabilities": sorted(profile["capabilities"]),
            "model": actual["model"],
            "reasoning": actual["model_reasoning_effort"],
        }

    workflow_summary: dict[str, Any] = {}
    for name, workflow in workflows.items():
        require(
            set(workflow) == {"skill", "profiles", "writer_profile", "default_review_tasks"},
            f"{name}: invalid workflow declaration",
        )
        require(
            isinstance(workflow["profiles"], list)
            and workflow["profiles"]
            and set(workflow["profiles"]) <= set(profiles),
            f"{name}: invalid workflow profiles",
        )
        writer = workflow["writer_profile"]
        if writer:
            require(writer in workflow["profiles"], f"{name}: writer is not available")
            require(profiles[writer]["kind"] == "writer", f"{name}: writer profile is not writable")
        else:
            require(
                all(profiles[item]["kind"] != "writer" for item in workflow["profiles"]),
                f"{name}: read-only workflow exposes a writer",
            )
        tasks = workflow["default_review_tasks"]
        require(
            isinstance(tasks, list) and all(isinstance(item, str) and item for item in tasks),
            f"{name}: invalid review tasks",
        )
        if tasks:
            require("reviewer" in workflow["profiles"], f"{name}: review tasks lack reviewer profile")
        skill = CODEX / workflow["skill"]
        require(skill.is_file(), f"{name}: missing skill")
        require(frontmatter_name(skill) == name, f"{name}: skill name mismatch")
        workflow_summary[name] = {
            "profiles": workflow["profiles"],
            "writer_profile": writer or None,
            "default_review_tasks": tasks,
        }

    observed = metrics(manifest)
    budgets = manifest.get("budgets", {})
    require(observed["profiles"] <= budgets.get("profiles_max", 0), "profile budget exceeded")
    budget_fields = {
        "runtime_python_lines": "runtime_python_lines_max",
        "test_python_lines": "test_python_lines_max",
        "skill_entry_lines_max": "skill_entry_lines_max",
        "mandatory_shared_reads_max": "mandatory_shared_reads_max",
    }
    for field, budget in budget_fields.items():
        require(observed[field] <= budgets.get(budget, 0), f"{field} budget exceeded")

    return {
        "baseline": {
            "source": baseline["source"],
            "metrics": baseline["metrics"],
            "forward_run_observation": baseline["forward_run_observation"],
        },
        "profiles": profile_summary,
        "workflows": workflow_summary,
        "metrics": observed,
        "budgets": budgets,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true", help="validate declarations and budgets")
    args = parser.parse_args()
    try:
        result = validate()
    except ValidationError as error:
        print(f"FAIL: {error}", file=__import__("sys").stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            f"PASS: {len(result['profiles'])} profiles, "
            f"{len(result['workflows'])} workflows, "
            f"{result['metrics']['runtime_python_lines']} runtime lines"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
