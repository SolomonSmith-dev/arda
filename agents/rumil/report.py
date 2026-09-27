"""Plain-text rendering of a :class:`GitHubSnapshot`.

Telegram is sent without a parse mode, so this is plain text, capped under
Telegram's 4096-character message limit.
"""

from __future__ import annotations

from agents.rumil.github import GitHubSnapshot, PullRequest

MAX_MESSAGE_CHARS = 3500
MAX_PRS_LISTED = 8


def _short_repo(repo: str) -> str:
    return repo.split("/", 1)[-1]


def _pr_lines(label: str, prs: list[PullRequest]) -> list[str]:
    if not prs:
        return []
    lines = [f"{label}: {len(prs)}"]
    for pr in prs[:MAX_PRS_LISTED]:
        lines.append(f"  {_short_repo(pr.repo)}#{pr.number} {pr.title[:80]}")
    if len(prs) > MAX_PRS_LISTED:
        lines.append(f"  ...and {len(prs) - MAX_PRS_LISTED} more")
    return lines


def format_stats(snap: GitHubSnapshot) -> str:
    hours = round((snap.window_end - snap.window_start).total_seconds() / 3600)
    lines = [f"GitHub, last {hours}h ({snap.username})"]
    if not snap.has_activity:
        lines.append("No GitHub activity in this window.")
    else:
        repos = ", ".join(
            f"{_short_repo(r)} {n}" for r, n in list(snap.commits_by_repo.items())[:MAX_PRS_LISTED]
        )
        noun = "repo" if len(snap.commits_by_repo) == 1 else "repos"
        commits = f"{snap.commit_count} commits across {len(snap.commits_by_repo)} {noun}"
        lines.append(f"{commits} ({repos})" if repos else commits)
        lines += _pr_lines("PRs opened", snap.prs_opened)
        lines += _pr_lines("PRs merged", snap.prs_merged)
    day = "day" if snap.streak_days == 1 else "days"
    lines.append(
        f"Streak: {snap.streak_days} {day}, {snap.contributions_today} contributions today"
    )
    text = "\n".join(lines)
    return text if len(text) <= MAX_MESSAGE_CHARS else text[: MAX_MESSAGE_CHARS - 3] + "..."


def build_message(snap: GitHubSnapshot, narrative: str) -> str:
    stats = format_stats(snap)
    if not narrative:
        return stats
    room = MAX_MESSAGE_CHARS - len(stats) - 2
    return f"{narrative[:room].rstrip()}\n\n{stats}" if room > 0 else stats
