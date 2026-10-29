"""Business rules for deciding which normalized matches belong in a report."""

from football_match_monitor.models import Match, MonitorConfig


def filter_matches(matches: list[Match], config: MonitorConfig) -> list[Match]:
    """Apply configured status/team rules, then sort matches for predictable reports."""
    selected = []
    # casefold() compares names without treating uppercase/lowercase differently.
    teams = tuple(team.casefold() for team in config.teams)
    for match in matches:
        # Ignore statuses the user did not enable in config.json.
        if match.status not in config.statuses:
            continue
        # If team filters exist, at least one must appear in either team name.
        if teams and not any(team in match.home_team.casefold() or team in match.away_team.casefold()
                             for team in teams):
            continue
        selected.append(match)
    # Stable ordering makes reports and change comparisons easier to read.
    return sorted(selected, key=lambda match: (match.date, match.league_name.casefold(), match.name.casefold()))
