
import streamlit as st
import requests

st.set_page_config(page_title="NBA Player Stats — Single Player Search", layout="wide")

API_URL = "https://www.balldontlie.io/api/v1"

def fetch_player(name):
    response = requests.get(f"{API_URL}/players", params={"search": name})
    if response.status_code == 429:
        st.error("Rate limit reached. Please wait a moment and try again.")
        return None
    elif response.status_code != 200:
        st.error(f"Error {response.status_code}: {response.text}")
        return None
    data = response.json()
    return data.get("data", [])

def fetch_player_stats(player_id, season):
    response = requests.get(f"{API_URL}/season_averages", params={"season": season, "player_ids[]": player_id})
    if response.status_code != 200:
        st.error(f"Error {response.status_code}: {response.text}")
        return None
    return response.json()

st.title("🏀 NBA Player Stats — Single Player Search")

player_name = st.text_input("Enter NBA player name (e.g. LeBron James):")

if player_name:
    st.write("🔍 Searching for player...")
    player_results = fetch_player(player_name)

    if not player_results:
        st.warning("No player found. Please check the name and try again.")
    else:
        player = player_results[0]
        st.subheader(f"{player['first_name']} {player['last_name']} — {player['team']['full_name']}")
        st.write(f"Position: {player['position'] or 'N/A'}")

        season = st.number_input("Select season:", min_value=1979, max_value=2025, value=2024)
        stats = fetch_player_stats(player['id'], season)

        if stats and stats.get("data"):
            st.success("Season averages:")
            st.dataframe(stats["data"][0])
        else:
            st.warning("No stats available for this player and season.")
else:
    st.info("Type a player's name to search for stats.")
