from __future__ import annotations

import pytest

from agents.sauron.planner import classify, plan


@pytest.mark.parametrize(
    "message,expected",
    [
        # --- earendil: existing ---
        ("uptime", "earendil"),
        ("show me system status", "earendil"),
        ("whoami", "earendil"),
        ("run echo hello", "earendil"),
        # --- earendil: new regex coverage ---
        ("docker ps", "earendil"),
        ("restart nginx", "earendil"),
        ("git status", "earendil"),
        ("how many ports are listening", "earendil"),
        ("ssh into home-server", "earendil"),
        ("apt-get install curl", "earendil"),
        ("check cpu usage", "earendil"),
        ("how much memory is available", "earendil"),
        ("deploy the service", "earendil"),
        # --- finrod: existing ---
        ("what is the deployment process", "finrod"),
        ("explain the redis architecture", "finrod"),
        ("summarize the architecture docs", "finrod"),
        # --- finrod: knowledge beats ops for ambiguous terms ---
        ("what are the running services", "finrod"),
        ("remember this for later", "finrod"),
        # --- tommodbadil ---
        ("Film: Ran\nRating: 9", "tombombadil"),
        ("recommend a movie for me", "tombombadil"),
        ("what did I watch last week", "tombombadil"),
        ("what about kurosawa films", "tombombadil"),
    ],
)
def test_classify_routes_keywords_to_correct_specialist(message, expected):
    assert classify(message) == expected


def test_classify_default_is_earendil():
    assert classify("xyzzy plover") == "earendil"


def test_plan_returns_single_subtask_with_full_message():
    p = plan("show me system status")
    assert p.intent == "earendil"
    assert len(p.subtasks) == 1
    assert p.subtasks[0].specialist == "earendil"
    assert p.subtasks[0].payload == {"message": "show me system status"}


def test_plan_film_message_routes_to_tombombadil():
    p = plan("Film: La Haine\nRating: 10")
    assert p.intent == "tombombadil"
    assert p.subtasks[0].specialist == "tombombadil"
