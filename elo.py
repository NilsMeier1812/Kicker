# elo.py
from db import get_db_connection

# --- ELO-Konstanten ---
STARTING_ELO = 1500
K_FACTOR = 32

def recalculate_all_elo():
    """
    Berechnet die Elo-Werte für alle Spieler von Grund auf neu und speichert
    die Veränderung für jedes Spiel in der elo_history Tabelle.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Alte Elo-Historie löschen für eine saubere Neuberechnung
    cursor.execute("DELETE FROM elo_history")

    # 2. Alle Spieler holen und ihre Elo-Werte im Speicher initialisieren
    cursor.execute("SELECT id FROM players")
    players = cursor.fetchall()
    player_elos = {player['id']: STARTING_ELO for player in players}

    # 3. Alle Spiele in chronologischer Reihenfolge abrufen
    cursor.execute("""
        SELECT g.id, g.red_score, g.blue_score,
               GROUP_CONCAT(CASE WHEN gp.side = 'red' THEN gp.player_id END) as red_team_ids,
               GROUP_CONCAT(CASE WHEN gp.side = 'blue' THEN gp.player_id END) as blue_team_ids
        FROM games g
        JOIN game_players gp ON g.id = gp.game_id
        GROUP BY g.id
        ORDER BY g.time_played ASC, g.id ASC
    """)
    games = cursor.fetchall()

    elo_history_to_insert = []

    # 4. Jedes Spiel durchgehen und Elo anpassen
    for game in games:
        red_team_ids = [int(pid) for pid in game['red_team_ids'].split(',') if pid] if game['red_team_ids'] else []
        blue_team_ids = [int(pid) for pid in game['blue_team_ids'].split(',') if pid] if game['blue_team_ids'] else []

        if not red_team_ids or not blue_team_ids:
            continue

        red_team_elos = [player_elos[pid] for pid in red_team_ids]
        blue_team_elos = [player_elos[pid] for pid in blue_team_ids]

        avg_elo_red = sum(red_team_elos) / len(red_team_elos)
        avg_elo_blue = sum(blue_team_elos) / len(blue_team_elos)

        expected_score_red = 1 / (1 + 10 ** ((avg_elo_blue - avg_elo_red) / 400))
        actual_score_red = 1.0 if game['red_score'] > game['blue_score'] else 0.0 if game['blue_score'] > game['red_score'] else 0.5
        elo_change = K_FACTOR * (actual_score_red - expected_score_red)

        # Elo-Werte im Speicher aktualisieren und Historie für DB vorbereiten
        for player_id in red_team_ids:
            player_elos[player_id] += elo_change
            elo_history_to_insert.append((game['id'], player_id, elo_change))
        for player_id in blue_team_ids:
            player_elos[player_id] -= elo_change
            elo_history_to_insert.append((game['id'], player_id, -elo_change))

    # 5. Finale Elo-Werte und die gesamte Historie in die Datenbank schreiben
    try:
        # Elo-Werte der Spieler aktualisieren
        update_players_data = [(round(elo), pid) for pid, elo in player_elos.items()]
        cursor.executemany("UPDATE players SET elo = ? WHERE id = ?", update_players_data)

        # Elo-Historie einfügen
        cursor.executemany("INSERT INTO elo_history (game_id, player_id, elo_change) VALUES (?, ?, ?)", elo_history_to_insert)

        conn.commit()
        print(f"Elo für {len(update_players_data)} Spieler und {len(elo_history_to_insert)} Historien-Einträge aktualisiert.")
    except Exception as e:
        conn.rollback()
        print(f"Fehler beim Speichern der Elo-Daten: {e}")
    finally:
        conn.close()
