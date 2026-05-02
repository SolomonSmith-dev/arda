"""Auto-detect and parse film notes from Discord messages.

Respects weekly window (Wed-Sun) and confirmation requirements.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any


class AutoParser:
    PARSE_DAYS = [2, 3, 4, 5, 6]  # 0=Monday, 6=Sunday — parse Wed-Sun

    def __init__(self, redis_client=None):
        self.redis = redis_client
        self.confirmation_key = "tom_confirmation:movie"

    def is_in_parse_window(self) -> bool:
        return datetime.now().weekday() in self.PARSE_DAYS

    def get_days_until_window(self) -> int:
        today = datetime.now().weekday()
        if today in self.PARSE_DAYS:
            return 0
        days_ahead = 2 - today  # 2 = Wednesday
        if days_ahead <= 0:
            days_ahead += 7
        return days_ahead

    def set_movie_confirmation(self, movie_title: str) -> bool:
        if not self.redis:
            return False
        try:
            self.redis.set(self.confirmation_key, movie_title)
            return True
        except Exception as e:
            print(f"Error setting confirmation: {e}")
            return False

    def get_movie_confirmation(self) -> str | None:
        if not self.redis:
            return None
        try:
            return self.redis.get(self.confirmation_key)
        except Exception as e:
            print(f"Error getting confirmation: {e}")
            return None

    def clear_movie_confirmation(self) -> bool:
        if not self.redis:
            return False
        try:
            self.redis.delete(self.confirmation_key)
            return True
        except Exception as e:
            print(f"Error clearing confirmation: {e}")
            return False

    def detect_notes_in_message(self, text: str) -> dict[str, Any] | None:
        has_film = bool(re.search(r"film:\s*(.+?)(?:\n|$)", text, re.IGNORECASE))
        has_rating = bool(re.search(r"rating.*?:\s*(\d+)", text, re.IGNORECASE))

        if not (has_film and has_rating):
            return None

        note: dict[str, Any] = {}

        name_match = re.search(r"name:\s*(.+?)(?:\n|$)", text, re.IGNORECASE)
        if name_match:
            note["name"] = name_match.group(1).strip()

        film_match = re.search(r"film:\s*(.+?)(?:\n|$)", text, re.IGNORECASE)
        if film_match:
            note["film"] = film_match.group(1).strip()

        rating_match = re.search(r"rating.*?:\s*(\d+(?:/\d+)?)", text, re.IGNORECASE)
        if rating_match:
            note["rating"] = rating_match.group(1).strip()

        reaction_match = re.search(
            r"reaction:\s*(.+?)(?:\n\n|\n[0-9])", text, re.IGNORECASE | re.DOTALL
        )
        if reaction_match:
            note["reaction"] = reaction_match.group(1).strip()[:100]

        themes_match = re.search(
            r"themes?:\s*(.+?)(?:\n\n|\n[0-9])", text, re.IGNORECASE | re.DOTALL
        )
        if themes_match:
            note["themes"] = themes_match.group(1).strip()[:200]

        return note if note.get("film") else None
