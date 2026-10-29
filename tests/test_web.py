"""Tests for website query validation, report exports, and HTTP API routes."""

import json
import threading
from datetime import date
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import Mock

import pytest

from football_match_monitor.exceptions import MonitorError
from football_match_monitor.models import League
from football_match_monitor.report_export import report_csv, report_pdf
from football_match_monitor.web import (
    create_handler,
    fetch_date_report,
    parse_requested_date,
    parse_requested_league,
    parse_requested_leagues,
    read_report,
)


@pytest.fixture
def dashboard_config(tmp_path):
    """Create the minimal valid settings needed by the local HTTP server tests."""
    return SimpleNamespace(
        output_file=str(tmp_path / "report.json"),
        leagues=(League("eng.1", "Premier League"),),
        max_concurrent_requests=2,
        statuses=("pre", "in", "post"),
        teams=(),
    )


def test_read_report_returns_empty_dashboard_state_when_no_report_exists(tmp_path):
    """A first-time website visit gets an empty report instead of a file error."""
    report = read_report(SimpleNamespace(output_file=str(tmp_path / "missing.json")))
    assert report["match_count"] == 0
    assert report["matches"] == []


@pytest.mark.parametrize(
    "content",
    ["not json", json.dumps({"matches": "not a list"}), json.dumps({"matches": [None]})],
)
def test_read_report_rejects_invalid_report(tmp_path, content):
    """Malformed JSON and non-match data are reported as invalid saved reports."""
    report_path = tmp_path / "report.json"
    report_path.write_text(content, encoding="utf-8")
    with pytest.raises(MonitorError, match="report"):
        read_report(SimpleNamespace(output_file=str(report_path)))


def test_parse_requested_date_accepts_iso_date():
    """The API accepts one strict ISO date and treats an omitted date as optional."""
    assert parse_requested_date("date=2026-10-29") == date(2026, 10, 29)
    assert parse_requested_date("") is None


@pytest.mark.parametrize("query", ["date=29-10-2026", "date=2026-02-30", "date=2026-10-29T00%3A00"])
def test_parse_requested_date_rejects_invalid_date(query):
    """Dates with another format, impossible days, or time suffixes are rejected."""
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        parse_requested_date(query)


def test_parse_requested_leagues_accepts_selection_and_deduplicates(dashboard_config):
    """Configured league selections are returned once, in the requested order."""
    assert parse_requested_leagues("leagues=eng.1,eng.1", dashboard_config) == dashboard_config.leagues
    assert parse_requested_leagues("", dashboard_config) is None


@pytest.mark.parametrize("query", ["leagues=", "leagues=unknown.1"])
def test_parse_requested_leagues_rejects_invalid_selection(dashboard_config, query):
    """Blank or unconfigured league IDs cannot be used for scoreboard requests."""
    with pytest.raises(ValueError, match="league"):
        parse_requested_leagues(query, dashboard_config)


def test_parse_requested_league_validates_configured_id(dashboard_config):
    """Single-league endpoints also enforce the configured competition allowlist."""
    assert parse_requested_league("league=eng.1", dashboard_config) == dashboard_config.leagues[0]
    with pytest.raises(ValueError, match="Unknown league"):
        parse_requested_league("league=unknown.1", dashboard_config)


def test_report_exporters_include_match_data_and_create_valid_pdf():
    """CSV includes match columns and PDF contains valid offsets and escaped text."""
    report = {
        "generated_at": "2026-10-29T12:00:00+00:00",
        "date_from": "2026-10-29",
        "matches": [{
            "id": "match-1",
            "date": "2026-10-29T16:30Z",
            "league_name": "Premier League",
            "home_team": "A (FC)",
            "home_score": 2,
            "away_team": "Town",
            "away_score": 1,
            "status": "post",
            "status_detail": "Full Time",
            "venue": "Main Ground",
        }],
    }

    csv_report = report_csv(report).decode("utf-8-sig")
    assert "Premier League" in csv_report
    assert "A (FC),2,Town,1" in csv_report
    pdf_report = report_pdf(report)
    assert pdf_report.startswith(b"%PDF-1.4")
    assert b"A \\(FC\\)" in pdf_report
    xref_offset = int(pdf_report.split(b"startxref\n", 1)[1].splitlines()[0])
    assert pdf_report[xref_offset:xref_offset + 4] == b"xref"
    assert pdf_report.endswith(b"%%EOF\n")


def test_fetch_date_report_queries_configured_leagues_for_selected_date(dashboard_config):
    """A date report requests the configured leagues and labels its date range."""
    client = Mock()
    client.get_matches.return_value = []
    report = fetch_date_report(dashboard_config, date(2001, 1, 1), client)
    assert report["date_from"] == "2001-01-01"
    assert report["date_to"] == "2001-01-01"
    assert report["matches"] == []
    client.get_matches.assert_called_once_with(dashboard_config.leagues[0], date(2001, 1, 1))


def test_fetch_date_report_only_requests_selected_leagues(dashboard_config):
    """A browser league selection limits API calls to those selected leagues."""
    selected = League("eng.1", "Premier League")
    not_selected = League("esp.1", "La Liga")
    dashboard_config.leagues = (selected, not_selected)
    client = Mock()
    client.get_matches.return_value = []

    fetch_date_report(dashboard_config, date(2001, 1, 1), client, (not_selected,))

    client.get_matches.assert_called_once_with(not_selected, date(2001, 1, 1))


def test_dashboard_serves_page_and_configured_data(dashboard_config, tmp_path, monkeypatch):
    """Exercise the website page and API endpoints through a real local HTTP server."""
    import football_match_monitor.web as web

    report = {
        "generated_at": "2026-10-29T12:00:00+00:00",
        "date_from": "2026-10-29",
        "date_to": "2026-10-29",
        "match_count": 0,
        "matches": [],
    }
    daily_report = {**report, "date_from": "2001-01-01", "date_to": "2001-01-01"}
    team = {"id": "349", "name": "Bournemouth", "abbreviation": "BOU", "logo": None}

    class FakeDirectoryClient:
        """Return predictable directory responses without contacting ESPN."""

        def __init__(self, config):
            """Match the real client constructor; test routes use no settings."""
            pass

        def get_teams(self, league):
            """Return the one team used by directory and performance assertions."""
            return [team]

        def get_roster(self, league, team_id):
            """Return one sample player for the roster API response."""
            return [{
                "id": "123",
                "name": "Player One",
                "position": "Forward",
                "jersey": "9",
                "age": 24,
                "nationality": "England",
                "status": "Active",
                "headshot": None,
            }]

        def get_team_schedule(self, league, team_id):
            """Return no fixtures so the fake performance summary is empty."""
            return []

        def get_match_summary(self, league, event_id):
            """Return an empty summary because the fake schedule has no events."""
            return {}

    # Replace network-backed providers so this test is repeatable and offline.
    monkeypatch.setattr(
        web,
        "fetch_date_report",
        lambda config, requested_date, leagues=None: daily_report,
    )
    monkeypatch.setattr(web, "ESPNDirectoryClient", FakeDirectoryClient)
    (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
    dashboard_config.output_file = str(tmp_path / "report.json")
    # Port zero asks Windows to select an unused local port for this test server.
    server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(dashboard_config))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        # Check the HTML shell and key UI features before exercising JSON routes.
        with urlopen(f"{base_url}/") as response:
            assert response.status == 200
            page = response.read().decode("utf-8")
            assert "Football," in page
            assert "in focus." in page
            assert 'data-view="teams"' in page
            assert 'id="download-csv"' in page
            assert 'id="download-pdf"' in page
            assert 'id="app-version"' in page
            assert response.headers["Content-Security-Policy"]
        # The report endpoint supports both saved and requested-day reports.
        with urlopen(f"{base_url}/api/matches") as response:
            assert json.loads(response.read()) == report
        with urlopen(f"{base_url}/api/matches?date=2001-01-01&leagues=eng.1") as response:
            assert json.loads(response.read()) == daily_report
        # Download endpoints return correctly labeled files for the selected date.
        with urlopen(f"{base_url}/api/report.csv?date=2001-01-01&leagues=eng.1") as response:
            assert response.headers["Content-Type"].startswith("text/csv")
            assert response.headers["Content-Disposition"].endswith("football-match-report-2001-01-01.csv\"")
            csv_report = response.read().decode("utf-8-sig")
            assert "Date,Competition,Home team,Home score,Away team,Away score" in csv_report
        with urlopen(f"{base_url}/api/report.pdf?date=2001-01-01&leagues=eng.1") as response:
            assert response.headers["Content-Type"] == "application/pdf"
            assert response.headers["Content-Disposition"].endswith("football-match-report-2001-01-01.pdf\"")
            assert response.read().startswith(b"%PDF-1.4")
        with pytest.raises(HTTPError) as error:
            urlopen(f"{base_url}/api/report.csv?date=not-a-date&leagues=eng.1")
        assert error.value.code == 400
        with pytest.raises(HTTPError) as error:
            urlopen(f"{base_url}/api/matches?date=not-a-date")
        assert error.value.code == 400
        with pytest.raises(HTTPError) as error:
            urlopen(f"{base_url}/api/matches?date=2001-01-01&leagues=unknown.1")
        assert error.value.code == 400
        # Directory and performance routes validate and return expected data.
        with urlopen(f"{base_url}/api/leagues") as response:
            assert json.loads(response.read()) == [{"id": "eng.1", "name": "Premier League"}]
        with urlopen(f"{base_url}/api/version") as response:
            assert json.loads(response.read())["version"] == "1.2.3"
        with urlopen(f"{base_url}/api/teams?league=eng.1") as response:
            assert json.loads(response.read()) == [team]
        with urlopen(f"{base_url}/api/players?league=eng.1") as response:
            players = json.loads(response.read())
            assert players[0]["team_name"] == "Bournemouth"
            assert players[0]["name"] == "Player One"
        with urlopen(f"{base_url}/api/performance?league=eng.1&team=349") as response:
            performance = json.loads(response.read())
            assert performance["team"]["name"] == "Bournemouth"
            assert performance["matches_played"] == 0
        with urlopen(f"{base_url}/api/performance?league=eng.1&team=349&date=2001-01-01") as response:
            assert json.loads(response.read())["as_of"] == "2001-01-01"
        with pytest.raises(HTTPError) as error:
            urlopen(f"{base_url}/api/performance?league=eng.1&team=349&date=not-a-date")
        assert error.value.code == 400
        with pytest.raises(HTTPError) as error:
            urlopen(f"{base_url}/api/teams")
        assert error.value.code == 400
        with pytest.raises(HTTPError) as error:
            urlopen(f"{base_url}/api/players?league=eng.1&team=999")
        assert error.value.code == 400
        with pytest.raises(HTTPError) as error:
            urlopen(f"{base_url}/not-found")
        assert error.value.code == 404
    finally:
        # Stop the test server even if an assertion fails.
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
