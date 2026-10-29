import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from football_match_monitor.exceptions import DataValidationError
from football_match_monitor.filters import filter_matches
from football_match_monitor.models import Match, MonitorConfig

logger = logging.getLogger("football_match_monitor")


class MatchMonitor:
    def __init__(self, config: MonitorConfig, client):
        self._config = config
        self._client = client

    def run(self, today: date | None = None) -> dict:
        today = today or date.today()
        start = today - timedelta(days=self._config.days_before)
        end = today + timedelta(days=self._config.days_after)
        return self._run(start, end)

    def run_for_date(self, match_date: date, leagues=None) -> dict:
        return self._run(match_date, match_date, leagues)

    def _run(self, start: date, end: date, leagues=None) -> dict:
        leagues = self._config.leagues if leagues is None else leagues
        logger.info("Match monitor started", extra={"event": "run_started"})
        requests = []
        current_date = start
        while current_date <= end:
            requests.extend((league, current_date) for league in leagues)
            current_date += timedelta(days=1)
        with ThreadPoolExecutor(max_workers=self._config.max_concurrent_requests) as executor:
            results = executor.map(lambda item: self._client.get_matches(*item), requests)
            matches_by_id = {match.id: match for day_matches in results for match in day_matches}
        matches = filter_matches(list(matches_by_id.values()), self._config)
        generated_at = datetime.now(timezone.utc).isoformat()
        report = {
            "generated_at": generated_at,
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
            "data_source": "local_cache" if getattr(self._client, "used_cache", False) is True else "live",
            "match_count": len(matches),
            "matches": [match.to_dict() for match in matches],
        }
        self._write_report(report)
        self._record_changes(matches)
        logger.info(
            "Match monitor completed",
            extra={
                "event": "run_completed",
                "match_count": len(matches),
                "data_source": report["data_source"],
            },
        )
        return report

    def _write_report(self, report: dict) -> None:
        path = Path(self._config.output_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def _record_changes(self, matches: list[Match]) -> None:
        state_path = Path(self._config.state_file)
        try:
            previous = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        except (OSError, json.JSONDecodeError) as exc:
            raise DataValidationError(f"Unable to read match state file '{state_path}': {exc}") from exc
        if not isinstance(previous, dict):
            raise DataValidationError(f"Match state file '{state_path}' must contain a JSON object.")

        current = {match.id: match.to_dict() for match in matches}
        changes = []
        for match_id, match_data in current.items():
            old_data = previous.get(match_id)
            if old_data is None:
                change_type = "new"
            elif old_data != match_data:
                change_type = "updated"
            else:
                continue
            changes.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "change": change_type,
                "match_id": match_id,
                "match": match_data,
            })

        if changes:
            change_path = Path(self._config.change_log_file)
            change_path.parent.mkdir(parents=True, exist_ok=True)
            with change_path.open("a", encoding="utf-8") as file:
                for change in changes:
                    file.write(json.dumps(change, ensure_ascii=False) + "\n")
                    logger.info(
                        "Match change recorded",
                        extra={"event": "match_change", "match_id": change["match_id"], "change": change["change"]},
                    )

        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
