import json
import logging
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from football_match_monitor.exceptions import DataValidationError, ESPNAPIError
from football_match_monitor.models import League, MonitorConfig

logger = logging.getLogger("football_match_monitor")
_TEAM_ID = re.compile(r"^\d+$")
_PLAYER_STATS = {
    "goals": "Goals",
    "goalAssists": "Assists",
    "totalShots": "Shots",
    "shotsOnTarget": "Shots on target",
    "saves": "Saves",
    "yellowCards": "Yellow cards",
    "redCards": "Red cards",
}


class ESPNDirectoryClient:
    def __init__(self, config: MonitorConfig):
        self._config = config
        self._cache_dir = Path(config.api_cache_dir) / "directory"

    def get_teams(self, league: League) -> list[dict]:
        payload = self._get(league, "teams", f"{league.id}/teams", {"limit": 1000})
        try:
            sports = payload["sports"]
            leagues = [item for sport in sports for item in sport.get("leagues", [])]
            entry = next(item for item in leagues if item.get("slug") == league.id)
            teams = [item["team"] for item in entry["teams"] if isinstance(item.get("team"), dict)]
        except (KeyError, StopIteration, TypeError) as exc:
            raise DataValidationError(f"ESPN returned an invalid team list for '{league.id}'.") from exc
        return [
            {
                "id": str(team["id"]),
                "name": team.get("displayName") or team.get("name") or "Unknown team",
                "abbreviation": team.get("abbreviation", ""),
                "logo": _logo(team),
            }
            for team in teams
            if team.get("id") is not None
        ]

    def get_roster(self, league: League, team_id: str) -> list[dict]:
        team_id = self._validate_team_id(team_id)
        payload = self._get(league, f"teams/{team_id}/roster", f"{league.id}/teams/{team_id}/roster")
        athletes = payload.get("athletes")
        if not isinstance(athletes, list):
            raise DataValidationError(f"ESPN returned an invalid roster for team '{team_id}'.")
        return [
            _normalize_player(player)
            for player in athletes
            if isinstance(player, dict) and player.get("id") is not None
        ]

    def get_team_schedule(self, league: League, team_id: str) -> list[dict]:
        team_id = self._validate_team_id(team_id)
        payload = self._get(league, f"teams/{team_id}/schedule", f"{league.id}/teams/{team_id}/schedule")
        events = payload.get("events")
        if not isinstance(events, list):
            raise DataValidationError(f"ESPN returned an invalid schedule for team '{team_id}'.")
        return [event for event in events if isinstance(event, dict)]

    def get_match_summary(self, league: League, event_id: str) -> dict:
        if not _TEAM_ID.fullmatch(event_id):
            raise ValueError("Invalid ESPN match id.")
        return self._get(league, f"events/{event_id}/summary", f"{league.id}/summary", {"event": event_id})

    def _get(self, league: League, resource: str, api_path: str, params: dict | None = None) -> dict:
        cache_path = self._cache_dir / league.id / f"{resource.replace('/', '_')}.json"
        url = f"{self._config.api_base_url}/{api_path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        request = Request(url, headers={"User-Agent": "FootballMatchMonitor/1.0"})
        try:
            with urlopen(request, timeout=self._config.request_timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict) or not payload:
                raise ESPNAPIError(f"ESPN returned an invalid response for '{resource}'.")
            self._save_cache(cache_path, league, resource, payload)
            return payload
        except HTTPError as exc:
            if exc.code < 500:
                raise ESPNAPIError(f"ESPN returned HTTP {exc.code} for '{resource}'.") from exc
            exc.close()
            return self._load_fallback(cache_path, league, resource, exc)
        except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return self._load_fallback(cache_path, league, resource, exc)

    def _save_cache(self, path: Path, league: League, resource: str, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        envelope = {
            "league_id": league.id,
            "resource": resource,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "data": payload,
        }
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, prefix=f"{path.name}.", suffix=".tmp", delete=False
            ) as file:
                temporary_path = Path(file.name)
                json.dump(envelope, file, ensure_ascii=False)
                file.write("\n")
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def _load_fallback(self, path: Path, league: League, resource: str, error: Exception) -> dict:
        if path.exists():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ESPNAPIError(f"ESPN is unavailable and cached '{resource}' data is invalid: {exc}") from error
            if (
                isinstance(cached, dict)
                and cached.get("league_id") == league.id
                and cached.get("resource") == resource
                and isinstance(cached.get("data"), dict)
            ):
                logger.warning(
                    "ESPN unavailable; serving saved directory data",
                    extra={"event": "espn_directory_cache_fallback", "league_id": league.id},
                )
                return cached["data"]
            raise ESPNAPIError(f"ESPN is unavailable and cached '{resource}' data has an invalid structure.") from error
        raise ESPNAPIError(f"ESPN request for '{resource}' failed and no local copy is available: {error}") from error

    @staticmethod
    def _validate_team_id(team_id: str) -> str:
        if not isinstance(team_id, str) or not _TEAM_ID.fullmatch(team_id):
            raise ValueError("Invalid ESPN team id.")
        return team_id


def build_team_performance(
    client: ESPNDirectoryClient,
    league: League,
    team: dict,
    max_concurrent_requests: int,
    as_of: date | None = None,
) -> dict:
    schedule = client.get_team_schedule(league, team["id"])
    completed = [
        event for event in schedule
        if _event_status(event) == "post"
        and _competitor_for_team(event, team["id"]) is not None
    ]
    if as_of is not None:
        completed = [
            event for event in completed
            if (event_date := _event_date(event)) is not None and event_date <= as_of
        ]
    completed.sort(key=lambda event: event.get("date", ""), reverse=True)
    recent = completed[:5]
    form = []
    scored = 0
    conceded = 0
    wins = draws = losses = 0
    for event in completed:
        own, opponent = _scores_for_team(event, team["id"])
        if own is None or opponent is None:
            continue
        scored += own
        conceded += opponent
        if own > opponent:
            wins += 1
        elif own == opponent:
            draws += 1
        else:
            losses += 1
    for event in recent:
        own, opponent = _scores_for_team(event, team["id"])
        if own is None or opponent is None:
            continue
        form.append({
            "event_id": str(event.get("id", "")),
            "date": event.get("date", ""),
            "opponent": _opponent_name(event, team["id"]),
            "home_away": _competitor_for_team(event, team["id"]).get("homeAway", ""),
            "goals_for": own,
            "goals_against": opponent,
            "result": "W" if own > opponent else "D" if own == opponent else "L",
        })

    summaries = []
    with ThreadPoolExecutor(max_workers=max_concurrent_requests) as executor:
        results = executor.map(
            lambda event: _summary_or_none(client, league, str(event.get("id", ""))),
            recent,
        )
        summaries = [summary for summary in results if summary is not None]
    player_performance = _aggregate_player_leaders(summaries, team["id"])
    return {
        "team": team,
        "league": league.name,
        "as_of": as_of.isoformat() if as_of else None,
        "season_record": f"{wins}-{draws}-{losses}",
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "goals_for": scored,
        "goals_against": conceded,
        "goal_difference": scored - conceded,
        "matches_played": wins + draws + losses,
        "recent_form": form,
        "player_performance": player_performance,
    }


def _summary_or_none(client, league: League, event_id: str) -> dict | None:
    if not _TEAM_ID.fullmatch(event_id):
        return None
    return client.get_match_summary(league, event_id)


def _event_date(event: dict) -> date | None:
    try:
        return date.fromisoformat(str(event.get("date", ""))[:10])
    except ValueError:
        return None


def _aggregate_player_leaders(summaries: list[dict], team_id: str) -> list[dict]:
    players = {}
    for summary in summaries:
        for team_entry in summary.get("leaders", []):
            if not isinstance(team_entry, dict) or str(team_entry.get("team", {}).get("id")) != team_id:
                continue
            for category in team_entry.get("leaders", []):
                if not isinstance(category, dict):
                    continue
                stat = category.get("name")
                if stat not in _PLAYER_STATS:
                    continue
                for leader in category.get("leaders", []):
                    athlete = leader.get("athlete", {}) if isinstance(leader, dict) else {}
                    player_id = str(athlete.get("id", ""))
                    if not player_id:
                        continue
                    player = players.setdefault(player_id, {
                        "id": player_id,
                        "name": athlete.get("displayName") or athlete.get("fullName") or "Unknown player",
                        "position": (athlete.get("position") or {}).get("displayName", ""),
                        "match_leader_categories": 0,
                        "stats": {},
                    })
                    player["match_leader_categories"] += 1
                    value = _numeric_value(leader.get("displayValue"))
                    player["stats"][stat] = player["stats"].get(stat, 0) + value
    return sorted(
        players.values(),
        key=lambda player: (
            player["stats"].get("goals", 0) + player["stats"].get("goalAssists", 0),
            player["match_leader_categories"],
            player["name"].casefold(),
        ),
        reverse=True,
    )


def _numeric_value(value) -> float:
    try:
        return float(str(value).replace("%", ""))
    except (TypeError, ValueError):
        return 0


def _normalize_player(player: dict) -> dict:
    position = player.get("position")
    status = player.get("status")
    return {
        "id": str(player["id"]),
        "name": player.get("displayName") or player.get("fullName") or "Unknown player",
        "jersey": str(player.get("jersey", "")),
        "position": position.get("displayName", "") if isinstance(position, dict) else "",
        "age": player.get("age"),
        "nationality": (player.get("citizenshipCountry") or {}).get("name", "")
        if isinstance(player.get("citizenshipCountry"), dict) else "",
        "status": status.get("name", "") if isinstance(status, dict) else "",
        "headshot": _logo(player),
    }


def _logo(data: dict) -> str | None:
    if isinstance(data.get("logo"), str):
        return data["logo"]
    logos = data.get("logos")
    if isinstance(logos, list) and logos and isinstance(logos[0], dict):
        return logos[0].get("href")
    return None


def _event_status(event: dict) -> str:
    competition = (event.get("competitions") or [{}])[0]
    status = competition.get("status", {}).get("type", {})
    return status.get("state", "") if isinstance(status, dict) else ""


def _competitor_for_team(event: dict, team_id: str) -> dict | None:
    competition = (event.get("competitions") or [{}])[0]
    competitors = competition.get("competitors", [])
    return next(
        (competitor for competitor in competitors if str(competitor.get("team", {}).get("id")) == team_id),
        None,
    )


def _scores_for_team(event: dict, team_id: str) -> tuple[int | None, int | None]:
    competition = (event.get("competitions") or [{}])[0]
    own = opponent = None
    for competitor in competition.get("competitors", []):
        team = competitor.get("team", {})
        score = competitor.get("score")
        if isinstance(score, dict):
            score = score.get("displayValue", score.get("value"))
        try:
            parsed = int(float(score))
        except (TypeError, ValueError):
            parsed = None
        if str(team.get("id")) == team_id:
            own = parsed
        else:
            opponent = parsed
    return own, opponent


def _opponent_name(event: dict, team_id: str) -> str:
    competition = (event.get("competitions") or [{}])[0]
    for competitor in competition.get("competitors", []):
        team = competitor.get("team", {})
        if str(team.get("id")) != team_id:
            return team.get("displayName", "Opponent")
    return "Opponent"
