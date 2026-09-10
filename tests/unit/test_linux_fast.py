from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from scripts.run_linux_fast import image_recipe, run, verify_source


@pytest.mark.parametrize("change", ["wrong", "tracked", "untracked"])
def test_exact_candidate_refuses_drift(tmp_path: Path, change: str) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "file").write_text("clean")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    sha = subprocess.check_output(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True
    ).strip()
    verify_source(tmp_path, sha)
    if change == "wrong":
        sha = "0" * 40
    else:
        (tmp_path / ("file" if change == "tracked" else "extra")).write_text("dirty")
    with pytest.raises(RuntimeError, match="mismatch_or_dirty"):
        verify_source(tmp_path, sha)


@pytest.mark.parametrize("change", ["missing", "duplicate", "malformed", "unsupported"])
def test_pin_parser_fails_closed(change: str) -> None:
    text = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text()
    line = next(line for line in text.splitlines() if line.endswith(" AS build"))
    replacement = {
        "missing": "",
        "duplicate": line + "\n" + line,
        "malformed": line.replace("@sha256:", "@sha512:"),
        "unsupported": line.replace("FROM ", "FROM --platform=linux/amd64 "),
    }[change]
    with pytest.raises(RuntimeError, match="unsupported_linux_toolchain_pin"):
        image_recipe(text.replace(line, replacement), 501, 20)


def test_recipe_identity_and_nonroot_home() -> None:
    text = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text()
    recipe = image_recipe(text, 501, 20)
    assert "USER 501:20" in recipe
    assert "--create-home" in recipe
    assert "UV_LINK_MODE=copy" in recipe
    assert (
        len(
            {
                recipe,
                image_recipe(text, 502, 20),
                image_recipe(text, 501, 21),
                image_recipe(text.replace("python:3.14.7", "python:3.14.8"), 501, 20),
            }
        )
        == 4
    )


def test_root_refused(tmp_path: Path) -> None:
    with (
        patch("scripts.run_linux_fast.os.getuid", return_value=0),
        pytest.raises(RuntimeError, match="nonroot"),
    ):
        run(tmp_path, "0" * 40, tmp_path / "cache", 1, 10)


@pytest.mark.parametrize("failure", [False, True])
def test_clone_and_failure_cleanup_preserve_source(
    tmp_path: Path, failure: bool
) -> None:
    import os
    from typing import Any

    root = tmp_path / "source"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "Dockerfile").write_text(
        (Path(__file__).resolve().parents[2] / "Dockerfile").read_text()
    )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    sha = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    real_run = subprocess.run
    clones: list[Path] = []
    cleaned: list[bool] = []

    def invoke(command: list[str], **kwargs: Any) -> Any:
        if command[0] != "docker":
            return real_run(command, **kwargs)
        if command[1] == "info":
            return subprocess.CompletedProcess(command, 0, stdout="aarch64\n")
        if command[1] == "run":
            mount = command[command.index("--mount") + 1]
            clone = Path(mount.split("src=")[1].split(",dst=")[0]) / "source"
            clones.append(clone)
            assert (clone / ".git").is_dir()
            assert not (clone / ".git/objects/info/alternates").exists()
            assert (
                os.stat(clone / "Dockerfile").st_ino
                != os.stat(root / "Dockerfile").st_ino
            )
            verify_source(clone, sha)
            assert "--init" in command and "--rm" in command
            if failure:
                raise subprocess.CalledProcessError(9, command)
        elif command[1] == "rm":
            cleaned.append(True)
        return subprocess.CompletedProcess(command, 0)

    with (
        patch("scripts.run_linux_fast.os.getuid", return_value=501),
        patch("scripts.run_linux_fast.subprocess.run", side_effect=invoke),
    ):
        if failure:
            with pytest.raises(subprocess.CalledProcessError):
                run(root, sha, tmp_path / "cache", 180, 30)
        else:
            run(root, sha, tmp_path / "cache", 180, 30)
    assert cleaned == [True]
    assert clones and all(not clone.exists() for clone in clones)
    verify_source(root, sha)


@pytest.mark.parametrize("operation", ["identity", "status", "docker"])
def test_job_deadline_bounds_initial_subprocesses(
    tmp_path: Path, operation: str
) -> None:
    import os
    import sys
    import time

    # A real stalled executable proves the timeout reaches process execution,
    # including the second Git identity operation and pre-test Docker setup.
    executable = tmp_path / ("docker" if operation == "docker" else "git")
    executable.write_text(
        f"#!{sys.executable}\nimport sys, time\n"
        + (
            "print('0' * 40) if 'rev-parse' in sys.argv else time.sleep(60)\n"
            if operation == "status"
            else "time.sleep(60)\n"
        )
    )
    executable.chmod(0o700)
    started = time.monotonic()
    with patch.dict(
        os.environ, {"PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]}
    ):
        with pytest.raises(subprocess.TimeoutExpired):
            if operation == "docker":
                with (
                    patch("scripts.run_linux_fast.os.getuid", return_value=501),
                    patch("scripts.run_linux_fast.verify_source"),
                    patch("scripts.run_linux_fast.image_recipe", return_value="recipe"),
                ):
                    (tmp_path / "Dockerfile").write_text("fixture")
                    run(tmp_path, "0" * 40, tmp_path / "cache", 1, 1)
            else:
                verify_source(tmp_path, "0" * 40, started + 0.2)
    assert time.monotonic() - started < 4


@pytest.mark.parametrize("uid", [33, 501])
def test_recipe_reuses_existing_uid_or_creates_fresh_home(uid: int) -> None:
    text = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text()
    recipe = image_recipe(text, uid, 20)
    assert f'if account="$(getent passwd {uid})"; then' in recipe
    assert 'usermod --home /home/qualifier --gid qualifier "${account%%:*}"' in recipe
    assert f"install -d -o {uid} -g 20 -m 0700 /home/qualifier" in recipe
    assert (
        f"else useradd --uid {uid} --gid qualifier --create-home qualifier; fi"
        in recipe
    )
    assert f"USER {uid}:20" in recipe
