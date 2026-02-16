# db_handler.py
import sqlite3
import json
import pandas as pd
import numpy as np
from datetime import datetime

DB_NAME = "experiments.db"


def _to_jsonable(value):
    if isinstance(value, pd.Series):
        return {str(k): _to_jsonable(v) for k, v in value.to_dict().items()}
    if isinstance(value, pd.DataFrame):
        return [{str(k): _to_jsonable(v) for k, v in row.items()} for row in value.to_dict(orient="records")]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return [_to_jsonable(v) for v in value.tolist()]
    if isinstance(value, np.generic):
        return value.item()
    if hasattr(value, "value"):
        try:
            return value.value
        except Exception:
            pass
    return value

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

def save_experiment(name, notes, variables, df_results, best_result, settings):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Serialize best_result as JSON (works for both dict and list)
    if best_result is not None:
        best_result_json = json.dumps(_to_jsonable(best_result))
    else:
        best_result_json = None

    cursor.execute("""
        INSERT INTO experiments (
            name, timestamp, notes, variables_json, results_json, best_result_json, settings_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        name,
        timestamp,
        notes,
        json.dumps(_to_jsonable(variables)),
        df_results.to_json(orient="records"),
        best_result_json,
        json.dumps(_to_jsonable(settings))
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

