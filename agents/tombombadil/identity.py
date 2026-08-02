"""Discord author -> film-club viewer resolution.

Maps an incoming Discord user (id + display name) to a :class:`Viewer`
record so Tom Bombadil can:

- pull the right film summary from ``FILM_DATABASE``,
- thread per-user prefs and memory by a stable canonical name, and
- distinguish the bot owner (Solomon) from regulars (Brian, Gavin, Isis,
  Anthony Taylor, G) from strangers (no data yet).

The mapping is loaded from ``data/tombombadil/identity.yaml`` (mounted
read-only into the container; gitignored). When the YAML is missing or
a Discord author isn't listed, we fall back to a case-insensitive name
match against ``FILM_DATABASE['people']`` so the bot is still useful
before the YAML is filled in.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

import yaml

from agents.tombombadil.film_knowledge import FILM_DATABASE
from core.logging import get_logger

log = get_logger("agents.tombombadil.identity")

_DEFAULT_CONFIG_PATH = Path("/app/data/tombombadil/identity.yaml")
_ROLE_OVERRIDE_KEY = "tom:identity:{discord_id}"


class Tier(StrEnum):
    SOLOMON = "solomon"
    REGULAR = "regular"
    STRANGER = "stranger"


@dataclass(frozen=True)
class Viewer:
    discord_id: str
    discord_name: str
    canonical_name: str | None
    tier: Tier

    @property
    def is_owner(self) -> bool:
        return self.tier is Tier.SOLOMON


@dataclass(frozen=True)
class _Config:
    owner_id: str | None
    owner_name: str | None
    regulars_by_id: dict[str, str]


def _config_path() -> Path:
    override = os.environ.get("TOMBOMBADIL_IDENTITY_FILE")
    return Path(override) if override else _DEFAULT_CONFIG_PATH


@lru_cache(maxsize=1)
def _load_config() -> _Config:
    path = _config_path()
    if not path.exists():
        log.info("identity_yaml_missing", path=str(path))
        return _Config(owner_id=None, owner_name=None, regulars_by_id={})

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError) as exc:
        log.error("identity_yaml_parse_failed", path=str(path), exc=str(exc))
        return _Config(owner_id=None, owner_name=None, regulars_by_id={})

    owner = raw.get("owner") or {}
    regulars = raw.get("regulars") or []
    regulars_by_id: dict[str, str] = {}
    for entry in regulars:
        rid = str(entry.get("discord_id") or "").strip()
        cname = str(entry.get("canonical_name") or "").strip()
        if rid and cname:
            regulars_by_id[rid] = cname

    cfg = _Config(
        owner_id=str(owner.get("discord_id") or "").strip() or None,
        owner_name=str(owner.get("canonical_name") or "").strip() or None,
        regulars_by_id=regulars_by_id,
    )
    log.info(
        "identity_yaml_loaded",
        owner_set=bool(cfg.owner_id),
        regulars=len(cfg.regulars_by_id),
    )
    return cfg


def _strip_discord_discriminator(name: str) -> str:
    # Old-style "Brian#1234" -> "Brian". Newer Discord usernames have no
    # discriminator but the strip is harmless when there's no '#'.
    return name.split("#", 1)[0].strip()


def _match_film_db_by_name(name: str) -> str | None:
    target = name.lower()
    for canonical in FILM_DATABASE.get("people", {}):
        if canonical.lower() == target:
            return canonical
    # Case-insensitive containment: "Solomon [GMNI]" -> "Solomon Smith".
    for canonical in FILM_DATABASE.get("people", {}):
        first = canonical.split(" ", 1)[0].lower()
        if first and (first == target or first in target.split()):
            return canonical
    return None


def _role_override_key(discord_id: str) -> str:
    return _ROLE_OVERRIDE_KEY.format(discord_id=str(discord_id).strip())


def get_role_override(redis, discord_id: str) -> tuple[Tier, str | None] | None:
    """Return a Redis role override ``(tier, canonical_name)`` or ``None``."""
    if redis is None:
        return None
    try:
        raw = redis.hgetall(_role_override_key(discord_id)) or {}
    except Exception as exc:
        log.warning("role_override_read_failed", discord_id=discord_id, exc=str(exc))
        return None
    if not raw:
        return None
    data: dict[str, str] = {}
    for k, v in raw.items():
        if isinstance(k, bytes):
            k = k.decode("utf-8")
        if isinstance(v, bytes):
            v = v.decode("utf-8")
        data[str(k)] = str(v)
    tier_raw = (data.get("tier") or "").strip().lower()
    try:
        tier = Tier(tier_raw)
    except ValueError:
        return None
    canonical = (data.get("canonical_name") or "").strip() or None
    if tier is Tier.STRANGER:
        canonical = None
    return tier, canonical


def set_role_override(
    redis,
    discord_id: str,
    *,
    tier: Tier,
    canonical_name: str | None = None,
) -> None:
    """Persist a runtime identity override (used by ``/setrole``)."""
    discord_id = str(discord_id).strip()
    mapping = {"tier": tier.value}
    if tier is Tier.STRANGER:
        mapping["canonical_name"] = ""
    else:
        mapping["canonical_name"] = (canonical_name or "").strip()
    redis.hset(_role_override_key(discord_id), mapping=mapping)


def clear_role_override(redis, discord_id: str) -> None:
    redis.delete(_role_override_key(str(discord_id).strip()))


def resolve(discord_id: str, discord_name: str, redis=None) -> Viewer:
    """Resolve a Discord author to a :class:`Viewer`.

    Resolution order:
    0. Redis ``/setrole`` override (when ``redis`` is provided).
    1. YAML owner entry (by discord_id) -> ``Tier.SOLOMON``.
    2. YAML regulars entry (by discord_id) -> ``Tier.REGULAR``.
    3. Case-insensitive name match against ``FILM_DATABASE['people']``.
       Solomon's canonical name -> ``Tier.SOLOMON``; everyone else
       -> ``Tier.REGULAR``.
    4. Fallback: ``Tier.STRANGER`` with ``canonical_name=None``.
    """
    discord_id = str(discord_id).strip()
    discord_name = str(discord_name).strip()

    override = get_role_override(redis, discord_id)
    if override is not None:
        tier, canonical = override
        return Viewer(discord_id, discord_name, canonical, tier)

    cfg = _load_config()

    if cfg.owner_id and discord_id == cfg.owner_id:
        canonical = cfg.owner_name or _match_film_db_by_name(discord_name) or "Solomon Smith"
        return Viewer(discord_id, discord_name, canonical, Tier.SOLOMON)

    if discord_id in cfg.regulars_by_id:
        return Viewer(discord_id, discord_name, cfg.regulars_by_id[discord_id], Tier.REGULAR)

    stripped = _strip_discord_discriminator(discord_name)
    canonical = _match_film_db_by_name(stripped) or _match_film_db_by_name(discord_name)
    if canonical:
        # Solomon's seed identity gets the owner tier even without YAML.
        if canonical.lower() == "solomon smith":
            return Viewer(discord_id, discord_name, canonical, Tier.SOLOMON)
        return Viewer(discord_id, discord_name, canonical, Tier.REGULAR)

    return Viewer(discord_id, discord_name, None, Tier.STRANGER)


def all_known() -> list[str]:
    """Return canonical names for everyone Tom can recognise.

    Used to enumerate club members in the system prompt so the LLM can
    correctly distinguish speakers in multi-user channels.
    """
    cfg = _load_config()
    names: list[str] = []
    if cfg.owner_name:
        names.append(cfg.owner_name)
    names.extend(cfg.regulars_by_id.values())
    for canonical in FILM_DATABASE.get("people", {}):
        if canonical not in names:
            names.append(canonical)
    return names


def reload_config() -> None:
    """Drop the cached YAML config. Test-only seam."""
    _load_config.cache_clear()
