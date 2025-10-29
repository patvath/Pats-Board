"""
Streamlit NBA projection app using Ball Don't Lie (BDL) API.

This app allows users to search for a single NBA player by name, using the BDL
API's `/players?search=` endpoint. Once a player is selected, the app
retrieves the player's season averages and recent game statistics to compute
projections for the upcoming game. It avoids bulk requests (like fetching
all active players) to respect API rate limits and only makes the necessary
calls for the single queried player.

To use this app:
1. Insert your Ball Don't Lie API key into `API_KEY_DEFAULT` below, or
   provide it via Streamlit Secrets (`BALLDONTLIE_API_KEY`) or environment
   variable `BALLDONTLIE_API_KEY`. You can also paste it in the sidebar
   text box at runtime.
2. Run the app with `streamlit run app.py`.
3. Enter a player's full name and click the submit button. The app will
   display season averages, recent form, and blended projections for
   points, assists, rebounds, and three-point makes for the selected
   upcoming date.

This script includes rate-limiting handling and caches requests to
minimize repeated API calls.
"""

import os
import time
import math
import datetime as dt
from typing import Dict, List, Optional

import pandas as pd
import requests
import streamlit as st


# ---------------------------------------------------------------------------
# Configuration
#
# Paste your Ball Don't Lie API key here, between the quotes. If you prefer
# to manage keys via Streamlit Secrets or environment variables, leave this
# as "PASTE_KEY_HERE" and set the `BALLDONTLIE_API_KEY` secret or env var.
API_KEY_DEFAULT = "7f4db7a9-c34e-478d-a799-fef77b9d1f78"

BASE = "https://api.balldontlie.io/nba/v1"  # NBA namespace required for BDL API


def resolve_api_key() -> str:
    """Resolve the API key from multiple possible locations.

    Priority: hardcoded value > Streamlit secret > environment variable >
    sidebar input.
    """
    # Hardcoded key (only use if not left as the placeholder)
    hardcoded = (API_KEY_DEFAULT or "").strip()
    if hardcoded and hardcoded != "PASTE_KEY_HERE":
        return hardcoded
    # Streamlit secrets
    secret = (st.secrets.get("BALLDONTLIE_API_KEY", "") or "").strip()
    if secret:
        return secret
    # Environment variable
    env = (os.getenv("BALLDONTLIE_API_KEY", "") or "").strip()
    if env:
        return env
    # Sidebar input (as a fallback)
    return st.sidebar.text_input(
        "BALLDONTLIE API Key (optional)", value="", type="password"
    ).strip()


# Resolve the API key once at startup
API_KEY = resolve_api_key()
HEADERS = {"Authorization": API_KEY} if API_KEY else {}

# Initialize the Streamlit page configuration
st.set_page_config(
    page_title="NBA Projections â Single Player (BALLDONTLIE)",
    page_icon="ð§®",
    layout="wide",
)


# ---------------------------------------------------------------------------
# HTTP helper functions

def http_get(
    path: str,
    params: Dict | List | None = None,
    timeout: int = 25,
    retries: int = 2,
) -> dict:
    """Make a GET request to the BDL API with basic retry and rate-limit handling.

    Args:
        path: The endpoint path (e.g., "/players").
        params: Dictionary or list of query parameters.
        timeout: Timeout for the request in seconds.
        retries: Number of additional attempts on HTTP 429 (rate limit).

    Returns:
        Parsed JSON payload from the API as a dictionary.

    Raises:
        Streamlit-specific errors when API key is missing or responses are not
        successful/JSON. These errors halt execution and display messages to
        the user.
    """
    if not API_KEY:
        st.error(
            "No API key detected. Please paste your key into app.py, set it "
            "as a secret, or input it in the sidebar."
        )
        st.stop()

    url = f"{BASE}{path}"
    params = params or {}
    # Attempt the request up to retries+1 times
    for attempt in range(retries + 1):
        response = requests.get(url, headers=HEADERS, params=params, timeout=timeout)
        # Handle rate limiting (HTTP 429) with exponential backoff
        if response.status_code == 429 and attempt < retries:
            retry_after = response.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else (1.0 + attempt)
            time.sleep(delay)
            continue
        # If not successful, show error and stop
        if response.status_code != 200:
            preview = (response.text or "")[:400]
            st.error(
                f"HTTP {response.status_code} on {path}\n\n"
                f"URL: {url}\nParams: {params}\n\n"
                f"Response preview:\n{preview}"
            )
            st.stop()
        # Attempt to parse JSON
        try:
            return response.json()
        except ValueError:
            preview = (response.text or "")[:400]
            st.error(
                f"Non-JSON response from {path}\n\n"
                f"URL: {url}\nParams: {params}\n\nPreview:\n{preview}"
            )
            st.stop()

    # If we exhausted retries and still didn't return, raise an error
    st.error("Failed to retrieve data after multiple attempts.")
    st.stop()


# ---------------------------------------------------------------------------
# API helper functions with caching to minimize repeated calls

@st.cache_data(ttl=600)
def search_player_by_name(name: str) -> pd.DataFrame:
    """Search for players by name using the BDL API.

    Uses the `/players?search=` endpoint to find players matching the
    provided name. This function only makes a single API call and returns
    up to 10 results to minimize rate-limit issues.

    Args:
        name: The player's name to search for.

    Returns:
        DataFrame containing player search results.
    """
    # Limit results to 10 players to reduce payload size
    payload = http_get("/players", params={"search": name, "per_page": 10})
    data = payload.get("data", []) if isinstance(payload, dict) else []
    return pd.json_normalize(data) if data else pd.DataFrame()


@st.cache_data(ttl=600)
def season_averages(
    player_id: int,
    season: int,
    season_type: str = "regular",
    category: str = "general",
    type_: str = "base",
) -> Dict:
    """Retrieve season averages for a player.

    Args:
        player_id: Player ID to retrieve stats for.
        season: Year of the season.
        season_type: Typically 'regular' or 'playoff'.
        category: The category of season averages (e.g. 'general').
        type_: The type of averages (e.g. 'base' for basic stats).

    Returns:
        A dictionary with the season averages for the player.
    """
    params = [
        ("season", season),
        ("season_type", season_type),
        ("type", type_),
        ("player_ids[]", player_id),
    ]
    payload = http_get(f"/season_averages/{category}", params=dict(params))
    rows = payload.get("data", [])
    return rows[0] if rows else {}


@st.cache_data(ttl=600)
def recent_stats(player_id: int, start_date: str, end_date: str) -> List[Dict]:
    """Retrieve recent game stats for a player between two dates.

    Breaks the date range into chunks to reduce the number of API calls.
    Args:
        player_id: Player ID to retrieve stats for.
        start_date: Start date in 'YYYY-MM-DD' format.
        end_date: End date in 'YYYY-MM-DD' format.

    Returns:
        List of game stat dictionaries.
    """
    d0 = dt.datetime.strptime(start_date, "%Y-%m-%d").date()
    d1 = dt.datetime.strptime(end_date, "%Y-%m-%d").date()
    dates: List[str] = []
    cur = d0
    while cur <= d1:
        dates.append(cur.strftime("%Y-%m-%d"))
        cur += dt.timedelta(days=1)

    rows: List[Dict] = []
    for i in range(0, len(dates), 10):
        chunk = dates[i : i + 10]
        params_list: List[tuple] = [("player_ids[]", player_id)]
        for d in chunk:
            params_list.append(("dates[]", d))
        payload = http_get("/stats", params=dict(params_list))
        rows.extend(payload.get("data", []))
    return rows


# ---------------------------------------------------------------------------
# Statistics and projection utilities

def recent_avgs(rows: List[Dict]) -> Optional[Dict]:
    """Compute average stats from a list of recent game stat dictionaries.

    Args:
        rows: List of game stat dictionaries returned by the API.

    Returns:
        Dictionary containing the average points, assists, rebounds,
        three-pointers made, and minutes (when available). Returns None if
        the input list is empty.
    """
    if not rows:
        return None
    df = pd.DataFrame(rows)
    # Ensure columns exist
    for col in ["pts", "ast", "reb", "fg3m", "min"]:
        if col not in df.columns:
            df[col] = 0
    df["min"] = pd.to_numeric(df["min"], errors="coerce")
    return {
        "GP": len(df),
        "PTS": df["pts"].mean(),
        "AST": df["ast"].mean(),
        "REB": df["reb"].mean(),
        "3PM": df["fg3m"].mean(),
        "MIN": df["min"].mean(skipna=True),
    }


def blend_projection(
    season_avg: Dict | None, recent_avg: Dict | None, s_w: float = 0.4, r_w: float = 0.6
) -> Dict:
    """Blend season and recent stats to create projections.

    Args:
        season_avg: Dictionary of season average stats (or None).
        recent_avg: Dictionary of recent average stats (or None).
        s_w: Weight for the season average.
        r_w: Weight for the recent average.

    Returns:
        A dictionary containing blended projections for points, assists,
        rebounds, and three-pointers made.
    """
    out: Dict[str, float] = {}
    for stat in ["PTS", "AST", "REB", "3PM"]:
        season_value = (season_avg or {}).get(stat, math.nan)
        recent_value = (recent_avg or {}).get(stat, math.nan)
        if not math.isnan(season_value) and not math.isnan(recent_value):
            out[stat] = s_w * season_value + r_w * recent_value
        elif not math.isnan(recent_value):
            out[stat] = recent_value
        elif not math.isnan(season_value):
            out[stat] = season_value
        else:
            out[stat] = math.nan
    return out


def r2(v: Optional[float]) -> Optional[float]:
    """Round a number to two decimal places or return None if invalid."""
    try:
        return round(float(v), 2)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Streamlit user interface

st.title("NBA Projections â Single Player (BALLDONTLIE)")
st.caption(
    "Only searches the player you enter (no bulk endpoints) to avoid rate limits. "
    "Uses `/nba/v1` endpoints."
)

# Sidebar diagnostics (helpful for debugging key issues)
st.sidebar.code(
    f"API Base: {BASE}\nKey length: {len(API_KEY) if API_KEY else 0}"
)

# Use a form to control when API calls are triggered
with st.form("player_form", clear_on_submit=False):
    player_name = st.text_input("Player name", placeholder="e.g., Stephen Curry")
    colA, colB, colC = st.columns([1, 1, 1])
    with colA:
        days_back = st.slider("Recent window (days)", 7, 60, 30)
    with colB:
        season_weight = st.slider("Season weight", 0.0, 1.0, 0.4, 0.05)
    with colC:
        today = dt.date.today()
        options = [today + dt.timedelta(days=1), today + dt.timedelta(days=2)]
        date_choice = st.selectbox(
            "Upcoming date", [d.strftime("%Y-%m-%d") for d in options]
        )
    submitted = st.form_submit_button("Get Projection")

recent_weight = 1 - season_weight if "season_weight" in locals() else 0.6

if submitted:
    # Validate input
    if not API_KEY:
        st.warning(
            "No API key found. Paste it at the top of this file (API_KEY_DEFAULT) "
            "or use Streamlit secrets/Env variables."
        )
        st.stop()
    if not player_name.strip():
        st.warning("Enter a player's full name.")
        st.stop()

    # Search for the player by name (single API call)
    results = search_player_by_name(player_name.strip())
    if results.empty:
        st.error("No players found by that search.")
        st.stop()

    # Attempt an exact match; fallback to the first result
    results["full_name"] = results["first_name"] + " " + results["last_name"]
    exact = results[results["full_name"].str.lower() == player_name.strip().lower()]
    row = exact.iloc[0] if not exact.empty else results.iloc[0]
    pid = int(row["id"])
    team_id = int(row["team.id"])
    st.subheader(f"{row['full_name']} (ID {pid})")

    # Fetch games for the selected date to determine opponent
    games_payload = http_get("/games", params={"dates[]": date_choice, "per_page": 50})
    game_list = games_payload.get("data", []) if isinstance(games_payload, dict) else []
    next_game = None
    for game in game_list:
        if game["home_team"]["id"] == team_id or game["visitor_team"]["id"] == team_id:
            next_game = game
            break
    if next_game is None:
        st.info(
            f"No game for {row['team.full_name']} on {date_choice}. Projections still shown "
            "based on averages."
        )

    # Pull season averages for the player
    current_season = dt.date.today().year
    season_row = season_averages(pid, current_season, season_type="regular", category="general", type_="base")
    season_avg = {
        "PTS": season_row.get("pts"),
        "AST": season_row.get("ast"),
        "REB": season_row.get("reb"),
        "3PM": season_row.get("fg3m"),
        "MIN": season_row.get("min"),
    }

    # Pull recent stats for the player
    start_date = (dt.date.today() - dt.timedelta(days=days_back)).strftime("%Y-%m-%d")
    end_date = dt.date.today().strftime("%Y-%m-%d")
    rec_rows = recent_stats(pid, start_date, end_date)
    recent_avg = recent_avgs(rec_rows) or {}

    # Compute blended projection
    projections = blend_projection(season_avg, recent_avg, s_w=season_weight, r_w=recent_weight)

    # Display season and recent averages side-by-side
    col_left, col_right = st.columns(2)
    with col_left:
        st.markdown("**Season Averages**")
        st.metric("PTS", r2(season_avg.get("PTS")))
        st.metric("AST", r2(season_avg.get("AST")))
        st.metric("REB", r2(season_avg.get("REB")))
        st.metric("3PM", r2(season_avg.get("3PM")))
    with col_right:
        st.markdown(f"**Recent ({days_back} days)**")
        st.metric("PTS", r2(recent_avg.get("PTS")))
        st.metric("AST", r2(recent_avg.get("AST")))
        st.metric("REB", r2(recent_avg.get("REB")))
        st.metric("3PM", r2(recent_avg.get("3PM")))

    # Display projections in a data table
    st.markdown(f"### Projection for {date_choice}")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Player": row["full_name"],
                    "Team": row["team.abbreviation"],
                    "Opponent": (
                        next_game["visitor_team"]["abbreviation"]
                        if (
                            next_game
                            and next_game["home_team"]["id"] == team_id
                        )
                        else (
                            next_game["home_team"]["abbreviation"] if next_game else None
                        )
                    ),
                    "PTS_proj": r2(projections.get("PTS")),
                    "AST_proj": r2(projections.get("AST")),
                    "REB_proj": r2(projections.get("REB")),
                    "3PM_proj": r2(projections.get("3PM")),
                    "Blend": f"{season_weight:.2f} season + {recent_weight:.2f} recent",
                }
            ]
        )
    )

st.caption(
    "This app performs a single player search and fetches only the necessary data "
    "to avoid rate limit errors. If you encounter a 429 error, please wait and "
    "try again later."
)