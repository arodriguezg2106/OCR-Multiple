import csv
import hashlib
import json
import sqlite3

import pymupdf

from analisis_documental_asf.builder import build
from analisis_documental_asf.report import publish


def make_pdf(path, texts):
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as document:
        for text in texts:
            page = document.new_page()
            page.insert_text((40, 50), text)
        document.save(path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_database(path, rows):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE documents (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO documents VALUES(?,?)",
            [(row["id"], json.dumps(row, ensure_ascii=False)) for row in rows],
        )


def test_catalog_is_resumable_searchable_and_preserves_pdfs(tmp_path):
    original = tmp_path / "original" / "1. Enero" / "CH-001.pdf"
    output = tmp_path / "ocr" / "1. Enero" / "CH-001.pdf"
    make_pdf(original, ["imagen original"])
    make_pdf(
        output,
        [
            "Páguese por este cheque. Póliza de egresos 12/01/2021.",
            "Comprobante fiscal digital. Folio fiscal UUID 13 de enero de 2021.",
        ],
    )
    before = digest(original)
    rows = [
        {
            "id": "ok",
            "ruta_relativa": "1. Enero/CH-001.pdf",
            "nombre": "CH-001.pdf",
            "archivo_original": str(original),
            "archivo_resultado": str(output),
            "hash_resultado": digest(output),
            "estado": "completed",
            "paginas_original": 2,
            "paginas_resultado": 2,
        },
        {
            "id": "failed",
            "ruta_relativa": "3. Marzo/T-999.pdf",
            "nombre": "T-999.pdf",
            "archivo_original": str(tmp_path / "original/3. Marzo/T-999.pdf"),
            "archivo_resultado": "",
            "hash_resultado": "",
            "estado": "failed",
            "paginas_original": 4,
            "paginas_resultado": 0,
            "mensaje_error": "motor no disponible",
        },
    ]
    source = tmp_path / "estado.sqlite3"
    source_database(source, rows)
    database, execution = build(source, tmp_path / "resultados/indice_documental.sqlite3")
    assert execution["analyzed"] == 1 and execution["ocr_pending"] == 1
    assert digest(original) == before
    records = database.documents()
    assert len(records) == 2
    ok = next(row for row in records if row["id"] == "ok")
    assert ok["primary_type"] == "Cheque"
    assert ok["pages_with_text"] == 2
    assert ok["date_min"] == "2021-01-12" and ok["date_max"] == "2021-01-13"
    with database.connect() as connection:
        found = connection.execute(
            "SELECT pages.document_id FROM pages_fts JOIN pages ON pages_fts.rowid=pages.id "
            "WHERE pages_fts MATCH 'fiscal'"
        ).fetchall()
    assert [row[0] for row in found] == ["ok"]
    _, second = build(source, database.path)
    assert second["reused"] == 2

    with database.connect() as connection:
        connection.execute("UPDATE documents SET schema_version=0 WHERE id='ok'")
    _, third = build(source, database.path)
    assert third["reclassified"] == 1
    assert third.get("analyzed", 0) == 0


def test_publish_creates_all_outputs_and_escapes_html(tmp_path):
    output = tmp_path / "ocr" / "2. Febrero" / "Nómina & uno.pdf"
    make_pdf(output, ["Nómina percepciones deducciones 01/02/2021"])
    source = tmp_path / "estado.sqlite3"
    source_database(
        source,
        [
            {
                "id": "one",
                "ruta_relativa": "2. Febrero/Nómina & uno.pdf",
                "nombre": "Nómina & uno.pdf",
                "archivo_original": str(output),
                "archivo_resultado": str(output),
                "hash_resultado": digest(output),
                "estado": "completed",
                "paginas_original": 1,
                "paginas_resultado": 1,
            }
        ],
    )
    database, _ = build(source, tmp_path / "report/indice_documental.sqlite3")
    summary = publish(database, tmp_path / "report")
    assert summary["documents"] == 1 and summary["pages"] == 1
    expected = {
        "catalogo_documentos.csv",
        "resumen_por_mes.csv",
        "resumen_por_tipo.csv",
        "documentos_sin_clasificar.csv",
        "documentos_con_advertencias.csv",
        "resumen.json",
        "catalogo_documental.html",
        "indice_documental.sqlite3",
    }
    assert expected <= {path.name for path in (tmp_path / "report").iterdir()}
    document = (tmp_path / "report/catalogo_documental.html").read_text(encoding="utf-8")
    assert "Nómina &amp; uno.pdf" in document
    with (tmp_path / "report/catalogo_documentos.csv").open(encoding="utf-8-sig", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["tipo_principal"] == "Nómina"
