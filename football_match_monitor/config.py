"""Load and validate the user's external JSON configuration file."""

import json
import re
from pathlib import Path
from typing import Any

from football_match_monitor.exceptions import ConfigurationError
from football_match_monitor.models import League, MonitorConfig

_LEAGUE_ID = re.compile(r"^[a-zA-Z0-9._-]+$")
# ESPN's scoreboard status codes: pre-match, in-progress, and completed.
_STATUSES = {"pre", "in", "post"}


def load_config(path: str) -> MonitorConfig:
    """Read config.json, validate every setting, and return absolute file paths."""
    # Resolve relative output paths against the config file, not the shell location.
    config_path = Path(path).expanduser().resolve()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Unable to read config file '{config_path}': {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError("Configuration root must be a JSON object.")

    # Validate competition entries once, before they are used in API requests.
    leagues_raw = raw.get("leagues")
    if not isinstance(leagues_raw, list) or not leagues_raw:
        raise ConfigurationError("'leagues' must be a non-empty list.")
    leagues = []
    seen_leagues = set()
    for entry in leagues_raw:
        if not isinstance(entry, dict):
            raise ConfigurationError("Each league must be an object with 'id' and 'name'.")
        league_id, name = entry.get("id"), entry.get("name")
        if not isinstance(league_id, str) or not _LEAGUE_ID.fullmatch(league_id):
            raise ConfigurationError(f"Invalid ESPN league id: {league_id!r}.")
        if not isinstance(name, str) or not name.strip():
            raise ConfigurationError(f"League '{league_id}' must have a non-empty name.")
        if league_id in seen_leagues:
            raise ConfigurationError(f"Duplicate ESPN league id: '{league_id}'.")
        seen_leagues.add(league_id)
        leagues.append(League(league_id, name.strip()))

    # These limits prevent accidental huge date windows or request bursts.
    before = _integer(raw, "days_before", 1, minimum=0, maximum=30)
    after = _integer(raw, "days_after", 7, minimum=0, maximum=30)
    timeout = _integer(raw, "request_timeout_seconds", 15, minimum=1, maximum=120)
    max_concurrent_requests = _integer(raw, "max_concurrent_requests", 8, minimum=1, maximum=16)
    # Status values control which fixtures survive the business-rule filter.
    statuses_raw = raw.get("statuses", ["pre", "in", "post"])
    if (not isinstance(statuses_raw, list) or
            any(status not in _STATUSES for status in statuses_raw)):
        raise ConfigurationError("'statuses' must contain only 'pre', 'in', and/or 'post'.")
    # Team entries are optional; when present, a match must mention one of them.
    teams_raw = raw.get("teams", [])
    if not isinstance(teams_raw, list) or any(
        not isinstance(team, str) or not team.strip() for team in teams_raw
    ):
        raise ConfigurationError("'teams' must be a list of non-empty team names.")

    # Build one immutable settings object for all application layers.
    return MonitorConfig(
        api_base_url=_string(raw, "api_base_url", "https://site.api.espn.com/apis/site/v2/sports/soccer"),
        leagues=tuple(leagues),
        days_before=before,
        days_after=after,
        statuses=tuple(statuses_raw),
        teams=tuple(team.strip() for team in teams_raw),
        output_file=_output_path(config_path, raw, "output_file", "output/matches.json"),
        change_log_file=_output_path(config_path, raw, "change_log_file", "logs/changes.jsonl"),
        automation_log_file=_output_path(config_path, raw, "automation_log_file", "logs/automation.log"),
        state_file=_output_path(config_path, raw, "state_file", "data/previous_matches.json"),
        api_cache_dir=_output_path(config_path, raw, "api_cache_dir", "data/espn_cache"),
        request_timeout_seconds=timeout,
        max_concurrent_requests=max_concurrent_requests,
    )


def _integer(raw: dict[str, Any], key: str, default: int, minimum: int, maximum: int) -> int:
    """Read an integer setting and reject booleans and out-of-range values."""
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ConfigurationError(f"'{key}' must be an integer from {minimum} to {maximum}.")
    return value


def _string(raw: dict[str, Any], key: str, default: str) -> str:
    """Read a required text setting and trim trailing URL slashes."""
    value = raw.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"'{key}' must be a non-empty string.")
    return value.rstrip("/")


def _output_path(config_path: Path, raw: dict[str, Any], key: str, default: str) -> str:
    """Return a normalized absolute path for a configured output file or folder."""
    value = raw.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"'{key}' must be a non-empty path.")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = config_path.parent / path
    return str(path.resolve())
