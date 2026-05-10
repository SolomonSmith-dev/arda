"""Film knowledge base for Tom Bombadil.

Loaded on startup to give context about the group's taste.
Only officially submitted films count toward stats; casually mentioned
films live in Redis mentions but not here.

If ``LETTERBOXD_EXPORT_DIR`` points at an unzipped Letterboxd export,
those rows are merged in at construction. See
:mod:`agents.tombombadil.letterboxd_loader`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agents.tombombadil.letterboxd_loader import (
    load_letterboxd_export,
    merge_into_film_database,
)
from core.logging import get_logger

log = get_logger("agents.tombombadil.film_knowledge")

FILM_DATABASE = {
    "films": [
        {
            "title": "Ran",
            "directors": "Akira Kurosawa",
            "year": 1985,
            "watchers": [
                {"name": "Solomon Smith", "rating": 9, "themes": ["violence", "power", "fate"], "take": "A cold, crushing vision of senseless violence, inherited hatred, and revenge"},
                {"name": "Anthony Taylor", "rating": 8.5, "themes": ["power", "greed", "loyalty"], "take": "Pride and greed can destroy a family"},
                {"name": "Isis", "rating": 8, "themes": ["vengeance", "deception", "greed"], "take": "You are your own destruction"},
                {"name": "Gavin", "rating": 9, "themes": ["power", "consequence", "character"], "take": "What goes around comes around"},
                {"name": "Brian", "rating": 8, "themes": ["tragedy", "suffering"], "take": "I'm sad. I'm sad now."},
            ],
            "group_consensus": "Epic meditation on power, violence, and the futility of legacy",
            "themes": ["violence", "power", "greed", "betrayal"],
        },
        {
            "title": "La Haine",
            "directors": "Mathieu Kassovitz",
            "year": 1995,
            "watchers": [
                {"name": "Solomon Smith", "rating": 10, "themes": ["systemic violence", "identity", "state power"], "take": "A lit fuse headed towards a brick of dynamite"},
                {"name": "Gavin", "rating": 9.5, "themes": ["racial tension", "social injustice"], "take": "Hate breeds more hate"},
                {"name": "Isis", "rating": 9.5, "themes": ["police brutality", "brotherhood", "grief"], "take": "Everything will be ok in a destabilized world"},
            ],
            "group_consensus": "Visceral critique of systemic marginalization and state violence against youth",
            "themes": ["systemic violence", "racism", "state power", "brotherhood"],
        },
        {
            "title": "Ghost Dog: The Way of the Samurai",
            "directors": "Jim Jarmusch",
            "year": 1999,
            "watchers": [
                {"name": "Solomon Smith", "rating": 8, "themes": ["loyalty", "code", "meditation"], "take": "A meditative, hip-hop-infused collision of dying cultures"},
                {"name": "Gavin", "rating": 7, "themes": ["absurdity", "loyalty", "loneliness"], "take": "People do stuff without knowing why"},
            ],
            "group_consensus": "Dreamy, philosophical hitman film about living by a code",
            "themes": ["loyalty", "honor", "solitude", "code"],
        },
    ],
    "people": {
        "Solomon Smith": {
            "avg_rating": 9.3,
            "preferred_themes": ["violence", "systemic critique", "fate", "meaning"],
            "style": "Intellectual, thematic analysis, connects to broader societal questions",
            "films_watched": ["Ran", "La Haine", "Ghost Dog"],
        },
        "Gavin": {
            "avg_rating": 8.75,
            "preferred_themes": ["systemic violence", "human connection", "absurdity", "consequence"],
            "style": "Direct, emotional, focused on character over spectacle",
            "films_watched": ["Ran", "La Haine", "Ghost Dog"],
        },
        "Isis": {
            "avg_rating": 8.5,
            "preferred_themes": ["beauty in chaos", "brotherhood", "moral ambiguity"],
            "style": "Poetic, visual, finds grace in tragedy",
            "films_watched": ["Ran", "La Haine"],
        },
        "Anthony Taylor": {
            "avg_rating": 8.5,
            "preferred_themes": ["power", "pride", "consequence"],
            "style": "Focused on character choice and personal responsibility",
            "films_watched": ["Ran"],
        },
        "Brian": {
            "avg_rating": 8.0,
            "preferred_themes": ["tragedy", "emotional impact"],
            "style": "Emotional, moved by the weight of the story",
            "films_watched": ["Ran"],
        },
        "G": {
            "avg_rating": 7.0,
            "preferred_themes": ["code", "loneliness", "honor"],
            "style": "Casual, observational, appreciates visual moments",
            "films_watched": ["Ghost Dog"],
        },
        "Isis": {  # noqa: F601 -- duplicate key in original; second wins, see ADR 0002
            "avg_rating": 8.5,
            "preferred_themes": ["beauty in chaos", "brotherhood", "moral ambiguity"],
            "style": "Engaged, emotional reactions, partner of Solomon",
            "films_watched": ["Ran", "La Haine"],
        },
    },
}


class FilmKnowledge:
    def __init__(self, redis_client=None, letterboxd_dir: Path | str | None = None):
        self.redis = redis_client

        if letterboxd_dir is None:
            env = os.environ.get("LETTERBOXD_EXPORT_DIR")
            if env:
                letterboxd_dir = env

        merged = FILM_DATABASE
        if letterboxd_dir:
            path = Path(letterboxd_dir)
            if path.is_dir():
                try:
                    viewer_name = os.environ.get("LETTERBOXD_VIEWER_NAME") or None
                    export = load_letterboxd_export(path, viewer_name=viewer_name)
                    merged = merge_into_film_database(FILM_DATABASE, export)
                    log.info(
                        "letterboxd_merged",
                        films=len(merged["films"]),
                        people=len(merged["people"]),
                    )
                except Exception as e:
                    log.error("letterboxd_load_failed", path=str(path), exc=str(e))
            else:
                log.warning("letterboxd_dir_missing", path=str(path))

        self.films = merged["films"]
        self.people = merged["people"]

        if self.redis:
            self._load_to_redis()

    def _load_to_redis(self) -> None:
        self.redis.set("film_knowledge:films", json.dumps(self.films))
        self.redis.set("film_knowledge:people", json.dumps(self.people))

    def get_person_profile(self, name: str) -> dict[str, Any]:
        return self.people.get(name, {})

    def get_film(self, title: str) -> dict[str, Any] | None:
        for film in self.films:
            if film["title"].lower() == title.lower():
                return film
        return None

    def suggest_for_person(self, name: str) -> dict[str, Any] | None:
        person = self.get_person_profile(name)
        if not person:
            return None

        watched = set(person.get("films_watched", []))
        preferred = set(person.get("preferred_themes", []))

        best = None
        best_match = 0

        for film in self.films:
            if film["title"] in watched:
                continue
            film_themes = set(film["themes"])
            match = len(film_themes & preferred)
            if match > best_match:
                best = film
                best_match = match

        return best

    def get_user_summary(self, name: str, recent_limit: int = 30) -> str | None:
        """Compact summary for the LLM system prompt: favorites + recent rated.

        Returns None if the user isn't in ``self.people``.
        """
        person = self.people.get(name)
        if not person:
            return None

        favorites = (
            [w["name"] for w in []]
            + [
                f["title"]
                for f in self.films
                if any(
                    w.get("name") == name and (w.get("rating") or 0) >= 9
                    for w in f.get("watchers", [])
                )
            ]
        )

        rated_by_user: list[tuple[str, float]] = []
        for f in self.films:
            for w in f.get("watchers", []):
                if w.get("name") == name and w.get("rating") is not None:
                    rated_by_user.append((f["title"], float(w["rating"])))
        rated_by_user.sort(key=lambda x: -x[1])

        lines = [f"Viewer: {name} (avg {person.get('avg_rating', 0)})"]
        if favorites[:8]:
            lines.append("Top-rated: " + ", ".join(favorites[:8]))
        if rated_by_user:
            top = rated_by_user[:recent_limit]
            lines.append(
                "Recent ratings: "
                + ", ".join(f"{t} ({r:g}/10)" for t, r in top)
            )
        return "\n".join(lines)

    def get_context_summary(self) -> str:
        summaries = []
        for film in self.films:
            watchers = ", ".join([w["name"] for w in film["watchers"]])
            ratings = [w["rating"] for w in film["watchers"] if w["rating"] is not None]
            if ratings:
                avg_rating = sum(ratings) / len(ratings)
                summaries.append(f"- {film['title']}: {watchers} (avg {avg_rating:.1f}/10)")
            else:
                summaries.append(f"- {film['title']}: {watchers}")
        return "Films watched by the group:\n" + "\n".join(summaries)

    def recommend_for_person(self, name: str) -> str | None:
        person = self.get_person_profile(name)
        if not person:
            return None

        suggestion = self.suggest_for_person(name)
        if not suggestion:
            return f"I don't have enough data yet to recommend something for {name}."

        themes = ", ".join(suggestion["themes"][:3])
        response = f"**For {name}:**\n"
        response += f"I'd recommend **{suggestion['title']}** ({suggestion['year']})\n"
        response += f"**Why?** You tend to go for films about {themes}. {suggestion['title']} is all about that.\n"
        response += f"**Quick take:** {suggestion['group_consensus']}"
        return response

    def answer_about_film(self, question: str) -> str | None:
        question_lower = question.lower()

        if "next" in question_lower or "pick" in question_lower or "choose" in question_lower:
            for name in self.people:
                if name.lower() in question_lower:
                    suggestion = self.suggest_for_person(name)
                    if suggestion:
                        return (
                            f"{name} tends to like films about "
                            f"{', '.join(suggestion['themes'][:2])}. "
                            f"Probably something like {suggestion['title']}."
                        )

        if "recommend" in question_lower or "suggest" in question_lower:
            for name in self.people:
                if name.lower() in question_lower:
                    return self.recommend_for_person(name)

        return None
