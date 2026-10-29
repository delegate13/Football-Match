"""ESPN scoreboard requests, offline fallback, and event normalization."""

import json
import logging
import threading
from datetime import date
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from football_match_monitor.cache import ScoreboardCache
from football_match_monitor.exceptions import DataValidationError, ESPNAPIError
from football_match_monitor.models import League, Match, MonitorConfig

logger = logging.getLogger(__name__)


class ESPNClient:
    """Download and normalize scoreboard data, falling back to the local cache."""

    def __init__(self, config: MonitorConfig):
        """Set up the API settings and a thread-safe count of cache fallbacks."""
        self._config = config
        self._cache = ScoreboardCache(config.api_cache_dir)
        self._cache_hits = 0
        self._cache_hits_lock = threading.Lock()

    @property
    def used_cache(self) -> bool:
        """Tell reports whether any request in this client used offline data."""
        with self._cache_hits_lock:
            return self._cache_hits > 0

    def get_matches(self, league: League, match_date: date) -> list[Match]:
        """Request one league/day scoreboard and convert events to Match objects."""
        # ESPN expects the requested calendar date without dashes.
        url = (
            f"{self._config.api_base_url}/{league.id}/scoreboard?"
            f"{urlencode({'dates': match_date.strftime('%Y%m%d')})}"
        )
        request = Request(url, headers={"User-Agent": "FootballMatchMonitor/1.0"})
        try:
            # The timeout prevents a stalled network request hanging a monitor run.
            with urlopen(request, timeout=self._config.request_timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            # Client errors (for example 404) should be visible, not hidden by stale data.
            if exc.code >= 500:
                exc.close()
                return self._use_cache_or_raise(league, match_date, exc)
            raise ESPNAPIError(
                f"ESPN returned HTTP {exc.code} for league '{league.id}' on {match_date.isoformat()}."
            ) from exc
        except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return self._use_cache_or_raise(league, match_date, exc)

        if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
            raise ESPNAPIError(f"ESPN returned an invalid scoreboard for league '{league.id}'.")
        # Normalize before caching so malformed events are not saved as usable data.
        matches = [parse_event(event, league) for event in payload["events"]]
        self._cache.save(league, match_date, payload)
        logger.info(
            "ESPN scoreboard cached",
            extra={"event": "espn_cache_saved", "league_id": league.id, "date": match_date.isoformat()},
        )
        return matches

    def _use_cache_or_raise(self, league: League, match_date: date, error: Exception) -> list[Match]:
        """Use a saved scoreboard when available; otherwise raise a clear API error."""
        try:
            payload = self._cache.load(league, match_date)
        except DataValidationError as cache_error:
            raise ESPNAPIError(
                f"ESPN is unavailable for league '{league.id}' on {match_date.isoformat()} "
                f"and its local cache is unusable: {cache_error}"
            ) from error
        if payload is None:
            logger.error(
                "ESPN request failed and no local scoreboard cache exists",
                extra={"event": "espn_request_failed", "league_id": league.id, "date": match_date.isoformat()},
            )
            raise ESPNAPIError(
                f"ESPN request failed for league '{league.id}' on {match_date.isoformat()}, "
                f"and no local copy is available: {error}"
            ) from error
        matches = [parse_event(event, league) for event in payload["events"]]
        # A lock is needed because league requests run concurrently in worker threads.
        with self._cache_hits_lock:
            self._cache_hits += 1
        logger.warning(
            "ESPN unavailable; serving saved scoreboard data",
            extra={"event": "espn_cache_fallback", "league_id": league.id, "date": match_date.isoformat()},
        )
        return matches


def parse_event(event: object, league: League) -> Match:
    """Convert ESPN's nested event JSON into the app's small Match data model."""
    if not isinstance(event, dict):
        raise DataValidationError(f"Invalid event data for league '{league.id}': expected object.")
    event_id = event.get("id")
    competitions = event.get("competitions")
    if not isinstance(event_id, str) or not isinstance(competitions, list) or not competitions:
        raise DataValidationError(f"Event in league '{league.id}' is missing id or competition data.")
    competition = competitions[0]
    # ESPN nests home/away details inside the event's competition object.
    competitors = competition.get("competitors", []) if isinstance(competition, dict) else []
    if not isinstance(competitors, list) or len(competitors) < 2:
        raise DataValidationError(f"Event '{event_id}' does not have two competitors.")
    teams = {}
    for competitor in competitors:
        if not isinstance(competitor, dict):
            continue
        home_away = competitor.get("homeAway")
        team = competitor.get("team")
        if home_away in {"home", "away"} and isinstance(team, dict):
            team_name = team.get("displayName")
            if isinstance(team_name, str) and team_name:
                teams[home_away] = (team_name, _score(competitor.get("score")))
    if "home" not in teams or "away" not in teams:
        raise DataValidationError(f"Event '{event_id}' is missing home or away team details.")

    # Normalize unknown ESPN status values to an upcoming fixture.
    status_data = competition.get("status", {})
    status_type = status_data.get("type", {}) if isinstance(status_data, dict) else {}
    state = status_type.get("state", "pre") if isinstance(status_type, dict) else "pre"
    if state not in {"pre", "in", "post"}:
        state = "pre"
    venue_data = competition.get("venue", {})
    venue = venue_data.get("fullName") if isinstance(venue_data, dict) else None
    return Match(
        id=event_id,
        league_id=league.id,
        league_name=league.name,
        date=str(event.get("date", "")),
        name=str(event.get("name", "")),
        home_team=teams["home"][0],
        away_team=teams["away"][0],
        home_score=teams["home"][1],
        away_score=teams["away"][1],
        status=state,
        status_detail=str(status_type.get("detail", "")) if isinstance(status_type, dict) else "",
        venue=venue if isinstance(venue, str) else None,
    )


def _score(value: object) -> Optional[int]:
    """Turn ESPN's score value into an integer; keep unreported scores as None."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise DataValidationError(f"Invalid team score: {value!r}.") from None
