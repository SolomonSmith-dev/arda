"""The factory scripts: syntax, claims, worktree creation, and the reviewer family guard."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = [
    ROOT / "scripts" / "check.sh",
    ROOT / "factory" / "new-task.sh",
    ROOT / "factory" / "claim.sh",
    ROOT / "factory" / "review.sh",
    ROOT / "factory" / "status.sh",
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


def _task_with_a_change(tmp_path, slug):
    fdir = tmp_path / "state"
    wt = fdir / "wt" / slug
    wt.mkdir(parents=True)
    run(["git", "init", "-q", "-b", "main"], cwd=wt)
    ident = ["-c", "user.name=t", "-c", "user.email=t@example.com"]
    (wt / "a.txt").write_text("a")
    run(["git", "add", "."], cwd=wt)
    run(["git", *ident, "commit", "-qm", "base"], cwd=wt)
    run(["git", "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=wt)
    (wt / "a.txt").write_text("b" * 500)
    run(["git", *ident, "commit", "-qam", "change"], cwd=wt)
    return fdir


def test_reviewer_refuses_an_oversized_diff_instead_of_reviewing_half(tmp_path):
    fdir = _task_with_a_change(tmp_path, "big")
    res = run(
        [str(ROOT / "factory" / "review.sh"), "big"],
        env={"REVIEW_MODEL": "qwen2.5-coder", "FACTORY_DIR": str(fdir), "REVIEW_MAX_CHARS": "100"},
    )
    assert res.returncode == 4 and "Split the task" in res.stderr


def _fake_ollama(tmp_path, output):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "ollama"
    exe.write_text(f"#!/bin/sh\ncat >/dev/null\nprintf '%s' '{output}'\n")
    exe.chmod(0o755)
    import os

    return f"{bin_dir}:{os.environ['PATH']}"


@pytest.mark.parametrize(
    ("output", "code"),
    [
        ("", 5),  # a dead or silent reviewer is not an approval
        ("looks fine to me", 5),  # prose without a verdict is not an approval
        ("NO FINDINGS", 0),
        ("BLOCKER a.py:1 secret committed", 0),
    ],
)
def test_reviewer_output_must_carry_a_verdict(tmp_path, output, code):
    fdir = _task_with_a_change(tmp_path, "t")
    path = _fake_ollama(tmp_path, output)
    res = run(
        [str(ROOT / "factory" / "review.sh"), "t"],
        env={"REVIEW_MODEL": "qwen2.5-coder", "FACTORY_DIR": str(fdir), "PATH": path},
    )
    assert res.returncode == code, res.stderr
    if code == 5:
        assert "no verdict" in res.stderr


def _commit_minutes_ago(repo, minutes_ago):
    repo.mkdir(parents=True)
    assert run(["git", "init", "-q", "-b", "main"], cwd=repo).returncode == 0
    ident = ["-c", "user.name=t", "-c", "user.email=t@example.com"]
    (repo / "f.txt").write_text("x\n")
    assert run(["git", "add", "."], cwd=repo).returncode == 0
    stamp = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    res = run(
        ["git", *ident, "commit", "-qm", "work"],
        cwd=repo,
        env={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp},
    )
    assert res.returncode == 0, res.stderr


def _write_status(fdir, slug, running, worktree):
    (fdir / f"STATUS-{slug}.md").write_text(
        f"# STATUS: {slug}\n- branch: task/{slug}\n- worktree: {worktree}\n- now running: {running}\n"
    )


def _status_lines(stdout):
    return {line.split(" | ")[0]: line.split(" | ") for line in stdout.splitlines()}


def test_status_flags_an_old_branch_and_not_a_fresh_one(tmp_path):
    fdir = tmp_path / "state"
    fdir.mkdir()
    trees = tmp_path / "trees"
    for slug, age, running in (
        ("fresh", 1, "writing tests"),
        ("recent", 29, "still writing"),
        ("cutoff", 30, "quiet"),
        ("old", 45, "waiting"),
    ):
        repo = trees / slug
        _commit_minutes_ago(repo, age)
        _write_status(fdir, slug, running, repo)
    _write_status(fdir, "gone", "idle", trees / "missing")
    plain = fdir / "wt" / "plain"
    _commit_minutes_ago(plain, 1)
    (fdir / "STATUS-plain.md").write_text("# STATUS: plain\n- branch: task/plain\n- now running: local\n")

    res = run([str(ROOT / "factory" / "status.sh")], env={"FACTORY_DIR": str(fdir)})
    assert res.returncode == 0, res.stderr
    lines = _status_lines(res.stdout)
    assert set(lines) == {"fresh", "recent", "cutoff", "old", "gone", "plain"}

    assert lines["fresh"] == ["fresh", "task/fresh", lines["fresh"][2], "writing tests"]
    assert int(lines["fresh"][2]) <= 1
    assert lines["recent"][3] == "still writing" and int(lines["recent"][2]) == 29
    assert len(lines["recent"]) == 4
    assert lines["cutoff"] == ["cutoff", "task/cutoff", "30", "quiet", "STALE"]
    assert lines["old"][1] == "task/old" and lines["old"][3] == "waiting" and lines["old"][-1] == "STALE"
    assert 44 <= int(lines["old"][2]) <= 46
    assert lines["gone"] == ["gone", "task/gone", "-", "idle", "STALE"]
    assert lines["plain"][:3] == ["plain", "task/plain", lines["plain"][2]]
    assert lines["plain"][3] == "local" and len(lines["plain"]) == 4

    empty = tmp_path / "empty"
    empty.mkdir()
    quiet = run([str(ROOT / "factory" / "status.sh")], env={"FACTORY_DIR": str(empty)})
    assert quiet.returncode == 0 and quiet.stdout == ""


def test_release_by_a_non_holder_fails_and_changes_nothing(tmp_path):
    env = {"FACTORY_DIR": str(tmp_path)}
    claim = str(ROOT / "factory" / "claim.sh")
    run([claim, "claim", "README.md", "a"], env=env)
    res = run([claim, "release", "README.md", "b"], env=env)
    assert res.returncode == 1 and "nothing to release" in res.stderr
    assert "README.md | a" in (tmp_path / "CLAIMS.md").read_text()
