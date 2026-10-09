"""F-3 contract: parameterised `ls -la <path>` under operator-set roots.

Tests marked xfail(strict) describe behaviour F-3 adds. Implement it, then delete the markers.
Everything else here passes today and must keep passing: refusal is the default.
"""

from __future__ import annotations

import os

import fakeredis
import pytest

from agents.earendil import worker
from core.config import settings
from core.models import TaskStatus
from core.redis_client import task_result_key
from core.shell_policy import CommandNotAllowedError, argv_for, check_command

F3 = pytest.mark.xfail(strict=True, reason="F-3: parameterised ls is not implemented yet")


def _set_roots(monkeypatch, value: str) -> None:
    # setitem on __dict__ works before the field exists (pydantic rejects setattr for unknown
    # names) and after, and monkeypatch restores it either way.
    monkeypatch.setitem(settings.__dict__, "earendil_ls_roots", value)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    evil_sibling = tmp_path / "root-evil"  # shares the root's string prefix
    evil_sibling.mkdir()
    (root / "escape").symlink_to(outside)  # link inside the root that points out of it
    (root / "inside-link").symlink_to(root / "sub")  # link that stays inside
    _set_roots(monkeypatch, str(root))
    return {"root": root, "outside": outside, "evil": evil_sibling}


# --- new behaviour ----------------------------------------------------------------------


@F3
def test_ls_inside_the_root_is_allowed(tree):
    root = tree["root"]
    assert check_command(f"ls -la {root}") == f"ls -la {root}"
    assert check_command(f"ls -la {root}/sub") == f"ls -la {root}/sub"
    assert check_command(f"ls   -la   {root}/sub") == f"ls -la {root}/sub"  # whitespace normalised


@F3
def test_argv_has_a_double_dash_and_the_resolved_path(tree):
    root = tree["root"]
    assert argv_for(f"ls -la {root}/sub") == ["ls", "-la", "--", os.path.realpath(f"{root}/sub")]


@F3
def test_a_symlink_that_stays_inside_the_root_is_allowed_and_resolved(tree):
    root = tree["root"]
    assert argv_for(f"ls -la {root}/inside-link") == [
        "ls", "-la", "--", os.path.realpath(f"{root}/sub"),
    ]


# --- regression guards: pass now, must keep passing -------------------------------------


def _refused_cases(t):
    root, outside, evil = t["root"], t["outside"], t["evil"]
    return [
        f"ls -la {outside}",  # outside every root
        "ls -la /etc",
        f"ls -la {evil}",  # string-prefix sibling of the root
        f"ls -la {root}/../outside",  # dot-dot
        f"ls -la {root}/sub/../../outside",
        f"ls -la {root}/escape",  # symlink out of the root
        f"ls -la {root}/escape/anything",
        f"ls -la {root}/*",  # globs
        f"ls -la {root}/su?",
        f"ls -la {root}/{{sub,x}}",
        f"ls -la -R {root}",  # extra option
        f"ls -la -- {root}",  # caller-supplied separator
        f"ls -la {root} {root}/sub",  # two paths
        f"ls -la {root}/sub /etc",
        "ls -la sub",  # relative
        "ls -la",  # bare form stays exact-match: fine, covered separately below
        f"ls -la {root}/sub; id",  # shell metacharacters
        f"ls -la {root}/sub && id",
        f"ls -la {root}/sub | sh",
        f"ls -la {root}/sub > /tmp/x",
        "ls -la $(id)",
        "ls -la `id`",
        f"ls -la {root}/sub\nid",
        f"ls -la {root}/su\x00b",
        f"ls -l {root}/sub",  # different flags are a different command
        f"ls {root}/sub",
        f"/bin/ls -la {root}/sub",
    ]


def test_every_escape_attempt_is_refused(tree):
    cases = [c for c in _refused_cases(tree) if c != "ls -la"]
    allowed = []
    for cmd in cases:
        try:
            check_command(cmd)
            allowed.append(cmd)
        except CommandNotAllowedError:
            pass
    assert not allowed, f"these should have been refused: {allowed}"


def test_bare_ls_la_stays_allowed():
    assert check_command("ls -la") == "ls -la"


def test_with_no_roots_configured_the_feature_is_off(tree, monkeypatch):
    _set_roots(monkeypatch, "")
    with pytest.raises(CommandNotAllowedError):
        check_command(f"ls -la {tree['root']}/sub")


def test_the_worker_refuses_a_queued_task_with_an_outside_path(tree, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("subprocess was invoked for a refused path")

    monkeypatch.setattr(worker.subprocess, "check_output", boom)
    r = fakeredis.FakeRedis(decode_responses=True)
    task = {
        "task_id": "poison-ls",
        "type": "system",
        "action": "run_command",
        "payload": {"command": f"ls -la {tree['outside']}"},
    }
    worker.process_task(r, task)
    import json

    out = json.loads(r.get(task_result_key("poison-ls")))
    assert out["status"] == TaskStatus.FAILED and "allowlist" in out["error"]
