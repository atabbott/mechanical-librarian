#!/usr/bin/env python3
"""Exercise the shipped reviewer broker and inner seccomp in supported Docker."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

_PAYLOAD = r"""
import ctypes, errno, json, os, platform, resource, threading, time
machine = platform.machine()
numbers = {
    "x86_64": {"clone": 56, "clone3": 435, "fork": 57, "vfork": 58, "unshare": 272, "setns": 308},
    "aarch64": {"clone": 220, "clone3": 435, "unshare": 97, "setns": 268},
}[machine]
observed = []
thread = threading.Thread(target=lambda: observed.append("thread"))
thread.start(); thread.join()
libc = ctypes.CDLL(None, use_errno=True)
denied = {}
for name, number in numbers.items():
    ctypes.set_errno(0)
    arguments = (17, 0, 0, 0, 0) if name == "clone" else ((-1, 0) if name == "setns" else ())
    result = libc.syscall(number, *arguments)
    denied[name] = result == -1 and ctypes.get_errno() == errno.EPERM
print(json.dumps({"arch": machine, "denied": denied, "thread": observed == ["thread"], "payload_as": resource.getrlimit(resource.RLIMIT_AS)[0]}, sort_keys=True), flush=True)
time.sleep(3)
"""

_RUNNER = r"""
import json, os, subprocess, sys, threading, time
from mechanical_librarian.adapters.reviewers.resource_budget import no_process_seccomp_descriptor
from mechanical_librarian.bootstrap.composition import _CodexAuthLeaseCoordinator
python = "/opt/mechanical-librarian/bin/python"
bwrap = "/opt/mechanical-librarian/reviewer/bin/bwrap"
wrapper = "/opt/mechanical-librarian/lib/python3.14/site-packages/mechanical_librarian/adapters/reviewers/seccomp_exec.py"
payload = sys.argv[1]
coordinator = _CodexAuthLeaseCoordinator()
state_lock = threading.Lock()
inside = 0
maximum_inside = 0
documents = []
errors = []

def exercise():
    global inside, maximum_inside
    try:
        with coordinator.activity(), no_process_seccomp_descriptor() as seccomp:
            with state_lock:
                inside += 1
                maximum_inside = max(maximum_inside, inside)
            try:
                command = [bwrap, "--die-with-parent", "--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup", "--clearenv", "--dir", "/runtime", "--ro-bind", "/opt", "/opt", "--ro-bind", "/usr", "/usr", "--ro-bind", "/usr/local", "/usr/local", "--ro-bind", "/lib", "/lib", "--dir", "/dev", "--dev-bind", "/dev/null", "/dev/null", "--dev-bind", "/dev/zero", "/dev/zero", "--dev-bind", "/dev/full", "/dev/full", "--dev-bind", "/dev/random", "/dev/random", "--dev-bind", "/dev/urandom", "/dev/urandom", "--dev-bind", "/dev/tty", "/dev/tty", "--tmpfs", "/tmp", "--perms", "0400", "--ro-bind-data", str(seccomp), "/runtime/seccomp.bpf", "--chdir", "/tmp", "--setenv", "PATH", "/opt/mechanical-librarian/bin:/usr/bin:/bin", "--setenv", "LD_LIBRARY_PATH", "/usr/local/lib:/usr/lib:/lib", python, "-I", wrapper, "/runtime/seccomp.bpf", python, "-c", payload]
                process = subprocess.Popen([python, "-m", "mechanical_librarian.adapters.reviewers.resource_exec", *command], pass_fds=(seccomp,), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
                assert process.stdout is not None
                line = process.stdout.readline()
                if not line:
                    process.wait(timeout=10)
                    diagnostics = process.stderr.read() if process.stderr is not None else ""
                    raise RuntimeError(diagnostics[-4096:] or f"broker exited {process.returncode}")
                document = json.loads(line)
                deadline = time.monotonic() + 2
                descendants = []
                while time.monotonic() < deadline:
                    parents = {}
                    for entry in os.listdir("/proc"):
                        if not entry.isdigit(): continue
                        try:
                            raw = open(f"/proc/{entry}/stat", encoding="ascii").read()
                            tail = raw[raw.rindex(")") + 2:].split()
                            parents[int(entry)] = int(tail[1])
                        except (OSError, ValueError, IndexError): pass
                    descendants = []
                    frontier = [process.pid]
                    while frontier:
                        parent = frontier.pop()
                        children = [pid for pid, ppid in parents.items() if ppid == parent]
                        descendants.extend(children); frontier.extend(children)
                    if len(descendants) == 3: break
                    time.sleep(.02)
                pids = [process.pid, *descendants]
                limits = []
                groups = []
                for pid in pids:
                    rows = open(f"/proc/{pid}/limits", encoding="ascii").read().splitlines()
                    row = next(value for value in rows if value.startswith("Max address space"))
                    limits.append(int(row.split()[3]))
                    groups.append(os.getpgid(pid))
                returncode = process.wait(timeout=10)
                stderr = process.stderr.read() if process.stderr is not None else ""
                if returncode != 0: raise RuntimeError(stderr[-4096:])
                document.update({"limits": sorted(limits), "one_tree": len(pids) == 4 and len(set(pids)) == 4, "one_group": len(set(groups)) == 1})
                documents.append(document)
            finally:
                with state_lock:
                    inside -= 1
    except BaseException as error:
        errors.append(repr(error))

threads = [threading.Thread(target=exercise) for _ in range(2)]
for thread in threads: thread.start()
for thread in threads: thread.join(timeout=20)
if errors or any(thread.is_alive() for thread in threads) or len(documents) != 2:
    raise RuntimeError(json.dumps({"errors": errors, "runs": len(documents)}))
document = documents[0]
document.update({"non_overlap": maximum_inside == 1, "runs": len(documents)})
print(json.dumps(document, sort_keys=True))
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
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-4096:])
    document = json.loads(completed.stdout)
    expected_limits = [
        64 * 1024**2,
        64 * 1024**2,
        64 * 1024**2,
        832 * 1024**2,
    ]
    if not (
        document["thread"] is True
        and document["one_tree"] is True
        and document["one_group"] is True
        and document["non_overlap"] is True
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
