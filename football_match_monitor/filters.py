from football_match_monitor.models import Match, MonitorConfig


def filter_matches(matches: list[Match], config: MonitorConfig) -> list[Match]:
    selected = []
    teams = tuple(team.casefold() for team in config.teams)
    for match in matches:
        if match.status not in config.statuses:
            continue
        if teams and not any(team in match.home_team.casefold() or team in match.away_team.casefold()
                             for team in teams):
            continue
        selected.append(match)
    return sorted(selected, key=lambda match: (match.date, match.league_name.casefold(), match.name.casefold()))
