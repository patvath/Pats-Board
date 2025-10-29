# app.py
# Streamlit single-player projections using BallDon'tLie (v2)
# ----------------------------------------------------------
# 🔑 PASTE YOUR KEY HERE (quotes required)
API_KEY_DEFAULT = "7f4db7a9-c34e-478d-a799-fef77b9d1f78"

import os
import time
import math
import datetime as dt
from typing import Dict, List, Optional

import requests
import pandas as pd
import streamlit as st

st.set_page_config(page_title="NBA Projections — Single Player (BDL v2)", page_icon="🏀", layout="wide")

# ----------------------------- Config -----------------------------
# Use v2 base as requested
API_BASE_DEFAULT = "https://api.balldontlie.io/v2"

# ------------------------- Key resolution --------------------------
def resolve_api_key() -> str:
    # priority: hardcoded constant > streamlit secrets > env var > sidebar override
    hardcoded = (API_KEY_DEFAULT or "").strip()
    if hardcoded and hardcoded != "PASTE_YOUR_KEY_HERE":
        return hardcoded

    secret = (st.secrets.get("BALLDONTLIE_API_KEY", "") or "").strip() if hasattr(st, "secrets") else ""
    if secret:
        return secret

    env = (os.getenv("BALLDONTLIE_API_KEY", "") or "").strip()
    if env:
        return env

    # runtime override (hidden input)
    return st.sidebar.text_input("BALLDONTLIE API Key (optional)", value="", type="password").strip()

API_KEY = resolve_api_key()
API_BASE = (st.secrets.get("API_BASE", API_BASE_DEFAULT) if hasattr(st, "secrets") else API_BASE_DEFAULT).strip()
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"} if API_KEY else {}

# diagnostics
if "last_wait" not in st.session_state:
    st.session_state.last_wait = 0.0

# ------------------------- HTTP helpers ----------------------------
def http_get(path: str, params: Dict = None, timeout: int = 20, retries: int = 2):
    """
    GET wrapper:
      - Uses API_BASE + path
      - Sends Authorization: Bearer <key>
      - Handles 401 (shows message) and 429 with Retry-After/backoff
      - Prevents raw JSON decode errors and shows response preview on error
    """
    if not API_KEY:
        st.error("No BALLDONTLIE API key detected. Paste it in the top of app.py, in Streamlit Secrets, or in the sidebar input.")
        st.stop()

    url = f"{API_BASE}{path}"
    params = params or {}

    attempt = 0
    backoff = 1.0
    while attempt <= retries:
        attempt += 1
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=timeout)
        except Exception as e:
            st.error(f"Request failed: {e}")
            st.stop()

        # 429 handling
        if r.status_code == 429:
            retry_after = r.headers.get("Retry-After")
            try:
                wait = float(retry_after) if retry_after else backoff
            except Exception:
                wait = backoff
            st.warning(f"Rate limited (429). Waiting {wait:.1f}s before retry (attempt {attempt}/{retries}).")
            st.session_state.last_wait = wait
            time.sleep(wait)
            backoff *= 2
            continue

        # 401/403 -> clear message
        if r.status_code in (401, 403):
            preview = (r.text or "")[:400]
            st.error(f"HTTP {r.status_code} Unauthorized/Forbidden on {path}\n\nURL: {url}\nParams: {params}\n\nResponse preview:\n{preview}\n\nCheck your key, tier, and header format (must be Bearer <KEY>).")
            st.stop()

        if r.status_code != 200:
            preview = (r.text or "")[:400]
            st.error(f"HTTP {r.status_code} on {path}\n\nURL: {url}\nParams: {params}\n\nResponse preview:\n{preview}")
            st.stop()

        # try parse JSON
        try:
            return r.json()
        except ValueError:
            preview = (r.text or "")[:400]
            st.error(f"Non-JSON response from {path}. Preview:\n{preview}")
            st.stop()

    st.error("Exceeded retry attempts due to rate limits.")
    st.stop()

# ------------------------- Minimal single-player API usage -------------------------
@st.cache_data(ttl=300)
def search_player_by_name(name: str) -> pd.DataFrame:
    """Single call: /players?search=<name>&per_page=10"""
    payload = http_get("/players", params={"search": name, "per_page": 10})
    data = payload.get("data", []) if isinstance(payload, dict) else []
    return pd.json_normalize(data) if data else pd.DataFrame()

@st.cache_data(ttl=600)
def get_season_averages(player_id: int, season: int) -> Dict:
    """Single-call to /season_averages (v2). Pass player_ids[]"""
    params = {"season": season, "player_ids[]": player_id}
    payload = http_get("/season_averages", params=params)
    rows = payload.get("data", [])
    return rows[0] if rows else {}

@st.cache_data(ttl=600)
def get_recent_stats(player_id: int, start_date: str, end_date: str) -> List[Dict]:
    """
    Pull recent per-game lines for the player via /stats with multiple dates[].
    Batch in chunks of up to 10 dates to minimize requests.
    """
    d0 = dt.datetime.strptime(start_date, "%Y-%m-%d").date()
    d1 = dt.datetime.strptime(end_date, "%Y-%m-%d").date()
    dates = []
    cur = d0
    while cur <= d1:
        dates.append(cur.strftime("%Y-%m-%d"))
        cur += dt.timedelta(days=1)

    rows = []
    for i in range(0, len(dates), 10):
        chunk = dates[i : i + 10]
        params = []
        params.append(("player_ids[]", player_id))
        for d in chunk:
            params.append(("dates[]", d))
        # http_get accepts dict; convert the list of tuples into a dict for requests:
        payload = http_get("/stats", params=dict(params))
        rows.extend(payload.get("data", []))
    return rows

# ------------------------- Projection math -------------------------
def compute_recent_avgs(stats_rows: List[Dict]) -> Optional[Dict]:
    if not stats_rows:
        return None
    df = pd.DataFrame(stats_rows)
    for col in ["pts", "ast", "reb", "fg3m", "min"]:
        if col not in df.columns:
            df[col] = 0
    # coerce minutes
    df["min"] = pd.to_numeric(df["min"], errors="coerce")
    return {
        "GP": len(df),
        "PTS": df["pts"].mean(),
        "AST": df["ast"].mean(),
        "REB": df["reb"].mean(),
        "3PM": df["fg3m"].mean(),
        "MIN": df["min"].mean(skipna=True),
    }

def blend_projection(season_avg: Dict, recent_avg: Dict, season_weight: float, recent_weight: float) -> Dict:
    out = {}
    for k in ["PTS", "AST", "REB", "3PM"]:
        s = (season_avg or {}).get(k, math.nan)
        r = (recent_avg or {}).get(k, math.nan)
        if not math.isnan(s) and not math.isnan(r):
            out[k] = season_weight * s + recent_weight * r
        elif not math.isnan(r):
            out[k] = r
        elif not math.isnan(s):
            out[k] = s
        else:
            out[k] = math.nan
    return out

def r2(v):
    try:
        return round(float(v), 2)
    except Exception:
        return None

# ------------------------- UI -------------------------
st.title("NBA Projections — Single Player (BDL v2)")
st.caption("Search only the one player you enter to avoid rate limits. Uses v2 endpoints and Bearer auth.")

# quick diagnostics
st.sidebar.markdown("**Diagnostics**")
st.sidebar.code(f"API Base: {API_BASE}\nKey length: {len(API_KEY) if API_KEY else 0}\nLast wait: {st.session_state.last_wait}s")

with st.form("player_form"):
    player_query = st.text_input("Player name", placeholder="e.g., LeBron James")
    col1, col2 = st.columns([2,1])
    with col1:
        days_back = st.slider("Recent window (days)", 7, 60, 30)
    with col2:
        season_weight = st.slider("Season weight", 0.0, 1.0, 0.4, 0.05)
    today = dt.date.today()
    options = [today + dt.timedelta(days=1), today + dt.timedelta(days=2)]
    date_choice = st.selectbox("Upcoming date", [d.strftime("%Y-%m-%d") for d in options])
    submitted = st.form_submit_button("Get Projection")

recent_weight = 1.0 - season_weight

if submitted:
    if not API_KEY:
        st.warning("No API key available. Paste it at the top of this file, set Streamlit Secret BALLDONTLIE_API_KEY, or use the sidebar override.")
        st.stop()

    if not player_query.strip():
        st.warning("Type a player's name and click Get Projection.")
        st.stop()

    # --- Single search call for that player only ---
    players_df = search_player_by_name(player_query.strip())
    if players_df.empty:
        st.error("No player matched that search.")
        st.stop()

    # Prefer exact (case-insensitive) match else first
    players_df["full_name"] = players_df["first_name"].str.strip() + " " + players_df["last_name"].str.strip()
    exact = players_df[players_df["full_name"].str.lower() == player_query.strip().lower()]
    row = exact.iloc[0] if not exact.empty else players_df.iloc[0]

    pid = int(row["id"])
    team_id = int(row["team.id"]) if "team.id" in row else None
    st.subheader(f"{row['full_name']} (ID {pid})")

    # Get season averages for single player
    season = dt.date.today().year
    season_raw = get_season_averages(pid, season)
    season_avg = {
        "PTS": season_raw.get("pts"),
        "AST": season_raw.get("ast"),
        "REB": season_raw.get("reb"),
        "3PM": season_raw.get("fg3m"),
        "MIN": season_raw.get("min"),
    }

    # Recent window
    start = (dt.date.today() - dt.timedelta(days=days_back)).strftime("%Y-%m-%d")
    end = dt.date.today().strftime("%Y-%m-%d")
    recent_rows = get_recent_stats(pid, start, end)
    recent_avg = compute_recent_avgs(recent_rows) or {}

    proj = blend_projection(season_avg, recent_avg, season_weight, recent_weight)

    left, right = st.columns(2)
    with left:
        st.markdown("**Season Averages**")
        st.metric("PTS", r2(season_avg.get("PTS")))
        st.metric("AST", r2(season_avg.get("AST")))
        st.metric("REB", r2(season_avg.get("REB")))
        st.metric("3PM", r2(season_avg.get("3PM")))
    with right:
        st.markdown(f"**Recent ({days_back} days)**")
        st.metric("PTS", r2(recent_avg.get("PTS")))
        st.metric("AST", r2(recent_avg.get("AST")))
        st.metric("REB", r2(recent_avg.get("REB")))
        st.metric("3PM", r2(recent_avg.get("3PM")))

    st.markdown(f"### Projection for {date_choice}")
    st.dataframe(pd.DataFrame([{
        "Player": row["full_name"],
        "Team": row.get("team.abbreviation"),
        "Opponent": None,
        "PTS_proj": r2(proj["PTS"]),
        "AST_proj": r2(proj["AST"]),
        "REB_proj": r2(proj["REB"]),
        "3PM_proj": r2(proj["3PM"]),
        "Blend": f"{season_weight:.2f} season + {recent_weight:.2f} recent",
    }]))

    with st.expander("Raw recent game lines"):
        if recent_rows:
            df = pd.DataFrame(recent_rows)
            st.dataframe(df.head(200))
        else:
            st.write("No recent game lines found in the window.")

st.caption("This app makes only the single-player search and single-player stats calls to avoid hitting rate limits. If you still receive 429, wait a moment and try again.")