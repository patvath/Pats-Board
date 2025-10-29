# app.py — Streamlit NBA projections using BALLDONTLIE (single-player lookup)
# --------------------------------------------------------------------------
# 🔑 PASTE YOUR KEY HERE (quotes required)
API_KEY_DEFAULT = "7f4db7a9-c34e-478d-a799-fef77b9d1f78"

import os
import time
import math
import datetime as dt
import requests
import pandas as pd
import streamlit as st
from typing import Dict, List, Optional

st.set_page_config(page_title="NBA Projections — Single Player (BALLDONTLIE)", page_icon="🧮", layout="wide")

# ============================ Key Resolution =============================
def resolve_api_key() -> str:
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
BASE = "https://api.balldontlie.io/nba/v1"   # ✅ NBA namespace
HEADERS = {"Authorization": API_KEY} if API_KEY else {}

# ============================ HTTP Utilities =============================
def http_get(path: str, params: Dict | List | None = None, timeout: int = 25, retries: int = 2):
    """
    GET wrapper with readable errors, JSON safety, and light 429 backoff.
    Only used for single-player search + single-player stats to minimize calls.
    """
    if not API_KEY:
        st.error("No API key detected. Paste it at the top of app.py or add to Secrets/Env/Sidebar.")
        st.stop()

    url = f"{BASE}{path}"
    last_exc = None
    for attempt in range(retries + 1):
        r = requests.get(url, headers=HEADERS, params=params or {}, timeout=timeout)
        # Rate limit handling
        if r.status_code == 429 and attempt < retries:
            retry_after = r.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else (1.0 + attempt)
            time.sleep(delay)
            continue
        if r.status_code != 200:
            preview = (r.text or "")[:400]
            st.error(f"HTTP {r.status_code} on {path}\n\nURL: {url}\nParams: {params}\n\nResponse preview:\n{preview}")
            st.stop()
        try:
            return r.json()
        except ValueError as e:
            last_exc = e
            preview = (r.text or "")[:400]
            st.error(f"Non-JSON response from {path}\n\nURL: {url}\nParams: {params}\n\nPreview:\n{preview}")
            st.stop()
    if last_exc:
        raise last_exc

# ============================== API Helpers ==============================
@st.cache_data(ttl=600)
def search_player_by_name(name: str) -> pd.DataFrame:
    """
    Search players by name — SINGLE CALL — using /players?search=<name>.
    (We do NOT query active lists or paginate widely to avoid rate limits.)
    """
    # Keep per_page small to reduce payload; usually 1–10 is enough for a name
    payload = http_get("/players", params={"search": name, "per_page": 10})
    data = payload.get("data", []) if isinstance(payload, dict) else []
    return pd.json_normalize(data) if data else pd.DataFrame()

@st.cache_data(ttl=600)
def season_averages(player_id: int, season: int,
                    season_type="regular", category="general", type_="base") -> Dict:
    q = [("season", season), ("season_type", season_type), ("type", type_), ("player_ids[]", player_id)]
    payload = http_get(f"/season_averages/{category}", params=dict(q))
    rows = payload.get("data", [])
    return rows[0] if rows else {}

@st.cache_data(ttl=600)
def recent_stats(player_id: int, start_date: str, end_date: str) -> List[Dict]:
    """
    Pull per-game stat lines between dates via /stats with repeated dates[] params.
    We batch dates in chunks of 10 to keep this to a SMALL number of calls.
    """
    d0 = dt.datetime.strptime(start_date, "%Y-%m-%d").date()
    d1 = dt.datetime.strptime(end_date, "%Y-%m-%d").date()
    dates = []
    cur = d0
    while cur <= d1:
        dates.append(cur.strftime("%Y-%m-%d"))
        cur += dt.timedelta(days=1)

    rows: List[Dict] = []
    # Do as few calls as possible — often 1–3 chunks
    for i in range(0, len(dates), 10):
        chunk = dates[i:i+10]
        q = [("player_ids[]", player_id)]
        for d in chunk:
            q.append(("dates[]", d))
        payload = http_get("/stats", params=dict(q))
        rows.extend(payload.get("data", []))
    return rows

# ========================= Math / Projections ============================
def recent_avgs(rows: List[Dict]) -> Optional[Dict]:
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

def blend_projection(season_avg: Dict | None, recent_avg: Dict | None, s_w=0.4, r_w=0.6) -> Dict:
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
st.title("NBA Projections — Single Player (BALLDONTLIE)")
st.caption("Only searches the player you enter (no bulk endpoints) to avoid rate limits. Uses `/nba/v1` endpoints.")

# Sidebar diagnostics (you can comment these out)
st.sidebar.code(f"API Base: {BASE}\nKey length: {len(API_KEY) if API_KEY else 0}")

# Use a form so we only call the API when you click submit (not every keystroke)
with st.form("player_form", clear_on_submit=False):
    player_name = st.text_input("Player name", placeholder="e.g., Stephen Curry")
    colA, colB, colC = st.columns([1,1,1])
    with colA:
        days_back = st.slider("Recent window (days)", 7, 60, 30)
    with colB:
        season_weight = st.slider("Season weight", 0.0, 1.0, 0.4, 0.05)
    with colC:
        # Tomorrow or the day after to find a likely upcoming slate
        today = dt.date.today()
        options = [today + dt.timedelta(days=1), today + dt.timedelta(days=2)]
        date_choice = st.selectbox("Upcoming date", [d.strftime("%Y-%m-%d") for d in options])
    submitted = st.form_submit_button("Get Projection")

recent_weight = 1 - season_weight if "season_weight" in locals() else 0.6

if submitted:
    if not API_KEY:
        st.warning("No API key found. Paste it at the very top of this file (API_KEY_DEFAULT) or in Secrets/Env/Sidebar.")
        st.stop()
    if not player_name.strip():
        st.warning("Enter a player's full name.")
        st.stop()

    # ---- SINGLE CALL: search only this player ----
    results = search_player_by_name(player_name.strip())
    if results.empty:
        st.error("No players found by that search.")
        st.stop()

    # Prefer exact match; otherwise first best match
    results["full_name"] = results["first_name"] + " " + results["last_name"]
    exact = results[results["full_name"].str.lower() == player_name.strip().lower()]
    row = (exact.iloc[0] if not exact.empty else results.iloc[0])
    pid = int(row["id"])
    team_id = int(row["team.id"])
    st.subheader(f"{row['full_name']}  (ID {pid})")

    # We do NOT list or loop all games — only the chosen date for opponent context
    games_payload = http_get("/games", params={"dates[]": date_choice, "per_page": 50})
    g_list = games_payload.get("data", []) if isinstance(games_payload, dict) else []
    next_game = None
    for g in g_list:
        if g["home_team"]["id"] == team_id or g["visitor_team"]["id"] == team_id:
            next_game = g
            break
    if next_game is None:
        st.info(f"No game for {row['team.full_name']} on {date_choice}. Projections still shown from averages.")

    # Season averages (single player)
    season = dt.date.today().year
    srow = season_averages(pid, season, season_type="regular", category="general", type_="base")
    season_avg = {
        "PTS": srow.get("pts"),
        "AST": srow.get("ast"),
        "REB": srow.get("reb"),
        "3PM": srow.get("fg3m"),
        "MIN": srow.get("min"),
    }

    # Recent window (single player)
    start = (dt.date.today() - dt.timedelta(days=days_back)).strftime("%Y-%m-%d")
    end = dt.date.today().strftime("%Y-%m-%d")
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
        "Player": row["full_name"],
        "Team": row["team.abbreviation"],
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

st.caption("This app performs just a single player search and single-player stats pulls to avoid rate limits. If you still see 429, try again in a moment.")