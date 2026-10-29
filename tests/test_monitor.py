import json
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest

from football_match_monitor.config import load_config
from football_match_monitor.espn import parse_event
from football_match_monitor.exceptions import ConfigurationError, DataValidationError
from football_match_monitor.filters import filter_matches
from football_match_monitor.logging_config import JSONFormatter
from football_match_monitor.models import League
from football_match_monitor.monitor import MatchMonitor


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config(tmp_path):
    raw = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    raw.update({
        "leagues": [{"id": "eng.1", "name": "Premier League"}],
        "days_before": 0,
        "days_after": 0,
        "output_file": str(tmp_path / "report.json"),
        "change_log_file": str(tmp_path / "changes.jsonl"),
        "automation_log_file": str(tmp_path / "automation.log"),
        "state_file": str(tmp_path / "state.json"),
        "api_cache_dir": str(tmp_path / "espn_cache"),
    })
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(raw), encoding="utf-8")
    return load_config(str(config_file))


def event(score="2"):
    return {
        "id": "match-1",
        "name": "Liverpool vs Arsenal",
        "date": "2026-10-29T16:30Z",
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "team": {"displayName": "Liverpool"}, "score": score},
                {"homeAway": "away", "team": {"displayName": "Arsenal"}, "score": "1"},
            ],
            "status": {"type": {"state": "post", "detail": "Full Time"}},
        }],
    }


def test_load_config_rejects_bad_league_id(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"leagues": [{"id": "../other", "name": "Bad"}]}))
    with pytest.raises(ConfigurationError, match="Invalid ESPN league id"):
        load_config(str(config_path))


def test_default_config_contains_unique_league_catalog():
    config = load_config(str(ROOT / "config.json"))
    league_ids = [league.id for league in config.leagues]
    assert len(league_ids) == 82
    assert len(set(league_ids)) == len(league_ids)
    assert config.max_concurrent_requests == 8
    assert config.api_cache_dir.endswith("data\\espn_cache")


def test_parse_event_maps_teams_scores_and_status():
    match = parse_event(event(), League("eng.1", "Premier League"))
    assert match.home_team == "Liverpool"
    assert match.away_team == "Arsenal"
    assert match.home_score == 2
    assert match.status == "post"


def test_parse_event_rejects_missing_competitors():
    with pytest.raises(DataValidationError, match="two competitors"):
        parse_event({"id": "bad", "competitions": [{"competitors": []}]}, League("eng.1", "Premier League"))


def test_filter_matches_applies_status_and_team(config):
    matches = [
        parse_event(event(), League("eng.1", "Premier League")),
        parse_event({**event(), "id": "match-2", "competitions": [{
            "competitors": [
                {"homeAway": "home", "team": {"displayName": "Liverpool"}, "score": ""},
                {"homeAway": "away", "team": {"displayName": "Arsenal"}, "score": ""},
            ],
            "status": {"type": {"state": "pre", "detail": "Scheduled"}},
        }]}, League("eng.1", "Premier League")),
    ]
    config = config.__class__(**{**config.__dict__, "statuses": ("post",), "teams": ("arsenal",)})
    assert [match.id for match in filter_matches(matches, config)] == ["match-1"]


def test_monitor_writes_report_and_tracks_new_then_updated_match(config):
    client = Mock()
    client.get_matches.return_value = [parse_event(event(), config.leagues[0])]
    monitor = MatchMonitor(config, client)
    report = monitor.run(today=date(2026, 10, 29))
    assert report["match_count"] == 1
    assert json.loads(Path(config.output_file).read_text(encoding="utf-8"))["matches"][0]["id"] == "match-1"
    first_change = json.loads(Path(config.change_log_file).read_text(encoding="utf-8").splitlines()[0])
    assert first_change["change"] == "new"

    client.get_matches.return_value = [parse_event(event("3"), config.leagues[0])]
    monitor.run(today=date(2026, 10, 29))
    changes = [json.loads(line) for line in Path(config.change_log_file).read_text(encoding="utf-8").splitlines()]
    assert [change["change"] for change in changes] == ["new", "updated"]


def test_monitor_can_refresh_only_the_selected_date(config):
    client = Mock()
    client.get_matches.return_value = [parse_event(event(), config.leagues[0])]
    report = MatchMonitor(config, client).run_for_date(date(2026, 10, 29))
    assert report["date_from"] == "2026-10-29"
    assert report["date_to"] == "2026-10-29"
    client.get_matches.assert_called_once_with(config.leagues[0], date(2026, 10, 29))


def test_monitor_refreshes_only_selected_leagues(config):
    selected_league = config.leagues[0]
    other_league = League("esp.1", "La Liga")
    client = Mock()
    client.get_matches.return_value = []
    selected_config = config.__class__(**{**config.__dict__, "leagues": (selected_league, other_league)})
    MatchMonitor(selected_config, client).run_for_date(date(2026, 10, 29), (other_league,))
    client.get_matches.assert_called_once_with(other_league, date(2026, 10, 29))


def test_repeated_match_does_not_add_duplicate_change(config):
    client = Mock()
    client.get_matches.return_value = [parse_event(event(), config.leagues[0])]
    monitor = MatchMonitor(config, client)
    monitor.run(today=date(2026, 10, 29))
    monitor.run(today=date(2026, 10, 29))
    assert len(Path(config.change_log_file).read_text(encoding="utf-8").splitlines()) == 1


def test_json_formatter_emits_structured_fields():
    import logging

    record = logging.LogRecord("test", logging.INFO, "", 0, "started", (), None)
    record.event = "run_started"
    formatted = json.loads(JSONFormatter().format(record))
    assert formatted["message"] == "started"
    assert formatted["event"] == "run_started"
