// This file powers the website: it remembers choices, calls our API, and updates the page.
(() => {
  // A short helper makes it easier to find one element by its CSS selector.
  const $ = (selector) => document.querySelector(selector);
  // Keep the current user choices and loaded data together in one place.
  const state = {
    report: null,
    leagues: [],
    selectedLeagueIds: new Set(JSON.parse(localStorage.getItem("touchline-leagues") || "[]")),
    selectedDate: "",
    status: "all",
    query: "",
    favorites: new Set(JSON.parse(localStorage.getItem("touchline-favorites") || "[]")),
    teams: [],
    players: [],
    playerQuery: "",
  };
  let toastTimer;
  // Ignore older scoreboard responses if a newer date request finishes first.
  let dateRequestId = 0;

  // Convert a Date object into the YYYY-MM-DD format used by date inputs and APIs.
  function dateKey(date) {
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  }

  // Treat all API text as text, never as executable HTML inserted into the page.
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);

  // Build two-letter team initials for badges when no official logo is available.
  function initials(name) {
    return name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
  }

  // Convert ESPN's timestamp to a local calendar date for date filtering.
  function localDateKey(value) {
    if (!value) return "";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value).slice(0, 10);
    return `${parsed.getFullYear()}-${String(parsed.getMonth() + 1).padStart(2, "0")}-${String(parsed.getDate()).padStart(2, "0")}`;
  }

  // Display a date in a friendly style such as "Thu 29 Oct".
  function formatDate(date) {
    return new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short" }).format(date);
  }

  // Parse an ISO date at midday to avoid daylight-saving midnight edge cases.
  function dateFromKey(value) {
    const [year, month, day] = value.split("-").map(Number);
    return new Date(year, month - 1, day, 12);
  }

  // Start the whole website on today's date and synchronize all date controls.
  function initDates() {
    const today = new Date();
    today.setHours(12, 0, 0, 0);
    state.selectedDate = dateKey(today);
    renderDates();
  }

  // Draw the seven nearby date buttons and keep every date input in sync.
  function renderDates() {
    const selected = dateFromKey(state.selectedDate);
    const dates = [];
    for (let offset = -3; offset <= 3; offset += 1) {
      const current = new Date(selected);
      current.setDate(selected.getDate() + offset);
      dates.push(current);
    }
    $("#date-list").innerHTML = dates.map((item) => {
      const key = dateKey(item);
      const label = key === dateKey(new Date()) ? "Today"
        : new Intl.DateTimeFormat("en-GB", { weekday: "short" }).format(item);
      return `<button type="button" role="tab" aria-selected="${key === state.selectedDate}" class="date-button ${key === state.selectedDate ? "selected" : ""}" data-date="${key}"><span>${label}</span><span>${item.getDate()}</span></button>`;
    }).join("");
    $("#date-picker").value = state.selectedDate;
    ["#teams-date-picker", "#players-date-picker", "#performance-date-picker"]
      .forEach((selector) => { $(selector).value = state.selectedDate; });
    updateDownloadLinks();
    $("#today-date").textContent = formatDate(selected);
    $("#date-list").querySelectorAll("[data-date]").forEach((button) => {
      button.addEventListener("click", () => selectDate(button.dataset.date));
    });
  }

  // Point the download buttons at the currently selected date and competitions.
  function updateDownloadLinks() {
    const enabled = state.selectedLeagueIds.size > 0;
    const params = new URLSearchParams({
      date: state.selectedDate,
      leagues: [...state.selectedLeagueIds].join(","),
    });
    [["#download-csv", "csv"], ["#download-pdf", "pdf"]].forEach(([selector, format]) => {
      const link = $(selector);
      link.href = enabled ? `/api/report.${format}?${params}` : "#";
      link.setAttribute("aria-disabled", String(!enabled));
      if (enabled) link.removeAttribute("tabindex");
      else link.setAttribute("tabindex", "-1");
    });
  }

  // Show one page section and run its data-loading function when needed.
  function showView(name) {
    document.querySelectorAll(".view-panel").forEach((panel) => {
      panel.hidden = panel.id !== `${name}-view`;
    });
    document.querySelectorAll("[data-view]").forEach((button) => {
      button.classList.toggle("active", button.dataset.view === name);
    });
    if (name === "teams") loadTeams();
    if (name === "players") loadPlayers();
    if (name === "performance") loadPerformanceTeams();
  }

  // Fill a competition dropdown and optionally restore a preferred selection.
  function leagueOptions(select, preferredId = "") {
    select.innerHTML = state.leagues.map((league) =>
      `<option value="${escapeHtml(league.id)}">${escapeHtml(league.name)}</option>`
    ).join("");
    if (preferredId && state.leagues.some((league) => league.id === preferredId)) {
      select.value = preferredId;
    }
  }

  // Look up the selected dropdown value in the known competition list.
  function currentLeague(select) {
    return state.leagues.find((league) => league.id === select.value);
  }

  // Fetch JSON and turn server error responses into readable JavaScript errors.
  async function getJson(url) {
    const response = await fetch(url);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Request failed.");
    return result;
  }

  // Render either the official team image or a text badge as a fallback.
  function teamLogo(team) {
    const badge = escapeHtml((team.abbreviation || team.name || "?").slice(0, 3).toUpperCase());
    return team.logo
      ? `<span class="team-logo"><img src="${escapeHtml(team.logo)}" alt="" loading="lazy"></span>`
      : `<span class="team-logo">${badge}</span>`;
  }

  // Show a consistent error message and wire its retry button to the active view.
  function showDirectoryError(container, error) {
    container.innerHTML = `<div class="error-state"><strong>Could not load data</strong><span>${escapeHtml(error.message)}</span><button type="button" class="retry-directory">Try again</button></div>`;
    container.querySelector(".retry-directory").addEventListener("click", () => {
      if (!$("#teams-view").hidden) loadTeams();
      else if (!$("#players-view").hidden) loadPlayers();
      else loadPerformance();
    });
  }

  // Load all teams for the selected league and let a team open its performance.
  async function loadTeams() {
    const select = $("#teams-league");
    if (!select.value) leagueOptions(select, [...state.selectedLeagueIds][0]);
    const league = currentLeague(select);
    if (!league) return;
    const container = $("#teams-content");
    container.innerHTML = '<div class="loading-card"><span class="spinner"></span> Loading teams...</div>';
    try {
      state.teams = await getJson(`/api/teams?league=${encodeURIComponent(league.id)}`);
      container.innerHTML = state.teams.map((team) => `<button class="team-card" type="button" data-team="${escapeHtml(team.id)}">
        ${teamLogo(team)}<span class="team-card-copy"><strong>${escapeHtml(team.name)}</strong><span>${escapeHtml(team.abbreviation || league.name)}</span></span>
      </button>`).join("") || '<div class="empty-state"><strong>No teams listed</strong><span>ESPN has no team directory data for this competition.</span></div>';
      container.querySelectorAll("[data-team]").forEach((button) => {
        button.addEventListener("click", () => {
          leagueOptions($("#performance-league"), league.id);
          $("#performance-team").dataset.pendingTeam = button.dataset.team;
          showView("performance");
        });
      });
    } catch (error) {
      showDirectoryError(container, error);
    }
  }

  // Filter the already-loaded squad and draw each remaining player card.
  function renderPlayers() {
    const query = state.playerQuery.trim().toLocaleLowerCase();
    const players = state.players.filter((player) =>
      `${player.name} ${player.position} ${player.team_name} ${player.jersey}`.toLocaleLowerCase().includes(query)
    );
    $("#players-content").innerHTML = players.map((player) => `<article class="player-card">
      <span class="player-avatar">${escapeHtml(initials(player.name))}</span>
      <span class="player-copy"><strong>${escapeHtml(player.name)}</strong><span>${escapeHtml(player.position || "Player")} · ${escapeHtml(player.team_name)}</span></span>
      <span class="player-meta">${player.jersey ? `#${escapeHtml(player.jersey)}` : ""}${player.age ? `<br>${escapeHtml(player.age)} yrs` : ""}</span>
    </article>`).join("") || `<div class="empty-state"><strong>No players found</strong><span>Try a different search or team.</span></div>`;
  }

  // Load team choices first, then request rosters for the chosen league/team.
  async function loadPlayers() {
    const leagueSelect = $("#players-league");
    if (!leagueSelect.value) leagueOptions(leagueSelect, [...state.selectedLeagueIds][0]);
    const league = currentLeague(leagueSelect);
    if (!league) return;
    const container = $("#players-content");
    container.innerHTML = '<div class="loading-card"><span class="spinner"></span> Loading league squads...</div>';
    try {
      const teams = await getJson(`/api/teams?league=${encodeURIComponent(league.id)}`);
      const teamSelect = $("#players-team");
      const previousTeam = teamSelect.value;
      teamSelect.innerHTML = '<option value="">All teams</option>' + teams.map((team) =>
        `<option value="${escapeHtml(team.id)}">${escapeHtml(team.name)}</option>`
      ).join("");
      if (teams.some((team) => team.id === previousTeam)) teamSelect.value = previousTeam;
      const params = new URLSearchParams({ league: league.id });
      if (teamSelect.value) params.set("team", teamSelect.value);
      state.players = await getJson(`/api/players?${params}`);
      renderPlayers();
    } catch (error) {
      showDirectoryError(container, error);
    }
  }

  // Populate the team selector before a user can request team performance.
  async function loadPerformanceTeams() {
    const leagueSelect = $("#performance-league");
    if (!leagueSelect.value) leagueOptions(leagueSelect, [...state.selectedLeagueIds][0]);
    const league = currentLeague(leagueSelect);
    if (!league) return;
    const teamSelect = $("#performance-team");
    const previousTeam = teamSelect.dataset.pendingTeam || teamSelect.value;
    teamSelect.innerHTML = '<option value="">Loading teams...</option>';
    try {
      state.teams = await getJson(`/api/teams?league=${encodeURIComponent(league.id)}`);
      teamSelect.innerHTML = '<option value="">Select a team</option>' + state.teams.map((team) =>
        `<option value="${escapeHtml(team.id)}">${escapeHtml(team.name)}</option>`
      ).join("");
      if (state.teams.some((team) => team.id === previousTeam)) {
        teamSelect.value = previousTeam;
        delete teamSelect.dataset.pendingTeam;
        loadPerformance();
      } else {
        $("#performance-content").innerHTML = `<div class="empty-state"><span class="empty-icon">↗</span><strong>Choose a team</strong><span>Select a team to see its record, recent form and player match leaders.</span></div>`;
      }
    } catch (error) {
      showDirectoryError($("#performance-content"), error);
    }
  }

  // Turn performance JSON into stat cards, recent results, and leader rows.
  function renderPerformance(data) {
    const team = data.team;
    const form = data.recent_form || [];
    const leaders = data.player_performance || [];
    const stats = [
      ["Played", data.matches_played],
      ["Record (W-D-L)", data.season_record],
      ["Goals for", data.goals_for],
      ["Goals against", data.goals_against],
      ["Goal difference", data.goal_difference > 0 ? `+${data.goal_difference}` : data.goal_difference],
    ];
    const formRows = form.map((match) => `<div class="form-row">
      <span class="form-result ${match.result === "W" ? "win" : match.result === "D" ? "draw" : "loss"}">${escapeHtml(match.result)}</span>
      <span class="form-opponent">${escapeHtml(match.opponent)} (${match.home_away === "home" ? "H" : "A"})</span>
      <span class="form-score">${escapeHtml(match.goals_for)}–${escapeHtml(match.goals_against)}</span>
      <span class="form-date">${escapeHtml(match.date.slice(0, 10))}</span>
    </div>`).join("") || '<p class="panel-note">No completed matches are available from ESPN for this team.</p>';
    const statNames = {
      goals: "Goals", goalAssists: "Assists", totalShots: "Shots",
      shotsOnTarget: "On target", saves: "Saves", yellowCards: "Yellow",
      redCards: "Red cards",
    };
    const leaderRows = leaders.map((player) => {
      const badges = Object.entries(player.stats).map(([name, value]) =>
        `<span class="leader-stat">${escapeHtml(statNames[name] || name)} ${escapeHtml(value)}</span>`
      ).join("");
      return `<div class="leader-row"><strong>${escapeHtml(player.name)}</strong><span class="leader-stats">${badges}</span></div>`;
    }).join("") || '<p class="panel-note">ESPN has no player leaders for the recent completed matches.</p>';
    $("#performance-content").innerHTML = `
      <div class="performance-team">${teamLogo(team)}<div><h3>${escapeHtml(team.name)}</h3><p>${escapeHtml(data.league)} · ${data.as_of ? `Season summary through ${escapeHtml(data.as_of)}` : "Season summary from ESPN fixtures"}</p></div></div>
      <div class="stat-grid">${stats.map(([label, value]) => `<div class="stat-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value ?? "—")}</strong></div>`).join("")}</div>
      <div class="performance-columns">
        <section class="performance-panel"><h3>Recent form · last ${form.length} completed</h3><div class="form-list">${formRows}</div></section>
        <section class="performance-panel"><h3>Player match leaders · last 5</h3><div class="leader-list">${leaderRows}</div><p class="panel-note">Player metrics are summed from ESPN match leader categories and are not full season player totals.</p></section>
      </div>`;
  }

  // Request the selected team's results through the current date.
  async function loadPerformance() {
    const league = currentLeague($("#performance-league"));
    const teamId = $("#performance-team").value;
    if (!league || !teamId) return;
    const container = $("#performance-content");
    container.innerHTML = '<div class="loading-card"><span class="spinner"></span> Calculating recent team and player performance...</div>';
    try {
      const params = new URLSearchParams({ league: league.id, team: teamId });
      params.set("date", state.selectedDate);
      renderPerformance(await getJson(`/api/performance?${params}`));
    } catch (error) {
      showDirectoryError(container, error);
    }
  }

  // Validate a date input, update shared state, and refresh the visible view.
  function selectDate(value) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || dateKey(dateFromKey(value)) !== value) return;
    state.selectedDate = value;
    renderDates();
    if (!$("#scores-view").hidden) loadSelectedDate();
    else if (!$("#performance-view").hidden) loadPerformance();
  }

  // Search and render the competition selector, retaining choices in this browser.
  function renderLeagues() {
    const search = $("#league-search").value.trim().toLocaleLowerCase();
    const visibleLeagues = state.leagues.filter((league) => league.name.toLocaleLowerCase().includes(search)
      || league.id.toLocaleLowerCase().includes(search));
    $("#league-count").textContent = `${state.selectedLeagueIds.size} selected`;
    $("#all-leagues").textContent = state.leagues.length > 0
      && state.selectedLeagueIds.size === state.leagues.length ? "Clear" : "Select all";
    $("#league-list").innerHTML = visibleLeagues.map((league) => {
      const selected = state.selectedLeagueIds.has(league.id);
      const badge = league.id.replace(/[^a-z0-9]/gi, "").slice(0, 3).toUpperCase();
      return `<button type="button" class="league-item ${selected ? "active" : ""}" data-league="${escapeHtml(league.id)}" aria-pressed="${selected}"><span class="league-badge">${escapeHtml(badge || "⚽")}</span><span class="league-name">${escapeHtml(league.name)}</span>${selected ? '<span class="league-check">✓</span>' : ""}</button>`;
    }).join("");
    $("#league-list").querySelectorAll("[data-league]").forEach((button) => {
      button.addEventListener("click", () => {
        const id = button.dataset.league;
        if (state.selectedLeagueIds.has(id)) state.selectedLeagueIds.delete(id);
        else state.selectedLeagueIds.add(id);
        localStorage.setItem("touchline-leagues", JSON.stringify([...state.selectedLeagueIds]));
        renderLeagues();
        loadSelectedDate();
      });
    });
  }

  // Convert ESPN's short status codes into a label and matching CSS class.
  function statusLabel(match) {
    if (match.status === "in") return { label: "LIVE", css: "live" };
    if (match.status === "post") return { label: "FT", css: "finished" };
    return { label: match.status_detail || "UPCOMING", css: "upcoming" };
  }

  // Show a live minute, full-time label, or scheduled kickoff time.
  function matchTime(match) {
    if (match.status === "in" && match.status_detail) return match.status_detail;
    if (match.status === "post") return "Full time";
    const parsed = new Date(match.date);
    return Number.isNaN(parsed.getTime()) ? "Scheduled" : new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" }).format(parsed);
  }

  // Build one match card; escape external names before placing them in HTML.
  function renderCard(match) {
    const status = statusLabel(match);
    const hasScore = match.home_score !== null && match.home_score !== undefined
      && match.away_score !== null && match.away_score !== undefined;
    const score = hasScore
      ? `<span>${escapeHtml(match.home_score)}</span><span>${escapeHtml(match.away_score)}</span>`
      : '<span class="no-score">–</span><span class="no-score">–</span>';
    const favorite = state.favorites.has(match.id);
    return `<article class="match-card">
      <div class="match-state"><span class="state-label ${status.css}">${escapeHtml(status.label)}</span><span class="${match.status === "in" ? "minute" : "state-label"}">${escapeHtml(matchTime(match))}</span></div>
      <div class="teams">
        <div class="team-row"><span class="team-crest">${escapeHtml(initials(match.home_team || "?"))}</span><span class="team-name">${escapeHtml(match.home_team || "Home team")}</span></div>
        <div class="team-row"><span class="team-crest">${escapeHtml(initials(match.away_team || "?"))}</span><span class="team-name">${escapeHtml(match.away_team || "Away team")}</span></div>
      </div>
      <div class="score-column">${score}</div>
      <button type="button" class="favorite-button ${favorite ? "favorited" : ""}" data-favorite="${escapeHtml(match.id)}" aria-label="${favorite ? "Remove from" : "Add to"} favourites" aria-pressed="${favorite}">${favorite ? "★" : "☆"}</button>
    </article>`;
  }

  // Apply date, league, status, and search filters before grouping match cards.
  function renderMatches() {
    if (!state.selectedLeagueIds.size) {
      $("#feed-title").textContent = "Choose your competitions";
      $("#match-count").textContent = "No competitions selected";
      $("#match-feed").innerHTML = `<div class="empty-state"><span class="empty-icon">⚽</span><strong>Make the scoreboard yours</strong><span>Select one or more competitions from the list, or choose all of them to see every configured league.</span></div>`;
      return;
    }
    if (!state.report) return;
    const matches = state.report.matches.filter((match) => {
      const dateMatches = localDateKey(match.date) === state.selectedDate;
      const leagueMatches = state.selectedLeagueIds.has(match.league_id);
      const statusMatches = state.status === "all" || match.status === state.status;
      const search = state.query.trim().toLocaleLowerCase();
      const queryMatches = !search || `${match.home_team} ${match.away_team} ${match.league_name}`.toLocaleLowerCase().includes(search);
      return dateMatches && leagueMatches && statusMatches && queryMatches;
    });
    // Group competitions so matches from the same league appear together.
    const groups = new Map();
    matches.forEach((match) => {
      const id = match.league_id || "other";
      if (!groups.has(id)) groups.set(id, { name: match.league_name || "Other matches", matches: [] });
      groups.get(id).matches.push(match);
    });
    $("#feed-title").textContent = formatDate(dateFromKey(state.selectedDate));
    $("#match-count").textContent = `${matches.length} ${matches.length === 1 ? "match" : "matches"}`;
    if (!matches.length) {
      $("#match-feed").innerHTML = `<div class="empty-state"><span class="empty-icon">◷</span><strong>No matches found</strong><span>Try another date or competition, or refresh the scoreboard for the latest fixtures.</span></div>`;
      return;
    }
    $("#match-feed").innerHTML = [...groups.values()].map((group) => `<section class="league-group">
      <div class="group-heading"><span class="group-mark">${escapeHtml(group.name.replace(/[^a-z0-9]/gi, "").slice(0, 2).toUpperCase())}</span><span class="group-name">${escapeHtml(group.name)}</span><span class="group-count">${group.matches.length} ${group.matches.length === 1 ? "match" : "matches"}</span></div>
      ${group.matches.map(renderCard).join("")}
    </section>`).join("");
    // Favorite controls save their IDs locally and redraw the match list.
    $("#match-feed").querySelectorAll("[data-favorite]").forEach((button) => {
      button.addEventListener("click", () => {
        const id = button.dataset.favorite;
        if (state.favorites.has(id)) state.favorites.delete(id);
        else state.favorites.add(id);
        localStorage.setItem("touchline-favorites", JSON.stringify([...state.favorites]));
        renderMatches();
      });
    });
  }

  // Explain when displayed scoreboard data came from the offline cache.
  function updateTimestamp(timestamp, source = "live") {
    if (source === "local_cache") {
      $("#updated-at").textContent = "Includes saved offline data";
      return;
    }
    if (!timestamp) {
      $("#updated-at").textContent = "Refresh for the latest scores";
      return;
    }
    const parsed = new Date(timestamp);
    $("#updated-at").textContent = Number.isNaN(parsed.getTime())
      ? "Scoreboard ready"
      : `Updated ${new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" }).format(parsed)}`;
  }

  // Load competition names, restore valid saved selections, then load today's data.
  async function loadData() {
    try {
      const leagueResponse = await fetch("/api/leagues");
      if (!leagueResponse.ok) throw new Error("Could not load the competition list.");
      state.leagues = await leagueResponse.json();
      const availableIds = new Set(state.leagues.map((league) => league.id));
      state.selectedLeagueIds = new Set([...state.selectedLeagueIds].filter((id) => availableIds.has(id)));
      localStorage.setItem("touchline-leagues", JSON.stringify([...state.selectedLeagueIds]));
      renderLeagues();
      if (state.selectedLeagueIds.size) await loadSelectedDate();
      else renderMatches();
      const preferredLeague = [...state.selectedLeagueIds][0] || state.leagues[0]?.id || "";
      leagueOptions($("#teams-league"), preferredLeague);
      leagueOptions($("#players-league"), preferredLeague);
      leagueOptions($("#performance-league"), preferredLeague);
    } catch (error) {
      $("#match-feed").innerHTML = `<div class="error-state"><strong>Scoreboard unavailable</strong><span>${escapeHtml(error.message)} Check that the dashboard server is running, then try again.</span><button type="button" id="retry-load">Try again</button></div>`;
      $("#retry-load").addEventListener("click", loadData);
    }
  }

  // Read the version from the server so the footer matches the installed code.
  async function loadVersion() {
    try {
      const result = await getJson("/api/version");
      $("#app-version").textContent = `v${result.version}`;
    } catch (error) {
      $("#app-version").textContent = "version unavailable";
    }
  }

  // Retrieve matches for the chosen day and ignore responses for stale dates.
  async function loadSelectedDate() {
    const requestId = ++dateRequestId;
    updateDownloadLinks();
    if (!state.selectedLeagueIds.size) {
      state.report = null;
      renderMatches();
      return;
    }
    $("#match-feed").innerHTML = '<div class="loading-card"><span class="spinner"></span> Loading fixtures for this date...</div>';
    $("#match-count").textContent = "Loading matches";
    try {
      const params = new URLSearchParams({
        date: state.selectedDate,
        leagues: [...state.selectedLeagueIds].join(","),
      });
      const response = await fetch(`/api/matches?${params}`);
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Could not load matches for this date.");
      if (requestId !== dateRequestId) return;
      state.report = result;
      updateTimestamp(result.generated_at, result.data_source);
      renderMatches();
    } catch (error) {
      if (requestId !== dateRequestId) return;
      $("#match-feed").innerHTML = `<div class="error-state"><strong>Matches unavailable</strong><span>${escapeHtml(error.message)} Check your connection and try again.</span><button type="button" id="retry-date">Try again</button></div>`;
      $("#retry-date").addEventListener("click", loadSelectedDate);
      $("#match-count").textContent = "Unable to load matches";
    }
  }

  // Display a temporary message without changing or replacing the current view.
  function showToast(message) {
    const toast = $("#toast");
    toast.textContent = message;
    toast.classList.add("visible");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toast.classList.remove("visible"), 3200);
  }

  // Ask the server to refresh ESPN data for the currently selected day/leagues.
  async function refreshScores() {
    if (!state.selectedLeagueIds.size) {
      showToast("Select at least one competition first.");
      return;
    }
    const button = $("#refresh-button");
    button.disabled = true;
    button.classList.add("is-loading");
    try {
      const params = new URLSearchParams({
        date: state.selectedDate,
        leagues: [...state.selectedLeagueIds].join(","),
      });
      const response = await fetch(`/api/refresh?${params}`, { method: "POST" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Refresh failed.");
      state.report = result;
      updateTimestamp(result.generated_at, result.data_source);
      renderMatches();
      showToast(`Scores refreshed — ${result.match_count} matches in the report.`);
    } catch (error) {
      showToast(error.message);
    } finally {
      button.disabled = false;
      button.classList.remove("is-loading");
    }
  }

  // Move the shared calendar selection by one day in either direction.
  function moveDate(direction) {
    const next = dateFromKey(state.selectedDate);
    next.setDate(next.getDate() + direction);
    selectDate(dateKey(next));
  }

  // Connect page controls to the functions above, then load initial data.
  initDates();
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.view));
  });
  $("#teams-league").addEventListener("change", loadTeams);
  $("#players-league").addEventListener("change", () => {
    $("#players-team").value = "";
    loadPlayers();
  });
  $("#players-team").addEventListener("change", loadPlayers);
  $("#player-search").addEventListener("input", (event) => {
    state.playerQuery = event.currentTarget.value;
    renderPlayers();
  });
  $("#performance-league").addEventListener("change", loadPerformanceTeams);
  $("#performance-team").addEventListener("change", loadPerformance);
  $("#refresh-button").addEventListener("click", refreshScores);
  $("#previous-day").addEventListener("click", () => moveDate(-1));
  $("#next-day").addEventListener("click", () => moveDate(1));
  $("#date-picker").addEventListener("change", (event) => {
    if (event.currentTarget.value) selectDate(event.currentTarget.value);
  });
  ["#teams-date-picker", "#players-date-picker", "#performance-date-picker"].forEach((selector) => {
    $(selector).addEventListener("change", (event) => {
      if (event.currentTarget.value) selectDate(event.currentTarget.value);
    });
  });
  $("#all-leagues").addEventListener("click", () => {
    state.selectedLeagueIds = state.selectedLeagueIds.size === state.leagues.length
      ? new Set()
      : new Set(state.leagues.map((league) => league.id));
    localStorage.setItem("touchline-leagues", JSON.stringify([...state.selectedLeagueIds]));
    renderLeagues();
    loadSelectedDate();
  });
  $("#league-search").addEventListener("input", renderLeagues);
  $("#search-input").addEventListener("input", (event) => { state.query = event.target.value; renderMatches(); });
  $("#search-input").addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      event.currentTarget.focus();
    }
  });
  document.querySelectorAll("[data-status]").forEach((button) => {
    button.addEventListener("click", () => {
      state.status = button.dataset.status;
      document.querySelectorAll("[data-status]").forEach((tab) => tab.classList.toggle("active", tab === button));
      renderMatches();
    });
  });
  $("#match-feed").innerHTML = `<div class="empty-state"><span class="empty-icon">⚽</span><strong>Choose your competitions</strong><span>Select one or more competitions from the list, or choose all of them to see every configured league.</span></div>`;
  loadVersion();
  loadData();
})();
