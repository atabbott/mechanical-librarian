from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "public-gate.yml"
CONTRIBUTING = ROOT / "CONTRIBUTING.md"

# Commands that prepare a job rather than check the tree, and so answer to no
# row in CONTRIBUTING's table. The exemption is per command and matched in
# full, never per step and never by prefix. Exempting a whole step hides every
# later command added inside it: `Qualify the frontend source` already contains
# a documented command, so a step-level rule passed while an undocumented
# `npm run e2e` sat beside it. Matching a prefix has the same shape of hole one
# level down, admitting any command that merely starts like an exempt one.
#
# The default therefore runs the other way: a command is a check, and calling
# one setup is a deliberate edit to this list rather than an omission nobody
# notices. `uses:` steps need no entry -- they install toolchains and move
# caches, and none of them runs a repository command.
SETUP_COMMANDS = frozenset(
    {
        # Installs the locked dependency closure the checks then run under.
        "uv sync --locked --all-groups",
        # Reads a version out of that closure to key the payload cache.
        "uv run python -c 'from importlib.metadata import version; "
        'print("version=" + version("playwright"))\' >> "$GITHUB_OUTPUT"',
        # Fetches the payload that the next command is what actually verifies.
        "uv run playwright install chromium",
        # Installs the manifest-pinned package manager before frontend checks.
        "npm install --global --ignore-scripts --no-audit --no-fund "
        '"npm@$(node -p \'require("./frontend/package.json").engines.npm\')"',
        # Installs the frontend closure, in both jobs that build the frontend.
        "npm ci --ignore-scripts --no-audit --no-fund",
        # The two documents `cmp` then compares, and the regeneration in place
        # that the typecheck, lint, test and clean-tree checks run against.
        'npm run generate -- --output "$RUNNER_TEMP/review-api-a.d.ts"',
        'npm run generate -- --output "$RUNNER_TEMP/review-api-b.d.ts"',
        "npm run generate",
        # The two output trees `diff -r` then compares.
        'ML_FRONTEND_OUT_DIR="$RUNNER_TEMP/frontend-build-a" npm run build',
        'ML_FRONTEND_OUT_DIR="$RUNNER_TEMP/frontend-build-b" npm run build',
        # Produces the asset directory the browser tier is then run against.
        'ML_FRONTEND_OUT_DIR="$RUNNER_TEMP/react-assets" npm run build',
    }
)


def workflow_steps(source: str) -> tuple[tuple[str, str], ...]:
    """Pair each `- name:` step with its own body, stopping at the next job.

    A step's body ends at the next step or at the next top-level job key, so a
    step never inherits the following job's header and cannot be credited with
    a command it does not run.
    """

    steps: list[tuple[str, list[str]]] = []
    collecting = False
    for line in source.splitlines():
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if stripped and indent <= 2 and not stripped.startswith("- "):
            collecting = False
            continue
        if stripped.startswith("- name: "):
            steps.append((stripped.removeprefix("- name: "), []))
            collecting = True
            continue
        if collecting:
            steps[-1][1].append(line)
    return tuple((name, "\n".join(body)) for name, body in steps)


def step_commands(body: str) -> tuple[str, ...]:
    """Every shell command one step runs, honouring the YAML block style.

    The style decides what a command is, so it cannot be guessed. A literal
    `|` block runs one command per line, while a folded `>` or `>-` block is a
    single command spread over several lines -- reading a folded block by line
    would report `python3` as a command of its own and demand a table row for
    it.
    """

    lines = body.splitlines()
    for position, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("run:"):
            continue
        inline = stripped.removeprefix("run:").strip()
        indent = len(line) - len(line.lstrip())
        block: list[str] = []
        for following in lines[position + 1 :]:
            if not following.strip():
                continue
            if len(following) - len(following.lstrip()) <= indent:
                break
            block.append(following.strip())
        if inline not in {"|", "|-", "|+", ">", ">-", ">+"}:
            return (inline,)
        return (" ".join(block),) if inline.startswith(">") else tuple(block)
    return ()


EXACT_SHA = "${{ github.event.pull_request.head.sha || github.sha }}"


def test_qualifies_pull_request_heads_and_merged_main() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request:" in workflow
    assert "types: [opened, synchronize, reopened]" in workflow
    assert "  push:\n    branches: [main]\n" in workflow
    assert "ready_for_review" not in workflow


def test_every_exact_revision_reference_resolves_for_both_events() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert workflow.count(f"ref: {EXACT_SHA}") == 8
    assert f'--source-sha "{EXACT_SHA}"' in workflow
    # An unguarded head reference is null on a push to `main`.
    assert "${{ github.event.pull_request.head.sha }}" not in workflow


def test_concurrency_never_cancels_post_merge_verification() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert (
        "group: public-gate-${{ github.workflow }}-"
        "${{ github.event.pull_request.number || github.sha }}\n"
    ) in workflow
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in workflow
    assert "cancel-in-progress: true" not in workflow


@pytest.mark.parametrize(
    "job",
    (
        "fast-source",
        "fast-source-2",
        "fast-source-3",
        "fast-source-4",
        "exact-installed",
        "protected-browser",
    ),
    ids=(
        "fast_source",
        "fast_source_2",
        "fast_source_3",
        "fast_source_4",
        "exact_installed",
        "protected_browser",
    ),
)
def test_retains_the_three_independent_timeout_bounded_tiers(job: str) -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert f"  {job}:\n" in workflow
    assert "  public-gate:\n" not in workflow
    assert "needs:" not in workflow


def test_every_job_is_bounded_and_the_source_job_gates_before_it_tests() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "  container-image:\n" in workflow
    assert "  dependency-audit:\n" in workflow
    assert workflow.count("runs-on: ubuntu-24.04") == 8
    assert (
        "  fast-source:\n    runs-on: ubuntu-24.04\n    timeout-minutes: 25\n"
    ) in workflow
    assert workflow.count("timeout-minutes: 25") == 1
    assert (
        "  protected-browser:\n    runs-on: ubuntu-24.04\n    timeout-minutes: 15\n"
    ) in workflow
    assert workflow.count("timeout-minutes: 15") == 1
    for shard in (2, 3, 4):
        assert (
            f"  fast-source-{shard}:\n    runs-on: ubuntu-24.04\n"
            "    timeout-minutes: 12\n"
        ) in workflow
    assert workflow.count("timeout-minutes: 12") == 3
    assert workflow.count("timeout-minutes: 10") == 3
    # The three commands CONTRIBUTING.md documents as reproducing this job.
    # `ruff format --check` is the backend's line-width contract, so a tree
    # that is not formatted has to fail here rather than pass unnoticed.
    for command in (
        "run: uv run ruff format --check src tests scripts",
        "run: uv run ruff check src tests scripts",
        "run: uv run mypy src tests scripts",
    ):
        assert command in workflow
    # Formatting is gated before any test runs, as the tier ordering intends.
    assert workflow.index("ruff format --check") < workflow.index(
        "run_test_tier.py fast"
    )


def test_the_documented_check_set_matches_the_only_workflow() -> None:
    """Bind CONTRIBUTING's reconciliation table to the workflow it describes.

    Both halves of the drift this exists to stop were real. Prose claimed
    contract, security and documentation checks that no job ran, and
    `.github/scripts/validate_linked_issues.py` outlived the workflow that ran
    it by three milestones, reading to anyone browsing the tree like an
    enforced linked-issue gate while nothing invoked it.

    The third column is the row's claim: a `gate` row's backticked spans are
    workflow text that must be there, and a `maintainer-manual` row's are text
    that must not.

    That direction alone is not reconciliation, and reading it as though it
    were is this milestone's own recurring defect appearing inside the test
    written to prevent it. It proves every documented check exists; it says
    nothing about a check the workflow gained and the table never learned
    about. So every command the workflow runs is mapped back to a row as well,
    and the two sets are compared for equality rather than containment: an
    undocumented command makes the unmapped set too large, and an exemption
    that no longer matches any command makes it too small.
    """

    workflow = WORKFLOW.read_text(encoding="utf-8")
    directory = ROOT / ".github" / "workflows"
    assert sorted(
        path.name for path in directory.iterdir() if path.suffix in {".yml", ".yaml"}
    ) == ["public-gate.yml"]
    # Automation under `.github/` is invoked by that workflow or it is nothing.
    for path in (ROOT / ".github").rglob("*"):
        if path.is_file() and path.suffix in {".py", ".sh"}:
            assert path.name in workflow, path.relative_to(ROOT)

    section = CONTRIBUTING.read_text(encoding="utf-8").split("## What merging proves")
    assert len(section) == 2
    rows = [
        line
        for line in section[1].split("\n## ")[0].splitlines()
        if line.startswith("|") and not line.startswith("| ---")
    ]
    # The header row plus one row per check; a table that stopped being parsed
    # would otherwise pass by asserting nothing.
    assert len(rows) > 20
    documented: list[str] = []
    enforced = 0
    for row in rows[1:]:
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        assert cells[1] in {"gate", "maintainer-manual"}, row
        spans = re.findall(r"`([^`]+)`", cells[2])
        if cells[1] == "gate":
            enforced += 1
            for span in spans:
                assert span in workflow, span
            documented.extend(spans)
            continue
        for span in spans:
            assert span not in workflow, span
    assert enforced >= 14

    # And back the other way, per command rather than per step: a command
    # added inside a step that already holds a documented one is exactly the
    # case a step-granular rule waves through.
    steps = workflow_steps(workflow)
    assert len(steps) >= 30
    commands = {command for _, body in steps for command in step_commands(body)}
    assert len(commands) >= 20
    unmapped = {
        command
        for command in commands
        if not any(span in command for span in documented)
    }
    assert unmapped == SETUP_COMMANDS


def test_builds_the_shipped_container_from_the_exact_revision() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "uses: docker/build-push-action@" in workflow
    assert "tags: mechanical-librarian:gate" in workflow
    assert "context: ." in workflow
    # setup-buildx downloads the Buildx binary at run time, so the SHA pin on
    # the action alone does not pin the toolchain: the version input must name
    # an exact release, never `latest` or a floating range.
    assert re.search(
        r"docker/setup-buildx-action@[0-9a-f]{40}[^\n]*\n"
        r"(?:[^\n]*\n){0,5}?\s+version: v\d+\.\d+\.\d+\n",
        workflow,
    )
    # Layers restore from the repository Actions cache; a stage re-runs only
    # when its inputs change. Base-image freshness is governed by the digest
    # pins on the Dockerfile's FROM lines, not by `--pull`.
    assert "cache-from: type=gha" in workflow
    assert "cache-to: type=gha,mode=max" in workflow
    assert "docker build --pull" not in workflow
    # The built image is never pushed or run by the gate. It is loaded and
    # its tag referenced exactly once more, by the extraction that hands the
    # image's pinned probe binary to the probe tier (#929) -- a `docker
    # create` never starts a process. The `docker run` in the dependency
    # audit runs a different, digest-pinned scanner image and never touches
    # this one.
    assert "push: false" in workflow
    assert "load: true" in workflow
    assert "docker push" not in workflow
    assert workflow.count("mechanical-librarian:gate") == 2
    assert 'container="$(docker create mechanical-librarian:gate)"' in workflow
    assert "docker run mechanical-librarian" not in workflow


def test_the_probe_tier_executes_the_images_own_pinned_binary() -> None:
    """The probe pass must be attributable to the image's pin, nothing else.

    The runner image preinstalls a distribution ffmpeg at `/usr/bin/ffprobe`,
    which is first in the inspector's closed candidate set, so leaving it in
    place would let the tier pass against a binary the repository does not
    pin. And a version literal repeated in the workflow would drift from the
    Dockerfile ARG on the next pin bump, so the assertion must read the ARG.
    """

    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "sudo rm -f /usr/bin/ffprobe" in workflow
    assert 'docker cp "${container}:/usr/local/bin/ffprobe"' in workflow
    assert (
        'sudo install -m 0755 "$RUNNER_TEMP/ffprobe" /usr/local/bin/ffprobe' in workflow
    )
    assert "version=\"$(sed -n 's/^ARG FFMPEG_VERSION=//p' Dockerfile)\"" in workflow
    # `${version:?}` fails the step when the ARG read comes back empty, so a
    # renamed Dockerfile ARG cannot silently turn the grep into a no-op match.
    assert (
        '/usr/local/bin/ffprobe -version | head -n 1 | grep -F "ffprobe version ${version:?}"'
        in workflow
    )
    assert "test ! -e /usr/bin/ffprobe" in workflow
    assert "uv run python scripts/run_test_tier.py probe" in workflow
    # The extraction and the tier both live in the job that built the image,
    # so the binary executed is the one this exact revision assembled.
    jobs = workflow.split("\n  container-image:\n")
    assert len(jobs) == 2
    tail = jobs[1].split("\n  dependency-audit:\n")[0]
    assert "docker create mechanical-librarian:gate" in tail
    assert "scripts/run_test_tier.py probe" in tail
    # `fast-source` stays ffprobe-free: unit tests consume recorded probe
    # documents, and the probe binary exists only where the image supplies it.
    assert "ffprobe" not in jobs[0]


def test_caches_the_pinned_chromium_payload_but_still_verifies_it() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert (
        "key: playwright-chromium-${{ runner.os }}-${{ runner.arch }}-"
        "${{ steps.playwright.outputs.version }}\n"
    ) in workflow
    assert workflow.count("path: ~/.cache/ms-playwright") == 2
    assert "uv run playwright install chromium" in workflow
    assert "run: uv run python scripts/verify_playwright_browser.py" in workflow
    # The payload is saved only after verification succeeds.
    save = workflow.index("actions/cache/save@")
    verify = workflow.index("scripts/verify_playwright_browser.py")
    assert verify < save


def test_the_browser_install_neither_stalls_silently_nor_skips_the_launch() -> None:
    """Hold the two halves of #772 that a later edit could quietly undo.

    `--with-deps` cost this job its entire ten-minute budget twice inside
    `apt-get` on a Chromium cache hit, because system libraries live on the
    runner rather than under the cached `~/.cache/ms-playwright`. Removing it
    is only safe while both replacements stay: a bound, so the next stall of
    any kind fails fast rather than eating the tier, and a launch, so a runner
    image that stops supplying one of Chromium's shared libraries fails in a
    step of its own instead of reading as a browser-tier regression.
    """

    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "playwright install --with-deps" not in workflow
    install = [
        body
        for name, body in workflow_steps(workflow)
        if name == "Install the Chromium qualification browser"
    ]
    assert len(install) == 1
    assert "timeout-minutes:" in install[0]
    assert step_commands(install[0]) == ("uv run playwright install chromium",)
    assert "uv run python scripts/verify_playwright_browser.py --launch" in workflow
    # The launch runs before the tier it is there to keep blame off.
    assert workflow.index("verify_playwright_browser.py --launch") < workflow.index(
        "run_test_tier.py browser"
    )


def test_every_action_is_sha_pinned_without_persisted_credentials() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    uses = [
        line.strip().removeprefix("uses:").strip()
        for line in workflow.splitlines()
        if line.strip().startswith("uses:")
    ]
    assert len(uses) == 33
    # The references are read out of the workflow at run time, so they cannot
    # become parameters without making the collected case count -- and the
    # cohort identity digest built from it -- move whenever the workflow gains
    # or loses a step. Each reference is carried into its assertion instead, so
    # a failure still names the one that is not pinned.
    for reference in uses:
        _, _, revision = reference.partition("@")
        pinned = revision.split()[0]
        assert len(pinned) == 40, reference
        assert all(value in "0123456789abcdef" for value in pinned), reference
    assert workflow.count("persist-credentials: false") == workflow.count(
        "actions/checkout@"
    )
    assert "permissions:\n  contents: read\n" in workflow
