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

@app.route("/statistics")
def statistics():
    conn = get_db_connection()

    # --- NEU: Elo-Ranking mit Rang-Änderung ---

    # 1. Aktuelle Elo-Stände aller Spieler laden
    players_elo_raw = conn.execute("SELECT id, name, elo FROM players").fetchall()

    # 2. Daten für die "letzte SpielGRUPPE" holen
    latest_changes = {} # Speichert {player_id: total_change}
    latest_game_group_row = conn.execute("SELECT group_id FROM games ORDER BY time_played DESC, id DESC LIMIT 1").fetchone()

    if latest_game_group_row:
        group_id = latest_game_group_row['group_id']
        game_ids_raw = conn.execute("SELECT id FROM games WHERE group_id = ?", (group_id,)).fetchall()
        game_ids = [row['id'] for row in game_ids_raw]

        if game_ids:
            placeholders = ",".join("?" for _ in game_ids)
            latest_changes_raw = conn.execute(
                f"SELECT player_id, SUM(elo_change) as total_change FROM elo_history WHERE game_id IN ({placeholders}) GROUP BY player_id",
                game_ids
            ).fetchall()
            latest_changes = {row['player_id']: row['total_change'] for row in latest_changes_raw}

    # 3. Listen für AKTUELLE und VORHERIGE Elo-Stände erstellen
    players_elo_data = []
    for player_row in players_elo_raw:
        player_dict = dict(player_row)
        player_id = player_dict['id']
        last_change = latest_changes.get(player_id, 0)

        player_dict['last_elo_change'] = last_change
        player_dict['elo_previous'] = player_dict['elo'] - last_change # Elo VOR der letzten Gruppe
        players_elo_data.append(player_dict)

    # 4. Beide Listen sortieren, um die Ränge zu ermitteln
    current_ranking_sorted = sorted(players_elo_data, key=lambda p: p['elo'], reverse=True)
    previous_ranking_sorted = sorted(players_elo_data, key=lambda p: p['elo_previous'], reverse=True)

    # 5. Eine Map der VORHERIGEN Ränge erstellen {player_id: previous_rank}
    previous_rank_map = {p['id']: rank for rank, p in enumerate(previous_ranking_sorted, 1)}

    # 6. Finale Liste für das Template erstellen (basierend auf AKTUELLEM Rang)
    players_for_template = []
    for current_rank, player in enumerate(current_ranking_sorted, 1):
        player_id = player['id']

        # Vorherigen Rang aus der Map holen
        # (Wenn Spieler neu ist, hat er keinen 'previous_rank' -> benutze aktuellen)
        previous_rank = previous_rank_map.get(player_id, current_rank)

        player['current_rank'] = current_rank
        player['previous_rank'] = previous_rank
        # Rang-Änderung: Positiv = aufgestiegen (z.B. von 5 auf 3 -> +2)
        player['rank_change'] = previous_rank - current_rank

        players_for_template.append(player)

    # --- Detail-Statistik (unverändert) ---

    query = """
    SELECT
        p.name, p.id,
        COALESCE(SUM(CASE WHEN g.red_score != g.blue_score THEN 1 ELSE 0 END), 0) AS games_played,
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

    # 5. Beide Datensätze an das Template übergeben
    # WICHTIG: 'players' ist jetzt die 'players_for_template'-Liste
    # 'latest_changes' wird für die "(0)"-Logik im Template benötigt
    return render_template("statistics.html",
                           players=players_for_template,
                           player_stats=player_stats,
                           latest_changes=latest_changes)


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


@app.route("/api/group/<group_id>", methods=["GET"])
def get_group_details(group_id):
    """Liefert die Details einer Spielgruppe als HTML für das Modal."""
    conn = get_db_connection()

    game_ids_query = "SELECT id FROM games WHERE group_id = ?"
    params = [group_id]
    if group_id.startswith("single_"):
        game_ids_query = "SELECT id FROM games WHERE id = ?"
        params = [group_id.split("_")[1]]

    game_ids = [row['id'] for row in conn.execute(game_ids_query, params).fetchall()]

    if not game_ids:
        return "Gruppe nicht gefunden", 404

    # 1. Hole alle Spiele der Gruppe
    games_data = conn.execute(f"SELECT * FROM games WHERE id IN ({','.join('?' for _ in game_ids)}) ORDER BY time_played ASC, id ASC", game_ids).fetchall()

    # 2. Hole für JEDES Spiel die Spieler, da Teams (rot/blau) wechseln konnten
    games_with_players = []
    for game in games_data:
        players = conn.execute("""
            SELECT p.name, gp.side
            FROM game_players gp JOIN players p ON gp.player_id = p.id
            WHERE gp.game_id = ?
        """, (game['id'],)).fetchall()
        games_with_players.append({
            'game_data': game,
            'red_team_names': sorted([p['name'] for p in players if p['side'] == 'red']),
            'blue_team_names': sorted([p['name'] for p in players if p['side'] == 'blue'])
        })

    if not games_with_players:
         return "Keine Spieldaten gefunden", 404

    # 3. Bestimme Team 1 (links) und Team 2 (rechts) basierend auf dem ERSTEN Spiel
    first_game_players = games_with_players[0]
    red_names_sorted = first_game_players['red_team_names']
    blue_names_sorted = first_game_players['blue_team_names']

    team1_names_sorted = []
    team2_names_sorted = []

    if not blue_names_sorted: # Nur rotes Team
        team1_names_sorted = red_names_sorted
        team2_names_sorted = []
    elif not red_names_sorted: # Nur blaues Team
        team1_names_sorted = blue_names_sorted
        team2_names_sorted = []
    elif red_names_sorted[0] < blue_names_sorted[0]: # Rot ist alphabetisch zuerst
        team1_names_sorted = red_names_sorted
        team2_names_sorted = blue_names_sorted
    else: # Blau ist alphabetisch zuerst
        team1_names_sorted = blue_names_sorted
        team2_names_sorted = red_names_sorted

    conn.close()

    # 4. Übergib die sortierten Listen und die volle Spielliste an das Template
    return render_template("edit_group_form.html",
                           games_list_with_players=games_with_players,
                           group_id=group_id,
                           team1_names_sorted=team1_names_sorted,
                           team2_names_sorted=team2_names_sorted)

@app.route("/api/group/<group_id>", methods=["POST"])
def update_group_details(group_id):
    """Empfängt die bearbeiteten Spieldaten und speichert sie (mit Historie)."""
    conn = get_db_connection()
    try:
        now = datetime.now()
        history_entries = []
        update_entries = []

        # 1. Daten aus Formular sammeln UND alte DB-Werte holen
        for key, value in request.form.items():
            if key.startswith("score_red["):
                game_id = key.replace("score_red[", "").replace("]", "")
                new_red_score = int(value)

                score_blue_key = f"score_blue[{game_id}]"
                if score_blue_key not in request.form:
                    continue
                new_blue_score = int(request.form[score_blue_key])

                # Alten Stand aus DB holen
                old_game = conn.execute("SELECT red_score, blue_score FROM games WHERE id = ?", (game_id,)).fetchone()

                if not old_game:
                    continue # Spiel nicht gefunden

                # Nur speichern, wenn sich was geändert hat
                if old_game['red_score'] != new_red_score or old_game['blue_score'] != new_blue_score:
                    # Für History-Tabelle vormerken
                    history_entries.append((
                        game_id, now,
                        old_game['red_score'], new_red_score,
                        old_game['blue_score'], new_blue_score
                    ))
                    # Für echtes Update vormerken
                    update_entries.append((
                        new_red_score, new_blue_score, game_id
                    ))

        # 2. Alles in einer Transaktion speichern
        if update_entries: # Nur wenn es Änderungen gab
            # 2a. Änderungen in die History schreiben
            conn.executemany(
                "INSERT INTO games_history (game_id, changed_at, old_red_score, new_red_score, old_blue_score, new_blue_score) VALUES (?, ?, ?, ?, ?, ?)",
                history_entries
            )
            # 2b. Aktuelle Tabelle updaten
            conn.executemany(
                "UPDATE games SET red_score = ?, blue_score = ? WHERE id = ?",
                update_entries
            )

            conn.commit()
            recalculate_all_elo() # Elo neu berechnen, da sich Daten geändert haben

        return "OK", 200
    except Exception as e:
        conn.rollback()
        print(f"Fehler beim Update von Gruppe {group_id}: {e}")
        return f"Fehler: {e}", 500
    finally:
        conn.close()


if __name__ == "__main__":
    app.run(debug=True)