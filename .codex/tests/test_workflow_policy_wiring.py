from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CODEX = ROOT / ".codex"
TEAM = CODEX / "team.toml"


def load_toml(path: Path) -> dict:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def skill_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise AssertionError(f"{path}: missing frontmatter")
    block = text.split("---\n", 2)[1]
    values: dict[str, str] = {}
    for line in block.splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        if not separator:
            raise AssertionError(f"{path}: malformed frontmatter line {line!r}")
        values[key.strip()] = value.strip().strip('"')
    return values


class WorkflowPolicyWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.team = load_toml(TEAM)

    def test_primary_skills_are_small_and_directly_reference_details(self) -> None:
        for workflow, declaration in self.team["workflows"].items():
            path = CODEX / declaration["skill"]
            text = path.read_text(encoding="utf-8")
            self.assertLess(
                len(text.splitlines()),
                500,
                f"{workflow}: primary SKILL.md must remain below 500 lines",
            )
            self.assertEqual(
                set(skill_frontmatter(path)),
                {"name", "description"},
                f"{workflow}: frontmatter must contain only name and description",
            )
            self.assertEqual(skill_frontmatter(path)["name"], workflow)
            references = re.findall(r"`(references/[^`]+\.md)`", text)
            self.assertTrue(references, f"{workflow}: missing direct progressive reference")
            for relative in references:
                self.assertTrue((path.parent / relative).is_file(), relative)

    def test_every_reviewer_is_context_isolated_and_write_disabled(self) -> None:
        reviewers = {
            name: declaration
            for name, declaration in self.team["roles"].items()
            if declaration["kind"] == "reviewer"
        }
        self.assertIn("readiness_reviewer", reviewers)
        self.assertIn("publication_challenger", reviewers)
        for role, declaration in reviewers.items():
            self.assertEqual(declaration["permissions"], ":read-only", role)
            self.assertEqual(declaration["initial_context"], "FRESH_ISOLATED", role)
            self.assertEqual(declaration["spawn_fork_turns"], "none", role)
            self.assertEqual(
                declaration["targeted_context"], "TARGETED_CONTINUATION", role
            )
            self.assertIn("raw_packet_review", declaration["capabilities"], role)
            self.assertIn(
                "report_any_substantiated_blocker", declaration["capabilities"], role
            )
            self.assertIn("external_write", declaration["forbidden_capabilities"], role)
            self.assertNotIn("external_write", declaration["capabilities"], role)
            self.assertTrue(declaration["required_policies"], role)

            config = load_toml(CODEX / declaration["config"])
            self.assertEqual(config["default_permissions"], ":read-only", role)
            self.assertFalse(config["features"]["apps"], role)
            defaults = config["apps"]["_default"]
            self.assertFalse(defaults["enabled"], role)
            self.assertFalse(defaults["destructive_enabled"], role)
            self.assertFalse(defaults["open_world_enabled"], role)
            prompt = config["developer_instructions"]
            self.assertIn("canonical packet ID/digest", prompt, role)
            self.assertIn("FRESH_ISOLATED", prompt, role)
            self.assertIn("primary", prompt, role)
            self.assertIn("substantiated", prompt, role)

    def test_role_declarations_expose_policies_and_capability_boundaries(self) -> None:
        for role, declaration in self.team["roles"].items():
            self.assertTrue(declaration.get("required_policies"), role)
            self.assertTrue(declaration.get("capabilities"), role)
            self.assertIn("forbidden_capabilities", declaration, role)
            if declaration["kind"] != "writer":
                self.assertIn("repository_write", declaration["forbidden_capabilities"], role)
                self.assertIn("external_write", declaration["forbidden_capabilities"], role)
                self.assertNotIn("workspace_write", declaration["capabilities"], role)

    def test_delivery_workflows_declare_barriers_and_expansion_thresholds(self) -> None:
        writer_workflows = {
            name: declaration
            for name, declaration in self.team["workflows"].items()
            if declaration["writer"]
        }
        for workflow, declaration in writer_workflows.items():
            self.assertTrue(declaration["evidence_before_review"], workflow)
            self.assertTrue(declaration["review_barrier"], workflow)
            self.assertEqual(declaration["family_expansion_thresholds"], [1, 2, 3])
            self.assertIn("publication_challenger", declaration["roles"], workflow)

        readiness = self.team["workflows"]["prepare-ready-work"]
        self.assertTrue(readiness["review_barrier"])
        self.assertEqual(readiness["family_expansion_thresholds"], [1, 2, 3])

    def test_policy_documents_encode_required_mechanical_controls(self) -> None:
        binding = (CODEX / "binding-preflight.md").read_text(encoding="utf-8")
        self.assertIn('fork_turns="none"', binding)
        self.assertIn("SHA-256", binding)
        self.assertIn("unique non-null thread", binding)
        self.assertIn("TARGETED_CONTINUATION", binding)
        self.assertIn("Capability-based reviewer fallback", binding)
        self.assertIn("isolated_cli_reviewer", binding)
        self.assertIn("launch receipt", binding)
        self.assertIn("generic or root-labelled host", binding)
        self.assertIn("never held a root or worker lease", binding)

        review = (CODEX / "review-method.md").read_text(encoding="utf-8")
        for state in ("FRESH", "INHERITED", "NOT_APPLICABLE", "UNAVAILABLE"):
            self.assertIn(state, review)
        self.assertLess(
            review.index("Run every mandatory private, Linux, and external qualification"),
            review.index("## Initial reviewer creation"),
        )
        self.assertIn("barrier", review.lower())

        families = (CODEX / "finding-family-audit.md").read_text(encoding="utf-8")
        self.assertIn("expansion_count = 1", families)
        self.assertIn("expansion_count = 2", families)
        self.assertIn("expansion_count >= 3", families)
        self.assertIn("exact original reviewer thread", families)

        model = (CODEX / "model-selection.md").read_text(encoding="utf-8")
        self.assertIn("before the first edit", model)
        self.assertIn("ROOT_ESCALATION", model)
        self.assertIn("dummy worker", model)

    def test_delivery_skills_use_frozen_packet_preflight_and_isolated_review(self) -> None:
        for workflow in (
            "clear-defect-backlog",
            "deliver-focused-change",
            "deliver-functional-slice",
            "maintain-open-prs",
        ):
            skill = CODEX / self.team["workflows"][workflow]["skill"]
            text = skill.read_text(encoding="utf-8")
            for relative in re.findall(r"`(references/[^`]+\.md)`", text):
                text += "\n" + (skill.parent / relative).read_text(encoding="utf-8")
            self.assertIn('fork_turns="none"', text, workflow)
            self.assertIn("ROOT_ESCALATION", text, workflow)
            self.assertIn("--packet", text, workflow)
            self.assertNotIn("review_preflight.py --base", text, workflow)
            self.assertRegex(text, r"private, Linux", workflow)
            self.assertIn("hard", text.lower(), workflow)
            self.assertIn("originating", text.lower(), workflow)

    def test_readiness_workflow_is_recorded_bounded_and_mutation_governed(self) -> None:
        text = (CODEX / "skills/prepare-ready-work/SKILL.md").read_text(
            encoding="utf-8"
        )
        for token in (
            ".codex/structured-records.md",
            "at most two analysts",
            'fork_turns="none"',
            "TARGETED_CONTINUATION",
            "exact tracking/handoff set",
        ):
            self.assertIn(token, text)
        self.assertRegex(
            text,
            r"Record every issue, relationship,\s+Project, or planning mutation",
        )


if __name__ == "__main__":
    unittest.main()
