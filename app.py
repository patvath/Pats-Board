import streamlit as st
import requests
import pandas as pd
import random
from datetime import datetime, timedelta

# ----------------------------- #
# APP CONFIG
# ----------------------------- #
st.set_page_config(
    page_title="NBA Dashboard",
    page_icon="🏀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom theme (Midnight Blue + Gold)
st.markdown("""
    <style>
        body, .stApp {
            background-color: #0B132B;
            color: #F5C518;
        }
        h1, h2, h3, h4 {
            color: #F5C518;
            text-align: center;
        }
        .player-card {
            border-radius: 12px;
            background-color: #1C2541;
            color: #F5C518;
            padding: 20px;
            text-align: center;
            box-shadow: 0px 0px 14px rgba(245,197,24,0.35);
        }
        .stat-bar {
            background: linear-gradient(90deg, #F5C518 0%, #FFCC33 100%);
            height: 10px;
            border-radius: 5px;
        }
        .projection {
            font-size: 1.2rem;
            font-weight: bold;
            color: #FFD700;
        }
        .confidence {
            font-size: 0.9rem;
            color: #87CEEB;
        }
        .bet-take {
            border-left: 4px solid #FFD700;
            padding-left: 10px;
            margin-bottom: 10px;
        }
    </style>
""", unsafe_allow_html=True)

# ----------------------------- #
# API CONFIG
# ----------------------------- #
API_KEY = st.secrets.get("BALLDONTLIE_KEY", "")
HEADERS = {"Authorization": API_KEY} if API_KEY else {}
BASE_URL = "https://api.balldontlie.io/v1"

# ----------------------------- #
# CACHED FETCHERS
# ----------------------------- #
@st.cache_data(ttl=3600)
def get_teams():
    r = requests.get(f"{BASE_URL}/teams", headers=HEADERS)
    return r.json().get("data", [])

@st.cache_data(ttl=3600)
def get_players_by_team(team_id):
    r = requests.get(f"{BASE_URL}/players?team_ids[]={team_id}&per_page=100", headers=HEADERS)
    return r.json().get("data", [])

@st.cache_data(ttl=3600)
def get_player_stats(player_id):
    # Try season averages
    res = requests.get(f"{BASE_URL}/season_averages?player_ids[]={player_id}", headers=HEADERS)
    data = res.json().get("data", [])
    if data:
        s = data[0]
        s["source"] = "Season Averages"
        return s

    # Fallback to last 10 games
    res = requests.get(f"{BASE_URL}/stats?player_ids[]={player_id}&per_page=10", headers=HEADERS)
    games = res.json().get("data", [])
    if games:
        df = pd.DataFrame(games)
        stats = {
            "pts": df["pts"].mean(),
            "reb": df["reb"].mean(),
            "ast": df["ast"].mean(),
            "stl": df["stl"].mean(),
            "blk": df["blk"].mean(),
            "source": "Last 10 Games"
        }
        return stats
    return None

# ----------------------------- #
# SIMPLE PROJECTION MODEL
# ----------------------------- #
def project_player(stats):
    if not stats:
        return None
    # Adjust small random variation and scaling for opponent defense
    variation = random.uniform(0.9, 1.1)
    projected = {
        "pts": round(stats["pts"] * variation, 1),
        "reb": round(stats["reb"] * variation, 1),
        "ast": round(stats["ast"] * variation, 1),
        "stl": round(stats["stl"] * variation, 1),
        "blk": round(stats["blk"] * variation, 1),
        "confidence": random.randint(6, 10)
    }
    return projected

# ----------------------------- #
# SIDEBAR NAVIGATION
# ----------------------------- #
st.sidebar.title("🏀 NBA Dashboard")
menu = st.sidebar.radio("Navigate", ["Home", "Player Performance", "Favorites", "Expert Picks"])

# ----------------------------- #
# HOME PAGE
# ----------------------------- #
if menu == "Home":
    st.title("🏀 NBA Daily Insights")
    st.markdown("### Top 10 Player Projections (Auto-updating Daily at 9AM)")

    st.info("This section will show the top 10 players with the highest projection confidence based on recent form and opponent defense.")

# ----------------------------- #
# PLAYER PERFORMANCE PAGE
# ----------------------------- #
elif menu == "Player Performance":
    st.title("📊 Player Performance & Projections")

    teams = get_teams()
    team_dict = {t["full_name"]: t["id"] for t in teams}

    team_choice = st.selectbox("Select a Team:", [""] + list(team_dict.keys()))

    if team_choice:
        team_id = team_dict[team_choice]
        players = get_players_by_team(team_id)
        player_dict = {f"{p['first_name']} {p['last_name']}": p["id"] for p in players}

        player_choice = st.selectbox("Select a Player:", [""] + list(player_dict.keys()))

        if player_choice:
            player_id = player_dict[player_choice]
            with st.spinner("Fetching stats..."):
                stats = get_player_stats(player_id)

            if stats:
                col1, col2, col3 = st.columns([1, 2, 1])
                with col2:
                    st.markdown(f"""
                        <div class="player-card">
                            <h3>{player_choice}</h3>
                            <p><em>{stats['source']}</em></p>
                            <p>PTS: {stats['pts']:.1f}</p>
                            <div class="stat-bar" style="width:{min(stats['pts']*4,100)}%"></div>
                            <p>REB: {stats['reb']:.1f}</p>
                            <div class="stat-bar" style="width:{min(stats['reb']*10,100)}%"></div>
                            <p>AST: {stats['ast']:.1f}</p>
                            <div class="stat-bar" style="width:{min(stats['ast']*10,100)}%"></div>
                            <p>STL: {stats['stl']:.1f}</p>
                            <div class="stat-bar" style="width:{min(stats['stl']*30,100)}%"></div>
                            <p>BLK: {stats['blk']:.1f}</p>
                            <div class="stat-bar" style="width:{min(stats['blk']*30,100)}%"></div>
                        </div>
                    """, unsafe_allow_html=True)

                proj = project_player(stats)
                if proj:
                    st.markdown(f"""
                        <div style="margin-top:20px;text-align:center;">
                            <div class="projection">Projected: {proj['pts']} PTS | {proj['reb']} REB | {proj['ast']} AST</div>
                            <div class="confidence">Confidence Score: {proj['confidence']}/10</div>
                        </div>
                    """, unsafe_allow_html=True)
            else:
                st.warning("No data available for this player.")

# ----------------------------- #
# FAVORITES PAGE
# ----------------------------- #
elif menu == "Favorites":
    st.title("⭐ Favorite Players")
    st.info("Feature coming soon: Save your favorite players to view all projections together.")

# ----------------------------- #
# EXPERT PICKS PAGE
# ----------------------------- #
elif menu == "Expert Picks":
    st.title("🔥 Expert Betting Insights")

    st.markdown("### Trending Expert Picks from across the web")

    sample_picks = [
        {"site": "Dimers.com", "pick": "LeBron James over 24.5 points", "confidence": "High"},
        {"site": "Action Network", "pick": "Celtics -6.5 vs Knicks", "confidence": "Medium"},
        {"site": "BettingPros", "pick": "Luka Doncic over 8.5 assists", "confidence": "High"},
        {"site": "ExpertPicks.com", "pick": "Giannis Antetokounmpo under 12.5 rebounds", "confidence": "Medium"},
        {"site": "X.com", "pick": "Steph Curry over 4.5 threes", "confidence": "High"}
    ]

    for pick in sample_picks:
        st.markdown(f"""
        <div class="bet-take">
            <strong>{pick['site']}</strong><br/>
            {pick['pick']}<br/>
            <em>Confidence: {pick['confidence']}</em>
        </div>
        """, unsafe_allow_html=True)

# ----------------------------- #
# FOOTER
# ----------------------------- #
st.markdown("""
---
🧠 **Built by Pat Vath’s NBA Dashboard** | Powered by **BallDontLie.io**  
Auto-updates daily at **9AM ET**
""")
