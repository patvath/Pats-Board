import streamlit as st
from nba_api.stats.static import players as static_players
from nba_api.stats.static import teams as static_teams
from nba_api.stats.endpoints import playercareerstats, playergamelogs, teamgamelogs
import pandas as pd
import re
import numpy as np

st.set_page_config(page_title="NBA Player Stats & Prediction", page_icon="🏀", layout="wide")
st.title("🏀 NBA Player Stats Dashboard (nba_api)")

# -----------------------------
# Helpers & caching
# -----------------------------
@st.cache_data(show_spinner=False)
def get_active_nba_players():
    try:
        return static_players.get_active_players()
    except Exception as e:
        st.error(f"Error fetching active players: {e}")
        return []

@st.cache_data(show_spinner=False)
def get_all_nba_teams():
    try:
        return static_teams.get_teams()
    except Exception as e:
        st.error(f"Error fetching teams: {e}")
        return []

@st.cache_data(show_spinner=False)
def cached_career_stats(player_id: int) -> pd.DataFrame:
    """Cache player career stats requests (reduces rate-limit pressure)."""
    return playercareerstats.PlayerCareerStats(player_id=player_id).get_data_frames()[0]

@st.cache_data(show_spinner=False)
def cached_player_logs(player_id: int, season: str) -> pd.DataFrame:
    """Cache player gamelogs for a season."""
    return playergamelogs.PlayerGameLogs(player_id_nullable=player_id, season_nullable=season).get_data_frames()[0]

@st.cache_data(show_spinner=False)
def cached_team_logs(season: str) -> pd.DataFrame:
    """Cache all team gamelogs for a season."""
    return teamgamelogs.TeamGameLogs(season_nullable=season).get_data_frames()[0]


# -----------------------------
# Core functions
# -----------------------------
def get_player_stats(player_id, season='2023-24'):
    """
    Fetches player stats and calculates various averages using player ID.
    Includes recent game averages across season boundaries.
    """
    try:
        # Career stats
        career_df = cached_career_stats(player_id)

        stats_columns = [
            'MIN','FGM','FGA','FG3M','FG3A','FTM','FTA','OREB','DREB','REB',
            'AST','STL','BLK','TOV','PF','PTS'
        ]

        # Historical career avg (exclude current row if more than one season)
        historical_career_averages = None
        if len(career_df) > 1:
            historical_career_df = career_df.iloc[:-1]
            total_games = historical_career_df['GP'].sum()
            valid_cols_hist = [c for c in stats_columns if c in historical_career_df.columns]
            career_totals = historical_career_df[valid_cols_hist].sum()
            if total_games > 0:
                historical_career_averages = (career_totals / total_games).to_frame("Historical Career Avg").T

        # Overall career averages
        overall_total_games = career_df['GP'].sum()
        valid_cols_overall = [c for c in stats_columns if c in career_df.columns]
        overall_career_totals = career_df[valid_cols_overall].sum()
        if overall_total_games > 0:
            overall_career_averages = (overall_career_totals / overall_total_games).to_frame("Overall Career Avg").T
        else:
            overall_career_averages = None

        # Determine previous season (in dataframe terms)
        last_season_averages = None
        last_season_id = None
        if season in career_df['SEASON_ID'].values:
            idx = career_df.index[career_df['SEASON_ID'] == season][0]
            if idx > 0:
                last_row = career_df.iloc[idx - 1]
                last_season_id = last_row['SEASON_ID']
                gp = last_row.get('GP', 0)
                valid_cols_last = [c for c in stats_columns if c in last_row.index]
                if gp and gp > 0:
                    last_season_averages = (last_row[valid_cols_last] / gp).to_frame(f"Last Season ({last_season_id}) Avg").T

        # Build recent averages using game logs from season + previous
        game_logs_df = None
        last_5_games_avg = last_10_games_avg = last_20_games_avg = None
        last_5_games_individual = None
        try:
            frames = []
            # current season
            cur_logs = cached_player_logs(player_id, season)
            frames.append(cur_logs)
            # previous season (if available)
            if last_season_id:
                prev_logs = cached_player_logs(player_id, last_season_id)
                frames.append(prev_logs)

            if frames:
                game_logs_df = pd.concat(frames, ignore_index=True)
                game_logs_df['GAME_DATE'] = pd.to_datetime(game_logs_df['GAME_DATE'])
                game_logs_df = game_logs_df.sort_values('GAME_DATE', ascending=False)

                valid_game_cols = [c for c in stats_columns if c in game_logs_df.columns]
                if len(game_logs_df) >= 5:
                    last_5_games_avg = game_logs_df.head(5)[valid_game_cols].mean().to_frame("Last 5 Games Avg").T
                    last_5_games_individual = game_logs_df.head(5)[['GAME_DATE','MATCHUP','SEASON_YEAR'] + valid_game_cols].copy()
                if len(game_logs_df) >= 10:
                    last_10_games_avg = game_logs_df.head(10)[valid_game_cols].mean().to_frame("Last 10 Games Avg").T
                if len(game_logs_df) >= 20:
                    last_20_games_avg = game_logs_df.head(20)[valid_game_cols].mean().to_frame("Last 20 Games Avg").T
        except Exception as e:
            st.warning(f"Could not fetch recent game logs. Error: {e}")

        return {
            'career_df': career_df,
            'historical_career_averages': historical_career_averages,
            'overall_career_averages': overall_career_averages,
            'last_season_averages': last_season_averages,
            'last_5_games_avg': last_5_games_avg,
            'last_10_games_avg': last_10_games_avg,
            'last_20_games_avg': last_20_games_avg,
            'game_logs_df': game_logs_df,
            'last_5_games_individual': last_5_games_individual,
        }
    except Exception as e:
        st.error(f"An error occurred while fetching career stats: {e}")
        return None


def get_player_vs_team_stats(player_id, team_id, season='2023-24'):
    """
    Player performance against a specific team (simple filter using MATCHUP).
    """
    stats_columns = [
        'MIN','FGM','FGA','FG3M','FG3A','FTM','FTA','OREB','DREB','REB',
        'AST','STL','BLK','TOV','PF','PTS'
    ]
    try:
        logs = cached_player_logs(player_id, season)
        if logs.empty:
            return None
        logs['GAME_DATE'] = pd.to_datetime(logs['GAME_DATE'])

        # find opponent team abbreviation
        teams = get_all_nba_teams()
        opp_abbr = None
        for t in teams:
            if t['id'] == team_id:
                opp_abbr = t['abbreviation']
                break
        if not opp_abbr:
            st.error(f"Could not find abbreviation for team ID: {team_id}")
            return None

        # player's own team abbr found in logs
        # (use the last non-null value seen in the season logs)
        player_team_abbr = logs['TEAM_ABBREVIATION'].dropna().iloc[-1] if 'TEAM_ABBREVIATION' in logs.columns and not logs['TEAM_ABBREVIATION'].dropna().empty else None

        # Filter rows where MATCHUP contains "vs. OPP" or "@ OPP"
        def vs_team(matchup: str) -> bool:
            if not isinstance(matchup, str):
                return False
            # robust case-insensitive check
            return bool(re.search(rf'(?:vs\.|@)\s*{re.escape(opp_abbr)}', matchup, flags=re.IGNORECASE))

        vs_df = logs[logs['MATCHUP'].apply(vs_team)].copy()
        if vs_df.empty:
            return None

        valid_cols = [c for c in stats_columns if c in vs_df.columns]
        avg = vs_df[valid_cols].mean().to_frame(f"Avg vs {opp_abbr} ({season})").T
        return avg
    except Exception as e:
        st.error(f"An error occurred while fetching player vs team stats: {e}")
        return None


def fetch_and_combine_game_data(player_id, season):
    """
    Combine player's logs with team/opponent logs to enable extra features.
    """
    try:
        frames = []
        cur_logs = cached_player_logs(player_id, season)
        cur_logs['GAME_DATE'] = pd.to_datetime(cur_logs['GAME_DATE'])
        frames.append(cur_logs)

        # prior season (based on 'YYYY-YY')
        try:
            base_year = int(season.split('-')[0])
            prev = f"{base_year-1}-{str(base_year)[-2:]}"
            prev_logs = cached_player_logs(player_id, prev)
            prev_logs['GAME_DATE'] = pd.to_datetime(prev_logs['GAME_DATE'])
            frames.append(prev_logs)
        except Exception as e:
            st.warning(f"Could not fetch previous season logs: {e}")

        all_player = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if all_player.empty:
            return None

        # team logs (current + maybe previous, if we can detect from career)
        team_frames = []
        try:
            cur_team_logs = cached_team_logs(season)
            cur_team_logs['GAME_DATE'] = pd.to_datetime(cur_team_logs['GAME_DATE'])
            team_frames.append(cur_team_logs)
        except Exception as e:
            st.warning(f"Team logs (current) unavailable: {e}")

        # attempt to add previous team logs if previous season exists in career
        try:
            career = cached_career_stats(player_id)
            if season in career['SEASON_ID'].values:
                idx = career.index[career['SEASON_ID'] == season][0]
                if idx > 0:
                    prev_season_id = career.iloc[idx-1]['SEASON_ID']
                    prev_team_logs = cached_team_logs(prev_season_id)
                    prev_team_logs['GAME_DATE'] = pd.to_datetime(prev_team_logs['GAME_DATE'])
                    team_frames.append(prev_team_logs)
        except Exception as e:
            st.warning(f"Team logs (previous) unavailable: {e}")

        all_teams = pd.concat(team_frames, ignore_index=True) if team_frames else pd.DataFrame()

        if all_teams.empty:
            # Fallback: return just player logs
            return all_player.sort_values('GAME_DATE').reset_index(drop=True)

        # Merge player logs with player's team logs for same GAME_ID + TEAM_ID
        player_team = pd.merge(
            all_player,
            all_teams,
            on=['GAME_ID', 'GAME_DATE', 'TEAM_ID'],
            suffixes=('_player', '_team'),
            how='left'
        )

        # Extract opponent team abbr from MATCHUP_player
        def extract_opp_abbr(matchup: str, player_abbr: str):
            # pattern matches "vs. XXX" or "@ XXX"
            m = re.search(r'(?:vs\.|@)\s*([A-Z]{2,3})', matchup or '', flags=re.IGNORECASE)
            if m:
                opp = m.group(1).upper()
                if player_abbr and opp == str(player_abbr).upper():
                    return None
                return opp
            return None

        player_team['OPPONENT_TEAM_ABBREVIATION'] = player_team.apply(
            lambda r: extract_opp_abbr(r.get('MATCHUP_player', ''), r.get('TEAM_ABBREVIATION_player', '')),
            axis=1
        )

        # Map abbreviation -> team id using team logs (more reliable than static list for historical expansion)
        abbr_map = (
            all_teams[['TEAM_ABBREVIATION', 'TEAM_ID']]
            .drop_duplicates()
            .set_index('TEAM_ABBREVIATION')['TEAM_ID']
            .to_dict()
        )
        player_team['OPPONENT_TEAM_ID'] = player_team['OPPONENT_TEAM_ABBREVIATION'].map(abbr_map)

        # Join opponent stats (same GAME_ID, opponent's TEAM_ID)
        opp_logs = all_teams.rename(columns={c: f"{c}_opp" for c in all_teams.columns})
        combined = pd.merge(
            player_team,
            opp_logs,
            left_on=['GAME_ID', 'OPPONENT_TEAM_ID'],
            right_on=['GAME_ID_opp', 'TEAM_ID_opp'],
            how='left'
        )

        # Clean up
        drop_cols = ['GAME_ID_opp', 'TEAM_ID_opp', 'OPPONENT_TEAM_ABBREVIATION']
        combined = combined.drop(columns=[c for c in drop_cols if c in combined.columns], errors='ignore')
        combined['GAME_DATE'] = pd.to_datetime(combined['GAME_DATE'])
        combined = combined.sort_values('GAME_DATE').reset_index(drop=True)
        return combined
    except Exception as e:
        st.error(f"An error occurred while fetching and combining data for prediction: {e}")
        return None


def engineer_features(combined_data):
    """
    Rolling averages, home/away, rest days, etc. Returns X, y, cleaned_df.
    """
    if combined_data is None or combined_data.empty:
        st.warning("No data available for feature engineering.")
        return None, None, None

    stats_columns = ['MIN','FGM','FGA','FG3M','FG3A','FTM','FTA','OREB','DREB','REB','AST','STL','BLK','TOV','PF','PTS']
    prediction_targets = ['PTS', 'AST', 'REB', 'FG3M']

    df = combined_data.copy()
    df['GAME_DATE'] = pd.to_datetime(df['GAME_DATE'])
    df = df.sort_values('GAME_DATE').reset_index(drop=True)

    # rolling on player columns (_player)
    for col in stats_columns:
        src = f"{col}_player"
        if src in df.columns:
            df[f'{col}_rolling_5']  = df.groupby('PLAYER_ID')[src].transform(lambda x: x.rolling(5, min_periods=1).mean().shift(1))
            df[f'{col}_rolling_10'] = df.groupby('PLAYER_ID')[src].transform(lambda x: x.rolling(10, min_periods=1).mean().shift(1))
            df[f'{col}_rolling_20'] = df.groupby('PLAYER_ID')[src].transform(lambda x: x.rolling(20, min_periods=1).mean().shift(1))

    df['IS_HOME_player'] = df['MATCHUP_player'].apply(lambda m: 1 if isinstance(m, str) and 'vs.' in m.lower() else 0)
    df['PREV_GAME_DATE'] = df.groupby('PLAYER_ID')['GAME_DATE'].shift(1)
    df['REST_DAYS_player'] = (df['GAME_DATE'] - df['PREV_GAME_DATE']).dt.days.fillna(0)

    feature_columns = [c for c in df.columns if '_rolling_' in c]
    opp_cols = [c for c in df.columns if c.endswith('_opp') and c not in ['SEASON_YEAR_opp']]
    if opp_cols:
        feature_columns.extend(opp_cols)
    feature_columns += ['IS_HOME_player', 'REST_DAYS_player']

    target_columns = [f'{c}_player' for c in prediction_targets if f'{c}_player' in df.columns]

    needed = feature_columns + target_columns
    df_clean = df.dropna(subset=[c for c in needed if c in df.columns]).copy()
    if df_clean.empty:
        st.warning("Insufficient data after cleaning for feature engineering.")
        return None, None, None

    # shift targets to be "next game"
    for t in target_columns:
        df_clean[t] = df_clean[t].shift(-1)
    df_clean = df_clean.iloc[:-1].copy()

    X = df_clean[feature_columns]
    y = df_clean[target_columns]
    if X.empty or y.empty:
        st.warning("No rows left for features/targets.")
        return None, None, None
    return X, y, df_clean


def predict_next_game_stats_from_logs(game_logs_df: pd.DataFrame, season: str):
    """
    Simple weighted blend using just the player's logs (no opponent features).
    """
    if game_logs_df is None or game_logs_df.empty:
        st.info("No logs available for prediction.")
        return None

    logs = game_logs_df.copy()
    logs['GAME_DATE'] = pd.to_datetime(logs['GAME_DATE'])
    logs = logs.sort_values('GAME_DATE')

    stats_cols = ['MIN','FGM','FGA','FG3M','FG3A','FTM','FTA','OREB','DREB','REB','AST','STL','BLK','TOV','PF','PTS']
    stats_cols = [c for c in stats_cols if c in logs.columns]

    # rolling means at the last row
    feat = {}
    for c in stats_cols:
        r5  = logs[c].rolling(5, min_periods=1).mean().iloc[-1]
        r10 = logs[c].rolling(10, min_periods=1).mean().iloc[-1] if len(logs) >= 1 else np.nan
        r20 = logs[c].rolling(20, min_periods=1).mean().iloc[-1] if len(logs) >= 1 else np.nan
        feat[f'{c}_r5']  = r5
        feat[f'{c}_r10'] = r10
        feat[f'{c}_r20'] = r20

    # season average baseline
    season_df = logs[logs['SEASON_YEAR'] == season]
    season_mean = (season_df[stats_cols].mean() if not season_df.empty else logs[stats_cols].mean()).fillna(0)

    out = {}
    for stat in ['PTS','AST','REB','FG3M']:
        r5  = feat.get(f'{stat}_r5',  np.nan)
        r10 = feat.get(f'{stat}_r10', np.nan)
        r20 = feat.get(f'{stat}_r20', np.nan)
        s   = season_mean.get(stat, 0) if isinstance(season_mean, pd.Series) else 0

        r5  = r5  if pd.notna(r5)  else s
        r10 = r10 if pd.notna(r10) else s
        r20 = r20 if pd.notna(r20) else s
        pred = 0.4*r5 + 0.3*r10 + 0.2*r20 + 0.1*s
        out[stat] = round(float(pred), 2)

    return out


# -----------------------------
# UI
# -----------------------------
active_players = get_active_nba_players()
all_teams = get_all_nba_teams()

team_name_to_id = {t['full_name']: t['id'] for t in all_teams}
team_abbr_to_id = {t['abbreviation']: t['id'] for t in all_teams}
team_names = sorted(team_name_to_id.keys())

player_name_to_id = {p['full_name']: p['id'] for p in active_players}
player_names = sorted(player_name_to_id.keys())

player1_name_select = st.selectbox("Select Player", player_names, index=0 if player_names else None)

career_df_all_seasons = None
if player1_name_select:
    pid = player_name_to_id[player1_name_select]
    try:
        career_df_all_seasons = cached_career_stats(pid)
        if career_df_all_seasons is not None and not career_df_all_seasons.empty:
            st.subheader(f"{player1_name_select} Season Averages (Per Game)")
            cols = ['SEASON_ID','TEAM_ABBREVIATION','GP','MIN','FGM','FGA','FG3M','FG3A','FTM','FTA','OREB','DREB','REB','AST','STL','BLK','TOV','PF','PTS']
            valid = [c for c in cols if c in career_df_all_seasons.columns]
            disp = career_df_all_seasons[valid].copy()
            if 'GP' in disp.columns:
                for c in ['MIN','FGM','FGA','FG3M','FG3A','FTM','FTA','OREB','DREB','REB','AST','STL','BLK','TOV','PF','PTS']:
                    if c in disp.columns:
                        disp[c] = disp.apply(lambda r: round(r[c]/r['GP'], 2) if r['GP'] else 0, axis=1)
            st.dataframe(disp, use_container_width=True)
        else:
            st.info(f"No career season data available for {player1_name_select}.")
    except Exception as e:
        st.error(f"Error fetching career stats: {e}")

opponent_team_name_select = st.selectbox("Select Opponent Team (for H2H)", [''] + team_names)

# Seasons list
current_year = pd.Timestamp.now().year
seasons = [f"{y}-{str(y+1)[-2:]}" for y in range(2000, current_year + 1)]
latest_season_for_detailed_view = seasons[-1]
if career_df_all_seasons is not None and not career_df_all_seasons.empty:
    # prefer the latest season value in the career df
    latest_season_for_detailed_view = str(career_df_all_seasons['SEASON_ID'].iloc[-1])

if st.button(f"Get Detailed Stats and Predictions for {latest_season_for_detailed_view}"):
    if not player1_name_select:
        st.warning("Please select a Player.")
    else:
        pid = player_name_to_id[player1_name_select]
        ps = get_player_stats(pid, season=latest_season_for_detailed_view)

        if ps:
            st.header(f"{player1_name_select} Detailed Statistics ({latest_season_for_detailed_view})")

            # last season averages
            if ps.get('last_season_averages') is not None and not ps['last_season_averages'].empty:
                label = ps['last_season_averages'].index[0].replace('Last Season (','').replace(') Avg','')
                st.subheader(f"Last Season Averages ({label})")
                st.dataframe(ps['last_season_averages'].round(2), use_container_width=True)
            else:
                st.subheader("Last Season Averages Not Available.")

            # recent averages
            st.subheader("Recent Game Averages")
            if ps.get('last_5_games_avg') is not None and not ps['last_5_games_avg'].empty:
                with st.expander("Last 5 Games"):
                    st.dataframe(ps['last_5_games_avg'].round(2), use_container_width=True)
            else:
                st.write("Last 5 Games Averages Not Available.")
            if ps.get('last_10_games_avg') is not None and not ps['last_10_games_avg'].empty:
                with st.expander("Last 10 Games"):
                    st.dataframe(ps['last_10_games_avg'].round(2), use_container_width=True)
            else:
                st.write("Last 10 Games Averages Not Available.")
            if ps.get('last_20_games_avg') is not None and not ps['last_20_games_avg'].empty:
                with st.expander("Last 20 Games"):
                    st.dataframe(ps['last_20_games_avg'].round(2), use_container_width=True)
            else:
                st.write("Last 20 Games Averages Not Available.")

            # last 5 games table
            st.subheader("Last 5 Games (Most Recent)")
            if ps.get('last_5_games_individual') is not None and not ps['last_5_games_individual'].empty:
                df5 = ps['last_5_games_individual'].copy()
                df5['GAME_DATE'] = pd.to_datetime(df5['GAME_DATE']).dt.strftime('%Y-%m-%d')
                st.dataframe(df5, use_container_width=True)
            else:
                st.write("No last 5 individual game rows available.")

            # H2H
            if opponent_team_name_select:
                opp_id = team_name_to_id.get(opponent_team_name_select)
                if opp_id:
                    st.header(f"{player1_name_select} vs {opponent_team_name_select} ({latest_season_for_detailed_view})")
                    vs_avg = get_player_vs_team_stats(pid, opp_id, season=latest_season_for_detailed_view)
                    if vs_avg is not None and not vs_avg.empty:
                        st.dataframe(vs_avg.round(2), use_container_width=True)
                    else:
                        st.info(f"No games vs {opponent_team_name_select} this season.")
                else:
                    st.error(f"Unknown team: {opponent_team_name_select}")
            else:
                st.info("Select an opponent to view H2H averages.")

            # Prediction – simple blend using player logs
            st.header(f"{player1_name_select} Next Game Prediction ({latest_season_for_detailed_view})")
            combined = fetch_and_combine_game_data(pid, latest_season_for_detailed_view)

            # Basic prediction from logs only (fast & robust)
            pred = predict_next_game_stats_from_logs(ps.get('game_logs_df'), latest_season_for_detailed_view)
            if pred:
                st.subheader("Predicted Stats (Blend of Recency & Season Avg)")
                st.dataframe(pd.DataFrame([pred]), use_container_width=True)
                st.caption("Heuristic: 40% L5, 30% L10, 20% L20, 10% season average.")

                if st.button(f"Save Prediction for {player1_name_select}"):
                    if 'saved_predictions' not in st.session_state:
                        st.session_state.saved_predictions = {}
                    key = f"{player1_name_select} ({latest_season_for_detailed_view})"
                    st.session_state.saved_predictions[key] = {**pred, 'Season': latest_season_for_detailed_view}
                    st.success("Prediction saved.")

        else:
            st.error(f"Could not fetch stats for {player1_name_select}.")