# app.py — Streamlit NBA projections using BALLDONTLIE (GOAT tier)
# ----------------------------------------------------------------
# 🔑 PASTE YOUR KEY HERE (quotes required)
API_KEY_DEFAULT = "7f4db7a9-c34e-478d-a799-fef77b9d1f78"

import os
import math
import datetime as dt
import requests
import pandas as pd
import streamlit as st

st.set_page_config(page_title="NBA Projections — BALLDONTLIE", page_icon="🧮", layout="wide")

# ============================ Key Resolution =============================
def resolve_api_key():
    # Precedence: hardcoded (this file) > Streamlit secrets > env var > sidebar field
    hardcoded = (API_KEY_DEFAULT or "").strip()
    if hardcoded and hardcoded != "PASTE_KEY_HERE":
        return hardcoded
    secret = (st.secrets.get("BALLDONTLIE_API_KEY", "") or "").strip()
    if secret:
        return secret
    env = (os.getenv("BALLDONTLIE_API_KEY", "") or "").strip()
    if env:
        return env
    # Optional manual override in the sidebar (hidden input)
    return st.sidebar.text_input("BALLDONTLIE API Key (optional)", value="", type="password").strip()

API_KEY = resolve_api_key()
BASE = "https://api.balldontlie.io/nba/v1"   # ✅ NBA namespace (required)
HEADERS = {"Authorization": API_KEY} if API_KEY else {}

# ============================ HTTP Utilities =============================
def http_get(path: str, params: dict | list | None = None, timeout: int = 25):
    """
    GET wrapper with readable errors and JSON safety (prevents JSONDecodeError).
    Shows a short response preview when status != 200.
    """
    if not API_KEY:
        st.error("No API key detected. Paste it at the top of app.py or add to Secrets/Env/Sidebar.")
        st.stop()

    url = f"{BASE}{path}"
    r = requests.get(url, headers=HEADERS, params=params or {}, timeout=timeout)

    if r.status_code != 200:
        preview = (r.text or "")[:400]
        st.error(f"HTTP {r.status_code} on {path}\n\nURL: {url}\nParams: {params}\n\nResponse preview:\n{preview}")
        st.stop()

    try:
        return r.json()
    except ValueError:
        preview = (r.text or "")[:400]
        st.error(f"Non-JSON response from {path}\n\nURL: {url}\nParams: {params}\n\nPreview:\n{preview}")
        st.stop()

def list_paginated(path: str, params: dict | list | None = None, max_pages: int = 20):
    """
    Cursor-based pagination helper. Returns concatenated 'data' arrays.
    """
    params = dict(params or {})
    params.setdefault("per_page", 100)
    rows, cursor = [], None
    for _ in range(max_pages):
        p = dict(params)
        if cursor:
            p["cursor"] = cursor
        payload = http_get(path, p)
        rows.extend(payload.get("data", []))
        cursor = (payload.get("meta") or {}).get("next_cursor")
        if not cursor:
            break
    return rows

# ============================== API Helpers ==============================
@st.cache_data(ttl=3600)
def list_active_players():
    # ✅ Correct route: /players?active=true (NOT /players/active)
    return list_paginated("/players", {"active": "true"})

@st.cache_data(ttl=900)
def games_on_date(date_str: str):
    return list_paginated("/games", {"dates[]": date_str})

@st.cache_data(ttl=600)
def season_averages(player_ids: list[int], season: int,
                    season_type="regular", category="general", type_="base"):
    # season averages lives under /season_averages/<category>
    q = [("season", season), ("season_type", season_type), ("type", type_)]
    for pid in player_ids:
        q.append(("player_ids[]", pid))
    payload = http_get(f"/season_averages/{category}", params=dict(q))
    return payload.get("data", [])

@st.cache_data(ttl=600)
def recent_stats(player_id: int, start_date: str, end_date: str):
    """
    Per-game stat lines between dates via /stats with repeated dates[] params.
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
        chunk = dates[i:i+10]
        q = [("player_ids[]", player_id)]
        for d in chunk:
            q.append(("dates[]", d))
        payload = http_get("/stats", params=dict(q))
        rows.extend(payload.get("data", []))
    return rows

# ========================= Math / Projections ============================
def recent_avgs(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    df = pd.DataFrame(rows)
    for col in ["pts", "ast", "reb", "fg3m", "min"]:
        if col not in df.columns:
            df[col] = 0
    df["min"] = pd.to_numeric(df["min"], errors="coerce")
    return {
        "GP":  len(df),
        "PTS": df["pts"].mean(),
        "AST": df["ast"].mean(),
        "REB": df["reb"].mean(),
        "3PM": df["fg3m"].mean(),
        "MIN": df["min"].mean(skipna=True),
    }

def blend_projection(season_avg: dict | None, recent_avg: dict | None, s_w=0.4, r_w=0.6):
    out = {}
    for k in ["PTS", "AST", "REB", "3PM"]:
        s = (season_avg or {}).get(k, math.nan)
        r = (recent_avg or {}).get(k, math.nan)
        if not math.isnan(s) and not math.isnan(r):
            out[k] = s_w * s + r_w * r
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

# ================================ UI =====================================
st.title("NBA Projections — BALLDONTLIE")
st.caption("Projections = blend of season averages and recent form (last N days). Uses `/nba/v1` endpoints.")

# Quick diagnostics in sidebar (you can comment these out)
st.sidebar.code(f"API Base: {BASE}\nKey length: {len(API_KEY) if API_KEY else 0}")

col1, col2, col3 = st.columns([2,1,1])
with col1:
    player_query = st.text_input("Player name", placeholder="e.g., Stephen Curry")
with col2:
    days_back = st.slider("Recent window (days)", 7, 60, 30)
with col3:
    season_weight = st.slider("Season weight", 0.0, 1.0, 0.4, 0.05)
recent_weight = 1 - season_weight

today = dt.date.today()
tomorrow = today + dt.timedelta(days=1)
date_choice = st.selectbox(
    "Upcoming date",
    [tomorrow.strftime("%Y-%m-%d"), (tomorrow + dt.timedelta(days=1)).strftime("%Y-%m-%d")]
)

if not API_KEY:
    st.warning("No API key found. Paste it at the very top of this file (API_KEY_DEFAULT) or in Secrets/Env/Sidebar.")
    st.stop()

# -------- Resolve players (uses /players?active=true) --------
players = list_active_players()
pdf = pd.json_normalize(players)
if pdf.empty:
    st.error("Could not load active players. Check key/tier or base URL.")
    st.stop()

pdf["full_name"] = pdf["first_name"] + " " + pdf["last_name"]

sel_row = None
if player_query:
    m = pdf[pdf["full_name"].str.contains(player_query, case=False, na=False)]
    if not m.empty:
        sel_row = m.iloc[0]
    else:
        st.warning("No active player matched that name.")

if sel_row is not None:
    pid = int(sel_row["id"])
    team_id = int(sel_row["team.id"])
    st.subheader(f"{sel_row['full_name']}  (ID {pid})")

    # Next game on chosen date (if any)
    g_list = games_on_date(date_choice)
    next_game = None
    for g in g_list:
        if g["home_team"]["id"] == team_id or g["visitor_team"]["id"] == team_id:
            next_game = g
            break
    if next_game is None:
        st.info(f"No game for {sel_row['team.full_name']} on {date_choice}. Projections still shown from averages.")

    # Season averages (general/base)
    season = today.year  # adjust if the API expects league-year pattern
    seas = season_averages([pid], season, season_type="regular", category="general", type_="base")
    srow = seas[0] if seas else {}
    season_avg = {
        "PTS": srow.get("pts"),
        "AST": srow.get("ast"),
        "REB": srow.get("reb"),
        "3PM": srow.get("fg3m"),
        "MIN": srow.get("min"),
    }

    # Recent window
    start = (today - dt.timedelta(days=days_back)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    rec_rows = recent_stats(pid, start, end)
    recent_avg = recent_avgs(rec_rows) or {}

    # Projection
    proj = blend_projection(season_avg, recent_avg, s_w=season_weight, r_w=recent_weight)

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
        "Player": sel_row["full_name"],
        "Team": sel_row["team.abbreviation"],
        "Opponent": (
            next_game["visitor_team"]["abbreviation"]
            if (next_game and next_game["home_team"]["id"] == team_id)
            else (next_game["home_team"]["abbreviation"] if next_game else None)
        ),
        "PTS_proj": r2(proj["PTS"]),
        "AST_proj": r2(proj["AST"]),
        "REB_proj": r2(proj["REB"]),
        "3PM_proj": r2(proj["3PM"]),
        "Blend": f"{season_weight:.2f} season + {recent_weight:.2f} recent"
    }]))
else:
    st.info("Type a player’s full name to generate a projection.")

st.caption("If you hit a 401, verify: (1) base URL includes /nba/v1, (2) header is Authorization: <your_key>, (3) no hidden whitespace in the key.")