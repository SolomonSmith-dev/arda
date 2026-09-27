"""GitHub fetcher for Rúmil (#67). Every test runs against httpx.MockTransport."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from agents.rumil.github import (
    GitHubAuditError,
    GitHubAuthError,
    GitHubRateLimitError,
    compute_streak,
    fetch_activity,
)

NOW = datetime(2026, 9, 26, 15, 0, tzinfo=UTC)


def _calendar(counts_by_offset: dict[int, int]) -> dict:
    """Contribution calendar with `counts_by_offset[n]` on NOW.date() - n days."""
    days = [
        {
            "date": (NOW.date() - timedelta(days=n)).isoformat(),
            "contributionCount": counts_by_offset.get(n, 0),
        }
        for n in range(30, -1, -1)
    ]
    return {
        "data": {
            "user": {
                "contributionsCollection": {
                    "contributionCalendar": {"weeks": [{"contributionDays": days}]}
                }
            }
        }
    }


def _pr(repo: str, number: int, title: str) -> dict:
    return {
        "number": number,
        "title": title,
        "html_url": f"https://github.com/{repo}/pull/{number}",
        "repository_url": f"https://api.github.com/repos/{repo}",
    }


def _happy_handler(seen: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        q = request.url.params.get("q", "")
        if path == "/search/commits":
            items = [{"repository": {"full_name": "SolomonSmith-dev/arda"}}] * 3 + [
                {"repository": {"full_name": "SolomonSmith-dev/ai-memory"}}
            ]
            return httpx.Response(200, json={"total_count": 4, "items": items})
        if path == "/search/issues" and "merged:" in q:
            return httpx.Response(
                200, json={"total_count": 1, "items": [_pr("SolomonSmith-dev/arda", 88, "pin anthropic")]}
            )
        if path == "/search/issues":
            return httpx.Response(
                200,
                json={
                    "total_count": 2,
                    "items": [
                        _pr("SolomonSmith-dev/arda", 91, "five open bugs"),
                        _pr("SolomonSmith-dev/arda", 92, "demo fixes"),
                    ],
                },
            )
        if path == "/graphql":
            return httpx.Response(200, json=_calendar({0: 5, 1: 2, 2: 1, 4: 7}))
        return httpx.Response(404)

    return handler


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")


def test_fetch_activity_builds_a_populated_snapshot():
    seen: list[httpx.Request] = []
    snap = fetch_activity("SolomonSmith-dev", "tok", client=_client(_happy_handler(seen)), now=NOW)

    assert snap.username == "SolomonSmith-dev"
    assert snap.window_end == NOW
    assert snap.window_start == NOW - timedelta(hours=24)
    assert snap.commit_count == 4
    assert snap.commits_by_repo == {"SolomonSmith-dev/arda": 3, "SolomonSmith-dev/ai-memory": 1}
    assert [(p.repo, p.number) for p in snap.prs_opened] == [
        ("SolomonSmith-dev/arda", 91),
        ("SolomonSmith-dev/arda", 92),
    ]
    assert [p.number for p in snap.prs_merged] == [88]
    assert snap.contributions_today == 5
    assert snap.streak_days == 3
    assert snap.has_activity


def test_fetch_activity_scopes_every_search_to_the_user_and_window():
    seen: list[httpx.Request] = []
    fetch_activity("SolomonSmith-dev", "tok", client=_client(_happy_handler(seen)), now=NOW)
    since = "2026-09-25T15:00:00Z"
    searches = [r.url.params["q"] for r in seen if r.url.path.startswith("/search/")]
    assert len(searches) == 3
    for q in searches:
        assert "author:SolomonSmith-dev" in q
        assert since in q
    assert all(r.headers["authorization"] == "Bearer tok" for r in seen)
    graphql = next(r for r in seen if r.url.path == "/graphql")
    assert json.loads(graphql.content)["variables"]["login"] == "SolomonSmith-dev"


def test_missing_token_fails_before_any_request():
    seen: list[httpx.Request] = []
    with pytest.raises(GitHubAuthError, match="GITHUB_TOKEN"):
        fetch_activity("SolomonSmith-dev", "", client=_client(_happy_handler(seen)), now=NOW)
    assert seen == []


@pytest.mark.parametrize(
    ("status", "headers", "exc", "match"),
    [
        (401, {}, GitHubAuthError, "rejected the token"),
        (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1790000000"}, GitHubRateLimitError, "rate limit"),
        (429, {}, GitHubRateLimitError, "rate limit"),
        (403, {"x-ratelimit-remaining": "12"}, GitHubAuthError, "403"),
        (500, {}, GitHubAuditError, "500"),
    ],
)
def test_http_errors_become_audit_errors(status, headers, exc, match):
    def handler(_request):
        return httpx.Response(status, headers=headers, json={"message": "nope"})

    with pytest.raises(exc, match=match):
        fetch_activity("u", "tok", client=_client(handler), now=NOW)


def test_network_errors_do_not_leak_raw_httpx_exceptions():
    def handler(request):
        raise httpx.ConnectError("dns failure", request=request)

    with pytest.raises(GitHubAuditError, match="could not reach GitHub"):
        fetch_activity("u", "tok", client=_client(handler), now=NOW)


def test_graphql_errors_in_a_200_body_are_not_a_partial_snapshot():
    def handler(request):
        if request.url.path == "/graphql":
            return httpx.Response(200, json={"errors": [{"message": "Could not resolve to a User"}]})
        return httpx.Response(200, json={"total_count": 0, "items": []})

    with pytest.raises(GitHubAuditError, match="Could not resolve to a User"):
        fetch_activity("ghost", "tok", client=_client(handler), now=NOW)


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ({0: 1, 1: 1, 2: 1}, 3),
        ({1: 1, 2: 1}, 2),  # nothing yet today: the streak is still alive
        ({0: 1, 2: 1}, 1),
        ({}, 0),
        ({2: 4}, 0),
    ],
)
def test_compute_streak(counts, expected):
    today = date(2026, 9, 26)
    days = {today - timedelta(days=n): c for n, c in counts.items()}
    assert compute_streak(days, today) == expected


def test_quiet_day_has_no_activity():
    def handler(request):
        if request.url.path == "/graphql":
            return httpx.Response(200, json=_calendar({}))
        return httpx.Response(200, json={"total_count": 0, "items": []})

    snap = fetch_activity("u", "tok", client=_client(handler), now=NOW)
    assert not snap.has_activity
    assert snap.streak_days == 0
