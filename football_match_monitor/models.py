"""Small immutable data objects shared across the application layers."""

from dataclasses import asdict, dataclass
from typing import Optional


@dataclass(frozen=True)
class League:
    """The ESPN identifier and display name for one competition."""

    # ESPN uses this identifier in scoreboard and team-directory URLs.
    id: str
    # Human-readable name shown in reports and the website.
    name: str


@dataclass(frozen=True)
class Match:
    """One normalized fixture, independent of ESPN's nested JSON format."""

    # ESPN's event identifier is used to distinguish matches between runs.
    id: str
    # Keep both the league ID and name so code can filter and display it.
    league_id: str
    league_name: str
    # ESPN kickoff timestamp, normally an ISO-formatted date and time.
    date: str
    name: str
    # Teams are stored separately so reports can show home and away clearly.
    home_team: str
    away_team: str
    # A missing score means the fixture has not reported one yet.
    home_score: Optional[int]
    away_score: Optional[int]
    # ESPN status codes are pre (upcoming), in (live), or post (finished).
    status: str
    status_detail: str
    venue: Optional[str]

    def to_dict(self) -> dict:
        """Turn this dataclass into a plain dictionary for JSON output."""
        return asdict(self)


@dataclass(frozen=True)
class MonitorConfig:
    """Validated settings shared by the monitor, API clients, and website."""

    # ESPN's API root and the competitions the app is allowed to request.
    api_base_url: str
    leagues: tuple[League, ...]
    # Default report time window and business filters.
    days_before: int
    days_after: int
    statuses: tuple[str, ...]
    teams: tuple[str, ...]
    # Local outputs: report, change log, automation log, saved state, and cache.
    output_file: str
    change_log_file: str
    automation_log_file: str
    state_file: str
    api_cache_dir: str
    # Bound network time and the number of simultaneous ESPN requests.
    request_timeout_seconds: int
    max_concurrent_requests: int
