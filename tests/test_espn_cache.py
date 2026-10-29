import json
from datetime import date
from io import BytesIO
from pathlib import Path
from urllib.error import URLError

import pytest

from football_match_monitor.config import load_config
from football_match_monitor.cache import ScoreboardCache
from football_match_monitor.espn import ESPNClient
from football_match_monitor.exceptions import DataValidationError, ESPNAPIError
from football_match_monitor.models import League


MATCH_DATE = date(2026, 10, 29)
LEAGUE = League("eng.1", "Premier League")


@pytest.fixture
def config(tmp_path):
    root = Path(__file__).resolve().parents[1]
    raw = json.loads((root / "config.json").read_text(encoding="utf-8"))
    raw.update({
        "leagues": [{"id": LEAGUE.id, "name": LEAGUE.name}],
        "output_file": str(tmp_path / "report.json"),
        "change_log_file": str(tmp_path / "changes.jsonl"),
        "automation_log_file": str(tmp_path / "automation.log"),
        "state_file": str(tmp_path / "state.json"),
        "api_cache_dir": str(tmp_path / "cache"),
    })
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    return load_config(str(config_path))


def scoreboard():
    return {
        "leagues": [{"id": "700", "slug": "eng.1", "name": "English Premier League"}],
        "season": {"year": 2026},
        "events": [{
            "id": "match-1",
            "date": "2026-10-29T16:30Z",
            "name": "Liverpool vs Arsenal",
            "competitions": [{
                "competitors": [
                    {"homeAway": "home", "team": {"displayName": "Liverpool"}, "score": "2"},
                    {"homeAway": "away", "team": {"displayName": "Arsenal"}, "score": "1"},
                ],
                "status": {"type": {"state": "post", "detail": "Full Time"}},
                "venue": {"fullName": "Anfield"},
            }],
        }],
    }


def response(payload):
    return BytesIO(json.dumps(payload).encode("utf-8"))


def test_scoreboard_cache_preserves_full_response_and_metadata(tmp_path):
    cache = ScoreboardCache(str(tmp_path))
    payload = scoreboard()
    cache.save(LEAGUE, MATCH_DATE, payload)

    cache_file = tmp_path / "eng.1_2026-10-29.json"
    saved = json.loads(cache_file.read_text(encoding="utf-8"))
    assert saved["league_id"] == LEAGUE.id
    assert saved["date"] == MATCH_DATE.isoformat()
    assert saved["fetched_at"]
    assert saved["data"] == payload
    assert cache.load(LEAGUE, MATCH_DATE) == payload


def test_espn_client_caches_success_then_serves_cache_when_offline(config, monkeypatch):
    from football_match_monitor import espn

    monkeypatch.setattr(espn, "urlopen", lambda *args, **kwargs: response(scoreboard()))
    live_client = ESPNClient(config)
    live_matches = live_client.get_matches(LEAGUE, MATCH_DATE)
    assert live_matches[0].home_score == 2
    assert not live_client.used_cache

    def offline(*args, **kwargs):
        raise URLError("network unavailable")

    monkeypatch.setattr(espn, "urlopen", offline)
    offline_client = ESPNClient(config)
    cached_matches = offline_client.get_matches(LEAGUE, MATCH_DATE)
    assert cached_matches == live_matches
    assert offline_client.used_cache


def test_espn_client_without_network_or_cache_raises_clear_error(config, monkeypatch):
    from football_match_monitor import espn

    def offline(*args, **kwargs):
        raise URLError("network unavailable")

    monkeypatch.setattr(espn, "urlopen", offline)
    with pytest.raises(ESPNAPIError, match="no local copy is available"):
        ESPNClient(config).get_matches(LEAGUE, MATCH_DATE)


def test_corrupt_local_scoreboard_cache_fails_explicitly(config, monkeypatch):
    from football_match_monitor import espn

    cache_file = Path(config.api_cache_dir) / "eng.1_2026-10-29.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text("invalid json", encoding="utf-8")

    def offline(*args, **kwargs):
        raise URLError("network unavailable")

    monkeypatch.setattr(espn, "urlopen", offline)
    with pytest.raises(ESPNAPIError, match="local cache is unusable"):
        ESPNClient(config).get_matches(LEAGUE, MATCH_DATE)
