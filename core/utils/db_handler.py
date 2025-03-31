# db_handler.py
import sqlite3
import json
import pandas as pd
from datetime import datetime

DB_NAME = "experiments.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            timestamp TEXT,
            notes TEXT,
            variables_json TEXT,
            results_json TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_experiment(name, notes, variables, df_results):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
        INSERT INTO experiments (name, timestamp, notes, variables_json, results_json)
        VALUES (?, ?, ?, ?, ?)
    """, (
        name,
        timestamp,
        notes,
        json.dumps(variables),
        df_results.to_json(orient="records"),
    ))

    conn.commit()
    conn.close()

def list_experiments():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, timestamp FROM experiments ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return rows

def load_experiment(exp_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT name, timestamp, notes, variables_json, results_json", (exp_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        name, timestamp, notes, var_json, res_json, best_json = row
        return {
            "name": name,
            "timestamp": timestamp,
            "notes": notes,
            "variables": json.loads(var_json),
            "df_results": pd.read_json(res_json, orient="records"),
        }
    else:
        return None
