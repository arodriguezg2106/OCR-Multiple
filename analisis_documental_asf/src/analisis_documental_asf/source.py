"""Read the OCR application's JSON rows without changing its database."""

import json
import sqlite3
from pathlib import Path


def load_ocr_rows(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError(f"No existe la base de OCR: {path}")
    uri = f"file:{path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=30) as connection:
        rows = [json.loads(row[0]) for row in connection.execute("SELECT data FROM documents ORDER BY id")]
    return rows
