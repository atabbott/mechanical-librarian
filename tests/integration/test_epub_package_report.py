from __future__ import annotations

import hashlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import mechanical_librarian.bootstrap as bootstrap
from mechanical_librarian.adapters.evidence_extractors.process import (
    _minimal_environment,
    _stop,
    run_epub_package_child,
)
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion
from mechanical_librarian.domain.local_evidence import (
    EpubPackageLimits,
    LocalEvidenceCapability,
    LocalEvidenceOutcome,
    LocalEvidenceReport,
)
from mechanical_librarian.infrastructure.local_media import (
    LocalMediaError,
    open_local_media,
    validate_relative_reference,
)
from tests.support.epub_packages import EPUB3_PACKAGE, epub_bytes


LIBRARY_ID = "01234567-89ab-4def-8123-456789abcdef"


def configuration(mount: Path) -> dict[str, object]:
    return {
        "schema_version": "config-v1",
        "instance_name": "epub-test",
        "connections": [
            {
                "adapter": "audiobookshelf",
                "endpoint": "https://library.example",
                "secret_reference": "docker-secret/abs",
                "enabled_libraries": [LIBRARY_ID],
                "capability_overrides": ["inventory-read"],
            }
        ],
        "media_mounts": [
            {
                "name": "books",
                "library_type": "ebook",
                "container_path": str(mount),
                "service_path": "/library/books",
                "read_only": True,
            }
        ],
        "limits": {"parser_concurrency": 1},
        "providers": [],
        "reviewers": [],
        "notifications": [],
        "backups_enabled": True,
    }


class EpubPackageProcessTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux production limit cohort")
    def test_linux_child_limits_are_exact_and_block_an_additional_process(self) -> None:
        program = '''
import resource
import subprocess
import sys
from mechanical_librarian.bootstrap._epub_child import _apply_limits
_apply_limits()
expected = {
    resource.RLIMIT_AS: 256 * 1024 * 1024,
    resource.RLIMIT_CORE: 0,
    resource.RLIMIT_CPU: 30,
    resource.RLIMIT_NOFILE: 64,
    resource.RLIMIT_NPROC: 1,
}
if any(resource.getrlimit(kind)[0] != value for kind, value in expected.items()):
    raise SystemExit(2)
try:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
except OSError:
    print("linux-limits-qualified")
else:
    child.kill()
    child.wait()
    raise SystemExit(3)
'''
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", program],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace"))
        self.assertEqual(completed.stdout, b"linux-limits-qualified\n")

    def test_child_process_has_a_closed_environment_and_inert_working_directory(self) -> None:
        environment = _minimal_environment()
        self.assertEqual(environment["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertNotIn("PYTHONPATH", environment)
        self.assertNotIn("HOME", environment)
        self.assertNotIn(str(Path.cwd()), repr(environment))

        expected = Fingerprint("sha256", SchemaVersion("source-v1"), "a" * 64)
        descriptor = os.open(os.devnull, os.O_RDONLY)
        self.addCleanup(os.close, descriptor)
        observed: dict[str, object] = {}
        real_popen = subprocess.Popen

        def capture(arguments: object, **keywords: object) -> subprocess.Popen[bytes]:
            observed.update(keywords)
            return real_popen(
                [sys.executable, "-c", "import os; os.write(1, b'{}\\n')"],
                **keywords,
            )

        with patch(
            "mechanical_librarian.adapters.evidence_extractors.process.subprocess.Popen",
            side_effect=capture,
        ):
            run_epub_package_child(descriptor, expected, EpubPackageLimits())
        self.assertEqual(observed["cwd"], "/")
        self.assertEqual(observed["env"], environment)

    def test_child_protocol_returns_one_canonical_bounded_report(self) -> None:
        value = epub_bytes()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "book.epub"
            path.write_bytes(value)
            descriptor = os.open(path, os.O_RDONLY)
            self.addCleanup(os.close, descriptor)
            import hashlib

            expected = Fingerprint("sha256", SchemaVersion("source-v1"), hashlib.sha256(value).hexdigest())
            report = run_epub_package_child(descriptor, expected, EpubPackageLimits())
        self.assertEqual(report.outcome, LocalEvidenceOutcome.SUCCESS)
        self.assertLessEqual(len(report.to_json().encode("utf-8")), 524_288)
        self.assertEqual(report.source_fingerprint, expected)

    def test_cancellation_terminates_and_reaps_then_a_sibling_remains_usable(self) -> None:
        value = epub_bytes()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "book.epub"
            path.write_bytes(value)
            descriptor = os.open(path, os.O_RDONLY)
            self.addCleanup(os.close, descriptor)
            import hashlib

            expected = Fingerprint("sha256", SchemaVersion("source-v1"), hashlib.sha256(value).hexdigest())
            cancelled = run_epub_package_child(
                descriptor,
                expected,
                EpubPackageLimits(),
                cancelled=lambda: True,
            )
            sibling = run_epub_package_child(descriptor, expected, EpubPackageLimits())
        self.assertEqual(cancelled.outcome, LocalEvidenceOutcome.CANCELLATION)
        self.assertEqual(sibling.outcome, LocalEvidenceOutcome.SUCCESS)

    def test_timeout_reaps_the_child_then_a_sibling_remains_usable(self) -> None:
        value = epub_bytes()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "book.epub"
            path.write_bytes(value)
            descriptor = os.open(path, os.O_RDONLY)
            self.addCleanup(os.close, descriptor)
            import hashlib

            expected = Fingerprint("sha256", SchemaVersion("source-v1"), hashlib.sha256(value).hexdigest())
            children: list[subprocess.Popen[bytes]] = []
            real_popen = subprocess.Popen

            def capture(*arguments: object, **keywords: object) -> subprocess.Popen[bytes]:
                child = real_popen(*arguments, **keywords)
                children.append(child)
                return child

            with patch(
                "mechanical_librarian.adapters.evidence_extractors.process.subprocess.Popen",
                side_effect=capture,
            ):
                timed_out = run_epub_package_child(
                    descriptor,
                    expected,
                    EpubPackageLimits(),
                    wall_seconds=0.001,
                )
            sibling = run_epub_package_child(descriptor, expected, EpubPackageLimits())
        self.assertEqual(timed_out.outcome, LocalEvidenceOutcome.TIMEOUT)
        self.assertTrue(children)
        self.assertTrue(all(child.poll() is not None for child in children))
        self.assertEqual(sibling.outcome, LocalEvidenceOutcome.SUCCESS)

    def test_stop_terminates_a_term_resistant_descendant_after_leader_exit(self) -> None:
        program = (
            "import subprocess,sys; "
            "child=subprocess.Popen([sys.executable,'-c',"
            "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print(1, flush=True); time.sleep(30)'],"
            "stdout=subprocess.PIPE); "
            "child.stdout.readline(); "
            "print(child.pid, flush=True)"
        )
        leader = subprocess.Popen(
            [sys.executable, "-c", program],
            stdout=subprocess.PIPE,
            start_new_session=True,
        )
        assert leader.stdout is not None
        descendant = int(leader.stdout.readline().decode("ascii"))
        leader.stdout.close()
        leader.wait(timeout=5)
        _stop(leader)

        deadline = time.monotonic() + 2.0
        alive = True
        while alive and time.monotonic() < deadline:
            try:
                os.kill(descendant, 0)
            except ProcessLookupError:
                alive = False
                break
            stat_path = Path(f"/proc/{descendant}/stat")
            if stat_path.exists() and stat_path.read_text(encoding="ascii").split()[2] == "Z":
                alive = False
                break
            time.sleep(0.02)
        self.assertFalse(alive)

    def test_parent_loss_closes_the_control_pipe_and_child_exits(self) -> None:
        value = epub_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.epub"
            pid_file = root / "child.pid"
            source.write_bytes(value)
            import hashlib

            digest = hashlib.sha256(value).hexdigest()
            program = '''
import os
from pathlib import Path
import mechanical_librarian.adapters.evidence_extractors.process as process_module
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion
from mechanical_librarian.domain.local_evidence import EpubPackageLimits
source, pid_file, digest = __import__("sys").argv[1:]
real_popen = process_module.subprocess.Popen
def capture(*arguments, **keywords):
    child = real_popen(*arguments, **keywords)
    Path(pid_file).write_text(str(child.pid), encoding="ascii")
    return child
process_module.subprocess.Popen = capture
descriptor = os.open(source, os.O_RDONLY)
try:
    process_module.run_epub_package_child(
        descriptor,
        Fingerprint("sha256", SchemaVersion("source-v1"), digest),
        EpubPackageLimits(),
        wall_seconds=60,
    )
finally:
    os.close(descriptor)
'''
            parent = subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", program, str(source), str(pid_file), digest],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            deadline = time.monotonic() + 10.0
            child_pid = None
            while parent.poll() is None and time.monotonic() < deadline:
                try:
                    content = pid_file.read_text(encoding="ascii").strip()
                    if content:
                        child_pid = int(content)
                        break
                except (FileNotFoundError, ValueError):
                    pass
                time.sleep(0.01)
            self.assertIsNotNone(
                child_pid, "outer process did not launch the extraction child"
            )
            assert child_pid is not None
            os.kill(parent.pid, signal.SIGKILL)
            parent.wait(timeout=5)
            alive = True
            deadline = time.monotonic() + 5.0
            while alive and time.monotonic() < deadline:
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    alive = False
                    break
                stat_path = Path(f"/proc/{child_pid}/stat")
                if stat_path.exists() and stat_path.read_text(encoding="ascii").split()[2] == "Z":
                    alive = False
                    break
                time.sleep(0.02)
            if alive:
                try:
                    os.killpg(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            self.assertFalse(alive)

    def test_composed_cli_uses_logical_reference_and_redacts_private_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mount = root / "media"
            mount.mkdir()
            (mount / "book.epub").write_bytes(epub_bytes())
            config = root / "configuration.json"
            config.write_text(json.dumps(configuration(mount)), encoding="utf-8")
            output = io.StringIO()
            errors = io.StringIO()
            with redirect_stdout(output), redirect_stderr(errors):
                exit_code = bootstrap.main(
                    [
                        "epub-package-report",
                        "--config",
                        str(config),
                        "--mount",
                        "books",
                        "--file",
                        "book.epub",
                    ]
                )
            report = json.loads(output.getvalue())
            combined = output.getvalue() + errors.getvalue()
            self.assertEqual(exit_code, 0, errors.getvalue())
            self.assertEqual(report["outcome"], "success")
            self.assertEqual(report["schema"], "local-evidence-report-v1")
            self.assertEqual(report["limits"]["profile"], "epub-package-limits-v1")
            self.assertEqual(report["source_fingerprint"]["schema"], "source-v1")
            self.assertTrue(report["observations"])
            self.assertEqual(
                report["observations"][0]["extractor_version"],
                "epub-package-v1",
            )
            self.assertIn(
                report["observations"][0]["compatibility_version"],
                {"epub-3.3", "opf-2.0.1"},
            )
            self.assertIn("archive_entries", report["usage"])
            self.assertEqual(errors.getvalue(), "")
            self.assertNotIn(str(root), combined)
            self.assertNotIn("book.epub", combined)
            self.assertNotIn("docker-secret", combined)

    def test_supported_cli_returns_exact_hostile_package_outcomes_and_streams(self) -> None:
        malformed_package = EPUB3_PACKAGE.replace(
            b"  <manifest>", b"  <unknown/>\n  <manifest>", 1
        )
        required_encryption = b'''<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#"><CipherData><CipherReference URI="EPUB/package.opf"/></CipherData></EncryptedData></encryption>'''
        cases = (
            (
                "malformed",
                epub_bytes(package=malformed_package),
                None,
                LocalEvidenceOutcome.MALFORMED,
                5,
            ),
            (
                "encrypted",
                epub_bytes(encryption=required_encryption),
                None,
                LocalEvidenceOutcome.ENCRYPTED,
                4,
            ),
            (
                "resource",
                epub_bytes(),
                {"archive_entries": 1},
                LocalEvidenceOutcome.RESOURCE_LIMIT,
                5,
            ),
        )
        for name, value, configured_limits, expected, expected_exit in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                mount = root / "media"
                mount.mkdir()
                (mount / "book.epub").write_bytes(value)
                data = configuration(mount)
                if configured_limits is not None:
                    data["evidence"] = {"epub_package": configured_limits}
                config = root / "configuration.json"
                config.write_text(json.dumps(data), encoding="utf-8")
                output = io.StringIO()
                errors = io.StringIO()
                with redirect_stdout(output), redirect_stderr(errors):
                    exit_code = bootstrap.main(
                        [
                            "epub-package-report",
                            "--config",
                            str(config),
                            "--mount",
                            "books",
                            "--file",
                            "book.epub",
                        ]
                    )
                report = json.loads(errors.getvalue())
                self.assertEqual(exit_code, expected_exit)
                self.assertEqual(output.getvalue(), "")
                self.assertEqual(report["outcome"], expected.value)
                self.assertNotIn(str(root), errors.getvalue())

    def test_invalid_reference_and_symlink_are_rejected_before_media_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.epub"
            target.write_bytes(epub_bytes())
            (root / "linked.epub").symlink_to(target)
            with self.assertRaises(LocalMediaError):
                open_local_media(str(root), "../target.epub")
            with self.assertRaises(LocalMediaError):
                open_local_media(str(root), "linked.epub")
            with self.assertRaises(LocalMediaError):
                validate_relative_reference("bad\ud800.epub")

    def test_non_regular_media_is_nonblocking_and_does_not_leak_descriptors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "directory").mkdir()
            fifo = root / "pipe"
            os.mkfifo(fifo)
            before = len(os.listdir("/dev/fd"))
            for _ in range(50):
                with self.assertRaises(LocalMediaError):
                    open_local_media(str(root), "directory")
            started = time.monotonic()
            with self.assertRaises(LocalMediaError):
                open_local_media(str(root), "pipe")
            self.assertLess(time.monotonic() - started, 1.0)
            self.assertEqual(len(os.listdir("/dev/fd")), before)

    def test_same_reference_mutation_is_reported_as_unstable_not_partial_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mount = root / "media"
            mount.mkdir()
            source = mount / "book.epub"
            source.write_bytes(epub_bytes())
            config = root / "configuration.json"
            config.write_text(json.dumps(configuration(mount)), encoding="utf-8")

            def mutate(
                _descriptor: int,
                expected: Fingerprint,
                limits: EpubPackageLimits,
                **_keywords: object,
            ) -> LocalEvidenceReport:
                source.write_bytes(epub_bytes(version=2))
                return LocalEvidenceReport(
                    LocalEvidenceOutcome.SUCCESS,
                    LocalEvidenceCapability.PACKAGE,
                    limits,
                    expected,
                )

            with patch.object(bootstrap, "run_epub_package_child", side_effect=mutate):
                report = bootstrap._run_epub_package_report(config, "books", "book.epub")
            self.assertEqual(report.outcome, LocalEvidenceOutcome.UNSTABLE_SOURCE)
            self.assertFalse(report.observations or report.declarations)

    def test_initial_source_hash_failure_is_unavailable_before_a_baseline_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mount = root / "media"
            mount.mkdir()
            (mount / "book.epub").write_bytes(epub_bytes())
            config = root / "configuration.json"
            config.write_text(json.dumps(configuration(mount)), encoding="utf-8")

            with patch.object(
                bootstrap,
                "sha256_descriptor",
                side_effect=LocalMediaError("synthetic read failure"),
            ):
                report = bootstrap._run_epub_package_report(
                    config, "books", "book.epub"
                )

            self.assertEqual(report.outcome, LocalEvidenceOutcome.UNAVAILABLE_SOURCE)
            self.assertFalse(report.observations or report.declarations)

    def test_parent_rejects_malformed_and_excessive_child_output(self) -> None:
        value = epub_bytes()
        expected = Fingerprint(
            "sha256",
            SchemaVersion("source-v1"),
            hashlib.sha256(value).hexdigest(),
        )
        real_popen = subprocess.Popen

        def replacement(program: str) -> object:
            def spawn(_arguments: object, **keywords: object) -> subprocess.Popen[bytes]:
                return real_popen([sys.executable, "-c", program], **keywords)

            return spawn

        source = tempfile.TemporaryFile()
        self.addCleanup(source.close)
        source.write(value)
        source.flush()
        descriptor = source.fileno()
        with patch(
            "mechanical_librarian.adapters.evidence_extractors.process.subprocess.Popen",
            side_effect=replacement("import os; os.write(1, b'{}\\n')"),
        ):
            malformed = run_epub_package_child(descriptor, expected, EpubPackageLimits())
        with patch(
            "mechanical_librarian.adapters.evidence_extractors.process.subprocess.Popen",
            side_effect=replacement("import os; os.write(1, b'x' * 1048577)"),
        ):
            excessive = run_epub_package_child(descriptor, expected, EpubPackageLimits())
        with patch(
            "mechanical_librarian.adapters.evidence_extractors.process.subprocess.Popen",
            side_effect=replacement("import os; os.write(2, b'x' * 65537)"),
        ):
            excessive_stderr = run_epub_package_child(
                descriptor, expected, EpubPackageLimits()
            )
        self.assertEqual(malformed.outcome, LocalEvidenceOutcome.CHILD_FAILURE)
        self.assertEqual(excessive.outcome, LocalEvidenceOutcome.CHILD_FAILURE)
        self.assertEqual(
            excessive_stderr.outcome, LocalEvidenceOutcome.CHILD_FAILURE
        )
        sibling = run_epub_package_child(
            descriptor, expected, EpubPackageLimits(), wall_seconds=5
        )
        self.assertEqual(sibling.outcome, LocalEvidenceOutcome.SUCCESS)

    def test_nonzero_child_crash_is_reaped_and_sibling_succeeds(self) -> None:
        value = epub_bytes()
        expected = Fingerprint(
            "sha256",
            SchemaVersion("source-v1"),
            hashlib.sha256(value).hexdigest(),
        )
        source = tempfile.TemporaryFile()
        self.addCleanup(source.close)
        source.write(value)
        source.flush()
        descriptor = source.fileno()
        real_popen = subprocess.Popen
        spawned: list[subprocess.Popen[bytes]] = []

        def replacement(
            _arguments: object,
            **keywords: object,
        ) -> subprocess.Popen[bytes]:
            process = real_popen(
                [sys.executable, "-I", "-B", "-c", "import os; os._exit(73)"],
                **keywords,
            )
            spawned.append(process)
            return process

        with patch(
            "mechanical_librarian.adapters.evidence_extractors.process.subprocess.Popen",
            side_effect=replacement,
        ):
            crashed = run_epub_package_child(
                descriptor, expected, EpubPackageLimits(), wall_seconds=5
            )
        self.assertEqual(crashed.outcome, LocalEvidenceOutcome.CHILD_FAILURE)
        self.assertEqual(len(spawned), 1)
        self.assertEqual(spawned[0].returncode, 73)
        with self.assertRaises(ChildProcessError):
            os.waitpid(spawned[0].pid, os.WNOHANG)
        sibling = run_epub_package_child(
            descriptor, expected, EpubPackageLimits(), wall_seconds=5
        )
        self.assertEqual(sibling.outcome, LocalEvidenceOutcome.SUCCESS)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux resource limits")
    def test_memory_and_descriptor_exhaustion_are_typed_and_parent_recovers(self) -> None:
        value = epub_bytes()
        expected = Fingerprint(
            "sha256",
            SchemaVersion("source-v1"),
            hashlib.sha256(value).hexdigest(),
        )
        source = tempfile.TemporaryFile()
        self.addCleanup(source.close)
        source.write(value)
        source.flush()
        descriptor = source.fileno()
        real_popen = subprocess.Popen

        programs = (
            """
import os, resource
resource.setrlimit(resource.RLIMIT_AS, (96 * 1024 * 1024, 96 * 1024 * 1024))
blocks = []
try:
    while True:
        blocks.append(b'x' * (1024 * 1024))
except MemoryError:
    os._exit(71)
""",
            """
import errno, os, resource
resource.setrlimit(resource.RLIMIT_NOFILE, (16, 16))
descriptors = []
try:
    while True:
        descriptors.append(os.open(os.devnull, os.O_RDONLY))
except OSError as error:
    os._exit(71 if error.errno == errno.EMFILE else 72)
""",
        )

        for program in programs:
            def replacement(
                _arguments: object,
                **keywords: object,
            ) -> subprocess.Popen[bytes]:
                return real_popen(
                    [sys.executable, "-I", "-B", "-c", program], **keywords
                )

            with self.subTest(program=hash(program)):
                with patch(
                    "mechanical_librarian.adapters.evidence_extractors.process.subprocess.Popen",
                    side_effect=replacement,
                ):
                    exhausted = run_epub_package_child(
                        descriptor,
                        expected,
                        EpubPackageLimits(),
                        wall_seconds=5,
                    )
                self.assertEqual(
                    exhausted.outcome, LocalEvidenceOutcome.RESOURCE_LIMIT
                )
        sibling = run_epub_package_child(
            descriptor, expected, EpubPackageLimits(), wall_seconds=5
        )
        self.assertEqual(sibling.outcome, LocalEvidenceOutcome.SUCCESS)

    def test_cpu_limit_exit_is_mapped_to_a_resource_limit(self) -> None:
        expected = Fingerprint("sha256", SchemaVersion("source-v1"), "a" * 64)
        descriptor = os.open(os.devnull, os.O_RDONLY)
        self.addCleanup(os.close, descriptor)
        real_popen = subprocess.Popen
        program = '''
import os
import resource
import signal
signal.signal(signal.SIGXCPU, lambda *_arguments: os._exit(71))
resource.setrlimit(resource.RLIMIT_CPU, (1, 2))
while True:
    pass
'''

        def replacement(_arguments: object, **keywords: object) -> subprocess.Popen[bytes]:
            return real_popen([sys.executable, "-I", "-B", "-c", program], **keywords)

        with patch(
            "mechanical_librarian.adapters.evidence_extractors.process.subprocess.Popen",
            side_effect=replacement,
        ):
            report = run_epub_package_child(
                descriptor,
                expected,
                EpubPackageLimits(),
                wall_seconds=5,
            )
        self.assertEqual(report.outcome, LocalEvidenceOutcome.RESOURCE_LIMIT)


if __name__ == "__main__":
    unittest.main()
