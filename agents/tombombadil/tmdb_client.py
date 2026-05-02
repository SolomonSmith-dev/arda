from __future__ import annotations

from typing import Any

import httpx

from core.config import settings
from core.logging import get_logger

log = get_logger("agents.tombombadil.tmdb")

TMDB_BASE_URL = "https://api.themoviedb.org/3"


class TMDBClient:
    def __init__(self):
        self.api_key = settings.tmdb_api_key
        self.base_url = TMDB_BASE_URL

    def _get(self, path: str, params: dict | None = None) -> dict | None:
        if not self.api_key:
            return None
        try:
            resp = httpx.get(
                f"{self.base_url}{path}",
                params={"api_key": self.api_key, **(params or {})},
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            log.warning("tmdb_request_failed", path=path, exception=str(e))
            return None

    def search_film(self, title: str) -> dict[str, Any] | None:
        data = self._get("/search/movie", {"query": title})
        if not data:
            return None
        results = data.get("results", [])
        return results[0] if results else None

    def get_film_details(self, tmdb_id: int) -> dict[str, Any] | None:
        return self._get(f"/movie/{tmdb_id}", {"append_to_response": "credits,reviews"})

    def format_film_info(self, film_data: dict[str, Any]) -> str:
        title = film_data.get("title", "Unknown")
        year = film_data.get("release_date", "")[:4]
        overview = film_data.get("overview", "")[:200]

        crew = film_data.get("credits", {}).get("crew", [])
        director = next((c["name"] for c in crew if c["job"] == "Director"), "Unknown")

        return f"**{title}** ({year})\nDirector: {director}\nOverview: {overview}..."

    def lookup_film(self, title: str) -> str | None:
        result = self.search_film(title)
        if not result:
            return None
        details = self.get_film_details(result["id"])
        return self.format_film_info(details) if details else None
