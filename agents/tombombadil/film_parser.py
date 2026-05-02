from __future__ import annotations

import re

from core.logging import get_logger

log = get_logger("agents.tombombadil.film_parser")


class FilmNoteParser:
    MAX_INPUT_LENGTH = 5000

    PATTERNS = {
        "name": r"(?im)^name\s*:?\s*(.+?)$",
        "film": r"(?im)^film\s*:?\s*(.+?)$",
        "rating": r"(?im)^rating\s*:?\s*(-?\d+(?:\.\d+)?)",
        "reaction": r"(?im)^reaction\s*:?\s*(.+?)(?=\n[a-z]+\s*:|$)",
        "themes": r"(?im)^themes?\s*:?\s*(.+?)(?=\n[a-z]+\s*:|$)",
    }

    def parse(self, text: str) -> dict:
        result = {
            "valid": False,
            "errors": [],
            "warnings": [],
            "data": {},
            "raw_input": text,
        }

        if not text or not text.strip():
            result["errors"].append("Empty submission")
            log.warning("parse_failed_empty_input")
            return result

        if len(text) > self.MAX_INPUT_LENGTH:
            result["errors"].append("Too long")
            log.warning(
                "parse_failed_too_long",
                input_length=len(text),
                max_length=self.MAX_INPUT_LENGTH,
            )
            return result

        log.debug("parsing_input", input_length=len(text))

        text = re.sub(r"\*\*|__|\*|_", "", text)

        extracted: dict = {}
        for field, pattern in self.PATTERNS.items():
            match = re.search(pattern, text)
            if match:
                extracted[field] = match.group(1).strip()

        if not extracted.get("film"):
            result["errors"].append("Film required")

        if not extracted.get("rating"):
            result["errors"].append("Rating required")
        else:
            try:
                rating = float(extracted["rating"])
                rating = max(0, min(10, rating))
                extracted["rating"] = rating
            except (ValueError, TypeError):
                result["errors"].append("Rating must be numeric")

        if not extracted.get("name"):
            result["warnings"].append("Name missing")
            extracted["name"] = None

        extracted.setdefault("reaction", None)
        extracted.setdefault("themes", None)

        if result["errors"]:
            log.warning(
                "parse_errors",
                errors=result["errors"],
                extracted_fields=list(extracted.keys()),
            )
            return result

        result["valid"] = True
        result["data"] = extracted
        log.debug(
            "parse_successful",
            film=extracted.get("film"),
            has_name=extracted.get("name") is not None,
            warnings=result["warnings"],
        )
        return result


parser = FilmNoteParser()


def parse_film_note(text: str) -> dict:
    return parser.parse(text)
