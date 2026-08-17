from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import pytest
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


def test_runtime_probe_requires_packaged_react_assets_for_product() -> None:
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
    with (
        tempfile.TemporaryDirectory() as directory,
        patch.object(qualifier_module, "_run", side_effect=completed) as run,
        patch.object(qualifier_module, "_qualify_guarded_effect_lifecycle"),
    ):
        _qualify_runtime_console(
            Path(directory) / "bin" / "mechanical-librarian",
            Path(directory),
            {},
            require_react_assets=True,
        )

    assert run.call_count == 4


def test_runtime_probe_decodes_help_and_requires_workbench_absence() -> None:
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
    with (
        tempfile.TemporaryDirectory() as directory,
        patch.object(qualifier_module, "_run", side_effect=completed),
        patch.object(qualifier_module, "_qualify_guarded_effect_lifecycle"),
    ):
        result = _qualify_runtime_console(
            Path(directory) / "bin" / "mechanical-librarian",
            Path(directory),
            {},
        )
    assert result == help_output
    assert not _runtime_help_is_closed(
        help_output.decode() + "    maintenance  unsupported extra command\n"
    )


def test_runtime_probe_runs_from_owner_only_directory() -> None:
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

    assert [0o700] == observed_modes
    assert evidence.runtime_surface_executed


def test_same_version_shared_cache_executes_exact_second_source() -> None:
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
            assert "source-a\n" == stale_output
            assert any(cache.iterdir())

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

    assert first.distribution_version == "0.0.0"
    assert second.distribution_version == "0.0.0"
    assert not first.full_suite_executed
    assert first.qualification_tier == "none"
    assert not first.required_suite_executed
    assert not first.slow_qualification_executed
    assert not partial.full_suite_executed
    assert not second.runtime_surface_executed
    assert first.source_sha != second.source_sha
    assert first.wheel_sha256 != second.wheel_sha256
    assert hashlib.sha256(b"source-b\n").hexdigest() == second.console_stdout_sha256
    assert second.wheel_payload_sha256 == second.installed_payload_sha256
    assert second.retained_wheel
    assert second.retained_environment
    assert second.locked_dependencies
    assert second.full_suite_executed
    assert second.qualification_tier == "legacy-complete"
    assert not second.required_suite_executed
    assert not second.slow_qualification_executed
    assert second.wheel_record_validated
    assert retained_console_exists
    assert 1 == retained_wheel_count


def test_command_timeout_is_distinct_from_a_nonzero_failure() -> None:
    with patch.object(qualifier_module, "_COMMAND_TIMEOUT_SECONDS", 0.01):
        with pytest.raises(QualificationFailure) as timed_out:
            _run(
                (sys.executable, "-c", "import time; time.sleep(10)"),
                reason="installed_test_suite_failed",
            )
    assert "installed_test_suite_timeout" == timed_out.value.reason

    with pytest.raises(QualificationFailure) as failed:
        _run(
            (sys.executable, "-c", "raise SystemExit(2)"),
            reason="installed_test_suite_failed",
        )
    assert "installed_test_suite_failed" == failed.value.reason


def test_a_failing_installed_suite_reports_a_redacted_diagnosis() -> None:
    """Prove a real failing installed suite survives as more than its reason.

    The whole path is exercised rather than faked: a wheel is built, installed
    into an isolated environment, and its suite is run by that environment's
    interpreter and fails. Before this, `_run` captured the child's streams and
    discarded them with the exception, so a failing gate reported
    `installed_test_suite_failed` and nothing an engineer could act on, while
    the failing identifier and its traceback sat in a buffer that was dropped.
    """

    secret = "probe-secret-value-0123456789"
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory)
        _write_probe(source, "source", with_test=True, failing=True)
        source_sha = _commit(source, "failing installed suite")
        with patch.dict(os.environ, {"ML_PROBE_TOKEN": secret}):
            with pytest.raises(QualificationFailure) as raised:
                qualify_exact_artifact(
                    source,
                    source_sha,
                    distribution="cache-probe",
                    import_name="cache_probe",
                    console_script="cache-probe",
                    console_args=(),
                )

    assert "installed_test_suite_failed" == raised.value.reason
    diagnostic = raised.value.diagnostic
    # The failing identifier and the assertion detail, neither of which the
    # reason alone ever carried.
    assert "InstalledProbeTests.test_exact_installed_source" in diagnostic
    assert "probe-failure-detail" in diagnostic
    # Both captured streams, not only the one the engine reports failures on.
    assert "probe-stdout-marker" in diagnostic
    # The child quoted a secret-named variable out of the environment the
    # qualifier constructed for it. The diagnostic names the variable it came
    # from and never the value, on both streams it reached.
    assert secret not in diagnostic
    assert diagnostic.count("[redacted $ML_PROBE_TOKEN]") == 2


def test_an_opaque_value_is_redacted_under_a_name_that_never_says_secret() -> None:
    # Auditing a real constructed environment is what found the first half:
    # `BAGGAGE` held 185 opaque identifying characters, and no name vocabulary
    # would ever have reached it. `hunter2` is the second half: a secret-named
    # variable is redacted almost regardless of length, because an
    # eight-character floor printed a six-character credential verbatim. Its
    # remaining floor separates a credential from a flag and nothing else. A
    # path-valued variable is deliberately kept, because a path is what makes a
    # reported traceback locatable.
    #
    # The two connection strings are the third half, and they are why "is a
    # path" had to stop meaning "contains a separator". Neither name matches
    # the vocabulary, and both hold slashes, so a contained-separator exemption
    # published both sets of credentials verbatim. `REDIS_URL` is also
    # twenty-one characters, so the length floor would not have caught it
    # either. A credential-free URL stays readable.
    redactions = dict(
        qualifier_module._environment_redactions(
            {
                "BAGGAGE": "session.id=41f0c2e9a7b64d18,user.id=9c3e77a1",
                "HOME": "/home/runner/work/mechanical-librarian",
                "GH_TOKEN": "ghp_probe0123456789",
                "DB_PASSWORD": "hunter2",
                "CARD_PIN_TOKEN": "4821",
                "SDK_HAS_OAUTH_REFRESH": "1",
                "EMPTY_TOKEN": "",
                "SHLVL": "1",
                "DATABASE_URL": "postgres://user:password@host/db",
                "REDIS_URL": "rediss://:pw@h:6379/0",
                "PUBLIC_ENDPOINT": "https://api.example.invalid/v1",
                "RELATIVE_OUTPUT": "./build/generated/review-api-v1.d.ts",
            }
        )
    )
    assert redactions == {
        "session.id=41f0c2e9a7b64d18,user.id=9c3e77a1": "[redacted $BAGGAGE]",
        "ghp_probe0123456789": "[redacted $GH_TOKEN]",
        "hunter2": "[redacted $DB_PASSWORD]",
        # Four characters is the shortest a credential can be -- a PIN -- and
        # is redacted. `SDK_HAS_OAUTH_REFRESH` is a boolean flag whose name
        # matches the vocabulary on `AUTH`; substring-replacing its `1` rewrote
        # every digit in a real diagnostic and left it unreadable, protecting
        # nothing. Three characters carry no credential entropy.
        "4821": "[redacted $CARD_PIN_TOKEN]",
        "postgres://user:password@host/db": "[redacted $DATABASE_URL]",
        "rediss://:pw@h:6379/0": "[redacted $REDIS_URL]",
        # A long non-path value with no credentials in it: redacted on shape
        # alone, which is the floor rule doing its job rather than the URL one.
        "https://api.example.invalid/v1": "[redacted $PUBLIC_ENDPOINT]",
    }
    # `REDIS_URL` is under the shape rule's floor, so only the credential rule
    # can have reached it. Asserting the length keeps that case honest if the
    # floor ever moves.
    assert len("rediss://:pw@h:6379/0") < qualifier_module._MIN_OPAQUE_VALUE_CHARACTERS
    # Longest first, so a value containing another is replaced whole rather
    # than leaving a fragment of it behind.
    lengths = [
        len(value)
        for value, _ in qualifier_module._environment_redactions(
            {"A_TOKEN": "short", "B_TOKEN": "a much longer secret value"}
        )
    ]
    assert lengths == sorted(lengths, reverse=True)


def test_a_secret_straddling_the_retained_boundary_leaks_no_fragment() -> None:
    """Redaction runs over the whole stream, before anything is dropped.

    Truncating first cut a straddling value in two: `replace` matched nothing,
    because the whole value was gone, and the suffix was printed verbatim. The
    value below also carries multi-byte characters and is positioned so the
    byte boundary falls inside one of them, which is the same failure one level
    down -- a `replace` decode of an already-truncated stream turns that
    character into U+FFFD and breaks the match for the rest of the value.
    """

    secret = "probe-token-" + "é" * 20 + "-tail"
    encoded = secret.encode("utf-8")
    assert len(encoded) == 57
    # 12 ASCII characters plus the first byte of the first two-byte character,
    # so the retained boundary lands mid-sequence and mid-value.
    lead = 13
    suffix = b"z" * (
        qualifier_module._MAX_DIAGNOSTIC_STREAM_BYTES - (len(encoded) - lead)
    )
    captured = bytearray(b"y" * 100 + encoded + suffix)

    diagnostic = qualifier_module._command_diagnostic(
        "installed_test_suite_failed",
        captured,
        bytearray(),
        {"PROBE_TOKEN": secret},
    )

    assert "[redacted $PROBE_TOKEN]" in diagnostic
    assert secret not in diagnostic
    # The fragment is the leak, and it is what a placeholder-only assertion
    # would miss: the placeholder can be present while a suffix sits beside it.
    assert "é" * 19 + "-tail" not in diagnostic
    assert "-tail" not in diagnostic
    # The stream genuinely exceeded the bound, so the boundary was crossed.
    assert "[elided " in diagnostic


def test_an_inserted_placeholder_is_never_rescanned() -> None:
    """One pass, so a placeholder's own text cannot match another value.

    Replacing value by value in sequence rescans what the previous replacement
    wrote, and the arrangement below is what makes that reachable rather than
    merely conceivable. Values are applied longest first, so the cascade needs
    the value matching the inserted text to be the *shorter* of the two: the
    long one is replaced, its placeholder is written into the body, and the
    short one then finds `TOKEN]` inside that placeholder. `TOKEN]` appears
    nowhere in what the child wrote, so under sequential replacement the
    account reports a secret the child never emitted, from text this function
    inserted itself.
    """

    diagnostic = qualifier_module._command_diagnostic(
        "installed_test_suite_failed",
        bytearray(b"assertion failed: abcdefghij\n"),
        bytearray(),
        {"LONGSECRET_TOKEN": "abcdefghij", "SHORT_TOKEN": "TOKEN]"},
    )

    assert "[redacted $LONGSECRET_TOKEN]" in diagnostic
    assert "SHORT_TOKEN" not in diagnostic
    # One placeholder, not a placeholder nested inside a placeholder.
    assert diagnostic.count("[redacted") == 1


def test_a_broken_narrative_stream_cannot_suppress_the_evidence_record(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A consumer reading part of standard output must not cost the record.

    `... | head` closes the pipe, and the narrative write then raises. While
    that write came first, the `BrokenPipeError` propagated before the record
    was ever produced, so the stream deliberately separated as the disposable
    one could destroy the one that has to survive.
    """

    class _ClosedPipe:
        def write(self, value: str) -> int:
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self) -> None:
            raise BrokenPipeError(32, "Broken pipe")

        def fileno(self) -> int:
            # Raised rather than answered, so the redirection this provokes
            # cannot reach the real descriptor the test runner is using.
            raise io.UnsupportedOperation("fileno")

    failure = QualificationFailure(
        "installed_test_suite_failed",
        "==== exact-artifact failure: installed_test_suite_failed ====\n",
    )
    with patch.object(
        qualifier_module,
        "qualify_exact_artifact",
        side_effect=failure,
    ):
        with patch.object(sys, "stdout", _ClosedPipe()):
            assert 1 == qualifier_module.main(["--source-sha", "0" * 40])

    assert json.loads(capsys.readouterr().err) == {
        "reason": "installed_test_suite_failed",
        "schema": "exact-artifact-evidence-v4",
        "status": "failed",
    }


def test_the_narrative_diagnosis_stays_out_of_the_evidence_record(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The record is the artifact a candidate publishes, so it keeps its exact
    # path-free three-key shape. The narrative account carries the runner's own
    # paths and goes to the other stream.
    failure = QualificationFailure(
        "installed_test_suite_failed",
        "==== exact-artifact failure: installed_test_suite_failed ====\n"
        "---- child stdout ----\n/runner/work/checkout/tests/test_probe.py\n",
    )
    with patch.object(
        qualifier_module,
        "qualify_exact_artifact",
        side_effect=failure,
    ):
        assert 1 == qualifier_module.main(["--source-sha", "0" * 40])

    captured = capsys.readouterr()
    assert "/runner/work/checkout/tests/test_probe.py" in captured.out
    assert json.loads(captured.err) == {
        "reason": "installed_test_suite_failed",
        "schema": "exact-artifact-evidence-v4",
        "status": "failed",
    }


def test_qualification_environment_discards_inherited_uv_controls() -> None:
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

    assert "1" == environment["UV_NO_CACHE"]
    assert "1" == environment["PYTHONNOUSERSITE"]
    assert Path(sys.executable).parent.resolve() not in {
        Path(entry).resolve()
        for entry in environment["PATH"].split(os.pathsep)
        if entry
    }
    assert not set(inherited).intersection(environment), environment


def test_source_change_during_qualification_fails_closed() -> None:
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
            with pytest.raises(QualificationFailure) as raised:
                qualify_exact_artifact(
                    source,
                    source_sha,
                    distribution="cache-probe",
                    import_name="cache_probe",
                    console_script="cache-probe",
                    console_args=(),
                    test_args=None,
                )
    assert "source_not_clean" == raised.value.reason


def test_dirty_or_wrong_source_identity_fails_before_build() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory)
        _write_probe(source, "source")
        source_sha = _commit(source, "source")

        with pytest.raises(QualificationFailure) as wrong:
            qualify_exact_artifact(
                source,
                "0" * 40,
                distribution="cache-probe",
                import_name="cache_probe",
                console_script="cache-probe",
                console_args=(),
                test_args=None,
            )
        assert "source_identity_mismatch" == wrong.value.reason

        (source / "untracked").write_text("dirty", encoding="utf-8")
        with pytest.raises(QualificationFailure) as dirty:
            qualify_exact_artifact(
                source,
                source_sha,
                distribution="cache-probe",
                import_name="cache_probe",
                console_script="cache-probe",
                console_args=(),
                test_args=None,
            )
        assert "source_not_clean" == dirty.value.reason


def test_installed_payload_difference_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        wheel = root / "cache_probe-0.0.0-py3-none-any.whl"
        purelib = root / "purelib"
        members = {
            "cache_probe.py": b"print('wheel')\n",
            "cache_probe-0.0.0.dist-info/METADATA": (
                b"Metadata-Version: 2.4\nName: cache-probe\nVersion: 0.0.0\n\n"
            ),
            "cache_probe-0.0.0.dist-info/WHEEL": (
                b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
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

        with pytest.raises(QualificationFailure) as raised:
            _verify_installed_payload(wheel, purelib, "cache-probe")
    assert "installed_payload_mismatch" == raised.value.reason


def test_invalid_wheel_record_fails_before_install() -> None:
    with tempfile.TemporaryDirectory() as directory:
        wheel = Path(directory) / "cache_probe-0.0.0-py3-none-any.whl"
        members = _with_record(
            {
                "cache_probe.py": b"print('wheel')\n",
                "cache_probe-0.0.0.dist-info/METADATA": (
                    b"Metadata-Version: 2.4\nName: cache-probe\nVersion: 0.0.0\n\n"
                ),
                "cache_probe-0.0.0.dist-info/WHEEL": (
                    b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
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

        with pytest.raises(QualificationFailure) as raised:
            _validate_wheel(wheel, "cache-probe")
    assert "wheel_record_invalid" == raised.value.reason


def test_subprocess_output_and_decoding_fail_closed() -> None:
    with pytest.raises(QualificationFailure) as output:
        _run(
            (
                sys.executable,
                "-c",
                "import sys; sys.stdout.buffer.write(b'x' * 1_048_577)",
            ),
            reason="command_output_invalid",
        )
    assert "command_output_invalid" == output.value.reason

    completed = subprocess.CompletedProcess(
        args=("probe",),
        returncode=0,
        stdout=b"\xff",
        stderr=b"",
    )
    with pytest.raises(QualificationFailure) as decoding:
        _decode_stdout(
            completed,
            encoding="utf-8",
            reason="command_encoding_invalid",
        )
    assert "command_encoding_invalid" == decoding.value.reason


@pytest.mark.skipif(os.name != "posix", reason="process-group contract is POSIX")
def test_subprocess_descendant_cannot_extend_deadline() -> None:
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
        with pytest.raises(QualificationFailure) as raised:
            _run(
                (sys.executable, "-c", statement),
                reason="command_descendant_invalid",
            )
    assert time.monotonic() - started < 1.5
    assert "command_descendant_invalid" == raised.value.reason


def _write_probe(
    source: Path,
    marker: str,
    *,
    with_test: bool = False,
    failing: bool = False,
) -> None:
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
        # The failing variant writes to both captured streams and quotes a
        # secret-named variable out of the environment the qualifier built for
        # it, so one run proves the diagnostic survives and that the value does
        # not survive with it.
        body = (
            """\
import os
import unittest


class InstalledProbeTests(unittest.TestCase):
    def test_exact_installed_source(self):
        print("probe-stdout-marker", os.environ["ML_PROBE_TOKEN"])
        self.fail("probe-failure-detail " + os.environ["ML_PROBE_TOKEN"])
"""
            if failing
            else f"""\
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
"""
        )
        (tests / "test_probe.py").write_text(body, encoding="utf-8")


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
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=")
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
