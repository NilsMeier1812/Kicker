# elo.py
from db import get_db_connection

# --- ELO-Konstanten ---
STARTING_ELO = 1500
K_FACTOR = 32

def recalculate_all_elo():
    """
    Berechnet die Elo-Werte für alle Spieler von Grund auf neu.
    Änderungen werden pro Spiel berechnet, aber erst pro Gruppe
    auf die Basis-Elo-Werte der Spieler angewendet.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Alte Elo-Historie löschen
    cursor.execute("DELETE FROM elo_history")

    # 2. Alle Spieler holen und Elo im Speicher initialisieren
    cursor.execute("SELECT id FROM players")
    players = cursor.fetchall()
    player_elos = {player['id']: STARTING_ELO for player in players}

    # 3. Alle Spiele in chronologischer Reihenfolge abrufen
    cursor.execute("""
        SELECT g.id, g.group_id, g.red_score, g.blue_score,
               GROUP_CONCAT(CASE WHEN gp.side = 'red' THEN gp.player_id END) as red_team_ids,
               GROUP_CONCAT(CASE WHEN gp.side = 'blue' THEN gp.player_id END) as blue_team_ids
        FROM games g
        JOIN game_players gp ON g.id = gp.game_id
        GROUP BY g.id
        ORDER BY g.time_played ASC, g.id ASC
    """)
    games = cursor.fetchall()

    elo_history_to_insert = []
    
    # NEU: Temporäre Speicher für Elo-Änderungen pro Gruppe
    current_group_id = None
    group_elo_changes = {} # Speichert {player_id: total_change} für die AKTUELLE Gruppe

    # 4. Jedes Spiel durchgehen
    for game in games:
        game_group_id = game['group_id']

        # Wenn eine neue Gruppe beginnt, wende die Änderungen der letzten Gruppe an
        if game_group_id != current_group_id and current_group_id is not None:
            for player_id, change in group_elo_changes.items():
                if player_id in player_elos:
                    player_elos[player_id] += change
            group_elo_changes = {} # Zurücksetzen für die neue Gruppe
        
        current_group_id = game_group_id

        red_team_ids = [int(pid) for pid in game['red_team_ids'].split(',') if pid] if game['red_team_ids'] else []
        blue_team_ids = [int(pid) for pid in game['blue_team_ids'].split(',') if pid] if game['blue_team_ids'] else []

        if not red_team_ids or not blue_team_ids:
            continue

        # WICHTIG: Elo-Werte aus dem Haupt-Dictionary holen (Basis-Elo für die Gruppe)
        red_team_elos = [player_elos[pid] for pid in red_team_ids]
        blue_team_elos = [player_elos[pid] for pid in blue_team_ids]

        avg_elo_red = sum(red_team_elos) / len(red_team_elos)
        avg_elo_blue = sum(blue_team_elos) / len(blue_team_elos)

        expected_score_red = 1 / (1 + 10 ** ((avg_elo_blue - avg_elo_red) / 400))
        actual_score_red = 1.0 if game['red_score'] > game['blue_score'] else 0.0 if game['blue_score'] > game['red_score'] else 0.5
        elo_change = K_FACTOR * (actual_score_red - expected_score_red)

        # Elo-Historie für DB vorbereiten (wie zuvor)
        for player_id in red_team_ids:
            elo_history_to_insert.append((game['id'], player_id, elo_change))
            # NEU: Änderung für die GRUPPE zwischenspeichern
            group_elo_changes[player_id] = group_elo_changes.get(player_id, 0) + elo_change
        
        for player_id in blue_team_ids:
            elo_history_to_insert.append((game['id'], player_id, -elo_change))
            # NEU: Änderung für die GRUPPE zwischenspeichern
            group_elo_changes[player_id] = group_elo_changes.get(player_id, 0) - elo_change

    # 5. Wende die Änderungen der ALLERLETZTEN Gruppe an
    for player_id, change in group_elo_changes.items():
        if player_id in player_elos:
            player_elos[player_id] += change

    # 6. Finale Elo-Werte und Historie in die DB schreiben
    try:
        update_players_data = [(round(elo), pid) for pid, elo in player_elos.items()]
        cursor.executemany("UPDATE players SET elo = ? WHERE id = ?", update_players_data)

        cursor.executemany("INSERT INTO elo_history (game_id, player_id, elo_change) VALUES (?, ?, ?)", elo_history_to_insert)

        conn.commit()
        print(f"Elo für {len(update_players_data)} Spieler und {len(elo_history_to_insert)} Historien-Einträge aktualisiert.")
    except Exception as e:
        conn.rollback()
        print(f"Fehler beim Speichern der Elo-Daten: {e}")
    finally:
        conn.close()