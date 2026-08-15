from __future__ import annotations

import base64
import hashlib
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import scripts.qualify_exact_artifact as qualifier_module
from scripts.qualify_exact_artifact import (
    QualificationFailure,
    _decode_stdout,
    _qualification_environment,
    _qualify_runtime_console,
    _run,
    _runtime_help_is_closed,
    _validate_wheel,
    _verify_installed_payload,
    qualify_exact_artifact,
)


class ExactArtifactQualificationTests(unittest.TestCase):
    def test_runtime_probe_requires_packaged_react_assets_for_product(self) -> None:
        help_output = (
            b"usage: mechanical-librarian "
            b"{review-serve,policy-preview,cover-review,review-activity,provider-recovery}\n"
            b"{review-serve,policy-preview,cover-review,review-activity,provider-recovery}\n"
            b"    review-serve  service\n"
            b"    policy-preview  preview\n"
            b"    cover-review  cover decision\n"
            b"    review-activity  guarded effect activity\n"
            b"    provider-recovery  durable provider recovery\n"
        )
        completed = (
            subprocess.CompletedProcess(("console", "--help"), 0, help_output, b""),
            subprocess.CompletedProcess(("python", "-I"), 0, b"", b""),
            subprocess.CompletedProcess(("python", "-I"), 0, b"", b""),
            subprocess.CompletedProcess(("python", "-I"), 0, b"", b""),
        )
        with tempfile.TemporaryDirectory() as directory, patch.object(
            qualifier_module, "_run", side_effect=completed
        ) as run, patch.object(qualifier_module, "_qualify_guarded_effect_lifecycle"):
            _qualify_runtime_console(
                Path(directory) / "bin" / "mechanical-librarian",
                Path(directory),
                {},
                require_react_assets=True,
            )

        self.assertEqual(run.call_count, 4)

    def test_runtime_probe_decodes_help_and_requires_workbench_absence(self) -> None:
        help_output = (
            b"usage: mechanical-librarian "
            b"{review-serve,policy-preview,cover-review,review-activity,provider-recovery}\n"
            b"{review-serve,policy-preview,cover-review,review-activity,provider-recovery}\n"
            b"    review-serve  service\n"
            b"    policy-preview  preview\n"
            b"    cover-review  cover decision\n"
            b"    review-activity  guarded effect activity\n"
            b"    provider-recovery  durable provider recovery\n"
        )
        completed = (
            subprocess.CompletedProcess(("console", "--help"), 0, help_output, b""),
            subprocess.CompletedProcess(("python", "-I"), 0, b"", b""),
        )
        with tempfile.TemporaryDirectory() as directory, patch.object(
            qualifier_module, "_run", side_effect=completed
        ), patch.object(qualifier_module, "_qualify_guarded_effect_lifecycle"):
            result = _qualify_runtime_console(
                Path(directory) / "bin" / "mechanical-librarian",
                Path(directory),
                {},
            )
        self.assertEqual(result, help_output)
        self.assertFalse(_runtime_help_is_closed(
            help_output.decode() + "    maintenance  unsupported extra command\n"
        ))

    def test_runtime_probe_runs_from_owner_only_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            _write_probe(source, "source")
            source_sha = _commit(source, "source")
            observed_modes: list[int] = []

            def inspect_run_directory(
                console: Path,
                run_directory: Path,
                environment: dict[str, str],
                *,
                require_react_assets: bool = False,
            ) -> bytes:
                del console, environment, require_react_assets
                observed_modes.append(stat.S_IMODE(run_directory.stat().st_mode))
                return b"review-probe\n"

            with patch.object(
                qualifier_module,
                "_qualify_runtime_console",
                side_effect=inspect_run_directory,
            ):
                evidence = qualify_exact_artifact(
                    source,
                    source_sha,
                    distribution="cache-probe",
                    import_name="cache_probe",
                    console_script="cache-probe",
                    console_args=(),
                    test_args=None,
                    runtime_probe=True,
                )

        self.assertEqual([0o700], observed_modes)
        self.assertTrue(evidence.runtime_surface_executed)

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
                    test_args=None,
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

                _write_probe(source, "source-b", with_test=True)
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
                partial = qualify_exact_artifact(
                    source,
                    second_sha,
                    distribution="cache-probe",
                    import_name="cache_probe",
                    console_script="cache-probe",
                    console_args=(),
                    test_args=(),
                )
                retained_console_exists = (
                    environment_path
                    / ("Scripts" if os.name == "nt" else "bin")
                    / "cache-probe"
                ).is_file()
                retained_wheel_count = len(tuple(wheel_output.glob("*.whl")))

        self.assertEqual(first.distribution_version, "0.0.0")
        self.assertEqual(second.distribution_version, "0.0.0")
        self.assertFalse(first.full_suite_executed)
        self.assertEqual(first.qualification_tier, "none")
        self.assertFalse(first.required_suite_executed)
        self.assertFalse(first.slow_qualification_executed)
        self.assertFalse(partial.full_suite_executed)
        self.assertFalse(second.runtime_surface_executed)
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
        self.assertTrue(second.locked_dependencies)
        self.assertTrue(second.full_suite_executed)
        self.assertEqual(second.qualification_tier, "legacy-complete")
        self.assertFalse(second.required_suite_executed)
        self.assertFalse(second.slow_qualification_executed)
        self.assertTrue(second.wheel_record_validated)
        self.assertTrue(retained_console_exists)
        self.assertEqual(1, retained_wheel_count)

    def test_command_timeout_is_distinct_from_a_nonzero_failure(self) -> None:
        with patch.object(qualifier_module, "_COMMAND_TIMEOUT_SECONDS", 0.01):
            with self.assertRaises(QualificationFailure) as timed_out:
                _run(
                    (sys.executable, "-c", "import time; time.sleep(10)"),
                    reason="installed_test_suite_failed",
                )
        self.assertEqual("installed_test_suite_timeout", timed_out.exception.reason)

        with self.assertRaises(QualificationFailure) as failed:
            _run(
                (sys.executable, "-c", "raise SystemExit(2)"),
                reason="installed_test_suite_failed",
            )
        self.assertEqual("installed_test_suite_failed", failed.exception.reason)

    def test_qualification_environment_discards_inherited_uv_controls(self) -> None:
        inherited = {
            "ML_FRONTEND_PREBUILT_DIR": "/tmp/stale-assets",
            "ML_OPENAPI_PATH": "/tmp/stale-openapi.json",
            "UV_CONFIG_FILE": "/tmp/untrusted-uv.toml",
            "UV_NO_BUILD_ISOLATION": "1",
            "UV_NO_VERIFY_HASHES": "1",
            "UV_PROJECT": "/tmp/untrusted-project",
        }
        with patch.dict(os.environ, inherited):
            environment = _qualification_environment()

        self.assertEqual("1", environment["UV_NO_CACHE"])
        self.assertEqual("1", environment["PYTHONNOUSERSITE"])
        self.assertNotIn(
            Path(sys.executable).parent.resolve(),
            {
                Path(entry).resolve()
                for entry in environment["PATH"].split(os.pathsep)
                if entry
            },
        )
        self.assertFalse(
            set(inherited).intersection(environment),
            environment,
        )

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
                        test_args=None,
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
                    test_args=None,
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
                    test_args=None,
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
            members = _with_record(members)
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

    def test_invalid_wheel_record_fails_before_install(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "cache_probe-0.0.0-py3-none-any.whl"
            members = _with_record(
                {
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
            )
            record_name = "cache_probe-0.0.0.dist-info/RECORD"
            members[record_name] = members[record_name].replace(
                b"sha256=",
                b"sha256=invalid",
                1,
            )
            with ZipFile(wheel, "w") as archive:
                for name, content in members.items():
                    archive.writestr(name, content)

            with self.assertRaises(QualificationFailure) as raised:
                _validate_wheel(wheel, "cache-probe")
        self.assertEqual("wheel_record_invalid", raised.exception.reason)

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

    @unittest.skipUnless(os.name == "posix", "process-group contract is POSIX")
    def test_subprocess_descendant_cannot_extend_deadline(self) -> None:
        statement = (
            "import subprocess, sys; "
            "subprocess.Popen("
            "[sys.executable, '-c', 'import time; time.sleep(5)']"
            ")"
        )
        started = time.monotonic()
        with patch.object(
            qualifier_module,
            "_COMMAND_TIMEOUT_SECONDS",
            0.5,
        ):
            with self.assertRaises(QualificationFailure) as raised:
                _run(
                    (sys.executable, "-c", statement),
                    reason="command_descendant_invalid",
                )
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual("command_descendant_invalid", raised.exception.reason)


def _write_probe(source: Path, marker: str, *, with_test: bool = False) -> None:
    (source / ".gitignore").write_text(
        "/build/\n*.egg-info/\n",
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

[dependency-groups]
dev = ["setuptools==83.0.0"]

[tool.setuptools]
package-dir = {"" = "src"}
py-modules = ["cache_probe"]
""",
        encoding="utf-8",
    )
    module = source / "src" / "cache_probe.py"
    module.parent.mkdir(exist_ok=True)
    module.write_text(
        f"""\
def main():
    print({marker!r})
""",
        encoding="utf-8",
    )
    if with_test:
        module.write_text(
            f"""\
MARKER = {marker!r}


def main():
    print(MARKER)
""",
            encoding="utf-8",
        )
        tests = source / "tests"
        tests.mkdir(exist_ok=True)
        (tests / "__init__.py").write_text("", encoding="utf-8")
        (tests / "test_probe.py").write_text(
            f"""\
import shutil
import subprocess
import unittest

import cache_probe


class InstalledProbeTests(unittest.TestCase):
    def test_exact_installed_source(self):
        self.assertEqual({marker!r}, cache_probe.MARKER)
        console = shutil.which("cache-probe")
        self.assertIsNotNone(console)
        completed = subprocess.run(
            (console,),
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual({marker!r} + "\\n", completed.stdout)
""",
            encoding="utf-8",
        )


def _commit(source: Path, message: str) -> str:
    if not (source / ".git").is_dir():
        _git(source, "init", "--quiet")
        _git(source, "config", "user.name", "Qualification Test")
        _git(source, "config", "user.email", "qualification@example.invalid")
    _command("uv", "lock", "--quiet", cwd=source)
    _git(source, "add", ".")
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


def _with_record(members: dict[str, bytes]) -> dict[str, bytes]:
    values = dict(members)
    record_name = "cache_probe-0.0.0.dist-info/RECORD"
    rows = []
    for name, content in sorted(values.items()):
        digest = base64.urlsafe_b64encode(
            hashlib.sha256(content).digest()
        ).rstrip(b"=")
        rows.append(
            name.encode("utf-8")
            + b",sha256="
            + digest
            + b","
            + str(len(content)).encode("ascii")
            + b"\n"
        )
    rows.append(record_name.encode("utf-8") + b",,\n")
    values[record_name] = b"".join(rows)
    return values


def _command(*arguments: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        arguments,
        cwd=cwd,
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
