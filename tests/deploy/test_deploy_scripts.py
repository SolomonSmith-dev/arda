"""End-to-end dry run of deploy/demo/deploy.sh and teardown.sh.

Docker, cloudflared, sudo, systemd, apt, DNS-over-HTTPS and the Cloudflare edge
are stubs (tests/deploy/harness). The demo app itself is real, in mock mode, on
127.0.0.1:5001; the fake edge listens on 127.0.0.1:5002 and plays the Free-plan
rate-limit rule. The tests run in order and share one server state, as a person
running the scripts again and again would.

Slow (about five minutes: step 16 is paced at one request per 2.5 s), so it is
opt-in: RUN_DEPLOY_HARNESS=1 uv run pytest tests/deploy -v. CI runs it in
.github/workflows/deploy-scripts.yml when the deploy files change. It deploys
the committed HEAD, not uncommitted edits.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).parent / "harness"
BRANCH = "harness"
KEY = "sk-ant-harness-0000"

pytestmark = [
    pytest.mark.deploy_harness,
    pytest.mark.skipif(
        os.environ.get("RUN_DEPLOY_HARNESS") != "1",
        reason="slow end-to-end harness; set RUN_DEPLOY_HARNESS=1",
    ),
]

# The two live-mode checks in step 7 cannot pass against the mock LLM. The
# harness runs a copy with exactly these lines swapped; anything else in the
# script runs as written.
MOCK_PATCHES = {
    """has "$st" '"mode":"live"'""": """has "$st" '"mode":"mock"'""",
    'has "$BODY" SimpleVectorStore': 'has "$BODY" "[mock mode, no LLM]"',
}


@dataclass
class Env:
    root: Path
    h: Path
    work: Path
    env: dict[str, str]

    @property
    def state(self) -> Path:
        return self.h / "state"

    def run(self, script: str, stdin: str, **extra: str) -> subprocess.CompletedProcess[str]:
        cmd = ["bash", script] + ([BRANCH] if script.endswith("deploy.harness.sh") else [])
        return subprocess.run(
            cmd,
            cwd=self.work,
            input=stdin,
            env={**self.env, **extra},
            capture_output=True,
            text=True,
            timeout=600,
        )

    def deploy(self, stdin: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return self.run("deploy/demo/deploy.harness.sh", stdin, **extra)

    def teardown(self, stdin: str) -> subprocess.CompletedProcess[str]:
        return self.run("deploy/demo/teardown.sh", stdin)

    def restart_app(self) -> None:
        subprocess.run([str(HARNESS / "app-restart.sh")], env=self.env, check=True, timeout=120)


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, timeout=120)


def _out(proc: subprocess.CompletedProcess[str]) -> str:
    return proc.stdout + proc.stderr


@pytest.fixture(scope="module")
def srv(tmp_path_factory: pytest.TempPathFactory):
    if not (_port_free(5001) and _port_free(5002)):
        pytest.skip("ports 5001/5002 are in use")
    for tool in ("git", "openssl", "sha256sum", "setsid"):
        if not shutil.which(tool):
            pytest.skip(f"{tool} not installed")

    root = tmp_path_factory.mktemp("deploy")
    h = root / "h"
    (h / "state").mkdir(parents=True)
    (h / "root" / "etc" / "apt" / "sources.list.d").mkdir(parents=True)
    (root / "home").mkdir()

    # origin: a bare repo holding HEAD as branch `harness`. CI checkouts are
    # shallow, so let the empty repo accept a shallow push.
    origin = root / "origin.git"
    _git("init", "-q", "--bare", str(origin), cwd=root)
    _git("config", "receive.shallowUpdate", "true", cwd=origin)
    _git("push", "-q", str(origin), f"HEAD:refs/heads/{BRANCH}", cwd=REPO)
    work = root / "arda-demo"
    _git("clone", "-q", "-b", BRANCH, str(origin), str(work), cwd=root)

    script = (work / "deploy/demo/deploy.sh").read_text()
    for old, new in MOCK_PATCHES.items():
        assert script.count(old) == 1, f"step 7 check changed, update MOCK_PATCHES: {old}"
        script = script.replace(old, new)
    patched = work / "deploy/demo/deploy.harness.sh"
    patched.write_text(script)
    patched.chmod(0o755)

    env = {
        "PATH": f"{HARNESS / 'bin'}:{os.environ['PATH']}",
        "HOME": str(root / "home"),
        "H": str(h),
        "HARNESS_DIR": str(HARNESS),
        "HARNESS_APP_DIR": str(REPO),
        "HARNESS_PYTHON": sys.executable,
        "DEMO_PUBLIC_URL": "http://127.0.0.1:5002",
        # deploy.harness.sh differs from deploy.sh; never re-exec into the strict one.
        "DEPLOY_REEXEC": "1",
        "LANG": "C.UTF-8",
    }
    (h / "state" / "edge-rule").touch()
    edge = subprocess.Popen(
        [sys.executable, str(HARNESS / "edge.py"), "5002",
         str(h / "state" / "edge.log"), str(h / "state" / "edge-rule")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        yield Env(root=root, h=h, work=work, env=env)
    finally:
        edge.terminate()
        pid_file = h / "state" / "app.pid"
        if pid_file.exists():
            with contextlib.suppress(ProcessLookupError, ValueError):
                os.kill(int(pid_file.read_text()), signal.SIGTERM)


def test_fresh_deploy_passes_every_step(srv: Env) -> None:
    proc = srv.deploy(f"{KEY}\nyes\nyes\n")
    out = _out(proc)
    assert proc.returncode == 0, out
    assert "All steps passed" in out
    assert "FAIL" not in out
    for step in ("13. Install the tunnel config", "16. Per-address limit", "18. Cloudflare"):
        assert step in out
    env_file = srv.work / ".env.demo"
    assert oct(env_file.stat().st_mode & 0o777) == "0o600"
    assert f"ANTHROPIC_API_KEY={KEY}" in env_file.read_text()


def test_key_never_printed_or_logged(srv: Env) -> None:
    for name in ("calls.log", "app.log", "edge.log"):
        path = srv.state / name
        if path.exists():
            assert KEY not in path.read_text(), name


def test_rerun_skips_done_steps_and_passes(srv: Env) -> None:
    proc = srv.deploy("yes\nyes\n")
    out = _out(proc)
    assert proc.returncode == 0, out
    for done in (".env.demo exists", "key present", "cloudflared version", "cert.pem present",
                 "tunnel arda-demo exists"):
        assert f"SKIP (already done): {done}" in out
    assert "PASS: app limits per visitor address (9 allowed, then 429)" in out


def test_missing_edge_rule_fails_step_18(srv: Env) -> None:
    rule = srv.state / "edge-rule"
    rule.unlink()
    try:
        proc = srv.deploy("yes\nyes\n")
    finally:
        rule.touch()
    assert proc.returncode == 1
    assert "FAIL: no edge block" in _out(proc)


def test_trust_header_off_fails_step_16(srv: Env) -> None:
    trust = srv.state / "trust"
    trust.write_text("false")
    srv.restart_app()
    try:
        proc = srv.deploy("yes\nyes\n")
    finally:
        trust.write_text("true")
        srv.restart_app()
    assert proc.returncode == 1
    assert "DEMO_TRUST_CF_HEADER is not in effect" in _out(proc)


def test_teardown_removes_everything(srv: Env) -> None:
    # The person deletes the DNS record in the dashboard while the script waits.
    threading.Timer(6, lambda: (srv.state / "dns").unlink(missing_ok=True)).start()
    proc = srv.teardown("arda-demo\nyes\nyes\n")
    out = _out(proc)
    assert proc.returncode == 0, out
    assert "The demo is down" in out
    etc = srv.h / "root" / "etc"
    assert not (etc / "systemd/system/cloudflared.service").exists()
    assert not (etc / "cloudflared/config.yml").exists()
    assert not (srv.state / "tunnels.json").exists()
    assert not (srv.state / "container").exists()
    assert not (srv.work / ".env.demo").exists()


def test_teardown_rerun_skips_everything(srv: Env) -> None:
    proc = srv.teardown("arda-demo\n")
    out = _out(proc)
    assert proc.returncode == 0, out
    assert out.count("SKIP (already done)") == 6


def test_foreign_cloudflared_config_is_left_alone(srv: Env) -> None:
    foreign = srv.h / "root/etc/cloudflared/config.yml"
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text("tunnel: 11111111-2222-3333-4444-555555555555\n")
    deploy = srv.deploy(f"{KEY}\nyes\n")
    assert deploy.returncode == 1
    assert "belongs to another tunnel; not touching it" in _out(deploy)
    assert foreign.read_text().startswith("tunnel: 1111")

    unit = srv.h / "root/etc/systemd/system/cloudflared.service"
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text("[Service]\n")
    teardown = srv.teardown("arda-demo\n")
    assert teardown.returncode == 1
    assert "not the arda-demo tunnel's; leaving it alone" in _out(teardown)
    assert unit.exists() and foreign.exists()
