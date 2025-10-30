"""
Streamlit Dashboard for NBA Player Statistics using the BALLDONTLIE API
====================================================================

This application provides a comprehensive look at an NBA player's recent
performance and calculates several key averages.  It supports the
following features:

* **Player search** – quickly look up any active or historical NBA player.
* **Recent performance** – compute averages over the last 5, 10 and 20
  games for core counting stats (points, rebounds, assists, steals,
  blocks, turnovers and minutes).
* **Season and career averages** – pull a player's current and prior
  season numbers and aggregate a career average by combining all
  available season data.
* **Head‑to‑head breakdown** – display the player’s per‑team averages
  so you can see how they perform against each opponent.
* **On‑demand projections** – a simple predictive model uses recent
  trends to forecast what the player might do in the next game.

To run this dashboard you will need a **GOAT tier** API key from
`ballDontLie.io` and a Python environment with the packages listed in
`requirements.txt` installed.  When the app starts it prompts for your
API key; the key is sent on each request via the `Authorization`
header.

Note: the BALLDONTLIE API uses cursor based pagination.  Helper
functions below transparently follow the `next_cursor` field to
retrieve all requested records.
"""

from __future__ import annotations

import os
from datetime import datetime
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests
import streamlit as st


###############################################################################
# Configuration
###############################################################################

# Base URL for the BALLDONTLIE API.  This path covers the NBA endpoints.
API_BASE_URL = "https://api.balldontlie.io/v1"


def build_headers(api_key: str) -> Dict[str, str]:
    """Return headers including the required Authorization token.

    Parameters
    ----------
    api_key : str
        Your BALLDONTLIE API key.

    Returns
    -------
    Dict[str, str]
        A dictionary of HTTP headers.
    """
    return {"Authorization": api_key} if api_key else {}


###############################################################################
# API Helpers
###############################################################################

@lru_cache(maxsize=128)
def search_players(query: str, api_key: str) -> List[Dict[str, Any]]:
    """Search for players by name.

    Uses the `players` endpoint with the `search` parameter to locate
    matching players.  Results are cached to avoid repeated network calls.

    Parameters
    ----------
    query : str
        Partial or full name to search for.
    api_key : str
        API key for authorization.

    Returns
    -------
    List[dict]
        List of players (each a dict) returned by the API.
    """
    url = f"{API_BASE_URL}/players"
    params: Dict[str, Any] = {"search": query, "per_page": 50}
    resp = requests.get(url, headers=build_headers(api_key), params=params, timeout=20)
    if resp.status_code != 200:
        st.error(f"Failed to search players: {resp.status_code} – {resp.text}")
        return []
    return resp.json().get("data", [])


def fetch_stats(
    player_id: int,
    api_key: str,
    seasons: Optional[Iterable[int]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    max_records: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Retrieve game stats for a player.

    This helper wraps the `/stats` endpoint and automatically follows
    pagination cursors until either all data is retrieved or
    `max_records` records have been collected.  It supports filtering
    by one or more seasons and/or a date range.

    Parameters
    ----------
    player_id : int
        The player's unique identifier.
    api_key : str
        API key for authorization.
    seasons : iterable of int, optional
        One or more seasons (e.g. 2023 for the 2023‑24 season).  If
        provided, stats will be limited to these seasons.
    start_date : str, optional
        Filter stats occurring on or after this date (YYYY‑MM‑DD).
    end_date : str, optional
        Filter stats occurring on or before this date (YYYY‑MM‑DD).
    max_records : int, optional
        Maximum number of records to retrieve.  If None, all records
        available will be pulled.

    Returns
    -------
    list of dict
        Each element corresponds to a single game’s stat line.
    """
    url = f"{API_BASE_URL}/stats"
    # Build base parameters.  We allow duplicate keys like player_ids[] and
    # seasons[] by assigning list values; requests will encode correctly.
    params: Dict[str, Any] = {"player_ids[]": [player_id], "per_page": 100}
    if seasons:
        params["seasons[]"] = list(seasons)
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date

    stats: List[Dict[str, Any]] = []
    cursor: Optional[int] = None
    while True:
        if cursor is not None:
            params["cursor"] = cursor
        resp = requests.get(url, headers=build_headers(api_key), params=params, timeout=30)
        if resp.status_code != 200:
            st.error(f"Failed to fetch stats: {resp.status_code} – {resp.text}")
            break
        payload = resp.json()
        data_batch = payload.get("data", [])
        stats.extend(data_batch)
        # Stop if we have gathered enough records
        if max_records is not None and len(stats) >= max_records:
            stats = stats[:max_records]
            break
        cursor = payload.get("meta", {}).get("next_cursor")
        if not cursor:
            break
    return stats


def fetch_season_average(
    player_id: int,
    api_key: str,
    season: int,
    season_type: str = "regular",
    category: str = "general",
    type: str = "base",
) -> Optional[Dict[str, Any]]:
    """Retrieve season average stats for a player.

    The `/season_averages/<category>` endpoint requires a season and
    additional type parameter.  This function returns the first (and
    typically only) record from the API or None if no data is found.

    Parameters
    ----------
    player_id : int
        Player ID to retrieve data for.
    api_key : str
        API key for authorization.
    season : int
        Year of the season (e.g., 2024 for the 2024‑25 season).  Season
        is interpreted relative to the starting calendar year.
    season_type : str, optional
        Type of season: "regular", "playoffs", "ist", or "playin".
    category : str, optional
        Category of averages to request; see API docs.  Defaults to
        "general".
    type : str, optional
        Specific statistics group; defaults to "base".  See API docs
        for valid pairs of category and type.

    Returns
    -------
    dict or None
        A dictionary containing the season average stats or None if
        nothing was returned.
    """
    url = f"{API_BASE_URL}/season_averages/{category}"
    params = {
        "season": season,
        "season_type": season_type,
        "type": type,
        "player_ids[]": [player_id],
    }
    resp = requests.get(url, headers=build_headers(api_key), params=params, timeout=20)
    if resp.status_code != 200:
        st.warning(
            f"Season averages unavailable for {season}: {resp.status_code} – {resp.text}"
        )
        return None
    data = resp.json().get("data", [])
    return data[0] if data else None


###############################################################################
# Data Transformation Utilities
###############################################################################

def stats_to_dataframe(stats: List[Dict[str, Any]]) -> pd.DataFrame:
    """Convert a list of raw stat dictionaries into a cleaned DataFrame.

    The BALLDONTLIE stats payload includes nested player, team and game
    objects alongside basic counting stats.  This function flattens
    nested fields, converts minute strings to integers and ensures
    numeric columns are numeric.

    Parameters
    ----------
    stats : list of dict
        Raw stat objects returned from the API.

    Returns
    -------
    pandas.DataFrame
        DataFrame with one row per game.  Columns include core
        statistics plus metadata such as game date, opponent and
        whether the game was at home or away.
    """
    if not stats:
        return pd.DataFrame()

    # Flatten stats and nested fields
    records = []
    for entry in stats:
        base = {k: entry.get(k) for k in [
            "id",
            "min",
            "fgm",
            "fga",
            "fg_pct",
            "fg3m",
            "fg3a",
            "fg3_pct",
            "ftm",
            "fta",
            "ft_pct",
            "oreb",
            "dreb",
            "reb",
            "ast",
            "stl",
            "blk",
            "turnover",
            "pf",
            "pts",
        ]}
        # Convert minutes (string) to integer total minutes
        minutes_str = base.get("min")
        if minutes_str is not None:
            try:
                base["min"] = int(minutes_str)
            except (ValueError, TypeError):
                base["min"] = 0
        # Flatten nested game and team objects
        game = entry.get("game", {})
        base["game_id"] = game.get("id")
        base["game_date"] = game.get("date")
        base["season"] = game.get("season")
        base["postseason"] = game.get("postseason")
        base["home_team_id"] = game.get("home_team_id")
        base["visitor_team_id"] = game.get("visitor_team_id")
        base["home_team_score"] = game.get("home_team_score")
        base["visitor_team_score"] = game.get("visitor_team_score")
        team = entry.get("team", {})
        base["team_id"] = team.get("id")
        base["team_name"] = team.get("full_name")
        base["team_abbr"] = team.get("abbreviation")
        records.append(base)

    df = pd.DataFrame.from_records(records)
    # Ensure numeric columns are numeric
    numeric_cols = [
        "min",
        "fgm",
        "fga",
        "fg_pct",
        "fg3m",
        "fg3a",
        "fg3_pct",
        "ftm",
        "fta",
        "ft_pct",
        "oreb",
        "dreb",
        "reb",
        "ast",
        "stl",
        "blk",
        "turnover",
        "pf",
        "pts",
        "home_team_score",
        "visitor_team_score",
    ]
    df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors="coerce")
    # Convert game_date to datetime
    df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")
    # Determine opponent team ID
    df["opponent_team_id"] = np.where(
        df["team_id"] == df["home_team_id"], df["visitor_team_id"], df["home_team_id"]
    )
    return df


def compute_averages(df: pd.DataFrame, window: Optional[int] = None) -> pd.Series:
    """Compute mean stats over an optional rolling window.

    Parameters
    ----------
    df : pandas.DataFrame
        DataFrame containing one row per game.  Must include the
        columns used for calculations (pts, reb, ast, stl, blk, turnover, min).
    window : int, optional
        Number of most recent games to include.  If None, all rows
        are used.

    Returns
    -------
    pandas.Series
        Series mapping stat names to their mean values.
    """
    if df.empty:
        return pd.Series(dtype=float)
    ordered = df.sort_values("game_date", ascending=False)
    if window:
        ordered = ordered.head(window)
    stats_cols = ["pts", "reb", "ast", "stl", "blk", "turnover", "min"]
    return ordered[stats_cols].mean()


def head_to_head_averages(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate per‑opponent average stats.

    Parameters
    ----------
    df : pandas.DataFrame
        Game log data for the player.

    Returns
    -------
    pandas.DataFrame
        DataFrame indexed by opponent team ID with mean statistics.
    """
    if df.empty:
        return pd.DataFrame()
    group_cols = ["opponent_team_id"]
    agg_cols = ["pts", "reb", "ast", "stl", "blk", "turnover", "min"]
    h2h = df.groupby(group_cols)[agg_cols].mean().reset_index()
    return h2h.sort_values("pts", ascending=False)


def weighted_prediction(last5: pd.Series, last10: pd.Series, season: pd.Series) -> pd.Series:
    """Compute a simple weighted projection for the next game.

    The weights emphasize recent performance (last 5 games) while
    incorporating broader context from the last 10 games and the full
    season.  Adjust weights here to tune the forecast.

    Parameters
    ----------
    last5 : pandas.Series
        Mean statistics from the player’s last 5 games.
    last10 : pandas.Series
        Mean statistics from the player’s last 10 games.
    season : pandas.Series
        Mean statistics from the current season.

    Returns
    -------
    pandas.Series
        Projected values for the next game.
    """
    # Define weights – heavier weight on the most recent games
    w5, w10, wSeason = 0.5, 0.3, 0.2
    # Align indices and fill missing values
    combined = pd.concat([last5, last10, season], axis=1, keys=["l5", "l10", "season"]).fillna(0)
    projection = w5 * combined["l5"] + w10 * combined["l10"] + wSeason * combined["season"]
    return projection


###############################################################################
# Streamlit UI
###############################################################################

def main() -> None:
    """Run the Streamlit dashboard."""
    st.set_page_config(page_title="NBA Player Dashboard", layout="wide")
    st.title("🏀 NBA Player Dashboard – BALLDONTLIE")

    st.markdown(
        """
        Use this dashboard to explore detailed NBA player statistics and
        generate a projection for their next game.  To begin, enter your
        BALLDONTLIE API key and search for a player.
        """
    )

    api_key = st.text_input(
        "Enter your BALLDONTLIE API key",
        type="password",
        value=os.getenv("BALLDONTLIE_API_KEY", ""),
        help="An API key is required for all endpoints.  Find yours at https://app.balldontlie.io",
    )
    if not api_key:
        st.info("Please enter your API key to begin.")
        st.stop()

    # Player search
    query = st.text_input("Search for a player", placeholder="LeBron James")
    player_options: List[Tuple[str, int]] = []
    if query and len(query) >= 3:
        with st.spinner("Searching players..."):
            players = search_players(query, api_key=api_key)
        player_options = [
            (f"{p['first_name']} {p['last_name']} (ID: {p['id']}, {p.get('team', {}).get('full_name', 'No Team')})", p["id"])
            for p in players
        ]

    selected_player: Optional[int] = None
    if player_options:
        label = [opt[0] for opt in player_options]
        vals = [opt[1] for opt in player_options]
        sel = st.selectbox("Select a player", options=list(range(len(label))), format_func=lambda i: label[i])
        selected_player = vals[sel]

    if selected_player is not None:
        # Retrieve the player object for display
        player_obj = next((p for p in players if p["id"] == selected_player), None)
        full_name = f"{player_obj['first_name']} {player_obj['last_name']}" if player_obj else "Selected Player"
        st.header(f"📊 Stats for {full_name}")

        # Determine seasons available for selection.  Use current year down to 1946.
        current_year = datetime.now().year
        seasons = list(range(current_year, 1945, -1))
        col1, col2 = st.columns(2)
        with col1:
            current_season = st.selectbox("Current season", options=seasons, index=0)
        with col2:
            previous_season = st.selectbox(
                "Previous season", options=seasons, index=1 if len(seasons) > 1 else 0
            )

        # Fetch game logs for last 20 games, current season and previous season
        with st.spinner("Fetching stats..."):
            # Last 20 games (regardless of season)
            recent_stats = fetch_stats(selected_player, api_key, max_records=25)
            recent_df = stats_to_dataframe(recent_stats)
            # Stats for current season
            season_stats = fetch_stats(selected_player, api_key, seasons=[current_season])
            season_df = stats_to_dataframe(season_stats)
            # Stats for previous season
            prev_stats = fetch_stats(selected_player, api_key, seasons=[previous_season])
            prev_df = stats_to_dataframe(prev_stats)

        # Compute averages
        last5_avg = compute_averages(recent_df, window=5)
        last10_avg = compute_averages(recent_df, window=10)
        last20_avg = compute_averages(recent_df, window=20)
        season_avg = compute_averages(season_df)
        prev_season_avg = compute_averages(prev_df)
        career_avg = compute_averages(pd.concat([season_df, prev_df, recent_df]))

        # Display summary metrics
        st.subheader("Averages Summary")
        metrics_cols = st.columns(5)
        for idx, (label, series) in enumerate(
            [
                ("Last 5 Games", last5_avg),
                ("Last 10 Games", last10_avg),
                ("Last 20 Games", last20_avg),
                (f"Season {current_season}", season_avg),
                (f"Season {previous_season}", prev_season_avg),
            ]
        ):
            with metrics_cols[idx]:
                st.markdown(f"**{label}**")
                st.metric("Points", f"{series.get('pts', 0):.1f}")
                st.metric("Rebounds", f"{series.get('reb', 0):.1f}")
                st.metric("Assists", f"{series.get('ast', 0):.1f}")

        # Career summary
        st.markdown("**Career Average (computed)**")
        st.metric("Points", f"{career_avg.get('pts', 0):.1f}")
        st.metric("Rebounds", f"{career_avg.get('reb', 0):.1f}")
        st.metric("Assists", f"{career_avg.get('ast', 0):.1f}")

        # Head‑to‑head breakdown
        st.subheader("Head‑to‑Head Averages (All Opponents)")
        h2h_df = head_to_head_averages(recent_df)
        if not h2h_df.empty:
            # Map opponent IDs to names by looking up team names in recent stats
            team_map = {
                row["team_id"]: row["team_name"] for row in recent_df[["team_id", "team_name"]].dropna().drop_duplicates().to_dict("records")
            }
            # Attempt to fetch unique opponent names from stats; some may not be present
            def lookup_team_name(opponent_id: Any) -> str:
                return team_map.get(opponent_id, str(opponent_id))

            h2h_df["Opponent"] = h2h_df["opponent_team_id"].apply(lookup_team_name)
            h2h_table = h2h_df[["Opponent", "pts", "reb", "ast", "stl", "blk", "turnover", "min"]]
            h2h_table.rename(
                columns={
                    "pts": "Pts",
                    "reb": "Reb",
                    "ast": "Ast",
                    "stl": "Stl",
                    "blk": "Blk",
                    "turnover": "TO",
                    "min": "Min",
                },
                inplace=True,
            )
            st.dataframe(h2h_table.style.format("{:.1f}"), use_container_width=True)
        else:
            st.info("Not enough data for head‑to‑head averages.")

        # Chart recent performance
        st.subheader("Last 20 Game Logs")
        if not recent_df.empty:
            chart_df = recent_df.sort_values("game_date")
            chart_df["game"] = chart_df["game_date"].dt.strftime("%Y-%m-%d")
            metrics = ["pts", "reb", "ast"]
            st.line_chart(chart_df.set_index("game")[metrics])
        else:
            st.info("No recent games available for charting.")

        # Prediction
        st.subheader("Projected Next Game")
        projection = weighted_prediction(last5_avg, last10_avg, season_avg)
        proj_cols = st.columns(len(projection))
        for i, (stat, value) in enumerate(projection.items()):
            with proj_cols[i]:
                st.metric(stat.upper(), f"{value:.1f}")


if __name__ == "__main__":
    main()