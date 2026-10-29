# Football Match Monitor

A configurable Python automation that reads fixtures and results from ESPN's public soccer scoreboard endpoints, filters matches, writes a JSON report, records match changes, and emits structured operational logs. It uses only Python's standard library at runtime.

The project also includes **Touchline**, an original responsive football website with scores, team and player directories, recent performance summaries, competition/date/status filters, team search, favourites, and ESPN refresh.

## Requirements

- Python 3.10 or later
- Internet access for live ESPN data
- `pytest` to run the test suite

## Run

From the repository root:

```powershell
python main.py
```

Or install the project and run its command:

```powershell
python -m pip install -e .
football-match-monitor --config config.json
```

The default `config.json` includes 82 ESPN-verified soccer leagues and competitions across domestic leagues, cups, and international tournaments. ESPN does not publish a public index of all supported competitions, so this is a broad catalog of IDs confirmed against ESPN's scoreboard metadata, not a guarantee of every historical or regional competition. You can remove entries to monitor a smaller selection. The configured date window is one day ago through seven days ahead. Outputs are created relative to the config file's directory:

- `output/matches.json` — current filtered report for downstream systems
- `logs/changes.jsonl` — append-only records for new or changed matches
- `logs/automation.log` — structured JSON-lines operational log
- `data/previous_matches.json` — state used for change detection
- `data/espn_cache/` — full ESPN responses for scoreboards, team lists, rosters, schedules and match summaries, cached for offline use

Generate a sample report without contacting ESPN:

```powershell
python main.py --sample
```

This parses `examples/sample_espn_response.json` and writes the report to the configured output path. `--config path\to\config.json` selects another configuration.

## Website

Start the local dashboard from the project root:

```powershell
python -m football_match_monitor.web
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in a browser. Select a page from the navigation:

- **Scores** — choose competitions and dates to view fixtures, live scores and results; download the selected date and competitions as CSV or PDF.
- **Teams** — browse all teams listed by ESPN for a selected configured competition; choose a date to use as the performance cutoff when opening a team.
- **Players** — browse the latest ESPN roster snapshot, filtered by competition, team and search. Choose a date to set the performance cutoff; ESPN's roster endpoint does not provide historical rosters.
- **Performance** — choose any date to view a team's W-D-L record, goals for/against, recent five results and player match leaders through that date.

Team and player directory data and match performance responses are cached under `data/espn_cache/directory/` and use saved API responses when ESPN is unavailable. Team totals are calculated from ESPN's returned schedule. Player metrics are aggregated only from ESPN match-leader categories over the team's five most recent completed matches; they are not complete season player totals. The footer displays the current app version, sourced from `football_match_monitor/version.py`; increment that version for each web-project release.

In Scores, choose one or more competitions in the sidebar; it starts with none selected to avoid loading every league at once. Use **Select all** to include every configured competition, **Clear** to remove all selections, or search by league name or ESPN ID. Your selection is saved in this browser. Use the calendar picker to select any date or move day-by-day with the arrows. Select **Refresh scores** to refresh the selected date and competitions and update the report and change history. Generate a sample report first with `python main.py --sample` to explore Scores without contacting ESPN.

The website binds to `127.0.0.1` by default so it is only available on the local machine. Use `--host` and `--port` to choose another bind address and port; only expose it on a trusted network, as the dashboard has no user authentication. The command accepts `--config path\to\config.json` for a non-default configuration.

After installing the project, the same server is available as `football-match-monitor-web`.

## Configuration

Edit `config.json` to select leagues and filters; no code change is needed. ESPN league IDs are used in endpoint paths, such as `eng.1` (English Premier League), `esp.1` (La Liga), `usa.1` (MLS), and `uefa.champions` (UEFA Champions League).

| Key | Purpose |
| --- | --- |
| `api_base_url` | ESPN soccer API base URL |
| `leagues` | List of ESPN league `id` and human-readable `name` |
| `days_before`, `days_after` | Inclusive date window around the local run date (0–30 days each) |
| `statuses` | Include `pre` (scheduled), `in` (in progress), and/or `post` (completed) |
| `teams` | Optional case-insensitive substring filters; a match is included when either team matches any entry |
| `output_file` | JSON report path |
| `change_log_file` | Append-only JSONL change history path |
| `automation_log_file` | Structured JSONL operational log path |
| `state_file` | Local match snapshot used to detect changes between runs |
| `api_cache_dir` | Local directory for complete ESPN API response copies |
| `request_timeout_seconds` | Per-request timeout (1–120 seconds) |
| `max_concurrent_requests` | Maximum simultaneous ESPN scoreboard requests (1–16) |

Each league/date pair is requested separately from `https://site.api.espn.com/apis/site/v2/sports/soccer/{league_id}/scoreboard?dates=YYYYMMDD`. The complete successful API response is saved as JSON in `api_cache_dir`, with its league, match date, and fetch timestamp; cached files are replaced atomically. If ESPN is unreachable, times out, returns malformed JSON, or has a server error, the app uses that league/date's saved response when available. Client errors such as HTTP 404 are reported rather than silently replaced with stale data. If there is no saved copy, the request fails with a clear error. Cache fallback is logged, and the dashboard indicates when it is showing an offline copy. Cached responses are retained until overwritten; remove the cache directory manually to clear local copies. The cache is local runtime data, excluded from version control by the `data/` ignore rule.

Requests run concurrently, up to `max_concurrent_requests` (1–16; default 8), to keep the larger catalog practical while avoiding an unbounded request burst. The monitor does not require an API key. ESPN controls endpoint availability and response content.

## Report and change behavior

The report contains a UTC generation timestamp, inclusive date range, match count, and normalized match objects with league, kickoff, teams, scores, status, status detail, and venue. Unavailable scores and venues are `null`. Match changes are classified as `new` or `updated` and include the complete normalized match. Identical runs do not append duplicate change entries. The state file is local runtime state and should be retained between scheduled runs.

Errors such as invalid configuration, malformed API payloads, and network failures are reported to stderr and return a non-zero exit code. API failures are also written to the automation log.

## Tests

```powershell
python -m pip install -e ".[test]"
python -m pytest
```

The tests mock the ESPN client for monitor behavior and do not require network access. GitHub Actions runs the test suite and sample-report generation on pushes and pull requests.

## Workplace operation

Schedule `python main.py --config config.json` using the organization's task scheduler or workflow runner. Give the process write access to the configured output/log/state directories; archive or ship the JSON report and JSONL change log to downstream systems as required. Keep the state file persistent across runs so updates are compared correctly. Review `logs/automation.log` for run events and failures.
