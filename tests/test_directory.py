"""Offline tests for ESPN team directories, local caching, and performance totals."""

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from football_match_monitor.directory import (
    ESPNDirectoryClient,
    build_team_performance,
)
from football_match_monitor.exceptions import ESPNAPIError
from football_match_monitor.models import League


LEAGUE = League("eng.1", "Premier League")


@pytest.fixture
def directory_config(tmp_path):
    """Provide an isolated cache folder and small request limits for each test."""
    return SimpleNamespace(
        api_base_url="https://example.test/soccer",
        api_cache_dir=str(tmp_path / "cache"),
        request_timeout_seconds=3,
        max_concurrent_requests=2,
    )


def test_get_teams_normalizes_and_caches_complete_payload(directory_config, monkeypatch):
    """A live team response is simplified for the UI and saved to disk."""
    from football_match_monitor import directory

    payload = {
        "sports": [{
            "leagues": [{
                "slug": "eng.1",
                "teams": [{"team": {
                    "id": "349",
                    "displayName": "AFC Bournemouth",
                    "abbreviation": "BOU",
                    "logos": [{"href": "https://a.espncdn.com/team.png"}],
                }}],
            }],
        }],
    }

    class Response:
        """Act like the context-managed HTTP response returned by urlopen."""

        def __enter__(self):
            """Allow the fake response to be used in a with statement."""
            return self

        def __exit__(self, *args):
            """Match the context manager interface without additional cleanup."""
            return None

        def read(self):
            """Return the fake ESPN response body as bytes."""
            return json.dumps(payload).encode()

    monkeypatch.setattr(directory, "urlopen", lambda *args, **kwargs: Response())
    client = ESPNDirectoryClient(directory_config)

    assert client.get_teams(LEAGUE) == [{
        "id": "349",
        "name": "AFC Bournemouth",
        "abbreviation": "BOU",
        "logo": "https://a.espncdn.com/team.png",
    }]
    cache_dir = Path(directory_config.api_cache_dir)
    saved = json.loads((cache_dir / "directory" / "eng.1" / "teams.json").read_text(encoding="utf-8"))
    assert saved["data"] == payload


def test_directory_uses_cached_payload_when_offline(directory_config, monkeypatch):
    """A network failure should use the same league's previously saved teams."""
    from football_match_monitor import directory
    from urllib.error import URLError

    payload = {
        "sports": [{"leagues": [{
            "slug": "eng.1",
            "teams": [{"team": {"id": "349", "displayName": "Bournemouth"}}],
        }]}],
    }
    client = ESPNDirectoryClient(directory_config)
    cache_path = Path(directory_config.api_cache_dir) / "directory" / "eng.1" / "teams.json"
    client._save_cache(cache_path, LEAGUE, "teams", payload)

    def offline(*args, **kwargs):
        """Simulate a machine with no available network connection."""
        raise URLError("offline")

    monkeypatch.setattr(directory, "urlopen", offline)
    assert client.get_teams(LEAGUE)[0]["name"] == "Bournemouth"


def test_directory_without_cache_reports_network_error(directory_config, monkeypatch):
    """Without a live response or saved copy, the client reports a clear failure."""
    from football_match_monitor import directory
    from urllib.error import URLError

    def offline(*args, **kwargs):
        """Simulate an offline request when there is no cached response."""
        raise URLError("offline")

    monkeypatch.setattr(directory, "urlopen", offline)
    with pytest.raises(ESPNAPIError, match="no local copy"):
        ESPNDirectoryClient(directory_config).get_teams(LEAGUE)


def test_team_performance_aggregates_results_and_recent_player_leaders():
    """Season totals use all completed games; player stats come from match leaders."""
    team = {"id": "349", "name": "Bournemouth", "abbreviation": "BOU", "logo": None}
    schedule = [{
        "id": "7001",
        "date": "2026-10-25T14:00Z",
        "competitions": [{
            "status": {"type": {"state": "post"}},
            "competitors": [
                {"homeAway": "home", "team": {"id": "349", "displayName": "Bournemouth"}, "score": {"displayValue": "3"}},
                {"homeAway": "away", "team": {"id": "360", "displayName": "Man Utd"}, "score": {"displayValue": "0"}},
            ],
        }],
    }]
    summary = {
        "leaders": [{
            "team": {"id": "349"},
            "leaders": [{
                "name": "goals",
                "leaders": [{
                    "displayValue": "2",
                    "athlete": {
                        "id": "123",
                        "displayName": "Player One",
                        "position": {"displayName": "Forward"},
                    },
                }],
            }, {
                "name": "totalShots",
                "leaders": [{
                    "displayValue": "4",
                    "athlete": {"id": "123", "displayName": "Player One"},
                }],
            }],
        }],
    }
    client = SimpleNamespace(
        get_team_schedule=lambda league, team_id: schedule,
        get_match_summary=lambda league, event_id: summary,
    )
    result = build_team_performance(client, LEAGUE, team, 2)

    assert result["season_record"] == "1-0-0"
    assert result["goals_for"] == 3
    assert result["goals_against"] == 0
    assert result["recent_form"][0]["opponent"] == "Man Utd"
    assert result["recent_form"][0]["result"] == "W"
    assert result["player_performance"][0]["stats"] == {"goals": 2.0, "totalShots": 4.0}


def test_team_performance_only_includes_completed_matches_through_selected_date():
    """The selected date excludes later completed results from every calculation."""
    team = {"id": "349", "name": "Bournemouth", "abbreviation": "BOU", "logo": None}
    past_match = {
        "id": "7001",
        "date": "2026-10-25T14:00Z",
        "competitions": [{
            "status": {"type": {"state": "post"}},
            "competitors": [
                {"homeAway": "home", "team": {"id": "349"}, "score": {"displayValue": "3"}},
                {"homeAway": "away", "team": {"id": "360"}, "score": {"displayValue": "0"}},
            ],
        }],
    }
    future_match = {
        **past_match,
        "id": "7002",
        "date": "2026-10-26T14:00Z",
        "competitions": [{
            **past_match["competitions"][0],
            "competitors": [
                {"homeAway": "home", "team": {"id": "349"}, "score": {"displayValue": "0"}},
                {"homeAway": "away", "team": {"id": "360"}, "score": {"displayValue": "2"}},
            ],
        }],
    }
    client = SimpleNamespace(
        get_team_schedule=lambda league, team_id: [past_match, future_match],
        get_match_summary=lambda league, event_id: {},
    )

    result = build_team_performance(client, LEAGUE, team, 2, as_of=date(2026, 10, 25))

    assert result["as_of"] == "2026-10-25"
    assert result["season_record"] == "1-0-0"
    assert result["goals_for"] == 3
    assert [match["event_id"] for match in result["recent_form"]] == ["7001"]
