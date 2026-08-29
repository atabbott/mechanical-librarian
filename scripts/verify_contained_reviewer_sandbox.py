#!/usr/bin/env python3
"""Exercise shipped reviewer builders and runners in supported Docker."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

_PAYLOAD = r"""
import ctypes, errno, json, os, platform, resource, threading, time
numbers = {
    "x86_64": {"clone": 56, "clone3": 435, "fork": 57, "vfork": 58, "unshare": 272, "setns": 308},
    "aarch64": {"clone": 220, "clone3": 435, "unshare": 97, "setns": 268},
}[platform.machine()]
observed = []
thread = threading.Thread(target=lambda: observed.append("thread")); thread.start(); thread.join()
libc = ctypes.CDLL(None, use_errno=True); denied = {}
for name, number in numbers.items():
    ctypes.set_errno(0)
    arguments = (17, 0, 0, 0, 0) if name == "clone" else ((-1, 0) if name == "setns" else ())
    denied[name] = libc.syscall(number, *arguments) == -1 and ctypes.get_errno() == errno.EPERM
print(json.dumps({"denied": denied, "thread": observed == ["thread"], "payload_as": resource.getrlimit(resource.RLIMIT_AS)[0]}, sort_keys=True), flush=True)
time.sleep(1)
"""

_RUNNER = r"""
import hashlib, json, os, subprocess, sys, tempfile, threading, time
from contextlib import ExitStack
from pathlib import Path
from mechanical_librarian.adapters.reviewers import codex_device_auth as auth_module
from mechanical_librarian.adapters.reviewers import contained_reviewer as reviewer_module
from mechanical_librarian.adapters.reviewers.codex_device_auth import CodexAuthCommand, CodexDeviceAuthManager
from mechanical_librarian.adapters.reviewers.contained_reviewer import ContainedReviewerAdapter, ContainedReviewerProfile, _bound_artifact, _resource_command, _run_owned_command, _sealed_model_catalog_descriptor, no_process_seccomp_descriptor, prepare_contained_device_auth_command
from mechanical_librarian.application.contained_reviewer_contract import ContainedReviewerIdentity
from mechanical_librarian.bootstrap.composition import _CodexAuthLeaseCoordinator

payload = sys.argv[1]; codex = Path('/opt/mechanical-librarian/reviewer/bin/codex'); bwrap = Path('/opt/mechanical-librarian/reviewer/bin/bwrap')
digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
bwrap_version = Path('/opt/mechanical-librarian/reviewer/metadata/bubblewrap-version.txt').read_text().strip()
coordinator = _CodexAuthLeaseCoordinator(); lock = threading.Lock(); inside = maximum = 0; runs = []; errors = []
operation_identities = []
original_popen = reviewer_module.subprocess.Popen
def recording_popen(*arguments, **keywords):
    process = original_popen(*arguments, **keywords); operation_identities.append(identity(process.pid)); return process
reviewer_module.subprocess.Popen = recording_popen

def descendants(root):
    parents = {}
    for entry in os.listdir('/proc'):
        if not entry.isdigit(): continue
        try:
            raw = Path('/proc', entry, 'stat').read_text(); tail = raw[raw.rindex(')') + 2:].split(); parents[int(entry)] = int(tail[1])
        except (OSError, ValueError, IndexError): pass
    result = []; frontier = [root]
    while frontier:
        parent = frontier.pop(); children = [pid for pid, ppid in parents.items() if ppid == parent]; result.extend(children); frontier.extend(children)
    return result

def identity(pid):
    raw = Path(f'/proc/{pid}/stat').read_text(); tail = raw[raw.rindex(')') + 2:].split(); return [pid, int(tail[2]), tail[19]]

def sleeping_auth_command(profile, home, action):
    prepared = prepare_contained_device_auth_command(profile, home, action)
    command = list(prepared.command); payload_index = command.index('/runtime/codex', command.index('/runtime/seccomp_exec.py'))
    if action == 'start':
        script = "import time; print('Open this URL in your browser'); print('https://auth.openai.com/codex/device'); print('Enter this one-time code (expires in 15 minutes)'); print('ABCD-EFGH', flush=True); time.sleep(30)"
    else:
        script = "import time; time.sleep(30)"
    command[payload_index:] = ['/runtime/python', '-c', script]
    return CodexAuthCommand(command, prepared.environment, prepared.pass_fds, prepared.close)

def successful_status_command(profile, home, action):
    if action != 'status': raise RuntimeError('foreground proof admitted a non-status action')
    prepared = prepare_contained_device_auth_command(profile, home, action)
    command = list(prepared.command); payload_index = command.index('/runtime/codex', command.index('/runtime/seccomp_exec.py'))
    command[payload_index:] = ['/runtime/python', '-c', "print('Logged in using ChatGPT', flush=True)"]
    return CodexAuthCommand(command, prepared.environment, prepared.pass_fds, prepared.close)

def wait_for_device(manager, states, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = manager.status()
        if state.state in states: return state
        time.sleep(.01)
    raise RuntimeError('device manager state timeout')

def exercise():
    global inside, maximum
    stage = 'profile'
    try:
        with tempfile.TemporaryDirectory(prefix='reviewer-sandbox-proof-') as temporary, coordinator.activity():
            root = Path(temporary); auth = root/'auth'; jobs = root/'jobs'; staging = root/'staging'
            for path in (auth, jobs, staging): path.mkdir(mode=0o700)
            profile = ContainedReviewerProfile(codex, bwrap, auth, jobs, ContainedReviewerIdentity(digest(codex), 'codex-cli 0.147.0', digest(bwrap), bwrap_version, 'gpt-5.6-sol', 'medium', 30, 65536, 1073741824, 32, 131072))
            adapter = ContainedReviewerAdapter(profile)
            with lock: inside += 1; maximum = max(maximum, inside)
            try:
                with ExitStack() as stack:
                    bound_codex = stack.enter_context(_bound_artifact(codex, profile.identity.executable_sha256)); bound_bwrap = stack.enter_context(_bound_artifact(bwrap, profile.identity.sandbox_sha256))
                    before = set(descendants(os.getpid())); stage = 'version'; adapter._verify_codex(bound_codex, bound_bwrap)
                    if set(descendants(os.getpid())) != before: raise RuntimeError('version tree survived')
                    stage = 'status'
                    prepared = prepare_contained_device_auth_command(profile, staging, 'status')
                    try:
                        status = _run_owned_command(_resource_command(list(prepared.command)), env=dict(prepared.environment), pass_fds=prepared.pass_fds, timeout=30)
                        if status.returncode == 0: raise RuntimeError('unconfigured status unexpectedly succeeded')
                    finally: prepared.close()
                    if set(descendants(os.getpid())) != before: raise RuntimeError('failed status tree survived')
                    prepared = prepare_contained_device_auth_command(profile, staging, 'status')
                    saved_token = reviewer_module._process_start_token
                    reviewer_module._process_start_token = lambda _pid: (_ for _ in ()).throw(reviewer_module.ContainedReviewerProcessError('runtime'))
                    try:
                        try: _run_owned_command(_resource_command(list(prepared.command)), env=dict(prepared.environment), pass_fds=prepared.pass_fds, timeout=30)
                        except reviewer_module.ContainedReviewerProcessError: pass
                        else: raise RuntimeError('identity failure path did not fail')
                    finally:
                        reviewer_module._process_start_token = saved_token; prepared.close()
                    if set(descendants(os.getpid())) != before: raise RuntimeError('identity-failed tree survived')
                    prepared = prepare_contained_device_auth_command(profile, staging, 'status')
                    try:
                        timeout_command = list(prepared.command); payload_index = timeout_command.index('/runtime/codex', timeout_command.index('/runtime/seccomp_exec.py')); timeout_command[payload_index:] = ['/runtime/python', '-c', 'import time; time.sleep(30)']
                        try: _run_owned_command(_resource_command(timeout_command), env=dict(prepared.environment), pass_fds=prepared.pass_fds, timeout=1)
                        except subprocess.TimeoutExpired: pass
                        else: raise RuntimeError('timeout path did not time out')
                    finally: prepared.close()
                    if set(descendants(os.getpid())) != before: raise RuntimeError('timed-out tree survived')
                    prepared = prepare_contained_device_auth_command(profile, staging, 'status')
                    try:
                        cancel_command = list(prepared.command); payload_index = cancel_command.index('/runtime/codex', cancel_command.index('/runtime/seccomp_exec.py')); cancel_command[payload_index:] = ['/runtime/python', '-c', 'import time; time.sleep(30)']
                        class CancelOnce:
                            def __init__(self, process): self.process = process; self.cancelled = False
                            def __getattr__(self, name): return getattr(self.process, name)
                            def wait(self, *arguments, **keywords):
                                if not self.cancelled: self.cancelled = True; raise KeyboardInterrupt
                                return self.process.wait(*arguments, **keywords)
                        saved = reviewer_module.subprocess.Popen
                        reviewer_module.subprocess.Popen = lambda *arguments, **keywords: CancelOnce(saved(*arguments, **keywords))
                        try:
                            try: _run_owned_command(_resource_command(cancel_command), env=dict(prepared.environment), pass_fds=prepared.pass_fds, timeout=30)
                            except KeyboardInterrupt: pass
                            else: raise RuntimeError('cancel path did not cancel')
                        finally: reviewer_module.subprocess.Popen = saved
                    finally: prepared.close()
                    if set(descendants(os.getpid())) != before: raise RuntimeError('cancelled tree survived')
                    stage = 'manager-cancel'
                    cancel_manager = CodexDeviceAuthManager(root/'manager-cancel'/'profile', lambda home, action: sleeping_auth_command(profile, home, action), popen=recording_popen)
                    cancel_manager.start(); wait_for_device(cancel_manager, {'issued', 'waiting'}); cancel_manager.cancel(); cancel_manager.close()
                    manager_cancel_reaped = set(descendants(os.getpid())) == before
                    if not manager_cancel_reaped: raise RuntimeError('device manager cancel tree survived')
                    stage = 'manager-timeout'; saved_deadline = auth_module._DEVICE_DEADLINE_SECONDS; auth_module._DEVICE_DEADLINE_SECONDS = 1
                    try:
                        timeout_manager = CodexDeviceAuthManager(root/'manager-timeout'/'profile', lambda home, action: sleeping_auth_command(profile, home, action), popen=recording_popen)
                        timeout_manager.start(); wait_for_device(timeout_manager, {'expired'}); timeout_manager.close()
                    finally: auth_module._DEVICE_DEADLINE_SECONDS = saved_deadline
                    manager_timeout_reaped = set(descendants(os.getpid())) == before
                    if not manager_timeout_reaped: raise RuntimeError('device manager timeout tree survived')
                    stage = 'manager-handoff'
                    predecessor = CodexDeviceAuthManager(root/'manager-handoff'/'profile', lambda home, action: sleeping_auth_command(profile, home, action), popen=recording_popen)
                    predecessor.start(); wait_for_device(predecessor, {'issued', 'waiting'}); predecessor.close()
                    successor = CodexDeviceAuthManager(root/'manager-handoff'/'profile', lambda home, action: sleeping_auth_command(profile, home, action), popen=recording_popen)
                    successor.clear(); successor.close()
                    manager_handoff_reaped = set(descendants(os.getpid())) == before
                    if not manager_handoff_reaped: raise RuntimeError('device manager handoff tree survived')
                    stage = 'manager-quarantine'; quarantine_coordinator = _CodexAuthLeaseCoordinator(); saved_deadline = auth_module._DEVICE_DEADLINE_SECONDS; auth_module._DEVICE_DEADLINE_SECONDS = 1
                    try:
                        quarantine_manager = CodexDeviceAuthManager(root/'manager-quarantine'/'profile', lambda home, action: sleeping_auth_command(profile, home, action), popen=recording_popen, activity_guard=quarantine_coordinator.activity, quarantine_activity=quarantine_coordinator.quarantine)
                        quarantine_manager.start(); wait_for_device(quarantine_manager, {'issued', 'waiting'}); saved_terminate = quarantine_manager._terminate_process_group
                        quarantine_manager._terminate_process_group = lambda _process, _identity: (_ for _ in ()).throw(auth_module.process_groups.ReviewerProcessGroupError('runtime'))
                        if quarantine_manager._thread is None: raise RuntimeError('device manager quarantine thread absent')
                        quarantine_manager._thread.join(timeout=5)
                        if quarantine_manager._thread.is_alive() or quarantine_manager._process is None or quarantine_manager._process_group is None: raise RuntimeError('device manager quarantine did not retain failed ownership')
                        try:
                            with quarantine_coordinator.activity(): pass
                        except auth_module.CodexDeviceAuthError: pass
                        else: raise RuntimeError('quarantined device manager admitted another operation')
                        process = quarantine_manager._process; process_group = quarantine_manager._process_group; saved_terminate(process, process_group); quarantine_manager._release(process, process_group)
                    finally: auth_module._DEVICE_DEADLINE_SECONDS = saved_deadline
                    manager_quarantine_reaped = set(descendants(os.getpid())) == before
                    if not manager_quarantine_reaped: raise RuntimeError('device manager quarantine tree survived explicit proof cleanup')
                    stage = 'manager-foreground-quarantine'; foreground_coordinator = _CodexAuthLeaseCoordinator(); foreground_root = root/'manager-foreground-quarantine'; foreground_root.mkdir(mode=0o700); foreground_home = foreground_root/'profile'; foreground_home.mkdir(mode=0o700)
                    foreground_credential = foreground_home/'auth.json'; foreground_credential.write_text(json.dumps({'auth_mode': 'chatgpt', 'OPENAI_API_KEY': None, 'tokens': {'id_token': 'id', 'access_token': 'access', 'refresh_token': 'refresh', 'account_id': 'account'}, 'last_refresh': '2026-08-28T00:00:00Z'})); foreground_credential.chmod(0o600)
                    foreground_manager = CodexDeviceAuthManager(foreground_home, lambda home, action: successful_status_command(profile, home, action), popen=recording_popen, activity_guard=foreground_coordinator.activity, quarantine_activity=foreground_coordinator.quarantine)
                    saved_finish = foreground_manager._finish_process_group; saved_terminate = foreground_manager._terminate_process_group
                    foreground_manager._finish_process_group = lambda _process, _identity: (_ for _ in ()).throw(auth_module.process_groups.ReviewerProcessGroupError('runtime'))
                    foreground_manager._terminate_process_group = lambda _process, _identity: (_ for _ in ()).throw(auth_module.process_groups.ReviewerProcessGroupError('runtime'))
                    operations_before_foreground = len(operation_identities); foreground_state = foreground_manager.recheck_credential_status()
                    if foreground_state.state != 'failed' or foreground_state.failure != 'protocol_or_process' or foreground_manager._process is None or foreground_manager._process_group is None: raise RuntimeError('foreground recheck did not quarantine retained ownership:' + json.dumps({'state': foreground_state.state, 'failure': foreground_state.failure, 'process': foreground_manager._process is not None, 'group': foreground_manager._process_group is not None, 'operations': len(operation_identities) - operations_before_foreground}))
                    operations_after_foreground = len(operation_identities)
                    foreground_manager.start()
                    if len(operation_identities) != operations_after_foreground or operations_after_foreground <= operations_before_foreground: raise RuntimeError('foreground quarantine admitted another process')
                    try:
                        with foreground_coordinator.activity(): pass
                    except auth_module.CodexDeviceAuthError: pass
                    else: raise RuntimeError('foreground quarantine admitted another activity')
                    process = foreground_manager._process; process_group = foreground_manager._process_group; foreground_manager._finish_process_group = saved_finish; foreground_manager._terminate_process_group = saved_terminate; saved_terminate(process, process_group); foreground_manager._release(process, process_group)
                    manager_foreground_quarantine_reaped = set(descendants(os.getpid())) == before
                    if not manager_foreground_quarantine_reaped: raise RuntimeError('foreground quarantine tree survived explicit proof cleanup')
                    stage = 'review'; job = jobs/'job'; provider = job/'provider'; job.mkdir(mode=0o700); provider.mkdir(mode=0o700)
                    credential = job/'auth.json'; credential.write_text('{}'); credential.chmod(0o600); result = job/'result.json'
                    stdin = stack.enter_context(open(os.devnull, 'rb')); stdout = stack.enter_context(tempfile.TemporaryFile()); stderr = stack.enter_context(tempfile.TemporaryFile())
                    seccomp = stack.enter_context(no_process_seccomp_descriptor()); catalog = stack.enter_context(_sealed_model_catalog_descriptor())
                    command = adapter._sandbox_command(job, result, (), provider, bound_codex.invocation_path, bound_bwrap.invocation_path, str(credential), seccomp, catalog)
                    payload_index = command.index('/runtime/codex', command.index('/runtime/seccomp_exec.py')); command[payload_index:] = ['/runtime/python', '-c', payload]
                    captured = {}
                    def dispatched(pid, pgid, token):
                        captured['broker'] = [pid, pgid, token]
                        def sample():
                            deadline = time.monotonic() + 5
                            while time.monotonic() < deadline:
                                children = descendants(pid)
                                if len(children) == 3:
                                    ids = [identity(value) for value in [pid, *children]]; limits = []
                                    for value in [pid, *children]:
                                        row = next(line for line in Path(f'/proc/{value}/limits').read_text().splitlines() if line.startswith('Max address space')); limits.append(int(row.split()[3]))
                                    if sorted(limits) == [67108864, 67108864, 67108864, 872415232]:
                                        captured.update(ids=ids, limits=sorted(limits)); return
                                time.sleep(.01)
                        thread = threading.Thread(target=sample); captured['thread'] = thread; thread.start()
                    credential_fd = os.open(credential, os.O_RDONLY)
                    try: returncode = adapter._run_gated(command, bound_codex, bound_bwrap, stdin.fileno(), stdout.fileno(), stderr.fileno(), dispatched, credential_descriptor=credential_fd, seccomp_descriptor=seccomp, model_catalog_descriptor=catalog)
                    finally: os.close(credential_fd)
                    captured['thread'].join(timeout=6); stdout.seek(0); document = json.loads(stdout.readline()); ids = captured['ids']; pgids = {value[1] for value in ids}
                    if returncode != 0 or len(ids) != 4 or len(pgids) != 1 or ids[0] != captured['broker'] or set(descendants(os.getpid())) != before: raise RuntimeError('owned reviewer tree proof failed')
                    document.update({'limits': captured['limits'], 'lineage': ids, 'one_group': True, 'manager_cancel_reaped': manager_cancel_reaped, 'manager_timeout_reaped': manager_timeout_reaped, 'manager_handoff_reaped': manager_handoff_reaped, 'manager_quarantine_reaped': manager_quarantine_reaped, 'manager_foreground_quarantine_reaped': manager_foreground_quarantine_reaped}); runs.append(document)
            finally:
                with lock: inside -= 1
    except BaseException as error: errors.append(stage + ':' + type(error).__name__ + ':' + str(error))

threads = [threading.Thread(target=exercise) for _ in range(2)]
for thread in threads: thread.start()
for thread in threads: thread.join(timeout=60)
if errors or any(thread.is_alive() for thread in threads) or len(runs) != 2: raise RuntimeError(json.dumps({'errors': errors, 'runs': len(runs)}))
document = runs[0]; document.update({'non_overlap': maximum == 1, 'runs': len(runs), 'distinct_identities': runs[0]['lineage'][0] != runs[1]['lineage'][0], 'distinct_operation_identities': len({tuple(value) for value in operation_identities}) == len(operation_identities), 'operation_count': len(operation_identities), 'failure_reaped': True, 'identity_failure_reaped': True, 'timeout_reaped': True, 'cancel_reaped': True}); print(json.dumps(document, sort_keys=True))
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    arguments = parser.parse_args()
    root = Path(__file__).parents[1]
    completed = subprocess.run(
        (
            "docker",
            "run",
            "--rm",
            "--read-only",
            "--memory",
            "2g",
            "--memory-swap",
            "2g",
            "--pids-limit",
            "128",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--security-opt",
            f"seccomp={root / 'config/seccomp/contained-reviewer.json'}",
            "--entrypoint",
            "/opt/mechanical-librarian/bin/python",
            arguments.image,
            "-c",
            _RUNNER,
            _PAYLOAD,
        ),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-4096:] or completed.stdout[-4096:])
    document = json.loads(completed.stdout)
    expected_limits = [64 * 1024**2, 64 * 1024**2, 64 * 1024**2, 832 * 1024**2]
    if not (
        document["thread"] is True
        and document["one_group"] is True
        and document["non_overlap"] is True
        and document["distinct_identities"] is True
        and document["distinct_operation_identities"] is True
        and document["operation_count"] >= 10
        and document["failure_reaped"] is True
        and document["identity_failure_reaped"] is True
        and document["timeout_reaped"] is True
        and document["cancel_reaped"] is True
        and document["manager_cancel_reaped"] is True
        and document["manager_timeout_reaped"] is True
        and document["manager_handoff_reaped"] is True
        and document["manager_quarantine_reaped"] is True
        and document["manager_foreground_quarantine_reaped"] is True
        and document["runs"] == 2
        and document["limits"] == expected_limits
        and document["payload_as"] == expected_limits[-1]
        and all(document["denied"].values())
    ):
        raise RuntimeError(
            "contained reviewer sandbox proof failed: "
            + json.dumps(document, sort_keys=True)
        )
    print(json.dumps(document, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
