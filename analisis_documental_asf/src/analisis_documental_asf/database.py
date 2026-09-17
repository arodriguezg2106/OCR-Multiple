import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 3


class CatalogDatabase:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    result_hash TEXT,
                    relative_path TEXT NOT NULL,
                    month_number INTEGER,
                    month_folder TEXT,
                    filename TEXT NOT NULL,
                    original_path TEXT,
                    pdf_path TEXT,
                    ocr_status TEXT NOT NULL,
                    pages INTEGER NOT NULL DEFAULT 0,
                    text_characters INTEGER NOT NULL DEFAULT 0,
                    pages_with_text INTEGER NOT NULL DEFAULT 0,
                    coverage TEXT NOT NULL,
                    primary_type TEXT NOT NULL,
                    classification_confidence TEXT NOT NULL,
                    contained_types TEXT NOT NULL,
                    classification_evidence TEXT NOT NULL,
                    date_min TEXT,
                    date_max TEXT,
                    detected_dates TEXT NOT NULL,
                    frequent_terms TEXT NOT NULL,
                    warning TEXT NOT NULL,
                    schema_version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pages (
                    id INTEGER PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    page_number INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    characters INTEGER NOT NULL,
                    UNIQUE(document_id, page_number)
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
                    text, content='pages', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
                );
                CREATE TRIGGER IF NOT EXISTS pages_ai AFTER INSERT ON pages BEGIN
                    INSERT INTO pages_fts(rowid, text) VALUES (new.id, new.text);
                END;
                CREATE TRIGGER IF NOT EXISTS pages_ad AFTER DELETE ON pages BEGIN
                    INSERT INTO pages_fts(pages_fts, rowid, text) VALUES('delete', old.id, old.text);
                END;
                CREATE TRIGGER IF NOT EXISTS pages_au AFTER UPDATE ON pages BEGIN
                    INSERT INTO pages_fts(pages_fts, rowid, text) VALUES('delete', old.id, old.text);
                    INSERT INTO pages_fts(rowid, text) VALUES (new.id, new.text);
                END;
                """
            )

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=60)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def reusable(self, document_id, result_hash):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT result_hash, schema_version FROM documents WHERE id=?", (document_id,)
            ).fetchone()
        return bool(row and row["result_hash"] == result_hash and row["schema_version"] == SCHEMA_VERSION)

    def cached_pages(self, document_id, result_hash):
        """Return already extracted text when only catalog logic changed."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT result_hash FROM documents WHERE id=?", (document_id,)
            ).fetchone()
            if not row or row["result_hash"] != result_hash:
                return None
            return [
                (page["page_number"], page["text"])
                for page in connection.execute(
                    "SELECT page_number, text FROM pages WHERE document_id=? ORDER BY page_number",
                    (document_id,),
                )
            ]

    def save(self, record, pages):
        fields = list(record)
        values = [
            json.dumps(record[name], ensure_ascii=False)
            if isinstance(record[name], (list, dict))
            else record[name]
            for name in fields
        ]
        placeholders = ",".join("?" for _ in fields)
        with self.connect() as connection:
            connection.execute("DELETE FROM pages WHERE document_id=?", (record["id"],))
            connection.execute(
                f"INSERT OR REPLACE INTO documents ({','.join(fields)}) VALUES ({placeholders})", values
            )
            connection.executemany(
                "INSERT INTO pages(document_id,page_number,text,characters) VALUES(?,?,?,?)",
                [(record["id"], number, text, len(text)) for number, text in pages],
            )

    def documents(self):
        with self.connect() as connection:
            return [
                dict(row)
                for row in connection.execute("SELECT * FROM documents ORDER BY month_number, relative_path")
            ]
