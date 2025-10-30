import os
import math
import time
import json
import numpy as np
import pandas as pd
import requests
import streamlit as st

# -----------------------------
# Helpers
# -----------------------------
API_BASE = "https://api.balldontlie.io/nba/v1"

def bdl_get(path, headers, params=None, max_pages=1):
    """Basic GET with simple pagination guard."""
    url = f"{API_BASE}/{path.lstrip('/')}"
    results = []
    page = 1
    while page <= max_pages:
        p = params.copy() if params else {}
        p["page"] = page
        r = requests.get(url, headers=headers, params=p, timeout=20)
        if r.status_code == 401:
            raise RuntimeError("Unauthorized (401). Double-check your Goat Tier API key and 'Authorization: Bearer <key>' header.")
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and "data" in data:
            results.extend(data["data"])
            meta = data.get("meta", {})
            total_pages = meta.get("total_pages", page)
            if page >= total_pages:
                break
        else:
            # Non-standard response; just return it
            return data
        page += 1
    return results

def find_player(name, headers):
    players = bdl_get("players", headers, params={"search": name, "per_page": 25}, max_pages=2)
    # Return the closest match by case-insensitive contains
    name_l = name.lower().strip()
    if not players:
        return None
    exact = [p for p in players if p.get("first_name","") and p.get("last_name","")
             and f"{p['first_name']} {p['last_name']}".lower() == name_l]
    if exact:
        return exact[0]
    contains = [p for p in players if name_l in f"{p.get('first_name','')} {p.get('last_name','')}".lower()]
    return (contains[0] if contains else players[0])

def get_season_averages(player_id, season, headers):
    # season_averages?player_ids[]=ID&season=YYYY
    params = {f"player_ids[]": player_id, "season": season}
    r = requests.get(f"{API_BASE}/season_averages", headers=headers, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    return (data.get("data") or [])

def get_recent_games_stats(player_id, season, headers, limit=25):
    # stats?player_ids[]=ID&seasons[]=YYYY&per_page=100
    params = {f"player_ids[]": player_id, f"seasons[]": season, "per_page": 100}
    stats = bdl_get("stats", headers, params=params, max_pages=3)
    # Most recent first by game date if present
    # BallDon’tLie returns nested game info; sort by game['date'] if present
    def _gdate(s):
        g = s.get("game", {})
        return g.get("date", "")
    stats_sorted = sorted(stats, key=_gdate, reverse=True)
    return stats_sorted[:limit]

def safe_div(a, b):
    return a / b if b else 0.0

def per_minute_from_averages(avg):
    minutes = avg.get("min", 0) or avg.get("minutes", 0) or 0
    if isinstance(minutes, str):
        try:
            # Some APIs return "34.2"
            minutes = float(minutes)
        except:
            minutes = 0
    if minutes <= 0:
        return {}
    pm = {}
    pm["pts_pm"] = safe_div(avg.get("pts", 0), minutes)
    pm["reb_pm"] = safe_div(avg.get("reb", 0), minutes)
    pm["ast_pm"] = safe_div(avg.get("ast", 0), minutes)
    pm["fg3m_pm"] = safe_div(avg.get("fg3m", avg.get("fg3a", 0)*0.35 if avg.get("fg3a") else 0), minutes)  # fallback
    pm["stl_pm"] = safe_div(avg.get("stl", 0), minutes)
    pm["blk_pm"] = safe_div(avg.get("blk", 0), minutes)
    pm["tov_pm"] = safe_div(avg.get("turnover", avg.get("turnovers", 0)), minutes)
    pm["fga_pm"] = safe_div(avg.get("fga", 0), minutes)
    pm["fta_pm"] = safe_div(avg.get("fta", 0), minutes)
    return pm

def aggregate_recent(stats_rows):
    """Compute last-N per-minute rates + per-game std devs for key categories."""
    if not stats_rows:
        return {}, {}
    mins = []
    pts = []; reb=[]; ast=[]; fg3m=[]
    for s in stats_rows:
        # Minutes sometimes as integer 'min' or 'minutes'
        m = s.get("min") or s.get("minutes")
        if isinstance(m, str):
            try: m = float(m)
            except: m = 0
        m = m or 0
        mins.append(m)
        pts.append(s.get("pts", 0))
        reb.append(s.get("reb", 0))
        ast.append(s.get("ast", 0))
        fg3m.append(s.get("fg3m", s.get("fg3a",0)*0.35 if s.get("fg3a") else 0))

    total_min = sum(mins) or 1
    pm = {
        "pts_pm": sum(pts)/total_min,
        "reb_pm": sum(reb)/total_min,
        "ast_pm": sum(ast)/total_min,
        "fg3m_pm": sum(fg3m)/total_min,
    }
    # Per-game std for simulation noise
    sd = {
        "pts_sd": float(np.std(pts, ddof=1)) if len(pts) > 1 else 0.0,
        "reb_sd": float(np.std(reb, ddof=1)) if len(reb) > 1 else 0.0,
        "ast_sd": float(np.std(ast, ddof=1)) if len(ast) > 1 else 0.0,
        "fg3m_sd": float(np.std(fg3m, ddof=1)) if len(fg3m) > 1 else 0.0,
    }
    return pm, sd

def blend_rates(season_pm, recent_pm, w_season=0.7, w_recent=0.3):
    out = {}
    keys = set(season_pm.keys()) | set(recent_pm.keys())
    for k in keys:
        out[k] = (w_season * season_pm.get(k, 0.0)) + (w_recent * recent_pm.get(k, 0.0))
    return out

def simulate_stats(mean_dict, sd_dict, sims=10000):
    """Simple Normal-based simulation, clipped at 0. Good enough for quick O/U odds."""
    rng = np.random.default_rng(42)
    out = {}
    for k_mean in ["pts","reb","ast","fg3m"]:
        mu = mean_dict.get(k_mean, 0.0)
        sd = sd_dict.get(f"{k_mean}_sd", max(0.10*mu, 0.5))  # fallback variance
        draws = rng.normal(mu, sd, size=sims)
        out[k_mean] = np.clip(draws, 0, None)
    pra = out["pts"] + out["reb"] + out["ast"]
    out["pra"] = pra
    return out

def prob_over(draws, line):
    if draws is None or len(draws) == 0: return 0.0
    return float((draws > line).mean())

# -----------------------------
# UI
# -----------------------------
st.set_page_config(page_title="Goat-Tier NBA Projections", page_icon="🏀", layout="wide")
st.title("🏀 Goat-Tier NBA: Player Projection (Season Avg → Game Projection)")

with st.sidebar:
    st.header("Setup")
    api_key = st.text_input("Goat Tier API Key", type="password", help="Paste your BallDon’tLie Goat Tier key here.")
    default_season = int(time.strftime("%Y"))
    season = st.number_input("Season (e.g., 2025)", min_value=2000, max_value=2100, value=default_season)
    projected_minutes = st.slider("Projected Minutes", 10, 44, 34)
    opp_adjust = st.slider("Opponent/Pace Adjustment (multiplier)", 0.80, 1.20, 1.00, 0.01,
                           help=">1.00 = faster pace / softer opponent; <1.00 = slower pace / tougher opponent.")
    blend_recent = st.slider("Recent Form Weight (%)", 0, 100, 30, help="Share of last-10 games in blend (season is the rest).")
    sims = st.select_slider("Simulations", options=[2000, 5000, 10000, 20000], value=10000)
    st.caption("Tip: Start at 1.00 adjustment. If opponent is very good defensively or slow, try 0.95; if fast/soft, try 1.05–1.10.")

st.subheader("Player & Lines")
cols = st.columns([1.2, 1, 1, 1, 1, 1])
with cols[0]:
    player_name = st.text_input("Player", value="LeBron James")
with cols[1]:
    pts_line = st.number_input("PTS line", min_value=0.0, value=25.5, step=0.5)
with cols[2]:
    reb_line = st.number_input("REB line", min_value=0.0, value=7.5, step=0.5)
with cols[3]:
    ast_line = st.number_input("AST line", min_value=0.0, value=6.5, step=0.5)
with cols[4]:
    fg3m_line = st.number_input("3PM line", min_value=0.0, value=2.5, step=0.5)
with cols[5]:
    pra_line = st.number_input("PRA line", min_value=0.0, value=38.5, step=0.5)

run_btn = st.button("⚡ Run Projection")

# -----------------------------
# Main Logic
# -----------------------------
if run_btn:
    if not api_key:
        st.error("Please paste your Goat Tier API key in the sidebar.")
        st.stop()

    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        # 1) Resolve player
        player = find_player(player_name, headers)
        if not player:
            st.error("No player found. Try a more precise name.")
            st.stop()

        full_name = f"{player.get('first_name','')} {player.get('last_name','')}".strip()
        st.success(f"Found: {full_name} (id={player.get('id')})")

        # 2) Season averages
        sa = get_season_averages(player["id"], season, headers)
        if not sa:
            st.warning("No season averages found for this season. Trying previous season.")
            sa = get_season_averages(player["id"], season-1, headers)

        if not sa:
            st.error("No season averages available. Cannot project.")
            st.stop()

        season_avg = sa[0]
        # Normalize minutes field name
        if "min" not in season_avg and "minutes" in season_avg:
            season_avg["min"] = season_avg["minutes"]

        # 3) Recent (last 10) game stats
        recent_rows = get_recent_games_stats(player["id"], season, headers, limit=10)
        if not recent_rows:
            # Try previous season if no games yet
            recent_rows = get_recent_games_stats(player["id"], season-1, headers, limit=10)

        recent_pm, sd_per_game = aggregate_recent(recent_rows)
        season_pm = per_minute_from_averages(season_avg)

        w_recent = (blend_recent/100.0)
        w_season = 1.0 - w_recent
        pm = blend_rates(season_pm, recent_pm, w_season=w_season, w_recent=w_recent)

        # 4) Scale by minutes & opponent/pace adjustment
        means = {
            "pts": pm.get("pts_pm", 0.0) * projected_minutes * opp_adjust,
            "reb": pm.get("reb_pm", 0.0) * projected_minutes * opp_adjust,
            "ast": pm.get("ast_pm", 0.0) * projected_minutes * opp_adjust,
            "fg3m": pm.get("fg3m_pm", 0.0) * projected_minutes * opp_adjust,
        }

        # 5) Simulation
        sim_draws = simulate_stats(means, sd_per_game, sims=int(sims))

        # 6) Probabilities
        probs = {
            "PTS>": prob_over(sim_draws["pts"], pts_line),
            "REB>": prob_over(sim_draws["reb"], reb_line),
            "AST>": prob_over(sim_draws["ast"], ast_line),
            "3PM>": prob_over(sim_draws["fg3m"], fg3m_line),
            "PRA>": prob_over(sim_draws["pra"], pra_line),
        }

        # 7) Output tables
        left, right = st.columns([1,1])
        with left:
            st.markdown("### 📈 Projected Means")
            proj_df = pd.DataFrame([
                {"Stat":"PTS","Mean":means["pts"]},
                {"Stat":"REB","Mean":means["reb"]},
                {"Stat":"AST","Mean":means["ast"]},
                {"Stat":"3PM","Mean":means["fg3m"]},
                {"Stat":"PRA","Mean":means["pts"]+means["reb"]+means["ast"]},
            ])
            st.dataframe(proj_df, use_container_width=True)

        with right:
            st.markdown("### 🎯 Over Probabilities")
            prob_df = pd.DataFrame([
                {"Market":"PTS", "Line":pts_line, "Over %":round(probs["PTS>"]*100,1)},
                {"Market":"REB", "Line":reb_line, "Over %":round(probs["REB>"]*100,1)},
                {"Market":"AST", "Line":ast_line, "Over %":round(probs["AST>"]*100,1)},
                {"Market":"3PM", "Line":fg3m_line, "Over %":round(probs["3PM>"]*100,1)},
                {"Market":"PRA", "Line":pra_line, "Over %":round(probs["PRA>"]*100,1)},
            ])
            st.dataframe(prob_df, use_container_width=True)

        # 8) Raw season & recent for transparency
        with st.expander("🔍 Raw Season Averages (from API)"):
            st.json(season_avg)
        with st.expander("🕒 Recent Games Used (last 10)"):
            st.write(pd.DataFrame(recent_rows)[["game","min","pts","reb","ast","fg3m"]])

        # 9) Download CSV
        out = (
            proj_df
            .assign(Season=season_avg.get("season", season),
                    Player=full_name,
                    Minutes=projected_minutes,
                    OppAdj=opp_adjust,
                    RecentWeight=blend_recent)
        )
        st.download_button("⬇️ Download Projections CSV", data=out.to_csv(index=False), file_name=f"{full_name.replace(' ','_')}_projection.csv", mime="text/csv")

        st.caption("Method: per-minute from season + last-10 (blend), scaled by projected minutes and your pace/opponent multiplier; simple Normal sims for O/U odds. Quick, transparent, tweakable.")

    except Exception as e:
        st.error(str(e))
        st.stop()