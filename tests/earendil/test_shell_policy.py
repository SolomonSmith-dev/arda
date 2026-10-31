"""Earendil command allowlist: refused at enqueue, at the API, and again in the worker."""

from __future__ import annotations

import json
import os
from unittest.mock import Mock

import fakeredis
import pytest
from fastapi.testclient import TestClient

from agents.earendil import agent as earendil_module
from agents.earendil import worker
from api.routes import tasks as tasks_routes
from core.config import settings
from core.models import AgentTask, TaskStatus
from core.redis_client import TASK_QUEUE_KEY, task_result_key
from core.shell_policy import (
    ALLOWED_COMMANDS,
    CommandNotAllowedError,
    argv_for,
    check_command,
)

HEADERS = {"x-api-key": os.environ["ARDA_API_KEY"]}
INJECTIONS = [
    "uptime; id",
    "uptime && cat /etc/passwd",
    "uptime | sh",
    "uptime\nid",
    "$(id)",
    "`id`",
    "df -h /",
    "ls -la /etc",
    "rm -rf /",
    "cat /etc/shadow",
    "echo hello",
    "UPTIME",
    "",
]


@pytest.fixture
def fake_redis(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(earendil_module, "get_redis_sync", lambda: r)
    monkeypatch.setattr(worker, "get_redis_sync", lambda: r)
    monkeypatch.setattr(tasks_routes, "get_redis_sync", lambda: r)
    return r


@pytest.mark.parametrize("cmd", ALLOWED_COMMANDS)
def test_allowlisted_commands_pass(cmd):
    assert check_command(cmd) == cmd
    assert argv_for(cmd) is not None


def test_whitespace_is_normalised():
    assert check_command("  df   -h ") == "df -h"


@pytest.mark.parametrize("cmd", INJECTIONS)
def test_everything_else_is_refused(cmd):
    with pytest.raises(CommandNotAllowedError):
        check_command(cmd)


def test_enqueue_refuses_and_queues_nothing(fake_redis):
    task = {"type": "system", "action": "run_command", "payload": {"command": "uptime; id"}}
    with pytest.raises(CommandNotAllowedError):
        earendil_module.enqueue_task(fake_redis, task)
    assert fake_redis.llen(TASK_QUEUE_KEY) == 0


@pytest.mark.parametrize("message", ["cat /etc/passwd", "run rm -rf /", "echo pwned > /tmp/x"])
async def test_earendil_agent_refuses_unlisted_messages(fake_redis, message):
    res = await earendil_module.Earendil().run(
        AgentTask(agent="earendil", type="execute", payload={"message": message})
    )
    assert res.status == TaskStatus.FAILED
    assert "allowlist" in (res.error or "")
    assert fake_redis.llen(TASK_QUEUE_KEY) == 0


async def test_earendil_agent_still_runs_allowlisted_messages(fake_redis):
    res = await earendil_module.Earendil().run(
        AgentTask(agent="earendil", type="execute", payload={"message": "system status"})
    )
    assert res.status == TaskStatus.QUEUED and len(res.result["task_ids"]) == 3


def test_worker_refuses_a_task_already_in_the_queue(fake_redis, monkeypatch):
    """A poisoned queue entry (written around enqueue_task) must still not run."""

    def boom(*a, **k):
        raise AssertionError("subprocess was invoked for a refused command")

    monkeypatch.setattr(worker.subprocess, "check_output", boom)
    task = {
        "task_id": "poison",
        "type": "system",
        "action": "run_command",
        "payload": {"command": "id; whoami"},
    }
    worker.process_task(fake_redis, task)
    out = json.loads(fake_redis.get(task_result_key("poison")))
    assert out["status"] == TaskStatus.FAILED and "allowlist" in out["error"]


def test_allowlisted_command_runs_without_a_shell(monkeypatch):
    seen = {}

    def fake(argv, **kw):
        seen["argv"], seen["shell"] = argv, kw.get("shell")
        return "ok\n"

    monkeypatch.setattr(worker.subprocess, "check_output", fake)
    assert worker.execute_system_task({"command": "df  -h"})["result"] == "ok"
    assert seen == {"argv": ["df", "-h"], "shell": False}


def test_escape_hatch_restores_old_behaviour(monkeypatch):
    monkeypatch.setattr(settings, "earendil_allow_any_command", True)
    assert check_command("echo hello") == "echo hello"
    assert argv_for("echo hello") is None  # not allowlisted: legacy shell path
    assert worker.execute_system_task({"command": "printf done"})["result"] == "done"


# -- ls <path> under EARENDIL_LS_ROOTS -----------------------------------


@pytest.fixture
def ls_root(tmp_path, monkeypatch):
    root = (tmp_path / "root").resolve()
    (root / "sub").mkdir(parents=True)
    (tmp_path / "root-sibling").mkdir()
    (tmp_path / "secret").mkdir()
    (root / "escape").symlink_to(tmp_path / "secret")
    monkeypatch.setattr(settings, "earendil_ls_roots", f"{root}, relative/ignored")
    return root


def test_ls_path_is_refused_when_no_roots_are_set(tmp_path):
    with pytest.raises(CommandNotAllowedError):
        check_command(f"ls -la {tmp_path}")


@pytest.mark.parametrize("form", ["ls", "ls -la"])
@pytest.mark.parametrize("rel", ["", "/sub", "/sub/"])
def test_ls_path_inside_a_root_is_allowed(ls_root, form, rel):
    cmd = f"{form} {ls_root}{rel}"
    assert check_command(cmd) == cmd
    assert argv_for(cmd) == [*form.split(), str(ls_root) + rel.rstrip("/")]


@pytest.mark.parametrize(
    "tail",
    [
        "/..",  # parent of the root
        "/sub/../..",  # climbs out through a subdir
        "/sub/..",  # stays inside, still refused: no `..` at all
        "-sibling",  # prefix match is not containment
        "/escape",  # symlink that points outside
        " /etc",  # two paths
    ],
)
def test_ls_path_escapes_are_refused(ls_root, tail):
    with pytest.raises(CommandNotAllowedError):
        check_command(f"ls {ls_root}{tail}")


@pytest.mark.parametrize(
    "cmd", ["ls /etc", "ls -la /", "ls sub", "ls ./sub", "ls -R {root}", "ls -la -R {root}",
            "cat {root}", "ls '{root}'"],
)
def test_ls_other_shapes_are_refused(ls_root, cmd):
    with pytest.raises(CommandNotAllowedError):
        check_command(cmd.format(root=ls_root))


def test_ls_path_runs_resolved_and_without_a_shell(ls_root, monkeypatch):
    seen = {}

    def fake(argv, **kw):
        seen["argv"], seen["shell"] = argv, kw.get("shell")
        return "sub\n"

    monkeypatch.setattr(worker.subprocess, "check_output", fake)
    assert worker.execute_system_task({"command": f"ls  -la {ls_root}/sub/"})["result"] == "sub"
    assert seen == {"argv": ["ls", "-la", str(ls_root / "sub")], "shell": False}


@pytest.mark.parametrize("form", ["ls", "ls -la"])
def test_worker_refuses_symlink_retargeted_after_validation(
    ls_root, fake_redis, monkeypatch, form
):
    monkeypatch.setattr(settings, "earendil_allow_any_command", False)
    link = ls_root / "link;id"
    link.symlink_to(ls_root / "sub")

    def check_then_retarget(command):
        validated = check_command(command)
        link.unlink()
        link.symlink_to(ls_root.parent / "secret")
        return validated

    subprocess = Mock(return_value="should not run")
    monkeypatch.setattr(worker, "check_command", check_then_retarget)
    monkeypatch.setattr(worker.subprocess, "check_output", subprocess)
    worker.process_task(fake_redis, {
        "task_id": "symlink-race",
        "type": "system",
        "action": "run_command",
        "payload": {"command": f"{form} {link}"},
    })
    subprocess.assert_not_called()
    result = json.loads(fake_redis.get(task_result_key("symlink-race")))
    assert result["status"] == TaskStatus.FAILED
    assert "allowlist" in result["error"]


@pytest.mark.parametrize("enabled", [False, True])
def test_shell_execution_requires_explicit_escape_hatch(monkeypatch, enabled):
    monkeypatch.setattr(settings, "earendil_allow_any_command", enabled)
    subprocess = Mock(return_value="ok")
    monkeypatch.setattr(worker.subprocess, "check_output", subprocess)
    command = "echo hello; id"
    if enabled:
        assert argv_for(command) is None
        worker.execute_system_task({"command": command})
        assert subprocess.call_args.args == (command,)
        assert subprocess.call_args.kwargs["shell"] is True
    else:
        with pytest.raises(CommandNotAllowedError):
            argv_for(command)
        with pytest.raises(CommandNotAllowedError):
            worker.execute_system_task({"command": command})
        subprocess.assert_not_called()


# -- API ---------------------------------------------------------------


@pytest.fixture
def client(fake_redis):
    from api.main import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/execute", {"message": "cat /etc/passwd"}),
        ("/execute/wait", {"message": "run rm -rf /"}),
        ("/task", {"type": "system", "action": "run_command", "payload": {"command": "id"}}),
        ("/task", {"type": "async", "payload": {"type": "system", "action": "run_command",
                                                 "payload": {"command": "id"}}}),
    ],
)
def test_api_refuses_unlisted_commands_with_403(client, fake_redis, path, body):
    r = client.post(path, json=body, headers=HEADERS)
    assert r.status_code == 403
    assert r.json()["status"] == "refused"
    assert fake_redis.llen(TASK_QUEUE_KEY) == 0


def test_api_still_queues_allowlisted_commands(client, fake_redis):
    r = client.post("/execute", json={"message": "uptime"}, headers=HEADERS)
    assert r.status_code == 200 and r.json()["status"] == "queued"
    assert fake_redis.llen(TASK_QUEUE_KEY) == 1
