"""REMOTE=<host> scripts/check.sh copies the tree and runs the gate there.

The fakes stand in for ssh and rsync. What they record is what the host would
see. What the script prints is what the operator would see. A planted token
must never show up in the operator's output.
"""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "scripts" / "check.sh"
SECRET = "super-secret-token"

SSH_FAKE = """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

log = Path(os.environ["CHECK_FAKE_LOG"])
mode = os.environ["CHECK_FAKE_MODE"]
secret = os.environ["CHECK_FAKE_SECRET"]
args = sys.argv[1:]
positional = []
i = 0
while i < len(args):
    if args[i] == "-o" and i + 1 < len(args):
        i += 2
        continue
    positional.append(args[i])
    i += 1
text = " ".join(positional)
with log.open("a") as fh:
    fh.write("SSH " + text + "\\n")
if "scripts/check.sh" in text:
    if "env -u REMOTE" not in text:
        print("REMOTE still set")
        sys.exit(99)
    if mode == "gate-fail":
        print("ruff failed")
        sys.exit(2)
    print("GATE PASS")
    sys.exit(0)
sys.stderr.write(secret + "\\n")
if mode == "ssh-fail":
    sys.exit(255)
if "mktemp" in text:
    if mode == "bad-dir":
        print("/tmp/not-safe")
        sys.exit(0)
    print("/tmp/arda-check.Abc123")
    sys.exit(0)
if "rm -rf" in text:
    with log.open("a") as fh:
        fh.write("CLEANUP\\n")
    if mode == "cleanup-fail":
        sys.exit(255)
    sys.exit(0)
sys.exit(97)
"""

RSYNC_FAKE = """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

log = Path(os.environ["CHECK_FAKE_LOG"])
mode = os.environ["CHECK_FAKE_MODE"]
secret = os.environ["CHECK_FAKE_SECRET"]
with log.open("a") as fh:
    fh.write("RSYNC " + " ".join(sys.argv[1:]) + "\\n")
sys.stderr.write(secret + "\\n")
sys.exit(1 if mode == "rsync-fail" else 0)
"""


def run_remote(tmp_path: Path, remote: str, mode: str) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "transport.log"
    for name, body in (("ssh", SSH_FAKE), ("rsync", RSYNC_FAKE)):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "REMOTE": remote,
        "CHECK_FAKE_MODE": mode,
        "CHECK_FAKE_LOG": str(log),
        "CHECK_FAKE_SECRET": SECRET,
        "ARDA_SECRET": SECRET,
    }
    proc = subprocess.Popen(
        ["bash", str(CHECK)],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise AssertionError("remote check hung") from None
    return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)


def output_of(res: subprocess.CompletedProcess[str]) -> str:
    return res.stdout + res.stderr


def log_text(tmp_path: Path) -> str:
    path = tmp_path / "transport.log"
    return path.read_text() if path.exists() else ""


def test_ssh_failure_exits_nonzero_and_hides_transport_stderr(tmp_path):
    res = run_remote(tmp_path, "sleeper.example", "ssh-fail")
    assert res.returncode != 0
    assert res.stderr.strip() == "check: ssh failed"
    assert SECRET not in output_of(res)
    assert "RSYNC" not in log_text(tmp_path)


def test_rsync_failure_exits_nonzero_and_removes_the_temp_dir(tmp_path):
    res = run_remote(tmp_path, "sleeper.example", "rsync-fail")
    assert res.returncode != 0
    assert res.stderr.strip() == "check: rsync failed"
    assert SECRET not in output_of(res)
    log = log_text(tmp_path)
    assert "CLEANUP" in log
    assert "scripts/check.sh" not in log


def test_bad_host_does_not_reach_ssh_or_echo_the_value(tmp_path):
    res = run_remote(tmp_path, f"sleeper.example;$(echo {SECRET})", "ssh-fail")
    assert res.returncode != 0
    assert SECRET not in output_of(res)
    assert log_text(tmp_path) == ""


def test_host_that_is_the_secret_is_not_printed(tmp_path):
    res = run_remote(tmp_path, SECRET, "ssh-fail")
    assert res.returncode != 0
    assert SECRET not in output_of(res)


def test_gate_pass_relays_output_and_unsets_remote(tmp_path):
    res = run_remote(tmp_path, "solomon@sleeper.example", "gate-pass")
    assert res.returncode == 0, output_of(res)
    assert "GATE PASS" in res.stdout
    assert SECRET not in output_of(res)
    log = log_text(tmp_path)
    assert "BatchMode=yes" in log
    assert "--exclude" in log and ".env" in log
    assert ".git" in log
    assert "gitignore" in log
    assert "env -u REMOTE" in log
    assert "bash scripts/check.sh" in log
    assert "CLEANUP" in log
    assert SECRET not in log


def test_gate_failure_keeps_the_remote_status(tmp_path):
    res = run_remote(tmp_path, "sleeper.example", "gate-fail")
    assert res.returncode == 2
    assert "ruff failed" in res.stdout
    assert SECRET not in output_of(res)


def test_untrusted_temp_path_is_not_used(tmp_path):
    res = run_remote(tmp_path, "sleeper.example", "bad-dir")
    assert res.returncode != 0
    assert res.stderr.strip() == "check: ssh failed"
    log = log_text(tmp_path)
    assert "RSYNC" not in log
    assert "rm -rf" not in log
    assert SECRET not in output_of(res)


def test_cleanup_ssh_failure_fails_a_passing_gate(tmp_path):
    res = run_remote(tmp_path, "sleeper.example", "cleanup-fail")
    assert res.returncode != 0
    assert "check: ssh failed" in res.stderr
    assert "GATE PASS" in res.stdout
    assert SECRET not in output_of(res)
