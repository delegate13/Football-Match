"""Command-line entry point for live monitoring and sample-report generation."""

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from football_match_monitor.config import load_config
from football_match_monitor.espn import ESPNClient, parse_event
from football_match_monitor.exceptions import MonitorError
from football_match_monitor.filters import filter_matches
from football_match_monitor.logging_config import configure_logging
from football_match_monitor.monitor import MatchMonitor


def main(argv: list[str] | None = None) -> int:
    """Run the command-line monitor and return a shell-friendly exit code."""
    # argparse handles --help and reports malformed command-line arguments.
    parser = argparse.ArgumentParser(description="Retrieve, filter, and report ESPN football matches.")
    parser.add_argument("--config", default="config.json", help="Path to the JSON configuration file.")
    parser.add_argument(
        "--sample",
        action="store_true",
        help="Generate a report from examples/sample_espn_response.json without calling ESPN.",
    )
    args = parser.parse_args(argv)
    try:
        # Load configuration before logging, since the log path comes from it.
        config = load_config(args.config)
        logger = configure_logging(config.automation_log_file)
        if args.sample:
            # Sample mode is useful for demonstrations without internet access.
            _generate_sample(config)
            logger.info("Sample report generated", extra={"event": "sample_generated"})
            print(f"Sample report written to {config.output_file}")
            return 0
        # The monitor owns filtering, reports, and change-history behavior.
        report = MatchMonitor(config, ESPNClient(config)).run()
        print(f"Report written to {config.output_file} ({report['match_count']} matches)")
        return 0
    except MonitorError as exc:
        logging.getLogger("football_match_monitor").exception(
            "Football Match Monitor failed", extra={"event": "run_failed"}
        )
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Error: Unable to write monitor output: {exc}", file=sys.stderr)
        return 1


def _generate_sample(config) -> None:
    """Create a report from the checked-in ESPN example without making a request."""
    sample_path = Path(__file__).resolve().parent.parent / "examples" / "sample_espn_response.json"
    try:
        payload = json.loads(sample_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MonitorError(f"Unable to load sample scoreboard '{sample_path}': {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise MonitorError(f"Sample scoreboard '{sample_path}' has an invalid structure.")
    # Label the example as Premier League when configured; otherwise use first league.
    league = next((league for league in config.leagues if league.id == "eng.1"), config.leagues[0])
    matches = [parse_event(event, league) for event in payload["events"]]
    matches = filter_matches(matches, config)
    today = date.today()
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date_from": today.isoformat(),
        "date_to": today.isoformat(),
        "match_count": len(matches),
        "matches": [match.to_dict() for match in matches],
    }
    # Create parent folders so first-time sample generation succeeds.
    output = Path(config.output_file)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
