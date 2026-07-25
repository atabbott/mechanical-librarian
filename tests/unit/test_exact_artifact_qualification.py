from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import scripts.qualify_exact_artifact as qualifier_module
from scripts.qualify_exact_artifact import (
    QualificationFailure,
    _decode_stdout,
    _run,
    _verify_installed_payload,
    qualify_exact_artifact,
)


class ExactArtifactQualificationTests(unittest.TestCase):
    def test_same_version_shared_cache_executes_exact_second_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            cache = root / "cache"
            source.mkdir()
            cache.mkdir()
            _write_probe(source, "source-a")
            first_sha = _commit(source, "source a")

            with patch.dict(os.environ, {"UV_CACHE_DIR": str(cache)}):
                first_wheel_output = root / "source-a-wheel"
                first_environment_path = root / "source-a-environment"
                first = qualify_exact_artifact(
                    source,
                    first_sha,
                    distribution="cache-probe",
                    import_name="cache_probe",
                    console_script="cache-probe",
                    console_args=(),
                    wheel_output=first_wheel_output,
                    environment_path=first_environment_path,
                )
                stale_environment = root / "stale-cache-environment"
                _command("uv", "venv", "--python", sys.executable, str(stale_environment))
                first_wheel = next(first_wheel_output.glob("*.whl"))
                _command(
                    "uv",
                    "pip",
                    "install",
                    "--reinstall",
                    "--python",
                    str(_environment_bin(stale_environment) / "python"),
                    str(first_wheel),
                )
                stale_output = _command(
                    str(_environment_bin(stale_environment) / "cache-probe")
                )
                self.assertEqual("source-a\n", stale_output)
                self.assertTrue(any(cache.iterdir()))

                _write_probe(source, "source-b")
                second_sha = _commit(source, "source b")
                wheel_output = root / "retained-wheel"
                environment_path = root / "retained-environment"
                second = qualify_exact_artifact(
                    source,
                    second_sha,
                    distribution="cache-probe",
                    import_name="cache_probe",
                    console_script="cache-probe",
                    console_args=(),
                    wheel_output=wheel_output,
                    environment_path=environment_path,
                )
                retained_console_exists = (
                    environment_path
                    / ("Scripts" if os.name == "nt" else "bin")
                    / "cache-probe"
                ).is_file()
                retained_wheel_count = len(tuple(wheel_output.glob("*.whl")))

        self.assertEqual(first.distribution_version, "0.0.0")
        self.assertEqual(second.distribution_version, "0.0.0")
        self.assertNotEqual(first.source_sha, second.source_sha)
        self.assertNotEqual(first.wheel_sha256, second.wheel_sha256)
        self.assertEqual(
            hashlib.sha256(b"source-b\n").hexdigest(),
            second.console_stdout_sha256,
        )
        self.assertEqual(
            second.wheel_payload_sha256,
            second.installed_payload_sha256,
        )
        self.assertTrue(second.retained_wheel)
        self.assertTrue(second.retained_environment)
        self.assertTrue(retained_console_exists)
        self.assertEqual(1, retained_wheel_count)

    def test_source_change_during_qualification_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            _write_probe(source, "source-a")
            source_sha = _commit(source, "source")
            real_run = qualifier_module._run
            changed = False

            def mutate_after_snapshot(
                command: tuple[str, ...],
                **kwargs: object,
            ) -> subprocess.CompletedProcess[bytes]:
                nonlocal changed
                completed = real_run(command, **kwargs)
                if not changed and command[:2] == ("git", "clone"):
                    _write_probe(source, "source-b")
                    changed = True
                return completed

            with patch.object(
                qualifier_module,
                "_run",
                side_effect=mutate_after_snapshot,
            ):
                with self.assertRaises(QualificationFailure) as raised:
                    qualify_exact_artifact(
                        source,
                        source_sha,
                        distribution="cache-probe",
                        import_name="cache_probe",
                        console_script="cache-probe",
                        console_args=(),
                    )
        self.assertEqual("source_not_clean", raised.exception.reason)

    def test_dirty_or_wrong_source_identity_fails_before_build(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            _write_probe(source, "source")
            source_sha = _commit(source, "source")

            with self.assertRaises(QualificationFailure) as wrong:
                qualify_exact_artifact(
                    source,
                    "0" * 40,
                    distribution="cache-probe",
                    import_name="cache_probe",
                    console_script="cache-probe",
                    console_args=(),
                )
            self.assertEqual("source_identity_mismatch", wrong.exception.reason)

            (source / "untracked").write_text("dirty", encoding="utf-8")
            with self.assertRaises(QualificationFailure) as dirty:
                qualify_exact_artifact(
                    source,
                    source_sha,
                    distribution="cache-probe",
                    import_name="cache_probe",
                    console_script="cache-probe",
                    console_args=(),
                )
            self.assertEqual("source_not_clean", dirty.exception.reason)

    def test_installed_payload_difference_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheel = root / "cache_probe-0.0.0-py3-none-any.whl"
            purelib = root / "purelib"
            members = {
                "cache_probe.py": b"print('wheel')\n",
                "cache_probe-0.0.0.dist-info/METADATA": (
                    b"Metadata-Version: 2.4\n"
                    b"Name: cache-probe\n"
                    b"Version: 0.0.0\n\n"
                ),
                "cache_probe-0.0.0.dist-info/WHEEL": (
                    b"Wheel-Version: 1.0\n"
                    b"Root-Is-Purelib: true\n"
                    b"Tag: py3-none-any\n"
                ),
            }
            with ZipFile(wheel, "w") as archive:
                for name, content in members.items():
                    archive.writestr(name, content)
            for name, content in members.items():
                target = purelib.joinpath(*name.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            (purelib / "cache_probe.py").write_text(
                "print('installed')\n",
                encoding="utf-8",
            )

            with self.assertRaises(QualificationFailure) as raised:
                _verify_installed_payload(wheel, purelib, "cache-probe")
        self.assertEqual("installed_payload_mismatch", raised.exception.reason)

    def test_subprocess_output_and_decoding_fail_closed(self) -> None:
        with self.assertRaises(QualificationFailure) as output:
            _run(
                (
                    sys.executable,
                    "-c",
                    "import sys; sys.stdout.buffer.write(b'x' * 1_048_577)",
                ),
                reason="command_output_invalid",
            )
        self.assertEqual("command_output_invalid", output.exception.reason)

        completed = subprocess.CompletedProcess(
            args=("probe",),
            returncode=0,
            stdout=b"\xff",
            stderr=b"",
        )
        with self.assertRaises(QualificationFailure) as decoding:
            _decode_stdout(
                completed,
                encoding="utf-8",
                reason="command_encoding_invalid",
            )
        self.assertEqual("command_encoding_invalid", decoding.exception.reason)


def _write_probe(source: Path, marker: str) -> None:
    (source / ".gitignore").write_text(
        "/build/\n/*.egg-info/\n",
        encoding="utf-8",
    )
    (source / "pyproject.toml").write_text(
        """\
[build-system]
requires = ["setuptools==83.0.0"]
build-backend = "setuptools.build_meta"

[project]
name = "cache-probe"
version = "0.0.0"

[project.scripts]
cache-probe = "cache_probe:main"

[tool.setuptools]
py-modules = ["cache_probe"]
""",
        encoding="utf-8",
    )
    (source / "cache_probe.py").write_text(
        f"""\
def main():
    print({marker!r})
""",
        encoding="utf-8",
    )


def _commit(source: Path, message: str) -> str:
    if not (source / ".git").is_dir():
        _git(source, "init", "--quiet")
        _git(source, "config", "user.name", "Qualification Test")
        _git(source, "config", "user.email", "qualification@example.invalid")
    _git(source, "add", ".gitignore", "pyproject.toml", "cache_probe.py")
    _git(source, "commit", "--quiet", "-m", message)
    return _git(source, "rev-parse", "HEAD").strip()


def _git(source: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=source,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return completed.stdout


def _environment_bin(environment: Path) -> Path:
    return environment / ("Scripts" if os.name == "nt" else "bin")


def _command(*arguments: str) -> str:
    completed = subprocess.run(
        arguments,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return completed.stdout


if __name__ == "__main__":
    unittest.main()
