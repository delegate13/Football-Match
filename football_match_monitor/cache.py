import json
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from football_match_monitor.exceptions import DataValidationError
from football_match_monitor.models import League


class ScoreboardCache:
    """Persists complete ESPN scoreboard responses by league and match date."""

    def __init__(self, directory: str):
        self._directory = Path(directory)

    def load(self, league: League, match_date: date) -> Optional[dict]:
        path = self._path(league, match_date)
        if not path.exists():
            return None
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DataValidationError(f"Unable to read ESPN cache file '{path}': {exc}") from exc
        if (
            not isinstance(cached, dict)
            or cached.get("league_id") != league.id
            or cached.get("date") != match_date.isoformat()
            or not isinstance(cached.get("data"), dict)
            or not isinstance(cached["data"].get("events"), list)
        ):
            raise DataValidationError(f"ESPN cache file '{path}' has an invalid structure.")
        return cached["data"]

    def save(self, league: League, match_date: date, payload: dict) -> None:
        path = self._path(league, match_date)
        path.parent.mkdir(parents=True, exist_ok=True)
        envelope = {
            "league_id": league.id,
            "league_name": league.name,
            "date": match_date.isoformat(),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "data": payload,
        }
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f"{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                json.dump(envelope, temporary_file, ensure_ascii=False)
                temporary_file.write("\n")
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def _path(self, league: League, match_date: date) -> Path:
        return self._directory / f"{league.id}_{match_date.isoformat()}.json"
