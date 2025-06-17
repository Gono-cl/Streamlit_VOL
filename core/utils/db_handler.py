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
            user_email TEXT,
            name TEXT,
            timestamp TEXT,
            notes TEXT,
            variables_json TEXT,
            results_json TEXT,
            best_result_json TEXT,
            settings_json TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_experiment(user_email, name, notes, variables, df_results, best_result, settings):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Serialize best_result as JSON (works for both dict and list)
    if best_result is not None:
        best_result_json = json.dumps(best_result)
    else:
        best_result_json = None

    cursor.execute("""
        INSERT INTO experiments (
            user_email, name, timestamp, notes, variables_json, results_json, best_result_json, settings_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_email,
        name,
        timestamp,
        notes,
        json.dumps(variables),
        df_results.to_json(orient="records"),
        best_result_json,
        json.dumps(settings)
    ))

    conn.commit()
    conn.close()

def list_experiments(user_email):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, timestamp FROM experiments WHERE user_email = ? ORDER BY id DESC", (user_email,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def load_experiment(exp_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT name, timestamp, notes, variables_json, results_json, best_result_json, settings_json
        FROM experiments
        WHERE id = ?
    """, (exp_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        name, timestamp, notes, var_json, res_json, best_json, settings_json = row
        # Load best_result as dict or list
        if best_json:
            try:
                best_result = json.loads(best_json)
            except Exception:
                best_result = None
        else:
            best_result = None
        return {
            "name": name,
            "timestamp": timestamp,
            "notes": notes,
            "variables": json.loads(var_json),
            "df_results": pd.read_json(res_json, orient="records"),
            "best_result": best_result,
            "settings": json.loads(settings_json) if settings_json else None
        }
    else:
        return None

def delete_experiments(exp_ids):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.executemany("DELETE FROM experiments WHERE id = ?", [(i,) for i in exp_ids])
    conn.commit()
    conn.close()

