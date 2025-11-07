# flask_app.py
from flask import Flask, render_template, request, redirect, session
from datetime import datetime
import uuid
from db import get_db_connection, init_db
from elo import recalculate_all_elo

app = Flask(__name__)
app.secret_key = "setz-dein-eigener-geheimschluessel-hier"

init_db()

@app.context_processor
def inject_now():
    return {'now': datetime.now()}

# --- Web-UI Routen ---

@app.route("/")
def index():
    conn = get_db_connection()
    players = conn.execute("SELECT id, name FROM players ORDER BY name").fetchall()
    conn.close()
    return render_template("index.html", players=players)

@app.route("/overview")
def overview():
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT
            g.id AS game_id, g.group_id, g.red_score, g.blue_score, g.time_played,
            p.name AS player_name,
            gp.side,
            eh.elo_change
        FROM games g
        JOIN game_players gp ON g.id = gp.game_id
        JOIN players p ON gp.player_id = p.id
        LEFT JOIN elo_history eh ON g.id = eh.game_id AND p.id = eh.player_id
        ORDER BY g.time_played DESC, g.id DESC
    """).fetchall()
    conn.close()
    
    games_dict = {}
    for row in rows:
        game_id = row['game_id']
        if game_id not in games_dict:
            games_dict[game_id] = {
                'id': game_id, 'group_id': row['group_id'],
                'red_score': row['red_score'], 'blue_score': row['blue_score'],
                'time_played': row['time_played'],
                'red_team': [], 'blue_team': []
            }
        player_data = {'name': row['player_name'], 'elo_change': row['elo_change']}
        if row['side'] == 'red':
            games_dict[game_id]['red_team'].append(player_data)
        else:
            games_dict[game_id]['blue_team'].append(player_data)
    all_games = list(games_dict.values())

    groups = {}
    for game in all_games:
        group_id = game["group_id"] or f"single_{game['id']}"
        if group_id not in groups:
            groups[group_id] = []
        groups[group_id].append(game)

    sorted_groups = sorted(groups.values(), key=lambda g: g[0]["time_played"], reverse=True)
    return render_template("overview.html", grouped_games=sorted_groups)


@app.route("/players", methods=["GET"])
def players_page():
    conn = get_db_connection()
    players = conn.execute("SELECT id, name FROM players ORDER BY name").fetchall()
    conn.close()
    return render_template("players.html", players=players)

# KORRIGIERTE STATISTIK-SEITE
@app.route("/statistics")
def statistics():
    conn = get_db_connection()

    # 1. Daten für Elo-Ranking laden
    players_elo = conn.execute("SELECT id, name, elo FROM players ORDER BY elo DESC, name ASC").fetchall()

    # 2. Daten für Detail-Tabelle laden
    query = """
    SELECT
        p.name, p.id,
        COALESCE(COUNT(gp.game_id), 0) AS games_played,
        COALESCE(SUM(CASE WHEN (gp.side = 'red' AND g.red_score > g.blue_score) OR (gp.side = 'blue' AND g.blue_score > g.red_score) THEN 1 ELSE 0 END), 0) AS wins,
        COALESCE(SUM(CASE WHEN (gp.side = 'red' AND g.red_score < g.blue_score) OR (gp.side = 'blue' AND g.blue_score < g.red_score) THEN 1 ELSE 0 END), 0) AS losses,
        COALESCE(SUM(CASE WHEN gp.side = 'red' THEN g.red_score ELSE g.blue_score END), 0) AS goals_for,
        COALESCE(SUM(CASE WHEN gp.side = 'red' THEN g.blue_score ELSE g.red_score END), 0) AS goals_against
    FROM players p
    LEFT JOIN game_players gp ON p.id = gp.player_id
    LEFT JOIN games g ON gp.game_id = g.id
    GROUP BY p.id, p.name
    ORDER BY p.name ASC;
    """
    player_stats_raw = conn.execute(query).fetchall()
    conn.close()

    player_stats = []
    for row in player_stats_raw:
        stats = dict(row)
        stats['win_rate'] = (stats['wins'] / stats['games_played'] * 100) if stats['games_played'] > 0 else 0
        stats['goal_difference'] = stats['goals_for'] - stats['goals_against']
        player_stats.append(stats)
        
    # 3. Beide Datensätze an das Template übergeben
    return render_template("statistics.html", players=players_elo, player_stats=player_stats)


# --- Formular-Endpunkte (Aktionen) ---
@app.route("/submit", methods=["POST"])
def submit_games():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        player_candidates = [
            ("team1_player1", "red"), ("team1_player2", "red"),
            ("team2_player1", "blue"), ("team2_player2", "blue")
        ]
        player_list = []
        for field, side in player_candidates:
            player_id = request.form.get(field)
            if player_id:
                player_list.append({'id': int(player_id), 'side': side})
        
        if not any(p['side'] == 'red' for p in player_list) or not any(p['side'] == 'blue' for p in player_list):
            return "Fehler: Es muss mindestens ein Spieler pro Team ausgewählt werden.", 400

        group_id = str(uuid.uuid4())
        now = datetime.now()

        game_index = 0
        while f"games[{game_index}][score1]" in request.form:
            score1 = int(request.form[f"games[{game_index}][score1]"])
            score2 = int(request.form[f"games[{game_index}][score2]"])
            swapped = request.form.get(f"games[{game_index}][swapped]", "0") == "1"

            red_score = score2 if swapped else score1
            blue_score = score1 if swapped else score2

            cur.execute(
                "INSERT INTO games (group_id, red_score, blue_score, comment, created_at, time_played) VALUES (?, ?, ?, '', ?, ?)",
                (group_id, red_score, blue_score, now, now)
            )
            game_id = cur.lastrowid

            for player in player_list:
                actual_side = "blue" if swapped and player['side'] == "red" else "red" if swapped and player['side'] == "blue" else player['side']
                cur.execute(
                    "INSERT INTO game_players (game_id, player_id, side) VALUES (?, ?, ?)",
                    (game_id, player['id'], actual_side)
                )
            game_index += 1

        conn.commit()
        recalculate_all_elo()
        return redirect("/")
    except Exception as e:
        conn.rollback()
        return f"Ein unerwarteter Fehler ist aufgetreten: {e}", 500
    finally:
        conn.close()

@app.route("/add_player", methods=["POST"])
def add_player():
    name = request.form.get("name")
    if name:
        conn = get_db_connection()
        try:
            conn.execute("INSERT INTO players (name) VALUES (?)", (name,))
            conn.commit()
        except conn.IntegrityError:
            print(f"Spieler '{name}' existiert bereits.")
        except Exception as e:
            print(f"Fehler beim Hinzufügen von '{name}': {e}")
        finally:
            conn.close()
    return redirect("/players")

@app.route("/delete_player", methods=["POST"])
def delete_player():
    player_id = request.form.get("player_id")
    if player_id:
        conn = get_db_connection()
        try:
            conn.execute("DELETE FROM players WHERE id = ?", (player_id,))
            conn.commit()
            recalculate_all_elo()
        except Exception as e:
            print(f"Fehler beim Löschen von Spieler {player_id}: {e}")
        finally:
            conn.close()
    return redirect("/players")

@app.route("/admin/games", methods=["GET", "POST"])
def admin_games():
    conn = get_db_connection()
    if request.method == "POST":
        ids_to_delete = request.form.getlist("game_id")
        if ids_to_delete:
            try:
                placeholders = ",".join("?" for _ in ids_to_delete)
                conn.execute(f"DELETE FROM games WHERE id IN ({placeholders})", ids_to_delete)
                conn.commit()
                recalculate_all_elo()
            except Exception as e:
                conn.rollback()
                return f"Fehler beim Löschen der Spiele: {e}", 500
    games = conn.execute("""
        SELECT g.id, g.red_score, g.blue_score,
            COALESCE(g.time_played, g.created_at) as played_at,
            GROUP_CONCAT(CASE WHEN gp.side='red' THEN p.name END, ', ') AS red_players,
            GROUP_CONCAT(CASE WHEN gp.side='blue' THEN p.name END, ', ') AS blue_players
        FROM games g
        LEFT JOIN game_players gp ON g.id = gp.game_id
        LEFT JOIN players p ON gp.player_id = p.id
        GROUP BY g.id ORDER BY played_at DESC
    """).fetchall()
    conn.close()
    # Annahme: 'admin_delete.html' existiert
    return render_template("admin_delete.html", games=games)

if __name__ == "__main__":
    app.run(debug=True)

