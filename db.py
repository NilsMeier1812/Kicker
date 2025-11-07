# db.py
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "kicker.db")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES|sqlite3.PARSE_COLNAMES)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Spieler-Tabelle
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS players (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        elo REAL NOT NULL DEFAULT 1500
    );
    """)

    # Spiele-Tabelle
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS games (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id     TEXT    NOT NULL,
        red_score    INTEGER NOT NULL,
        blue_score   INTEGER NOT NULL,
        comment      TEXT,
        created_at   DATETIME NOT NULL,
        time_played  DATETIME NOT NULL
    );
    """)

    # Verknüpfungstabelle Spieler <-> Spiele
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS game_players (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        game_id INTEGER NOT NULL,
        player_id INTEGER NOT NULL,
        side TEXT CHECK(side IN ('red', 'blue')),
        position INTEGER,
        FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE,
        FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE
    );
    """)

    # NEU: Tabelle zum Speichern der Elo-Historie pro Spiel
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS elo_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        game_id INTEGER NOT NULL,
        player_id INTEGER NOT NULL,
        elo_change REAL NOT NULL,
        FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE,
        FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE
    );
    """)

    # Sicherstellen, dass die 'elo'-Spalte in der 'players'-Tabelle existiert
    cursor.execute("PRAGMA table_info(players)")
    columns = [row['name'] for row in cursor.fetchall()]
    if 'elo' not in columns:
        cursor.execute("ALTER TABLE players ADD COLUMN elo REAL NOT NULL DEFAULT 1500;")

    conn.commit()
    conn.close()
