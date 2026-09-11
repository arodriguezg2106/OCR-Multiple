import json
import sqlite3
from contextlib import contextmanager


class Database:
    """Each operation uses its own connection; safe across document worker threads."""

    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=60)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def save(self, row):
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO documents VALUES (?, ?)",
                (row["id"], json.dumps(row, ensure_ascii=False)),
            )

    def get(self, key):
        with self.connect() as conn:
            row = conn.execute("SELECT data FROM documents WHERE id=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def rows(self):
        with self.connect() as conn:
            return [json.loads(r[0]) for r in conn.execute("SELECT data FROM documents ORDER BY id")]

    def setting(self, key, value=None):
        with self.connect() as conn:
            if value is not None:
                conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))
                return value
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None
