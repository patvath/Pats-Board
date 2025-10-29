# app.py — Single-player BallDontLie Streamlit app (drop-in)
# ---------------------------------------------------------
# 🔑 PASTE YOUR KEY HERE (keep quotes)
API_KEY_DEFAULT = "7f4db7a9-c34e-478d-a799-fef77b9d1f78"

import os
import time
import math
import datetime as dt
from typing import List, Dict, Optional
import requests
import pandas as pd
import streamlit as st

st.set_page_config(page_title="NBA Projections — Single Player (BALLDONTLIE)",
                   page_icon="🏀", layout="wide")

# -------------------- Key resolution (manual override) --------------------
# Precedence: hardcoded API_KEY_DEFAULT (if changed from placeholder) >
#             sidebar manual paste (runtime) > Streamlit secrets > env var
def resolve_api_key() -> str:
    hardcoded = (API_KEY_DEFAULT or "").strip()
    if hardcoded and hardcoded != "PASTE_YOUR_KEY_HERE":
        return hardcoded

    # Sidebar override (hidden input)
    sidebar_k = st.sidebar.text_input("BALLDONTLIE API Key (optional)", value="", type="password")
    if sidebar_k and sidebar_k.strip():
        return sidebar_k.strip()

    # Streamlit secrets
    try:
        secret_k = st.secrets.get("BALLDONTLIE_API_KEY", "") or st.secrets.get("api", {}).get("api_key", "")
        if secret_k:
            return str(secret_k).strip()
    except Exception:
        pass

    # Environment variable
    env_k = os.getenv("BALLDONTLIE_API_KEY", "")
    if env_k:
        return env_k.strip()

    return ""

API_KEY = resolve_api_key()
BASE = "https://api.balldontlie.io/nba/v1"   # correct NBA namespace
HEADERS = {"Authorization": API_KEY} if API_KEY else {}

# --------------------------- HTTP helper ---------------------------------
def http_get(path: str, params: dict = None, timeout: int = 20, retries: int = 2):
    if not API_KEY:
        st.error("No API key detected. Paste your key into API_KEY_DEFAULT at the top of this file, or use the sidebar/secrets/env.")
        st.stop()

    url = f"{BASE}{path}"
    params = params or {}
    last_exc = None

    for attempt in range(retries + 1):
        r = requests.get(url, headers=HEADERS, params=params, timeout=timeout)
        # Handle rate limit politely
        if r.status_code == 429 and attempt < retries:
            retry_after = r.headers.get("Retry-After")
            try:
                wait = float(retry_after) if retry_after else (1.0 + attempt * 2.0)
            except Exception:
                wait = 1.0 + attempt * 2.0
            time.sleep(wait)
            continue

        if r.status_code != 200:
            # Short preview of response for debugging but don't print key
            preview = (r.text or "")[:400]
            st.error(f"HTTP {r.status_code} on {path}\nURL: {url}\nParams: {params}\n\nResponse preview: {preview}")
            st.stop()

        try:
            return r.json()
        except ValueError as e:
            last_exc = e
            preview = (r.text or "")[:400]
            st.error(f"Non-JSON response from {path}. Preview:\n{preview}")
            st.stop()

    if last_exc:
        raise last_exc
    return None

# -------------------- Single-player API calls ----------------------------
@st.cache_data(ttl=600)
def search_player_by_name(name: str) -> pd.DataFrame:
    """
    Single search call: /players?search=<name>&per_page=10
    Returns a DataFrame of matches (may include retired/duplicates).
    """
    payload = http_get("/players", params={"search": name, "per_page": 10})
    data = payload.get("data", []) if isinstance(payload, dict) else []
    return pd.json_normalize(data) if data else pd.DataFrame()

@st.cache_data(ttl=600)
def get_season_averages(player_id: int, season: int) -> Dict:
    payload = http_get(f"/season_averages/general", params={"season": season, "type": "base", "player_ids[]": player_id})
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    return rows[0] if rows else {}

@st.cache_data(ttl=600)
def get_recent_stats(player_id: int, start_date: str, end_date: str) -> List[Dict]:
    # batch dates in chunks of 10 to minimize number of requests
    d0 = dt.datetime.strptime(start_date, "%Y-%m-%d").date()
    d1 = dt.datetime.strptime(end_date, "%Y-%m-%d").date()
    dates = []
    cur = d0
    while cur <= d1:
        dates.append(cur.strftime("%Y-%m-%d"))
        cur += dt.timedelta(days=1)

    rows: List[Dict] = []
    for i in range(0, len(dates), 10):
        chunk = dates[i:i+10]
        params = []
        params.append(("player_ids[]", player_id))
        for d in chunk:
            params.append(("dates[]", d))
        # Convert list of tuples to dict-like by using requests' ability to accept list-of-tuples
        # but our http_get helper expects dict; we will pass dict(params) which is acceptable for these endpoints
        payload = http_get("/stats", params=dict(params))
        rows.extend(payload.get("data", []) if isinstance(payload, dict) else [])
    return rows

# ------------------------ Projection helpers -----------------------------
def compute_recent_avgs(rows: List[Dict]) -> Optional[Dict]:
    if not rows:
        return None
    df = pd.DataFrame(rows)
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

def blend_projection(season_avg: Optional[Dict], recent_avg: Optional[Dict], season_weight: float, recent_weight: float) -> Dict:
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

def round_or_none(v):
    try:
        return None if (v is None or (isinstance(v, float) and math.isnan(v))) else round(float(v), 2)
    except Exception:
        return None

# ------------------------------- UI -------------------------------------
st.title("NBA Projections — Single Player (BALLDONTLIE)")
st.caption("Paste your API key at the top of this file to bypass secrets. This app only searches the single player you request.")

# Sidebar diagnostics (optional)
st.sidebar.markdown("**Diagnostics**")
st.sidebar.code(f"Base: {BASE}\nKey length: {len(API_KEY) if API_KEY else 0}")

# Use a form to avoid auto-calls on every keystroke
with st.form("player_form"):
    player_query = st.text_input("Player name (full or partial)", placeholder="e.g., LeBron James")
    col1, col2 = st.columns([1,1])
    with col1:
        recent_days = st.slider("Recent window (days)", min_value=7, max_value=60, value=30)
    with col2:
        season_weight = st.slider("Season weight", min_value=0.0, max_value=1.0, value=0.4, step=0.05)
    upcoming_opts = [(dt.date.today() + dt.timedelta(days=1)).strftime("%Y-%m-%d"),
                     (dt.date.today() + dt.timedelta(days=2)).strftime("%Y-%m-%d")]
    upcoming_date = st.selectbox("Upcoming date", upcoming_opts)
    submitted = st.form_submit_button("Get Projection")

if submitted:
    if not API_KEY:
        st.error("No API key found. Paste into API_KEY_DEFAULT at top or use sidebar/secrets/env.")
        st.stop()

    if not player_query or not player_query.strip():
        st.warning("Enter a player name.")
        st.stop()

    # Single-player search only
    df_players = search_player_by_name(player_query.strip())
    if df_players.empty:
        st.error("No players matched that search.")
        st.stop()

    # prefer exact match
    df_players["full_name"] = df_players["first_name"].str.strip() + " " + df_players["last_name"].str.strip()
    exact = df_players[df_players["full_name"].str.lower() == player_query.strip().lower()]
    selected = (exact.iloc[0] if not exact.empty else df_players.iloc[0])

    pid = int(selected["id"])
    team_id = int(selected.get("team.id") or 0)
    st.subheader(f"{selected['full_name']}  (ID {pid})")
    st.write(f"Team: {selected.get('team.full_name', 'N/A')} — Pos: {selected.get('position') or 'N/A'}")

    # Season averages (single player)
    season_num = dt.date.today().year
    srow = get_season_averages(pid, season_num)
    season_avg = {
        "PTS": srow.get("pts"),
        "AST": srow.get("ast"),
        "REB": srow.get("reb"),
        "3PM": srow.get("fg3m"),
        "MIN": srow.get("min"),
    }

    # Recent stats for player
    start_date = (dt.date.today() - dt.timedelta(days=recent_days)).strftime("%Y-%m-%d")
    end_date = dt.date.today().strftime("%Y-%m-%d")
    rec_rows = get_recent_stats(pid, start_date, end_date)
    recent_avg = compute_recent_avgs(rec_rows) if rec_rows else {}

    proj = blend_projection(season_avg, recent_avg, season_weight, 1.0 - season_weight)

    left, right = st.columns(2)
    with left:
        st.markdown("**Season Averages**")
        st.metric("PTS", round_or_none(season_avg.get("PTS")))
        st.metric("AST", round_or_none(season_avg.get("AST")))
        st.metric("REB", round_or_none(season_avg.get("REB")))
        st.metric("3PM", round_or_none(season_avg.get("3PM")))
    with right:
        st.markdown(f"**Recent ({recent_days} days)**")
        st.metric("PTS", round_or_none(recent_avg.get("PTS")))
        st.metric("AST", round_or_none(recent_avg.get("AST")))
        st.metric("REB", round_or_none(recent_avg.get("REB")))
        st.metric("3PM", round_or_none(recent_avg.get("3PM")))

    st.markdown(f"### Projection for {upcoming_date}")
    st.table(pd.DataFrame([{
        "Player": selected["full_name"],
        "Team": selected.get("team.abbreviation"),
        "PTS_proj": round_or_none(proj.get("PTS")),
        "AST_proj": round_or_none(proj.get("AST")),
        "REB_proj": round_or_none(proj.get("REB")),
        "3PM_proj": round_or_none(proj.get("3PM")),
        "Blend": f"{season_weight:.2f} season + {1-season_weight:.2f} recent"
    }]))
else:
    st.info("Enter a player name and click 'Get Projection' to run the single-player lookup.")