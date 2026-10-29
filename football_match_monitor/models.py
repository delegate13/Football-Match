from dataclasses import asdict, dataclass
from typing import Optional


@dataclass(frozen=True)
class League:
    id: str
    name: str


@dataclass(frozen=True)
class Match:
    id: str
    league_id: str
    league_name: str
    date: str
    name: str
    home_team: str
    away_team: str
    home_score: Optional[int]
    away_score: Optional[int]
    status: str
    status_detail: str
    venue: Optional[str]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class MonitorConfig:
    api_base_url: str
    leagues: tuple[League, ...]
    days_before: int
    days_after: int
    statuses: tuple[str, ...]
    teams: tuple[str, ...]
    output_file: str
    change_log_file: str
    automation_log_file: str
    state_file: str
    api_cache_dir: str
    request_timeout_seconds: int
    max_concurrent_requests: int
