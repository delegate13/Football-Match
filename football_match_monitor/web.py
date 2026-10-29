"""Local HTTP server that powers the football dashboard and its JSON APIs."""

import argparse
import json
import logging
import mimetypes
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from football_match_monitor import __version__
from football_match_monitor.config import load_config
from football_match_monitor.directory import ESPNDirectoryClient, build_team_performance
from football_match_monitor.espn import ESPNClient
from football_match_monitor.exceptions import MonitorError
from football_match_monitor.filters import filter_matches
from football_match_monitor.logging_config import configure_logging
from football_match_monitor.monitor import MatchMonitor
from football_match_monitor.models import League
from football_match_monitor.report_export import report_csv, report_pdf

_WEB_DIR = Path(__file__).resolve().parent / "web_assets"


def read_report(config) -> dict:
    """Read the saved JSON report, or return an empty report for a new install."""
    report_path = Path(config.output_file)
    if not report_path.exists():
        return {
            "generated_at": None,
            "date_from": None,
            "date_to": None,
            "match_count": 0,
            "matches": [],
        }
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MonitorError(f"Unable to read match report '{report_path}': {exc}") from exc
    if (
        not isinstance(report, dict)
        or not isinstance(report.get("matches"), list)
        or any(not isinstance(match, dict) for match in report["matches"])
    ):
        raise MonitorError(f"Match report '{report_path}' has an invalid structure.")
    return report


def parse_requested_date(query: str) -> date | None:
    """Parse the optional YYYY-MM-DD query value and reject invalid dates."""
    value = parse_qs(query).get("date", [None])[0]
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Date must use the YYYY-MM-DD format.") from exc
    if parsed.isoformat() != value:
        raise ValueError("Date must use the YYYY-MM-DD format.")
    return parsed


def parse_requested_leagues(query: str, config) -> tuple | None:
    """Resolve comma-separated league IDs against the configured league allowlist."""
    params = parse_qs(query, keep_blank_values=True)
    if "leagues" not in params:
        return None
    requested_ids = params["leagues"][0].split(",")
    if not requested_ids or any(not league_id for league_id in requested_ids):
        raise ValueError("Select one or more configured leagues.")
    # Never trust a browser-supplied league ID unless it exists in config.json.
    configured = {league.id: league for league in config.leagues}
    unknown = [league_id for league_id in requested_ids if league_id not in configured]
    if unknown:
        raise ValueError(f"Unknown league id: {unknown[0]}.")
    return tuple(configured[league_id] for league_id in dict.fromkeys(requested_ids))


def fetch_date_report(config, match_date: date, client=None, leagues=None) -> dict:
    """Fetch, filter, and serialize all selected leagues for one calendar day."""
    client = client or ESPNClient(config)
    leagues = config.leagues if leagues is None else leagues
    # Parallelize network requests, but cap the number of active connections.
    with ThreadPoolExecutor(max_workers=config.max_concurrent_requests) as executor:
        results = executor.map(
            lambda league: client.get_matches(league, match_date),
            leagues,
        )
        # A match ID is globally unique, so duplicates collapse to one report row.
        matches_by_id = {match.id: match for league_matches in results for match in league_matches}
    matches = filter_matches(list(matches_by_id.values()), config)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date_from": match_date.isoformat(),
        "date_to": match_date.isoformat(),
        "data_source": "local_cache" if getattr(client, "used_cache", False) is True else "live",
        "match_count": len(matches),
        "matches": [match.to_dict() for match in matches],
    }


def parse_requested_league(query: str, config) -> League:
    """Require one league query value and return its configured League object."""
    params = parse_qs(query, keep_blank_values=True)
    league_ids = params.get("league", [])
    if len(league_ids) != 1 or not league_ids[0]:
        raise ValueError("Select one configured league.")
    for league in config.leagues:
        if league.id == league_ids[0]:
            return league
    raise ValueError(f"Unknown league id: {league_ids[0]}.")


def create_handler(config):
    """Create an HTTP handler whose routes all use this validated configuration."""
    class DashboardHandler(BaseHTTPRequestHandler):
        """Answer browser requests using the supplied app configuration."""

        server_version = "FootballMatchMonitor/1.0"

        def do_GET(self):
            """Route read-only page and API requests to their response handlers."""
            route = urlparse(self.path).path
            if route in {"/api/report.csv", "/api/report.pdf"}:
                self._get_download(route, urlparse(self.path).query)
                return
            if route == "/api/matches":
                try:
                    query = urlparse(self.path).query
                    requested_date = parse_requested_date(query)
                    requested_leagues = parse_requested_leagues(query, config)
                    report = (
                        fetch_date_report(config, requested_date, leagues=requested_leagues)
                        if requested_date
                        else read_report(config)
                    )
                    self._send_json(report)
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, status=400)
                except MonitorError as exc:
                    logging.getLogger("football_match_monitor").exception(
                        "Dashboard report could not be loaded",
                        extra={"event": "dashboard_report_failed"},
                    )
                    self._send_json({"error": str(exc)}, status=500)
            elif route == "/api/leagues":
                self._send_json([{"id": league.id, "name": league.name} for league in config.leagues])
            elif route == "/api/teams":
                self._get_teams(urlparse(self.path).query)
            elif route == "/api/players":
                self._get_players(urlparse(self.path).query)
            elif route == "/api/performance":
                self._get_performance(urlparse(self.path).query)
            elif route == "/api/version":
                self._send_json({"version": __version__})
            elif route == "/" or route in {"/index.html", "/styles.css", "/app.js"}:
                self._send_asset(route)
            else:
                self._send_json({"error": "Not found"}, status=404)

        def do_POST(self):
            """Refresh and save a report for the requested date and leagues."""
            if urlparse(self.path).path != "/api/refresh":
                self._send_json({"error": "Not found"}, status=404)
                return
            try:
                query = urlparse(self.path).query
                requested_date = parse_requested_date(query)
                requested_leagues = parse_requested_leagues(query, config)
                monitor = MatchMonitor(config, ESPNClient(config))
                if requested_date and requested_leagues is not None:
                    report = monitor.run_for_date(requested_date, requested_leagues)
                elif requested_date:
                    report = monitor.run_for_date(requested_date)
                else:
                    report = monitor.run()
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            except (MonitorError, OSError) as exc:
                logging.getLogger("football_match_monitor").exception(
                    "Dashboard refresh failed",
                    extra={"event": "dashboard_refresh_failed"},
                )
                self._send_json({"error": str(exc)}, status=502)
                return
            self._send_json(report)

        def _get_download(self, route: str, query: str) -> None:
            """Generate a CSV or PDF download for the query's selected report."""
            try:
                requested_date = parse_requested_date(query)
                requested_leagues = parse_requested_leagues(query, config)
                report = (
                    fetch_date_report(config, requested_date, leagues=requested_leagues)
                    if requested_date
                    else read_report(config)
                )
                # The URL extension selects a format and its browser download type.
                if route.endswith(".csv"):
                    content = report_csv(report)
                    content_type = "text/csv; charset=utf-8"
                    extension = "csv"
                else:
                    content = report_pdf(report)
                    content_type = "application/pdf"
                    extension = "pdf"
                try:
                    report_date = date.fromisoformat(str(report.get("date_from", ""))).isoformat()
                except ValueError:
                    report_date = date.today().isoformat()
                filename = f"football-match-report-{report_date}.{extension}"
                # Content-Disposition tells the browser to save rather than display.
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Content-Length", str(len(content)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(content)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
            except MonitorError as exc:
                logging.getLogger("football_match_monitor").exception(
                    "Dashboard report download failed",
                    extra={"event": "dashboard_download_failed"},
                )
                self._send_json({"error": str(exc)}, status=500)

        def _get_teams(self, query: str) -> None:
            """Return teams for one configured league."""
            try:
                league = parse_requested_league(query, config)
                self._send_json(ESPNDirectoryClient(config).get_teams(league))
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
            except MonitorError as exc:
                logging.getLogger("football_match_monitor").exception(
                    "Team directory request failed",
                    extra={"event": "team_directory_failed"},
                )
                self._send_json({"error": str(exc)}, status=502)

        def _get_players(self, query: str) -> None:
            """Return roster entries for every team or one selected team."""
            try:
                league = parse_requested_league(query, config)
                params = parse_qs(query, keep_blank_values=True)
                team_ids = params.get("team", [])
                if len(team_ids) > 1:
                    raise ValueError("Select at most one team.")
                client = ESPNDirectoryClient(config)
                teams = client.get_teams(league)
                if team_ids and team_ids[0]:
                    selected_ids = {team["id"] for team in teams}
                    if team_ids[0] not in selected_ids:
                        raise ValueError("Team does not belong to the selected league.")
                    teams = [team for team in teams if team["id"] == team_ids[0]]
                # Fetch team rosters concurrently while respecting the request limit.
                with ThreadPoolExecutor(max_workers=config.max_concurrent_requests) as executor:
                    rosters = executor.map(lambda team: client.get_roster(league, team["id"]), teams)
                    players = [
                        {**player, "team_id": team["id"], "team_name": team["name"], "league_id": league.id}
                        for team, roster in zip(teams, rosters)
                        for player in roster
                    ]
                self._send_json(players)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
            except MonitorError as exc:
                logging.getLogger("football_match_monitor").exception(
                    "Player directory request failed",
                    extra={"event": "player_directory_failed"},
                )
                self._send_json({"error": str(exc)}, status=502)

        def _get_performance(self, query: str) -> None:
            """Validate a team selection and return its performance as of a date."""
            try:
                league = parse_requested_league(query, config)
                params = parse_qs(query, keep_blank_values=True)
                team_ids = params.get("team", [])
                if len(team_ids) != 1 or not team_ids[0]:
                    raise ValueError("Select a team to view performance.")
                client = ESPNDirectoryClient(config)
                teams = client.get_teams(league)
                team = next((item for item in teams if item["id"] == team_ids[0]), None)
                if team is None:
                    raise ValueError("Team does not belong to the selected league.")
                performance = build_team_performance(
                    client,
                    league,
                    team,
                    config.max_concurrent_requests,
                    as_of=parse_requested_date(query),
                )
                self._send_json(performance)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
            except MonitorError as exc:
                logging.getLogger("football_match_monitor").exception(
                    "Team performance request failed",
                    extra={"event": "team_performance_failed"},
                )
                self._send_json({"error": str(exc)}, status=502)

        def _send_asset(self, route: str) -> None:
            """Serve only known files from the packaged website asset directory."""
            asset_name = "index.html" if route == "/" else route.lstrip("/")
            asset = (_WEB_DIR / asset_name).resolve()
            # Resolve paths before serving to prevent traversing outside web_assets.
            if asset.parent != _WEB_DIR.resolve() or not asset.is_file():
                self._send_json({"error": "Not found"}, status=404)
                return
            content_type = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
            body = asset.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            # Restrict browser resource loading to this site and trusted image/font hosts.
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; img-src 'self' https://a.espncdn.com; "
                "script-src 'self'",
            )
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, payload: object, status: int = 200) -> None:
            """Serialize a response as UTF-8 JSON with conservative browser headers."""
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format_string: str, *args) -> None:
            """Send standard HTTP access messages through the app's JSON logger."""
            logging.getLogger("football_match_monitor").info(
                "Dashboard HTTP request: " + format_string % args,
                extra={"event": "dashboard_http"},
            )

    return DashboardHandler


def serve(config, host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the threaded local website until the user presses Ctrl+C."""
    server = ThreadingHTTPServer((host, port), create_handler(config))
    server.daemon_threads = True
    logging.getLogger("football_match_monitor").info(
        "Dashboard started",
        extra={"event": "dashboard_started", "host": host, "port": server.server_port},
    )
    print(f"Football Match Monitor is available at http://{host}:{server.server_port}")
    # Always close the listening socket on normal exit or keyboard interruption.
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard.")
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    """Parse website options, configure logging, and start the local server."""
    parser = argparse.ArgumentParser(description="Run the Football Match Monitor website.")
    parser.add_argument("--config", default="config.json", help="Path to the JSON configuration file.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (defaults to localhost only).")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000).")
    args = parser.parse_args(argv)
    # Port zero is allowed so the operating system can choose an available port.
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535.")
    try:
        config = load_config(args.config)
        configure_logging(config.automation_log_file)
        serve(config, args.host, args.port)
    except (MonitorError, OSError) as exc:
        print(f"Error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
