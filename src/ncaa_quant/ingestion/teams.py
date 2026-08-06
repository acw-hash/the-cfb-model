"""Shared team-name normalization for cross-source joins.

Team naming mismatches across sources are the #1 integration bug in this
project. Canonical school names come from ``configs/team_names.yaml`` via
:func:`normalize_team_name` / :func:`make_game_key`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path

from omegaconf import OmegaConf

# Common mascot / nickname tokens stripped when a name is missing from the map.
_MASCOT_SUFFIX_RE = re.compile(
    r"\s+(?:Falcons|Zips|Crimson Tide|Mountaineers|Sun Devils|Wildcats|"
    r"Razorbacks|Red Wolves|Black Knights|Tigers|Cardinals|Broncos|Eagles|"
    r"Bulls|Cougars|Golden Bears|Chippewas|49ers|Bearcats|Chanticleers|"
    r"Buffaloes|Rams|Blue Devils|Pirates|Owls|Gators|Panthers|Seminoles|"
    r"Bulldogs|Yellow Jackets|Rainbow Warriors|Fighting Illini|Hoosiers|"
    r"Hawkeyes|Cyclones|Gamecocks|Dukes|Jayhawks|Golden Flashes|Flames|"
    r"Ragin[' ]?Cajuns|Thundering Herd|Terrapins|Hurricanes|RedHawks|"
    r"Spartans|Wolverines|Blue Raiders|Golden Gophers|Midshipmen|Wolfpack|"
    r"Wolf Pack|Cornhuskers|Lobos|Aggies|Tar Heels|Mean Green|Huskies|"
    r"Bobcats|Buckeyes|Sooners|Cowboys|Monarchs|Rebels|Ducks|Beavers|"
    r"Nittany Lions|Boilermakers|Scarlet Knights|Bearkats|Aztecs|"
    r"Mustangs|Jaguars|Golden Eagles|Cardinal|Orange|Horned Frogs|"
    r"Volunteers|Longhorns|Red Raiders|Rockets|Trojans|Green Wave|"
    r"Golden Hurricane|Blazers|Knights|Bruins|Warhawks|Minutemen|"
    r"Miners|Roadrunners|Commodores|Cavaliers|Hokies|Demon Deacons|"
    r"Badgers|Fighting Irish)\s*$",
    re.IGNORECASE,
)


def _norm_key(name: str) -> str:
    return " ".join(name.split()).casefold()


def load_team_name_map(path: Path | str) -> dict[str, str]:
    """Load ``odds_api`` name → canonical school map from YAML."""
    cfg = OmegaConf.load(Path(path))
    container = OmegaConf.to_container(cfg, resolve=True)
    if not isinstance(container, dict):
        msg = f"team names config must be a mapping: {path}"
        raise TypeError(msg)
    raw = container.get("odds_api", container)
    if not isinstance(raw, dict):
        msg = f"odds_api map missing in {path}"
        raise TypeError(msg)
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            msg = f"team name entries must be str→str, got {key!r}: {value!r}"
            raise TypeError(msg)
        out[_norm_key(key)] = value.strip()
    return out


def normalize_team_name(name: str, mapping: Mapping[str, str]) -> str:
    """Map a sportsbook/Odds API display name to the canonical school name.

    Lookup is case-insensitive with collapsed whitespace. Unmapped names get a
    deterministic mascot-suffix strip; if that yields nothing useful, the
    whitespace-normalized original is returned.
    """
    cleaned = " ".join(name.split())
    if not cleaned:
        return cleaned
    hit = mapping.get(_norm_key(cleaned))
    if hit is not None:
        return hit
    stripped = _MASCOT_SUFFIX_RE.sub("", cleaned).strip()
    if stripped and stripped != cleaned:
        hit2 = mapping.get(_norm_key(stripped))
        if hit2 is not None:
            return hit2
        return stripped
    return cleaned


def make_game_key(
    season: int,
    home_team: str,
    away_team: str,
    kickoff_date: date,
) -> str:
    """Stable deterministic key: ``{season}:{home}:{away}:{YYYY-MM-DD}``.

    Teams must already be canonical (via :func:`normalize_team_name`).
    """
    return f"{season}:{home_team}:{away_team}:{kickoff_date.isoformat()}"
