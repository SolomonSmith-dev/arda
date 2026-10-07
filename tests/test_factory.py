"""The factory scripts: syntax, claims, worktree creation, and the reviewer family guard."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = [
    ROOT / "scripts" / "check.sh",
    ROOT / "factory" / "new-task.sh",
    ROOT / "factory" / "claim.sh",
    ROOT / "factory" / "review.sh",
]


def run(cmd, cwd=None, env=None):
    import os

    return subprocess.run(
        cmd, cwd=cwd, env={**os.environ, **(env or {})}, text=True, capture_output=True
    )


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_scripts_parse_and_are_executable(script):
    assert script.stat().st_mode & 0o111, f"{script.name} is not executable"
    assert run(["bash", "-n", str(script)]).returncode == 0


def test_claims_block_a_second_agent_and_release_frees_the_file(tmp_path):
    env = {"FACTORY_DIR": str(tmp_path)}
    claim = str(ROOT / "factory" / "claim.sh")
    assert run([claim, "claim", "README.md", "a"], env=env).returncode == 0
    refused = run([claim, "claim", "README.md", "b"], env=env)
    assert refused.returncode == 1 and "claimed by a" in refused.stderr
    assert run([claim, "claim", "README.md", "a"], env=env).returncode == 0  # idempotent
    assert run([claim, "release", "README.md", "a"], env=env).returncode == 0
    assert run([claim, "claim", "README.md", "b"], env=env).returncode == 0
    assert (tmp_path / "CLAIMS.md").read_text().count("README.md") == 1


def test_new_task_makes_a_worktree_branch_and_status_file(tmp_path):
    origin, clone, fdir = tmp_path / "origin.git", tmp_path / "clone", tmp_path / "state"
    assert run(["git", "init", "-q", "--bare", "-b", "main", str(origin)]).returncode == 0
    assert run(["git", "clone", "-q", str(origin), str(clone)]).returncode == 0
    ident = ["-c", "user.name=t", "-c", "user.email=t@example.com"]
    (clone / "f.txt").write_text("x")
    run(["git", "add", "."], cwd=clone)
    assert run(["git", *ident, "commit", "-qm", "init"], cwd=clone).returncode == 0
    assert run(["git", "push", "-q", "origin", "main"], cwd=clone).returncode == 0

    script = ROOT / "factory" / "new-task.sh"
    res = run([str(script), "demo-task"], cwd=clone, env={"FACTORY_DIR": str(fdir)})
    assert res.returncode == 0, res.stderr
    wt = fdir / "wt" / "demo-task"
    assert (wt / "f.txt").exists()
    assert run(["git", "branch", "--show-current"], cwd=wt).stdout.strip() == "task/demo-task"
    status = (fdir / "STATUS-demo-task.md").read_text()
    assert "task/demo-task" in status and "GATE PASS" in status
    # the same slug twice is refused rather than clobbering the first worktree
    assert run([str(script), "demo-task"], cwd=clone, env={"FACTORY_DIR": str(fdir)}).returncode != 0


def test_new_task_rejects_a_bad_slug(tmp_path):
    res = run([str(ROOT / "factory" / "new-task.sh"), "Bad Slug"], env={"FACTORY_DIR": str(tmp_path)})
    assert res.returncode == 2


@pytest.mark.parametrize("model", ["gemma2:9b", "Gemma3", "gemini-2.5-pro", "claude-sonnet"])
def test_reviewer_refuses_the_writers_family(model, tmp_path):
    res = run(
        [str(ROOT / "factory" / "review.sh"), "any"],
        env={"REVIEW_MODEL": model, "FACTORY_DIR": str(tmp_path)},
    )
    assert res.returncode == 3 and "same family" in res.stderr


def test_reviewer_refuses_an_oversized_diff_instead_of_reviewing_half(tmp_path):
    fdir = tmp_path / "state"
    wt = fdir / "wt" / "big"
    wt.mkdir(parents=True)
    run(["git", "init", "-q", "-b", "main"], cwd=wt)
    ident = ["-c", "user.name=t", "-c", "user.email=t@example.com"]
    (wt / "a.txt").write_text("a")
    run(["git", "add", "."], cwd=wt)
    run(["git", *ident, "commit", "-qm", "base"], cwd=wt)
    run(["git", "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=wt)
    (wt / "a.txt").write_text("b" * 500)
    run(["git", *ident, "commit", "-qam", "change"], cwd=wt)
    res = run(
        [str(ROOT / "factory" / "review.sh"), "big"],
        env={"REVIEW_MODEL": "qwen2.5-coder", "FACTORY_DIR": str(fdir), "REVIEW_MAX_CHARS": "100"},
    )
    assert res.returncode == 4 and "Split the task" in res.stderr
