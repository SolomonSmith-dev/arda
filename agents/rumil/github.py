"""GitHub activity fetcher.

Pulls one window of a user's activity -- commits, PRs opened, PRs merged --
from the REST search API, plus the contribution calendar from GraphQL for
the streak. Every failure surfaces as a :class:`GitHubAuditError` subclass
with a message fit for a Telegram line; callers never see raw ``httpx``
exceptions, and there is no partial snapshot.

Commit search only indexes default branches, so work sitting on a feature
branch shows up once it is merged.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

GITHUB_API = "https://api.github.com"
HTTP_TIMEOUT_SECONDS = 15
SEARCH_PAGE_SIZE = 100
DEFAULT_WINDOW_HOURS = 24

_CALENDAR_QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


class GitHubAuditError(RuntimeError):
    """Base for every fetch failure."""


class GitHubAuthError(GitHubAuditError):
    """Missing, invalid or under-scoped token."""


class GitHubRateLimitError(GitHubAuditError):
    """Primary or secondary rate limit hit."""


@dataclass(frozen=True)
class PullRequest:
    repo: str
    number: int
    title: str
    url: str


@dataclass(frozen=True)
class GitHubSnapshot:
    username: str
    window_start: datetime
    window_end: datetime
    commit_count: int
    commits_by_repo: dict[str, int] = field(default_factory=dict)
    prs_opened: list[PullRequest] = field(default_factory=list)
    prs_merged: list[PullRequest] = field(default_factory=list)
    contributions_today: int = 0
    streak_days: int = 0

    @property
    def has_activity(self) -> bool:
        return bool(self.commit_count or self.prs_opened or self.prs_merged)

    def to_dict(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "commit_count": self.commit_count,
            "commits_by_repo": dict(self.commits_by_repo),
            "prs_opened": [pr.__dict__ for pr in self.prs_opened],
            "prs_merged": [pr.__dict__ for pr in self.prs_merged],
            "contributions_today": self.contributions_today,
            "streak_days": self.streak_days,
        }


def compute_streak(counts: dict[date, int], today: date) -> int:
    """Consecutive days with contributions, ending today.

    A day with nothing *yet* today does not break the streak: it counts
    back from yesterday instead, since the day is not over.
    """
    day = today if counts.get(today, 0) > 0 else today - timedelta(days=1)
    streak = 0
    while counts.get(day, 0) > 0:
        streak += 1
        day -= timedelta(days=1)
    return streak


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _repo_from_api_url(url: str) -> str:
    # https://api.github.com/repos/<owner>/<name>
    return "/".join(url.rstrip("/").split("/")[-2:])


def _pull_requests(items: list[dict[str, Any]]) -> list[PullRequest]:
    return [
        PullRequest(
            repo=_repo_from_api_url(item.get("repository_url", "")),
            number=int(item["number"]),
            title=str(item.get("title", "")),
            url=str(item.get("html_url", "")),
        )
        for item in items
    ]


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.is_success:
        return
    status = resp.status_code
    if status == 429 or (status == 403 and resp.headers.get("x-ratelimit-remaining") == "0"):
        reset = resp.headers.get("x-ratelimit-reset")
        when = ""
        if reset and reset.isdigit():
            when = f"; resets {datetime.fromtimestamp(int(reset), UTC):%H:%M} UTC"
        raise GitHubRateLimitError(f"GitHub rate limit hit ({status}{when})")
    if status == 401:
        raise GitHubAuthError("GitHub rejected the token (401); check GITHUB_TOKEN")
    if status == 403:
        raise GitHubAuthError("GitHub refused the request (403); the token may lack scope")
    raise GitHubAuditError(f"GitHub API error {status} on {resp.request.url.path}")


def _get(client: httpx.Client, path: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        resp = client.get(path, params=params)
    except httpx.HTTPError as e:
        raise GitHubAuditError(f"could not reach GitHub: {e}") from e
    _raise_for_status(resp)
    return resp.json()


def _calendar(client: httpx.Client, username: str) -> dict[date, int]:
    try:
        resp = client.post(
            "/graphql", json={"query": _CALENDAR_QUERY, "variables": {"login": username}}
        )
    except httpx.HTTPError as e:
        raise GitHubAuditError(f"could not reach GitHub: {e}") from e
    _raise_for_status(resp)
    body = resp.json()
    if body.get("errors"):
        msg = "; ".join(str(err.get("message", err)) for err in body["errors"])
        raise GitHubAuditError(f"GitHub GraphQL error: {msg}")
    try:
        weeks = body["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    except (KeyError, TypeError) as e:
        raise GitHubAuditError("GitHub GraphQL returned no contribution calendar") from e
    return {
        date.fromisoformat(d["date"]): int(d["contributionCount"])
        for week in weeks
        for d in week["contributionDays"]
    }


def fetch_activity(
    username: str,
    token: str,
    *,
    client: httpx.Client | None = None,
    now: datetime | None = None,
    window_hours: int = DEFAULT_WINDOW_HOURS,
) -> GitHubSnapshot:
    """Fetch one window of ``username``'s activity.

    A token is required: the contribution calendar is GraphQL-only, and
    GraphQL rejects anonymous calls.
    """
    if not token:
        raise GitHubAuthError("GITHUB_TOKEN is not set; the audit needs a token")

    end = now or datetime.now(UTC)
    start = end - timedelta(hours=window_hours)
    since = _iso(start)

    owns_client = client is None
    if client is None:
        client = httpx.Client(base_url=GITHUB_API, timeout=HTTP_TIMEOUT_SECONDS)
    client.headers.update(
        {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    )
    try:
        commits = _get(
            client,
            "/search/commits",
            {"q": f"author:{username} author-date:>={since}", "per_page": SEARCH_PAGE_SIZE},
        )
        opened = _get(
            client,
            "/search/issues",
            {"q": f"author:{username} type:pr created:>={since}", "per_page": SEARCH_PAGE_SIZE},
        )
        merged = _get(
            client,
            "/search/issues",
            {"q": f"author:{username} type:pr merged:>={since}", "per_page": SEARCH_PAGE_SIZE},
        )
        calendar = _calendar(client, username)
    finally:
        if owns_client:
            client.close()

    by_repo = Counter(
        item["repository"]["full_name"]
        for item in commits.get("items", [])
        if item.get("repository", {}).get("full_name")
    )
    today = end.date()
    return GitHubSnapshot(
        username=username,
        window_start=start,
        window_end=end,
        commit_count=int(commits.get("total_count", 0)),
        commits_by_repo=dict(by_repo.most_common()),
        prs_opened=_pull_requests(opened.get("items", [])),
        prs_merged=_pull_requests(merged.get("items", [])),
        contributions_today=calendar.get(today, 0),
        streak_days=compute_streak(calendar, today),
    )
